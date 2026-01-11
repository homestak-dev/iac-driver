"""OpenTofu actions using ConfigResolver."""

import logging
import os
import time
from dataclasses import dataclass
from pathlib import Path

from common import ActionResult, run_command
from config import HostConfig, get_sibling_dir, get_base_dir
from config_resolver import ConfigResolver

logger = logging.getLogger(__name__)


@dataclass
class TofuApplyAction:
    """Run tofu init and apply using ConfigResolver + envs/generic."""
    name: str
    env_name: str  # e.g., "test", "nested-pve"
    timeout_init: int = 120
    timeout_apply: int = 300

    def run(self, config: HostConfig, context: dict) -> ActionResult:
        """Execute tofu init + apply with resolved config."""
        start = time.time()

        # Use context env_name if provided (CLI override), otherwise use action default
        env_name = context.get('env_name', self.env_name)

        # Resolve config to tfvars
        try:
            resolver = ConfigResolver()
            resolved = resolver.resolve_env(env=env_name, node=config.name)

            # Apply VM ID overrides from context
            vm_id_overrides = context.get('vm_id_overrides', {})
            if vm_id_overrides:
                for vm in resolved.get('vms', []):
                    vm_name = vm.get('name')
                    if vm_name in vm_id_overrides:
                        original = vm.get('vmid')
                        vm['vmid'] = vm_id_overrides[vm_name]
                        logger.info(f"[{self.name}] VM ID override: {vm_name} {original} -> {vm['vmid']}")

            tfvars_path = Path(f'/tmp/{env_name}-{config.name}.tfvars.json')
            resolver.write_tfvars(resolved, str(tfvars_path))
            logger.info(f"[{self.name}] Generated tfvars: {tfvars_path}")
        except Exception as e:
            return ActionResult(
                success=False,
                message=f"ConfigResolver failed: {e}",
                duration=time.time() - start
            )

        # Always use generic env
        tofu_dir = get_sibling_dir('tofu') / 'envs' / 'generic'
        if not tofu_dir.exists():
            return ActionResult(
                success=False,
                message=f"Tofu directory not found: {tofu_dir}",
                duration=time.time() - start
            )

        # State isolation: each env+node gets its own state directory
        # States stored in iac-driver (orchestrator owns state, tofu is dumb executor)
        # IMPORTANT: TF_DATA_DIR must NOT contain terraform.tfstate, otherwise
        # OpenTofu's legacy code path reads it and rejects version 4 states.
        # We use a 'data/' subdirectory for TF_DATA_DIR (modules/providers).
        state_dir = get_base_dir() / '.states' / f'{env_name}-{config.name}'
        data_dir = state_dir / 'data'
        data_dir.mkdir(parents=True, exist_ok=True)
        state_file = state_dir / 'terraform.tfstate'
        env = {**os.environ, 'TF_DATA_DIR': str(data_dir)}

        # Run tofu init
        logger.info(f"[{self.name}] Running tofu init...")
        rc, out, err = run_command(['tofu', 'init'], cwd=tofu_dir, timeout=self.timeout_init, env=env)
        if rc != 0:
            return ActionResult(
                success=False,
                message=f"tofu init failed: {err}",
                duration=time.time() - start
            )

        # Run tofu apply with explicit state file
        logger.info(f"[{self.name}] Running tofu apply (state: {state_file})...")
        cmd = ['tofu', 'apply', '-auto-approve', f'-state={state_file}', f'-var-file={tfvars_path}']
        rc, out, err = run_command(cmd, cwd=tofu_dir, timeout=self.timeout_apply, env=env)
        if rc != 0:
            return ActionResult(
                success=False,
                message=f"tofu apply failed: {err}",
                duration=time.time() - start
            )

        # Extract VM IDs from resolved config for downstream actions
        context_updates = {}
        provisioned_vms = []
        for vm in resolved.get('vms', []):
            vm_name = vm.get('name')
            vmid = vm.get('vmid')
            if vm_name and vmid:
                # Add as {name}_vm_id (e.g., test_vm_id, inner_vm_id)
                context_updates[f'{vm_name}_vm_id'] = vmid
                provisioned_vms.append({'name': vm_name, 'vmid': vmid})

        # Add list of all provisioned VMs for multi-VM scenarios
        context_updates['provisioned_vms'] = provisioned_vms

        return ActionResult(
            success=True,
            message=f"Tofu apply completed for {env_name} on {config.name}",
            duration=time.time() - start,
            context_updates=context_updates
        )


