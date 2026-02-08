"""CLI handlers for manifest-based verb commands (create, destroy, test).

Usage:
    ./run.sh create -M <manifest> -H <host> [--dry-run] [--json-output] [--verbose]
    ./run.sh destroy -M <manifest> -H <host> [--dry-run] [--yes]
    ./run.sh test -M <manifest> -H <host> [--dry-run] [--json-output]
"""

import argparse
import json
import logging
import sys
import time

from config import load_host_config, list_hosts
from manifest import load_manifest
from manifest_opr.executor import NodeExecutor
from manifest_opr.graph import ManifestGraph

logger = logging.getLogger(__name__)


def _common_parser(verb: str) -> argparse.ArgumentParser:
    """Build argument parser with common options for all verbs."""
    parser = argparse.ArgumentParser(
        prog=f'run.sh {verb}',
        description=f'{verb.capitalize()} infrastructure from manifest',
    )
    parser.add_argument(
        '--manifest', '-M',
        help='Manifest name from site-config/manifests/',
    )
    parser.add_argument(
        '--manifest-file',
        help='Path to manifest file',
    )
    parser.add_argument(
        '--manifest-json',
        help='Inline manifest JSON',
    )
    parser.add_argument(
        '--host', '-H',
        required=True,
        help=f'Target PVE host. Available: {", ".join(list_hosts())}',
    )
    parser.add_argument(
        '--dry-run',
        action='store_true',
        help='Preview operations without executing',
    )
    parser.add_argument(
        '--verbose', '-v',
        action='store_true',
        help='Enable verbose logging',
    )
    parser.add_argument(
        '--json-output',
        action='store_true',
        help='Output structured JSON to stdout (logs to stderr)',
    )
    parser.add_argument(
        '--depth',
        type=int,
        help='Limit manifest to first N levels',
    )
    return parser


def _setup_logging(verbose: bool, json_output: bool) -> None:
    """Configure logging based on flags."""
    if json_output:
        root_logger = logging.getLogger()
        for handler in root_logger.handlers[:]:
            root_logger.removeHandler(handler)
        stderr_handler = logging.StreamHandler(sys.stderr)
        stderr_handler.setFormatter(logging.Formatter(
            '%(asctime)s [%(levelname)s] %(message)s',
            datefmt='%Y-%m-%d %H:%M:%S',
        ))
        root_logger.addHandler(stderr_handler)

    if verbose:
        logging.getLogger().setLevel(logging.DEBUG)


def _parse_host_arg(value: str) -> tuple[str | None, str]:
    """Parse user@host syntax from -H flag.

    Returns:
        (user, host) tuple. user is None if no @ present.
    """
    if '@' in value:
        user, host = value.split('@', 1)
        return (user or None, host)
    return (None, value)


def _load_manifest_and_config(args):
    """Load manifest and host config from parsed args.

    Returns:
        (manifest, config) tuple

    Raises:
        SystemExit: On validation errors
    """
    # Load manifest
    try:
        manifest = load_manifest(
            name=args.manifest,
            file_path=args.manifest_file,
            json_str=args.manifest_json,
            depth=args.depth,
        )
    except Exception as e:
        print(f"Error loading manifest: {e}", file=sys.stderr)
        sys.exit(1)

    if manifest.schema_version != 2 or not manifest.nodes:
        print("Error: Verb commands require a v2 manifest with nodes[]", file=sys.stderr)
        sys.exit(1)

    # Parse user@host syntax
    ssh_user_override, host = _parse_host_arg(args.host)

    # Load host config
    available = list_hosts()
    if host not in available:
        print(f"Error: Unknown host '{host}'. Available: {', '.join(available)}", file=sys.stderr)
        sys.exit(1)

    config = load_host_config(host)
    if ssh_user_override:
        config.ssh_user = ssh_user_override
    return manifest, config


def _emit_json(verb: str, success: bool, state, duration: float) -> None:
    """Emit structured JSON output."""
    nodes = []
    for name, ns in state.nodes.items():
        node_data = {'name': name, 'status': ns.status}
        if ns.vm_id is not None:
            node_data['vm_id'] = ns.vm_id
        if ns.ip is not None:
            node_data['ip'] = ns.ip
        if ns.duration is not None:
            node_data['duration'] = round(ns.duration, 2)
        if ns.error is not None:
            node_data['error'] = ns.error
        nodes.append(node_data)

    output = {
        'verb': verb,
        'success': success,
        'duration_seconds': round(duration, 2),
        'nodes': nodes,
    }
    print(json.dumps(output, indent=2))


def create_main(argv: list) -> int:
    """Handle 'create' verb."""
    parser = _common_parser('create')
    args = parser.parse_args(argv)
    _setup_logging(args.verbose, args.json_output)

    manifest, config = _load_manifest_and_config(args)
    graph = ManifestGraph(manifest)

    logger.info(f"Creating infrastructure from manifest '{manifest.name}' on {config.name}")

    executor = NodeExecutor(
        manifest=manifest,
        graph=graph,
        config=config,
        dry_run=args.dry_run,
        json_output=args.json_output,
    )

    start = time.time()
    context: dict = {}
    success, state = executor.create(context)
    duration = time.time() - start

    if args.json_output:
        _emit_json('create', success, state, duration)

    return 0 if success else 1


def destroy_main(argv: list) -> int:
    """Handle 'destroy' verb."""
    parser = _common_parser('destroy')
    parser.add_argument(
        '--yes', '-y',
        action='store_true',
        help='Skip confirmation prompt',
    )
    args = parser.parse_args(argv)
    _setup_logging(args.verbose, args.json_output)

    manifest, config = _load_manifest_and_config(args)
    graph = ManifestGraph(manifest)

    # Confirmation for destructive operation
    if not args.dry_run and not args.yes:
        print(f"\nWARNING: This will destroy all nodes in manifest '{manifest.name}'.")
        print(f"Target host: {config.name}")
        print("This action cannot be undone.")
        response = input("Continue? [y/N] ").strip().lower()
        if response != 'y':
            print("Aborted.")
            return 1

    logger.info(f"Destroying infrastructure from manifest '{manifest.name}' on {config.name}")

    executor = NodeExecutor(
        manifest=manifest,
        graph=graph,
        config=config,
        dry_run=args.dry_run,
        json_output=args.json_output,
    )

    start = time.time()
    context: dict = {}
    success, state = executor.destroy(context)
    duration = time.time() - start

    if args.json_output:
        _emit_json('destroy', success, state, duration)

    return 0 if success else 1


def test_main(argv: list) -> int:
    """Handle 'test' verb."""
    parser = _common_parser('test')
    args = parser.parse_args(argv)
    _setup_logging(args.verbose, args.json_output)

    manifest, config = _load_manifest_and_config(args)
    graph = ManifestGraph(manifest)

    logger.info(f"Testing infrastructure from manifest '{manifest.name}' on {config.name}")

    executor = NodeExecutor(
        manifest=manifest,
        graph=graph,
        config=config,
        dry_run=args.dry_run,
        json_output=args.json_output,
    )

    start = time.time()
    context: dict = {}
    success, state = executor.test(context)
    duration = time.time() - start

    if args.json_output:
        _emit_json('test', success, state, duration)

    return 0 if success else 1
