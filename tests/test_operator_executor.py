"""Tests for manifest_opr.executor module.

Uses mocked action classes to test execution ordering, error handling,
and dry-run behavior without real infrastructure.
"""

import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

# Add src to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent / 'src'))

from common import ActionResult
from manifest import Manifest
from manifest_opr.executor import NodeExecutor
from manifest_opr.graph import ManifestGraph


def _make_manifest(nodes_data, name='test', pattern='flat', on_error='stop'):
    """Helper to create a v2 manifest from node dicts."""
    return Manifest.from_dict({
        'schema_version': 2,
        'name': name,
        'pattern': pattern,
        'nodes': nodes_data,
        'settings': {'on_error': on_error, 'verify_ssh': False},
    })


def _make_config():
    """Create a mock HostConfig."""
    config = MagicMock()
    config.name = 'test-host'
    config.ssh_host = '10.0.12.61'
    config.ssh_user = 'root'
    config.automation_user = 'homestak'
    return config


def _success_result(**ctx):
    """Create a successful ActionResult with optional context updates."""
    return ActionResult(success=True, message='ok', duration=0.1, context_updates=ctx)


def _fail_result(msg='failed'):
    """Create a failed ActionResult."""
    return ActionResult(success=False, message=msg, duration=0.1)


@pytest.fixture(autouse=True)
def _skip_server(monkeypatch):
    """Prevent real SSH calls to start/stop the spec server in unit tests."""
    monkeypatch.setattr(
        'manifest_opr.executor.NodeExecutor._ensure_server', lambda self: None,
    )
    monkeypatch.setattr(
        'manifest_opr.executor.NodeExecutor._stop_server', lambda self: None,
    )


class TestNodeExecutorDryRun:
    """Tests for dry-run (preview) mode."""

    def test_create_dry_run(self, capsys):
        manifest = _make_manifest([
            {'name': 'test', 'type': 'vm', 'vmid': 99001, 'image': 'debian-12', 'preset': 'vm-small'},
        ])
        graph = ManifestGraph(manifest)
        config = _make_config()

        executor = NodeExecutor(
            manifest=manifest, graph=graph, config=config, dry_run=True,
        )
        success, state = executor.create({})

        assert success is True
        captured = capsys.readouterr()
        assert 'DRY-RUN CREATE' in captured.out
        assert 'test' in captured.out

    def test_destroy_dry_run(self, capsys):
        manifest = _make_manifest([
            {'name': 'test', 'type': 'vm', 'vmid': 99001, 'image': 'debian-12', 'preset': 'vm-small'},
        ])
        graph = ManifestGraph(manifest)
        config = _make_config()

        executor = NodeExecutor(
            manifest=manifest, graph=graph, config=config, dry_run=True,
        )
        success, state = executor.destroy({})

        assert success is True
        captured = capsys.readouterr()
        assert 'DRY-RUN DESTROY' in captured.out


