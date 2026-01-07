"""PVE setup scenario.

Runs pve-setup.yml and user.yml playbooks to configure a Proxmox VE host.
Supports both local and remote execution.
"""

from actions import AnsiblePlaybookAction, AnsibleLocalPlaybookAction
from config import HostConfig
from scenarios import register_scenario


@register_scenario
class PVESetup:
    """Setup a PVE host with pve-setup and user playbooks."""

    name = 'pve-setup'
    description = 'Setup PVE host (pve-setup + user)'

    def get_phases(self, config: HostConfig) -> list[tuple[str, object, str]]:
        """Return phases for PVE setup.

        Uses local or remote actions based on context:
        - context['local_mode'] = True: Run playbooks locally
        - context['remote_ip'] set: Run playbooks on remote host
        """
        # Note: Context is checked at runtime by the orchestrator
        # We return a factory function that creates the right action
        return [
            ('setup_pve', _PVESetupPhase(), 'Run pve-setup.yml'),
            ('create_user', _UserPhase(), 'Run user.yml'),
        ]


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


class _UserPhase:
    """Phase that runs user.yml locally or remotely."""

    def run(self, config: HostConfig, context: dict):
        if context.get('local_mode'):
            action = AnsibleLocalPlaybookAction(
                name='user-local',
                playbook='playbooks/user.yml',
            )
        else:
            action = AnsiblePlaybookAction(
                name='user-remote',
                playbook='playbooks/user.yml',
                inventory='inventory/remote-dev.yml',
                extra_vars={'ansible_user': config.ssh_user},
                host_key='remote_ip',
                wait_for_ssh_before=False,  # Already connected from previous phase
            )
        return action.run(config, context)
