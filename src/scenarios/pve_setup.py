"""PVE setup scenario.

Installs PVE (if needed) and configures a Proxmox VE host.
Supports both local and remote execution.
"""

import subprocess
import time

from actions import AnsiblePlaybookAction, AnsibleLocalPlaybookAction, EnsurePVEAction
from common import ActionResult, run_command, wait_for_ssh
from config import HostConfig, get_sibling_dir
from scenarios import register_scenario


@register_scenario
class PVESetup:
    """Install and configure a PVE host."""

    name = 'pve-setup'
    description = 'Install PVE (if needed) and configure host'
    requires_root = True
    requires_host_config = False
    expected_runtime = 180  # ~3 min (skip if PVE already installed)

    def get_phases(self, config: HostConfig) -> list[tuple[str, object, str]]:
        """Return phases for PVE setup.

        Uses local or remote actions based on context:
        - context['local_mode'] = True: Run locally
        - context['remote_ip'] set: Run on remote host
        """
        return [
            ('ensure_pve', _EnsurePVEPhase(), 'Ensure PVE installed'),
            ('setup_pve', _PVESetupPhase(), 'Run pve-setup.yml'),
        ]


class _EnsurePVEPhase:
    """Phase that ensures PVE is installed locally or remotely."""

    def run(self, config: HostConfig, context: dict):
        start = time.time()

        if context.get('local_mode'):
            # Check locally if PVE is running
            result = subprocess.run(
                ['systemctl', 'is-active', 'pveproxy'],
                capture_output=True,
                text=True,
                timeout=30
            )
            if result.returncode == 0 and 'active' in result.stdout:
                return ActionResult(
                    success=True,
                    message="PVE already installed and running - skipped",
                    duration=time.time() - start
                )

            # PVE not running, install locally
            ansible_dir = get_sibling_dir('ansible')
            if not ansible_dir.exists():
                return ActionResult(
                    success=False,
                    message=f"Ansible directory not found: {ansible_dir}",
                    duration=time.time() - start
                )

            cmd = [
                'ansible-playbook',
                '-i', 'inventory/local.yml',
                'playbooks/pve-install.yml',
            ]

            rc, out, err = run_command(cmd, cwd=ansible_dir, timeout=1200)
            if rc != 0:
                error_msg = err[-500:] if err else out[-500:]
                return ActionResult(
                    success=False,
                    message=f"pve-install.yml failed: {error_msg}",
                    duration=time.time() - start
                )

            return ActionResult(
                success=True,
                message="PVE installed successfully",
                duration=time.time() - start
            )
        else:
            # Remote mode - use EnsurePVEAction
            remote_ip = context.get('remote_ip') or config.ssh_host
            if not remote_ip:
                return ActionResult(
                    success=False,
                    message="No target host: use --local, --remote <IP>, or configure ssh_host",
                    duration=time.time() - start
                )
            context['remote_ip'] = remote_ip

            # Wait for SSH first
            if not wait_for_ssh(remote_ip, timeout=120):
                return ActionResult(
                    success=False,
                    message=f"SSH not available on {remote_ip}",
                    duration=time.time() - start
                )

            action = EnsurePVEAction(
                name='ensure-pve-remote',
                host_key='remote_ip',
                pve_hostname=config.name or 'pve',
            )
            return action.run(config, context)


class _PVESetupPhase:
    """Phase that runs pve-setup.yml locally or remotely."""

    def run(self, config: HostConfig, context: dict):
        if context.get('local_mode'):
            action = AnsibleLocalPlaybookAction(
                name='pve-setup-local',
                playbook='playbooks/pve-setup.yml',
            )
        else:
            # Use remote_ip from context, or fall back to config.ssh_host
            remote_ip = context.get('remote_ip') or config.ssh_host
            if not remote_ip:
                from common import ActionResult
                return ActionResult(
                    success=False,
                    message="No target host: use --local, --remote <IP>, or configure ssh_host",
                    duration=0
                )
            # Ensure remote_ip is in context for AnsiblePlaybookAction
            context['remote_ip'] = remote_ip
            action = AnsiblePlaybookAction(
                name='pve-setup-remote',
                playbook='playbooks/pve-setup.yml',
                inventory='inventory/remote-dev.yml',
                extra_vars={'ansible_user': config.ssh_user},
                host_key='remote_ip',
                wait_for_ssh_before=True,
            )
        return action.run(config, context)
