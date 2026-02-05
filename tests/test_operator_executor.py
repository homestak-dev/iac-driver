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

    @patch('manifest_opr.executor.NodeExecutor._create_node')
    def test_tiered_create_order(self, mock_create):
        manifest = _make_manifest([
            {'name': 'pve', 'type': 'pve', 'vmid': 99001, 'image': 'debian-13-pve', 'preset': 'vm-large'},
            {'name': 'test', 'type': 'vm', 'vmid': 99002, 'image': 'debian-12', 'preset': 'vm-small', 'parent': 'pve'},
        ], pattern='tiered')
        graph = ManifestGraph(manifest)
        config = _make_config()

        call_order = []

        def side_effect(exec_node, context):
            call_order.append(exec_node.name)
            return _success_result(**{f'{exec_node.name}_vm_id': exec_node.manifest_node.vmid})

        mock_create.side_effect = side_effect

        executor = NodeExecutor(manifest=manifest, graph=graph, config=config)
        success, state = executor.create({})

        assert success is True
        assert call_order == ['pve', 'test']

    @patch('manifest_opr.executor.NodeExecutor._create_node')
    def test_create_stop_on_failure(self, mock_create):
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
        assert mock_create.call_count == 1  # Stopped after first failure
        assert state.get_node('pve').status == 'failed'
        assert state.get_node('test').status == 'pending'  # Never attempted

    @patch('manifest_opr.executor.NodeExecutor._destroy_node')
    @patch('manifest_opr.executor.NodeExecutor._create_node')
    def test_create_rollback_on_failure(self, mock_create, mock_destroy):
        manifest = _make_manifest([
            {'name': 'pve', 'type': 'pve', 'vmid': 99001, 'image': 'debian-13-pve', 'preset': 'vm-large'},
            {'name': 'test', 'type': 'vm', 'vmid': 99002, 'image': 'debian-12', 'preset': 'vm-small', 'parent': 'pve'},
        ], pattern='tiered', on_error='rollback')
        graph = ManifestGraph(manifest)
        config = _make_config()

        call_count = [0]

        def create_side_effect(exec_node, context):
            call_count[0] += 1
            if call_count[0] == 1:
                # pve succeeds
                return _success_result(pve_vm_id=99001, pve_ip='10.0.12.100')
            # test fails
            return _fail_result('provision failed')

        mock_create.side_effect = create_side_effect
        mock_destroy.return_value = _success_result()

        executor = NodeExecutor(manifest=manifest, graph=graph, config=config)
        success, state = executor.create({})

        assert success is False
        assert mock_destroy.call_count == 1  # Rolled back pve
        assert state.get_node('pve').status == 'destroyed'

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
    def test_destroy_reverse_order(self, mock_destroy):
        manifest = _make_manifest([
            {'name': 'pve', 'type': 'pve', 'vmid': 99001, 'image': 'debian-13-pve', 'preset': 'vm-large'},
            {'name': 'test', 'type': 'vm', 'vmid': 99002, 'image': 'debian-12', 'preset': 'vm-small', 'parent': 'pve'},
        ], pattern='tiered')
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
        assert destroy_order == ['test', 'pve']  # Children before parents


class TestNodeExecutorDepthLimit:
    """Tests for depth limit enforcement."""

    @patch('manifest_opr.executor.NodeExecutor._create_node')
    def test_depth_exceeded_stops(self, mock_create):
        manifest = _make_manifest([
            {'name': 'root', 'type': 'pve', 'vmid': 99001, 'image': 'debian-13-pve', 'preset': 'vm-large'},
            {'name': 'leaf', 'type': 'pve', 'vmid': 99002, 'image': 'debian-13-pve', 'preset': 'vm-medium', 'parent': 'root'},
            {'name': 'test', 'type': 'vm', 'vmid': 99003, 'image': 'debian-12', 'preset': 'vm-small', 'parent': 'leaf'},
        ], pattern='tiered', on_error='stop')
        graph = ManifestGraph(manifest)
        config = _make_config()

        call_count = [0]

        def side_effect(exec_node, context):
            call_count[0] += 1
            return _success_result(**{f'{exec_node.name}_vm_id': exec_node.manifest_node.vmid})

        mock_create.side_effect = side_effect

        executor = NodeExecutor(manifest=manifest, graph=graph, config=config)
        success, state = executor.create({})

        # root (depth 0) and leaf (depth 1) should succeed
        # test (depth 2) exceeds limit
        assert success is False
        assert call_count[0] == 2  # Only root and leaf attempted
        assert state.get_node('test').status == 'failed'
        assert 'depth' in state.get_node('test').error


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
