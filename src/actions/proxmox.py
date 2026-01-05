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

        # Check context first (from TofuApplyAction), then config
        vm_id = context.get(self.vm_id_attr) or getattr(config, self.vm_id_attr, None)
        pve_host = context.get(self.pve_host_attr) or getattr(config, self.pve_host_attr, None)
        ssh_user = config.ssh_user

        if not vm_id or not pve_host:
            return ActionResult(
                success=False,
                message=f"Missing config: {self.vm_id_attr}={vm_id}, {self.pve_host_attr}={pve_host}",
                duration=time.time() - start
            )

        logger.info(f"[{self.name}] Starting VM {vm_id} on {pve_host}...")
        if not start_vm(vm_id, pve_host, user=ssh_user):
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

        # Check context first (from TofuApplyAction), then config
        vm_id = context.get(self.vm_id_attr) or getattr(config, self.vm_id_attr, None)
        pve_host = context.get(self.pve_host_attr) or getattr(config, self.pve_host_attr, None)
        ssh_user = config.ssh_user

        if not vm_id or not pve_host:
            return ActionResult(
                success=False,
                message=f"Missing config: {self.vm_id_attr}={vm_id}, {self.pve_host_attr}={pve_host}",
                duration=time.time() - start
            )

        logger.info(f"[{self.name}] Waiting for guest agent on VM {vm_id}...")
        ip = wait_for_guest_agent(vm_id, pve_host, timeout=self.timeout, user=ssh_user)

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
class StartProvisionedVMsAction:
    """Start all VMs from provisioned_vms context (for multi-VM environments)."""
    name: str
    pve_host_attr: str = 'ssh_host'

    def run(self, config: HostConfig, context: dict) -> ActionResult:
        """Start all provisioned VMs."""
        start = time.time()

        provisioned_vms = context.get('provisioned_vms', [])
        if not provisioned_vms:
            return ActionResult(
                success=False,
                message="No provisioned_vms in context",
                duration=time.time() - start
            )

        pve_host = context.get(self.pve_host_attr) or getattr(config, self.pve_host_attr, None)
        ssh_user = config.ssh_user

        if not pve_host:
            return ActionResult(
                success=False,
                message=f"Missing config: {self.pve_host_attr}",
                duration=time.time() - start
            )

        started = []
        for vm in provisioned_vms:
            vm_name = vm.get('name')
            vm_id = vm.get('vmid')
            logger.info(f"[{self.name}] Starting VM {vm_id} ({vm_name}) on {pve_host}...")
            if start_vm(vm_id, pve_host, user=ssh_user):
                started.append(vm_name)
            else:
                return ActionResult(
                    success=False,
                    message=f"Failed to start VM {vm_id} ({vm_name})",
                    duration=time.time() - start
                )

        return ActionResult(
            success=True,
            message=f"Started {len(started)} VMs: {', '.join(started)}",
            duration=time.time() - start
        )


@dataclass
class WaitForProvisionedVMsAction:
    """Wait for guest agent on all provisioned VMs and collect their IPs."""
    name: str
    pve_host_attr: str = 'ssh_host'
    timeout: int = 300

    def run(self, config: HostConfig, context: dict) -> ActionResult:
        """Wait for guest agent on all provisioned VMs."""
        start = time.time()

        provisioned_vms = context.get('provisioned_vms', [])
        if not provisioned_vms:
            return ActionResult(
                success=False,
                message="No provisioned_vms in context",
                duration=time.time() - start
            )

        pve_host = context.get(self.pve_host_attr) or getattr(config, self.pve_host_attr, None)
        ssh_user = config.ssh_user

        if not pve_host:
            return ActionResult(
                success=False,
                message=f"Missing config: {self.pve_host_attr}",
                duration=time.time() - start
            )

        context_updates = {}
        vm_ips = {}

        for vm in provisioned_vms:
            vm_name = vm.get('name')
            vm_id = vm.get('vmid')
            logger.info(f"[{self.name}] Waiting for guest agent on VM {vm_id} ({vm_name})...")

            ip = wait_for_guest_agent(vm_id, pve_host, timeout=self.timeout, user=ssh_user)
            if not ip:
                return ActionResult(
                    success=False,
                    message=f"Failed to get IP for VM {vm_id} ({vm_name})",
                    duration=time.time() - start
                )

            # Store IP as {name}_ip (e.g., deb12-test_ip)
            context_updates[f'{vm_name}_ip'] = ip
            vm_ips[vm_name] = ip
            logger.info(f"[{self.name}] VM {vm_name} has IP: {ip}")

        # Also store first VM's IP as 'vm_ip' for backward compatibility
        if vm_ips:
            first_vm = provisioned_vms[0]['name']
            context_updates['vm_ip'] = vm_ips[first_vm]

        return ActionResult(
            success=True,
            message=f"Got IPs for {len(vm_ips)} VMs",
            duration=time.time() - start,
            context_updates=context_updates
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

        # Check context first (from TofuApplyAction), then config
        vm_id = context.get(self.vm_id_attr) or getattr(config, self.vm_id_attr, None)
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
    interval: int = 5

    def run(self, config: HostConfig, context: dict) -> ActionResult:
        """Wait for guest agent on remote PVE."""
        start = time.time()

        # Check context first (from TofuApplyAction), then config
        vm_id = context.get(self.vm_id_attr) or getattr(config, self.vm_id_attr, None)
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
