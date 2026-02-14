"""Node executor for manifest-based orchestration.

Walks the execution graph and executes per-node lifecycle operations
using existing tofu and proxmox actions.

Root nodes (depth 0) are executed locally. Children of PVE nodes are
delegated to the PVE node via SSH, where a new operator instance
handles them as its own root nodes.
"""

import json
import logging
import os
import shlex
import time
from dataclasses import dataclass, field
from typing import Optional

from common import ActionResult, run_ssh
from config import HostConfig
from manifest import Manifest
from manifest_opr.graph import ExecutionNode, ManifestGraph
from manifest_opr.state import ExecutionState

logger = logging.getLogger(__name__)

SERVER_PORT = 44443


@dataclass
class NodeExecutor:
    """Executes lifecycle operations on manifest graph nodes.

    Walks the graph in topological order, running create/destroy/test
    operations for each node using existing action classes.

    Only root nodes (depth 0) are handled locally. Children of PVE nodes
    are delegated via SSH to the PVE host using RecursiveScenarioAction.

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
    self_addr: Optional[str] = None
    _server_refs: int = field(default=0, init=False, repr=False)
    _started_server: bool = field(default=False, init=False, repr=False)

    def _ensure_server(self) -> None:
        """Ensure the spec server is running on the target host.

        Uses reference counting so nested calls (test → create → destroy)
        only start/stop once. First call starts if needed; subsequent calls
        just increment the ref count.
        """
        self._server_refs += 1
        if self._server_refs > 1:
            # Already ensured by an outer caller
            return

        host = self.config.ssh_host
        user = self.config.ssh_user

        # Check current status
        rc, stdout, _ = run_ssh(
            host, f'cd /usr/local/lib/homestak/iac-driver && ./run.sh server status --json --port {SERVER_PORT}',
            user=user, timeout=15,
        )

        if rc == 0:
            try:
                status = json.loads(stdout.strip())
                if status.get('running') and status.get('healthy'):
                    logger.info("Server already running on %s:%d (reusing)", host, SERVER_PORT)
                    self._started_server = False
                    self._set_source_env(host)
                    return
            except (json.JSONDecodeError, ValueError):
                pass

        # Start the server (with repo serving for pull mode bootstrap, no auth)
        logger.info("Starting server on %s:%d", host, SERVER_PORT)
        rc, stdout, stderr = run_ssh(
            host, f"cd /usr/local/lib/homestak/iac-driver && ./run.sh server start --port {SERVER_PORT} --repos --repo-token ''",
            user=user, timeout=30,
        )

        if rc != 0:
            logger.warning("Server start returned rc=%d: %s", rc, stderr.strip() or stdout.strip())
        else:
            logger.info("Server started on %s:%d", host, SERVER_PORT)

        self._started_server = True
        self._set_source_env(host)

    def _stop_server(self) -> None:
        """Stop the spec server if we started it.

        Only actually stops when the ref count reaches zero (outermost caller).
        Preserves user-managed servers (those we didn't start).
        Always cleans up HOMESTAK_SOURCE env vars when ref count reaches zero.
        """
        self._server_refs = max(0, self._server_refs - 1)
        if self._server_refs > 0:
            # Inner caller returning; outer caller will handle stop
            return

        # Clean up env vars regardless of whether we started the server
        self._clear_source_env()

        if not self._started_server:
            return

        host = self.config.ssh_host
        user = self.config.ssh_user

        logger.info("Stopping server on %s:%d", host, SERVER_PORT)
        rc, _, stderr = run_ssh(
            host, f'cd /usr/local/lib/homestak/iac-driver && ./run.sh server stop --port {SERVER_PORT}',
            user=user, timeout=15,
        )

        if rc != 0:
            logger.warning("Server stop returned rc=%d: %s", rc, stderr.strip())
        else:
            logger.info("Server stopped on %s:%d", host, SERVER_PORT)

        self._started_server = False

    @staticmethod
    def _detect_external_ip() -> Optional[str]:
        """Detect this machine's routable IP address.

        Uses a UDP socket connect to a public IP (no traffic sent) to
        determine which local interface the OS would route through.
        Falls back to None if detection fails or returns a non-routable address.
        """
        import socket
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
                s.connect(('198.51.100.1', 1))  # RFC 5737 TEST-NET-2, no traffic sent
                addr = s.getsockname()[0]
                if addr and addr not in ('0.0.0.0', '127.0.0.1'):
                    return addr
        except OSError:
            pass
        return None

    @staticmethod
    def _validate_addr(addr: str, source: str) -> str:
        """Validate an address provided for HOMESTAK_SOURCE.

        Args:
            addr: IP address or hostname to validate
            source: Description of where the address came from (for error messages)

        Returns:
            The validated address

        Raises:
            ValueError: If the address is empty or loopback
        """
        if not addr or not addr.strip():
            raise ValueError(f"{source} is empty")
        addr = addr.strip()
        if addr in ('localhost', '127.0.0.1'):
            raise ValueError(
                f"{source} resolved to loopback ({addr}); "
                "use --self-addr or HOMESTAK_SELF_ADDR to specify a routable address"
            )
        return addr

    def _resolve_self_addr(self) -> Optional[str]:
        """Resolve the routable address for this host.

        Priority order:
        1. --self-addr CLI argument (set during subtree delegation)
        2. HOMESTAK_SELF_ADDR environment variable (manual override)

        Returns:
            Routable address, or None if not explicitly provided
        """
        if self.self_addr:
            return self.self_addr
        env_addr = os.environ.get('HOMESTAK_SELF_ADDR')
        if env_addr:
            return env_addr.strip()
        return None

    def _set_source_env(self, host: str) -> None:
        """Set HOMESTAK_SOURCE env vars so downstream actions use serve-repos.

        Called after the server is confirmed running (started or reused).
        BootstrapAction and RecursiveScenarioAction read these env vars
        to propagate serve-repos to child hosts instead of falling back
        to GitHub master.

        When host is loopback (localhost/127.0.0.1), resolves to a routable
        address using (in priority order):
        1. --self-addr CLI arg or HOMESTAK_SELF_ADDR env var (#200)
        2. _detect_external_ip() (socket-based detection)
        3. Fails with clear error if no routable address can be determined

        Args:
            host: IP or hostname of the server (typically self.config.ssh_host)
        """
        addr = host
        if host in ('localhost', '127.0.0.1'):
            explicit = self._resolve_self_addr()
            if explicit:
                addr = self._validate_addr(explicit, '--self-addr / HOMESTAK_SELF_ADDR')
                logger.info(
                    "Using explicit address %s instead of loopback %s for HOMESTAK_SOURCE",
                    addr, host,
                )
            else:
                detected = self._detect_external_ip()
                if detected:
                    addr = detected
                    logger.warning(
                        "Auto-detected external IP %s for HOMESTAK_SOURCE "
                        "(override with --self-addr or HOMESTAK_SELF_ADDR if incorrect)",
                        addr,
                    )
                else:
                    logger.warning(
                        "Could not detect external IP; using loopback %s for HOMESTAK_SOURCE "
                        "(child VMs will not be able to reach this address — "
                        "use --self-addr or HOMESTAK_SELF_ADDR to set a routable address)",
                        host,
                    )
        os.environ['HOMESTAK_SOURCE'] = f'https://{addr}:{SERVER_PORT}'
        os.environ.setdefault('HOMESTAK_REF', '_working')
        logger.info(
            "Set HOMESTAK_SOURCE=https://%s:%d (ref=%s)",
            addr, SERVER_PORT, os.environ.get('HOMESTAK_REF'),
        )

    def _clear_source_env(self) -> None:
        """Clear HOMESTAK_SOURCE env vars set by _set_source_env."""
        for var in ('HOMESTAK_SOURCE', 'HOMESTAK_REF'):
            os.environ.pop(var, None)
        logger.debug("Cleared HOMESTAK_SOURCE env vars")

    def create(self, context: dict) -> tuple[bool, ExecutionState]:
        """Execute create lifecycle: provision root nodes, delegate subtrees.

        For each root node:
        1. Run tofu apply to provision the VM
        2. Start the VM
        3. Wait for guest agent / IP
        4. Wait for SSH
        5. If PVE type: run PVE lifecycle (bootstrap, secrets, bridge, etc.)
        6. If PVE type with children: delegate subtree via SSH

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

        # Ensure server is running for spec serving (pull mode, etc.)
        self._ensure_server()

        on_error = self.manifest.settings.on_error
        created_nodes: list[ExecutionNode] = []
        success = True

        try:
            for exec_node in self.graph.create_order():
                # Only handle root nodes locally; children are delegated
                if exec_node.depth > 0:
                    continue

                node_state = state.get_node(exec_node.name)
                node_state.start()

                result = self._create_node(exec_node, context)
                if result.success:
                    context.update(result.context_updates or {})
                    vm_id = result.context_updates.get(f'{exec_node.name}_vm_id')
                    ip = result.context_updates.get(f'{exec_node.name}_ip')
                    node_state.complete(vm_id=vm_id, ip=ip)
                    created_nodes.append(exec_node)
                    state.save()

                    # If PVE node with children: delegate subtree
                    if exec_node.manifest_node.type == 'pve' and exec_node.children:
                        delegate_result = self._delegate_subtree(exec_node, context, state)
                        if not delegate_result.success:
                            # Mark all descendant nodes as failed
                            for desc in self._get_descendants(exec_node):
                                desc_state = state.get_node(desc.name)
                                desc_state.fail(f"Delegation failed: {delegate_result.message}")
                            success = False
                            logger.error(f"Subtree delegation failed for '{exec_node.name}': {delegate_result.message}")
                            if on_error == 'stop':
                                break
                            if on_error == 'rollback':
                                self._rollback(created_nodes, context, state)
                                break
                        else:
                            # Update state and context from delegation result
                            context.update(delegate_result.context_updates or {})
                            for desc in self._get_descendants(exec_node):
                                desc_state = state.get_node(desc.name)
                                desc_vm_id = (delegate_result.context_updates or {}).get(f'{desc.name}_vm_id')
                                desc_ip = (delegate_result.context_updates or {}).get(f'{desc.name}_ip')
                                desc_state.complete(vm_id=desc_vm_id, ip=desc_ip)
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
        finally:
            self._stop_server()

        state.finish()
        state.save()
        return success, state

    def destroy(self, context: dict) -> tuple[bool, ExecutionState]:
        """Execute destroy lifecycle: delegate subtree destruction, then destroy roots.

        For root PVE nodes with children, delegates destruction to the inner
        PVE host first, then destroys the root node locally.

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

        # Ensure server is running (needed for subtree delegation)
        self._ensure_server()

        success = True

        try:
            # Process root nodes only; children are delegated
            for exec_node in self.graph.destroy_order():
                if exec_node.depth > 0:
                    continue

                # If PVE node with children: delegate subtree destruction first
                if exec_node.manifest_node.type == 'pve' and exec_node.children:
                    ip = context.get(f'{exec_node.name}_ip')
                    if ip:
                        delegate_result = self._delegate_subtree_destroy(exec_node, context)
                        if not delegate_result.success:
                            logger.error(f"Subtree destroy delegation failed for '{exec_node.name}': {delegate_result.message}")
                            success = False
                        else:
                            # Mark descendant nodes as destroyed
                            for desc in self._get_descendants(exec_node):
                                desc_state = state.get_node(desc.name) if desc.name in state.nodes else state.add_node(desc.name)
                                desc_state.mark_destroyed()
                    else:
                        logger.warning(f"No IP for PVE node '{exec_node.name}', skipping subtree delegation")

                # Now destroy the root node itself
                node_state = state.get_node(exec_node.name) if exec_node.name in state.nodes else state.add_node(exec_node.name)
                node_state.start()

                result = self._destroy_node(exec_node, context)
                if result.success:
                    node_state.mark_destroyed()
                else:
                    node_state.fail(result.message)
                    success = False
                    logger.error(f"Destroy failed for node '{exec_node.name}': {result.message}")
        finally:
            self._stop_server()

        state.finish()
        state.save()
        return success, state

    def test(self, context: dict) -> tuple[bool, ExecutionState]:
        """Execute test lifecycle: create, verify, destroy.

        Manages server lifecycle at the outer level so create/destroy
        don't each start/stop independently.

        Args:
            context: Shared execution context

        Returns:
            (success, execution_state) tuple
        """
        if not self.dry_run:
            self._ensure_server()

        try:
            # Create (server already running, create() will see it as healthy and reuse)
            create_ok, state = self.create(context)
            if not create_ok:
                if self.manifest.settings.cleanup_on_failure:
                    logger.info("Create failed, cleaning up...")
                    self.destroy(context)
                return False, state

            # Verify SSH on all created nodes
            verify_ok = self._verify_nodes(context, state)

            # Destroy (server still running, destroy() will reuse)
            destroy_ok, _ = self.destroy(context)

            return create_ok and verify_ok and destroy_ok, state
        finally:
            if not self.dry_run:
                self._stop_server()

    def _create_node(self, exec_node: ExecutionNode, context: dict) -> ActionResult:
        """Create a single node: provision, start, wait for IP/SSH, PVE lifecycle.

        For PVE-type nodes, runs the full PVE lifecycle after SSH is available:
        bootstrap, copy secrets, inject SSH keys, pve-setup, configure bridge,
        generate node config, create API token, inject self SSH key, download images.

        Returns ActionResult with context_updates containing {name}_vm_id and {name}_ip.
        """
        from actions.tofu import TofuApplyAction
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

        logger.info(f"[create] Provisioning node '{mn.name}' on {pve_host}")

        # 1. Tofu apply
        apply_action = TofuApplyAction(
            name=f'provision-{mn.name}',
            vm_name=mn.name,
            vmid=mn.vmid,
            vm_preset=mn.preset,
            image=mn.image,
            spec=mn.spec,
        )
        result = apply_action.run(self.config, context)
        if not result.success:
            return result

        context_updates = dict(result.context_updates or {})
        context.update(context_updates)

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
            ip_context_key=f'{mn.name}_ip',
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
                timeout=120,
            )
            ssh_result = ssh_action.run(self.config, context)
            if not ssh_result.success:
                return ActionResult(
                    success=False,
                    message=f"SSH wait failed for {mn.name}: {ssh_result.message}",
                    duration=time.time() - start,
                    context_updates=context_updates,
                )

        # 5. Post-SSH: PVE lifecycle, pull mode wait, or pass-through
        exec_mode = mn.execution_mode or self.manifest.execution_mode
        if mn.type == 'pve' and ip:
            # PVE lifecycle requires push: bootstrap install, secrets injection,
            # bridge config, API token creation, and image download are
            # multi-step orchestration steps that need the driver's active
            # participation. A single spec→ansible flow can't cover these.
            pve_result = self._run_pve_lifecycle(exec_node, ip, context)
            if not pve_result.success:
                return ActionResult(
                    success=False,
                    message=f"PVE lifecycle failed for {mn.name}: {pve_result.message}",
                    duration=time.time() - start,
                    context_updates=context_updates,
                )
        elif exec_mode == 'pull' and ip:
            # Pull mode: VM self-configures, poll for completion markers
            pull_result = self._wait_for_config_complete(exec_node, ip, context)
            if not pull_result.success:
                return ActionResult(
                    success=False,
                    message=f"Pull mode wait failed for {mn.name}: {pull_result.message}",
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

    def _run_pve_lifecycle(self, exec_node: ExecutionNode, ip: str, context: dict) -> ActionResult:
        """Run PVE lifecycle phases on a freshly provisioned PVE node.

        Phase sequence:
        1. Bootstrap (curl|bash installer)
        2. Copy secrets
        3. Inject outer host SSH key
        4. Copy SSH private key
        5. Run pve-setup post-scenario
        6. Configure vmbr0 bridge
        7. Generate node config
        8. Create API token
        9. Inject self SSH key
        10. Download packer images for children

        Args:
            exec_node: The PVE ExecutionNode
            ip: IP address of the PVE node
            context: Shared execution context

        Returns:
            ActionResult indicating overall success/failure
        """
        from actions.pve_lifecycle import (
            BootstrapAction,
            SyncDriverCodeAction,
            CopySecretsAction,
            InjectSSHKeyAction,
            CopySSHPrivateKeyAction,
            ConfigureNetworkBridgeAction,
            GenerateNodeConfigAction,
            CreateApiTokenAction,
            InjectSelfSSHKeyAction,
        )
        from actions.recursive import RecursiveScenarioAction
        from actions.file import DownloadGitHubReleaseAction
        from actions.pve_lifecycle import _image_to_asset_name

        mn = exec_node.manifest_node
        host_key = f'{mn.name}_ip'
        start = time.time()

        # Ensure IP is in context for actions that look it up by key
        context[host_key] = ip

        # Phase sequence: list of (name, action) tuples
        phases: list[tuple[str, object]] = []

        # 1. Bootstrap
        phases.append(('bootstrap', BootstrapAction(
            name=f'bootstrap-{mn.name}',
            host_attr=host_key,
            timeout=600,
        )))

        # 2. Sync iac-driver code (ensures delegation uses same code)
        phases.append(('sync_code', SyncDriverCodeAction(
            name=f'sync-{mn.name}',
            host_attr=host_key,
        )))

        # 3. Copy secrets
        phases.append(('copy_secrets', CopySecretsAction(
            name=f'secrets-{mn.name}',
            host_attr=host_key,
        )))

        # 3. Inject outer host SSH key
        phases.append(('inject_ssh_key', InjectSSHKeyAction(
            name=f'sshkey-{mn.name}',
            host_attr=host_key,
        )))

        # 4. Copy SSH private key
        phases.append(('copy_private_key', CopySSHPrivateKeyAction(
            name=f'privkey-{mn.name}',
            host_attr=host_key,
        )))

        # 5. Run pve-setup post-scenario
        phases.append(('post_scenario', RecursiveScenarioAction(
            name=f'post-{mn.name}',
            scenario_name='pve-setup',
            host_attr=host_key,
            scenario_args=['--local', '--skip-preflight'],
            timeout=1200,
        )))

        # 6. Configure vmbr0 bridge
        phases.append(('configure_bridge', ConfigureNetworkBridgeAction(
            name=f'network-{mn.name}',
            host_attr=host_key,
        )))

        # 7. Generate node config
        phases.append(('generate_node_config', GenerateNodeConfigAction(
            name=f'nodeconfig-{mn.name}',
            host_attr=host_key,
        )))

        # 8. Create API token
        phases.append(('create_api_token', CreateApiTokenAction(
            name=f'apitoken-{mn.name}',
            host_attr=host_key,
        )))

        # 9. Inject self SSH key
        phases.append(('inject_self_ssh_key', InjectSelfSSHKeyAction(
            name=f'selfsshkey-{mn.name}',
            host_attr=host_key,
        )))

        # 10. Download packer images for children
        for child in exec_node.children:
            child_image = child.manifest_node.image or 'debian-12'
            child_asset = _image_to_asset_name(child_image)
            phases.append((f'download_image_{child.name}', DownloadGitHubReleaseAction(
                name=f'download-image-{child.name}',
                asset_name=child_asset,
                dest_dir='/var/lib/vz/template/iso',
                host_key=host_key,
                rename_ext='.img',
                timeout=300,
            )))

        # Execute phases sequentially
        for phase_name, action in phases:
            logger.info(f"[pve-lifecycle] {mn.name}: {phase_name}")
            result = action.run(self.config, context)
            if not result.success:
                return ActionResult(
                    success=False,
                    message=f"PVE lifecycle phase '{phase_name}' failed: {result.message}",
                    duration=time.time() - start,
                )
            if result.context_updates:
                context.update(result.context_updates)

        return ActionResult(
            success=True,
            message=f"PVE lifecycle completed for {mn.name}",
            duration=time.time() - start,
        )

    def _wait_for_config_complete(
        self, exec_node: ExecutionNode, ip: str, context: dict, timeout: int = 300
    ) -> ActionResult:
        """Poll for spec fetch + config completion on a pull-mode node.

        Waits for two marker files:
        1. spec.yaml — indicates spec was fetched from server
        2. config-complete.json — indicates config phase completed

        Args:
            exec_node: The ExecutionNode being created
            ip: IP address of the node
            context: Shared execution context
            timeout: Max seconds to wait for each marker

        Returns:
            ActionResult indicating success/failure
        """
        from actions.ssh import WaitForFileAction

        mn = exec_node.manifest_node
        start = time.time()

        # Ensure IP is in context
        context[f'{mn.name}_ip'] = ip

        # 1. Wait for spec.yaml (fetched by cloud-init)
        wait_spec = WaitForFileAction(
            name=f'wait-spec-{mn.name}',
            host_key=f'{mn.name}_ip',
            file_path='/usr/local/etc/homestak/state/spec.yaml',
            timeout=timeout,
            interval=10,
        )
        spec_result = wait_spec.run(self.config, context)
        if not spec_result.success:
            return ActionResult(
                success=False,
                message=f"Spec not fetched on {mn.name}: {spec_result.message}",
                duration=time.time() - start,
            )

        # 2. Wait for config-complete marker (written by ./run.sh config)
        wait_config = WaitForFileAction(
            name=f'wait-config-{mn.name}',
            host_key=f'{mn.name}_ip',
            file_path='/usr/local/etc/homestak/state/config-complete.json',
            timeout=timeout,
            interval=10,
        )
        config_result = wait_config.run(self.config, context)
        if not config_result.success:
            return ActionResult(
                success=False,
                message=f"Config not complete on {mn.name}: {config_result.message}",
                duration=time.time() - start,
            )

        logger.info(f"[pull] Node '{mn.name}' self-configured successfully")
        return ActionResult(
            success=True,
            message=f"Pull mode config complete for {mn.name}",
            duration=time.time() - start,
        )

    def _delegate_subtree(self, exec_node: ExecutionNode, context: dict, state: ExecutionState) -> ActionResult:
        """Delegate creation of a PVE node's children to the PVE host.

        Extracts the subtree as a new manifest, SSHs to the PVE node, and
        runs './run.sh manifest apply --manifest-json <json> -H <hostname> --json-output'
        on the target host. Uses RecursiveScenarioAction for PTY streaming
        and JSON result parsing.

        Args:
            exec_node: The PVE ExecutionNode whose children to delegate
            context: Shared execution context
            state: Execution state for recording descendant status

        Returns:
            ActionResult with context_updates containing descendant IPs and VM IDs
        """
        from actions.recursive import RecursiveScenarioAction

        mn = exec_node.manifest_node
        ip = context.get(f'{mn.name}_ip')
        if not ip:
            return ActionResult(
                success=False,
                message=f"No IP for PVE node '{mn.name}' in context",
                duration=0,
            )

        start = time.time()

        # Extract subtree manifest
        subtree = self.graph.extract_subtree(mn.name)
        subtree_json = subtree.to_json()

        # Build context keys to extract from result
        descendants = self._get_descendants(exec_node)
        context_keys = []
        for desc in descendants:
            context_keys.append(f'{desc.name}_ip')
            context_keys.append(f'{desc.name}_vm_id')

        # Get the hostname of the PVE node (used as -H argument)
        # The host's node config is named after its hostname
        inner_hostname = mn.name

        # Build raw command for delegation
        # Pass --self-addr so the inner executor knows its routable address
        # for HOMESTAK_SOURCE (avoids localhost propagation, #200)
        raw_cmd = (
            f'cd /usr/local/lib/homestak/iac-driver && '
            f'sudo ./run.sh manifest apply '
            f'--manifest-json {shlex.quote(subtree_json)} '
            f'-H {shlex.quote(inner_hostname)} '
            f'--self-addr {shlex.quote(ip)} '
            f'--json-output'
        )

        logger.info(f"[delegate] Delegating subtree of '{mn.name}' ({len(descendants)} nodes)")

        action = RecursiveScenarioAction(
            name=f'delegate-{mn.name}',
            host_attr=f'{mn.name}_ip',
            raw_command=raw_cmd,
            context_keys=context_keys,
            timeout=1200,
            ssh_user=self.config.automation_user,
        )

        return action.run(self.config, context)

    def _delegate_subtree_destroy(self, exec_node: ExecutionNode, context: dict) -> ActionResult:
        """Delegate destruction of a PVE node's children to the PVE host.

        Args:
            exec_node: The PVE ExecutionNode whose children to destroy
            context: Shared execution context

        Returns:
            ActionResult indicating success/failure
        """
        from actions.recursive import RecursiveScenarioAction

        mn = exec_node.manifest_node
        ip = context.get(f'{mn.name}_ip')
        if not ip:
            return ActionResult(
                success=False,
                message=f"No IP for PVE node '{mn.name}' in context",
                duration=0,
            )

        start = time.time()

        # Extract subtree manifest
        subtree = self.graph.extract_subtree(mn.name)
        subtree_json = subtree.to_json()

        inner_hostname = mn.name

        raw_cmd = (
            f'cd /usr/local/lib/homestak/iac-driver && '
            f'sudo ./run.sh manifest destroy '
            f'--manifest-json {shlex.quote(subtree_json)} '
            f'-H {shlex.quote(inner_hostname)} '
            f'--self-addr {shlex.quote(ip)} '
            f'--json-output --yes'
        )

        logger.info(f"[delegate] Delegating subtree destroy for '{mn.name}'")

        action = RecursiveScenarioAction(
            name=f'delegate-destroy-{mn.name}',
            host_attr=f'{mn.name}_ip',
            raw_command=raw_cmd,
            context_keys=[],
            timeout=600,
            ssh_user=self.config.automation_user,
        )

        return action.run(self.config, context)

    def _get_descendants(self, exec_node: ExecutionNode) -> list[ExecutionNode]:
        """Get all descendants of a node via BFS."""
        from collections import deque
        descendants: list[ExecutionNode] = []
        queue: deque[ExecutionNode] = deque(exec_node.children)
        while queue:
            node = queue.popleft()
            descendants.append(node)
            queue.extend(node.children)
        return descendants

    def _destroy_node(self, exec_node: ExecutionNode, context: dict) -> ActionResult:
        """Destroy a single node via tofu destroy."""
        from actions.tofu import TofuDestroyAction

        mn = exec_node.manifest_node
        start = time.time()

        logger.info(f"[destroy] Destroying node '{mn.name}'")

        destroy_action = TofuDestroyAction(
            name=f'destroy-{mn.name}',
            vm_name=mn.name,
            vmid=mn.vmid,
            vm_preset=mn.preset,
            image=mn.image,
            spec=mn.spec,
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
            # If PVE node with children, delegate subtree destruction first
            if exec_node.manifest_node.type == 'pve' and exec_node.children:
                ip = context.get(f'{exec_node.name}_ip')
                if ip:
                    self._delegate_subtree_destroy(exec_node, context)

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
            mode = "local" if exec_node.depth == 0 else "delegated"
            print(f"  [{exec_node.depth}] {mn.name}: {mn.type}{parent_info} [{mode}]")
            print(f"      preset={mn.preset} image={mn.image} vmid={mn.vmid}")
            if mn.type == 'pve' and exec_node.children:
                children_names = ', '.join(c.name for c in exec_node.children)
                print(f"      delegates: {children_names}")
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
            mode = "local" if exec_node.depth == 0 else "delegated"
            print(f"  [{exec_node.depth}] {mn.name}: destroy [{mode}]")
        print("")
