"""Authentication middleware for the server.

Provides:
- Posture-based auth for specs (network, site_token, node_token)
- Token auth for repos (repo_token)
"""

import logging
from dataclasses import dataclass
from typing import Optional, Tuple

from resolver.spec_resolver import SpecResolver, SpecNotFoundError
from resolver.base import ResolverError

logger = logging.getLogger(__name__)


@dataclass
class AuthError:
    """Authentication error with error code and HTTP status."""

    code: str
    message: str
    http_status: int


def extract_bearer_token(auth_header: str) -> Optional[str]:
    """Extract Bearer token from Authorization header.

    Args:
        auth_header: Authorization header value

    Returns:
        Token string, or None if not a Bearer token
    """
    if auth_header and auth_header.startswith("Bearer "):
        return auth_header[7:]
    return None


def validate_spec_auth(
    identity: str,
    auth_header: str,
    resolver: SpecResolver,
) -> Optional[AuthError]:
    """Validate authentication for a spec request.

    Uses posture-based auth model:
    - network: No token required (trust network boundary)
    - site_token: Shared site-wide token required
    - node_token: Per-identity unique token required

    Args:
        identity: Spec identity being requested
        auth_header: Authorization header from request
        resolver: SpecResolver for posture/token lookup

    Returns:
        None if auth is valid, or AuthError on failure
    """
    try:
        auth_method = resolver.get_auth_method(identity)
    except SpecNotFoundError as e:
        return AuthError(e.code, e.message, 404)
    except ResolverError as e:
        return AuthError(e.code, e.message, 500)

    if auth_method == "network":
        # Trust network boundary, no token required
        logger.debug("Auth: network trust for %s", identity)
        return None

    token = extract_bearer_token(auth_header)

    if auth_method == "site_token":
        expected = resolver.get_site_token()
        if not expected:
            return AuthError("E500", "site_token not configured in secrets", 500)
        if not token:
            return AuthError("E300", "Authorization required", 401)
        if token != expected:
            return AuthError("E301", "Invalid token", 403)
        logger.debug("Auth: site_token validated for %s", identity)
        return None

    if auth_method == "node_token":
        expected = resolver.get_node_token(identity)
        if not expected:
            return AuthError("E500", f"node_token not configured for {identity}", 500)
        if not token:
            return AuthError("E300", "Authorization required", 401)
        if token != expected:
            return AuthError("E301", "Invalid token", 403)
        logger.debug("Auth: node_token validated for %s", identity)
        return None

    return AuthError("E500", f"Unknown auth method: {auth_method}", 500)


def validate_repo_token(
    auth_header: str,
    expected_token: str,
) -> Optional[AuthError]:
    """Validate Bearer token for repo access.

    Repos use a simple token-based auth model - all requests must
    present the correct repo_token.

    Args:
        auth_header: Authorization header from request
        expected_token: Expected repo_token

    Returns:
        None if auth is valid, or AuthError on failure
    """
    if not expected_token:
        # Token auth disabled (dev mode)
        return None

    token = extract_bearer_token(auth_header)
    if not token:
        return AuthError("E300", "Authorization required", 401)
    if token != expected_token:
        return AuthError("E301", "Invalid token", 403)

    return None
