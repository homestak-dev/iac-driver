#!/usr/bin/env python3
"""Tests for ConfigResolver.

Tests verify:
1. IP validation (CIDR format, dhcp, None)
2. VM resolution with preset inheritance
3. vmid allocation (base + index, explicit override)
4. Error handling for missing/invalid config
5. Ansible variable resolution (v0.13)
"""

import json
import sys
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).parent.parent / 'src'))

import pytest
from config import ConfigError
from config_resolver import ConfigResolver

# Note: site_config_dir fixture is provided by conftest.py



class TestIPValidation:
    """Test IP format validation."""

    @pytest.fixture
    def resolver(self, tmp_path):
        """Create a minimal resolver for validation tests."""
        (tmp_path / 'nodes').mkdir()
        (tmp_path / 'envs').mkdir()

        (tmp_path / 'presets').mkdir()
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

        (tmp_path / 'presets').mkdir()
        (tmp_path / 'site.yaml').write_text('defaults: {}')
        (tmp_path / 'secrets.yaml').write_text('api_tokens: {}\npasswords: {}')
        (tmp_path / 'nodes/test.yaml').write_text('node: test\napi_endpoint: https://localhost:8006\ndatastore: local')
        (tmp_path / 'presets/vm-small.yaml').write_text('cores: 1\nmemory: 2048\ndisk: 10')

        resolver = ConfigResolver(str(tmp_path))
        config = resolver.resolve_inline_vm(
            node='test', vm_name='test-vm', vmid=99900,
            vm_preset='vm-small', image='debian-12'
        )

        output_path = tmp_path / 'tfvars.json'
        resolver.write_tfvars(config, str(output_path))

        assert output_path.exists()
        with open(output_path) as f:
            loaded = json.load(f)
        assert loaded['node'] == 'test'


class TestListMethods:
    """Test list_presets."""

    @pytest.fixture
    def resolver(self, tmp_path):
        """Create resolver with presets."""
        (tmp_path / 'nodes').mkdir()
        (tmp_path / 'presets').mkdir()
        (tmp_path / 'site.yaml').write_text('defaults: {}')
        (tmp_path / 'secrets.yaml').write_text('api_tokens: {}')

        # Create presets
        for preset in ['small', 'medium', 'large']:
            (tmp_path / f'presets/vm-{preset}.yaml').write_text('cores: 1')

        return ConfigResolver(str(tmp_path))

    def test_list_presets(self, resolver):
        """list_presets should return sorted preset names."""
        presets = resolver.list_presets()
        assert presets == ['vm-large', 'vm-medium', 'vm-small']


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
        config = resolver.resolve_ansible_vars('dev')

        assert config['ssh_permit_root_login'] == 'yes'
        assert config['ssh_password_authentication'] == 'yes'

    def test_applies_posture_sudo_settings(self, site_config_dir):
        """Sudo settings should come from posture."""
        resolver = ConfigResolver(str(site_config_dir))
        config = resolver.resolve_ansible_vars('dev')

        # dev posture has sudo_nopasswd: true
        assert config['sudo_nopasswd'] is True

    def test_applies_site_timezone(self, site_config_dir):
        """Timezone should come from site defaults."""
        resolver = ConfigResolver(str(site_config_dir))
        config = resolver.resolve_ansible_vars('dev')

        assert config['timezone'] == 'America/Denver'

    def test_merges_site_and_posture_packages(self, site_config_dir):
        """Packages should be merged from site and posture."""
        resolver = ConfigResolver(str(site_config_dir))
        config = resolver.resolve_ansible_vars('dev')

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
        config = resolver.resolve_ansible_vars('dev')

        packages = config['packages']
        assert len(packages) == len(set(packages))

    def test_includes_posture_metadata(self, site_config_dir):
        """Result should include posture name."""
        resolver = ConfigResolver(str(site_config_dir))
        config = resolver.resolve_ansible_vars('dev')

        assert config['posture_name'] == 'dev'

    def test_resolves_ssh_keys(self, site_config_dir):
        """SSH keys should be resolved from secrets."""
        resolver = ConfigResolver(str(site_config_dir))
        config = resolver.resolve_ansible_vars('dev')

        assert 'ssh_authorized_keys' in config
        assert len(config['ssh_authorized_keys']) == 2

    def test_default_posture_is_dev(self, site_config_dir):
        """Default posture should be dev when not specified."""
        resolver = ConfigResolver(str(site_config_dir))
        config = resolver.resolve_ansible_vars()

        assert config['posture_name'] == 'dev'
        assert config['sudo_nopasswd'] is True  # from dev posture


