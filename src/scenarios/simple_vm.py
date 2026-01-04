"""Simple VM test scenario.

Deploys a single VM, verifies SSH access, then destroys it.
"""

import time
import logging
from dataclasses import dataclass

from actions import (
    TofuApplyAction,
    TofuDestroyAction,
    StartVMAction,
    WaitForGuestAgentAction,
    WaitForSSHAction,
)
from common import ActionResult, run_ssh
from config import HostConfig
from scenarios import register_scenario

logger = logging.getLogger(__name__)


@dataclass
class EnsureImageAction:
    """Ensure packer image exists on PVE host, download if missing."""
    name: str

    def run(self, config: HostConfig, context: dict) -> ActionResult:
        """Check for image, download from release if missing."""
        start = time.time()

        pve_host = config.ssh_host
        ssh_user = config.ssh_user
        image_name = config.packer_image.replace('.qcow2', '.img')
        image_path = f'/var/lib/vz/template/iso/{image_name}'

        # Use sudo if not root
        sudo = '' if ssh_user == 'root' else 'sudo '

        # Check if image exists
        logger.info(f"[{self.name}] Checking for {image_name} on {pve_host}...")
        rc, out, err = run_ssh(pve_host, f'{sudo}test -f {image_path} && echo exists',
                               user=ssh_user, timeout=30)

        if rc == 0 and 'exists' in out:
            return ActionResult(
                success=True,
                message=f"Image {image_name} already exists",
                duration=time.time() - start
            )

        # Download from release
        repo = config.packer_release_repo
        tag = config.packer_release_tag
        url = f'https://github.com/{repo}/releases/download/{tag}/{config.packer_image}'

        logger.info(f"[{self.name}] Downloading {config.packer_image} from {repo} {tag}...")

        # Create directory and download
        rc, out, err = run_ssh(pve_host, f'{sudo}mkdir -p /var/lib/vz/template/iso',
                               user=ssh_user, timeout=30)
        if rc != 0:
            return ActionResult(
                success=False,
                message=f"Failed to create directory: {err}",
                duration=time.time() - start
            )

        dl_cmd = f'{sudo}curl -fSL -o {image_path} {url}'
        rc, out, err = run_ssh(pve_host, dl_cmd, user=ssh_user, timeout=300)
        if rc != 0:
            return ActionResult(
                success=False,
                message=f"Failed to download image: {err}",
                duration=time.time() - start
            )

        return ActionResult(
            success=True,
            message=f"Downloaded {image_name}",
            duration=time.time() - start
        )


@register_scenario
class SimpleVMConstructor:
    """Deploy a VM and verify SSH access."""

    name = 'simple-vm-constructor'
    description = 'Ensure image, provision test VM, verify SSH access'

    def get_phases(self, config: HostConfig) -> list[tuple[str, object, str]]:
        """Return phases for simple VM deployment."""
        return [
            ('ensure_image', EnsureImageAction(
                name='ensure-image',
            ), 'Ensure packer image exists'),

            ('provision', TofuApplyAction(
                name='provision-vm',
                env_path='envs/test',
            ), 'Provision test VM'),

            ('start', StartVMAction(
                name='start-vm',
                vm_id_attr='test_vm_id',
                pve_host_attr='ssh_host',
            ), 'Start test VM'),

            ('wait_ip', WaitForGuestAgentAction(
                name='wait-for-ip',
                vm_id_attr='test_vm_id',
                pve_host_attr='ssh_host',
                ip_context_key='vm_ip',
                timeout=180,
            ), 'Wait for VM IP'),

            ('verify_ssh', WaitForSSHAction(
                name='verify-ssh',
                host_key='vm_ip',
                timeout=120,
            ), 'Verify SSH access'),
        ]


@register_scenario
class SimpleVMDestructor:
    """Destroy a test VM."""

    name = 'simple-vm-destructor'
    description = 'Stop and destroy test VM'

    def get_phases(self, config: HostConfig) -> list[tuple[str, object, str]]:
        """Return phases for simple VM destruction."""
        return [
            ('destroy', TofuDestroyAction(
                name='destroy-vm',
                env_path='envs/test',
            ), 'Destroy test VM'),
        ]


@register_scenario
class SimpleVMRoundtrip:
    """Full roundtrip: deploy, verify, destroy."""

    name = 'simple-vm-roundtrip'
    description = 'Deploy test VM, verify SSH, destroy (full cycle)'

    def get_phases(self, config: HostConfig) -> list[tuple[str, object, str]]:
        """Return phases for full roundtrip test."""
        return [
            ('ensure_image', EnsureImageAction(
                name='ensure-image',
            ), 'Ensure packer image exists'),

            ('provision', TofuApplyAction(
                name='provision-vm',
                env_path='envs/test',
            ), 'Provision test VM'),

            ('start', StartVMAction(
                name='start-vm',
                vm_id_attr='test_vm_id',
                pve_host_attr='ssh_host',
            ), 'Start test VM'),

            ('wait_ip', WaitForGuestAgentAction(
                name='wait-for-ip',
                vm_id_attr='test_vm_id',
                pve_host_attr='ssh_host',
                ip_context_key='vm_ip',
                timeout=180,
            ), 'Wait for VM IP'),

            ('verify_ssh', WaitForSSHAction(
                name='verify-ssh',
                host_key='vm_ip',
                timeout=120,
            ), 'Verify SSH access'),

            ('destroy', TofuDestroyAction(
                name='destroy-vm',
                env_path='envs/test',
            ), 'Destroy test VM'),
        ]
