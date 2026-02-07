#!/usr/bin/env python3
"""Integration tests for CLI scenario attribute handling.

These tests verify the CLI properly handles:
1. requires_root check for --local mode
2. requires_host_config for auto-detect host

Some tests require site-config with configured hosts and are marked
with @pytest.mark.requires_infrastructure - these are skipped in CI.
"""

import subprocess
import sys
from pathlib import Path

import pytest


# Path to run.sh
RUN_SH = Path(__file__).parent.parent / 'run.sh'

# Marker for tests that require site-config/infrastructure
requires_infrastructure = pytest.mark.requires_infrastructure


class TestRequiresRoot:
    """Test CLI requires_root check."""

    def test_pve_setup_local_requires_root(self):
        """pve-setup --local should fail when not root."""
        result = subprocess.run(
            [str(RUN_SH), '--scenario', 'pve-setup', '--local'],
            capture_output=True,
            text=True
        )
        # Should fail with exit code 1 when not root
        if result.returncode == 1:
            assert "requires root privileges" in result.stderr or "requires root privileges" in result.stdout
        else:
            # If running as root, it would attempt the scenario
            pytest.skip("Running as root - cannot test non-root failure")

    def test_user_setup_local_requires_root(self):
        """user-setup --local should fail when not root."""
        result = subprocess.run(
            [str(RUN_SH), '--scenario', 'user-setup', '--local'],
            capture_output=True,
            text=True
        )
        if result.returncode == 1:
            assert "requires root privileges" in result.stderr or "requires root privileges" in result.stdout
        else:
            pytest.skip("Running as root - cannot test non-root failure")

    def test_packer_build_local_does_not_require_root(self):
        """packer-build --local should not fail due to root check."""
        result = subprocess.run(
            [str(RUN_SH), '--scenario', 'packer-build', '--local'],
            capture_output=True,
            text=True,
            timeout=10
        )
        # Should NOT contain requires_root error
        combined_output = result.stdout + result.stderr
        assert "requires root privileges" not in combined_output


class TestAutoDetectHost:
    """Test CLI auto-detect host from hostname."""

    @requires_infrastructure
    def test_packer_build_local_auto_detects_host(self):
        """packer-build --local should auto-detect host from hostname."""
        # Get current hostname
        hostname_result = subprocess.run(['hostname'], capture_output=True, text=True)
        hostname = hostname_result.stdout.strip()

        result = subprocess.run(
            [str(RUN_SH), '--scenario', 'packer-build', '--local'],
            capture_output=True,
            text=True,
            timeout=10
        )
        combined_output = result.stdout + result.stderr
        # Should either auto-detect or fail gracefully
        if f"Auto-detected host from hostname: {hostname}" in combined_output:
            pass  # Success - auto-detected
        elif "Could not auto-detect host" in combined_output:
            pass  # Expected if hostname doesn't match a node
        elif "No host config needed" in combined_output:
            pass  # Expected if requires_host_config is False
        else:
            # If it starts running phases, that's also success
            assert "Starting scenario" in combined_output or "Running phase" in combined_output


class TestListScenarios:
    """Test CLI --list-scenarios works correctly."""

    def test_list_scenarios_shows_all(self):
        """--list-scenarios should show active scenarios."""
        result = subprocess.run(
            [str(RUN_SH), '--list-scenarios'],
            capture_output=True,
            text=True
        )
        assert result.returncode == 0
        output = result.stdout
        # Check active scenarios are listed
        assert 'pve-setup' in output
        assert 'user-setup' in output
        assert 'packer-build' in output
        # Retired scenarios should NOT appear (check with leading whitespace to avoid substring matches)
        lines = output.split('\n')
        scenario_names = [line.strip().split()[0] for line in lines if line.strip() and not line.startswith('Available') and not line.startswith('Usage')]
        assert 'vm-roundtrip' not in scenario_names
        assert 'nested-pve-constructor' not in scenario_names
        assert 'recursive-pve-constructor' not in scenario_names

    def test_list_scenarios_shows_runtime_estimates(self):
        """--list-scenarios should show runtime estimates."""
        result = subprocess.run(
            [str(RUN_SH), '--list-scenarios'],
            capture_output=True,
            text=True
        )
        assert result.returncode == 0
        output = result.stdout
        # Check runtime estimates are shown (format: ~Nm or ~Ns)
        assert '~2m' in output  # bootstrap-install
        assert '~30s' in output  # user-setup, packer-sync


