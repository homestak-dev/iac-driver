#!/usr/bin/env python3
"""CLI entry point for iac-driver."""

import argparse
import json
import logging
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
        help='Run scenario locally (for pve-configure, packer-build)'
    )
    parser.add_argument(
        '--remote',
        help='Target host IP for remote execution (for pve-configure, packer-build)'
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

    args = parser.parse_args()

    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    if args.list_scenarios or args.scenario is None:
        print("Available scenarios:")
        for name in available_scenarios:
            scenario = get_scenario(name)
            print(f"  {name}: {scenario.description}")
        if args.scenario is None:
            print("\nUsage: ./run.sh --scenario <name> --host <host>")
        return 0

    # Scenarios that don't require --host
    hostless_scenarios = {'pve-configure', 'packer-build', 'packer-build-fetch',
                          'packer-build-publish', 'packer-sync', 'packer-sync-build-fetch',
                          'bootstrap-install'}

    # Validate --host is provided for scenarios that need it
    if args.scenario not in hostless_scenarios and not args.host:
        print(f"Error: --host is required for scenario '{args.scenario}'")
        print(f"Available hosts: {', '.join(available_hosts) if available_hosts else 'none configured'}")
        print(f"\nUsage: ./run.sh --scenario {args.scenario} --host <host>")
        return 1

    # Validate --host value if provided
    if args.host and args.host not in available_hosts:
        print(f"Error: Unknown host '{args.host}'")
        print(f"Available hosts: {', '.join(available_hosts) if available_hosts else 'none configured'}")
        return 1

    # Load config (use dummy config for hostless scenarios without --host)
    if args.host:
        config = load_host_config(args.host)
    else:
        # Create minimal config for hostless scenarios
        from config import HostConfig
        config = HostConfig(name='local', config_file=Path('/dev/null'))

    # Override packer release if specified (CLI takes precedence)
    if args.packer_release:
        config.packer_release = args.packer_release
        logger.info(f"Using packer release override: {args.packer_release}")

    scenario = get_scenario(args.scenario)

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
        skip_phases=args.skip
    )

    # Load context from file if specified and exists
    if args.context_file and args.context_file.exists():
        try:
            with open(args.context_file) as f:
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

    # Pre-populate context for pve-configure and packer-build scenarios
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

            with open(args.context_file, 'w') as f:
                json.dump(serializable_context, f, indent=2)
            logger.info(f"Saved context to {args.context_file}: {list(serializable_context.keys())}")
        except Exception as e:
            logger.warning(f"Failed to save context to {args.context_file}: {e}")

    return 0 if success else 1


if __name__ == '__main__':
    sys.exit(main())