@dataclass
class TofuDestroyAction:
    """Run tofu destroy using ConfigResolver + envs/generic."""
    name: str
    env_name: str
    timeout: int = 300

    def run(self, config: HostConfig, context: dict) -> ActionResult:
        """Execute tofu destroy with resolved config."""
        start = time.time()

        # Use context env_name if provided (CLI override), otherwise use action default
        env_name = context.get('env_name', self.env_name)

        # Resolve config to tfvars
        try:
            resolver = ConfigResolver()
            resolved = resolver.resolve_env(env=env_name, node=config.name)
            tfvars_path = Path(f'/tmp/{env_name}-{config.name}.tfvars.json')
            resolver.write_tfvars(resolved, str(tfvars_path))
        except Exception as e:
            return ActionResult(
                success=False,
                message=f"ConfigResolver failed: {e}",
                duration=time.time() - start
            )

        # Always use generic env
        tofu_dir = get_sibling_dir('tofu') / 'envs' / 'generic'
        if not tofu_dir.exists():
            return ActionResult(
                success=False,
                message=f"Tofu directory not found: {tofu_dir}",
                duration=time.time() - start
            )

        # State isolation: use same state directory layout as apply
        # States stored in iac-driver (orchestrator owns state)
        # TF_DATA_DIR points to data/ subdirectory (see TofuApplyAction comment)
        state_dir = get_base_dir() / '.states' / f'{env_name}-{config.name}'
        data_dir = state_dir / 'data'
        state_file = state_dir / 'terraform.tfstate'
        if not state_file.exists():
            return ActionResult(
                success=False,
                message=f"No state found for {env_name}-{config.name} at {state_file}",
                duration=time.time() - start
            )
        env = {**os.environ, 'TF_DATA_DIR': str(data_dir)}

        logger.info(f"[{self.name}] Running tofu destroy (state: {state_file})...")
        cmd = ['tofu', 'destroy', '-auto-approve', f'-state={state_file}', f'-var-file={tfvars_path}']
        rc, out, err = run_command(cmd, cwd=tofu_dir, timeout=self.timeout, env=env)
        if rc != 0:
            return ActionResult(
                success=False,
                message=f"tofu destroy failed: {err}",
                duration=time.time() - start
            )

        return ActionResult(
            success=True,
            message=f"Tofu destroy completed for {env_name} on {config.name}",
            duration=time.time() - start
        )


