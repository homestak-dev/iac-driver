"""Phase 5: Provision test VM on inner PVE."""

import logging
import time

from ..common import PhaseResult, run_ssh
from ..config import HostConfig

logger = logging.getLogger(__name__)


def run(config: HostConfig, context: dict) -> PhaseResult:
    """Provision a test VM on the inner PVE."""
    start = time.time()

    inner_ip = context.get('inner_ip')
    if not inner_ip:
        return PhaseResult(
            success=False,
            message="No inner_ip in context",
            duration=time.time() - start
        )

    # Run tofu init on inner PVE
    logger.info("Running tofu init on inner PVE...")
    rc, out, err = run_ssh(inner_ip, 'cd /root/tofu/envs/test && tofu init', timeout=120)
    if rc != 0:
        return PhaseResult(
            success=False,
            message=f"tofu init failed on inner PVE: {err}",
            duration=time.time() - start
        )

    # Run tofu apply on inner PVE
    logger.info("Running tofu apply on inner PVE...")
    rc, out, err = run_ssh(inner_ip, 'cd /root/tofu/envs/test && tofu apply -auto-approve', timeout=300)
    if rc != 0:
        return PhaseResult(
            success=False,
            message=f"tofu apply failed on inner PVE: {err}",
            duration=time.time() - start
        )

    # Start the test VM
    test_vm_id = config.test_vm_id
    logger.info(f"Starting test VM {test_vm_id}...")
    rc, out, err = run_ssh(inner_ip, f'qm start {test_vm_id}', timeout=60)
    if rc != 0:
        return PhaseResult(
            success=False,
            message=f"Failed to start test VM: {err}",
            duration=time.time() - start
        )

    # Wait for guest agent on test VM (via inner PVE)
    logger.info(f"Waiting for test VM {test_vm_id} guest agent...")
    test_ip = None
    for _ in range(30):  # 5 minutes max
        rc, out, err = run_ssh(
            inner_ip,
            f'qm guest cmd {test_vm_id} network-get-interfaces 2>/dev/null | jq -r \'.[].["ip-addresses"][]? | select(.["ip-address-type"]=="ipv4") | .["ip-address"]\' | grep -v "^127\\." | head -1',
            timeout=30
        )
        if rc == 0 and out.strip():
            test_ip = out.strip()
            break
        time.sleep(10)

    if not test_ip:
        return PhaseResult(
            success=False,
            message=f"Failed to get IP for test VM {test_vm_id}",
            duration=time.time() - start
        )

    return PhaseResult(
        success=True,
        message=f"Test VM {test_vm_id} running at {test_ip}",
        duration=time.time() - start,
        context_updates={'test_ip': test_ip}
    )
