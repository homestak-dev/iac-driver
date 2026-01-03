"""Phase 2: Install Proxmox VE on inner VM using ansible."""

import logging
import time

from ..common import PhaseResult, run_command, wait_for_ssh
from ..config import HostConfig, get_sibling_dir

logger = logging.getLogger(__name__)


def run(config: HostConfig, context: dict) -> PhaseResult:
    """Install Proxmox VE on the inner VM."""
    start = time.time()

    inner_ip = context.get('inner_ip')
    if not inner_ip:
        return PhaseResult(
            success=False,
            message="No inner_ip in context (provision phase failed?)",
            duration=time.time() - start
        )

    ansible_dir = get_sibling_dir('ansible')
    if not ansible_dir.exists():
        return PhaseResult(
            success=False,
            message=f"Ansible directory not found: {ansible_dir}",
            duration=time.time() - start
        )

    # Wait for SSH
    if not wait_for_ssh(inner_ip, timeout=120):
        return PhaseResult(
            success=False,
            message=f"SSH not available on {inner_ip}",
            duration=time.time() - start
        )

    # Run pve-install playbook
    logger.info(f"Running pve-install playbook on {inner_ip}...")
    cmd = [
        'ansible-playbook',
        '-i', 'inventory/remote-dev.yml',
        'playbooks/pve-install.yml',
        '-e', f'ansible_host={inner_ip}',
        '-e', 'pve_hostname=pve-deb'
    ]

    rc, out, err = run_command(cmd, cwd=ansible_dir, timeout=1200)  # 20 min for PVE install + reboot
    if rc != 0:
        return PhaseResult(
            success=False,
            message=f"pve-install playbook failed: {err[-500:] if err else out[-500:]}",
            duration=time.time() - start
        )

    # Wait for SSH after reboot (PVE install reboots the system)
    logger.info("Waiting for SSH after PVE install reboot...")
    if not wait_for_ssh(inner_ip, timeout=300):
        return PhaseResult(
            success=False,
            message=f"SSH not available after reboot on {inner_ip}",
            duration=time.time() - start
        )

    return PhaseResult(
        success=True,
        message=f"Proxmox VE installed on {inner_ip}",
        duration=time.time() - start
    )
