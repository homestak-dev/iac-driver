"""Tests for validation module."""

import pytest
from unittest.mock import patch, MagicMock

from src.validation import (
    validate_api_token,
    validate_host_resolvable,
    validate_host_reachable,
    validate_host_availability,
    validate_readiness,
    parse_provider_version,
    parse_lockfile_version,
    validate_provider_lockfiles,
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


class TestParseProviderVersion:
    """Tests for parsing provider version from providers.tf."""

    def test_parses_exact_version(self, tmp_path):
        """Parses exact version constraint."""
        providers_tf = tmp_path / "providers.tf"
        providers_tf.write_text('''
terraform {
  required_providers {
    proxmox = {
      source  = "bpg/proxmox"
      version = "0.93.0"
    }
  }
}
''')
        assert parse_provider_version(providers_tf) == "0.93.0"

    def test_parses_version_with_spaces(self, tmp_path):
        """Parses version with extra spaces."""
        providers_tf = tmp_path / "providers.tf"
        providers_tf.write_text('version   =   "1.2.3"')
        assert parse_provider_version(providers_tf) == "1.2.3"

    def test_returns_none_for_missing_file(self, tmp_path):
        """Returns None for missing file."""
        providers_tf = tmp_path / "nonexistent.tf"
        assert parse_provider_version(providers_tf) is None

    def test_returns_none_for_no_version(self, tmp_path):
        """Returns None when no version is specified."""
        providers_tf = tmp_path / "providers.tf"
        providers_tf.write_text('provider "proxmox" {}')
        assert parse_provider_version(providers_tf) is None


class TestParseLockfileVersion:
    """Tests for parsing provider version from .terraform.lock.hcl."""

    def test_parses_lockfile_version(self, tmp_path):
        """Parses version from lockfile."""
        lockfile = tmp_path / ".terraform.lock.hcl"
        lockfile.write_text('''
provider "registry.opentofu.org/bpg/proxmox" {
  version     = "0.92.0"
  constraints = "0.92.0"
  hashes = [
    "h1:abc123",
  ]
}
''')
        assert parse_lockfile_version(lockfile) == "0.92.0"

    def test_parses_multiline_lockfile(self, tmp_path):
        """Parses version from multi-provider lockfile."""
        lockfile = tmp_path / ".terraform.lock.hcl"
        lockfile.write_text('''
provider "registry.opentofu.org/other/provider" {
  version = "1.0.0"
}

provider "registry.opentofu.org/bpg/proxmox" {
  version     = "0.93.0"
  constraints = ">= 0.90.0, 0.93.0"
  hashes = [
    "h1:xyz789",
  ]
}
''')
        assert parse_lockfile_version(lockfile) == "0.93.0"

    def test_returns_none_for_missing_file(self, tmp_path):
        """Returns None for missing file."""
        lockfile = tmp_path / "nonexistent.lock.hcl"
        assert parse_lockfile_version(lockfile) is None

    def test_returns_none_for_no_proxmox_provider(self, tmp_path):
        """Returns None when bpg/proxmox not in lockfile."""
        lockfile = tmp_path / ".terraform.lock.hcl"
        lockfile.write_text('''
provider "registry.opentofu.org/other/provider" {
  version = "1.0.0"
}
''')
        assert parse_lockfile_version(lockfile) is None


class TestValidateProviderLockfiles:
    """Tests for provider lockfile validation."""

    def test_no_states_dir_returns_empty(self, tmp_path):
        """No .states directory returns no errors."""
        # Create providers.tf but no .states
        tofu_dir = tmp_path / "tofu"
        generic_dir = tofu_dir / "envs" / "generic"
        generic_dir.mkdir(parents=True)
        (generic_dir / "providers.tf").write_text('version = "0.93.0"')

        states_dir = tmp_path / ".states"
        # Don't create states_dir - test that missing dir is handled

        errors, fixed = validate_provider_lockfiles(
            _tofu_dir=tofu_dir,
            _states_dir=states_dir
        )
        assert errors == []
        assert fixed == []

    def test_matching_versions_no_errors(self, tmp_path):
        """Matching versions return no errors."""
        # Create providers.tf
        tofu_dir = tmp_path / "tofu"
        generic_dir = tofu_dir / "envs" / "generic"
        generic_dir.mkdir(parents=True)
        (generic_dir / "providers.tf").write_text('version = "0.93.0"')

        # Create state with matching lockfile
        states_dir = tmp_path / ".states"
        state_dir = states_dir / "test-node" / "data"
        state_dir.mkdir(parents=True)
        (state_dir / ".terraform.lock.hcl").write_text('''
provider "registry.opentofu.org/bpg/proxmox" {
  version = "0.93.0"
}
''')

        errors, fixed = validate_provider_lockfiles(
            _tofu_dir=tofu_dir,
            _states_dir=states_dir
        )
        assert errors == []
        assert fixed == []

    def test_stale_lockfile_auto_fixed(self, tmp_path):
        """Stale lockfile is automatically deleted when auto_fix=True."""
        # Create providers.tf with new version
        tofu_dir = tmp_path / "tofu"
        generic_dir = tofu_dir / "envs" / "generic"
        generic_dir.mkdir(parents=True)
        (generic_dir / "providers.tf").write_text('version = "0.93.0"')

        # Create state with old lockfile
        states_dir = tmp_path / ".states"
        state_dir = states_dir / "test-node" / "data"
        state_dir.mkdir(parents=True)
        lockfile = state_dir / ".terraform.lock.hcl"
        lockfile.write_text('''
provider "registry.opentofu.org/bpg/proxmox" {
  version = "0.92.0"
}
''')

        errors, fixed = validate_provider_lockfiles(
            auto_fix=True,
            _tofu_dir=tofu_dir,
            _states_dir=states_dir
        )
        assert errors == []
        assert len(fixed) == 1
        assert "test-node" in fixed[0]
        assert "0.92.0" in fixed[0]
        assert "0.93.0" in fixed[0]
        assert not lockfile.exists()  # Lockfile was deleted

    def test_stale_lockfile_error_without_auto_fix(self, tmp_path):
        """Stale lockfile returns error when auto_fix=False."""
        # Create providers.tf with new version
        tofu_dir = tmp_path / "tofu"
        generic_dir = tofu_dir / "envs" / "generic"
        generic_dir.mkdir(parents=True)
        (generic_dir / "providers.tf").write_text('version = "0.93.0"')

        # Create state with old lockfile
        states_dir = tmp_path / ".states"
        state_dir = states_dir / "test-node" / "data"
        state_dir.mkdir(parents=True)
        lockfile = state_dir / ".terraform.lock.hcl"
        lockfile.write_text('''
provider "registry.opentofu.org/bpg/proxmox" {
  version = "0.92.0"
}
''')

        errors, fixed = validate_provider_lockfiles(
            auto_fix=False,
            _tofu_dir=tofu_dir,
            _states_dir=states_dir
        )
        assert len(errors) == 1
        assert "Stale provider lockfile" in errors[0]
        assert "0.92.0" in errors[0]
        assert "0.93.0" in errors[0]
        assert fixed == []
        assert lockfile.exists()  # Lockfile was NOT deleted

    def test_multiple_stale_lockfiles(self, tmp_path):
        """Multiple stale lockfiles are all handled."""
        # Create providers.tf
        tofu_dir = tmp_path / "tofu"
        generic_dir = tofu_dir / "envs" / "generic"
        generic_dir.mkdir(parents=True)
        (generic_dir / "providers.tf").write_text('version = "0.93.0"')

        # Create multiple states with old lockfiles
        states_dir = tmp_path / ".states"
        for env in ["test-node1", "test-node2"]:
            state_dir = states_dir / env / "data"
            state_dir.mkdir(parents=True)
            (state_dir / ".terraform.lock.hcl").write_text('''
provider "registry.opentofu.org/bpg/proxmox" {
  version = "0.91.0"
}
''')

        errors, fixed = validate_provider_lockfiles(
            auto_fix=True,
            _tofu_dir=tofu_dir,
            _states_dir=states_dir
        )
        assert errors == []
        assert len(fixed) == 2
