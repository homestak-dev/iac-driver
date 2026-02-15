"""Tests for Proxmox action classes.

Tests for StartVMAction.
"""

import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

sys.path.insert(0, str(Path(__file__).parent.parent / 'src'))


class TestStartVMAction:
    """Test StartVMAction."""

    def test_start_vm_success(self):
        """Successful VM start should return success."""
        from actions.proxmox import StartVMAction

        action = StartVMAction(name='test', vm_id_attr='inner_vm_id', pve_host_attr='ssh_host')
        config = MagicMock()
        config.inner_vm_id = 99913
        config.ssh_host = '192.0.2.1'
        config.ssh_user = 'root'
        context = {}

        # Mock start_vm from common.py (which is imported into proxmox.py)
        with patch('actions.proxmox.start_vm', return_value=True):
            result = action.run(config, context)

        assert result.success is True

    def test_start_vm_failure(self):
        """Failed VM start should return failure."""
        from actions.proxmox import StartVMAction

        action = StartVMAction(name='test', vm_id_attr='inner_vm_id', pve_host_attr='ssh_host')
        config = MagicMock()
        config.inner_vm_id = 99913
        config.ssh_host = '192.0.2.1'
        config.ssh_user = 'root'
        context = {}

        with patch('actions.proxmox.start_vm', return_value=False):
            result = action.run(config, context)

        assert result.success is False
        assert 'Failed' in result.message

    def test_missing_vm_id_returns_error(self):
        """Missing vm_id should return failure."""
        from actions.proxmox import StartVMAction

        action = StartVMAction(name='test', vm_id_attr='missing_id')
        config = MagicMock()
        config.ssh_host = '192.0.2.1'
        config.ssh_user = 'root'
        # Simulate getattr returning None for missing attribute
        config.missing_id = None
        context = {}

        result = action.run(config, context)

        assert result.success is False
        assert 'missing' in result.message.lower()
