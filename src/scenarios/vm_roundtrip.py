"""Spec VM lifecycle scenarios.

Validates the Create → Specify integration: VMs provisioned via tofu
receive spec server environment variables via cloud-init.

Includes push (verify env vars) and pull (verify autonomous config) modes.
"""

import logging
import subprocess
import time
from dataclasses import dataclass
from typing import Optional

from actions import (
    TofuApplyAction,
    TofuDestroyAction,
    StartProvisionedVMsAction,
    WaitForProvisionedVMsAction,
    WaitForSSHAction,
    WaitForFileAction,
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
    serve_repos: bool = False
    repo_token: str | None = None  # None = don't pass flag, "" = disable auth

    def run(self, config: HostConfig, context: dict) -> ActionResult:
        """Start controller serve on PVE host via SSH."""
        start = time.time()

        pve_host = config.ssh_host
        ssh_user = config.ssh_user
        iac_dir = '/usr/local/lib/homestak/iac-driver'

        # Check if iac-driver exists on remote host
        check_cmd = f'test -f {iac_dir}/run.sh && echo FOUND || echo NOT_FOUND'
        rc, out, err = run_ssh(pve_host, check_cmd, user=ssh_user, timeout=10)
        if 'NOT_FOUND' in out:
            return ActionResult(
                success=False,
                message=f"iac-driver not found at {iac_dir}. Run bootstrap first.",
                duration=time.time() - start
            )

        # Check if already running by checking the port
        check_cmd = f'ss -tlnp | grep ":{self.server_port} " || true'
        rc, out, err = run_ssh(pve_host, check_cmd, user=ssh_user, timeout=10)
        if out.strip():
            # Port is bound — verify the controller is actually healthy (#176)
            health_cmd = f'curl -sk --connect-timeout 5 --max-time 5 https://localhost:{self.server_port}/health 2>&1 || echo HEALTH_FAILED'
            rc, health_out, _ = run_ssh(pve_host, health_cmd, user=ssh_user, timeout=10)
            if '"status"' in health_out and 'ok' in health_out.lower():
                logger.info(f"[{self.name}] Controller already running and healthy on port {self.server_port}")
                return ActionResult(
                    success=True,
                    message=f"Controller already running on port {self.server_port}",
                    duration=time.time() - start,
                )

            # Stale/hung controller — kill it and start fresh
            logger.warning(f"[{self.name}] Controller on port {self.server_port} is unresponsive, killing stale process")
            kill_cmd = f'ss -tlnp | grep ":{self.server_port} " | grep -oP "pid=\\K[0-9]+" | head -1'
            rc, pid_out, _ = run_ssh(pve_host, kill_cmd, user=ssh_user, timeout=10)
            stale_pid = pid_out.strip()
            if stale_pid:
                run_ssh(pve_host, f'kill -9 {stale_pid} 2>/dev/null; sleep 1', user=ssh_user, timeout=10)
                logger.info(f"[{self.name}] Killed stale controller (PID: {stale_pid})")

        # Start controller in background via iac-driver.
        # Write PID to file — SSH with capture_output blocks until all
        # inherited FDs close, even with & (#166). Use Popen to avoid
        # waiting for the SSH process to complete.
        serve_flags = f'--port {self.server_port}'
        if self.serve_repos:
            serve_flags += ' --repos'
            if self.repo_token is not None:
                serve_flags += f" --repo-token '{self.repo_token}'"
        pid_file = '/tmp/homestak-controller.pid'
        start_cmd = (
            f'cd {iac_dir} && '
            f'nohup ./run.sh serve {serve_flags} '
            f'> /tmp/homestak-controller.log 2>&1 </dev/null & '
            f'echo $! > {pid_file}'
        )
        logger.info(f"[{self.name}] Starting controller on {pve_host}:{self.server_port}...")
        ssh_opts = [
            '-o', 'StrictHostKeyChecking=no',
            '-o', 'UserKnownHostsFile=/dev/null',
            '-o', 'LogLevel=ERROR',
            '-o', 'ConnectTimeout=10',
        ]
        subprocess.Popen(
            ['ssh'] + ssh_opts + [f'{ssh_user}@{pve_host}', start_cmd],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL,
        )

        # Wait for controller to start and read PID
        time.sleep(3)
        rc, pid_out, _ = run_ssh(pve_host, f'cat {pid_file} 2>/dev/null', user=ssh_user, timeout=10)
        pid = pid_out.strip()
        if not pid:
            log_cmd = 'tail -20 /tmp/homestak-controller.log 2>/dev/null || true'
            _, log_out, _ = run_ssh(pve_host, log_cmd, user=ssh_user, timeout=10)
            return ActionResult(
                success=False,
                message=f"Controller PID file not written. Log: {log_out}",
                duration=time.time() - start
            )

        # Verify process is running
        verify_cmd = f'kill -0 {pid} 2>/dev/null && echo running || echo stopped'
        rc, out, err = run_ssh(pve_host, verify_cmd, user=ssh_user, timeout=10)

        if 'running' not in out:
            # Check log for errors
            log_cmd = 'tail -20 /tmp/homestak-controller.log 2>/dev/null || true'
            _, log_out, _ = run_ssh(pve_host, log_cmd, user=ssh_user, timeout=10)
            return ActionResult(
                success=False,
                message=f"Controller failed to start. Log: {log_out}",
                duration=time.time() - start
            )

        return ActionResult(
            success=True,
            message=f"Controller started (PID: {pid})",
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


@dataclass
class VerifyPackagesAction:
    """Verify expected packages are installed on a VM."""
    name: str
    packages: tuple
    host_key: str = 'vm_ip'
    timeout: int = 30

    def run(self, config: HostConfig, context: dict) -> ActionResult:
        """Check that packages are installed via dpkg."""
        start = time.time()

        host = context.get(self.host_key)
        if not host:
            return ActionResult(
                success=False,
                message=f"No {self.host_key} in context",
                duration=time.time() - start
            )

        missing = []
        for pkg in self.packages:
            cmd = f'dpkg -s {pkg} 2>/dev/null | grep -q "Status: install ok installed" && echo INSTALLED || echo MISSING'
            rc, out, _ = run_ssh(host, cmd, user=config.automation_user, timeout=self.timeout)
            if 'MISSING' in out or rc != 0:
                missing.append(pkg)

        if missing:
            return ActionResult(
                success=False,
                message=f"Packages not installed: {', '.join(missing)}",
                duration=time.time() - start
            )

        return ActionResult(
            success=True,
            message=f"All packages installed: {', '.join(self.packages)}",
            duration=time.time() - start
        )


@dataclass
class VerifyUserAction:
    """Verify expected user exists on a VM."""
    name: str
    username: str
    host_key: str = 'vm_ip'
    timeout: int = 30

    def run(self, config: HostConfig, context: dict) -> ActionResult:
        """Check that user exists via id command."""
        start = time.time()

        host = context.get(self.host_key)
        if not host:
            return ActionResult(
                success=False,
                message=f"No {self.host_key} in context",
                duration=time.time() - start
            )

        cmd = f'id {self.username} 2>/dev/null && echo USER_EXISTS || echo USER_MISSING'
        rc, out, _ = run_ssh(host, cmd, user=config.automation_user, timeout=self.timeout)

        if 'USER_MISSING' in out or rc != 0:
            return ActionResult(
                success=False,
                message=f"User '{self.username}' not found",
                duration=time.time() - start
            )

        return ActionResult(
            success=True,
            message=f"User '{self.username}' exists: {out.strip().splitlines()[0][:60]}",
            duration=time.time() - start
        )


@register_scenario
class SpecVMPushRoundtrip:
    """Test Create → Specify flow (push): provision VM, verify spec server integration."""

    name = 'push-vm-roundtrip'
    description = 'Deploy VM with spec server vars, verify env injection via SSH, destroy'
    expected_runtime = 180  # ~3 min

    def get_phases(self, config: HostConfig) -> list[tuple[str, object, str]]:
        """Return phases for spec VM push roundtrip test."""
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
                vm_name='test',
                vmid=99900,
                vm_preset='vm-small',
                image='debian-12',
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
                vm_name='test',
                vmid=99900,
                vm_preset='vm-small',
                image='debian-12',
            ), 'Destroy VM(s)'),

            ('stop_server', StopSpecServerAction(
                name='stop-spec-server',
            ), 'Stop spec discovery server'),
        ]


