"""Tests for controller/specs.py - spec endpoint handler."""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import yaml

# Add src to path for imports
import sys
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from controller.specs import handle_spec_request, handle_specs_list, _error_response
from resolver.spec_resolver import SpecResolver, SpecNotFoundError, SchemaValidationError
from resolver.base import PostureNotFoundError, SSHKeyNotFoundError, ResolverError


class TestErrorResponse:
    """Tests for _error_response helper."""

    def test_error_response_format(self):
        """Error response has correct structure."""
        response = _error_response("E200", "Not found")
        assert response == {"error": {"code": "E200", "message": "Not found"}}


class TestHandleSpecRequest:
    """Tests for handle_spec_request function."""

    @pytest.fixture
    def site_config(self, tmp_path):
        """Create a minimal site-config for spec testing."""
        # Create directories
        (tmp_path / "v2" / "specs").mkdir(parents=True)
        (tmp_path / "v2" / "postures").mkdir(parents=True)

        # Create site.yaml
        site_yaml = {
            "defaults": {
                "timezone": "America/Denver",
                "domain": "example.com",
            }
        }
        (tmp_path / "site.yaml").write_text(yaml.dump(site_yaml))

        # Create secrets.yaml
        secrets_yaml = {
            "ssh_keys": {
                "admin": "ssh-ed25519 AAAA... admin@host",
            },
            "auth": {
                "site_token": "test-site-token",
            },
        }
        (tmp_path / "secrets.yaml").write_text(yaml.dump(secrets_yaml))

        # Create v2 postures
        dev_posture = {"auth": {"method": "network"}, "ssh": {"port": 22}}
        (tmp_path / "v2" / "postures" / "dev.yaml").write_text(yaml.dump(dev_posture))

        stage_posture = {"auth": {"method": "site_token"}}
        (tmp_path / "v2" / "postures" / "stage.yaml").write_text(yaml.dump(stage_posture))

        # Create specs
        base_spec = {
            "schema_version": 1,
            "access": {
                "posture": "dev",
                "users": [{"name": "root", "ssh_keys": ["admin"]}],
            },
            "platform": {"packages": ["htop"]},
        }
        (tmp_path / "v2" / "specs" / "base.yaml").write_text(yaml.dump(base_spec))

        protected_spec = {
            "schema_version": 1,
            "access": {"posture": "stage"},
        }
        (tmp_path / "v2" / "specs" / "protected.yaml").write_text(yaml.dump(protected_spec))

        return tmp_path

    @pytest.fixture
    def resolver(self, site_config):
        """Create SpecResolver with test site-config."""
        return SpecResolver(etc_path=site_config)

    def test_success_returns_spec(self, resolver):
        """Successful request returns resolved spec."""
        response, status = handle_spec_request("base", "", resolver)

        assert status == 200
        assert response["schema_version"] == 1
        assert response["identity"]["hostname"] == "base"
        assert response["identity"]["domain"] == "example.com"

    def test_success_resolves_ssh_keys(self, resolver):
        """Successful request includes resolved SSH keys."""
        response, status = handle_spec_request("base", "", resolver)

        assert status == 200
        users = response["access"]["users"]
        assert len(users) == 1
        assert users[0]["ssh_keys"][0].startswith("ssh-ed25519")

    def test_success_removes_internal_posture(self, resolver):
        """Response does not include internal _posture field."""
        response, status = handle_spec_request("base", "", resolver)

        assert status == 200
        assert "_posture" not in response.get("access", {})

    def test_auth_failure_returns_error(self, resolver):
        """Auth failure returns appropriate error."""
        # protected spec requires site_token
        response, status = handle_spec_request("protected", "", resolver)

        assert status == 401
        assert "error" in response
        assert response["error"]["code"] == "E300"

    def test_auth_success_with_token(self, resolver):
        """Auth succeeds with correct token."""
        response, status = handle_spec_request(
            "protected", "Bearer test-site-token", resolver
        )

        assert status == 200
        assert "error" not in response

    def test_spec_not_found_returns_404(self, resolver):
        """Nonexistent spec returns 404."""
        response, status = handle_spec_request("nonexistent", "", resolver)

        assert status == 404
        assert response["error"]["code"] == "E200"

    def test_posture_not_found_returns_500(self, site_config):
        """Bad posture FK returns 500 (server config error)."""
        # Create spec with bad posture - this is a server-side config error
        bad_spec = {"schema_version": 1, "access": {"posture": "nonexistent"}}
        (site_config / "v2" / "specs" / "bad.yaml").write_text(yaml.dump(bad_spec))

        resolver = SpecResolver(etc_path=site_config)
        response, status = handle_spec_request("bad", "", resolver)

        # Returns 500 because this is caught during auth validation
        # (bad config is server's fault, not client's)
        assert status == 500
        assert response["error"]["code"] == "E201"

    def test_ssh_key_not_found_returns_500(self, site_config):
        """Bad SSH key FK returns 500 (server config error)."""
        # Create spec with bad SSH key reference - server-side config error
        bad_spec = {
            "schema_version": 1,
            "access": {
                "posture": "dev",
                "users": [{"name": "root", "ssh_keys": ["nonexistent"]}],
            },
        }
        (site_config / "v2" / "specs" / "bad-ssh.yaml").write_text(yaml.dump(bad_spec))

        resolver = SpecResolver(etc_path=site_config)
        response, status = handle_spec_request("bad-ssh", "", resolver)

        # Returns 500 because this is caught during auth validation
        assert status == 500
        assert response["error"]["code"] == "E202"

    def test_internal_error_returns_500(self, resolver):
        """Unexpected error returns 500."""
        # Mock validate_spec_auth to return None (success), then mock resolve to raise
        with patch("controller.specs.validate_spec_auth", return_value=None):
            with patch.object(resolver, "resolve", side_effect=RuntimeError("Boom")):
                response, status = handle_spec_request("base", "", resolver)

        assert status == 500
        assert response["error"]["code"] == "E500"
        assert "Internal error" in response["error"]["message"]


