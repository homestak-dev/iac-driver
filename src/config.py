"""Host configuration management.

Configuration is loaded from site-config YAML files:
- site.yaml: Site-wide defaults
- secrets.yaml: All sensitive values (decrypted)
- nodes/*.yaml: PVE instance configuration
- envs/*.yaml: Environment configuration (for tofu)

The merge order is: site → node, with secrets resolved by key reference.
"""

import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

try:
    import yaml
except ImportError:
    yaml = None  # Fallback to tfvars parsing if yaml not available


class ConfigError(Exception):
    """Configuration error."""
    pass


@dataclass
class HostConfig:
    """Configuration for a target host/node.

    Note: 'host' terminology maintained for backward compatibility.
    Internally this represents a PVE node from nodes/*.yaml.
    """
    name: str
    config_file: Path
    api_endpoint: str = ''
    node_name: str = ''
    ssh_host: str = ''
    inner_vm_id: int = 99800  # Match site-config/envs/nested-pve.yaml vmid_base
    test_vm_id: int = 99900   # Match site-config/envs/test.yaml vmid_base
    ssh_user: str = 'root'
    ssh_key: Path = field(default_factory=lambda: Path.home() / '.ssh' / 'id_rsa')
    datastore: str = 'local-zfs'

    # Packer release settings
    packer_release_repo: str = 'homestak-dev/packer'
    packer_release_tag: str = 'v0.1.0-rc1'
    packer_image: str = 'debian-12-custom.qcow2'

    # Keep tfvars_file as alias for backward compatibility
    @property
    def tfvars_file(self) -> Path:
        return self.config_file

    def __post_init__(self):
        if isinstance(self.config_file, str):
            self.config_file = Path(self.config_file)
        if isinstance(self.ssh_key, str):
            self.ssh_key = Path(self.ssh_key)

        # Read config from file if it exists
        if self.config_file.exists():
            if self.config_file.suffix == '.yaml':
                self._load_from_yaml()
            else:
                self._load_from_tfvars()

        # Derive ssh_host from api_endpoint if not set
        if not self.ssh_host and self.api_endpoint:
            self.ssh_host = urlparse(self.api_endpoint).hostname or ''

    def _load_from_yaml(self):
        """Load configuration from YAML file with secrets resolution."""
        if yaml is None:
            raise ConfigError("PyYAML not installed. Run: apt install python3-yaml")

        site_config_dir = self.config_file.parent.parent

        # Load site defaults
        site_file = site_config_dir / 'site.yaml'
        site_defaults = {}
        if site_file.exists():
            site_defaults = _parse_yaml(site_file).get('defaults', {})

        # Load node config
        node_config = _parse_yaml(self.config_file)

        # Load secrets for resolution
        secrets = _load_secrets(site_config_dir)

        # Apply values with merge order: site → node
        if not self.api_endpoint:
            self.api_endpoint = node_config.get('api_endpoint', '')

        if not self.node_name:
            self.node_name = node_config.get('node', self.name)

        # Resolve api_token from secrets
        api_token_key = node_config.get('api_token', self.name)
        if secrets and 'api_tokens' in secrets:
            # Store resolved token for use by scenarios
            self._api_token = secrets['api_tokens'].get(api_token_key, '')

        # Datastore: node > site > default
        self.datastore = node_config.get('datastore',
                                         site_defaults.get('datastore', 'local-zfs'))

        # SSH user: node > site > default
        if ssh_user := node_config.get('ssh_user', site_defaults.get('ssh_user')):
            self.ssh_user = ssh_user

    def _load_from_tfvars(self):
        """Load configuration from legacy tfvars file."""
        tfvars = _parse_tfvars(self.config_file)
        if not self.api_endpoint:
            self.api_endpoint = tfvars.get('proxmox_api_endpoint', '')
        if not self.node_name:
            self.node_name = tfvars.get('proxmox_node_name', '')
        if ssh_user := tfvars.get('ssh_user'):
            self.ssh_user = ssh_user

    def get_api_token(self) -> str:
        """Get resolved API token (from secrets.yaml)."""
        return getattr(self, '_api_token', '')


def _parse_yaml(path: Path) -> dict:
    """Parse a YAML file and return contents."""
    if yaml is None:
        raise ConfigError("PyYAML not installed. Run: apt install python3-yaml")
    with open(path) as f:
        return yaml.safe_load(f) or {}