@dataclass
class TofuApplyRemoteAction:
    """Run ConfigResolver + tofu apply on a remote host via SSH."""
    name: str
    env_name: str  # e.g., "test"
    node_name: str = 'nested-pve'  # Node name on remote site-config
    host_key: str = 'inner_ip'  # context key for target host
    timeout_init: int = 120
    timeout_apply: int = 300

    def run(self, config: HostConfig, context: dict) -> ActionResult:
        """Execute ConfigResolver + tofu on remote host."""
        from common import run_ssh
        start = time.time()

        host = context.get(self.host_key)
        if not host:
            return ActionResult(
                success=False,
                message=f"No {self.host_key} in context",
                duration=time.time() - start
            )

        # Resolve config locally to extract VM IDs for context updates
        # (remote has the same site-config, so VM IDs will match)
        try:
            resolver = ConfigResolver()
            resolved = resolver.resolve_env(env=self.env_name, node=self.node_name)
        except Exception as e:
            logger.warning(f"[{self.name}] Could not resolve config locally: {e}")
            resolved = {}

        # Run ConfigResolver + tofu on remote host
        # The remote host has iac-driver, site-config, and tofu at /opt/homestak/
        # States stored in iac-driver (orchestrator owns state, tofu is dumb executor)
        # IMPORTANT: TF_DATA_DIR must NOT contain terraform.tfstate, otherwise
        # OpenTofu's legacy code path reads it and rejects version 4 states.
        state_dir = f'/opt/homestak/iac-driver/.states/{self.env_name}-{self.node_name}'
        data_dir = f'{state_dir}/data'
        state_file = f'{state_dir}/terraform.tfstate'
        remote_script = f'''
cd /opt/homestak/iac-driver/src
python3 -c "
from config_resolver import ConfigResolver
r = ConfigResolver('/opt/homestak/site-config')
config = r.resolve_env('{self.env_name}', '{self.node_name}')
r.write_tfvars(config, '/tmp/{self.env_name}.tfvars.json')
print('Generated tfvars for {self.env_name}')
"

cd /opt/homestak/tofu/envs/generic
export TF_DATA_DIR="{data_dir}"
mkdir -p "$TF_DATA_DIR"
mkdir -p "{state_dir}"
tofu init
tofu apply -auto-approve -state={state_file} -var-file=/tmp/{self.env_name}.tfvars.json
'''

        logger.info(f"[{self.name}] Running ConfigResolver + tofu on {host}...")
        rc, out, err = run_ssh(host, remote_script, timeout=self.timeout_init + self.timeout_apply)
        if rc != 0:
            return ActionResult(
                success=False,
                message=f"Remote tofu failed on {host}: {err}",
                duration=time.time() - start
            )

        # Extract VM IDs from locally resolved config for downstream actions
        context_updates = {}
        for vm in resolved.get('vms', []):
            vm_name = vm.get('name')
            vmid = vm.get('vmid')
            if vm_name and vmid:
                context_updates[f'{vm_name}_vm_id'] = vmid
                logger.debug(f"[{self.name}] Added {vm_name}_vm_id={vmid} to context")

        return ActionResult(
            success=True,
            message=f"Tofu apply completed on {host} for {self.env_name}",
            duration=time.time() - start,
            context_updates=context_updates
        )


@dataclass
class TofuDestroyRemoteAction:
    """Run ConfigResolver + tofu destroy on a remote host via SSH."""
    name: str
    env_name: str
    node_name: str = 'nested-pve'
    host_key: str = 'inner_ip'
    timeout: int = 300

    def run(self, config: HostConfig, context: dict) -> ActionResult:
        """Execute ConfigResolver + tofu destroy on remote host."""
        from common import run_ssh
        start = time.time()

        host = context.get(self.host_key)
        if not host:
            return ActionResult(
                success=False,
                message=f"No {self.host_key} in context",
                duration=time.time() - start
            )

        # Same directory layout as TofuApplyRemoteAction
        # States stored in iac-driver (orchestrator owns state)
        state_dir = f'/opt/homestak/iac-driver/.states/{self.env_name}-{self.node_name}'
        data_dir = f'{state_dir}/data'
        state_file = f'{state_dir}/terraform.tfstate'
        remote_script = f'''
cd /opt/homestak/iac-driver/src
python3 -c "
from config_resolver import ConfigResolver
r = ConfigResolver('/opt/homestak/site-config')
config = r.resolve_env('{self.env_name}', '{self.node_name}')
r.write_tfvars(config, '/tmp/{self.env_name}.tfvars.json')
"

cd /opt/homestak/tofu/envs/generic
export TF_DATA_DIR="{data_dir}"
tofu destroy -auto-approve -state={state_file} -var-file=/tmp/{self.env_name}.tfvars.json
'''

        logger.info(f"[{self.name}] Running tofu destroy on {host}...")
        rc, out, err = run_ssh(host, remote_script, timeout=self.timeout)
        if rc != 0:
            return ActionResult(
                success=False,
                message=f"Remote tofu destroy failed on {host}: {err}",
                duration=time.time() - start
            )

        return ActionResult(
            success=True,
            message=f"Tofu destroy completed on {host} for {self.env_name}",
            duration=time.time() - start
        )
