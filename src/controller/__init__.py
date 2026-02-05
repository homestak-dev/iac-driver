"""Controller package for unified HTTPS daemon.

The controller serves both specs (for config phase) and repos (for bootstrap)
on a single HTTPS port with posture-based and token authentication.
"""

from controller.server import (
    ControllerServer,
    create_server,
    DEFAULT_PORT,
    DEFAULT_BIND,
)
from controller.tls import (
    TLSConfig,
    generate_self_signed_cert,
    get_cert_fingerprint,
)
from controller.auth import (
    AuthError,
    validate_spec_auth,
    validate_repo_token,
)

__all__ = [
    # Server
    "ControllerServer",
    "create_server",
    "DEFAULT_PORT",
    "DEFAULT_BIND",
    # TLS
    "TLSConfig",
    "generate_self_signed_cert",
    "get_cert_fingerprint",
    # Auth
    "AuthError",
    "validate_spec_auth",
    "validate_repo_token",
]
