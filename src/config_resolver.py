"""Config resolution for site-config YAML files.

Resolves site-config entities (site, secrets, nodes, envs, vms, postures) into
flat configurations suitable for tofu and ansible. All template and vm_preset
inheritance is resolved here, so consumers receive fully-computed values.

Resolution order (tofu):
1. presets/{vm_preset}.yaml (if template uses preset:)
2. vms/{template}.yaml (template definition)
3. envs/{env}.yaml instance overrides (name, ip, vmid)
4. postures/{posture}.yaml for auth.method (v0.45+)

Resolution order (ansible):
1. site.yaml defaults (timezone, packages, pve settings)
2. postures/{posture}.yaml (security settings from env's posture FK)
3. Packages merged: site packages + posture packages (deduplicated)

Auth token resolution (v0.45+):
- network: empty string (trust network boundary)
- site_token: secrets.auth.site_token (shared)
- node_token: secrets.auth.node_tokens.{vm_name} (per-VM)
"""

import json
import re
from pathlib import Path
from typing import Any, Optional

try:
    import yaml
except ImportError:
    yaml = None  # type: ignore[assignment]

from config import ConfigError, get_site_config_dir, _parse_yaml, _load_secrets


class ConfigResolver:
    """Resolves site-config YAML into flat VM specs for tofu."""

    def __init__(self, site_config_path: Optional[str] = None):
        """Initialize resolver with site-config path.

        Args:
            site_config_path: Path to site-config directory. If None, uses
                              auto-discovery (env var, sibling, /opt/homestak).
        """
        if yaml is None:
            raise ConfigError("PyYAML not installed. Run: apt install python3-yaml")

        if site_config_path:
            self.path = Path(site_config_path)
        else:
            self.path = get_site_config_dir()

        self.site = self._load_yaml("site.yaml")
        self.secrets = _load_secrets(self.path) or {}
        self.vm_presets = self._load_dir("presets")
        self.templates = self._load_dir("vms")
        self.postures = self._load_dir("postures")

    def _load_yaml(self, relative_path: str) -> dict:
        """Load a YAML file from site-config directory."""
        path = self.path / relative_path
        if not path.exists():
            return {}
        return _parse_yaml(path)

    def _load_dir(self, relative_path: str) -> dict:
        """Load all YAML files in a directory as dict keyed by filename stem."""
        path = self.path / relative_path
        if not path.exists():
            return {}
        result = {}
        for f in path.glob("*.yaml"):
            if f.is_file():
                result[f.stem] = _parse_yaml(f)
        return result

    def _resolve_auth_token(self, posture_name: str, vm_name: str) -> str:
        """Resolve auth token for a VM based on posture's auth method.

        Uses postures/{posture}.yaml to determine auth.method, then
        resolves the appropriate token from secrets.yaml.

        Args:
            posture_name: Posture name (matches postures/{posture}.yaml)
            vm_name: VM name (used for node_token resolution)

        Returns:
            Auth token string, or empty string for network trust
        """
        posture = self.postures.get(posture_name, {})
        auth_config = posture.get("auth", {})
        auth_method = auth_config.get("method", "network")

        # Resolve token based on method
        auth_secrets = self.secrets.get("auth", {})

        if auth_method == "network":
            # Trust network boundary, no token needed
            return ""
        elif auth_method == "site_token":
            # Shared site-wide token
            return auth_secrets.get("site_token", "")
        elif auth_method == "node_token":
            # Per-VM unique token
            node_tokens = auth_secrets.get("node_tokens", {})
            return node_tokens.get(vm_name, "")
        else:
            # Unknown method, default to no token
            return ""

    def resolve_inline_vm(
        self,
        node: str,
        vm_name: str,
        vmid: int,
        template: Optional[str] = None,
        vm_preset: Optional[str] = None,
        image: Optional[str] = None,
        posture: Optional[str] = None
    ) -> dict:
        """Resolve inline VM definition.

        VM is defined by direct parameters (vm_name, vmid, preset/template)
        rather than via an env file.

        Supports two modes:
        1. Template mode: template references vms/{template}.yaml
        2. Preset mode: vm_preset references presets/{vm_preset}.yaml (requires image)

        Args:
            node: Target PVE node name (matches nodes/{node}.yaml)
            vm_name: VM hostname
            vmid: Explicit VM ID
            template: Template name (matches vms/{template}.yaml)
            vm_preset: Preset name (matches presets/{vm_preset}.yaml)
            image: Image name (required for vm_preset mode, optional override for template)
            posture: Posture name for auth token resolution (default: dev)

        Returns:
            Dict with all resolved config ready for tfvars.json
        """
        if not template and not vm_preset:
            raise ConfigError("resolve_inline_vm requires either template or vm_preset")

        node_config = self._load_yaml(f"nodes/{node}.yaml")

        if not node_config:
            raise ConfigError(f"Node config not found: nodes/{node}.yaml")

        if 'datastore' not in node_config:
            raise ConfigError(
                f"Node '{node}' missing required 'datastore' in nodes/{node}.yaml. "
                f"Run 'make node-config FORCE=1' in site-config to regenerate."
            )

        # Resolve API token from secrets
        api_token_key = node_config.get("api_token", node)
        api_token = self.secrets.get("api_tokens", {}).get(api_token_key, "")

        # Site defaults
        defaults = self.site.get("defaults", {})

        # Spec server for Create → Specify flow (v0.45+)
        spec_server = defaults.get("spec_server", "")

        # Posture for auth token resolution (default to dev)
        posture_name = posture or "dev"

        # Build VM instance dict for _resolve_vm
        vm_instance = {
            "name": vm_name,
            "vmid": vmid,
        }
        if template:
            vm_instance["template"] = template
        if vm_preset:
            vm_instance["vm_preset"] = vm_preset
        if image:
            vm_instance["image"] = image

        # Resolve the single VM
        resolved_vm = self._resolve_vm(vm_instance, vmid, defaults)
        # Add auth token based on posture (v0.45+)
        resolved_vm["auth_token"] = self._resolve_auth_token(posture_name, vm_name)

        # Resolve passwords and SSH keys from secrets
        passwords = self.secrets.get("passwords", {})
        ssh_keys_dict = self.secrets.get("ssh_keys", {})
        ssh_keys_list = list(ssh_keys_dict.values())

        # Determine SSH host for file uploads
        api_endpoint = node_config.get("api_endpoint", "")
        if "localhost" in api_endpoint or "127.0.0.1" in api_endpoint:
            ssh_host = "127.0.0.1"
        else:
            ssh_host = node_config.get("ip", "")

        return {
            "node": node_config.get("node", node),
            "api_endpoint": api_endpoint,
            "api_token": api_token,
            "ssh_user": node_config.get("ssh_user", defaults.get("ssh_user", "root")),
            "automation_user": defaults.get("automation_user", "homestak"),
            "ssh_host": ssh_host,
            "datastore": node_config["datastore"],
            "root_password": passwords.get("vm_root", ""),
            "ssh_keys": ssh_keys_list,
            # Spec server for Create → Specify flow (v0.45+)
            "spec_server": spec_server,
            "vms": [resolved_vm],
        }

    def _resolve_vm(self, vm_instance: dict, default_vmid: Optional[int], defaults: dict) -> dict:
        """Resolve VM instance with template/vm_preset inheritance.

        Merge order depends on mode:
        - Template mode: vm_preset (from template) → template → instance overrides
        - Preset mode: vm_preset → instance overrides (no template)

        Args:
            vm_instance: VM instance from envs/{env}.yaml vms[] list or manifest
            default_vmid: Auto-computed vmid (base + index), or None for PVE auto-assign
            defaults: Site defaults from site.yaml

        Returns:
            Fully resolved VM configuration
        """
        template_name = vm_instance.get("template")
        direct_vm_preset_name = vm_instance.get("vm_preset")  # Direct vm_preset from manifest

        if template_name:
            # Template mode: preset (from template) → template → instance
            template = self.templates.get(template_name, {}).copy()
            preset_name = template.get("preset")  # Templates use 'preset' key
            base = self.vm_presets.get(preset_name, {}).copy() if preset_name else {}

            # Layer 2: Template (merge on top of preset)
            for key, value in template.items():
                if key != "preset":  # Don't include preset key in final output
                    base[key] = value
        elif direct_vm_preset_name:
            # Preset mode: vm_preset → instance (no template)
            base = self.vm_presets.get(direct_vm_preset_name, {}).copy()
            if not base:
                raise ConfigError(f"Preset not found: presets/{direct_vm_preset_name}.yaml")
        else:
            # No template or vm_preset - start with empty base
            base = {}

        # Layer 3: Instance overrides
        for key, value in vm_instance.items():
            if key not in ("template", "vm_preset"):  # Don't include meta keys in final output
                base[key] = value

        # Layer 4: Default vmid if not specified
        if "vmid" not in base and default_vmid is not None:
            base["vmid"] = default_vmid

        # Apply site defaults for optional fields
        if "bridge" not in base:
            base["bridge"] = defaults.get("bridge", "vmbr0")

        # Apply gateway default for static IPs
        if "gateway" not in base and "gateway" in defaults:
            base["gateway"] = defaults.get("gateway")

        # Validate IP format
        if "ip" in base:
            self._validate_ip(base["ip"], base.get("name", "unknown"))

        return base

    def _validate_ip(self, ip: Any, vm_name: str) -> None:
        """Validate IP is 'dhcp', None, or valid CIDR notation.

        Args:
            ip: IP value from config
            vm_name: VM name for error context

        Raises:
            ConfigError: If IP format is invalid
        """
        if ip is None or ip == "dhcp":
            return

        # Must be a string at this point
        if not isinstance(ip, str):
            raise ConfigError(
                f"Invalid IP type for VM '{vm_name}': expected string, got {type(ip).__name__}"
            )

        # IPv4 CIDR: x.x.x.x/y where y is 0-32
        ipv4_cidr = r'^(\d{1,3})\.(\d{1,3})\.(\d{1,3})\.(\d{1,3})/(\d{1,2})$'
        match = re.match(ipv4_cidr, ip)

        if not match:
            raise ConfigError(
                f"Invalid IP format for VM '{vm_name}': '{ip}'. "
                f"Static IPs must use CIDR notation (e.g., '10.0.12.124/24'). "
                f"Use 'dhcp' for dynamic assignment."
            )

        # Validate CIDR prefix (0-32)
        prefix = int(match.group(5))
        if prefix > 32:
            raise ConfigError(
                f"Invalid CIDR prefix for VM '{vm_name}': /{prefix}. "
                f"Must be between 0 and 32."
            )

    def write_tfvars(self, config: dict, output_path: str) -> None:
        """Write resolved config as tfvars.json.

        Args:
            config: Resolved configuration from resolve_env()
            output_path: Path to write tfvars.json
        """
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(config, f, indent=2)

    def resolve_ansible_vars(self, posture_name: str = 'dev') -> dict:
        """Resolve ansible variables from site defaults and posture.

        Merges site defaults with security posture settings.
        Packages are merged (union of site + posture, deduplicated).

        Args:
            posture_name: Posture name (matches postures/{posture}.yaml, default: dev)

        Returns:
            Dict with all resolved ansible variables
        """
        defaults = self.site.get("defaults", {})
        posture = self.postures.get(posture_name, {})

        # Merge packages: site defaults + posture additions (deduplicated)
        site_packages = defaults.get("packages", [])
        posture_packages = posture.get("packages", [])
        merged_packages = list(dict.fromkeys(site_packages + posture_packages))

        # Resolve SSH keys from secrets
        ssh_keys_dict = self.secrets.get("ssh_keys", {})
        ssh_keys_list = list(ssh_keys_dict.values())

        # Read posture settings from nested structure
        ssh_config = posture.get("ssh", {})
        sudo_config = posture.get("sudo", {})
        fail2ban_config = posture.get("fail2ban", {})

        return {
            # System config from site defaults
            "timezone": defaults.get("timezone", "UTC"),
            "pve_remove_subscription_nag": defaults.get("pve_remove_subscription_nag", True),

            # Merged packages
            "packages": merged_packages,

            # Security settings from posture (nested keys)
            "ssh_port": ssh_config.get("port", 22),
            "ssh_permit_root_login": ssh_config.get("permit_root_login", "prohibit-password"),
            "ssh_password_authentication": ssh_config.get("password_authentication", "no"),
            "sudo_nopasswd": sudo_config.get("nopasswd", False),
            "fail2ban_enabled": fail2ban_config.get("enabled", False),

            # Metadata
            "posture_name": posture_name,

            # SSH keys for authorized_keys
            "ssh_authorized_keys": ssh_keys_list,
        }

    def write_ansible_vars(self, config: dict, output_path: str) -> None:
        """Write resolved ansible vars as JSON.

        Args:
            config: Resolved configuration from resolve_ansible_vars()
            output_path: Path to write ansible-vars.json
        """
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(config, f, indent=2)

    def list_postures(self) -> list[str]:
        """List available posture names."""
        return sorted(self.postures.keys())

    def list_vm_presets(self) -> list[str]:
        """List available vm_preset names."""
        return sorted(self.vm_presets.keys())

    def list_presets(self) -> list[str]:
        """List available preset names (alias for list_vm_presets)."""
        return self.list_vm_presets()
