#!/usr/bin/env python3
"""CLI entry point for iac-driver."""

import argparse
import json
import logging
import os
import socket
import sys
from pathlib import Path

from config import list_hosts, list_envs, load_host_config, get_base_dir
from scenarios import Orchestrator, get_scenario, list_scenarios

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger(__name__)


def main():
    available_hosts = list_hosts()
    available_envs = list_envs()
    available_scenarios = list_scenarios()

    parser = argparse.ArgumentParser(
        description='Infrastructure-as-Code Driver - Orchestrates provisioning and testing workflows'
    )
    parser.add_argument(
        '--scenario', '-S',
        choices=available_scenarios,
        help='Scenario to run (required unless using --list-scenarios)'
    )
    parser.add_argument(
        '--host', '-H',
        help=f'Target PVE host (required for most scenarios). Available: {", ".join(available_hosts) if available_hosts else "none configured"}'
    )
    parser.add_argument(
        '--env', '-E',
        choices=available_envs,
        help=f'Environment to deploy (overrides scenario default)'
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
        help='Target host IP for remote execution (for pve-setup, packer-build)'
    )
    parser.add_argument(
        '--templates',
        help='Comma-separated list of packer templates to build (for packer-build)'
    )
    parser.add_argument(
        '--vm-ip',
        help='Target VM IP (for bootstrap-install scenario)'
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

    args = parser.parse_args()

    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

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

    # Handle --host resolution
    host = args.host

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
    if host and host not in available_hosts:
        print(f"Error: Unknown host '{host}'")
        print(f"Available hosts: {', '.join(available_hosts) if available_hosts else 'none configured'}")
        return 1

    # Load config (use dummy config for scenarios without host config)
    if host:
        config = load_host_config(host)
    else:
        # Create minimal config for scenarios that don't need host config
        from config import HostConfig
        config = HostConfig(name='local', config_file=Path('/dev/null'))

    # Override packer release if specified (CLI takes precedence)
    if args.packer_release:
        config.packer_release = args.packer_release
        logger.info(f"Using packer release override: {args.packer_release}")

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
        timeout=args.timeout
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

    # Pre-populate context with env override
    if args.env:
        orchestrator.context['env_name'] = args.env

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
        if args.env:
            print(f"Environment: {args.env}")
        print("\nThis action cannot be undone.")
        response = input("Continue? [y/N] ").strip().lower()
        if response != 'y':
            print("Aborted.")
            return 1

    success = orchestrator.run()

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
