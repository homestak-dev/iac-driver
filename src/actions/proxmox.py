"""Proxmox VE actions."""

import logging
import time
from dataclasses import dataclass
from typing import Optional

from common import ActionResult, run_ssh, start_vm, wait_for_guest_agent
from config import HostConfig

logger = logging.getLogger(__name__)


@dataclass
class StartVMAction:
    """Start a VM on a PVE host."""
    name: str
    vm_id_attr: str = 'inner_vm_id'  # config attribute for VM ID
    pve_host_attr: str = 'ssh_host'  # config attribute for PVE host

    def run(self, config: HostConfig, context: dict) -> ActionResult:
        """Start the VM."""
        start = time.time()

        vm_id = getattr(config, self.vm_id_attr, None)
        pve_host = getattr(config, self.pve_host_attr, None)

        if not vm_id or not pve_host:
            return ActionResult(
                success=False,
                message=f"Missing config: {self.vm_id_attr}={vm_id}, {self.pve_host_attr}={pve_host}",
                duration=time.time() - start
            )

        logger.info(f"[{self.name}] Starting VM {vm_id} on {pve_host}...")
        if not start_vm(vm_id, pve_host):
            return ActionResult(
                success=False,
                message=f"Failed to start VM {vm_id}",
                duration=time.time() - start
            )

        return ActionResult(
            success=True,
            message=f"VM {vm_id} started",
            duration=time.time() - start
        )


@dataclass
class WaitForGuestAgentAction:
    """Wait for QEMU guest agent and get VM IP."""
    name: str
    vm_id_attr: str = 'inner_vm_id'
    pve_host_attr: str = 'ssh_host'
    ip_context_key: str = 'inner_ip'  # store IP in context
    timeout: int = 300

    def run(self, config: HostConfig, context: dict) -> ActionResult:
        """Wait for guest agent and extract IP."""
        start = time.time()

        vm_id = getattr(config, self.vm_id_attr, None)
        pve_host = getattr(config, self.pve_host_attr, None)

        if not vm_id or not pve_host:
            return ActionResult(
                success=False,
                message=f"Missing config: {self.vm_id_attr}={vm_id}, {self.pve_host_attr}={pve_host}",
                duration=time.time() - start
            )

        logger.info(f"[{self.name}] Waiting for guest agent on VM {vm_id}...")
        ip = wait_for_guest_agent(vm_id, pve_host, timeout=self.timeout)

        if not ip:
            return ActionResult(
                success=False,
                message=f"Failed to get IP for VM {vm_id}",
                duration=time.time() - start
            )

        return ActionResult(
            success=True,
            message=f"VM {vm_id} has IP: {ip}",
            duration=time.time() - start,
            context_updates={self.ip_context_key: ip}
        )


@dataclass
class StartVMRemoteAction:
    """Start a VM on a remote PVE host via SSH."""
    name: str
    vm_id_attr: str = 'test_vm_id'
    pve_host_key: str = 'inner_ip'  # context key for remote PVE host

    def run(self, config: HostConfig, context: dict) -> ActionResult:
        """Start VM on remote PVE."""
        start = time.time()

        vm_id = getattr(config, self.vm_id_attr, None)
        pve_host = context.get(self.pve_host_key)

        if not vm_id:
            return ActionResult(
                success=False,
                message=f"Missing config attribute: {self.vm_id_attr}",
                duration=time.time() - start
            )

        if not pve_host:
            return ActionResult(
                success=False,
                message=f"No {self.pve_host_key} in context",
                duration=time.time() - start
            )

        logger.info(f"[{self.name}] Starting VM {vm_id} on {pve_host}...")
        rc, out, err = run_ssh(pve_host, f'qm start {vm_id}', timeout=60)

        if rc != 0:
            return ActionResult(
                success=False,
                message=f"Failed to start VM {vm_id}: {err}",
                duration=time.time() - start
            )

        return ActionResult(
            success=True,
            message=f"VM {vm_id} started on {pve_host}",
            duration=time.time() - start
        )


@dataclass
class WaitForGuestAgentRemoteAction:
    """Wait for guest agent on a remote PVE and get VM IP."""
    name: str
    vm_id_attr: str = 'test_vm_id'
    pve_host_key: str = 'inner_ip'
    ip_context_key: str = 'test_ip'
    timeout: int = 300
    interval: int = 10

    def run(self, config: HostConfig, context: dict) -> ActionResult:
        """Wait for guest agent on remote PVE."""
        start = time.time()

        vm_id = getattr(config, self.vm_id_attr, None)
        pve_host = context.get(self.pve_host_key)

        if not vm_id:
            return ActionResult(
                success=False,
                message=f"Missing config attribute: {self.vm_id_attr}",
                duration=time.time() - start
            )

        if not pve_host:
            return ActionResult(
                success=False,
                message=f"No {self.pve_host_key} in context",
                duration=time.time() - start
            )

        logger.info(f"[{self.name}] Waiting for guest agent on VM {vm_id}...")

        # Poll for IP via remote qm command
        test_ip = None
        deadline = time.time() + self.timeout
        while time.time() < deadline:
            rc, out, err = run_ssh(
                pve_host,
                f'qm guest cmd {vm_id} network-get-interfaces 2>/dev/null | jq -r \'.[].["ip-addresses"][]? | select(.["ip-address-type"]=="ipv4") | .["ip-address"]\' | grep -v "^127\\." | head -1',
                timeout=30
            )
            if rc == 0 and out.strip():
                test_ip = out.strip()
                break
            logger.debug(f"Guest agent not ready on VM {vm_id}, retrying...")
            time.sleep(self.interval)

        if not test_ip:
            return ActionResult(
                success=False,
                message=f"Failed to get IP for VM {vm_id}",
                duration=time.time() - start
            )

        return ActionResult(
            success=True,
            message=f"VM {vm_id} has IP: {test_ip}",
            duration=time.time() - start,
            context_updates={self.ip_context_key: test_ip}
        )
