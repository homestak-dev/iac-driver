"""Config resolution for site-config YAML files.

Resolves site-config entities (site, secrets, nodes, envs, vms, postures) into
flat configurations suitable for tofu and ansible. All template and preset
inheritance is resolved here, so consumers receive fully-computed values.

Resolution order (tofu):
1. vms/presets/{preset}.yaml (if template uses preset:)
2. vms/{template}.yaml (template definition)
3. envs/{env}.yaml instance overrides (name, ip, vmid)

Resolution order (ansible):
1. site.yaml defaults (timezone, packages, pve settings)
2. postures/{posture}.yaml (security settings from env's posture FK)
3. Packages merged: site packages + posture packages (deduplicated)
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
        self.presets = self._load_dir("vms/presets")
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

    def resolve_env(self, env: str, node: str) -> dict:
        """Resolve environment to flat tofu variables.

        Args:
            env: Environment name (matches envs/{env}.yaml)
            node: Target PVE node name (matches nodes/{node}.yaml)

        Returns:
            Dict with all resolved config ready for tfvars.json
        """
        env_config = self._load_yaml(f"envs/{env}.yaml")
        node_config = self._load_yaml(f"nodes/{node}.yaml")

        if not node_config:
            raise ConfigError(f"Node config not found: nodes/{node}.yaml")

        # Require datastore from node config (v0.13+)
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

        # vmid_base: None = let PVE auto-assign
        vmid_base = env_config.get("vmid_base")

        # Resolve VMs
        vms = []
        for idx, vm_instance in enumerate(env_config.get("vms", [])):
            default_vmid = vmid_base + idx if vmid_base is not None else None
            resolved = self._resolve_vm(vm_instance, default_vmid, defaults)
            vms.append(resolved)

        # Resolve passwords and SSH keys from secrets
        passwords = self.secrets.get("passwords", {})
        ssh_keys_dict = self.secrets.get("ssh_keys", {})
        ssh_keys_list = list(ssh_keys_dict.values())

        return {
            "node": node_config.get("node", node),
            "api_endpoint": node_config.get("api_endpoint", ""),
            "api_token": api_token,
            "ssh_user": defaults.get("ssh_user", "root"),
            "datastore": node_config["datastore"],  # Required from node config (v0.13+)
            "root_password": passwords.get("vm_root", ""),
            "ssh_keys": ssh_keys_list,
            "vms": vms,
        }

    def _resolve_vm(self, vm_instance: dict, default_vmid: Optional[int], defaults: dict) -> dict:
        """Resolve VM instance with template/preset inheritance.

        Merge order: preset → template → instance overrides

        Args:
            vm_instance: VM instance from envs/{env}.yaml vms[] list
            default_vmid: Auto-computed vmid (base + index), or None for PVE auto-assign
            defaults: Site defaults from site.yaml

        Returns:
            Fully resolved VM configuration
        """
        template_name = vm_instance.get("template")

        # Layer 1: Preset (if template references one)
        template = self.templates.get(template_name, {}).copy() if template_name else {}
        preset_name = template.get("preset")
        base = self.presets.get(preset_name, {}).copy() if preset_name else {}

        # Layer 2: Template (merge on top of preset)
        for key, value in template.items():
            if key != "preset":  # Don't include preset key in final output
                base[key] = value

        # Layer 3: Instance overrides
        for key, value in vm_instance.items():
            if key != "template":  # Don't include template key in final output
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

    def resolve_ansible_vars(self, env: str) -> dict:
        """Resolve environment to ansible variables.

        Merges site defaults with security posture from the environment's
        posture FK. Packages are merged (union of site + posture, deduplicated).

        Args:
            env: Environment name (matches envs/{env}.yaml)

        Returns:
            Dict with all resolved ansible variables
        """
        env_config = self._load_yaml(f"envs/{env}.yaml")
        defaults = self.site.get("defaults", {})

        # Load posture (default to dev if not specified)
        posture_name = env_config.get("posture", "dev")
        posture = self.postures.get(posture_name, {})

        # Merge packages: site defaults + posture additions (deduplicated)
        site_packages = defaults.get("packages", [])
        posture_packages = posture.get("packages", [])
        merged_packages = list(dict.fromkeys(site_packages + posture_packages))

        # Resolve SSH keys from secrets
        ssh_keys_dict = self.secrets.get("ssh_keys", {})
        ssh_keys_list = list(ssh_keys_dict.values())

        return {
            # System config from site defaults
            "timezone": defaults.get("timezone", "UTC"),
            "pve_remove_subscription_nag": defaults.get("pve_remove_subscription_nag", True),

            # Merged packages
            "packages": merged_packages,

            # Security settings from posture
            "ssh_port": posture.get("ssh_port", 22),
            "ssh_permit_root_login": posture.get("ssh_permit_root_login", "prohibit-password"),
            "ssh_password_authentication": posture.get("ssh_password_authentication", "no"),
            "sudo_nopasswd": posture.get("sudo_nopasswd", False),
            "fail2ban_enabled": posture.get("fail2ban_enabled", False),

            # Metadata
            "env_name": env,
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

    def list_envs(self) -> list[str]:
        """List available environment names."""
        envs_dir = self.path / "envs"
        if not envs_dir.exists():
            return []
        return sorted([f.stem for f in envs_dir.glob("*.yaml") if f.is_file()])

    def list_templates(self) -> list[str]:
        """List available VM template names."""
        return sorted(self.templates.keys())

    def list_presets(self) -> list[str]:
        """List available preset names."""
        return sorted(self.presets.keys())
