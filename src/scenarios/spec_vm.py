"""Spec VM lifecycle scenarios.

Validates the Create → Specify integration: VMs provisioned via tofu
receive spec server environment variables via cloud-init.
"""

import logging
import time
from dataclasses import dataclass
from typing import Optional

from actions import (
    TofuApplyAction,
    TofuDestroyAction,
    StartProvisionedVMsAction,
    WaitForProvisionedVMsAction,
    WaitForSSHAction,
    SSHCommandAction,
)
from common import ActionResult, run_ssh
from config import HostConfig
from config_resolver import ConfigResolver
from scenarios import register_scenario
from actions.pve_lifecycle import EnsureImageAction

logger = logging.getLogger(__name__)


@dataclass
class CheckSpecServerConfigAction:
    """Verify spec_server is configured in site.yaml."""
    name: str

    def run(self, config: HostConfig, context: dict) -> ActionResult:
        """Check that spec_server is configured."""
        start = time.time()

        try:
            resolver = ConfigResolver()
            site_config = resolver._load_yaml('site.yaml')
            spec_server = site_config.get('defaults', {}).get('spec_server', '')

            if not spec_server:
                return ActionResult(
                    success=False,
                    message="spec_server not configured in site.yaml. "
                            "Set defaults.spec_server to enable Create → Specify flow.",
                    duration=time.time() - start
                )

            logger.info(f"[{self.name}] spec_server configured: {spec_server}")
            return ActionResult(
                success=True,
                message=f"spec_server: {spec_server}",
                duration=time.time() - start,
                context_updates={'spec_server_url': spec_server}
            )
        except Exception as e:
            return ActionResult(
                success=False,
                message=f"Failed to read site.yaml: {e}",
                duration=time.time() - start
            )


@dataclass
class StartSpecServerAction:
    """Start spec server on controller host."""
    name: str
    server_port: int = 44443
    timeout: int = 30

    def run(self, config: HostConfig, context: dict) -> ActionResult:
        """Start spec server on controller via SSH."""
        start = time.time()

        pve_host = config.ssh_host
        ssh_user = config.ssh_user

        # Check if serve command exists (requires v0.44+)
        version_cmd = 'homestak --version 2>/dev/null || echo "NOT_FOUND"'
        rc, out, err = run_ssh(pve_host, version_cmd, user=ssh_user, timeout=10)
        if 'NOT_FOUND' in out:
            return ActionResult(
                success=False,
                message="homestak CLI not found on controller. Run bootstrap first.",
                duration=time.time() - start
            )

        # Check if serve subcommand exists
        help_cmd = 'homestak serve --help 2>&1 | head -1 || echo "COMMAND_NOT_FOUND"'
        rc, out, err = run_ssh(pve_host, help_cmd, user=ssh_user, timeout=10)
        if 'COMMAND_NOT_FOUND' in out or 'Unknown' in out:
            return ActionResult(
                success=False,
                message="'homestak serve' command not available. Requires v0.44+. "
                        "Update bootstrap on controller: cd /usr/local/lib/homestak/bootstrap && git pull",
                duration=time.time() - start
            )

        # Check if already running by checking the port
        check_cmd = f'ss -tlnp | grep ":{self.server_port} " || true'
        rc, out, err = run_ssh(pve_host, check_cmd, user=ssh_user, timeout=10)
        if out.strip():
            logger.info(f"[{self.name}] Spec server already running on port {self.server_port}")
            return ActionResult(
                success=True,
                message=f"Spec server already running on port {self.server_port}",
                duration=time.time() - start,
            )

        # Start spec server in background
        # Use nohup and redirect to log file
        start_cmd = (
            f'nohup /usr/local/bin/homestak serve --port {self.server_port} '
            f'> /tmp/homestak-serve.log 2>&1 & echo $!'
        )
        logger.info(f"[{self.name}] Starting spec server on {pve_host}:{self.server_port}...")
        rc, out, err = run_ssh(pve_host, start_cmd, user=ssh_user, timeout=self.timeout)

        if rc != 0:
            return ActionResult(
                success=False,
                message=f"Failed to start spec server: {err}",
                duration=time.time() - start
            )

        pid = out.strip()

        # Give it a moment to start and verify
        time.sleep(2)
        verify_cmd = f'kill -0 {pid} 2>/dev/null && echo running || echo stopped'
        rc, out, err = run_ssh(pve_host, verify_cmd, user=ssh_user, timeout=10)

        if 'running' not in out:
            # Check log for errors
            log_cmd = 'tail -20 /tmp/homestak-serve.log 2>/dev/null || true'
            _, log_out, _ = run_ssh(pve_host, log_cmd, user=ssh_user, timeout=10)
            return ActionResult(
                success=False,
                message=f"Spec server failed to start. Log: {log_out}",
                duration=time.time() - start
            )

        return ActionResult(
            success=True,
            message=f"Spec server started (PID: {pid})",
            duration=time.time() - start,
            context_updates={'spec_server_pid': pid}
        )


