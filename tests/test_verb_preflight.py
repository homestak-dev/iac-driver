#!/usr/bin/env python3
"""Tests for preflight checks in verb commands (create/destroy/test).

Verifies that manifest_opr/cli.py calls validate_readiness() before
verb execution, and that --skip-preflight and --dry-run bypass checks.
"""

import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch, MagicMock

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent / 'src'))

import pytest
from manifest_opr.cli import _manifest_requires_nested_virt, _run_preflight


class TestManifestRequiresNestedVirt:
    """Test nested virt detection from manifest."""

    def test_flat_manifest_no_nested_virt(self):
        """Flat manifest (all root nodes) doesn't require nested virt."""
        manifest = SimpleNamespace(nodes=[
            SimpleNamespace(name='vm1', type='vm', parent=None),
            SimpleNamespace(name='vm2', type='vm', parent=None),
        ])
        assert _manifest_requires_nested_virt(manifest) is False

    def test_pve_with_child_requires_nested_virt(self):
        """PVE node with children requires nested virt."""
        manifest = SimpleNamespace(nodes=[
            SimpleNamespace(name='pve1', type='pve', parent=None),
            SimpleNamespace(name='vm1', type='vm', parent='pve1'),
        ])
        assert _manifest_requires_nested_virt(manifest) is True

    def test_pve_without_children_no_nested_virt(self):
        """PVE node without children doesn't require nested virt."""
        manifest = SimpleNamespace(nodes=[
            SimpleNamespace(name='pve1', type='pve', parent=None),
        ])
        assert _manifest_requires_nested_virt(manifest) is False

    def test_vm_parent_no_nested_virt(self):
        """VM parent (not PVE) doesn't trigger nested virt."""
        manifest = SimpleNamespace(nodes=[
            SimpleNamespace(name='vm1', type='vm', parent=None),
            SimpleNamespace(name='vm2', type='vm', parent='vm1'),
        ])
        assert _manifest_requires_nested_virt(manifest) is False


class TestRunPreflight:
    """Test _run_preflight() behavior."""

    def test_skip_preflight_flag_bypasses(self):
        """--skip-preflight should bypass all checks."""
        args = SimpleNamespace(skip_preflight=True, dry_run=False)
        result = _run_preflight(args, MagicMock(), MagicMock())
        assert result is None

    def test_dry_run_bypasses(self):
        """--dry-run should bypass preflight checks."""
        args = SimpleNamespace(skip_preflight=False, dry_run=True)
        result = _run_preflight(args, MagicMock(), MagicMock())
        assert result is None

    @patch('manifest_opr.cli.validate_readiness')
    def test_calls_validate_readiness(self, mock_validate):
        """Should call validate_readiness when not skipped."""
        mock_validate.return_value = []
        config = MagicMock()
        manifest = SimpleNamespace(nodes=[
            SimpleNamespace(name='vm1', type='vm', parent=None),
        ])
        args = SimpleNamespace(skip_preflight=False, dry_run=False)

        result = _run_preflight(args, config, manifest)

        assert result is None
        mock_validate.assert_called_once()
        # Verify the requirements object has expected attributes
        req_class = mock_validate.call_args[0][1]
        assert req_class.requires_api is True
        assert req_class.requires_host_ssh is True
        assert req_class.requires_nested_virt is False

    @patch('manifest_opr.cli.validate_readiness')
    def test_nested_virt_detected_for_tiered_manifest(self, mock_validate):
        """Should set requires_nested_virt=True for tiered manifests."""
        mock_validate.return_value = []
        config = MagicMock()
        manifest = SimpleNamespace(nodes=[
            SimpleNamespace(name='pve1', type='pve', parent=None),
            SimpleNamespace(name='vm1', type='vm', parent='pve1'),
        ])
        args = SimpleNamespace(skip_preflight=False, dry_run=False)

        _run_preflight(args, config, manifest)

        req_class = mock_validate.call_args[0][1]
        assert req_class.requires_nested_virt is True

    @patch('manifest_opr.cli.validate_readiness')
    def test_returns_1_on_errors(self, mock_validate):
        """Should return 1 when preflight finds errors."""
        mock_validate.return_value = ['secrets.yaml not decrypted']
        config = MagicMock()
        manifest = SimpleNamespace(nodes=[
            SimpleNamespace(name='vm1', type='vm', parent=None),
        ])
        args = SimpleNamespace(skip_preflight=False, dry_run=False)

        result = _run_preflight(args, config, manifest)

        assert result == 1


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
