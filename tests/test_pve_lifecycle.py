"""Tests for actions/pve_lifecycle module.

Unit tests for PVE lifecycle actions and helpers.
"""

import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

# Add src to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent / 'src'))

from common import ActionResult
from actions.pve_lifecycle import _image_to_asset_name


class TestImageToAssetName:
    """Tests for _image_to_asset_name helper."""

    def test_plain_debian_12(self):
        assert _image_to_asset_name('debian-12') == 'debian-12-custom.qcow2'

    def test_plain_debian_13(self):
        assert _image_to_asset_name('debian-13') == 'debian-13-custom.qcow2'

    def test_already_custom(self):
        assert _image_to_asset_name('debian-12-custom') == 'debian-12-custom.qcow2'

    def test_pve_image(self):
        assert _image_to_asset_name('debian-13-pve') == 'debian-13-pve.qcow2'

    def test_unknown_image(self):
        assert _image_to_asset_name('ubuntu-22') == 'ubuntu-22-custom.qcow2'


class TestEnsureImageAction:
    """Tests for EnsureImageAction."""

    @patch('actions.pve_lifecycle.run_ssh')
    def test_image_exists(self, mock_ssh):
        from actions.pve_lifecycle import EnsureImageAction

        mock_ssh.return_value = (0, '/var/lib/vz/template/iso/debian-12-custom.img\n', '')

        action = EnsureImageAction(name='test-ensure')
        config = MagicMock()
        config.ssh_host = '198.51.100.61'
        config.ssh_user = 'root'

        result = action.run(config, {})
        assert result.success is True
        assert 'exists' in result.message.lower() or 'found' in result.message.lower() or result.success

    @patch('actions.pve_lifecycle.run_ssh')
    def test_image_not_found(self, mock_ssh):
        from actions.pve_lifecycle import EnsureImageAction

        mock_ssh.return_value = (1, '', 'No such file')

        action = EnsureImageAction(name='test-ensure')
        config = MagicMock()
        config.ssh_host = '198.51.100.61'
        config.ssh_user = 'root'

        result = action.run(config, {})
        # Should fail when image not found
        assert result.success is False


class TestBootstrapAction:
    """Tests for BootstrapAction."""

    @patch('actions.pve_lifecycle.run_ssh')
    def test_requires_host_attr_in_context(self, mock_ssh):
        from actions.pve_lifecycle import BootstrapAction

        action = BootstrapAction(name='test-bootstrap', host_attr='pve_ip')
        config = MagicMock()

        result = action.run(config, {})
        assert result.success is False
        assert 'pve_ip' in result.message

    @patch('actions.pve_lifecycle.run_ssh')
    def test_success_with_host_in_context(self, mock_ssh):
        from actions.pve_lifecycle import BootstrapAction

        # Simulate bootstrap success
        mock_ssh.return_value = (0, 'Bootstrap complete', '')

        action = BootstrapAction(name='test-bootstrap', host_attr='pve_ip')
        config = MagicMock()
        config.ssh_user = 'root'

        result = action.run(config, {'pve_ip': '198.51.100.10'})
        assert result.success is True

    @patch('actions.pve_lifecycle.run_ssh')
    @patch.dict('os.environ', {'HOMESTAK_SOURCE': 'https://198.51.100.61:44443', 'HOMESTAK_REF': '_working'}, clear=False)
    def test_serve_repos_uses_insecure_tls(self, mock_ssh):
        """Serve-repos path must pass -k to curl and HOMESTAK_INSECURE=1."""
        from actions.pve_lifecycle import BootstrapAction

        mock_ssh.return_value = (0, 'Bootstrap complete', '')

        action = BootstrapAction(name='test-bootstrap', host_attr='pve_ip')
        config = MagicMock()
        config.automation_user = 'homestak'

        result = action.run(config, {'pve_ip': '198.51.100.10'})
        assert result.success is True

        # Verify the SSH command used curl -k and HOMESTAK_INSECURE=1
        ssh_cmd = mock_ssh.call_args_list[-1][0][1]  # last call, second positional arg
        assert 'curl -fsSLk' in ssh_cmd, f"Expected curl -k flag in: {ssh_cmd}"
        assert 'HOMESTAK_INSECURE=1' in ssh_cmd, f"Expected HOMESTAK_INSECURE=1 in: {ssh_cmd}"


class TestCopySecretsAction:
    """Tests for CopySecretsAction."""

    @patch('actions.pve_lifecycle.run_ssh')
    def test_requires_host_attr_in_context(self, mock_ssh):
        from actions.pve_lifecycle import CopySecretsAction

        action = CopySecretsAction(name='test-secrets', host_attr='pve_ip')
        config = MagicMock()

        result = action.run(config, {})
        assert result.success is False
        assert 'pve_ip' in result.message


class TestCreateApiTokenAction:
    """Tests for CreateApiTokenAction."""

    @patch('actions.pve_lifecycle.run_ssh')
    def test_requires_host_attr_in_context(self, mock_ssh):
        from actions.pve_lifecycle import CreateApiTokenAction

        action = CreateApiTokenAction(name='test-token', host_attr='pve_ip')
        config = MagicMock()

        result = action.run(config, {})
        assert result.success is False
        assert 'pve_ip' in result.message


class TestConfigureNetworkBridgeAction:
    """Tests for ConfigureNetworkBridgeAction."""

    @patch('actions.pve_lifecycle.run_ssh')
    def test_requires_host_attr_in_context(self, mock_ssh):
        from actions.pve_lifecycle import ConfigureNetworkBridgeAction

        action = ConfigureNetworkBridgeAction(name='test-bridge', host_attr='pve_ip')
        config = MagicMock()

        result = action.run(config, {})
        assert result.success is False
        assert 'pve_ip' in result.message


class TestGenerateNodeConfigAction:
    """Tests for GenerateNodeConfigAction."""

    @patch('actions.pve_lifecycle.run_ssh')
    def test_requires_host_attr_in_context(self, mock_ssh):
        from actions.pve_lifecycle import GenerateNodeConfigAction

        action = GenerateNodeConfigAction(name='test-nodeconfig', host_attr='pve_ip')
        config = MagicMock()

        result = action.run(config, {})
        assert result.success is False
        assert 'pve_ip' in result.message
