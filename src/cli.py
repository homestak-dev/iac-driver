#!/usr/bin/env python3
"""CLI entry point for iac-driver.

Supports both scenario-based workflows and verb-based subcommands:
- Scenarios: ./run.sh --scenario <name> --host <host>
- Verbs: ./run.sh serve [options]

Verb commands (4-phase lifecycle):
- serve: Start the unified controller daemon (specs + repos)
- create: Create infrastructure from manifest
- destroy: Destroy infrastructure from manifest
- test: Create, verify, and destroy infrastructure
- config: Apply specification to the local host
"""

import argparse
import json
import logging
import os
import socket
import subprocess
import sys
from pathlib import Path

import re

from config import list_hosts, load_host_config, get_base_dir
from scenarios import Orchestrator, get_scenario, list_scenarios
from validation import validate_readiness, run_preflight_checks, format_preflight_results

# Verb commands (subcommands for 4-phase lifecycle)
VERB_COMMANDS = {
    "serve": "Start the unified controller daemon",
    "create": "Create infrastructure from manifest",
    "destroy": "Destroy infrastructure from manifest",
    "test": "Create, verify, and destroy infrastructure from manifest",
    "config": "Apply specification to the local host",
}

# Scenarios retired in v0.47 (scenario consolidation)
# Maps old scenario names to migration hints
RETIRED_SCENARIOS = {
    "vm-constructor": "Use: ./run.sh create -M n1-basic -H <host>",
    "vm-destructor": "Use: ./run.sh destroy -M n1-basic -H <host>",
    "vm-roundtrip": "Use: ./run.sh test -M n1-basic -H <host>",
    "nested-pve-constructor": "Use: ./run.sh create -M n2-quick -H <host>",
    "nested-pve-destructor": "Use: ./run.sh destroy -M n2-quick -H <host>",
    "nested-pve-roundtrip": "Use: ./run.sh test -M n2-quick -H <host>",
    "recursive-pve-constructor": "Use: ./run.sh create -M <manifest> -H <host>",
    "recursive-pve-destructor": "Use: ./run.sh destroy -M <manifest> -H <host>",
    "recursive-pve-roundtrip": "Use: ./run.sh test -M <manifest> -H <host>",
}


def _is_ip_address(value: str) -> bool:
    """Check if value looks like an IPv4 address."""
    return bool(re.match(r'^\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}$', value))


def _parse_host_arg(value: str) -> tuple[str | None, str]:
    """Parse user@host syntax from -H flag.

    Returns:
        (user, host) tuple. user is None if no @ present.
    """
    if '@' in value:
        user, host = value.split('@', 1)
        return (user or None, host)
    return (None, value)


def _create_ip_config(ip: str, ssh_user: str | None = None):
    """Create a HostConfig for a raw IP address (no site-config lookup)."""
    from config import HostConfig
    config = HostConfig(name=ip, config_file=Path('/dev/null'))
    config.ssh_host = ip
    if ssh_user:
        config.ssh_user = ssh_user
    config.is_host_only = True
    return config


def dispatch_verb(verb: str, argv: list) -> int:
    """Dispatch to verb-specific CLI handler.

    Args:
        verb: The verb command (e.g., "serve")
        argv: Remaining command line arguments

    Returns:
        Exit code
    """
    if verb == "serve":
        from controller.cli import main as serve_main
        return serve_main(argv)

    if verb == "create":
        from manifest_opr.cli import create_main
        return create_main(argv)

    if verb == "destroy":
        from manifest_opr.cli import destroy_main
        return destroy_main(argv)

    if verb == "test":
        from manifest_opr.cli import test_main
        return test_main(argv)

    if verb == "config":
        from config_apply import config_main
        return config_main(argv)

    print(f"Error: Verb '{verb}' not yet implemented")
    return 1


def get_version():
    """Get version from git tags (do not use hardcoded VERSION constant)."""
    try:
        result = subprocess.run(
            ['git', 'describe', '--tags', '--abbrev=0'],
            capture_output=True, text=True,
            cwd=Path(__file__).parent
        )
        return result.stdout.strip() if result.returncode == 0 else 'dev'
    except Exception:
        return 'dev'

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger(__name__)


