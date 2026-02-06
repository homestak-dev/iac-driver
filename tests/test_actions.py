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
    ssh_host: str = '192.0.2.1'  # TEST-NET-1 (RFC 5737)
    ssh_user: str = 'root'  # For PVE host connections
    automation_user: str = 'homestak'  # For VM connections
    config_file: Path = Path('/tmp/test.yaml')


class TestSSHCommandAction:
    """Test SSHCommandAction."""

    def test_success_returns_action_result(self):
        """Successful SSH command should return success=True."""
        from actions.ssh import SSHCommandAction

        action = SSHCommandAction(name='test', command='echo hello', host_key='inner_ip')
        config = MockHostConfig()
        context = {'inner_ip': '192.0.2.1'}

        with patch('actions.ssh.run_ssh', return_value=(0, 'hello\n', '')):
            result = action.run(config, context)

        assert result.success is True
        assert 'hello' in result.message

    def test_failure_returns_action_result(self):
        """Failed SSH command should return success=False."""
        from actions.ssh import SSHCommandAction

        action = SSHCommandAction(name='test', command='false', host_key='inner_ip')
        config = MockHostConfig()
        context = {'inner_ip': '192.0.2.1'}

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
        context = {'inner_ip': '192.0.2.1'}

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


class TestWaitForFileAction:
    """Test WaitForFileAction."""

    def test_file_found_immediately(self):
        """File found on first poll should return success."""
        from actions.ssh import WaitForFileAction

        action = WaitForFileAction(
            name='test', host_key='vm_ip',
            file_path='/tmp/marker.json', timeout=10, interval=1,
        )
        config = MockHostConfig()
        context = {'vm_ip': '192.0.2.1'}

        with patch('actions.ssh.run_ssh', return_value=(0, 'EXISTS', '')):
            result = action.run(config, context)

        assert result.success is True
        assert 'marker.json' in result.message

    def test_file_not_found_timeout(self):
        """File never found should return failure after timeout."""
        from actions.ssh import WaitForFileAction

        action = WaitForFileAction(
            name='test', host_key='vm_ip',
            file_path='/tmp/missing.json', timeout=1, interval=0.5,
        )
        config = MockHostConfig()
        context = {'vm_ip': '192.0.2.1'}

        with patch('actions.ssh.run_ssh', return_value=(1, '', 'not found')):
            result = action.run(config, context)

        assert result.success is False
        assert 'Timeout' in result.message

    def test_missing_host_returns_error(self):
        """Missing host in context should return failure."""
        from actions.ssh import WaitForFileAction

        action = WaitForFileAction(
            name='test', host_key='missing', file_path='/tmp/x',
        )
        config = MockHostConfig()
        context = {}

        result = action.run(config, context)

        assert result.success is False
        assert 'missing' in result.message


class TestVerifyPackagesAction:
    """Test VerifyPackagesAction."""

    def test_all_packages_installed(self):
        """All packages installed should return success."""
        from scenarios.spec_vm import VerifyPackagesAction

        action = VerifyPackagesAction(
            name='test', host_key='vm_ip', packages=('htop', 'curl'),
        )
        config = MockHostConfig()
        context = {'vm_ip': '192.0.2.1'}

        with patch('scenarios.spec_vm.run_ssh', return_value=(0, 'INSTALLED', '')):
            result = action.run(config, context)

        assert result.success is True
        assert 'htop' in result.message

    def test_missing_package_fails(self):
        """Missing package should return failure."""
        from scenarios.spec_vm import VerifyPackagesAction

        action = VerifyPackagesAction(
            name='test', host_key='vm_ip', packages=('htop', 'missing-pkg'),
        )
        config = MockHostConfig()
        context = {'vm_ip': '192.0.2.1'}

        with patch('scenarios.spec_vm.run_ssh') as mock_ssh:
            mock_ssh.side_effect = [
                (0, 'INSTALLED', ''),  # htop
                (0, 'MISSING', ''),    # missing-pkg
            ]
            result = action.run(config, context)

        assert result.success is False
        assert 'missing-pkg' in result.message

    def test_missing_host_returns_error(self):
        """Missing host in context should return failure."""
        from scenarios.spec_vm import VerifyPackagesAction

        action = VerifyPackagesAction(name='test', packages=('curl',), host_key='missing')
        config = MockHostConfig()
        result = action.run(config, {})

        assert result.success is False
        assert 'missing' in result.message


