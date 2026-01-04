#!/usr/bin/env python3
"""CLI entry point for iac-driver."""

import argparse
import logging
import sys
from pathlib import Path

from config import list_hosts, load_host_config, get_base_dir
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
        default='pve',
        choices=available_hosts,
        help=f'Target PVE host (default: pve)'
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
        help='Run scenario locally (for pve-configure)'
    )
    parser.add_argument(
        '--remote',
        help='Target host IP for remote execution (for pve-configure)'
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
            print("\nUsage: ./run.sh --scenario <name> [--host <host>]")
        return 0

    # Load config for phase listing
    config = load_host_config(args.host)
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

    # Pre-populate context if inner-ip provided
    if args.inner_ip:
        orchestrator.context['inner_ip'] = args.inner_ip

    # Pre-populate context for pve-configure scenario
    if args.local:
        orchestrator.context['local_mode'] = True
    if args.remote:
        orchestrator.context['remote_ip'] = args.remote

    success = orchestrator.run()
    return 0 if success else 1


if __name__ == '__main__':
    sys.exit(main())