@dataclass
class VerifyEnvVarsAction:
    """Verify HOMESTAK_* env vars are present in /etc/profile.d/homestak.sh."""
    name: str
    host_key: str = 'vm_ip'
    timeout: int = 30

    def run(self, config: HostConfig, context: dict) -> ActionResult:
        """Check that env vars were injected by cloud-init."""
        start = time.time()

        host = context.get(self.host_key)
        if not host:
            return ActionResult(
                success=False,
                message=f"No {self.host_key} in context",
                duration=time.time() - start
            )

        # Read the profile.d file
        cmd = 'cat /etc/profile.d/homestak.sh 2>/dev/null || echo "FILE_NOT_FOUND"'
        logger.info(f"[{self.name}] Checking env vars on {host}...")
        rc, out, err = run_ssh(host, cmd, user=config.automation_user, timeout=self.timeout)

        if 'FILE_NOT_FOUND' in out:
            return ActionResult(
                success=False,
                message="/etc/profile.d/homestak.sh not found - cloud-init may not have run",
                duration=time.time() - start
            )

        # Check for required env vars
        required_vars = ['HOMESTAK_SPEC_SERVER', 'HOMESTAK_IDENTITY']
        missing = []
        for var in required_vars:
            if var not in out:
                missing.append(var)

        if missing:
            return ActionResult(
                success=False,
                message=f"Missing env vars: {', '.join(missing)}. Content: {out[:200]}",
                duration=time.time() - start
            )

        # Extract values for logging
        lines = out.strip().split('\n')
        env_summary = '; '.join(l.strip() for l in lines if l.strip() and not l.startswith('#'))

        return ActionResult(
            success=True,
            message=f"Env vars present: {env_summary[:100]}",
            duration=time.time() - start,
            context_updates={'homestak_env_content': out.strip()}
        )


@dataclass
class VerifyServerReachableAction:
    """Verify spec server is reachable from VM."""
    name: str
    host_key: str = 'vm_ip'
    timeout: int = 30

    def run(self, config: HostConfig, context: dict) -> ActionResult:
        """Check that VM can reach the spec server."""
        start = time.time()

        host = context.get(self.host_key)
        spec_server = context.get('spec_server_url')

        if not host:
            return ActionResult(
                success=False,
                message=f"No {self.host_key} in context",
                duration=time.time() - start
            )

        if not spec_server:
            return ActionResult(
                success=False,
                message="No spec_server_url in context",
                duration=time.time() - start
            )

        # Curl the health endpoint (allow self-signed cert)
        cmd = f'curl -sk {spec_server}/health 2>&1 || echo "CURL_FAILED"'
        logger.info(f"[{self.name}] Testing connectivity to {spec_server} from {host}...")
        rc, out, err = run_ssh(host, cmd, user=config.automation_user, timeout=self.timeout)

        if 'CURL_FAILED' in out or rc != 0:
            return ActionResult(
                success=False,
                message=f"Cannot reach spec server from VM: {out}",
                duration=time.time() - start
            )

        # Check for expected health response
        if 'ok' in out.lower() or 'healthy' in out.lower() or '"status"' in out:
            return ActionResult(
                success=True,
                message=f"Spec server reachable: {out.strip()[:50]}",
                duration=time.time() - start
            )

        return ActionResult(
            success=True,
            message=f"Spec server responded: {out.strip()[:50]}",
            duration=time.time() - start
        )


