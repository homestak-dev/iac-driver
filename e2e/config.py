"""Host configuration management."""

from dataclasses import dataclass, field
from pathlib import Path

import yaml


@dataclass
class HostConfig:
    """Configuration for a target PVE host."""
    name: str
    api_endpoint: str
    node_name: str
    tfvars_file: Path
    inner_vm_id: int = 99913
    test_vm_id: int = 99901
    ssh_user: str = 'root'
    ssh_key: Path = field(default_factory=lambda: Path.home() / '.ssh' / 'id_rsa')

    # Packer release settings
    packer_release_repo: str = 'homestak-dev/packer'
    packer_release_tag: str = 'latest'
    packer_image: str = 'debian-12-custom.qcow2'

    def __post_init__(self):
        if isinstance(self.tfvars_file, str):
            self.tfvars_file = Path(self.tfvars_file)
        if isinstance(self.ssh_key, str):
            self.ssh_key = Path(self.ssh_key)


def get_base_dir() -> Path:
    """Get the iac-driver directory."""
    return Path(__file__).parent.parent


def get_sibling_dir(name: str) -> Path:
    """Get a sibling repo directory (ansible, tofu, packer)."""
    return get_base_dir().parent / name


def load_host_config(host: str) -> HostConfig:
    """Load configuration for a named host."""
    config_file = Path(__file__).parent / 'config' / 'hosts.yaml'

    if not config_file.exists():
        raise FileNotFoundError(f"Host config not found: {config_file}")

    with open(config_file) as f:
        all_configs = yaml.safe_load(f)

    if host not in all_configs.get('hosts', {}):
        available = list(all_configs.get('hosts', {}).keys())
        raise ValueError(f"Unknown host: {host}. Available: {available}")

    host_data = all_configs['hosts'][host]
    defaults = all_configs.get('defaults', {})
    merged = {**defaults, **host_data, 'name': host}

    # Resolve tfvars path relative to iac-driver
    if 'tfvars_file' in merged:
        merged['tfvars_file'] = get_base_dir() / merged['tfvars_file']

    return HostConfig(**merged)
