"""Authentication middleware for the server.

Provides:
- Provisioning token verification for specs (HMAC-SHA256, v0.49+)
- Token auth for repos (repo_token)
"""

import base64
import hashlib
import hmac as hmac_mod
import json
import logging
from typing import Optional

logger = logging.getLogger(__name__)


class AuthError(Exception):
    """Authentication error with error code and HTTP status."""

    def __init__(self, code: str, message: str, http_status: int):
        self.code = code
        self.message = message
        self.http_status = http_status
        super().__init__(f"{code}: {message}")


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


def _base64url_decode(s: str) -> bytes:
    """Decode base64url string (padding-free)."""
    # Add padding
    s += '=' * (4 - len(s) % 4) if len(s) % 4 else ''
    return base64.urlsafe_b64decode(s)


def verify_provisioning_token(
    token: str,
    signing_key: str,
    url_identity: str,
) -> dict:
    """Verify a provisioning token and return decoded claims.

    Args:
        token: The provisioning token (base64url(payload).base64url(sig))
        signing_key: Hex-encoded 256-bit signing key
        url_identity: Identity from the URL path (for defense-in-depth check)

    Returns:
        Decoded claims dict on success

    Raises:
        AuthError: On any verification failure
    """
    # 1. Split token
    parts = token.split(".")
    if len(parts) != 2:
        raise AuthError("E300", "Malformed token: expected 2 dot-separated segments", 400)

    payload_b64, sig_b64 = parts

    # 2. Verify HMAC (constant-time comparison)
    try:
        expected_sig = hmac_mod.new(
            bytes.fromhex(signing_key),
            payload_b64.encode(),
            hashlib.sha256,
        ).digest()
    except ValueError:
        raise AuthError("E500", "Invalid signing key configuration", 500)

    try:
        actual_sig = _base64url_decode(sig_b64)
    except Exception:
        raise AuthError("E300", "Malformed token: invalid signature encoding", 400)

    if not hmac_mod.compare_digest(expected_sig, actual_sig):
        raise AuthError("E301", "Invalid token signature", 401)

    # 3. Decode payload
    try:
        claims = json.loads(_base64url_decode(payload_b64))
    except Exception:
        raise AuthError("E300", "Malformed token: invalid payload encoding", 400)

    # 4. Validate version
    if claims.get("v") != 1:
        raise AuthError("E300", f"Unsupported token version: {claims.get('v')}", 400)

    # 5. Validate required claims
    if "n" not in claims or "s" not in claims:
        raise AuthError("E300", "Malformed token: missing required claims", 400)

    # 6. Validate identity match (defense in depth)
    if claims["n"] != url_identity:
        raise AuthError(
            "E301",
            f"Token identity mismatch: token={claims['n']} url={url_identity}",
            401,
        )

    return claims


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
