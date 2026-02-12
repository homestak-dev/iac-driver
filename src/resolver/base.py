"""Base resolver with shared FK resolution utilities.

This module provides common functionality for resolving site-config entities:
- Path discovery (FHS-compliant only)
- YAML loading with caching
- Secrets and posture loading
- SSH key FK resolution

Used by both ConfigResolver (tofu/ansible) and SpecResolver (server).
"""

import logging
import os
from pathlib import Path
from typing import Optional

try:
    import yaml
except ImportError:
    yaml = None  # type: ignore[assignment]

logger = logging.getLogger(__name__)


class ResolverError(Exception):
    """Base exception for resolver errors."""

    def __init__(self, code: str, message: str):
        self.code = code
        self.message = message
        super().__init__(f"{code}: {message}")


class PostureNotFoundError(ResolverError):
    """Posture file not found."""

    def __init__(self, posture: str):
        super().__init__("E201", f"Posture not found: {posture}")


class SSHKeyNotFoundError(ResolverError):
    """SSH key not found in secrets."""

    def __init__(self, key_id: str):
        super().__init__("E202", f"SSH key not found: {key_id}")


class SecretsNotFoundError(ResolverError):
    """Secrets file not found or not decrypted."""

    def __init__(self, path: Path):
        super().__init__("E500", f"Secrets file not found: {path}")


def discover_etc_path() -> Path:
    """Discover the site-config path.

    Resolution order (FHS-compliant only):
    1. HOMESTAK_ETC environment variable
    2. HOMESTAK_SITE_CONFIG environment variable (alias)
    3. ../site-config/ sibling (dev workspace)
    4. /usr/local/etc/homestak/ (FHS bootstrap)

    Note: Legacy /opt/homestak/ path is no longer supported.

    Returns:
        Path to site-config directory

    Raises:
        ResolverError: If no valid path found
    """
    # Check environment variables first
    for env_var in ("HOMESTAK_ETC", "HOMESTAK_SITE_CONFIG"):
        if env_path := os.environ.get(env_var):
            path = Path(env_path)
            if path.is_dir():
                return path

    # Check sibling directory (dev workspace)
    # Works from both src/ and src/resolver/
    script_dir = Path(__file__).resolve().parent
    # Try both: parent/../site-config (from resolver/) and parent/../../site-config (from src/)
    for parent_levels in range(1, 4):
        base = script_dir
        for _ in range(parent_levels):
            base = base.parent
        sibling = base / "site-config"
        if sibling.is_dir():
            return sibling

    # Check FHS path (v0.24+ bootstrap)
    fhs_path = Path("/usr/local/etc/homestak")
    if fhs_path.is_dir():
        return fhs_path

    raise ResolverError(
        "E500",
        "Cannot find site-config directory. "
        "Set HOMESTAK_ETC or clone site-config as sibling directory."
    )


class ResolverBase:
    """Base class for FK resolution with caching.

    Provides common functionality for loading and caching site-config
    entities: site.yaml, secrets.yaml, postures, and SSH key resolution.
    """

    def __init__(self, etc_path: Optional[Path] = None):
        """Initialize resolver.

        Args:
            etc_path: Path to site-config. Auto-discovered if not provided.

        Raises:
            ResolverError: If PyYAML not installed
        """
        if yaml is None:
            raise ResolverError("E500", "PyYAML not installed. Run: apt install python3-yaml")

        self.etc_path = etc_path or discover_etc_path()
        self._secrets: Optional[dict] = None
        self._site: Optional[dict] = None
        self._posture_cache: dict = {}

    def clear_cache(self):
        """Clear all caches (called on SIGHUP for hot reload)."""
        self._secrets = None
        self._site = None
        self._posture_cache.clear()
        logger.info("Cache cleared")

    def _load_yaml(self, path: Path) -> dict:
        """Load YAML file.

        Args:
            path: Path to YAML file

        Returns:
            Parsed YAML content as dict, or empty dict if file missing
        """
        if not path.exists():
            return {}
        with open(path, "r", encoding="utf-8") as f:
            return yaml.safe_load(f) or {}

    def _load_secrets(self) -> dict:
        """Load secrets.yaml (cached).

        Returns:
            Secrets dict

        Raises:
            SecretsNotFoundError: If secrets.yaml not found
        """
        if self._secrets is None:
            secrets_path = self.etc_path / "secrets.yaml"
            if not secrets_path.exists():
                raise SecretsNotFoundError(secrets_path)
            self._secrets = self._load_yaml(secrets_path)
        return self._secrets

    def _load_site(self) -> dict:
        """Load site.yaml (cached).

        Returns:
            Site config dict (empty if file missing)
        """
        if self._site is None:
            site_path = self.etc_path / "site.yaml"
            self._site = self._load_yaml(site_path)
        return self._site

    def _load_posture(self, name: str) -> dict:
        """Load posture by name (cached).

        Args:
            name: Posture name (e.g., "dev", "prod", "local")

        Returns:
            Posture config dict

        Raises:
            PostureNotFoundError: If posture file not found
        """
        if name not in self._posture_cache:
            posture_path = self.etc_path / "postures" / f"{name}.yaml"

            if not posture_path.exists():
                raise PostureNotFoundError(name)
            self._posture_cache[name] = self._load_yaml(posture_path)
        return self._posture_cache[name]

    def _resolve_ssh_keys(self, key_refs: list) -> list:
        """Resolve SSH key references to actual public keys.

        Handles both formats:
        - "ssh_keys.keyname" -> looks up secrets.ssh_keys.keyname
        - "keyname" -> looks up secrets.ssh_keys.keyname

        Args:
            key_refs: List of SSH key reference strings

        Returns:
            List of resolved public key strings

        Raises:
            SSHKeyNotFoundError: If a referenced key is not found
        """
        secrets = self._load_secrets()
        ssh_keys = secrets.get("ssh_keys", {})
        resolved = []

        for ref in key_refs:
            # Handle both "ssh_keys.keyname" and "keyname" formats
            key_id = ref.replace("ssh_keys.", "") if ref.startswith("ssh_keys.") else ref
            if key_id not in ssh_keys:
                raise SSHKeyNotFoundError(key_id)
            resolved.append(ssh_keys[key_id])

        return resolved

    def _get_site_defaults(self) -> dict:
        """Get site.yaml defaults section.

        Returns:
            Defaults dict from site.yaml, or empty dict
        """
        return self._load_site().get("defaults", {})

    def get_signing_key(self) -> Optional[str]:
        """Get the provisioning token signing key from secrets.

        Returns:
            Hex-encoded signing key, or None if not configured
        """
        try:
            secrets = self._load_secrets()
            return secrets.get("auth", {}).get("signing_key")
        except SecretsNotFoundError:
            return None