class TestVerifyUserAction:
    """Test VerifyUserAction."""

    def test_user_exists(self):
        """User exists should return success."""
        from scenarios.spec_vm import VerifyUserAction

        action = VerifyUserAction(
            name='test', host_key='vm_ip', username='homestak',
        )
        config = MockHostConfig()
        context = {'vm_ip': '192.0.2.1'}

        with patch('scenarios.spec_vm.run_ssh',
                   return_value=(0, 'uid=1000(homestak) gid=1000(homestak)\nUSER_EXISTS', '')):
            result = action.run(config, context)

        assert result.success is True
        assert 'homestak' in result.message

    def test_user_missing_fails(self):
        """Missing user should return failure."""
        from scenarios.spec_vm import VerifyUserAction

        action = VerifyUserAction(
            name='test', host_key='vm_ip', username='noone',
        )
        config = MockHostConfig()
        context = {'vm_ip': '192.0.2.1'}

        with patch('scenarios.spec_vm.run_ssh',
                   return_value=(1, 'USER_MISSING', '')):
            result = action.run(config, context)

        assert result.success is False
        assert 'noone' in result.message

    def test_missing_host_returns_error(self):
        """Missing host in context should return failure."""
        from scenarios.spec_vm import VerifyUserAction

        action = VerifyUserAction(name='test', username='homestak', host_key='missing')
        config = MockHostConfig()
        result = action.run(config, {})

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

    def test_tofu_apply_applies_vmid_override(self):
        """TofuApplyAction should apply vm_id_overrides from context."""
        from actions.tofu import TofuApplyAction

        action = TofuApplyAction(
            name='test',
            env_name='test'
        )

        # Mock ConfigResolver returns a VM with original vmid
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
        # Context includes vm_id_overrides
        context = {'vm_id_overrides': {'test-vm': 99950}}

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

        # Verify override was applied - the resolved dict should have been modified
        assert mock_resolved['vms'][0]['vmid'] == 99950
        # Context updates should use the overridden value
        if result.context_updates:
            assert result.context_updates.get('test-vm_vm_id') == 99950