def _parse_tfvars(path: Path) -> dict:
    """Parse a tfvars file and return key-value pairs (legacy support)."""
    result = {}
    content = path.read_text()
    # Match: key = "value" or key = 'value'
    for match in re.finditer(r'^(\w+)\s*=\s*["\']([^"\']*)["\']', content, re.MULTILINE):
        result[match.group(1)] = match.group(2)
    return result


def _load_secrets(site_config_dir: Path) -> Optional[dict]:
    """Load decrypted secrets from secrets.yaml."""
    secrets_file = site_config_dir / 'secrets.yaml'
    if not secrets_file.exists():
        return None
    try:
        return _parse_yaml(secrets_file)
    except Exception:
        return None


def get_base_dir() -> Path:
    """Get the iac-driver directory."""
    return Path(__file__).parent.parent  # src/ -> iac-driver/


def get_sibling_dir(name: str) -> Path:
    """Get a sibling repo directory (ansible, tofu, packer, site-config)."""
    return get_base_dir().parent / name  # iac-driver/ -> homestak/ -> ansible/


def get_site_config_dir() -> Path:
    """Discover site-config directory.

    Resolution order:
    1. $HOMESTAK_SITE_CONFIG environment variable
    2. ../site-config/ sibling directory
    3. /opt/homestak/site-config/ bootstrap default
    """
    # 1. Environment variable
    if env_path := os.environ.get('HOMESTAK_SITE_CONFIG'):
        path = Path(env_path)
        if path.exists():
            return path
        raise ConfigError(f"HOMESTAK_SITE_CONFIG={env_path} does not exist")

    # 2. Sibling directory
    sibling = get_base_dir().parent / 'site-config'
    if sibling.exists():
        return sibling

    # 3. Bootstrap default
    default = Path('/opt/homestak/site-config')
    if default.exists():
        return default

    raise ConfigError(
        "site-config not found. "
        "Set HOMESTAK_SITE_CONFIG or clone site-config as sibling directory."
    )


def list_envs() -> list[str]:
    """List available environments from site-config/envs/*.yaml."""
    try:
        site_config = get_site_config_dir()
    except ConfigError:
        return []

    envs_dir = site_config / 'envs'
    if envs_dir.exists():
        return sorted([f.stem for f in envs_dir.glob('*.yaml') if f.is_file()])

    return []


def list_hosts() -> list[str]:
    """List available hosts/nodes from site-config.

    Checks nodes/*.yaml first (new format), falls back to hosts/*.tfvars (legacy).
    """
    try:
        site_config = get_site_config_dir()
    except ConfigError:
        return []

    # Try new format first: nodes/*.yaml
    nodes_dir = site_config / 'nodes'
    if nodes_dir.exists():
        nodes = [f.stem for f in nodes_dir.glob('*.yaml') if f.is_file()]
        if nodes:
            return sorted(nodes)

    # Fallback to legacy format: hosts/*.tfvars
    hosts_dir = site_config / 'hosts'
    if hosts_dir.exists():
        return sorted([
            f.stem for f in hosts_dir.glob('*.tfvars')
            if f.is_file()
        ])

    return []


def load_host_config(host: str) -> HostConfig:
    """Load configuration for a named host/node.

    Checks nodes/{host}.yaml first (new format), falls back to hosts/{host}.tfvars.
    """
    site_config = get_site_config_dir()

    # Try new format first: nodes/*.yaml
    yaml_file = site_config / 'nodes' / f'{host}.yaml'
    if yaml_file.exists():
        return HostConfig(name=host, config_file=yaml_file)

    # Fallback to legacy format: hosts/*.tfvars
    tfvars_file = site_config / 'hosts' / f'{host}.tfvars'
    if tfvars_file.exists():
        return HostConfig(name=host, config_file=tfvars_file)

    available = list_hosts()
    raise ValueError(f"Unknown host: {host}. Available: {available}")


def load_secrets() -> dict:
    """Load all secrets from site-config/secrets.yaml."""
    site_config = get_site_config_dir()
    secrets = _load_secrets(site_config)
    if secrets is None:
        raise ConfigError(
            "secrets.yaml not found or not decrypted. "
            "Run: cd ../site-config && make decrypt"
        )
    return secrets
