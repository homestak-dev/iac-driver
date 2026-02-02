"""Shared pytest fixtures for iac-driver tests."""

import sys
from pathlib import Path

import pytest

# Add src to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent / 'src'))


@pytest.fixture
def site_config_dir(tmp_path):
    """Create temporary site-config directory structure.

    Creates minimal site-config with:
    - site.yaml (defaults)
    - secrets.yaml (mock secrets)
    - nodes/test-node.yaml
    - envs/test.yaml
    - vms/presets/small.yaml
    - vms/debian-12.yaml
    - postures/dev.yaml
    - postures/prod.yaml
    """
    # Create directories
    for d in ['nodes', 'envs', 'vms', 'vms/presets', 'postures', 'hosts', 'v2/postures']:
        (tmp_path / d).mkdir(parents=True, exist_ok=True)

    # Create site.yaml (v0.13: packages and pve settings, v0.45: spec_server)
    (tmp_path / 'site.yaml').write_text("""
defaults:
  timezone: America/Denver
  bridge: vmbr0
  ssh_user: root
  gateway: 10.0.12.1
  packages:
    - htop
    - curl
    - wget
  pve_remove_subscription_nag: true
  spec_server: "https://controller:44443"
""")

    # Create secrets.yaml (v0.45: auth section for spec tokens)
    (tmp_path / 'secrets.yaml').write_text("""
api_tokens:
  test-node: "user@pam!token=secret"
passwords:
  vm_root: "$6$rounds=4096$hash"
ssh_keys:
  key1: "ssh-rsa AAAA... user1"
  key2: "ssh-ed25519 AAAA... user2"
auth:
  site_token: "shared-staging-token"
  node_tokens:
    test1: "unique-test1-token"
    test2: "unique-test2-token"
""")

    # Create node config (datastore required)
    (tmp_path / 'nodes/test-node.yaml').write_text("""
node: test-node
api_endpoint: https://10.0.12.100:8006
api_token: test-node
datastore: local-zfs
""")

    # Create postures
    (tmp_path / 'postures/dev.yaml').write_text("""
ssh_port: 22
ssh_permit_root_login: "yes"
ssh_password_authentication: "yes"
sudo_nopasswd: true
fail2ban_enabled: false
packages:
  - net-tools
  - strace
""")

    (tmp_path / 'postures/prod.yaml').write_text("""
ssh_port: 22
ssh_permit_root_login: "no"
ssh_password_authentication: "no"
sudo_nopasswd: false
fail2ban_enabled: true
packages: []
""")

    # Create v2 postures (with auth.method for spec discovery)
    (tmp_path / 'v2/postures/dev.yaml').write_text("""
auth:
  method: network
ssh:
  port: 22
  permit_root_login: "yes"
""")

    (tmp_path / 'v2/postures/stage.yaml').write_text("""
auth:
  method: site_token
ssh:
  port: 22
""")

    (tmp_path / 'v2/postures/prod.yaml').write_text("""
auth:
  method: node_token
ssh:
  port: 22
""")

    # Create preset
    (tmp_path / 'vms/presets/small.yaml').write_text("""
cores: 1
memory: 2048
disk: 20
""")

    # Create template
    (tmp_path / 'vms/debian-12.yaml').write_text("""
preset: small
image: debian-12-custom.img
packages:
  - qemu-guest-agent
""")

    # Create environment (with posture FK)
    (tmp_path / 'envs/test.yaml').write_text("""
posture: dev
vmid_base: 99900
vms:
  - name: test1
    template: debian-12
    ip: 10.0.12.100/24
  - name: test2
    template: debian-12
    ip: dhcp
  - name: test3
    template: debian-12
    cores: 2
    vmid: 99999
""")

    return tmp_path


@pytest.fixture
def site_config_without_datastore(tmp_path):
    """Site config with node missing datastore (for error tests)."""
    for d in ['nodes', 'envs', 'vms']:
        (tmp_path / d).mkdir(parents=True, exist_ok=True)

    (tmp_path / 'site.yaml').write_text("""
defaults:
  timezone: UTC
""")

    (tmp_path / 'secrets.yaml').write_text("""
api_tokens: {}
""")

    # Node WITHOUT datastore - should trigger error
    (tmp_path / 'nodes/bad-node.yaml').write_text("""
node: bad-node
api_endpoint: https://10.0.12.100:8006
""")

    (tmp_path / 'envs/test.yaml').write_text("""
vmid_base: 99900
vms: []
""")

    return tmp_path


@pytest.fixture
def site_config_without_posture(tmp_path):
    """Site config with env missing posture FK (for fallback tests)."""
    for d in ['nodes', 'envs', 'vms', 'postures']:
        (tmp_path / d).mkdir(parents=True, exist_ok=True)

    (tmp_path / 'site.yaml').write_text("""
defaults:
  timezone: America/Denver
  packages: []
""")

    (tmp_path / 'secrets.yaml').write_text("""
api_tokens:
  test-node: "token"
ssh_keys: {}
""")

    (tmp_path / 'nodes/test-node.yaml').write_text("""
node: test-node
api_endpoint: https://10.0.12.100:8006
datastore: local-zfs
""")

    # Dev posture for fallback
    (tmp_path / 'postures/dev.yaml').write_text("""
ssh_port: 22
sudo_nopasswd: true
packages: []
""")

    # Env WITHOUT posture - should fall back to dev
    (tmp_path / 'envs/no-posture.yaml').write_text("""
vmid_base: 99900
vms: []
""")

    return tmp_path


@pytest.fixture
def mock_context():
    """Common context dict for action tests."""
    return {
        'inner_ip': '10.0.12.100',
        'provisioned_vms': [
            {'name': 'test1', 'vmid': 99900},
        ],
        'test1_vm_id': 99900,
    }