class TestNodeExecutorCreate:
    """Tests for create lifecycle with mocked actions."""

    @patch('manifest_opr.executor.NodeExecutor._create_node')
    def test_single_node_create(self, mock_create):
        manifest = _make_manifest([
            {'name': 'test', 'type': 'vm', 'vmid': 99001, 'image': 'debian-12', 'preset': 'vm-small'},
        ])
        graph = ManifestGraph(manifest)
        config = _make_config()

        mock_create.return_value = _success_result(test_vm_id=99001, test_ip='10.0.12.100')

        executor = NodeExecutor(manifest=manifest, graph=graph, config=config)
        success, state = executor.create({})

        assert success is True
        assert mock_create.call_count == 1
        assert state.get_node('test').status == 'completed'

    @patch('manifest_opr.executor.NodeExecutor._delegate_subtree')
    @patch('manifest_opr.executor.NodeExecutor._create_node')
    def test_tiered_create_delegates_children(self, mock_create, mock_delegate):
        manifest = _make_manifest([
            {'name': 'pve', 'type': 'pve', 'vmid': 99001, 'image': 'debian-13-pve', 'preset': 'vm-large'},
            {'name': 'test', 'type': 'vm', 'vmid': 99002, 'image': 'debian-12', 'preset': 'vm-small', 'parent': 'pve'},
        ], pattern='tiered')
        graph = ManifestGraph(manifest)
        config = _make_config()

        mock_create.return_value = _success_result(pve_vm_id=99001, pve_ip='10.0.12.100')
        mock_delegate.return_value = _success_result(test_vm_id=99002, test_ip='10.0.12.101')

        executor = NodeExecutor(manifest=manifest, graph=graph, config=config)
        success, state = executor.create({})

        assert success is True
        assert mock_create.call_count == 1  # Only root (pve) created locally
        assert mock_delegate.call_count == 1  # Children delegated

    @patch('manifest_opr.executor.NodeExecutor._create_node')
    def test_create_stop_on_root_failure(self, mock_create):
        """When root node fails, stop immediately (children never attempted)."""
        manifest = _make_manifest([
            {'name': 'pve', 'type': 'pve', 'vmid': 99001, 'image': 'debian-13-pve', 'preset': 'vm-large'},
            {'name': 'test', 'type': 'vm', 'vmid': 99002, 'image': 'debian-12', 'preset': 'vm-small', 'parent': 'pve'},
        ], pattern='tiered', on_error='stop')
        graph = ManifestGraph(manifest)
        config = _make_config()

        mock_create.return_value = _fail_result('tofu apply failed')

        executor = NodeExecutor(manifest=manifest, graph=graph, config=config)
        success, state = executor.create({})

        assert success is False
        assert mock_create.call_count == 1  # Only root attempted
        assert state.get_node('pve').status == 'failed'
        assert state.get_node('test').status == 'pending'  # Never attempted (delegation skipped)

    @patch('manifest_opr.executor.NodeExecutor._delegate_subtree_destroy')
    @patch('manifest_opr.executor.NodeExecutor._delegate_subtree')
    @patch('manifest_opr.executor.NodeExecutor._destroy_node')
    @patch('manifest_opr.executor.NodeExecutor._create_node')
    def test_create_rollback_on_delegation_failure(self, mock_create, mock_destroy, mock_delegate, mock_delegate_destroy):
        """When subtree delegation fails with on_error=rollback, root node should be rolled back."""
        manifest = _make_manifest([
            {'name': 'pve', 'type': 'pve', 'vmid': 99001, 'image': 'debian-13-pve', 'preset': 'vm-large'},
            {'name': 'test', 'type': 'vm', 'vmid': 99002, 'image': 'debian-12', 'preset': 'vm-small', 'parent': 'pve'},
        ], pattern='tiered', on_error='rollback')
        graph = ManifestGraph(manifest)
        config = _make_config()

        mock_create.return_value = _success_result(pve_vm_id=99001, pve_ip='10.0.12.100')
        mock_delegate.return_value = _fail_result('delegation failed')
        mock_destroy.return_value = _success_result()
        mock_delegate_destroy.return_value = _success_result()

        executor = NodeExecutor(manifest=manifest, graph=graph, config=config)
        success, state = executor.create({})

        assert success is False
        assert mock_destroy.call_count == 1  # Rolled back pve
        assert state.get_node('pve').status == 'destroyed'

    @patch('manifest_opr.executor.NodeExecutor._destroy_node')
    @patch('manifest_opr.executor.NodeExecutor._create_node')
    def test_create_rollback_flat_failure(self, mock_create, mock_destroy):
        """Flat manifest rollback: first VM succeeds, second fails, first rolled back."""
        manifest = _make_manifest([
            {'name': 'vm1', 'type': 'vm', 'vmid': 99001, 'image': 'debian-12', 'preset': 'vm-small'},
            {'name': 'vm2', 'type': 'vm', 'vmid': 99002, 'image': 'debian-12', 'preset': 'vm-small'},
        ], on_error='rollback')
        graph = ManifestGraph(manifest)
        config = _make_config()

        call_count = [0]

        def create_side_effect(exec_node, context):
            call_count[0] += 1
            if call_count[0] == 1:
                return _success_result(vm1_vm_id=99001, vm1_ip='10.0.12.100')
            return _fail_result('provision failed')

        mock_create.side_effect = create_side_effect
        mock_destroy.return_value = _success_result()

        executor = NodeExecutor(manifest=manifest, graph=graph, config=config)
        success, state = executor.create({})

        assert success is False
        assert mock_destroy.call_count == 1  # Rolled back vm1
        assert state.get_node('vm1').status == 'destroyed'

    @patch('manifest_opr.executor.NodeExecutor._create_node')
    def test_create_continue_on_failure(self, mock_create):
        manifest = _make_manifest([
            {'name': 'vm1', 'type': 'vm', 'vmid': 99001, 'image': 'debian-12', 'preset': 'vm-small'},
            {'name': 'vm2', 'type': 'vm', 'vmid': 99002, 'image': 'debian-12', 'preset': 'vm-small'},
        ], on_error='continue')
        graph = ManifestGraph(manifest)
        config = _make_config()

        call_count = [0]

        def side_effect(exec_node, context):
            call_count[0] += 1
            if call_count[0] == 1:
                return _fail_result('vm1 failed')
            return _success_result(vm2_vm_id=99002, vm2_ip='10.0.12.101')

        mock_create.side_effect = side_effect

        executor = NodeExecutor(manifest=manifest, graph=graph, config=config)
        success, state = executor.create({})

        assert success is False  # Overall failed because vm1 failed
        assert mock_create.call_count == 2  # Both attempted
        assert state.get_node('vm1').status == 'failed'
        assert state.get_node('vm2').status == 'completed'


