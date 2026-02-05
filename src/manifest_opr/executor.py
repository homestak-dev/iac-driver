"""Node executor for manifest-based orchestration.

Walks the execution graph and executes per-node lifecycle operations
using existing tofu and proxmox actions.

Scope: Push execution mode only. Supports flat (depth=1) and tiered (depth=2).
Deeper nesting requires SSH-based remote execution (deferred to #145).
"""

import logging
import time
from dataclasses import dataclass
from typing import Optional

from common import ActionResult
from config import HostConfig
from manifest import Manifest
from manifest_opr.graph import ExecutionNode, ManifestGraph
from manifest_opr.state import ExecutionState

logger = logging.getLogger(__name__)


@dataclass
class NodeExecutor:
    """Executes lifecycle operations on manifest graph nodes.

    Walks the graph in topological order, running create/destroy/test
    operations for each node using existing action classes.

    Attributes:
        manifest: The v2 manifest defining the deployment
        graph: The execution graph built from the manifest
        config: Host configuration for the target PVE host
        dry_run: If True, preview operations without executing
        json_output: If True, emit structured JSON
    """
    manifest: Manifest
    graph: ManifestGraph
    config: HostConfig
    dry_run: bool = False
    json_output: bool = False

    def create(self, context: dict) -> tuple[bool, ExecutionState]:
        """Execute create lifecycle: provision nodes in topological order.

        For each node:
        1. Run tofu apply to provision the VM
        2. Start the VM
        3. Wait for guest agent / IP
        4. Wait for SSH
        5. If PVE type: run pve-setup post-scenario actions

        Args:
            context: Shared execution context

        Returns:
            (success, execution_state) tuple
        """
        state = ExecutionState(self.manifest.name, self.config.name)
        state.start()

        # Register all nodes
        for exec_node in self.graph.create_order():
            state.add_node(exec_node.name)

        if self.dry_run:
            self._preview_create()
            state.finish()
            return True, state

        on_error = self.manifest.settings.on_error
        created_nodes: list[ExecutionNode] = []
        success = True

        for exec_node in self.graph.create_order():
            node_state = state.get_node(exec_node.name)
            node_state.start()

            # Check depth limit: operator supports depth 0 and 1 (flat + tiered)
            if exec_node.depth > 1:
                msg = (
                    f"Node '{exec_node.name}' at depth {exec_node.depth} exceeds "
                    f"operator support (max depth=1). Use --scenario recursive-pve-* "
                    f"for deeper nesting."
                )
                logger.error(msg)
                node_state.fail(msg)
                success = False
                if on_error == 'stop':
                    break
                if on_error == 'rollback':
                    self._rollback(created_nodes, context, state)
                    break
                continue

            result = self._create_node(exec_node, context)
            if result.success:
                context.update(result.context_updates or {})
                vm_id = result.context_updates.get(f'{exec_node.name}_vm_id')
                ip = result.context_updates.get(f'{exec_node.name}_ip')
                node_state.complete(vm_id=vm_id, ip=ip)
                created_nodes.append(exec_node)
                state.save()
            else:
                node_state.fail(result.message)
                success = False
                logger.error(f"Create failed for node '{exec_node.name}': {result.message}")

                if on_error == 'stop':
                    break
                if on_error == 'rollback':
                    self._rollback(created_nodes, context, state)
                    break
                # on_error == 'continue': skip and continue

        state.finish()
        state.save()
        return success, state

    def destroy(self, context: dict) -> tuple[bool, ExecutionState]:
        """Execute destroy lifecycle: tear down nodes in reverse order.

        Args:
            context: Shared execution context (may contain IPs/IDs from create or loaded state)

        Returns:
            (success, execution_state) tuple
        """
        # Try to load existing state for IPs/IDs
        state = self._load_or_create_state()
        state.start()

        # Merge state context into context (so destroy can find IPs)
        context.update(state.to_context())

        if self.dry_run:
            self._preview_destroy()
            state.finish()
            return True, state

        success = True

        for exec_node in self.graph.destroy_order():
            if exec_node.depth > 1:
                logger.warning(f"Skipping node '{exec_node.name}' at depth {exec_node.depth} (operator limit)")
                continue

            node_state = state.get_node(exec_node.name) if exec_node.name in state.nodes else state.add_node(exec_node.name)
            node_state.start()

            result = self._destroy_node(exec_node, context)
            if result.success:
                node_state.mark_destroyed()
            else:
                node_state.fail(result.message)
                success = False
                logger.error(f"Destroy failed for node '{exec_node.name}': {result.message}")

        state.finish()
        state.save()
        return success, state

    def test(self, context: dict) -> tuple[bool, ExecutionState]:
        """Execute test lifecycle: create, verify, destroy.

        Args:
            context: Shared execution context

        Returns:
            (success, execution_state) tuple
        """
        # Create
        create_ok, state = self.create(context)
        if not create_ok:
            if self.manifest.settings.cleanup_on_failure:
                logger.info("Create failed, cleaning up...")
                self.destroy(context)
            return False, state

        # Verify SSH on all created nodes
        verify_ok = self._verify_nodes(context, state)

        # Destroy
        destroy_ok, _ = self.destroy(context)

        return create_ok and verify_ok and destroy_ok, state

    def _create_node(self, exec_node: ExecutionNode, context: dict) -> ActionResult:
        """Create a single node (provision, start, wait for IP, wait for SSH).

        Returns ActionResult with context_updates containing {name}_vm_id and {name}_ip.
        """
        from actions.tofu import TofuApplyInlineAction
        from actions.proxmox import StartVMAction, WaitForGuestAgentAction
        from actions.ssh import WaitForSSHAction

        mn = exec_node.manifest_node
        start = time.time()

        # Determine PVE host for this node
        if exec_node.is_root:
            pve_host = self.config.ssh_host
        else:
            parent_ip = context.get(f'{exec_node.parent.name}_ip')
            if not parent_ip:
                return ActionResult(
                    success=False,
                    message=f"Parent '{exec_node.parent.name}' IP not in context",
                    duration=time.time() - start,
                )
            pve_host = parent_ip

        # Strip vm- prefix from preset for v1 compat
        vm_preset = mn.preset
        if vm_preset and vm_preset.startswith('vm-'):
            vm_preset = vm_preset[3:]

        logger.info(f"[create] Provisioning node '{mn.name}' on {pve_host}")

        # 1. Tofu apply
        apply_action = TofuApplyInlineAction(
            name=f'provision-{mn.name}',
            vm_name=mn.name,
            vmid=mn.vmid,
            vm_preset=vm_preset,
            image=mn.image,
        )
        result = apply_action.run(self.config, context)
        if not result.success:
            return result

        context_updates = dict(result.context_updates or {})

        # 2. Start VM
        start_action = StartVMAction(
            name=f'start-{mn.name}',
            vm_id_attr=f'{mn.name}_vm_id',
            pve_host_attr='ssh_host' if exec_node.is_root else None,
        )
        # For non-root nodes, we need to set the PVE host in context
        if not exec_node.is_root:
            context[f'_pve_host_{mn.name}'] = pve_host

        start_result = start_action.run(self.config, context)
        if not start_result.success:
            return ActionResult(
                success=False,
                message=f"Start VM failed for {mn.name}: {start_result.message}",
                duration=time.time() - start,
                context_updates=context_updates,
            )

        # 3. Wait for guest agent / IP
        wait_action = WaitForGuestAgentAction(
            name=f'wait-ip-{mn.name}',
            vm_id_attr=f'{mn.name}_vm_id',
            pve_host_attr='ssh_host' if exec_node.is_root else None,
            timeout=300,
        )
        wait_result = wait_action.run(self.config, context)
        if not wait_result.success:
            return ActionResult(
                success=False,
                message=f"Wait for IP failed for {mn.name}: {wait_result.message}",
                duration=time.time() - start,
                context_updates=context_updates,
            )

        context.update(wait_result.context_updates or {})
        context_updates.update(wait_result.context_updates or {})

        # Extract IP
        ip = context.get(f'{mn.name}_ip') or context.get('vm_ip')
        if ip:
            context_updates[f'{mn.name}_ip'] = ip

        # 4. Wait for SSH
        if self.manifest.settings.verify_ssh and ip:
            # Ensure IP is in context under the key WaitForSSHAction expects
            context[f'{mn.name}_ip'] = ip
            ssh_action = WaitForSSHAction(
                name=f'wait-ssh-{mn.name}',
                host_key=f'{mn.name}_ip',
                timeout=60,
            )
            ssh_result = ssh_action.run(self.config, context)
            if not ssh_result.success:
                return ActionResult(
                    success=False,
                    message=f"SSH wait failed for {mn.name}: {ssh_result.message}",
                    duration=time.time() - start,
                    context_updates=context_updates,
                )

        logger.info(f"[create] Node '{mn.name}' created successfully (ip={ip})")

        return ActionResult(
            success=True,
            message=f"Node {mn.name} created on {pve_host}",
            duration=time.time() - start,
            context_updates=context_updates,
        )

    def _destroy_node(self, exec_node: ExecutionNode, context: dict) -> ActionResult:
        """Destroy a single node via tofu destroy."""
        from actions.tofu import TofuDestroyInlineAction

        mn = exec_node.manifest_node
        start = time.time()

        # Strip vm- prefix from preset
        vm_preset = mn.preset
        if vm_preset and vm_preset.startswith('vm-'):
            vm_preset = vm_preset[3:]

        logger.info(f"[destroy] Destroying node '{mn.name}'")

        destroy_action = TofuDestroyInlineAction(
            name=f'destroy-{mn.name}',
            vm_name=mn.name,
            vmid=mn.vmid,
            vm_preset=vm_preset,
            image=mn.image,
        )
        return destroy_action.run(self.config, context)

    def _verify_nodes(self, context: dict, state: ExecutionState) -> bool:
        """Verify SSH connectivity to all completed nodes."""
        from actions.ssh import WaitForSSHAction

        if not self.manifest.settings.verify_ssh:
            return True

        all_ok = True
        for name, node_state in state.nodes.items():
            if node_state.status != 'completed':
                continue
            ip = node_state.ip or context.get(f'{name}_ip')
            if not ip:
                logger.warning(f"No IP for node '{name}', skipping verify")
                continue

            # Ensure IP is in context under the key WaitForSSHAction expects
            context[f'{name}_ip'] = ip
            ssh_action = WaitForSSHAction(
                name=f'verify-ssh-{name}',
                host_key=f'{name}_ip',
                timeout=30,
            )
            result = ssh_action.run(self.config, context)
            if not result.success:
                logger.error(f"SSH verify failed for {name} ({ip})")
                all_ok = False

        return all_ok

    def _rollback(
        self,
        created_nodes: list[ExecutionNode],
        context: dict,
        state: ExecutionState,
    ) -> None:
        """Roll back created nodes in reverse order."""
        logger.info(f"Rolling back {len(created_nodes)} created nodes...")
        for exec_node in reversed(created_nodes):
            result = self._destroy_node(exec_node, context)
            node_state = state.get_node(exec_node.name)
            if result.success:
                node_state.mark_destroyed()
            else:
                logger.error(f"Rollback destroy failed for {exec_node.name}: {result.message}")

    def _load_or_create_state(self) -> ExecutionState:
        """Try to load existing state; create fresh if not found."""
        try:
            return ExecutionState.load(self.manifest.name, self.config.name)
        except FileNotFoundError:
            state = ExecutionState(self.manifest.name, self.config.name)
            for exec_node in self.graph.create_order():
                state.add_node(exec_node.name)
            return state

    def _preview_create(self) -> None:
        """Preview create operations."""
        print("")
        print("=" * 65)
        print(f"  DRY-RUN CREATE: {self.manifest.name}")
        print(f"  Host: {self.config.name}")
        print(f"  Pattern: {self.manifest.pattern or 'flat'}")
        print("=" * 65)
        print("")
        for exec_node in self.graph.create_order():
            mn = exec_node.manifest_node
            parent_info = f" (parent: {mn.parent})" if mn.parent else " (root)"
            print(f"  [{exec_node.depth}] {mn.name}: {mn.type}{parent_info}")
            print(f"      preset={mn.preset} image={mn.image} vmid={mn.vmid}")
        print("")

    def _preview_destroy(self) -> None:
        """Preview destroy operations."""
        print("")
        print("=" * 65)
        print(f"  DRY-RUN DESTROY: {self.manifest.name}")
        print(f"  Host: {self.config.name}")
        print("=" * 65)
        print("")
        for exec_node in self.graph.destroy_order():
            mn = exec_node.manifest_node
            print(f"  [{exec_node.depth}] {mn.name}: destroy")
        print("")
