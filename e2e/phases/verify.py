"""Phase 6: Verify SSH chain to test VM."""

import logging
import time

from ..common import PhaseResult, run_ssh

logger = logging.getLogger(__name__)


def run(config, context: dict) -> PhaseResult:
    """Verify SSH connectivity through the full chain."""
    start = time.time()

    inner_ip = context.get('inner_ip')
    test_ip = context.get('test_ip')

    if not inner_ip or not test_ip:
        return PhaseResult(
            success=False,
            message=f"Missing context: inner_ip={inner_ip}, test_ip={test_ip}",
            duration=time.time() - start
        )

    # Wait for SSH on test VM (via jump host)
    logger.info(f"Waiting for SSH on test VM {test_ip}...")
    ssh_ready = False
    for attempt in range(30):  # Wait up to 5 minutes
        rc, out, err = run_ssh(
            test_ip,
            'echo ready',
            jump_host=inner_ip,
            timeout=10
        )
        if rc == 0 and 'ready' in out:
            ssh_ready = True
            break
        logger.debug(f"SSH not ready on test VM, attempt {attempt+1}/30...")
        time.sleep(10)

    if not ssh_ready:
        return PhaseResult(
            success=False,
            message=f"Timeout waiting for SSH on test VM {test_ip}",
            duration=time.time() - start
        )

    # Test SSH jump chain: outer -> inner -> test
    logger.info(f"Verifying SSH chain: outer -> {inner_ip} -> {test_ip}")

    rc, out, err = run_ssh(
        test_ip,
        'hostname && uname -a',
        jump_host=inner_ip,
        timeout=30
    )

    if rc != 0:
        return PhaseResult(
            success=False,
            message=f"SSH chain verification failed: {err}",
            duration=time.time() - start
        )

    hostname = out.strip().split('\n')[0] if out else 'unknown'

    return PhaseResult(
        success=True,
        message=f"SSH chain verified: {hostname}",
        duration=time.time() - start,
        context_updates={'test_hostname': hostname, 'ssh_output': out.strip()}
    )