class TestNodeExecutorDestroy:
    """Tests for destroy lifecycle."""

    @patch('manifest_opr.executor.NodeExecutor._destroy_node')
    def test_destroy_flat(self, mock_destroy):
        """Flat manifest: all VMs destroyed locally."""
        manifest = _make_manifest([
            {'name': 'vm1', 'type': 'vm', 'vmid': 99001, 'image': 'debian-12', 'preset': 'vm-small'},
            {'name': 'vm2', 'type': 'vm', 'vmid': 99002, 'image': 'debian-12', 'preset': 'vm-small'},
        ])
        graph = ManifestGraph(manifest)
        config = _make_config()

        destroy_order = []

        def side_effect(exec_node, context):
            destroy_order.append(exec_node.name)
            return _success_result()

        mock_destroy.side_effect = side_effect

        executor = NodeExecutor(manifest=manifest, graph=graph, config=config)
        success, state = executor.destroy({})

        assert success is True
        # Destroy order is reversed create order: vm2, vm1
        assert set(destroy_order) == {'vm1', 'vm2'}

    @patch('manifest_opr.executor.NodeExecutor._delegate_subtree_destroy')
    @patch('manifest_opr.executor.NodeExecutor._destroy_node')
    def test_destroy_tiered_delegates_children(self, mock_destroy, mock_delegate_destroy):
        """Tiered manifest: children delegated, root destroyed locally."""
        manifest = _make_manifest([
            {'name': 'pve', 'type': 'pve', 'vmid': 99001, 'image': 'debian-13-pve', 'preset': 'vm-large'},
            {'name': 'test', 'type': 'vm', 'vmid': 99002, 'image': 'debian-12', 'preset': 'vm-small', 'parent': 'pve'},
        ], pattern='tiered')
        graph = ManifestGraph(manifest)
        config = _make_config()

        mock_destroy.return_value = _success_result()
        mock_delegate_destroy.return_value = _success_result()

        executor = NodeExecutor(manifest=manifest, graph=graph, config=config)
        # Put PVE IP in context so delegation can find it
        success, state = executor.destroy({'pve_ip': '10.0.12.100'})

        assert success is True
        assert mock_delegate_destroy.call_count == 1  # Children delegated
        assert mock_destroy.call_count == 1  # Root destroyed locally


