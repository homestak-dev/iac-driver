"""Tests for validation module."""

import pytest
from unittest.mock import patch, MagicMock

from src.validation import (
    validate_api_token,
    validate_host_resolvable,
    validate_host_reachable,
    validate_host_availability,
    validate_readiness,
)


class TestValidateApiToken:
    """Tests for API token validation."""

    def test_missing_endpoint_returns_error(self):
        """Missing API endpoint returns error."""
        errors = validate_api_token(None, "token", "test")
        assert len(errors) == 1
        assert "API endpoint not configured" in errors[0]

    def test_empty_endpoint_returns_error(self):
        """Empty API endpoint returns error."""
        errors = validate_api_token("", "token", "test")
        assert len(errors) == 1
        assert "API endpoint not configured" in errors[0]

    def test_missing_token_returns_error(self):
        """Missing API token returns error with decrypt instructions."""
        errors = validate_api_token("https://localhost:8006", None, "test")
        assert len(errors) == 1
        assert "API token not found" in errors[0]
        assert "make decrypt" in errors[0]

    def test_empty_token_returns_error(self):
        """Empty API token returns error."""
        errors = validate_api_token("https://localhost:8006", "", "test")
        assert len(errors) == 1
        assert "API token not found" in errors[0]

    def test_invalid_format_missing_exclamation_returns_error(self):
        """Token without '!' returns format error."""
        errors = validate_api_token("https://localhost:8006", "bad-token", "test")
        assert len(errors) == 1
        assert "invalid format" in errors[0]

    def test_invalid_format_missing_equals_returns_error(self):
        """Token without '=' returns format error."""
        errors = validate_api_token("https://localhost:8006", "root@pam!token", "test")
        assert len(errors) == 1
        assert "invalid format" in errors[0]

    @patch('src.validation.requests.get')
    def test_401_returns_regenerate_instructions(self, mock_get):
        """401 response returns regeneration instructions."""
        mock_get.return_value.status_code = 401
        errors = validate_api_token(
            "https://localhost:8006",
            "root@pam!test=abc123",
            "test"
        )
        assert len(errors) == 1
        assert "API token invalid" in errors[0]
        assert "pveum user token add" in errors[0]

    @patch('src.validation.requests.get')
    def test_valid_token_returns_empty(self, mock_get):
        """Valid token returns empty error list."""
        mock_get.return_value.status_code = 200
        mock_get.return_value.json.return_value = {"data": {"version": "8.1"}}
        errors = validate_api_token(
            "https://localhost:8006",
            "root@pam!test=abc123",
            "test"
        )
        assert errors == []

    @patch('src.validation.requests.get')
    def test_unexpected_status_returns_error(self, mock_get):
        """Unexpected status code returns error."""
        mock_get.return_value.status_code = 500
        mock_get.return_value.text = "Internal Server Error"
        errors = validate_api_token(
            "https://localhost:8006",
            "root@pam!test=abc123",
            "test"
        )
        assert len(errors) == 1
        assert "Unexpected API response" in errors[0]
        assert "500" in errors[0]

    @patch('src.validation.requests.get')
    def test_connection_error_returns_error(self, mock_get):
        """Connection error returns descriptive error."""
        import requests
        mock_get.side_effect = requests.exceptions.ConnectionError("refused")
        errors = validate_api_token(
            "https://badhost:8006",
            "root@pam!test=abc123",
            "test"
        )
        assert len(errors) == 1
        assert "Cannot connect" in errors[0]

    @patch('src.validation.requests.get')
    def test_timeout_returns_error(self, mock_get):
        """Timeout returns descriptive error."""
        import requests
        mock_get.side_effect = requests.exceptions.Timeout()
        errors = validate_api_token(
            "https://slowhost:8006",
            "root@pam!test=abc123",
            "test"
        )
        assert len(errors) == 1
        assert "Timeout" in errors[0]


class TestValidateHostResolvable:
    """Tests for hostname resolution validation."""

    def test_localhost_resolves(self):
        """localhost resolves to 127.0.0.1."""
        success, result = validate_host_resolvable("localhost")
        assert success is True
        assert result == "127.0.0.1"

    def test_ip_resolves_to_itself(self):
        """IP address resolves to itself."""
        success, result = validate_host_resolvable("127.0.0.1")
        assert success is True
        assert result == "127.0.0.1"

    def test_invalid_hostname_fails(self):
        """Non-existent hostname fails."""
        success, result = validate_host_resolvable("nonexistent.invalid.test")
        assert success is False
        assert "Cannot resolve" in result


class TestValidateHostReachable:
    """Tests for host reachability validation."""

    def test_unreachable_port_fails(self):
        """Unreachable port returns failure."""
        success, message = validate_host_reachable("127.0.0.1", port=59999, timeout=1)
        assert success is False
        assert "Cannot connect" in message

    def test_timeout_returns_failure(self):
        """Timeout returns failure with message."""
        # Use a non-routable IP to trigger timeout
        success, message = validate_host_reachable("10.255.255.1", port=22, timeout=0.5)
        assert success is False
        # Could be timeout or connection refused depending on network


class TestValidateHostAvailability:
    """Tests for combined host availability validation."""

    def test_missing_host_returns_error(self):
        """Missing SSH host returns error."""
        errors = validate_host_availability(None, "test")
        assert len(errors) == 1
        assert "SSH host not configured" in errors[0]

    def test_empty_host_returns_error(self):
        """Empty SSH host returns error."""
        errors = validate_host_availability("", "test")
        assert len(errors) == 1
        assert "SSH host not configured" in errors[0]

    def test_unresolvable_host_returns_error(self):
        """Unresolvable hostname returns error."""
        errors = validate_host_availability("nonexistent.invalid.test", "test")
        assert len(errors) == 1
        assert "Cannot resolve" in errors[0]

    def test_localhost_with_no_checks_passes(self):
        """localhost with no port checks passes."""
        errors = validate_host_availability(
            "localhost", "test",
            check_ssh=False, check_api=False
        )
        assert errors == []


class TestValidateReadiness:
    """Tests for combined readiness validation."""

    def test_scenario_without_api_skips_token_check(self):
        """Scenario with requires_api=False skips token validation."""
        config = MagicMock()
        config.name = "test"
        config.api_endpoint = None  # Would fail if checked
        config.ssh_host = "localhost"

        class NoApiScenario:
            requires_api = False
            requires_host_ssh = False

        errors = validate_readiness(config, NoApiScenario)
        # Should not fail on missing API endpoint
        assert not any("API endpoint" in e for e in errors)

    def test_scenario_without_ssh_skips_host_check(self):
        """Scenario with requires_host_ssh=False skips SSH check."""
        config = MagicMock()
        config.name = "test"
        config.api_endpoint = "https://localhost:8006"
        config._api_token = "root@pam!test=abc"
        config.ssh_host = None  # Would fail if checked

        class NoSshScenario:
            requires_api = False
            requires_host_ssh = False

        errors = validate_readiness(config, NoSshScenario)
        # Should not fail on missing SSH host
        assert not any("SSH host" in e for e in errors)