@dataclass
class StopSpecServerAction:
    """Stop spec server on controller host."""
    name: str
    timeout: int = 30

    def run(self, config: HostConfig, context: dict) -> ActionResult:
        """Stop spec server."""
        start = time.time()

        pve_host = config.ssh_host
        ssh_user = config.ssh_user

        # Find PID from port and kill it
        find_cmd = 'ss -tlnp | grep ":44443 " | grep -oP "pid=\\K[0-9]+" || true'
        rc, out, err = run_ssh(pve_host, find_cmd, user=ssh_user, timeout=10)
        pid = out.strip()

        if pid:
            kill_cmd = f'kill {pid} 2>/dev/null || true'
            logger.info(f"[{self.name}] Stopping spec server (PID: {pid}) on {pve_host}...")
            run_ssh(pve_host, kill_cmd, user=ssh_user, timeout=self.timeout)
        else:
            logger.info(f"[{self.name}] No spec server found on port 44443")

        return ActionResult(
            success=True,
            message=f"Spec server stopped (PID: {pid})" if pid else "No spec server was running",
            duration=time.time() - start
        )


@register_scenario
class SpecVMRoundtrip:
    """Test Create → Specify flow: provision VM, verify spec server integration."""

    name = 'spec-vm-roundtrip'
    description = 'Deploy VM with spec server vars, verify env injection, destroy'
    expected_runtime = 180  # ~3 min

    def get_phases(self, config: HostConfig) -> list[tuple[str, object, str]]:
        """Return phases for spec VM roundtrip test."""
        return [
            # Prerequisites
            ('check_config', CheckSpecServerConfigAction(
                name='check-spec-config',
            ), 'Verify spec_server configured'),

            ('start_server', StartSpecServerAction(
                name='start-spec-server',
            ), 'Start spec discovery server'),

            # Standard VM provisioning
            ('ensure_image', EnsureImageAction(
                name='ensure-image',
            ), 'Ensure packer image exists'),

            ('provision', TofuApplyAction(
                name='provision-vm',
                env_name='test',
            ), 'Provision VM(s)'),

            ('start', StartProvisionedVMsAction(
                name='start-vms',
                pve_host_attr='ssh_host',
            ), 'Start VM(s)'),

            ('wait_ip', WaitForProvisionedVMsAction(
                name='wait-for-ips',
                pve_host_attr='ssh_host',
                timeout=180,
            ), 'Wait for VM IP(s)'),

            ('verify_ssh', WaitForSSHAction(
                name='verify-ssh',
                host_key='vm_ip',
                timeout=120,
            ), 'Verify SSH access'),

            # Spec-specific verification
            ('verify_env', VerifyEnvVarsAction(
                name='verify-env-vars',
                host_key='vm_ip',
            ), 'Verify HOMESTAK_* env vars'),

            ('verify_server', VerifyServerReachableAction(
                name='verify-server-reachable',
                host_key='vm_ip',
            ), 'Verify spec server reachable'),

            # Cleanup
            ('destroy', TofuDestroyAction(
                name='destroy-vm',
                env_name='test',
            ), 'Destroy VM(s)'),

            ('stop_server', StopSpecServerAction(
                name='stop-spec-server',
            ), 'Stop spec discovery server'),
        ]
