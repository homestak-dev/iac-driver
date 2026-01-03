#!/usr/bin/env python3
"""
E2E Test Orchestrator for Proxmox VE Infrastructure

Runs the full nested PVE test sequence:
1. Provision inner PVE VM (tofu)
2. Install Proxmox VE (ansible)
3. Configure inner PVE (ansible)
4. Build/download packer image
5. Provision test VM (tofu)
6. Verify SSH chain
"""

import argparse
import json
import logging
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional

from .phases import provision, install_pve, configure, download_image, test_vm, verify
from .config import load_host_config, list_hosts
from .reporting import TestReport

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger(__name__)


class Orchestrator:
    """Coordinates E2E test phases."""

    PHASES = [
        ('provision', provision, 'Provision inner PVE VM'),
        ('install_pve', install_pve, 'Install Proxmox VE'),
        ('configure', configure, 'Configure inner PVE'),
        ('download_image', download_image, 'Download packer image from release'),
        ('test_vm', test_vm, 'Provision test VM'),
        ('verify', verify, 'Verify SSH chain'),
    ]

    def __init__(self, host: str, report_dir: Path, skip_phases: list[str] = None):
        self.host = host
        self.report_dir = report_dir
        self.skip_phases = skip_phases or []
        self.config = load_host_config(host)
        self.report = TestReport(host=host, report_dir=report_dir)
        self.context = {}  # Shared state between phases

    def run(self) -> bool:
        """Run all phases. Returns True if all passed."""
        logger.info(f"Starting E2E test on host: {self.host}")
        self.report.start()

        all_passed = True
        for phase_name, phase_module, description in self.PHASES:
            if phase_name in self.skip_phases:
                logger.info(f"Skipping phase: {phase_name}")
                self.report.skip_phase(phase_name, description)
                continue

            logger.info(f"Running phase: {phase_name} - {description}")
            self.report.start_phase(phase_name, description)

            try:
                result = phase_module.run(self.config, self.context)
                if result.success:
                    logger.info(f"Phase {phase_name} passed")
                    self.report.pass_phase(phase_name, result.message, result.duration)
                    self.context.update(result.context_updates or {})
                else:
                    logger.error(f"Phase {phase_name} failed: {result.message}")
                    self.report.fail_phase(phase_name, result.message, result.duration)
                    all_passed = False
                    if not result.continue_on_failure:
                        break
            except Exception as e:
                logger.exception(f"Phase {phase_name} raised exception")
                self.report.fail_phase(phase_name, str(e), 0)
                all_passed = False
                break

        self.report.finish(all_passed)
        return all_passed


def main():
    available_hosts = list_hosts()

    parser = argparse.ArgumentParser(
        description='E2E Test Orchestrator for Proxmox VE Infrastructure'
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
        default=Path(__file__).parent / 'reports',
        help='Directory for test reports'
    )
    parser.add_argument(
        '--skip', '-s',
        action='append',
        default=[],
        help='Phases to skip (can be repeated)'
    )
    parser.add_argument(
        '--list-phases',
        action='store_true',
        help='List available phases and exit'
    )
    parser.add_argument(
        '--verbose', '-v',
        action='store_true',
        help='Enable verbose logging'
    )
    parser.add_argument(
        '--inner-ip',
        help='Inner PVE VM IP (auto-detected if not provided, required when skipping provision)'
    )

    args = parser.parse_args()

    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    if args.list_phases:
        print("Available phases:")
        for name, _, desc in Orchestrator.PHASES:
            print(f"  {name}: {desc}")
        return 0

    orchestrator = Orchestrator(
        host=args.host,
        report_dir=args.report_dir,
        skip_phases=args.skip
    )

    # Pre-populate context if inner-ip provided
    if args.inner_ip:
        orchestrator.context['inner_ip'] = args.inner_ip

    success = orchestrator.run()
    return 0 if success else 1


if __name__ == '__main__':
    sys.exit(main())
