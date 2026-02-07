"""Spec endpoint handler for the controller.

Serves resolved specs from site-config/specs/ with posture-based auth.
"""

import json
import logging
from typing import Optional, Tuple

from resolver.spec_resolver import (
    SpecResolver,
    SpecNotFoundError,
    SchemaValidationError,
)
from resolver.base import (
    ResolverError,
    PostureNotFoundError,
    SSHKeyNotFoundError,
)
from controller.auth import validate_spec_auth, AuthError

logger = logging.getLogger(__name__)


def handle_spec_request(
    identity: str,
    auth_header: str,
    resolver: SpecResolver,
) -> Tuple[dict, int]:
    """Handle a spec request.

    Validates auth and returns resolved spec.

    Args:
        identity: Spec identity (e.g., "base", "pve")
        auth_header: Authorization header from request
        resolver: SpecResolver instance

    Returns:
        Tuple of (response_dict, http_status)
    """
    # Validate authentication
    auth_error = validate_spec_auth(identity, auth_header, resolver)
    if auth_error:
        return _error_response(auth_error.code, auth_error.message), auth_error.http_status

    # Resolve spec
    try:
        spec = resolver.resolve(identity)

        # Remove internal _posture field from response
        if "access" in spec and "_posture" in spec["access"]:
            spec = dict(spec)
            spec["access"] = {k: v for k, v in spec["access"].items() if k != "_posture"}

        return spec, 200

    except SpecNotFoundError as e:
        return _error_response(e.code, e.message), 404
    except PostureNotFoundError as e:
        return _error_response(e.code, e.message), 404
    except SSHKeyNotFoundError as e:
        return _error_response(e.code, e.message), 404
    except SchemaValidationError as e:
        return _error_response(e.code, e.message), 422
    except ResolverError as e:
        return _error_response(e.code, e.message), 500
    except Exception as e:
        logger.exception("Unexpected error resolving spec %s", identity)
        return _error_response("E500", f"Internal error: {e}"), 500


def handle_specs_list(resolver: SpecResolver) -> Tuple[dict, int]:
    """Handle a request to list available specs.

    Args:
        resolver: SpecResolver instance

    Returns:
        Tuple of (response_dict, http_status)
    """
    try:
        specs = resolver.list_specs()
        return {"specs": specs}, 200
    except ResolverError as e:
        return _error_response(e.code, e.message), 500
    except Exception as e:
        logger.exception("Unexpected error listing specs")
        return _error_response("E500", f"Internal error: {e}"), 500


def _error_response(code: str, message: str) -> dict:
    """Build error response dict."""
    return {"error": {"code": code, "message": message}}