@register_scenario
class SpecVMPullRoundtrip:
    """Test Create → Config flow (pull): VM self-configures, driver verifies."""

    name = 'pull-vm-roundtrip'
    description = 'Deploy VM with pull mode, verify autonomous spec fetch + config apply, destroy'
    expected_runtime = 300  # ~5 min (includes waiting for cloud-init config)

    def get_phases(self, config: HostConfig) -> list[tuple[str, object, str]]:
        """Return phases for spec VM pull roundtrip test."""
        return [
            # Prerequisites
            ('check_config', CheckSpecServerConfigAction(
                name='check-spec-config',
            ), 'Verify spec_server configured'),

            ('start_server', StartSpecServerAction(
                name='start-spec-server',
                serve_repos=True,
                repo_token='',  # Disable auth for dev posture (network trust)
            ), 'Start spec + repo server'),

            # Standard VM provisioning
            ('ensure_image', EnsureImageAction(
                name='ensure-image',
            ), 'Ensure packer image exists'),

            ('provision', TofuApplyAction(
                name='provision-vm',
                vm_name='edge',
                vmid=99950,
                vm_preset='vm-small',
                image='debian-12',
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

            # Pull mode verification: VM autonomously fetches spec and applies config
            ('wait_spec', WaitForFileAction(
                name='wait-spec-file',
                host_key='vm_ip',
                file_path='/usr/local/etc/homestak/state/spec.yaml',
                timeout=150,
                interval=10,
            ), 'Wait for spec fetch (pull)'),

            ('wait_config', WaitForFileAction(
                name='wait-config-complete',
                host_key='vm_ip',
                file_path='/usr/local/etc/homestak/state/config-complete.json',
                timeout=180,
                interval=10,
            ), 'Wait for config complete (pull)'),

            # Verify config was applied correctly
            ('verify_packages', VerifyPackagesAction(
                name='verify-packages',
                host_key='vm_ip',
                packages=('htop', 'curl'),
            ), 'Verify packages installed'),

            ('verify_user', VerifyUserAction(
                name='verify-user',
                host_key='vm_ip',
                username='homestak',
            ), 'Verify user created'),

            # Cleanup
            ('destroy', TofuDestroyAction(
                name='destroy-vm',
                vm_name='edge',
                vmid=99950,
                vm_preset='vm-small',
                image='debian-12',
            ), 'Destroy VM(s)'),

            ('stop_server', StopSpecServerAction(
                name='stop-spec-server',
            ), 'Stop spec discovery server'),
        ]
