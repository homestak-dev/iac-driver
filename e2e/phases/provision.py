"""Phase 1: Provision inner PVE VM using tofu."""

import logging
import time
from pathlib import Path

from ..common import PhaseResult, run_command, wait_for_guest_agent
from ..config import HostConfig, get_sibling_dir

logger = logging.getLogger(__name__)


def run(config: HostConfig, context: dict) -> PhaseResult:
    """Provision the inner PVE VM."""
    start = time.time()

    tofu_dir = get_sibling_dir('tofu') / 'envs' / 'pve-deb'
    if not tofu_dir.exists():
        return PhaseResult(
            success=False,
            message=f"Tofu directory not found: {tofu_dir}",
            duration=time.time() - start
        )

    # Run tofu init
    logger.info("Running tofu init...")
    rc, out, err = run_command(['tofu', 'init'], cwd=tofu_dir, timeout=120)
    if rc != 0:
        return PhaseResult(
            success=False,
            message=f"tofu init failed: {err}",
            duration=time.time() - start
        )

    # Run tofu apply
    logger.info("Running tofu apply...")
    cmd = ['tofu', 'apply', '-auto-approve']
    if config.tfvars_file.exists():
        cmd.extend(['-var-file', str(config.tfvars_file)])

    rc, out, err = run_command(cmd, cwd=tofu_dir, timeout=600)
    if rc != 0:
        return PhaseResult(
            success=False,
            message=f"tofu apply failed: {err}",
            duration=time.time() - start
        )

    # Wait for guest agent and get IP
    logger.info(f"Waiting for VM {config.inner_vm_id} guest agent...")
    inner_ip = wait_for_guest_agent(config.inner_vm_id, timeout=300)
    if not inner_ip:
        return PhaseResult(
            success=False,
            message=f"Failed to get IP for VM {config.inner_vm_id}",
            duration=time.time() - start
        )

    return PhaseResult(
        success=True,
        message=f"Inner PVE VM provisioned at {inner_ip}",
        duration=time.time() - start,
        context_updates={'inner_ip': inner_ip}
    )