def create_local_config():
    """Create HostConfig for local execution with auto-derived values.

    Derives API endpoint and attempts to load API token for current hostname.
    Used when --local flag is specified.
    """
    from config import HostConfig
    from config_resolver import ConfigResolver

    hostname = socket.gethostname()
    config = HostConfig(
        name='local',
        config_file=Path('/dev/null'),
    )

    # Derive API endpoint for local PVE
    config.api_endpoint = 'https://localhost:8006'
    config.ssh_host = 'localhost'

    # Try to load API token for current host
    try:
        resolver = ConfigResolver()
        secrets = resolver._load_yaml(resolver.site_config_dir / 'secrets.yaml')
        token = secrets.get('api_tokens', {}).get(hostname)
        if token:
            config._api_token = token
            logger.info(f"Loaded API token for {hostname}")
        else:
            logger.debug(f"No API token found for hostname '{hostname}'")
    except FileNotFoundError:
        logger.debug("secrets.yaml not found, skipping API token loading")
    except Exception as e:
        logger.debug(f"Could not load API token for localhost: {e}")

    return config


def print_usage():
    """Print top-level usage showing verbs and scenario command."""
    print(f"iac-driver {get_version()}")
    print()
    print("Usage: ./run.sh <command> [options]")
    print()
    print("Commands:")
    print(f"  {'scenario':<12} Run a standalone scenario workflow")
    for verb, desc in VERB_COMMANDS.items():
        print(f"  {verb:<12} {desc}")
    print()
    print("Run './run.sh <command> --help' for command-specific options.")
    print()
    print("Examples:")
    print("  ./run.sh scenario pve-setup -H father")
    print("  ./run.sh create -M n1-basic -H father")
    print("  ./run.sh test -M n2-quick -H father")
    print("  ./run.sh serve --port 44443")
    print("  ./run.sh config --fetch --insecure")


