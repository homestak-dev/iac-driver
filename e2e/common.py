"""Common utilities and types for E2E phases."""

import logging
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class PhaseResult:
    """Result returned by a phase."""
    success: bool
    message: str = ''
    duration: float = 0.0
    context_updates: dict = field(default_factory=dict)
    continue_on_failure: bool = False


def run_command(
    cmd: list[str],
    cwd: Optional[Path] = None,
    timeout: int = 600,
    capture: bool = True
) -> tuple[int, str, str]:
    """Run a command and return (returncode, stdout, stderr)."""
    logger.debug(f"Running: {' '.join(cmd)}")
    try:
        result = subprocess.run(
            cmd,
            cwd=cwd,
            capture_output=capture,
            text=True,
            timeout=timeout
        )
        return result.returncode, result.stdout, result.stderr
    except subprocess.TimeoutExpired:
        return -1, '', f'Command timed out after {timeout}s'
    except Exception as e:
        return -1, '', str(e)


def run_ssh(
    host: str,
    command: str,
    user: str = 'root',
    timeout: int = 60,
    jump_host: Optional[str] = None
) -> tuple[int, str, str]:
    """Run command over SSH."""
    # Use relaxed host key checking for E2E tests where VMs are recreated
    ssh_opts = [
        '-o', 'StrictHostKeyChecking=no',
        '-o', 'UserKnownHostsFile=/dev/null',
        '-o', 'LogLevel=ERROR',
        '-o', f'ConnectTimeout={timeout}'
    ]

    if jump_host:
        cmd = ['ssh'] + ssh_opts + ['-J', f'{user}@{jump_host}', f'{user}@{host}', command]
    else:
        cmd = ['ssh'] + ssh_opts + [f'{user}@{host}', command]

    return run_command(cmd, timeout=timeout)


def wait_for_ssh(host: str, user: str = 'root', timeout: int = 300, interval: int = 10) -> bool:
    """Wait for SSH to become available."""
    logger.info(f"Waiting for SSH on {host}...")
    start = time.time()
    while time.time() - start < timeout:
        rc, out, err = run_ssh(host, 'echo ready', user=user, timeout=10)
        if rc == 0 and 'ready' in out:
            logger.info(f"SSH available on {host}")
            return True
        logger.debug(f"SSH not ready, retrying in {interval}s...")
        time.sleep(interval)
    logger.error(f"SSH timeout waiting for {host}")
    return False


def get_vm_ip(vm_id: int, pve_host: str, interface: str = 'eth0') -> Optional[str]:
    """Get VM IP via qm guest cmd on PVE host."""
    rc, out, err = run_ssh(pve_host, f'qm guest cmd {vm_id} network-get-interfaces')
    if rc != 0:
        return None

    import json
    try:
        interfaces = json.loads(out)
        for iface in interfaces:
            if iface.get('name') == interface or interface == '*':
                for addr in iface.get('ip-addresses', []):
                    if addr.get('ip-address-type') == 'ipv4':
                        ip = addr.get('ip-address')
                        if ip and not ip.startswith('127.'):
                            return ip
    except (json.JSONDecodeError, KeyError):
        pass
    return None


def wait_for_guest_agent(
    vm_id: int,
    pve_host: str,
    timeout: int = 300,
    interval: int = 10
) -> Optional[str]:
    """Wait for guest agent and return IP."""
    logger.info(f"Waiting for guest agent on VM {vm_id}...")
    start = time.time()
    while time.time() - start < timeout:
        ip = get_vm_ip(vm_id, pve_host, '*')
        if ip:
            logger.info(f"VM {vm_id} has IP: {ip}")
            return ip
        logger.debug(f"Guest agent not ready, retrying in {interval}s...")
        time.sleep(interval)
    logger.error(f"Guest agent timeout for VM {vm_id}")
    return None


def start_vm(vm_id: int, pve_host: str) -> bool:
    """Start a VM on the PVE host."""
    logger.info(f"Starting VM {vm_id} on {pve_host}...")
    rc, out, err = run_ssh(pve_host, f'qm start {vm_id}')
    return rc == 0
