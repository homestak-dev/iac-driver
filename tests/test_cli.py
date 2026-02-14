"""Tests for CLI module."""

import os
import pytest
from unittest.mock import patch, MagicMock
from pathlib import Path


class TestCreateLocalConfig:
    """Tests for create_local_config() function."""

    @patch('config_resolver.ConfigResolver')
    @patch('socket.gethostname')
    def test_creates_config_with_local_name(self, mock_hostname, mock_resolver_class):
        """Config should have name='local'."""
        mock_hostname.return_value = 'testhost'
        mock_resolver_class.side_effect = Exception("Not needed")

        from src.cli import create_local_config
        config = create_local_config()

        assert config.name == 'local'

    @patch('config_resolver.ConfigResolver')
    @patch('socket.gethostname')
    def test_sets_localhost_api_endpoint(self, mock_hostname, mock_resolver_class):
        """API endpoint should be localhost:8006."""
        mock_hostname.return_value = 'testhost'
        mock_resolver_class.side_effect = Exception("Not needed")

        from src.cli import create_local_config
        config = create_local_config()

        assert config.api_endpoint == 'https://localhost:8006'

    @patch('config_resolver.ConfigResolver')
    @patch('socket.gethostname')
    def test_sets_localhost_ssh_host(self, mock_hostname, mock_resolver_class):
        """SSH host should be localhost, ssh_user defaults to current user."""
        mock_hostname.return_value = 'testhost'
        mock_resolver_class.side_effect = Exception("Not needed")

        from src.cli import create_local_config
        config = create_local_config()

        assert config.ssh_host == 'localhost'
        assert config.ssh_user == os.getenv('USER', '')

    @patch('config_resolver.ConfigResolver')
    @patch('socket.gethostname')
    def test_loads_token_for_current_hostname(self, mock_hostname, mock_resolver_class):
        """Should try to load API token matching current hostname."""
        mock_hostname.return_value = 'father'

        mock_resolver = MagicMock()
        mock_resolver.secrets = {
            'api_tokens': {'father': 'root@pam!homestak=secret123'}
        }
        mock_resolver_class.return_value = mock_resolver

        from src.cli import create_local_config
        config = create_local_config()

        assert config.get_api_token() == 'root@pam!homestak=secret123'

    @patch('config_resolver.ConfigResolver')
    @patch('socket.gethostname')
    def test_no_token_when_hostname_not_in_secrets(self, mock_hostname, mock_resolver_class):
        """Should handle missing token gracefully."""
        mock_hostname.return_value = 'unknown-host'

        mock_resolver = MagicMock()
        mock_resolver.secrets = {
            'api_tokens': {'father': 'token1', 'mother': 'token2'}
        }
        mock_resolver_class.return_value = mock_resolver

        from src.cli import create_local_config
        config = create_local_config()

        assert config.get_api_token() == ''

    @patch('config_resolver.ConfigResolver')
    @patch('socket.gethostname')
    def test_handles_missing_secrets_file(self, mock_hostname, mock_resolver_class):
        """Should handle missing secrets.yaml gracefully."""
        mock_hostname.return_value = 'testhost'

        mock_resolver_class.side_effect = FileNotFoundError("secrets.yaml not found")

        from src.cli import create_local_config
        config = create_local_config()

        # Should not raise, config should still be valid
        assert config.name == 'local'
        assert config.api_endpoint == 'https://localhost:8006'

    @patch('config_resolver.ConfigResolver')
    @patch('socket.gethostname')
    def test_handles_resolver_exception(self, mock_hostname, mock_resolver_class):
        """Should handle ConfigResolver failures gracefully."""
        mock_hostname.return_value = 'testhost'
        mock_resolver_class.side_effect = Exception("Site config not found")

        from src.cli import create_local_config
        config = create_local_config()

        # Should not raise, config should still be valid
        assert config.name == 'local'
        assert config.api_endpoint == 'https://localhost:8006'


class TestParseHostArg:
    """Tests for _parse_host_arg() user@host parsing."""

    def test_plain_hostname(self):
        from src.cli import _parse_host_arg
        user, host = _parse_host_arg('father')
        assert user is None
        assert host == 'father'

    def test_user_at_hostname(self):
        from src.cli import _parse_host_arg
        user, host = _parse_host_arg('root@father')
        assert user == 'root'
        assert host == 'father'

    def test_user_at_ip(self):
        from src.cli import _parse_host_arg
        user, host = _parse_host_arg('admin@198.51.100.1')
        assert user == 'admin'
        assert host == '198.51.100.1'

    def test_plain_ip(self):
        from src.cli import _parse_host_arg
        user, host = _parse_host_arg('198.51.100.1')
        assert user is None
        assert host == '198.51.100.1'

    def test_empty_user_at_host(self):
        """Bare @ should result in None user."""
        from src.cli import _parse_host_arg
        user, host = _parse_host_arg('@father')
        assert user is None
        assert host == 'father'
