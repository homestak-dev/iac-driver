"""Config resolution for site-config YAML files.

Resolves site-config entities (site, secrets, nodes, envs, vms) into flat
configurations suitable for tofu. All template and preset inheritance is
resolved here, so tofu receives fully-computed values.

Resolution order:
1. vms/presets/{preset}.yaml (if template uses preset:)
2. vms/{template}.yaml (template definition)
3. envs/{env}.yaml instance overrides (name, ip, vmid)
"""

import json
from pathlib import Path
from typing import Any, Optional

try:
    import yaml
except ImportError:
    yaml = None

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
            "datastore": node_config.get("datastore", defaults.get("datastore", "local-zfs")),
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

        return base

    def write_tfvars(self, config: dict, output_path: str) -> None:
        """Write resolved config as tfvars.json.

        Args:
            config: Resolved configuration from resolve_env()
            output_path: Path to write tfvars.json
        """
        with open(output_path, "w") as f:
            json.dump(config, f, indent=2)

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
