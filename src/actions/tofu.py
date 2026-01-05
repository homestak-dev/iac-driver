"""OpenTofu actions."""

import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from common import ActionResult, run_command
from config import HostConfig, get_sibling_dir

logger = logging.getLogger(__name__)


@dataclass
class TofuApplyAction:
    """Run tofu init and apply on an environment."""
    name: str
    env_path: str  # e.g., "envs/pve-deb"
    var_file: Optional[Path] = None
    timeout_init: int = 120
    timeout_apply: int = 600

    def run(self, config: HostConfig, context: dict) -> ActionResult:
        """Execute tofu init + apply."""
        start = time.time()

        tofu_dir = get_sibling_dir('tofu') / self.env_path
        if not tofu_dir.exists():
            return ActionResult(
                success=False,
                message=f"Tofu directory not found: {tofu_dir}",
                duration=time.time() - start
            )

        # Run tofu init
        logger.info(f"[{self.name}] Running tofu init...")
        rc, out, err = run_command(['tofu', 'init'], cwd=tofu_dir, timeout=self.timeout_init)
        if rc != 0:
            return ActionResult(
                success=False,
                message=f"tofu init failed: {err}",
                duration=time.time() - start
            )

        # Run tofu apply
        logger.info(f"[{self.name}] Running tofu apply...")
        cmd = ['tofu', 'apply', '-auto-approve']

        # Pass node name for config-loader module (YAML config)
        # or var-file for legacy tfvars
        if self.var_file and self.var_file.exists():
            cmd.extend(['-var-file', str(self.var_file)])
        elif config.config_file.suffix == '.yaml':
            # YAML config: pass node name and site-config path
            from config import get_site_config_dir
            cmd.extend(['-var', f'node={config.name}'])
            cmd.extend(['-var', f'site_config_path={get_site_config_dir()}'])
        elif config.config_file.exists():
            cmd.extend(['-var-file', str(config.config_file)])

        rc, out, err = run_command(cmd, cwd=tofu_dir, timeout=self.timeout_apply)
        if rc != 0:
            return ActionResult(
                success=False,
                message=f"tofu apply failed: {err}",
                duration=time.time() - start
            )

        return ActionResult(
            success=True,
            message=f"Tofu apply completed for {self.env_path}",
            duration=time.time() - start
        )


@dataclass
class TofuDestroyAction:
    """Run tofu destroy on an environment."""
    name: str
    env_path: str
    var_file: Optional[Path] = None
    timeout: int = 300

    def run(self, config: HostConfig, context: dict) -> ActionResult:
        """Execute tofu destroy."""
        start = time.time()

        tofu_dir = get_sibling_dir('tofu') / self.env_path
        if not tofu_dir.exists():
            return ActionResult(
                success=False,
                message=f"Tofu directory not found: {tofu_dir}",
                duration=time.time() - start
            )

        logger.info(f"[{self.name}] Running tofu destroy...")
        cmd = ['tofu', 'destroy', '-auto-approve']

        # Pass node name for config-loader module (YAML config)
        # or var-file for legacy tfvars
        if self.var_file and self.var_file.exists():
            cmd.extend(['-var-file', str(self.var_file)])
        elif config.config_file.suffix == '.yaml':
            # YAML config: pass node name and site-config path
            from config import get_site_config_dir
            cmd.extend(['-var', f'node={config.name}'])
            cmd.extend(['-var', f'site_config_path={get_site_config_dir()}'])
        elif config.config_file.exists():
            cmd.extend(['-var-file', str(config.config_file)])

        rc, out, err = run_command(cmd, cwd=tofu_dir, timeout=self.timeout)
        if rc != 0:
            return ActionResult(
                success=False,
                message=f"tofu destroy failed: {err}",
                duration=time.time() - start
            )

        return ActionResult(
            success=True,
            message=f"Tofu destroy completed for {self.env_path}",
            duration=time.time() - start
        )


@dataclass
class TofuApplyRemoteAction:
    """Run tofu init and apply on a remote host via SSH."""
    name: str
    remote_path: str  # e.g., "/root/tofu/envs/test"
    host_key: str = 'inner_ip'  # context key for target host
    timeout_init: int = 120
    timeout_apply: int = 300

    def run(self, config: HostConfig, context: dict) -> ActionResult:
        """Execute tofu init + apply on remote host."""
        from common import run_ssh
        start = time.time()

        host = context.get(self.host_key)
        if not host:
            return ActionResult(
                success=False,
                message=f"No {self.host_key} in context",
                duration=time.time() - start
            )

        # Run tofu init on remote
        logger.info(f"[{self.name}] Running tofu init on {host}...")
        rc, out, err = run_ssh(host, f'cd {self.remote_path} && tofu init', timeout=self.timeout_init)
        if rc != 0:
            return ActionResult(
                success=False,
                message=f"tofu init failed on {host}: {err}",
                duration=time.time() - start
            )

        # Run tofu apply on remote
        logger.info(f"[{self.name}] Running tofu apply on {host}...")
        rc, out, err = run_ssh(host, f'cd {self.remote_path} && tofu apply -auto-approve', timeout=self.timeout_apply)
        if rc != 0:
            return ActionResult(
                success=False,
                message=f"tofu apply failed on {host}: {err}",
                duration=time.time() - start
            )

        return ActionResult(
            success=True,
            message=f"Tofu apply completed on {host}",
            duration=time.time() - start
        )
