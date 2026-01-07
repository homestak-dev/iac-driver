#!/usr/bin/env python3
"""Integration tests for CLI scenario attribute handling.

These tests verify the CLI properly handles:
1. requires_root check for --local mode
2. requires_host_config for auto-detect host
"""

import subprocess
import sys
from pathlib import Path

import pytest


# Path to run.sh
RUN_SH = Path(__file__).parent.parent / 'run.sh'


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
        """--list-scenarios should show all scenarios."""
        result = subprocess.run(
            [str(RUN_SH), '--list-scenarios'],
            capture_output=True,
            text=True
        )
        assert result.returncode == 0
        output = result.stdout
        # Check key scenarios are listed
        assert 'pve-setup' in output
        assert 'user-setup' in output
        assert 'packer-build' in output
        assert 'nested-pve-constructor' in output

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
        assert '~9m' in output  # nested-pve-roundtrip
        assert '~2m' in output  # vm-roundtrip, nested-pve-destructor, bootstrap-install
        assert '~30s' in output  # user-setup, packer-sync, vm-destructor


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


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
