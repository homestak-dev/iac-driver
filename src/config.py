"""Host configuration management."""

import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlparse


class ConfigError(Exception):
    """Configuration error."""
    pass


@dataclass
class HostConfig:
    """Configuration for a target host."""
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

        # Read config from tfvars if file exists
        if self.tfvars_file.exists():
            tfvars = _parse_tfvars(self.tfvars_file)
            if not self.api_endpoint:
                self.api_endpoint = tfvars.get('proxmox_api_endpoint', '')
            if not self.node_name:
                self.node_name = tfvars.get('proxmox_node_name', '')
            if ssh_user := tfvars.get('ssh_user'):
                self.ssh_user = ssh_user

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


def list_hosts() -> list[str]:
    """List available hosts from site-config/hosts/*.tfvars files."""
    try:
        hosts_dir = get_site_config_dir() / 'hosts'
    except ConfigError:
        return []
    return sorted([
        f.stem for f in hosts_dir.glob('*.tfvars')
        if f.is_file()
    ])


def load_host_config(host: str) -> HostConfig:
    """Load configuration for a named host."""
    site_config = get_site_config_dir()
    tfvars_file = site_config / 'hosts' / f'{host}.tfvars'

    if not tfvars_file.exists():
        available = list_hosts()
        raise ValueError(f"Unknown host: {host}. Available: {available}")

    return HostConfig(name=host, tfvars_file=tfvars_file)
