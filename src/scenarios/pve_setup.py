"""PVE setup scenario.

Installs PVE (if needed) and configures a Proxmox VE host.
Supports both local and remote execution.

After PVE is installed and configured, generates nodes/{hostname}.yaml
to enable the host for use with vm-constructor and other scenarios.
"""

import logging
import subprocess
import time

from actions import AnsiblePlaybookAction, AnsibleLocalPlaybookAction, EnsurePVEAction
from common import ActionResult, run_command, run_ssh, wait_for_ssh
from config import HostConfig, get_sibling_dir, get_site_config_dir
from scenarios import register_scenario

logger = logging.getLogger(__name__)


@register_scenario
class PVESetup:
    """Install and configure a PVE host."""

    name = 'pve-setup'
    description = 'Install PVE (if needed) and configure host'
    requires_root = True
    requires_host_config = False
    expected_runtime = 180  # ~3 min (skip if PVE already installed)

    def get_phases(self, _config: HostConfig) -> list[tuple[str, object, str]]:
        """Return phases for PVE setup.

        Uses local or remote actions based on context:
        - context['local_mode'] = True: Run locally
        - context['remote_ip'] set: Run on remote host
        """
        return [
            ('ensure_pve', _EnsurePVEPhase(), 'Ensure PVE installed'),
            ('setup_pve', _PVESetupPhase(), 'Run pve-setup.yml'),
            ('generate_node_config', _GenerateNodeConfigPhase(), 'Generate node config'),
        ]


class _EnsurePVEPhase:
    """Phase that ensures PVE is installed locally or remotely."""

    def run(self, config: HostConfig, context: dict):
        """Ensure PVE is installed locally or remotely."""
        start = time.time()

        if context.get('local_mode'):
            # Check locally if PVE is running
            result = subprocess.run(
                ['systemctl', 'is-active', 'pveproxy'],
                capture_output=True,
                text=True,
                timeout=30,
                check=False
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
        """Run pve-setup.yml locally or remotely."""
        if context.get('local_mode'):
            action = AnsibleLocalPlaybookAction(
                name='pve-setup-local',
                playbook='playbooks/pve-setup.yml',
            )
        else:
            # Use remote_ip from context, or fall back to config.ssh_host
            remote_ip = context.get('remote_ip') or config.ssh_host
            if not remote_ip:
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


class _GenerateNodeConfigPhase:
    """Phase that generates nodes/{hostname}.yaml after PVE setup.

    Creates the node configuration file that enables the host for use
    with vm-constructor and other PVE-dependent scenarios.

    In remote mode, also copies the generated config back to local site-config.
    """

    def run(self, config: HostConfig, context: dict) -> ActionResult:
        """Generate node config locally or remotely."""
        start = time.time()

        if context.get('local_mode'):
            return self._run_local(config, context, start)
        return self._run_remote(config, context, start)

    def _run_local(self, _config: HostConfig, _context: dict, start: float) -> ActionResult:
        """Generate node config locally."""
        try:
            site_config_dir = get_site_config_dir()
        except Exception as e:
            return ActionResult(
                success=False,
                message=f"Cannot find site-config: {e}",
                duration=time.time() - start
            )

        logger.info("Generating node config locally...")
        rc, out, err = run_command(
            ['make', 'node-config', 'FORCE=1'],
            cwd=site_config_dir,
            timeout=60
        )

        if rc != 0:
            return ActionResult(
                success=False,
                message=f"make node-config failed: {err or out}",
                duration=time.time() - start
            )

        # Extract hostname from output or detect it
        import socket
        hostname = socket.gethostname()
        node_file = site_config_dir / 'nodes' / f'{hostname}.yaml'

        return ActionResult(
            success=True,
            message=f"Generated {node_file}",
            duration=time.time() - start,
            context_updates={'generated_node_config': str(node_file)}
        )

    def _run_remote(self, config: HostConfig, context: dict, start: float) -> ActionResult:
        """Generate node config on remote host and sync back."""
        remote_ip = context.get('remote_ip') or config.ssh_host
        if not remote_ip:
            return ActionResult(
                success=False,
                message="No remote_ip in context",
                duration=time.time() - start
            )

        # Determine site-config path on remote (FHS or legacy)
        # Try FHS first, fall back to legacy
        detect_cmd = '''
if [ -d /usr/local/etc/homestak ]; then
    echo "/usr/local/etc/homestak"
elif [ -d /opt/homestak/site-config ]; then
    echo "/opt/homestak/site-config"
else
    echo "NOT_FOUND"
fi
'''
        rc, remote_site_config, _ = run_ssh(remote_ip, detect_cmd, timeout=10)
        remote_site_config = remote_site_config.strip()

        if rc != 0 or remote_site_config == "NOT_FOUND":
            return ActionResult(
                success=False,
                message="site-config not found on remote host. Is it bootstrapped?",
                duration=time.time() - start
            )

        # Generate node config on remote
        logger.info(f"Generating node config on {remote_ip}...")
        rc, out, err = run_ssh(
            remote_ip,
            f'cd {remote_site_config} && make node-config FORCE=1',
            timeout=60
        )

        if rc != 0:
            return ActionResult(
                success=False,
                message=f"Remote make node-config failed: {err or out}",
                duration=time.time() - start
            )

        # Get hostname from remote
        rc, remote_hostname, _ = run_ssh(remote_ip, 'hostname', timeout=10)
        remote_hostname = remote_hostname.strip()

        if not remote_hostname:
            return ActionResult(
                success=False,
                message="Could not determine remote hostname",
                duration=time.time() - start
            )

        # Copy generated node config back to local site-config
        try:
            local_site_config = get_site_config_dir()
        except Exception as e:
            return ActionResult(
                success=False,
                message=f"Cannot find local site-config: {e}",
                duration=time.time() - start
            )

        remote_node_file = f'{remote_site_config}/nodes/{remote_hostname}.yaml'
        local_node_file = local_site_config / 'nodes' / f'{remote_hostname}.yaml'

        logger.info(f"Copying {remote_node_file} to {local_node_file}...")

        # Use scp to copy the file
        scp_cmd = [
            'scp',
            '-o', 'StrictHostKeyChecking=no',
            '-o', 'UserKnownHostsFile=/dev/null',
            f'root@{remote_ip}:{remote_node_file}',
            str(local_node_file)
        ]

        result = subprocess.run(scp_cmd, capture_output=True, text=True, timeout=30, check=False)
        if result.returncode != 0:
            return ActionResult(
                success=False,
                message=f"scp failed: {result.stderr}",
                duration=time.time() - start
            )

        return ActionResult(
            success=True,
            message=f"Generated and synced nodes/{remote_hostname}.yaml",
            duration=time.time() - start,
            context_updates={
                'generated_node_config': str(local_node_file),
                'remote_hostname': remote_hostname
            }
        )