class TestRetiredScenarios:
    """Test CLI migration hints for retired scenarios."""

    def test_retired_scenario_shows_hint(self):
        """Retired scenario should show migration hint."""
        result = subprocess.run(
            [str(RUN_SH), '--scenario', 'vm-roundtrip', '--host', 'father'],
            capture_output=True,
            text=True
        )
        assert result.returncode == 1
        combined = result.stdout + result.stderr
        assert "retired" in combined.lower()
        assert "create" in combined or "test" in combined  # Migration hint

    def test_retired_nested_pve_shows_hint(self):
        """Retired nested-pve scenario should show migration hint."""
        result = subprocess.run(
            [str(RUN_SH), '--scenario', 'nested-pve-roundtrip', '--host', 'father'],
            capture_output=True,
            text=True
        )
        assert result.returncode == 1
        combined = result.stdout + result.stderr
        assert "retired" in combined.lower()

    def test_retired_recursive_pve_shows_hint(self):
        """Retired recursive-pve scenario should show migration hint."""
        result = subprocess.run(
            [str(RUN_SH), '--scenario', 'recursive-pve-constructor', '--host', 'father'],
            capture_output=True,
            text=True
        )
        assert result.returncode == 1
        combined = result.stdout + result.stderr
        assert "retired" in combined.lower()


class TestScenarioVerb:
    """Test 'scenario' verb command."""

    def test_scenario_verb_lists_phases(self):
        """scenario verb should list phases like legacy --scenario."""
        result = subprocess.run(
            [str(RUN_SH), 'scenario', 'pve-setup', '--list-phases',
             '--host', 'father', '--skip-preflight'],
            capture_output=True,
            text=True
        )
        assert result.returncode == 0
        assert 'ensure_pve' in result.stdout

    def test_scenario_verb_no_name_shows_usage(self):
        """scenario verb with no name shows usage."""
        result = subprocess.run(
            [str(RUN_SH), 'scenario'],
            capture_output=True,
            text=True
        )
        assert result.returncode == 1
        assert 'Usage' in result.stdout

    def test_scenario_verb_help_lists_scenarios(self):
        """scenario verb --help lists available scenarios."""
        result = subprocess.run(
            [str(RUN_SH), 'scenario', '--help'],
            capture_output=True,
            text=True
        )
        assert result.returncode == 0
        assert 'pve-setup' in result.stdout

    def test_scenario_verb_retired_shows_hint(self):
        """scenario verb with retired scenario shows migration hint."""
        result = subprocess.run(
            [str(RUN_SH), 'scenario', 'vm-roundtrip', '-H', 'father'],
            capture_output=True,
            text=True
        )
        assert result.returncode == 1
        assert 'retired' in (result.stdout + result.stderr).lower()

    def test_no_deprecation_warning_with_verb(self):
        """scenario verb should NOT show deprecation warning."""
        result = subprocess.run(
            [str(RUN_SH), 'scenario', 'pve-setup', '--list-phases',
             '--host', 'father', '--skip-preflight'],
            capture_output=True,
            text=True
        )
        combined = result.stdout + result.stderr
        assert 'deprecated' not in combined.lower()

    def test_legacy_flag_shows_deprecation(self):
        """Legacy --scenario should show deprecation warning."""
        result = subprocess.run(
            [str(RUN_SH), '--scenario', 'pve-setup', '--list-phases',
             '--host', 'father', '--skip-preflight'],
            capture_output=True,
            text=True
        )
        combined = result.stdout + result.stderr
        assert 'deprecated' in combined.lower()


