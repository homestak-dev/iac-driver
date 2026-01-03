"""Host configuration management."""

import re
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlparse


@dataclass
class HostConfig:
    """Configuration for a target PVE host."""
    name: str
    tfvars_file: Path
    api_endpoint: str = ''
    node_name: str = ''
    ssh_host: str = ''
    inner_vm_id: int = 99913
    test_vm_id: int = 99901
    ssh_user: str = 'root'
    ssh_key: Path = field(default_factory=lambda: Path.home() / '.ssh' / 'id_rsa')

    # Packer release settings
    packer_release_repo: str = 'homestak-dev/packer'
    packer_release_tag: str = 'v0.1.0-rc1'
    packer_image: str = 'debian-12-custom.qcow2'

    def __post_init__(self):
        if isinstance(self.tfvars_file, str):
            self.tfvars_file = Path(self.tfvars_file)
        if isinstance(self.ssh_key, str):
            self.ssh_key = Path(self.ssh_key)

        # Read api_endpoint and node_name from tfvars if not set
        if self.tfvars_file.exists() and (not self.api_endpoint or not self.node_name):
            tfvars = _parse_tfvars(self.tfvars_file)
            if not self.api_endpoint:
                self.api_endpoint = tfvars.get('proxmox_api_endpoint', '')
            if not self.node_name:
                self.node_name = tfvars.get('proxmox_node_name', '')

        # Derive ssh_host from api_endpoint if not set
        if not self.ssh_host and self.api_endpoint:
            self.ssh_host = urlparse(self.api_endpoint).hostname or ''


def _parse_tfvars(path: Path) -> dict:
    """Parse a tfvars file and return key-value pairs."""
    result = {}
    content = path.read_text()
    # Match: key = "value" or key = 'value'
    for match in re.finditer(r'^(\w+)\s*=\s*["\']([^"\']*)["\']', content, re.MULTILINE):
        result[match.group(1)] = match.group(2)
    return result


def get_base_dir() -> Path:
    """Get the iac-driver directory."""
    return Path(__file__).parent.parent


def get_sibling_dir(name: str) -> Path:
    """Get a sibling repo directory (ansible, tofu, packer)."""
    return get_base_dir().parent / name


def list_hosts() -> list[str]:
    """List available hosts from secrets/*.tfvars files."""
    secrets_dir = get_base_dir() / 'secrets'
    return sorted([
        f.stem for f in secrets_dir.glob('*.tfvars')
        if f.is_file()
    ])


def load_host_config(host: str) -> HostConfig:
    """Load configuration for a named host."""
    tfvars_file = get_base_dir() / 'secrets' / f'{host}.tfvars'

    if not tfvars_file.exists():
        available = list_hosts()
        raise ValueError(f"Unknown host: {host}. Available: {available}")

    return HostConfig(name=host, tfvars_file=tfvars_file)