class TestDownloadGitHubReleaseAction:
    """Test DownloadGitHubReleaseAction including split file handling."""

    def test_direct_download_success(self):
        """Direct download of single file should succeed."""
        from actions.file import DownloadGitHubReleaseAction

        action = DownloadGitHubReleaseAction(
            name='test',
            asset_name='debian-12-custom.qcow2',
            host_key='inner_ip'
        )

        config = MagicMock()
        config.packer_release_repo = 'homestak-dev/packer'
        config.packer_release = 'v0.20'
        context = {'inner_ip': '192.0.2.1'}

        with patch('actions.file.run_ssh') as mock_ssh:
            # mkdir success, download success, mv rename, verify success
            mock_ssh.side_effect = [
                (0, '', ''),      # mkdir
                (0, '', ''),      # curl download
                (0, '', ''),      # mv rename (rename_ext='.img' by default)
                (0, '-rw-r--r-- 1 root root 123456 file', ''),  # ls verify
            ]
            result = action.run(config, context)

        assert result.success is True
        assert 'Downloaded' in result.message

    def test_missing_host_key_returns_error(self):
        """Missing host_key in context should return failure."""
        from actions.file import DownloadGitHubReleaseAction

        action = DownloadGitHubReleaseAction(
            name='test',
            asset_name='test.qcow2',
            host_key='nonexistent'
        )

        config = MagicMock()
        config.packer_release_repo = 'homestak-dev/packer'
        config.packer_release = 'v0.20'
        context = {}

        result = action.run(config, context)

        assert result.success is False
        assert 'nonexistent' in result.message

    def test_split_file_detection_and_download(self):
        """Should detect split files and download/reassemble them."""
        from actions.file import DownloadGitHubReleaseAction

        action = DownloadGitHubReleaseAction(
            name='test',
            asset_name='debian-13-pve.qcow2',
            host_key='inner_ip'
        )

        config = MagicMock()
        config.packer_release_repo = 'homestak-dev/packer'
        config.packer_release = 'v0.20'
        context = {'inner_ip': '192.0.2.1'}

        with patch('actions.file.run_ssh') as mock_ssh:
            mock_ssh.side_effect = [
                (0, '', ''),      # mkdir
                (1, '', 'curl: (22) 404'),  # direct download fails
                # _get_split_parts returns parts
                (0, 'debian-13-pve.qcow2.partaa\ndebian-13-pve.qcow2.partab\n', ''),
                (0, '', ''),      # download partaa
                (0, '', ''),      # download partab
                (0, '', ''),      # cat reassemble
                (0, '', ''),      # rm cleanup
                (0, '', ''),      # mv rename (rename_ext='.img' by default)
                (0, '-rw-r--r-- 1 root root 123456 file', ''),  # ls verify
            ]
            result = action.run(config, context)

        assert result.success is True
        assert 'Downloaded' in result.message

    def test_split_file_part_download_failure(self):
        """Failure to download a part should clean up and return error."""
        from actions.file import DownloadGitHubReleaseAction

        action = DownloadGitHubReleaseAction(
            name='test',
            asset_name='debian-13-pve.qcow2',
            host_key='inner_ip'
        )

        config = MagicMock()
        config.packer_release_repo = 'homestak-dev/packer'
        config.packer_release = 'v0.20'
        context = {'inner_ip': '192.0.2.1'}

        with patch('actions.file.run_ssh') as mock_ssh:
            mock_ssh.side_effect = [
                (0, '', ''),      # mkdir
                (1, '', 'curl: (22) 404'),  # direct download fails
                # _get_split_parts returns parts
                (0, 'debian-13-pve.qcow2.partaa\ndebian-13-pve.qcow2.partab\n', ''),
                (0, '', ''),      # download partaa succeeds
                (1, '', 'curl: (22) 404'),  # download partab fails
                (0, '', ''),      # cleanup (rm parts)
            ]
            result = action.run(config, context)

        assert result.success is False
        assert 'partab' in result.message or 'Failed' in result.message

    def test_no_split_parts_returns_original_error(self):
        """If no split parts found, should return original download error."""
        from actions.file import DownloadGitHubReleaseAction

        action = DownloadGitHubReleaseAction(
            name='test',
            asset_name='nonexistent.qcow2',
            host_key='inner_ip'
        )

        config = MagicMock()
        config.packer_release_repo = 'homestak-dev/packer'
        config.packer_release = 'v0.20'
        context = {'inner_ip': '192.0.2.1'}

        with patch('actions.file.run_ssh') as mock_ssh:
            mock_ssh.side_effect = [
                (0, '', ''),      # mkdir
                (1, '', 'curl: (22) 404'),  # direct download fails
                (0, '', ''),      # _get_split_parts returns empty
            ]
            result = action.run(config, context)

        assert result.success is False
        assert 'no split parts found' in result.message

    def test_latest_tag_resolution(self):
        """Should resolve 'latest' tag via GitHub API."""
        from actions.file import DownloadGitHubReleaseAction

        action = DownloadGitHubReleaseAction(
            name='test',
            asset_name='test.qcow2',
            host_key='inner_ip'
        )

        config = MagicMock()
        config.packer_release_repo = 'homestak-dev/packer'
        config.packer_release = 'latest'
        context = {'inner_ip': '192.0.2.1'}

        with patch('actions.file.run_ssh') as mock_ssh:
            mock_ssh.side_effect = [
                (0, 'v0.25\n', ''),  # resolve latest -> v0.25
                (0, '', ''),      # mkdir
                (0, '', ''),      # curl download
                (0, '', ''),      # mv rename (rename_ext='.img' by default)
                (0, '-rw-r--r-- 1 root root 123456 file', ''),  # ls verify
            ]
            result = action.run(config, context)

        assert result.success is True

    def test_rename_extension(self):
        """Should rename .qcow2 to .img by default."""
        from actions.file import DownloadGitHubReleaseAction

        action = DownloadGitHubReleaseAction(
            name='test',
            asset_name='debian-12-custom.qcow2',
            host_key='inner_ip',
            rename_ext='.img'
        )

        config = MagicMock()
        config.packer_release_repo = 'homestak-dev/packer'
        config.packer_release = 'v0.20'
        context = {'inner_ip': '192.0.2.1'}

        with patch('actions.file.run_ssh') as mock_ssh:
            mock_ssh.side_effect = [
                (0, '', ''),      # mkdir
                (0, '', ''),      # curl download
                (0, '', ''),      # mv rename
                (0, '-rw-r--r-- 1 root root 123456 file', ''),  # ls verify
            ]
            result = action.run(config, context)

        assert result.success is True
        assert 'debian-12-custom.img' in result.message

    def test_get_split_parts_returns_sorted_list(self):
        """_get_split_parts should return sorted list of part filenames."""
        from actions.file import DownloadGitHubReleaseAction

        action = DownloadGitHubReleaseAction(
            name='test',
            asset_name='large-file.qcow2',
            host_key='inner_ip'
        )

        with patch('actions.file.run_ssh') as mock_ssh:
            # API returns parts in arbitrary order
            mock_ssh.return_value = (0, 'large-file.qcow2.partab\nlarge-file.qcow2.partaa\n', '')
            parts = action._get_split_parts('repo/name', 'v1.0', '192.0.2.1')

        # Should be sorted alphabetically
        assert parts == ['large-file.qcow2.partab', 'large-file.qcow2.partaa']


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
            context_updates={'vm_ip': '192.0.2.1'}
        )
        assert result.context_updates == {'vm_ip': '192.0.2.1'}
