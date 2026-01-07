"""Bootstrap install scenario.

Installs homestak on a target VM and verifies the installation.
Designed to run after simple-vm-constructor has created a VM.
"""

import time
import logging
from dataclasses import dataclass
from typing import Optional

from common import ActionResult, run_ssh
from config import HostConfig
from scenarios import register_scenario

logger = logging.getLogger(__name__)

BOOTSTRAP_URL = 'https://raw.githubusercontent.com/homestak-dev/bootstrap/master/install.sh'


@dataclass
class RunBootstrapAction:
    """Run homestak bootstrap on target VM."""
    name: str
    host_key: str = 'vm_ip'
    user: str = 'root'
    branch: Optional[str] = None
    homestak_user: Optional[str] = None
    timeout: int = 600

    def run(self, config: HostConfig, context: dict) -> ActionResult:
        """Run bootstrap script via SSH."""
        start = time.time()

        host = context.get(self.host_key)
        if not host:
            return ActionResult(
                success=False,
                message=f"No {self.host_key} in context",
                duration=time.time() - start
            )

        # Check context for overrides
        branch = context.get('bootstrap_branch', self.branch)
        homestak_user = context.get('homestak_user', self.homestak_user)

        # Build environment variables
        env_vars = []
        if branch:
            env_vars.append(f'HOMESTAK_BRANCH={branch}')
        if homestak_user:
            env_vars.append(f'HOMESTAK_USER={homestak_user}')

        env_prefix = ' '.join(env_vars) + ' ' if env_vars else ''

        # Build bootstrap command
        cmd = f'curl -fsSL {BOOTSTRAP_URL} | {env_prefix}bash'

        logger.info(f"[{self.name}] Running bootstrap on {host}...")
        if env_vars:
            logger.info(f"[{self.name}] Environment: {' '.join(env_vars)}")

        rc, out, err = run_ssh(host, cmd, user=self.user, timeout=self.timeout)

        if rc != 0:
            return ActionResult(
                success=False,
                message=f"Bootstrap failed: {err}",
                duration=time.time() - start
            )

        # Store homestak_user in context for verify_user phase
        context_updates = {}
        if homestak_user:
            context_updates['homestak_user'] = homestak_user

        return ActionResult(
            success=True,
            message="Bootstrap completed successfully",
            duration=time.time() - start,
            context_updates=context_updates
        )


@dataclass
class VerifyInstallAction:
    """Verify homestak installation via 'homestak status'."""
    name: str
    host_key: str = 'vm_ip'
    user: str = 'root'
    timeout: int = 30

    def run(self, config: HostConfig, context: dict) -> ActionResult:
        """Verify installation by running homestak status."""
        start = time.time()

        host = context.get(self.host_key)
        if not host:
            return ActionResult(
                success=False,
                message=f"No {self.host_key} in context",
                duration=time.time() - start
            )

        logger.info(f"[{self.name}] Verifying installation on {host}...")

        rc, out, err = run_ssh(host, 'homestak status', user=self.user, timeout=self.timeout)

        if rc != 0:
            return ActionResult(
                success=False,
                message=f"homestak status failed: {err}",
                duration=time.time() - start
            )

        # Check for expected modules
        required_modules = ['ansible', 'iac-driver', 'tofu']
        missing = []
        for module in required_modules:
            if module not in out or '(not installed)' in out.split(module)[1].split('\n')[0]:
                missing.append(module)

        if missing:
            return ActionResult(
                success=False,
                message=f"Missing modules: {', '.join(missing)}",
                duration=time.time() - start
            )

        # Check for tools (look in the Tools section for "not installed")
        tools_section = out.split('Tools:')[1] if 'Tools:' in out else ''
        if 'ansible' in tools_section and 'not installed' in tools_section.split('ansible')[1].split('\n')[0]:
            logger.warning("ansible tool may not be installed")

        logger.info(f"[{self.name}] Installation verified")
        return ActionResult(
            success=True,
            message="Installation verified: all modules present",
            duration=time.time() - start,
            context_updates={'homestak_status': out}
        )


@dataclass
class VerifyUserAction:
    """Verify homestak user was created (if HOMESTAK_USER was set)."""
    name: str
    host_key: str = 'vm_ip'
    user: str = 'root'
    timeout: int = 30

    def run(self, config: HostConfig, context: dict) -> ActionResult:
        """Verify user exists and has sudo access."""
        start = time.time()

        host = context.get(self.host_key)
        homestak_user = context.get('homestak_user')

        if not host:
            return ActionResult(
                success=False,
                message=f"No {self.host_key} in context",
                duration=time.time() - start
            )

        if not homestak_user:
            logger.info(f"[{self.name}] No HOMESTAK_USER set, skipping user verification")
            return ActionResult(
                success=True,
                message="Skipped: no HOMESTAK_USER set",
                duration=time.time() - start
            )

        logger.info(f"[{self.name}] Verifying user '{homestak_user}' on {host}...")

        # Check user exists
        rc, out, err = run_ssh(host, f'id {homestak_user}', user=self.user, timeout=self.timeout)
        if rc != 0:
            return ActionResult(
                success=False,
                message=f"User {homestak_user} not found: {err}",
                duration=time.time() - start
            )

        # Check sudo access
        rc, out, err = run_ssh(
            host,
            f'grep -r {homestak_user} /etc/sudoers.d/ 2>/dev/null | grep NOPASSWD',
            user=self.user,
            timeout=self.timeout
        )
        if rc != 0:
            return ActionResult(
                success=False,
                message=f"User {homestak_user} does not have passwordless sudo",
                duration=time.time() - start
            )

        logger.info(f"[{self.name}] User '{homestak_user}' verified with sudo access")
        return ActionResult(
            success=True,
            message=f"User {homestak_user} exists with passwordless sudo",
            duration=time.time() - start
        )


@register_scenario
class BootstrapInstall:
    """Install homestak on a target VM."""

    name = 'bootstrap-install'
    description = 'Run bootstrap, verify installation and user (requires vm_ip in context)'
    expected_runtime = 120  # ~2 min

    def get_phases(self, config: HostConfig) -> list[tuple[str, object, str]]:
        """Return phases for bootstrap installation."""
        return [
            ('run_bootstrap', RunBootstrapAction(
                name='run-bootstrap',
            ), 'Run bootstrap script'),

            ('verify_install', VerifyInstallAction(
                name='verify-install',
            ), 'Verify homestak installation'),

            ('verify_user', VerifyUserAction(
                name='verify-user',
            ), 'Verify user creation (if HOMESTAK_USER set)'),
        ]
