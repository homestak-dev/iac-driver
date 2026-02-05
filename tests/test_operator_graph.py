"""Tests for manifest_opr.graph module."""

import sys
from pathlib import Path

import pytest

# Add src to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent / 'src'))

from manifest import Manifest, ManifestNode
from manifest_opr.graph import ExecutionNode, ManifestGraph


def _make_manifest(nodes_data, name='test', pattern='flat'):
    """Helper to create a v2 manifest from node dicts."""
    return Manifest.from_dict({
        'schema_version': 2,
        'name': name,
        'pattern': pattern,
        'nodes': nodes_data,
    })


class TestExecutionNode:
    """Tests for ExecutionNode dataclass."""

    def test_properties(self):
        mn = ManifestNode(name='test', type='vm', vmid=99001)
        node = ExecutionNode(manifest_node=mn, depth=0)
        assert node.name == 'test'
        assert node.type == 'vm'
        assert node.is_root is True
        assert node.is_leaf is True

    def test_non_root_node(self):
        parent_mn = ManifestNode(name='pve', type='pve', vmid=99001)
        parent = ExecutionNode(manifest_node=parent_mn, depth=0)

        child_mn = ManifestNode(name='test', type='vm', vmid=99002, parent='pve')
        child = ExecutionNode(manifest_node=child_mn, parent=parent, depth=1)
        parent.children.append(child)

        assert child.is_root is False
        assert child.is_leaf is True
        assert parent.is_leaf is False

    def test_repr(self):
        mn = ManifestNode(name='test', type='vm', vmid=99001)
        node = ExecutionNode(manifest_node=mn, depth=0)
        assert 'test' in repr(node)
        assert 'vm' in repr(node)


class TestManifestGraph:
    """Tests for ManifestGraph class."""

    def test_flat_single_node(self):
        manifest = _make_manifest([
            {'name': 'test', 'type': 'vm', 'vmid': 99001, 'image': 'debian-12'},
        ])
        graph = ManifestGraph(manifest)

        assert len(graph.roots) == 1
        assert graph.roots[0].name == 'test'
        assert graph.max_depth == 0

    def test_flat_multiple_roots(self):
        manifest = _make_manifest([
            {'name': 'vm1', 'type': 'vm', 'vmid': 99001, 'image': 'debian-12'},
            {'name': 'vm2', 'type': 'vm', 'vmid': 99002, 'image': 'debian-12'},
        ])
        graph = ManifestGraph(manifest)

        assert len(graph.roots) == 2
        assert graph.max_depth == 0

    def test_tiered_two_level(self):
        manifest = _make_manifest([
            {'name': 'pve', 'type': 'pve', 'vmid': 99001, 'preset': 'vm-large', 'image': 'debian-13-pve'},
            {'name': 'test', 'type': 'vm', 'vmid': 99002, 'preset': 'vm-small', 'image': 'debian-12', 'parent': 'pve'},
        ], pattern='tiered')
        graph = ManifestGraph(manifest)

        assert len(graph.roots) == 1
        assert graph.roots[0].name == 'pve'
        assert len(graph.roots[0].children) == 1
        assert graph.roots[0].children[0].name == 'test'
        assert graph.max_depth == 1

    def test_three_level_chain(self):
        manifest = _make_manifest([
            {'name': 'root', 'type': 'pve', 'vmid': 99001, 'image': 'debian-13-pve'},
            {'name': 'leaf', 'type': 'pve', 'vmid': 99002, 'image': 'debian-13-pve', 'parent': 'root'},
            {'name': 'test', 'type': 'vm', 'vmid': 99003, 'image': 'debian-12', 'parent': 'leaf'},
        ], pattern='tiered')
        graph = ManifestGraph(manifest)

        assert len(graph.roots) == 1
        assert graph.max_depth == 2
        assert graph.get_node('test').depth == 2

    def test_get_node(self):
        manifest = _make_manifest([
            {'name': 'pve', 'type': 'pve', 'vmid': 99001, 'image': 'debian-13-pve'},
            {'name': 'test', 'type': 'vm', 'vmid': 99002, 'image': 'debian-12', 'parent': 'pve'},
        ], pattern='tiered')
        graph = ManifestGraph(manifest)

        node = graph.get_node('test')
        assert node.name == 'test'
        assert node.depth == 1

    def test_get_node_not_found(self):
        manifest = _make_manifest([
            {'name': 'test', 'type': 'vm', 'vmid': 99001, 'image': 'debian-12'},
        ])
        graph = ManifestGraph(manifest)

        with pytest.raises(KeyError):
            graph.get_node('nonexistent')

    def test_requires_v2_manifest(self):
        manifest = Manifest(
            schema_version=1,
            name='v1-test',
            levels=[],
            nodes=None,
        )
        with pytest.raises(ValueError, match="v2 manifest"):
            ManifestGraph(manifest)


