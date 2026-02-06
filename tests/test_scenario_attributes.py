#!/usr/bin/env python3
"""Tests for scenario attributes (requires_root, requires_host_config).

These tests verify that:
1. Scenario classes have expected attributes
2. Default values are correct when attributes are missing
3. CLI getattr pattern works as expected
"""

import sys
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent / 'src'))

import pytest
from scenarios import get_scenario, list_scenarios


class TestScenarioAttributes:
    """Test scenario attribute definitions."""

    def test_pve_setup_requires_root(self):
        """PVESetup should require root for local mode."""
        scenario = get_scenario('pve-setup')
        assert getattr(scenario, 'requires_root', False) is True

    def test_pve_setup_does_not_require_host_config(self):
        """PVESetup should not require host config (can auto-detect)."""
        scenario = get_scenario('pve-setup')
        assert getattr(scenario, 'requires_host_config', True) is False

    def test_user_setup_requires_root(self):
        """UserSetup should require root for local mode."""
        scenario = get_scenario('user-setup')
        assert getattr(scenario, 'requires_root', False) is True

    def test_user_setup_does_not_require_host_config(self):
        """UserSetup should not require host config (can auto-detect)."""
        scenario = get_scenario('user-setup')
        assert getattr(scenario, 'requires_host_config', True) is False

    def test_packer_build_does_not_require_root(self):
        """PackerBuild should not require root."""
        scenario = get_scenario('packer-build')
        assert getattr(scenario, 'requires_root', False) is False

    def test_packer_build_does_not_require_host_config(self):
        """PackerBuild should not require host config."""
        scenario = get_scenario('packer-build')
        assert getattr(scenario, 'requires_host_config', True) is False

    def test_packer_build_publish_does_not_require_host_config(self):
        """PackerBuildAndPublish should not require host config."""
        scenario = get_scenario('packer-build-publish')
        assert getattr(scenario, 'requires_host_config', True) is False

    def test_packer_build_fetch_does_not_require_host_config(self):
        """PackerBuildAndFetch should not require host config."""
        scenario = get_scenario('packer-build-fetch')
        assert getattr(scenario, 'requires_host_config', True) is False

    def test_packer_sync_does_not_require_host_config(self):
        """PackerSync should not require host config."""
        scenario = get_scenario('packer-sync')
        assert getattr(scenario, 'requires_host_config', True) is False

    def test_packer_sync_build_fetch_does_not_require_host_config(self):
        """PackerSyncBuildFetch should not require host config."""
        scenario = get_scenario('packer-sync-build-fetch')
        assert getattr(scenario, 'requires_host_config', True) is False


class TestScenarioDefaults:
    """Test default values for scenarios without explicit attributes."""

    def test_spec_vm_roundtrip_requires_host_config_by_default(self):
        """spec-vm-roundtrip should require host config (no explicit attr)."""
        scenario = get_scenario('spec-vm-roundtrip')
        # Default should be True when not specified
        assert getattr(scenario, 'requires_host_config', True) is True

    def test_spec_vm_roundtrip_does_not_require_root_by_default(self):
        """spec-vm-roundtrip should not require root (no explicit attr)."""
        scenario = get_scenario('spec-vm-roundtrip')
        # Default should be False when not specified
        assert getattr(scenario, 'requires_root', False) is False

    def test_bootstrap_install_requires_host_config_by_default(self):
        """bootstrap-install should require host config (no explicit attr)."""
        scenario = get_scenario('bootstrap-install')
        assert getattr(scenario, 'requires_host_config', True) is True


class TestAllScenariosHaveRequiredAttrs:
    """Verify all scenarios have required base attributes."""

    def test_all_scenarios_have_name(self):
        """All scenarios must have a name attribute."""
        for name in list_scenarios():
            scenario = get_scenario(name)
            assert hasattr(scenario, 'name'), f"{name} missing 'name' attribute"
            assert scenario.name == name

    def test_all_scenarios_have_description(self):
        """All scenarios must have a description attribute."""
        for name in list_scenarios():
            scenario = get_scenario(name)
            assert hasattr(scenario, 'description'), f"{name} missing 'description' attribute"
            assert len(scenario.description) > 0

    def test_all_scenarios_have_get_phases(self):
        """All scenarios must have a get_phases method."""
        for name in list_scenarios():
            scenario = get_scenario(name)
            assert hasattr(scenario, 'get_phases'), f"{name} missing 'get_phases' method"
            assert callable(scenario.get_phases)


class TestGetAttrDefaults:
    """Test that getattr with defaults works correctly."""

    def test_getattr_returns_explicit_value(self):
        """When attribute is defined, getattr should return it."""
        scenario = get_scenario('pve-setup')
        # requires_root is explicitly True
        assert getattr(scenario, 'requires_root', False) is True
        # requires_host_config is explicitly False
        assert getattr(scenario, 'requires_host_config', True) is False

    def test_getattr_returns_default_for_missing(self):
        """When attribute is missing, getattr should return default."""
        scenario = get_scenario('spec-vm-roundtrip')
        # These attributes are not defined on spec-vm-roundtrip
        # Default for requires_root should be False
        assert getattr(scenario, 'requires_root', False) is False
        # Default for requires_host_config should be True
        assert getattr(scenario, 'requires_host_config', True) is True


class TestExpectedRuntime:
    """Test expected_runtime attribute on scenarios."""

    def test_all_scenarios_have_expected_runtime(self):
        """All scenarios should have expected_runtime defined."""
        for name in list_scenarios():
            scenario = get_scenario(name)
            runtime = getattr(scenario, 'expected_runtime', None)
            assert runtime is not None, f"{name} missing 'expected_runtime' attribute"
            assert isinstance(runtime, int), f"{name} expected_runtime should be int"
            assert runtime > 0, f"{name} expected_runtime should be positive"

    def test_spec_vm_roundtrip_runtime(self):
        """spec-vm-roundtrip should have ~3 min runtime."""
        scenario = get_scenario('spec-vm-roundtrip')
        assert scenario.expected_runtime == 180  # 3 * 60

    def test_packer_sync_runtime(self):
        """packer-sync should have short runtime (~30s)."""
        scenario = get_scenario('packer-sync')
        assert scenario.expected_runtime == 30


class TestRequiresConfirmation:
    """Test requires_confirmation attribute for destructive scenarios."""

    def test_packer_build_does_not_require_confirmation(self):
        """PackerBuild should not require confirmation (non-destructive)."""
        scenario = get_scenario('packer-build')
        assert getattr(scenario, 'requires_confirmation', False) is False

    def test_pve_setup_does_not_require_confirmation(self):
        """PVESetup should not require confirmation."""
        scenario = get_scenario('pve-setup')
        assert getattr(scenario, 'requires_confirmation', False) is False


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
