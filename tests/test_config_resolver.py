#!/usr/bin/env python3
"""Tests for ConfigResolver.

Tests verify:
1. IP validation (CIDR format, dhcp, None)
2. VM resolution with preset/template inheritance
3. vmid allocation (base + index, explicit override)
4. Error handling for missing/invalid config
"""

import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / 'src'))

import pytest
from config import ConfigError
from config_resolver import ConfigResolver


class TestConfigResolver:
    """Test ConfigResolver functionality."""

    @pytest.fixture
    def site_config_dir(self, tmp_path):
        """Create a minimal site-config structure for testing."""
        # Create directories
        (tmp_path / 'nodes').mkdir()
        (tmp_path / 'envs').mkdir()
        (tmp_path / 'vms').mkdir()
        (tmp_path / 'vms/presets').mkdir()

        # Create site.yaml
        (tmp_path / 'site.yaml').write_text("""
defaults:
  bridge: vmbr0
  ssh_user: root
  datastore: local-zfs
  gateway: 10.0.12.1
""")

        # Create secrets.yaml (decrypted format)
        (tmp_path / 'secrets.yaml').write_text("""
api_tokens:
  test-node: "user@pam!token=secret"
passwords:
  vm_root: "$6$rounds=4096$hash"
ssh_keys:
  key1: "ssh-rsa AAAA... user1"
  key2: "ssh-ed25519 AAAA... user2"
""")

        # Create node config
        (tmp_path / 'nodes/test-node.yaml').write_text("""
node: test-node
api_endpoint: https://10.0.12.100:8006
api_token: test-node
datastore: local-zfs
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

        # Create environment
        (tmp_path / 'envs/test.yaml').write_text("""
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

    def test_resolve_env_returns_expected_structure(self, site_config_dir):
        """resolve_env should return dict with required keys."""
        resolver = ConfigResolver(str(site_config_dir))
        config = resolver.resolve_env('test', 'test-node')

        assert 'node' in config
        assert 'api_endpoint' in config
        assert 'api_token' in config
        assert 'ssh_user' in config
        assert 'datastore' in config
        assert 'vms' in config
        assert len(config['vms']) == 3

    def test_resolve_env_applies_vmid_base(self, site_config_dir):
        """VMs should get vmid = vmid_base + index unless overridden."""
        resolver = ConfigResolver(str(site_config_dir))
        config = resolver.resolve_env('test', 'test-node')

        assert config['vms'][0]['vmid'] == 99900  # base + 0
        assert config['vms'][1]['vmid'] == 99901  # base + 1
        assert config['vms'][2]['vmid'] == 99999  # explicit override

    def test_resolve_env_applies_preset_inheritance(self, site_config_dir):
        """VM should inherit cores/memory/disk from preset via template."""
        resolver = ConfigResolver(str(site_config_dir))
        config = resolver.resolve_env('test', 'test-node')

        vm = config['vms'][0]
        assert vm['cores'] == 1  # from preset
        assert vm['memory'] == 2048  # from preset
        assert vm['disk'] == 20  # from preset
        assert vm['image'] == 'debian-12-custom.img'  # from template

    def test_resolve_env_allows_instance_override(self, site_config_dir):
        """Instance values should override preset/template."""
        resolver = ConfigResolver(str(site_config_dir))
        config = resolver.resolve_env('test', 'test-node')

        # test3 overrides cores to 2
        assert config['vms'][2]['cores'] == 2
        assert config['vms'][2]['memory'] == 2048  # still from preset

    def test_resolve_env_applies_site_defaults(self, site_config_dir):
        """VMs should get bridge and gateway from site defaults."""
        resolver = ConfigResolver(str(site_config_dir))
        config = resolver.resolve_env('test', 'test-node')

        assert config['vms'][0]['bridge'] == 'vmbr0'
        assert config['vms'][0]['gateway'] == '10.0.12.1'

    def test_resolve_env_missing_node_raises(self, site_config_dir):
        """Missing node config should raise ConfigError."""
        resolver = ConfigResolver(str(site_config_dir))

        with pytest.raises(ConfigError) as exc_info:
            resolver.resolve_env('test', 'nonexistent-node')

        assert 'nonexistent-node' in str(exc_info.value)


class TestIPValidation:
    """Test IP format validation."""

    @pytest.fixture
    def resolver(self, tmp_path):
        """Create a minimal resolver for validation tests."""
        (tmp_path / 'nodes').mkdir()
        (tmp_path / 'envs').mkdir()
        (tmp_path / 'vms').mkdir()
        (tmp_path / 'vms/presets').mkdir()
        (tmp_path / 'site.yaml').write_text('defaults: {}')
        (tmp_path / 'secrets.yaml').write_text('api_tokens: {}')
        (tmp_path / 'nodes/test.yaml').write_text('node: test\napi_endpoint: https://localhost:8006')
        return ConfigResolver(str(tmp_path))

    def test_validate_ip_accepts_dhcp(self, resolver):
        """'dhcp' should be a valid IP value."""
        # Should not raise
        resolver._validate_ip('dhcp', 'test-vm')

    def test_validate_ip_accepts_none(self, resolver):
        """None should be a valid IP value (PVE auto-assign)."""
        resolver._validate_ip(None, 'test-vm')

    def test_validate_ip_accepts_valid_cidr(self, resolver):
        """Valid CIDR notation should be accepted."""
        resolver._validate_ip('10.0.12.100/24', 'test-vm')
        resolver._validate_ip('192.168.1.1/16', 'test-vm')
        resolver._validate_ip('172.16.0.1/32', 'test-vm')

    def test_validate_ip_rejects_bare_ip(self, resolver):
        """IP without CIDR prefix should be rejected."""
        with pytest.raises(ConfigError) as exc_info:
            resolver._validate_ip('10.0.12.100', 'test-vm')
        assert 'CIDR notation' in str(exc_info.value)

    def test_validate_ip_rejects_invalid_prefix(self, resolver):
        """CIDR prefix > 32 should be rejected."""
        with pytest.raises(ConfigError) as exc_info:
            resolver._validate_ip('10.0.12.100/33', 'test-vm')
        assert 'prefix' in str(exc_info.value)

    def test_validate_ip_rejects_non_string(self, resolver):
        """Non-string IP (like integer) should be rejected."""
        with pytest.raises(ConfigError) as exc_info:
            resolver._validate_ip(12345, 'test-vm')
        assert 'expected string' in str(exc_info.value)


class TestWriteTfvars:
    """Test tfvars.json generation."""

    def test_write_tfvars_creates_valid_json(self, tmp_path):
        """write_tfvars should create valid JSON file."""
        # Create minimal site-config
        (tmp_path / 'nodes').mkdir()
        (tmp_path / 'envs').mkdir()
        (tmp_path / 'vms').mkdir()
        (tmp_path / 'vms/presets').mkdir()
        (tmp_path / 'site.yaml').write_text('defaults: {}')
        (tmp_path / 'secrets.yaml').write_text('api_tokens: {}\npasswords: {}')
        (tmp_path / 'nodes/test.yaml').write_text('node: test\napi_endpoint: https://localhost:8006')
        (tmp_path / 'envs/minimal.yaml').write_text('vms: []')

        resolver = ConfigResolver(str(tmp_path))
        config = resolver.resolve_env('minimal', 'test')

        output_path = tmp_path / 'tfvars.json'
        resolver.write_tfvars(config, str(output_path))

        assert output_path.exists()
        with open(output_path) as f:
            loaded = json.load(f)
        assert loaded['node'] == 'test'


class TestListMethods:
    """Test list_envs, list_templates, list_presets."""

    @pytest.fixture
    def resolver(self, tmp_path):
        """Create resolver with multiple envs/templates/presets."""
        (tmp_path / 'nodes').mkdir()
        (tmp_path / 'envs').mkdir()
        (tmp_path / 'vms').mkdir()
        (tmp_path / 'vms/presets').mkdir()
        (tmp_path / 'site.yaml').write_text('defaults: {}')
        (tmp_path / 'secrets.yaml').write_text('api_tokens: {}')

        # Create multiple envs
        for env in ['dev', 'test', 'prod']:
            (tmp_path / f'envs/{env}.yaml').write_text('vms: []')

        # Create templates
        for tmpl in ['debian-12', 'debian-13']:
            (tmp_path / f'vms/{tmpl}.yaml').write_text('cores: 1')

        # Create presets
        for preset in ['small', 'medium', 'large']:
            (tmp_path / f'vms/presets/{preset}.yaml').write_text('cores: 1')

        return ConfigResolver(str(tmp_path))

    def test_list_envs(self, resolver):
        """list_envs should return sorted environment names."""
        envs = resolver.list_envs()
        assert envs == ['dev', 'prod', 'test']

    def test_list_templates(self, resolver):
        """list_templates should return sorted template names."""
        templates = resolver.list_templates()
        assert templates == ['debian-12', 'debian-13']

    def test_list_presets(self, resolver):
        """list_presets should return sorted preset names."""
        presets = resolver.list_presets()
        assert presets == ['large', 'medium', 'small']