class TestHandleSpecsList:
    """Tests for handle_specs_list function."""

    @pytest.fixture
    def site_config(self, tmp_path):
        """Create a minimal site-config with multiple specs."""
        (tmp_path / "v2" / "specs").mkdir(parents=True)
        (tmp_path / "v2" / "postures").mkdir(parents=True)
        (tmp_path / "site.yaml").write_text(yaml.dump({"defaults": {}}))
        (tmp_path / "secrets.yaml").write_text(yaml.dump({}))

        # Create postures
        (tmp_path / "v2" / "postures" / "dev.yaml").write_text(
            yaml.dump({"auth": {"method": "network"}})
        )

        # Create multiple specs
        for name in ["base", "pve", "k8s", "staging"]:
            spec = {"schema_version": 1, "access": {"posture": "dev"}}
            (tmp_path / "v2" / "specs" / f"{name}.yaml").write_text(yaml.dump(spec))

        return tmp_path

    @pytest.fixture
    def resolver(self, site_config):
        """Create SpecResolver with test site-config."""
        return SpecResolver(etc_path=site_config)

    def test_list_specs_returns_all(self, resolver):
        """Lists all available specs."""
        response, status = handle_specs_list(resolver)

        assert status == 200
        assert "specs" in response
        specs = response["specs"]
        assert "base" in specs
        assert "pve" in specs
        assert "k8s" in specs
        assert "staging" in specs

    def test_list_specs_sorted(self, resolver):
        """Specs are returned in sorted order."""
        response, status = handle_specs_list(resolver)

        assert status == 200
        specs = response["specs"]
        assert specs == sorted(specs)

    def test_list_specs_empty_dir(self, tmp_path):
        """Returns empty list when no specs exist."""
        resolver = SpecResolver(etc_path=tmp_path)
        response, status = handle_specs_list(resolver)

        assert status == 200
        assert response["specs"] == []

    def test_list_specs_internal_error(self, resolver):
        """Unexpected error returns 500."""
        with patch.object(resolver, "list_specs", side_effect=RuntimeError("Boom")):
            response, status = handle_specs_list(resolver)

        assert status == 500
        assert response["error"]["code"] == "E500"
