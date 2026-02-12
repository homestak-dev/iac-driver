"""Server package for unified HTTPS daemon.

The server serves both specs (for config phase) and repos (for bootstrap)
on a single HTTPS port with provisioning token and token authentication.
"""

from server.httpd import (
    Server,
    create_server,
    DEFAULT_PORT,
    DEFAULT_BIND,
)
from server.tls import (
    TLSConfig,
    generate_self_signed_cert,
    get_cert_fingerprint,
)
from server.auth import (
    AuthError,
    verify_provisioning_token,
    validate_repo_token,
)
from server.daemon import (
    daemonize,
    stop_daemon,
    check_status,
    get_pid_file,
)

__all__ = [
    # Server
    "Server",
    "create_server",
    "DEFAULT_PORT",
    "DEFAULT_BIND",
    # TLS
    "TLSConfig",
    "generate_self_signed_cert",
    "get_cert_fingerprint",
    # Auth
    "AuthError",
    "verify_provisioning_token",
    "validate_repo_token",
    # Daemon
    "daemonize",
    "stop_daemon",
    "check_status",
    "get_pid_file",
]
