"""Phase 3: Configure inner PVE using ansible."""

import logging
import time

from ..common import PhaseResult, run_command, wait_for_ssh
from ..config import HostConfig, get_sibling_dir

logger = logging.getLogger(__name__)


def run(config: HostConfig, context: dict) -> PhaseResult:
    """Configure the inner PVE for testing."""
    start = time.time()

    inner_ip = context.get('inner_ip')
    if not inner_ip:
        return PhaseResult(
            success=False,
            message="No inner_ip in context",
            duration=time.time() - start
        )

    ansible_dir = get_sibling_dir('ansible')

    # Ensure SSH is available
    if not wait_for_ssh(inner_ip, timeout=60):
        return PhaseResult(
            success=False,
            message=f"SSH not available on {inner_ip}",
            duration=time.time() - start
        )

    # Run nested-pve-setup playbook
    logger.info(f"Running nested-pve-setup playbook on {inner_ip}...")
    cmd = [
        'ansible-playbook',
        '-i', 'inventory/remote-dev.yml',
        'playbooks/nested-pve-setup.yml',
        '-e', f'ansible_host={inner_ip}'
    ]

    rc, out, err = run_command(cmd, cwd=ansible_dir, timeout=600)
    if rc != 0:
        return PhaseResult(
            success=False,
            message=f"nested-pve-setup playbook failed: {err[-500:] if err else out[-500:]}",
            duration=time.time() - start
        )

    return PhaseResult(
        success=True,
        message=f"Inner PVE configured at {inner_ip}",
        duration=time.time() - start
    )
