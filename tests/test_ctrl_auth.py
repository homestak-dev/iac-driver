"""Tests for controller/auth.py - authentication middleware."""

import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import yaml

# Add src to path for imports
import sys
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from controller.auth import (
    AuthError,
    extract_bearer_token,
    validate_spec_auth,
    validate_repo_token,
)
from resolver.spec_resolver import SpecResolver, SpecNotFoundError
from resolver.base import ResolverError


class TestAuthError:
    """Tests for AuthError dataclass."""

    def test_auth_error_fields(self):
        """AuthError has correct fields."""
        error = AuthError(code="E300", message="Auth required", http_status=401)
        assert error.code == "E300"
        assert error.message == "Auth required"
        assert error.http_status == 401


class TestExtractBearerToken:
    """Tests for extract_bearer_token function."""

    def test_valid_bearer_token(self):
        """Extracts token from valid Bearer header."""
        token = extract_bearer_token("Bearer my-secret-token")
        assert token == "my-secret-token"

    def test_empty_header(self):
        """Returns None for empty header."""
        token = extract_bearer_token("")
        assert token is None

    def test_none_header(self):
        """Returns None for None header."""
        token = extract_bearer_token(None)
        assert token is None

    def test_basic_auth_header(self):
        """Returns None for Basic auth header."""
        token = extract_bearer_token("Basic dXNlcjpwYXNz")
        assert token is None

    def test_bearer_case_sensitive(self):
        """Bearer prefix is case-sensitive."""
        token = extract_bearer_token("bearer my-token")
        assert token is None

    def test_bearer_with_no_token(self):
        """Returns empty string for Bearer with no token."""
        token = extract_bearer_token("Bearer ")
        assert token == ""


