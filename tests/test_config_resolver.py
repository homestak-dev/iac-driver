#!/usr/bin/env python3
"""Tests for ConfigResolver.

Tests verify:
1. IP validation (CIDR format, dhcp, None)
2. VM resolution with preset/template inheritance
3. vmid allocation (base + index, explicit override)
4. Error handling for missing/invalid config
5. Ansible variable resolution (v0.13)
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / 'src'))

import pytest
from config import ConfigError
from config_resolver import ConfigResolver

# Note: site_config_dir fixture is provided by conftest.py


class TestConfigResolver:
    """Test ConfigResolver functionality."""

    # Uses site_config_dir from conftest.py

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

    def test_resolve_env_missing_datastore_raises(self, site_config_without_datastore):
        """Missing datastore in node config should raise ConfigError."""
        resolver = ConfigResolver(str(site_config_without_datastore))

        with pytest.raises(ConfigError) as exc_info:
            resolver.resolve_env('test', 'bad-node')

        assert "missing required 'datastore'" in str(exc_info.value)
        assert "make node-config FORCE=1" in str(exc_info.value)


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
        (tmp_path / 'nodes/test.yaml').write_text('node: test\napi_endpoint: https://localhost:8006\ndatastore: local')
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
        (tmp_path / 'nodes/test.yaml').write_text('node: test\napi_endpoint: https://localhost:8006\ndatastore: local')
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


class TestResolveAnsibleVars:
    """Test ansible variable resolution from site-config."""

    # Uses site_config_dir from conftest.py

    def test_loads_postures(self, site_config_dir):
        """Postures should be loaded from postures/ directory."""
        resolver = ConfigResolver(str(site_config_dir))
        assert 'dev' in resolver.postures
        assert 'prod' in resolver.postures

    def test_applies_posture_ssh_settings(self, site_config_dir):
        """SSH settings should come from posture."""
        resolver = ConfigResolver(str(site_config_dir))
        config = resolver.resolve_ansible_vars('test')

        # test env uses dev posture
        assert config['ssh_permit_root_login'] == 'yes'
        assert config['ssh_password_authentication'] == 'yes'

    def test_applies_posture_sudo_settings(self, site_config_dir):
        """Sudo settings should come from posture."""
        resolver = ConfigResolver(str(site_config_dir))
        config = resolver.resolve_ansible_vars('test')

        # dev posture has sudo_nopasswd: true
        assert config['sudo_nopasswd'] is True

    def test_applies_site_timezone(self, site_config_dir):
        """Timezone should come from site defaults."""
        resolver = ConfigResolver(str(site_config_dir))
        config = resolver.resolve_ansible_vars('test')

        assert config['timezone'] == 'America/Denver'

    def test_merges_site_and_posture_packages(self, site_config_dir):
        """Packages should be merged from site and posture."""
        resolver = ConfigResolver(str(site_config_dir))
        config = resolver.resolve_ansible_vars('test')

        packages = config['packages']
        # Site packages
        assert 'htop' in packages
        assert 'curl' in packages
        # Posture packages (dev posture)
        assert 'net-tools' in packages
        assert 'strace' in packages

    def test_deduplicates_merged_packages(self, site_config_dir):
        """Merged packages should have no duplicates."""
        resolver = ConfigResolver(str(site_config_dir))
        config = resolver.resolve_ansible_vars('test')

        packages = config['packages']
        assert len(packages) == len(set(packages))

    def test_includes_env_metadata(self, site_config_dir):
        """Result should include env and posture names."""
        resolver = ConfigResolver(str(site_config_dir))
        config = resolver.resolve_ansible_vars('test')

        assert config['env_name'] == 'test'
        assert config['posture_name'] == 'dev'

    def test_resolves_ssh_keys(self, site_config_dir):
        """SSH keys should be resolved from secrets."""
        resolver = ConfigResolver(str(site_config_dir))
        config = resolver.resolve_ansible_vars('test')

        assert 'ssh_authorized_keys' in config
        assert len(config['ssh_authorized_keys']) == 2

    def test_missing_posture_defaults_to_dev(self, site_config_without_posture):
        """Missing posture should fall back to dev."""
        resolver = ConfigResolver(str(site_config_without_posture))
        config = resolver.resolve_ansible_vars('no-posture')

        assert config['posture_name'] == 'dev'
        assert config['sudo_nopasswd'] is True  # from dev posture


class TestWriteAnsibleVars:
    """Test ansible vars JSON generation."""

    def test_write_ansible_vars_creates_valid_json(self, site_config_dir, tmp_path):
        """write_ansible_vars should create valid JSON file."""
        resolver = ConfigResolver(str(site_config_dir))
        config = resolver.resolve_ansible_vars('test')

        output_path = tmp_path / 'ansible-vars.json'
        resolver.write_ansible_vars(config, str(output_path))

        assert output_path.exists()
        with open(output_path) as f:
            loaded = json.load(f)
        assert loaded['timezone'] == 'America/Denver'
        assert loaded['posture_name'] == 'dev'


class TestListPostures:
    """Test list_postures method."""

    def test_list_postures(self, site_config_dir):
        """list_postures should return sorted posture names."""
        resolver = ConfigResolver(str(site_config_dir))
        postures = resolver.list_postures()
        assert postures == ['dev', 'prod']
