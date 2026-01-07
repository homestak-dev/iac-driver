"""User setup scenario.

Creates the homestak user on a PVE host.
Supports both local and remote execution.
"""

from actions import AnsiblePlaybookAction, AnsibleLocalPlaybookAction
from config import HostConfig
from scenarios import register_scenario


@register_scenario
class UserSetup:
    """Create homestak user on a PVE host."""

    name = 'user-setup'
    description = 'Create homestak user'
    requires_root = True
    requires_host_config = False
    expected_runtime = 30

    def get_phases(self, config: HostConfig) -> list[tuple[str, object, str]]:
        """Return phases for user setup.

        Uses local or remote actions based on context:
        - context['local_mode'] = True: Run locally
        - context['remote_ip'] set: Run on remote host
        """
        return [
            ('create_user', _CreateUserPhase(), 'Run user.yml'),
        ]


class _CreateUserPhase:
    """Phase that runs user.yml locally or remotely."""

    def run(self, config: HostConfig, context: dict):
        if context.get('local_mode'):
            action = AnsibleLocalPlaybookAction(
                name='user-local',
                playbook='playbooks/user.yml',
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
                name='user-remote',
                playbook='playbooks/user.yml',
                inventory='inventory/remote-dev.yml',
                extra_vars={'ansible_user': config.ssh_user},
                host_key='remote_ip',
                wait_for_ssh_before=True,
            )
        return action.run(config, context)