class TestValidateSpecAuth:
    """Tests for validate_spec_auth function."""

    @pytest.fixture
    def site_config(self, tmp_path):
        """Create a minimal site-config for auth testing."""
        # Create directories
        (tmp_path / "specs").mkdir(parents=True)
        (tmp_path / "postures").mkdir(parents=True)

        # Create site.yaml
        site_yaml = {"defaults": {"domain": "test.local"}}
        (tmp_path / "site.yaml").write_text(yaml.dump(site_yaml))

        # Create secrets.yaml
        secrets_yaml = {
            "ssh_keys": {},
            "auth": {
                "site_token": "test-site-token",
                "node_tokens": {
                    "prod1": "prod1-node-token",
                    "prod2": "prod2-node-token",
                },
            },
        }
        (tmp_path / "secrets.yaml").write_text(yaml.dump(secrets_yaml))

        # Create postures
        dev_posture = {"auth": {"method": "network"}}
        (tmp_path / "postures" / "dev.yaml").write_text(yaml.dump(dev_posture))

        stage_posture = {"auth": {"method": "site_token"}}
        (tmp_path / "postures" / "stage.yaml").write_text(yaml.dump(stage_posture))

        prod_posture = {"auth": {"method": "node_token"}}
        (tmp_path / "postures" / "prod.yaml").write_text(yaml.dump(prod_posture))

        # Create specs with different postures
        dev_spec = {"schema_version": 1, "access": {"posture": "dev"}}
        (tmp_path / "specs" / "dev-vm.yaml").write_text(yaml.dump(dev_spec))

        stage_spec = {"schema_version": 1, "access": {"posture": "stage"}}
        (tmp_path / "specs" / "stage-vm.yaml").write_text(yaml.dump(stage_spec))

        prod_spec = {"schema_version": 1, "access": {"posture": "prod"}}
        (tmp_path / "specs" / "prod1.yaml").write_text(yaml.dump(prod_spec))
        (tmp_path / "specs" / "prod2.yaml").write_text(yaml.dump(prod_spec))

        return tmp_path

    @pytest.fixture
    def resolver(self, site_config):
        """Create SpecResolver with test site-config."""
        return SpecResolver(etc_path=site_config)

    # Network auth tests (no token required)

    def test_network_auth_no_token_required(self, resolver):
        """Network auth succeeds without any token."""
        error = validate_spec_auth("dev-vm", "", resolver)
        assert error is None

    def test_network_auth_ignores_provided_token(self, resolver):
        """Network auth ignores any provided token."""
        error = validate_spec_auth("dev-vm", "Bearer some-token", resolver)
        assert error is None

    # Site token auth tests

    def test_site_token_auth_success(self, resolver):
        """Site token auth succeeds with correct token."""
        error = validate_spec_auth("stage-vm", "Bearer test-site-token", resolver)
        assert error is None

    def test_site_token_auth_missing_token(self, resolver):
        """Site token auth fails when no token provided."""
        error = validate_spec_auth("stage-vm", "", resolver)
        assert error is not None
        assert error.code == "E300"
        assert error.http_status == 401

    def test_site_token_auth_wrong_token(self, resolver):
        """Site token auth fails with wrong token."""
        error = validate_spec_auth("stage-vm", "Bearer wrong-token", resolver)
        assert error is not None
        assert error.code == "E301"
        assert error.http_status == 403

    def test_site_token_auth_not_configured(self, tmp_path):
        """Site token auth fails when site_token not in secrets."""
        # Create site-config without site_token
        (tmp_path / "specs").mkdir(parents=True)
        (tmp_path / "postures").mkdir(parents=True)
        (tmp_path / "site.yaml").write_text(yaml.dump({"defaults": {}}))
        (tmp_path / "secrets.yaml").write_text(yaml.dump({"auth": {}}))

        stage_posture = {"auth": {"method": "site_token"}}
        (tmp_path / "postures" / "stage.yaml").write_text(yaml.dump(stage_posture))

        spec = {"schema_version": 1, "access": {"posture": "stage"}}
        (tmp_path / "specs" / "test.yaml").write_text(yaml.dump(spec))

        resolver = SpecResolver(etc_path=tmp_path)
        error = validate_spec_auth("test", "Bearer some-token", resolver)

        assert error is not None
        assert error.code == "E500"
        assert "not configured" in error.message

    # Node token auth tests

    def test_node_token_auth_success(self, resolver):
        """Node token auth succeeds with correct token."""
        error = validate_spec_auth("prod1", "Bearer prod1-node-token", resolver)
        assert error is None

    def test_node_token_auth_different_nodes(self, resolver):
        """Each node has unique token."""
        error1 = validate_spec_auth("prod1", "Bearer prod1-node-token", resolver)
        error2 = validate_spec_auth("prod2", "Bearer prod2-node-token", resolver)
        assert error1 is None
        assert error2 is None

        # Cross-node token fails
        error3 = validate_spec_auth("prod1", "Bearer prod2-node-token", resolver)
        assert error3 is not None
        assert error3.code == "E301"

    def test_node_token_auth_missing_token(self, resolver):
        """Node token auth fails when no token provided."""
        error = validate_spec_auth("prod1", "", resolver)
        assert error is not None
        assert error.code == "E300"
        assert error.http_status == 401

    def test_node_token_auth_not_configured(self, tmp_path):
        """Node token auth fails when node_token not in secrets."""
        (tmp_path / "specs").mkdir(parents=True)
        (tmp_path / "postures").mkdir(parents=True)
        (tmp_path / "site.yaml").write_text(yaml.dump({"defaults": {}}))
        (tmp_path / "secrets.yaml").write_text(yaml.dump({"auth": {"node_tokens": {}}}))

        prod_posture = {"auth": {"method": "node_token"}}
        (tmp_path / "postures" / "prod.yaml").write_text(yaml.dump(prod_posture))

        spec = {"schema_version": 1, "access": {"posture": "prod"}}
        (tmp_path / "specs" / "unknown.yaml").write_text(yaml.dump(spec))

        resolver = SpecResolver(etc_path=tmp_path)
        error = validate_spec_auth("unknown", "Bearer some-token", resolver)

        assert error is not None
        assert error.code == "E500"
        assert "not configured" in error.message

    # Error handling tests

    def test_spec_not_found_error(self, resolver):
        """Returns 404 for nonexistent spec."""
        error = validate_spec_auth("nonexistent", "", resolver)
        assert error is not None
        assert error.code == "E200"
        assert error.http_status == 404

    def test_unknown_auth_method(self, tmp_path):
        """Returns 500 for unknown auth method."""
        (tmp_path / "specs").mkdir(parents=True)
        (tmp_path / "postures").mkdir(parents=True)
        (tmp_path / "site.yaml").write_text(yaml.dump({"defaults": {}}))
        (tmp_path / "secrets.yaml").write_text(yaml.dump({}))

        # Create posture with invalid auth method
        bad_posture = {"auth": {"method": "invalid_method"}}
        (tmp_path / "postures" / "bad.yaml").write_text(yaml.dump(bad_posture))

        spec = {"schema_version": 1, "access": {"posture": "bad"}}
        (tmp_path / "specs" / "test.yaml").write_text(yaml.dump(spec))

        resolver = SpecResolver(etc_path=tmp_path)
        error = validate_spec_auth("test", "", resolver)

        assert error is not None
        assert error.code == "E500"
        assert "Unknown auth method" in error.message


class TestValidateRepoToken:
    """Tests for validate_repo_token function."""

    def test_valid_token(self):
        """Validates correct repo token."""
        error = validate_repo_token("Bearer correct-token", "correct-token")
        assert error is None

    def test_missing_token(self):
        """Fails when no token provided."""
        error = validate_repo_token("", "expected-token")
        assert error is not None
        assert error.code == "E300"
        assert error.http_status == 401

    def test_wrong_token(self):
        """Fails with incorrect token."""
        error = validate_repo_token("Bearer wrong-token", "correct-token")
        assert error is not None
        assert error.code == "E301"
        assert error.http_status == 403

    def test_empty_expected_token_disables_auth(self):
        """Empty expected token disables auth (dev mode)."""
        error = validate_repo_token("", "")
        assert error is None

    def test_dev_mode_accepts_any_token(self):
        """Dev mode (empty expected) accepts any token."""
        error = validate_repo_token("Bearer any-token", "")
        assert error is None

    def test_basic_auth_header_fails(self):
        """Basic auth header fails (not Bearer)."""
        error = validate_repo_token("Basic dXNlcjpwYXNz", "some-token")
        assert error is not None
        assert error.code == "E300"