class TestNodeExecutorDelegation:
    """Tests for PVE subtree delegation."""

    @patch('manifest_opr.executor.NodeExecutor._delegate_subtree')
    @patch('manifest_opr.executor.NodeExecutor._create_node')
    def test_pve_with_children_delegates(self, mock_create, mock_delegate):
        """PVE root node with children should trigger delegation."""
        manifest = _make_manifest([
            {'name': 'pve', 'type': 'pve', 'vmid': 99001, 'image': 'debian-13-pve', 'preset': 'vm-large'},
            {'name': 'test', 'type': 'vm', 'vmid': 99002, 'image': 'debian-12', 'preset': 'vm-small', 'parent': 'pve'},
        ], pattern='tiered')
        graph = ManifestGraph(manifest)
        config = _make_config()

        mock_create.return_value = _success_result(pve_vm_id=99001, pve_ip='10.0.12.100')
        mock_delegate.return_value = _success_result(test_vm_id=99002, test_ip='10.0.12.101')

        executor = NodeExecutor(manifest=manifest, graph=graph, config=config)
        success, state = executor.create({})

        assert success is True
        assert mock_create.call_count == 1  # Only root node
        assert mock_delegate.call_count == 1  # Delegation called

    @patch('manifest_opr.executor.NodeExecutor._delegate_subtree')
    @patch('manifest_opr.executor.NodeExecutor._create_node')
    def test_vm_root_no_delegation(self, mock_create, mock_delegate):
        """VM root node without children should not delegate."""
        manifest = _make_manifest([
            {'name': 'test', 'type': 'vm', 'vmid': 99001, 'image': 'debian-12', 'preset': 'vm-small'},
        ])
        graph = ManifestGraph(manifest)
        config = _make_config()

        mock_create.return_value = _success_result(test_vm_id=99001, test_ip='10.0.12.100')

        executor = NodeExecutor(manifest=manifest, graph=graph, config=config)
        success, state = executor.create({})

        assert success is True
        assert mock_delegate.call_count == 0

    @patch('manifest_opr.executor.NodeExecutor._delegate_subtree')
    @patch('manifest_opr.executor.NodeExecutor._create_node')
    def test_delegation_failure_marks_descendants(self, mock_create, mock_delegate):
        """Failed delegation should mark all descendants as failed."""
        manifest = _make_manifest([
            {'name': 'pve', 'type': 'pve', 'vmid': 99001, 'image': 'debian-13-pve', 'preset': 'vm-large'},
            {'name': 'test', 'type': 'vm', 'vmid': 99002, 'image': 'debian-12', 'preset': 'vm-small', 'parent': 'pve'},
        ], pattern='tiered')
        graph = ManifestGraph(manifest)
        config = _make_config()

        mock_create.return_value = _success_result(pve_vm_id=99001, pve_ip='10.0.12.100')
        mock_delegate.return_value = _fail_result('SSH connection refused')

        executor = NodeExecutor(manifest=manifest, graph=graph, config=config)
        success, state = executor.create({})

        assert success is False
        assert state.get_node('pve').status == 'completed'
        assert state.get_node('test').status == 'failed'
        assert 'Delegation failed' in state.get_node('test').error

    @patch('manifest_opr.executor.NodeExecutor._create_node')
    def test_only_root_nodes_created_locally(self, mock_create):
        """Only depth-0 nodes should be passed to _create_node."""
        manifest = _make_manifest([
            {'name': 'root', 'type': 'pve', 'vmid': 99001, 'image': 'debian-13-pve', 'preset': 'vm-large'},
            {'name': 'leaf', 'type': 'pve', 'vmid': 99002, 'image': 'debian-13-pve', 'preset': 'vm-medium', 'parent': 'root'},
            {'name': 'test', 'type': 'vm', 'vmid': 99003, 'image': 'debian-12', 'preset': 'vm-small', 'parent': 'leaf'},
        ], pattern='tiered')
        graph = ManifestGraph(manifest)
        config = _make_config()

        created_names = []

        def side_effect(exec_node, context):
            created_names.append(exec_node.name)
            return _success_result(**{f'{exec_node.name}_vm_id': exec_node.manifest_node.vmid, f'{exec_node.name}_ip': '10.0.12.100'})

        mock_create.side_effect = side_effect

        # Mock _delegate_subtree since root has children
        with patch.object(NodeExecutor, '_delegate_subtree') as mock_delegate:
            mock_delegate.return_value = _success_result(leaf_vm_id=99002, leaf_ip='10.0.12.101', test_vm_id=99003, test_ip='10.0.12.102')
            executor = NodeExecutor(manifest=manifest, graph=graph, config=config)
            success, state = executor.create({})

        assert success is True
        assert created_names == ['root']  # Only root created locally


class TestNodeExecutorTest:
    """Tests for test lifecycle (create + verify + destroy)."""

    @patch('manifest_opr.executor.NodeExecutor.destroy')
    @patch('manifest_opr.executor.NodeExecutor.create')
    def test_test_success(self, mock_create, mock_destroy):
        manifest = _make_manifest([
            {'name': 'test', 'type': 'vm', 'vmid': 99001, 'image': 'debian-12', 'preset': 'vm-small'},
        ])
        graph = ManifestGraph(manifest)
        config = _make_config()

        from manifest_opr.state import ExecutionState
        state = ExecutionState('test', 'test-host')
        state.add_node('test').complete(vm_id=99001, ip='10.0.12.100')

        mock_create.return_value = (True, state)
        mock_destroy.return_value = (True, state)

        executor = NodeExecutor(manifest=manifest, graph=graph, config=config)
        success, _ = executor.test({})

        assert success is True
        assert mock_create.call_count == 1
        assert mock_destroy.call_count == 1

    @patch('manifest_opr.executor.NodeExecutor.destroy')
    @patch('manifest_opr.executor.NodeExecutor.create')
    def test_test_cleanup_on_create_failure(self, mock_create, mock_destroy):
        manifest = _make_manifest([
            {'name': 'test', 'type': 'vm', 'vmid': 99001, 'image': 'debian-12', 'preset': 'vm-small'},
        ])
        graph = ManifestGraph(manifest)
        config = _make_config()

        from manifest_opr.state import ExecutionState
        state = ExecutionState('test', 'test-host')
        state.add_node('test').fail('provision error')

        mock_create.return_value = (False, state)
        mock_destroy.return_value = (True, state)

        executor = NodeExecutor(manifest=manifest, graph=graph, config=config)
        success, _ = executor.test({})

        assert success is False
        assert mock_destroy.call_count == 1  # Cleanup called
