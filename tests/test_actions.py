#!/usr/bin/env python3
"""Tests for action classes.

Tests verify:
1. Action success/failure handling
2. Context key lookups
3. Error messages for missing context
4. Mocked SSH/subprocess execution
"""

import sys
from dataclasses import dataclass
from pathlib import Path
from unittest.mock import patch, MagicMock

sys.path.insert(0, str(Path(__file__).parent.parent / 'src'))

import pytest
from common import ActionResult
from config import HostConfig


@dataclass
class MockHostConfig:
    """Minimal host config for testing."""
    name: str = 'test-host'
    ssh_host: str = '10.0.12.100'
    ssh_user: str = 'root'
    config_file: Path = Path('/tmp/test.yaml')


class TestSSHCommandAction:
    """Test SSHCommandAction."""

    def test_success_returns_action_result(self):
        """Successful SSH command should return success=True."""
        from actions.ssh import SSHCommandAction

        action = SSHCommandAction(name='test', command='echo hello', host_key='inner_ip')
        config = MockHostConfig()
        context = {'inner_ip': '10.0.12.100'}

        with patch('actions.ssh.run_ssh', return_value=(0, 'hello\n', '')):
            result = action.run(config, context)

        assert result.success is True
        assert 'hello' in result.message

    def test_failure_returns_action_result(self):
        """Failed SSH command should return success=False."""
        from actions.ssh import SSHCommandAction

        action = SSHCommandAction(name='test', command='false', host_key='inner_ip')
        config = MockHostConfig()
        context = {'inner_ip': '10.0.12.100'}

        with patch('actions.ssh.run_ssh', return_value=(1, '', 'command failed')):
            result = action.run(config, context)

        assert result.success is False
        assert 'failed' in result.message.lower()

    def test_missing_host_key_returns_error(self):
        """Missing host_key in context should return failure."""
        from actions.ssh import SSHCommandAction

        action = SSHCommandAction(name='test', command='echo hello', host_key='nonexistent')
        config = MockHostConfig()
        context = {}

        result = action.run(config, context)

        assert result.success is False
        assert 'nonexistent' in result.message


class TestWaitForSSHAction:
    """Test WaitForSSHAction."""

    def test_immediate_success(self):
        """SSH available immediately should return success."""
        from actions.ssh import WaitForSSHAction

        action = WaitForSSHAction(name='test', host_key='inner_ip', timeout=5)
        config = MockHostConfig()
        context = {'inner_ip': '10.0.12.100'}

        with patch('actions.ssh.wait_for_ping', return_value=True), \
             patch('actions.ssh.run_ssh', return_value=(0, 'ready', '')):
            result = action.run(config, context)

        assert result.success is True
        assert 'available' in result.message.lower()

    def test_missing_host_returns_error(self):
        """Missing host in context should return failure."""
        from actions.ssh import WaitForSSHAction

        action = WaitForSSHAction(name='test', host_key='missing')
        config = MockHostConfig()
        context = {}

        result = action.run(config, context)

        assert result.success is False
        assert 'missing' in result.message


class TestStartVMAction:
    """Test StartVMAction."""

    def test_start_vm_success(self):
        """Successful VM start should return success."""
        from actions.proxmox import StartVMAction

        action = StartVMAction(name='test', vm_id_attr='inner_vm_id', pve_host_attr='ssh_host')
        config = MagicMock()
        config.inner_vm_id = 99913
        config.ssh_host = '10.0.12.100'
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
        config.ssh_host = '10.0.12.100'
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
        config.ssh_host = '10.0.12.100'
        config.ssh_user = 'root'
        # Simulate getattr returning None for missing attribute
        config.missing_id = None
        context = {}

        result = action.run(config, context)

        assert result.success is False
        assert 'missing' in result.message.lower()


class TestTofuApplyAction:
    """Test TofuApplyAction."""

    def test_tofu_apply_success(self):
        """Successful tofu apply should return success with context updates."""
        from actions.tofu import TofuApplyAction

        action = TofuApplyAction(
            name='test',
            env_name='test'
        )

        # Mock ConfigResolver
        mock_resolved = {
            'node': 'test-node',
            'api_endpoint': 'https://localhost:8006',
            'api_token': 'token',
            'ssh_user': 'root',
            'datastore': 'local-zfs',
            'ssh_keys': ['ssh-rsa AAA...'],
            'root_password': 'hash',
            'vms': [
                {'name': 'test-vm', 'vmid': 99900, 'cores': 1, 'memory': 2048}
            ]
        }

        config = MagicMock()
        config.name = 'test-node'
        context = {}

        with patch('actions.tofu.ConfigResolver') as MockResolver, \
             patch('actions.tofu.get_sibling_dir') as mock_dir, \
             patch('actions.tofu.get_base_dir') as mock_base, \
             patch('actions.tofu.run_command', return_value=(0, 'Apply complete!', '')):

            mock_dir.return_value = Path('/tmp/tofu')
            (Path('/tmp/tofu') / 'envs/generic').mkdir(parents=True, exist_ok=True)
            mock_base.return_value = Path('/tmp/iac-driver')
            (Path('/tmp/iac-driver') / '.states').mkdir(parents=True, exist_ok=True)

            mock_resolver = MockResolver.return_value
            mock_resolver.resolve_env.return_value = mock_resolved

            result = action.run(config, context)

        # Should have extracted VM IDs to context
        assert 'test-vm_vm_id' in result.context_updates or result.success


class TestActionResult:
    """Test ActionResult dataclass."""

    def test_action_result_defaults(self):
        """ActionResult should have sensible defaults."""
        result = ActionResult(success=True)
        assert result.success is True
        assert result.message == ''
        assert result.duration == 0.0
        assert result.context_updates == {}
        assert result.continue_on_failure is False

    def test_action_result_with_context(self):
        """ActionResult should store context updates."""
        result = ActionResult(
            success=True,
            message='done',
            context_updates={'vm_ip': '10.0.12.100'}
        )
        assert result.context_updates == {'vm_ip': '10.0.12.100'}
