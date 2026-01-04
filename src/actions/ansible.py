"""Ansible playbook actions."""

import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from common import ActionResult, run_command, wait_for_ssh
from config import HostConfig, get_sibling_dir

logger = logging.getLogger(__name__)


@dataclass
class AnsiblePlaybookAction:
    """Run an ansible playbook."""
    name: str
    playbook: str  # e.g., "playbooks/pve-install.yml"
    inventory: str = "inventory/remote-dev.yml"
    extra_vars: dict = field(default_factory=dict)
    host_key: str = 'inner_ip'  # context key for ansible_host
    wait_for_ssh_before: bool = True
    wait_for_ssh_after: bool = False
    ssh_timeout: int = 120
    timeout: int = 600

    def run(self, config: HostConfig, context: dict) -> ActionResult:
        """Execute ansible playbook."""
        start = time.time()

        target_host = context.get(self.host_key)
        if not target_host:
            return ActionResult(
                success=False,
                message=f"No {self.host_key} in context",
                duration=time.time() - start
            )

        ansible_dir = get_sibling_dir('ansible')
        if not ansible_dir.exists():
            return ActionResult(
                success=False,
                message=f"Ansible directory not found: {ansible_dir}",
                duration=time.time() - start
            )

        # Wait for SSH if requested
        if self.wait_for_ssh_before:
            if not wait_for_ssh(target_host, timeout=self.ssh_timeout):
                return ActionResult(
                    success=False,
                    message=f"SSH not available on {target_host}",
                    duration=time.time() - start
                )

        # Build command
        logger.info(f"[{self.name}] Running {self.playbook} on {target_host}...")
        cmd = [
            'ansible-playbook',
            '-i', self.inventory,
            self.playbook,
            '-e', f'ansible_host={target_host}'
        ]

        # Add extra vars
        for key, value in self.extra_vars.items():
            cmd.extend(['-e', f'{key}={value}'])

        rc, out, err = run_command(cmd, cwd=ansible_dir, timeout=self.timeout)
        if rc != 0:
            # Truncate error message for readability
            error_msg = err[-500:] if err else out[-500:]
            return ActionResult(
                success=False,
                message=f"{self.playbook} failed: {error_msg}",
                duration=time.time() - start
            )

        # Wait for SSH after reboot if requested
        if self.wait_for_ssh_after:
            logger.info(f"[{self.name}] Waiting for SSH after playbook...")
            if not wait_for_ssh(target_host, timeout=self.ssh_timeout * 2):
                return ActionResult(
                    success=False,
                    message=f"SSH not available after reboot on {target_host}",
                    duration=time.time() - start
                )

        return ActionResult(
            success=True,
            message=f"{self.playbook} completed on {target_host}",
            duration=time.time() - start
        )


@dataclass
class AnsibleLocalPlaybookAction:
    """Run an ansible playbook locally."""
    name: str
    playbook: str  # e.g., "playbooks/pve-setup.yml"
    inventory: str = "inventory/local.yml"
    extra_vars: dict = field(default_factory=dict)
    timeout: int = 600

    def run(self, config: HostConfig, context: dict) -> ActionResult:
        """Execute ansible playbook locally."""
        start = time.time()

        ansible_dir = get_sibling_dir('ansible')
        if not ansible_dir.exists():
            return ActionResult(
                success=False,
                message=f"Ansible directory not found: {ansible_dir}",
                duration=time.time() - start
            )

        # Build command
        logger.info(f"[{self.name}] Running {self.playbook} locally...")
        cmd = [
            'ansible-playbook',
            '-i', self.inventory,
            self.playbook,
        ]

        # Add extra vars
        for key, value in self.extra_vars.items():
            cmd.extend(['-e', f'{key}={value}'])

        rc, out, err = run_command(cmd, cwd=ansible_dir, timeout=self.timeout)
        if rc != 0:
            # Truncate error message for readability
            error_msg = err[-500:] if err else out[-500:]
            return ActionResult(
                success=False,
                message=f"{self.playbook} failed: {error_msg}",
                duration=time.time() - start
            )

        return ActionResult(
            success=True,
            message=f"{self.playbook} completed locally",
            duration=time.time() - start
        )