class TestManifestGraphOrdering:
    """Tests for create_order and destroy_order."""

    def test_create_order_parents_before_children(self):
        manifest = _make_manifest([
            {'name': 'pve', 'type': 'pve', 'vmid': 99001, 'image': 'debian-13-pve'},
            {'name': 'test', 'type': 'vm', 'vmid': 99002, 'image': 'debian-12', 'parent': 'pve'},
        ], pattern='tiered')
        graph = ManifestGraph(manifest)
        order = graph.create_order()

        names = [n.name for n in order]
        assert names == ['pve', 'test']

    def test_destroy_order_children_before_parents(self):
        manifest = _make_manifest([
            {'name': 'pve', 'type': 'pve', 'vmid': 99001, 'image': 'debian-13-pve'},
            {'name': 'test', 'type': 'vm', 'vmid': 99002, 'image': 'debian-12', 'parent': 'pve'},
        ], pattern='tiered')
        graph = ManifestGraph(manifest)
        order = graph.destroy_order()

        names = [n.name for n in order]
        assert names == ['test', 'pve']

    def test_three_level_create_order(self):
        manifest = _make_manifest([
            {'name': 'root', 'type': 'pve', 'vmid': 99001, 'image': 'debian-13-pve'},
            {'name': 'leaf', 'type': 'pve', 'vmid': 99002, 'image': 'debian-13-pve', 'parent': 'root'},
            {'name': 'test', 'type': 'vm', 'vmid': 99003, 'image': 'debian-12', 'parent': 'leaf'},
        ], pattern='tiered')
        graph = ManifestGraph(manifest)
        order = graph.create_order()

        names = [n.name for n in order]
        assert names == ['root', 'leaf', 'test']

    def test_three_level_destroy_order(self):
        manifest = _make_manifest([
            {'name': 'root', 'type': 'pve', 'vmid': 99001, 'image': 'debian-13-pve'},
            {'name': 'leaf', 'type': 'pve', 'vmid': 99002, 'image': 'debian-13-pve', 'parent': 'root'},
            {'name': 'test', 'type': 'vm', 'vmid': 99003, 'image': 'debian-12', 'parent': 'leaf'},
        ], pattern='tiered')
        graph = ManifestGraph(manifest)
        order = graph.destroy_order()

        names = [n.name for n in order]
        assert names == ['test', 'leaf', 'root']

    def test_flat_multiple_roots_order(self):
        manifest = _make_manifest([
            {'name': 'vm1', 'type': 'vm', 'vmid': 99001, 'image': 'debian-12'},
            {'name': 'vm2', 'type': 'vm', 'vmid': 99002, 'image': 'debian-12'},
            {'name': 'vm3', 'type': 'vm', 'vmid': 99003, 'image': 'debian-12'},
        ])
        graph = ManifestGraph(manifest)
        order = graph.create_order()

        names = [n.name for n in order]
        assert names == ['vm1', 'vm2', 'vm3']

    def test_branching_topology(self):
        """Test tree with one parent and two children."""
        manifest = _make_manifest([
            {'name': 'pve', 'type': 'pve', 'vmid': 99001, 'image': 'debian-13-pve'},
            {'name': 'vm1', 'type': 'vm', 'vmid': 99002, 'image': 'debian-12', 'parent': 'pve'},
            {'name': 'vm2', 'type': 'vm', 'vmid': 99003, 'image': 'debian-12', 'parent': 'pve'},
        ], pattern='tiered')
        graph = ManifestGraph(manifest)

        create = [n.name for n in graph.create_order()]
        assert create[0] == 'pve'  # Parent first
        assert set(create[1:]) == {'vm1', 'vm2'}  # Children after

        destroy = [n.name for n in graph.destroy_order()]
        assert destroy[-1] == 'pve'  # Parent last
        assert set(destroy[:-1]) == {'vm1', 'vm2'}  # Children first


class TestManifestGraphParentIPKey:
    """Tests for get_parent_ip_key."""

    def test_root_uses_ssh_host(self):
        manifest = _make_manifest([
            {'name': 'test', 'type': 'vm', 'vmid': 99001, 'image': 'debian-12'},
        ])
        graph = ManifestGraph(manifest)
        root = graph.get_node('test')

        assert graph.get_parent_ip_key(root) == 'ssh_host'

    def test_child_uses_parent_ip(self):
        manifest = _make_manifest([
            {'name': 'pve', 'type': 'pve', 'vmid': 99001, 'image': 'debian-13-pve'},
            {'name': 'test', 'type': 'vm', 'vmid': 99002, 'image': 'debian-12', 'parent': 'pve'},
        ], pattern='tiered')
        graph = ManifestGraph(manifest)
        child = graph.get_node('test')

        assert graph.get_parent_ip_key(child) == 'pve_ip'