class TestTopLevelUsage:
    """Test top-level usage display."""

    def test_no_args_shows_usage(self):
        """No arguments shows top-level usage."""
        result = subprocess.run(
            [str(RUN_SH)],
            capture_output=True,
            text=True
        )
        assert result.returncode == 0
        assert 'scenario' in result.stdout
        assert 'create' in result.stdout
        assert 'serve' in result.stdout

    def test_unknown_command_shows_error(self):
        """Unknown command shows error and usage."""
        result = subprocess.run(
            [str(RUN_SH), 'foobar'],
            capture_output=True,
            text=True
        )
        assert result.returncode == 1
        assert 'Unknown command' in result.stdout


class TestTimeoutFlag:
    """Test CLI --timeout flag."""

    def test_timeout_flag_accepted(self):
        """--timeout flag should be accepted by CLI."""
        result = subprocess.run(
            [str(RUN_SH), '--help'],
            capture_output=True,
            text=True
        )
        assert result.returncode == 0
        assert '--timeout' in result.stdout or '-t' in result.stdout

    @requires_infrastructure
    def test_timeout_shown_in_log(self):
        """Timeout should be shown in log when scenario starts."""
        # Use a scenario that doesn't require host config and will fail quickly
        result = subprocess.run(
            [str(RUN_SH), '--scenario', 'packer-build', '--local', '--timeout', '5'],
            capture_output=True,
            text=True,
            timeout=30
        )
        combined = result.stdout + result.stderr
        # Should show timeout in startup message
        assert 'timeout: 5s' in combined or 'timeout' in combined.lower()


class TestVmIdFlag:
    """Test CLI --vm-id flag."""

    def test_vm_id_flag_accepted(self):
        """--vm-id flag should be accepted by CLI."""
        result = subprocess.run(
            [str(RUN_SH), '--help'],
            capture_output=True,
            text=True
        )
        assert result.returncode == 0
        assert '--vm-id' in result.stdout

    @requires_infrastructure
    def test_vm_id_invalid_format_no_equals(self):
        """--vm-id without = should fail with clear error."""
        result = subprocess.run(
            [str(RUN_SH), '--scenario', 'pve-setup', '--host', 'father',
             '--skip-preflight', '--vm-id', 'badformat'],
            capture_output=True,
            text=True
        )
        assert result.returncode == 1
        combined = result.stdout + result.stderr
        assert "Invalid --vm-id format" in combined
        assert "Expected NAME=VMID" in combined

    @requires_infrastructure
    def test_vm_id_invalid_format_non_numeric(self):
        """--vm-id with non-numeric ID should fail with clear error."""
        result = subprocess.run(
            [str(RUN_SH), '--scenario', 'pve-setup', '--host', 'father',
             '--skip-preflight', '--vm-id', 'test=abc'],
            capture_output=True,
            text=True
        )
        assert result.returncode == 1
        combined = result.stdout + result.stderr
        assert "Invalid --vm-id format" in combined
        assert "VMID must be an integer" in combined

    @requires_infrastructure
    def test_vm_id_empty_name_rejected(self):
        """--vm-id with empty name should fail with clear error."""
        result = subprocess.run(
            [str(RUN_SH), '--scenario', 'pve-setup', '--host', 'father',
             '--skip-preflight', '--vm-id', '=99990'],
            capture_output=True,
            text=True
        )
        assert result.returncode == 1
        combined = result.stdout + result.stderr
        assert "Invalid --vm-id format" in combined
        assert "VM name cannot be empty" in combined

    @requires_infrastructure
    def test_vm_id_valid_format_accepted(self):
        """Valid --vm-id should be accepted (though scenario may fail for other reasons)."""
        result = subprocess.run(
            [str(RUN_SH), '--scenario', 'pve-setup', '--host', 'father',
             '--vm-id', 'test=99990', '--list-phases'],
            capture_output=True,
            text=True
        )
        # --list-phases should succeed even with --vm-id
        assert result.returncode == 0
        assert "Phases for scenario" in result.stdout


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