class TestWriteAnsibleVars:
    """Test ansible vars JSON generation."""

    def test_write_ansible_vars_creates_valid_json(self, site_config_dir, tmp_path):
        """write_ansible_vars should create valid JSON file."""
        resolver = ConfigResolver(str(site_config_dir))
        config = resolver.resolve_ansible_vars('dev')

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
        assert postures == ['dev', 'prod', 'stage']


class TestSpecServerResolution:
    """Test spec_server resolution for Create → Specify flow (v0.45+)."""

    def test_resolve_inline_vm_includes_spec_server(self, site_config_dir):
        """resolve_inline_vm should include spec_server from site.yaml defaults."""
        resolver = ConfigResolver(str(site_config_dir))
        config = resolver.resolve_inline_vm(
            node='test-node',
            vm_name='inline-vm',
            vmid=99900,
            vm_preset='vm-small',
            image='debian-12-custom.img'
        )

        assert 'spec_server' in config
        assert config['spec_server'] == 'https://controller:44443'


class TestProvisioningTokenResolution:
    """Test provisioning token minting in resolve_inline_vm (v0.49+)."""

    def test_resolve_inline_vm_mints_token_with_spec(self, site_config_dir):
        """resolve_inline_vm mints provisioning token when spec and spec_server set."""
        resolver = ConfigResolver(str(site_config_dir))
        config = resolver.resolve_inline_vm(
            node='test-node',
            vm_name='inline-vm',
            vmid=99900,
            vm_preset='vm-small',
            image='debian-12-custom.img',
            spec='base',
        )

        token = config['vms'][0]['auth_token']
        # Token should be non-empty (minted)
        assert token != ''
        # Token format: base64url.base64url
        assert '.' in token
        parts = token.split('.')
        assert len(parts) == 2

    def test_resolve_inline_vm_empty_token_without_spec(self, site_config_dir):
        """resolve_inline_vm returns empty token when no spec FK."""
        resolver = ConfigResolver(str(site_config_dir))
        config = resolver.resolve_inline_vm(
            node='test-node',
            vm_name='inline-vm',
            vmid=99900,
            vm_preset='vm-small',
            image='debian-12-custom.img',
            # No spec parameter
        )

        assert config['vms'][0]['auth_token'] == ''

    def test_resolve_inline_vm_empty_token_without_spec_server(self, site_config_dir):
        """resolve_inline_vm returns empty token when no spec_server configured."""
        # Override site.yaml to remove spec_server
        (site_config_dir / 'site.yaml').write_text(yaml.dump({
            "defaults": {"domain": "test.local", "timezone": "UTC"}
        }))
        resolver = ConfigResolver(str(site_config_dir))
        config = resolver.resolve_inline_vm(
            node='test-node',
            vm_name='inline-vm',
            vmid=99900,
            vm_preset='vm-small',
            image='debian-12-custom.img',
            spec='base',
        )

        assert config['vms'][0]['auth_token'] == ''


class TestPosturesAuthModel:
    """Test posture auth model loading."""

    def test_postures_loaded_with_auth(self, site_config_dir):
        """Postures should include auth model from nested format."""
        resolver = ConfigResolver(str(site_config_dir))
        assert 'dev' in resolver.postures
        assert 'stage' in resolver.postures
        assert 'prod' in resolver.postures

    def test_postures_has_auth_method(self, site_config_dir):
        """Postures should have auth.method field."""
        resolver = ConfigResolver(str(site_config_dir))
        assert resolver.postures['dev']['auth']['method'] == 'network'
        assert resolver.postures['stage']['auth']['method'] == 'site_token'
        assert resolver.postures['prod']['auth']['method'] == 'node_token'