def main():
    from_verb = False

    if len(sys.argv) > 1:
        first_arg = sys.argv[1]

        # Handle 'scenario' verb: rewrite to legacy --scenario format
        if first_arg == 'scenario':
            from_verb = True
            if len(sys.argv) < 3 or sys.argv[2].startswith('-'):
                # Show scenario list or help
                if '--help' in sys.argv or '-h' in sys.argv:
                    # Rewrite as --list-scenarios for help
                    sys.argv = [sys.argv[0], '--list-scenarios']
                else:
                    print("Usage: ./run.sh scenario <name> [options]")
                    print("\nRun './run.sh scenario --help' to list available scenarios.")
                    return 1 if len(sys.argv) < 3 else 0
            else:
                # Transform: "scenario pve-setup -H father" -> "--scenario pve-setup -H father"
                sys.argv = [sys.argv[0], '--scenario', sys.argv[2]] + sys.argv[3:]
            # Fall through to legacy scenario parser below

        # Handle other verbs (serve, create, destroy, test, config)
        elif first_arg in VERB_COMMANDS:
            return dispatch_verb(first_arg, sys.argv[2:])

        # Show usage when no recognized command
        elif not first_arg.startswith('-'):
            print(f"Error: Unknown command '{first_arg}'")
            print_usage()
            return 1

    # Show top-level usage when no arguments
    if len(sys.argv) == 1:
        print_usage()
        return 0

    # Check for retired scenarios and print migration hint
    for i, arg in enumerate(sys.argv[1:], 1):
        if arg in ('--scenario', '-S') and i < len(sys.argv) - 1:
            scenario_name = sys.argv[i + 1]
            if scenario_name in RETIRED_SCENARIOS:
                hint = RETIRED_SCENARIOS[scenario_name]
                print(f"Error: Scenario '{scenario_name}' was retired in v0.47.")
                print(f"  {hint}")
                print(f"\nSee: ./run.sh create --help")
                return 1

    # Deprecation notice for legacy --scenario flag (skip if invoked via verb)
    if not from_verb and any(arg in ('--scenario', '-S') for arg in sys.argv[1:]):
        logger.warning("--scenario is deprecated. Use: ./run.sh scenario <name> [options]")

    # Scenario-based CLI continues below
    available_hosts = list_hosts()
    available_scenarios = list_scenarios()

    parser = argparse.ArgumentParser(
        description='Infrastructure-as-Code Driver - Orchestrates provisioning and testing workflows'
    )
    parser.add_argument(
        '--version',
        action='version',
        version=f'iac-driver {get_version()}'
    )
    parser.add_argument(
        '--scenario', '-S',
        choices=available_scenarios,
        help='Scenario to run (required unless using --list-scenarios)'
    )
    parser.add_argument(
        '--host', '-H',
        help=f'Target host: named host from site-config or raw IP. Available: {", ".join(available_hosts) if available_hosts else "none configured"}'
    )
    parser.add_argument(
        '--report-dir', '-r',
        type=Path,
        default=get_base_dir() / 'reports',
        help='Directory for test reports'
    )
    parser.add_argument(
        '--skip', '-s',
        action='append',
        default=[],
        help='Phases to skip (can be repeated)'
    )
    parser.add_argument(
        '--list-scenarios',
        action='store_true',
        help='List available scenarios and exit'
    )
    parser.add_argument(
        '--list-phases',
        action='store_true',
        help='List phases for the selected scenario and exit'
    )
    parser.add_argument(
        '--verbose', '-v',
        action='store_true',
        help='Enable verbose logging'
    )
    parser.add_argument(
        '--inner-ip',
        help='Inner PVE VM IP (auto-detected if not provided, required when skipping provision phases)'
    )
    parser.add_argument(
        '--local',
        action='store_true',
        help='Run scenario locally (for pve-setup, packer-build)'
    )
    parser.add_argument(
        '--remote',
        help='[Deprecated: use -H <ip>] Target host IP for remote execution'
    )
    parser.add_argument(
        '--templates',
        help='Comma-separated list of packer templates to build (for packer-build)'
    )
    parser.add_argument(
        '--vm-ip',
        help='[Deprecated: use -H <ip>] Target VM IP (for bootstrap-install scenario)'
    )
    parser.add_argument(
        '--homestak-user',
        help='Create this user during bootstrap (for bootstrap-install scenario)'
    )
    parser.add_argument(
        '--context-file', '-C',
        type=Path,
        help='Save/load scenario context to file for chained runs (e.g., constructor then destructor)'
    )
    parser.add_argument(
        '--packer-release',
        help='Packer release tag for image downloads (e.g., v0.8.0-rc1 or latest). Overrides site.yaml default.'
    )
    parser.add_argument(
        '--timeout', '-t',
        type=int,
        help='Overall scenario timeout in seconds. Checked between phases (does not interrupt running phases).'
    )
    parser.add_argument(
        '--yes', '-y',
        action='store_true',
        help='Skip confirmation prompt for destructive scenarios'
    )
    parser.add_argument(
        '--vm-id',
        action='append',
        metavar='NAME=VMID',
        help='Override VM ID (repeatable): --vm-id test=99990 --vm-id inner=99912'
    )
    parser.add_argument(
        '--dry-run',
        action='store_true',
        help='Show what would be executed without running actions'
    )
    parser.add_argument(
        '--preflight',
        action='store_true',
        help='Run preflight checks only (no scenario execution)'
    )
    parser.add_argument(
        '--skip-preflight',
        action='store_true',
        help='Skip preflight checks before scenario execution'
    )
    parser.add_argument(
        '--json-output',
        action='store_true',
        help='Output structured JSON to stdout (logs go to stderr)'
    )

    # Manifest arguments for recursive-pve scenarios
    parser.add_argument(
        '--manifest', '-M',
        help='Manifest name from site-config/manifests/ (for recursive-pve scenarios)'
    )
    parser.add_argument(
        '--manifest-file',
        type=Path,
        help='Path to manifest file (for recursive-pve scenarios)'
    )
    parser.add_argument(
        '--manifest-json',
        help='Inline manifest JSON (for recursive calls, not user-facing)'
    )
    parser.add_argument(
        '--keep-on-failure',
        action='store_true',
        help='Keep levels on failure for debugging (for recursive-pve scenarios)'
    )
    parser.add_argument(
        '--depth',
        type=int,
        help='Limit manifest to first N levels (for recursive-pve scenarios)'
    )

    args = parser.parse_args()

    # Configure logging for --json-output mode
    if args.json_output:
        # Remove existing handlers and redirect to stderr
        root_logger = logging.getLogger()
        for handler in root_logger.handlers[:]:
            root_logger.removeHandler(handler)
        stderr_handler = logging.StreamHandler(sys.stderr)
        stderr_handler.setFormatter(logging.Formatter(
            '%(asctime)s [%(levelname)s] %(message)s',
            datefmt='%Y-%m-%d %H:%M:%S'
        ))
        root_logger.addHandler(stderr_handler)

    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    # Handle --preflight mode (standalone check, no scenario)
    if args.preflight:
        hostname = socket.gethostname()
        # Check if nested-pve scenario would be run (for nested virt check)
        check_nested = args.scenario and 'nested-pve' in args.scenario

        logger.info(f"Running preflight checks for {hostname}")
        success, results = run_preflight_checks(
            local_mode=args.local or not args.host,
            hostname=hostname,
            check_nested_virt=check_nested,
            verbose=args.verbose
        )

        print(format_preflight_results(hostname, results))
        return 0 if success else 1

    if args.list_scenarios or args.scenario is None:
        print("Available scenarios:")
        for name in available_scenarios:
            scenario = get_scenario(name)
            runtime = getattr(scenario, 'expected_runtime', None)
            if runtime:
                # Format runtime nicely (e.g., 30 -> "~30s", 540 -> "~9m")
                if runtime >= 60:
                    runtime_str = f"~{runtime // 60}m"
                else:
                    runtime_str = f"~{runtime}s"
                print(f"  {name:30} {runtime_str:>6}  {scenario.description}")
            else:
                print(f"  {name:30}         {scenario.description}")
        if args.scenario is None:
            print("\nUsage: ./run.sh --scenario <name> --host <host>")
        return 0

    # Get scenario to check its requirements
    scenario = get_scenario(args.scenario)

    # Check scenario attributes (with defaults)
    requires_root = getattr(scenario, 'requires_root', False)
    requires_host_config = getattr(scenario, 'requires_host_config', True)

    # Check root requirement for --local mode
    if args.local and requires_root and os.getuid() != 0:
        print(f"Error: Scenario '{args.scenario}' requires root privileges in --local mode")
        print("Run with sudo or as root")
        return 1

    # Handle --host resolution (supports user@host syntax)
    host_arg = args.host
    ssh_user_override = None

    if host_arg:
        ssh_user_override, host = _parse_host_arg(host_arg)
    else:
        host = None

    # Auto-detect host from hostname when --local and no --host
    if args.local and not host:
        hostname = socket.gethostname()
        if hostname in available_hosts:
            host = hostname
            logger.info(f"Auto-detected host from hostname: {host}")
        elif not requires_host_config:
            # Scenario doesn't need host config, proceed without
            host = None
            logger.debug(f"No host config needed for scenario '{args.scenario}'")
        else:
            print(f"Error: Could not auto-detect host. Hostname '{hostname}' not in available hosts.")
            print(f"Available hosts: {', '.join(available_hosts) if available_hosts else 'none configured'}")
            print(f"\nEither:")
            print(f"  1. Create nodes/{hostname}.yaml in site-config")
            print(f"  2. Specify --host explicitly")
            return 1

    # Validate --host is provided for scenarios that need it (when not in --local mode)
    if not args.local and requires_host_config and not host:
        print(f"Error: --host is required for scenario '{args.scenario}'")
        print(f"Available hosts: {', '.join(available_hosts) if available_hosts else 'none configured'}")
        print(f"\nUsage: ./run.sh --scenario {args.scenario} --host <host>")
        return 1

    # Validate --host value if provided
    is_raw_ip = host and _is_ip_address(host)
    if host and not is_raw_ip and host not in available_hosts:
        print(f"Error: Unknown host '{host}'")
        print(f"Available hosts: {', '.join(available_hosts) if available_hosts else 'none configured'}")
        return 1

    # Deprecation warnings for --remote and --vm-ip
    if args.remote:
        logger.warning("--remote is deprecated. Use: -H %s", args.remote)
    if args.vm_ip:
        logger.warning("--vm-ip is deprecated. Use: -H %s", args.vm_ip)

    # Load config (use local config with auto-derived values for --local)
    if is_raw_ip:
        config = _create_ip_config(host, ssh_user=ssh_user_override)
        logger.info(f"Using raw IP: {host} (no site-config lookup)")
    elif host:
        config = load_host_config(host)
    else:
        # Create local config with auto-derived API endpoint and token
        config = create_local_config()

    # Apply user@ override if specified
    if ssh_user_override and not is_raw_ip:
        config.ssh_user = ssh_user_override

    # Override packer release if specified (CLI takes precedence)
    if args.packer_release:
        config.packer_release = args.packer_release
        logger.info(f"Using packer release override: {args.packer_release}")

    # Load manifest for recursive-pve scenarios
    if args.scenario and 'recursive-pve' in args.scenario:
        from manifest import load_manifest, ConfigError as ManifestConfigError
        try:
            manifest = load_manifest(
                name=args.manifest,
                file_path=str(args.manifest_file) if args.manifest_file else None,
                json_str=args.manifest_json,
                depth=args.depth
            )
            # Set manifest on scenario
            scenario.manifest = manifest
            # Set keep_on_failure flag
            if hasattr(scenario, 'keep_on_failure'):
                scenario.keep_on_failure = args.keep_on_failure
            logger.info(f"Loaded manifest: {manifest.name} (depth={manifest.depth})")
        except ManifestConfigError as e:
            print(f"Error loading manifest: {e}")
            return 1

    # Pre-flight validation (skip for --skip-preflight, --dry-run)
    if not args.skip_preflight and not args.dry_run:
        scenario_class = type(scenario)
        errors = validate_readiness(
            config,
            scenario_class,
            local_mode=args.local
        )
        if errors:
            print("\nPre-flight validation failed:")
            for error in errors:
                # Indent multi-line errors
                for i, line in enumerate(error.split('\n')):
                    prefix = "  ✗ " if i == 0 else "    "
                    print(f"{prefix}{line}")
            print("\nUse --skip-preflight to bypass these checks")
            print()
            return 1
        logger.info("Pre-flight validation passed")

    if args.list_phases:
        print(f"Phases for scenario '{args.scenario}':")
        for name, action, desc in scenario.get_phases(config):
            print(f"  {name}: {desc}")
        return 0

    # Create orchestrator
    orchestrator = Orchestrator(
        scenario=scenario,
        config=config,
        report_dir=args.report_dir,
        skip_phases=args.skip,
        timeout=args.timeout,
        dry_run=args.dry_run
    )

    # Load context from file if specified and exists
    if args.context_file and args.context_file.exists():
        try:
            with open(args.context_file, encoding="utf-8") as f:
                loaded_context = json.load(f)
            orchestrator.context.update(loaded_context)
            logger.info(f"Loaded context from {args.context_file}: {list(loaded_context.keys())}")
        except json.JSONDecodeError as e:
            print(f"Error: Invalid JSON in context file {args.context_file}: {e}")
            return 1
        except Exception as e:
            print(f"Error reading context file {args.context_file}: {e}")
            return 1

    # Pre-populate context if inner-ip provided
    if args.inner_ip:
        orchestrator.context['inner_ip'] = args.inner_ip

    # Pre-populate context for pve-setup and packer-build scenarios
    if args.local:
        orchestrator.context['local_mode'] = True
    if args.remote:
        orchestrator.context['remote_ip'] = args.remote
    if args.templates:
        orchestrator.context['templates'] = [t.strip() for t in args.templates.split(',')]

    # Pre-populate context for bootstrap-install scenario
    if args.vm_ip:
        orchestrator.context['vm_ip'] = args.vm_ip
    if args.homestak_user:
        orchestrator.context['homestak_user'] = args.homestak_user

    # Pre-populate context with VM ID overrides
    if args.vm_id:
        vm_id_overrides = {}
        for override in args.vm_id:
            if '=' not in override:
                print(f"Error: Invalid --vm-id format '{override}'. Expected NAME=VMID (e.g., test=99990)")
                return 1
            name, vmid_str = override.split('=', 1)
            if not name:
                print(f"Error: Invalid --vm-id format '{override}'. VM name cannot be empty.")
                return 1
            try:
                vmid = int(vmid_str)
            except ValueError:
                print(f"Error: Invalid --vm-id format '{override}'. VMID must be an integer.")
                return 1
            vm_id_overrides[name] = vmid
        orchestrator.context['vm_id_overrides'] = vm_id_overrides
        logger.info(f"VM ID overrides: {vm_id_overrides}")

    # Check for confirmation on destructive scenarios
    if getattr(scenario, 'requires_confirmation', False) and not args.yes:
        print(f"\nWARNING: '{args.scenario}' is a destructive scenario.")
        print(f"Target: {config.name}")
        print("\nThis action cannot be undone.")
        response = input("Continue? [y/N] ").strip().lower()
        if response != 'y':
            print("Aborted.")
            return 1

    success = orchestrator.run()

    # Output JSON if requested
    if args.json_output:
        report_data = orchestrator.report.to_dict(orchestrator.context)
        print(json.dumps(report_data, indent=2))

    # Save context to file if specified
    if args.context_file:
        try:
            # Convert any non-serializable values to strings
            serializable_context = {}
            for key, value in orchestrator.context.items():
                try:
                    json.dumps(value)
                    serializable_context[key] = value
                except (TypeError, ValueError):
                    serializable_context[key] = str(value)

            with open(args.context_file, 'w', encoding="utf-8") as f:
                json.dump(serializable_context, f, indent=2)
            logger.info(f"Saved context to {args.context_file}: {list(serializable_context.keys())}")
        except Exception as e:
            logger.warning(f"Failed to save context to {args.context_file}: {e}")

    return 0 if success else 1


if __name__ == '__main__':
    sys.exit(main())
