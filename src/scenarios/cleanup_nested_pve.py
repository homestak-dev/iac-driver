"""Cleanup scenario for nested PVE environment.

Destroys the inner PVE VM and any test VMs created during integration testing.
"""

import time
import logging
from dataclasses import dataclass

from actions import TofuDestroyAction, TofuDestroyRemoteAction
from common import ActionResult, run_ssh
from config import HostConfig
from scenarios import register_scenario

logger = logging.getLogger(__name__)


@dataclass
class StopVMAction:
    """Stop a VM on a PVE host (if running)."""
    name: str
    vm_id_attr: str
    pve_host_attr: str = 'ssh_host'

    def run(self, config: HostConfig, context: dict) -> ActionResult:
        """Stop the VM if running."""
        start = time.time()

        # Check context first (from TofuApplyAction), then config
        vm_id = context.get(self.vm_id_attr) or getattr(config, self.vm_id_attr, None)
        pve_host = context.get(self.pve_host_attr) or getattr(config, self.pve_host_attr, None)

        if not vm_id or not pve_host:
            return ActionResult(
                success=False,
                message=f"Missing config: {self.vm_id_attr}={vm_id}, {self.pve_host_attr}={pve_host}",
                duration=time.time() - start
            )

        # Check if VM exists
        logger.info(f"[{self.name}] Checking VM {vm_id} on {pve_host}...")
        rc, out, err = run_ssh(pve_host, f'qm status {vm_id}', timeout=30)

        if rc != 0:
            return ActionResult(
                success=True,
                message=f"VM {vm_id} does not exist",
                duration=time.time() - start
            )

        if 'stopped' in out:
            return ActionResult(
                success=True,
                message=f"VM {vm_id} already stopped",
                duration=time.time() - start
            )

        # Stop VM
        logger.info(f"[{self.name}] Stopping VM {vm_id}...")
        rc, out, err = run_ssh(pve_host, f'qm stop {vm_id}', timeout=60)

        if rc != 0:
            return ActionResult(
                success=False,
                message=f"Failed to stop VM {vm_id}: {err}",
                duration=time.time() - start
            )

        return ActionResult(
            success=True,
            message=f"VM {vm_id} stopped",
            duration=time.time() - start
        )


@dataclass
class DestroyRemoteVMAction:
    """Destroy a VM on a remote PVE via tofu (best effort)."""
    name: str
    vm_id_attr: str = 'test_vm_id'
    inner_ip_key: str = 'inner_ip'

    def run(self, config: HostConfig, context: dict) -> ActionResult:
        """Destroy VM on inner PVE if reachable."""
        start = time.time()

        inner_ip = context.get(self.inner_ip_key)
        if not inner_ip:
            return ActionResult(
                success=True,
                message="No inner_ip - skipping remote cleanup",
                duration=time.time() - start
            )

        vm_id = getattr(config, self.vm_id_attr, None)

        # Check if inner PVE is reachable
        logger.info(f"[{self.name}] Checking if inner PVE {inner_ip} is reachable...")
        rc, out, err = run_ssh(inner_ip, 'echo ready', timeout=10)

        if rc != 0:
            return ActionResult(
                success=True,
                message=f"Inner PVE not reachable - skipping",
                duration=time.time() - start
            )

        # Try to destroy test VM via tofu
        logger.info(f"[{self.name}] Destroying test VM on inner PVE...")
        rc, out, err = run_ssh(
            inner_ip,
            'cd /root/tofu/envs/test && tofu destroy -auto-approve 2>/dev/null || true',
            timeout=120
        )

        # Also try direct qm destroy as fallback
        if vm_id:
            run_ssh(inner_ip, f'qm stop {vm_id} 2>/dev/null || true', timeout=30)
            run_ssh(inner_ip, f'qm destroy {vm_id} 2>/dev/null || true', timeout=30)

        return ActionResult(
            success=True,
            message=f"Remote cleanup completed",
            duration=time.time() - start
        )


@register_scenario
class NestedPVEDestructor:
    """Cleanup nested PVE environment."""

    name = 'nested-pve-destructor'
    description = 'Cleanup test VM (if reachable), stop and destroy inner PVE VM'
    expected_runtime = 120  # ~2 min

    def get_phases(self, config: HostConfig) -> list[tuple[str, object, str]]:
        """Return phases for cleanup."""
        return [
            # Phase 1: Cleanup test VM on inner PVE (if reachable)
            ('cleanup_remote', TofuDestroyRemoteAction(
                name='cleanup-remote-vm',
                env_name='test',
                node_name='nested-pve',
                host_key='inner_ip',
            ), 'Cleanup test VM on inner PVE'),

            # Phase 2: Stop inner PVE VM (if running)
            ('stop_inner', StopVMAction(
                name='stop-inner-pve',
                vm_id_attr='nested-pve_vm_id',
                pve_host_attr='ssh_host',
            ), 'Stop inner PVE VM'),

            # Phase 3: Destroy inner PVE via tofu
            ('destroy_inner', TofuDestroyAction(
                name='destroy-inner-pve',
                env_name='nested-pve',
            ), 'Destroy inner PVE VM'),
        ]
