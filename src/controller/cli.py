"""CLI for the controller serve command.

Provides the `serve` verb for starting the unified controller daemon.
"""

import argparse
import logging
import os
import secrets
import sys
from pathlib import Path

from controller.server import ControllerServer, DEFAULT_PORT, DEFAULT_BIND
from controller.tls import generate_self_signed_cert, TLSConfig
from controller.repos import RepoManager
from resolver.spec_resolver import SpecResolver
from resolver.base import ResolverError, discover_etc_path

logger = logging.getLogger(__name__)


def get_default_repos_dir() -> Path:
    """Get default repos directory (parent of iac-driver)."""
    return Path(__file__).resolve().parent.parent.parent.parent


def generate_repo_token(length: int = 16) -> str:
    """Generate a random repo token."""
    return secrets.token_urlsafe(length)[:length]


def main(argv=None):
    """CLI entry point for serve command.

    Args:
        argv: Command line arguments (default: sys.argv[1:])

    Returns:
        Exit code
    """
    parser = argparse.ArgumentParser(
        prog="run.sh serve",
        description="Start the unified controller daemon",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )

    # Server options
    parser.add_argument(
        "--port", "-p",
        type=int,
        default=DEFAULT_PORT,
        help="Port to listen on",
    )
    parser.add_argument(
        "--bind", "-b",
        default=DEFAULT_BIND,
        help="Address to bind to",
    )

    # TLS options
    parser.add_argument(
        "--cert",
        type=Path,
        help="Path to TLS certificate (auto-generated if not provided)",
    )
    parser.add_argument(
        "--key",
        type=Path,
        help="Path to TLS private key (required if --cert is provided)",
    )
    parser.add_argument(
        "--cert-dir",
        type=Path,
        help="Directory for auto-generated certificate",
    )

    # Repo options
    parser.add_argument(
        "--repos", "-r",
        action="store_true",
        help="Enable repo serving",
    )
    parser.add_argument(
        "--repos-dir",
        type=Path,
        help="Directory containing source repos (default: auto-detected)",
    )
    parser.add_argument(
        "--repo-token",
        help="Token for repo authentication (auto-generated if not provided)",
    )
    parser.add_argument(
        "--exclude",
        action="append",
        default=[],
        help="Exclude specific repo from serving (repeatable)",
    )

    # General options
    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Enable verbose logging",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Output startup info as JSON (for programmatic use)",
    )

    args = parser.parse_args(argv)

    # Configure logging
    level = logging.DEBUG if args.verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # Initialize spec resolver
    try:
        spec_resolver = SpecResolver()
        logger.info("Using site-config at: %s", spec_resolver.etc_path)
    except ResolverError as e:
        logger.error("Failed to initialize: %s", e.message)
        return 1

    # Initialize TLS
    tls_config = None
    if args.cert:
        if not args.key:
            logger.error("--key is required when --cert is provided")
            return 1
        try:
            tls_config = TLSConfig.from_paths(args.cert, args.key)
        except FileNotFoundError as e:
            logger.error("TLS file not found: %s", e)
            return 1
    else:
        try:
            tls_config = generate_self_signed_cert(cert_dir=args.cert_dir)
        except Exception as e:
            logger.error("Failed to generate TLS cert: %s", e)
            return 1

    # Initialize repos if requested
    repo_manager = None
    repo_token = ""
    if args.repos:
        repos_dir = args.repos_dir or get_default_repos_dir()
        repo_manager = RepoManager(
            repos_dir=repos_dir,
            exclude_repos=args.exclude,
        )
        repo_token = args.repo_token if args.repo_token is not None else generate_repo_token()

    # Create and start server
    server = ControllerServer(
        bind=args.bind,
        port=args.port,
        spec_resolver=spec_resolver,
        repo_manager=repo_manager,
        repo_token=repo_token,
        tls_config=tls_config,
    )

    try:
        server.start()
    except RuntimeError as e:
        logger.error("Failed to start server: %s", e)
        return 1

    # Output startup info
    if args.json:
        import json
        info = {
            "url": f"https://{args.bind}:{args.port}",
            "port": args.port,
            "fingerprint": tls_config.fingerprint,
            "specs": spec_resolver.list_specs(),
        }
        if repo_manager:
            info["repo_token"] = repo_token
            info["repos"] = list(repo_manager.repo_status.keys())
        print(json.dumps(info, indent=2))
    else:
        print(f"\nController running at https://{args.bind}:{args.port}")
        print(f"Certificate fingerprint: {tls_config.fingerprint}")
        print(f"Available specs: {', '.join(spec_resolver.list_specs())}")
        if repo_manager:
            print(f"Repo token: {repo_token}")
            prepared = [k for k, v in repo_manager.repo_status.items() if v.get("status") == "ok"]
            print(f"Available repos: {', '.join(prepared)}")
        print("\nPress Ctrl+C to stop...")

    # Run server
    server.serve_forever()
    return 0


if __name__ == "__main__":
    sys.exit(main())
