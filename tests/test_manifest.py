#!/usr/bin/env python3
"""Tests for manifest.py - recursion manifest loading and validation.

Tests verify:
1. Manifest dataclass creation
2. Schema validation
3. Level parsing
4. YAML file loading
5. JSON serialization/deserialization
6. Depth limiting
"""

import json
import sys
from pathlib import Path
from unittest.mock import patch, MagicMock
import tempfile

sys.path.insert(0, str(Path(__file__).parent.parent / 'src'))

import pytest
from config import ConfigError


class TestManifestLevel:
    """Test ManifestLevel dataclass."""

    def test_from_dict_minimal(self):
        """Should create level with minimal required fields."""
        from manifest import ManifestLevel

        data = {'name': 'inner-pve', 'env': 'nested-pve'}
        level = ManifestLevel.from_dict(data)

        assert level.name == 'inner-pve'
        assert level.env == 'nested-pve'
        assert level.image is None
        assert level.vmid_offset == 0
        assert level.post_scenario is None
        assert level.post_scenario_args == []

    def test_from_dict_full(self):
        """Should create level with all fields."""
        from manifest import ManifestLevel

        data = {
            'name': 'inner-pve',
            'env': 'nested-pve',
            'image': 'debian-13-pve',
            'vmid_offset': 100,
            'post_scenario': 'pve-setup',
            'post_scenario_args': ['--local']
        }
        level = ManifestLevel.from_dict(data)

        assert level.name == 'inner-pve'
        assert level.env == 'nested-pve'
        assert level.image == 'debian-13-pve'
        assert level.vmid_offset == 100
        assert level.post_scenario == 'pve-setup'
        assert level.post_scenario_args == ['--local']


class TestManifestSettings:
    """Test ManifestSettings dataclass."""

    def test_defaults(self):
        """Should have sensible defaults."""
        from manifest import ManifestSettings

        settings = ManifestSettings()

        assert settings.verify_ssh is True
        assert settings.cleanup_on_failure is True
        assert settings.timeout_buffer == 60

    def test_from_dict_none(self):
        """Should return defaults for None input."""
        from manifest import ManifestSettings

        settings = ManifestSettings.from_dict(None)

        assert settings.verify_ssh is True
        assert settings.cleanup_on_failure is True

    def test_from_dict_partial(self):
        """Should apply partial overrides."""
        from manifest import ManifestSettings

        settings = ManifestSettings.from_dict({'cleanup_on_failure': False})

        assert settings.verify_ssh is True  # default
        assert settings.cleanup_on_failure is False  # overridden


class TestManifest:
    """Test Manifest dataclass."""

    def test_from_dict_minimal(self):
        """Should create manifest with minimal required fields."""
        from manifest import Manifest

        data = {
            'name': 'test',
            'levels': [
                {'name': 'level1', 'env': 'test'}
            ]
        }
        manifest = Manifest.from_dict(data)

        assert manifest.schema_version == 1
        assert manifest.name == 'test'
        assert manifest.description == ''
        assert len(manifest.levels) == 1
        assert manifest.levels[0].name == 'level1'

    def test_from_dict_full(self):
        """Should create manifest with all fields."""
        from manifest import Manifest

        data = {
            'schema_version': 1,
            'name': 'n2-quick',
            'description': 'Quick test',
            'levels': [
                {'name': 'inner', 'env': 'nested-pve', 'image': 'debian-13-pve'},
                {'name': 'leaf', 'env': 'test'}
            ],
            'settings': {
                'verify_ssh': False,
                'cleanup_on_failure': False,
                'timeout_buffer': 30
            }
        }
        manifest = Manifest.from_dict(data)

        assert manifest.schema_version == 1
        assert manifest.name == 'n2-quick'
        assert manifest.description == 'Quick test'
        assert len(manifest.levels) == 2
        assert manifest.settings.verify_ssh is False
        assert manifest.settings.cleanup_on_failure is False
        assert manifest.settings.timeout_buffer == 30

    def test_missing_name_raises_error(self):
        """Should raise error when name missing."""
        from manifest import Manifest

        data = {'levels': [{'name': 'level1', 'env': 'test'}]}

        with pytest.raises(ConfigError, match='missing required field: name'):
            Manifest.from_dict(data)

    def test_missing_levels_raises_error(self):
        """Should raise error when levels missing."""
        from manifest import Manifest

        data = {'name': 'test'}

        with pytest.raises(ConfigError, match='missing required field: levels'):
            Manifest.from_dict(data)

    def test_empty_levels_raises_error(self):
        """Should raise error when levels empty."""
        from manifest import Manifest

        data = {'name': 'test', 'levels': []}

        with pytest.raises(ConfigError, match='must have at least one level'):
            Manifest.from_dict(data)

    def test_unsupported_schema_version(self):
        """Should raise error for unsupported schema version."""
        from manifest import Manifest

        data = {
            'schema_version': 99,
            'name': 'test',
            'levels': [{'name': 'level1', 'env': 'test'}]
        }

        with pytest.raises(ConfigError, match='Unsupported manifest schema version'):
            Manifest.from_dict(data)

    def test_level_missing_name(self):
        """Should raise error when level missing name."""
        from manifest import Manifest

        data = {
            'name': 'test',
            'levels': [{'env': 'test'}]
        }

        with pytest.raises(ConfigError, match='Level 0 missing required field: name'):
            Manifest.from_dict(data)

    def test_level_missing_env_and_vm_preset(self):
        """Should raise error when level missing env, template, and vm_preset."""
        from manifest import Manifest

        data = {
            'name': 'test',
            'levels': [{'name': 'level1'}]
        }

        with pytest.raises(ConfigError, match="requires 'vm_preset', 'template', or 'env'"):
            Manifest.from_dict(data)


class TestManifestProperties:
    """Test Manifest property methods."""

    def test_depth(self):
        """Should return correct depth."""
        from manifest import Manifest

        data = {
            'name': 'test',
            'levels': [
                {'name': 'l1', 'env': 'test'},
                {'name': 'l2', 'env': 'test'},
                {'name': 'l3', 'env': 'test'}
            ]
        }
        manifest = Manifest.from_dict(data)

        assert manifest.depth == 3

    def test_is_leaf_true(self):
        """Should return True for single-level manifest."""
        from manifest import Manifest

        data = {
            'name': 'test',
            'levels': [{'name': 'leaf', 'env': 'test'}]
        }
        manifest = Manifest.from_dict(data)

        assert manifest.is_leaf is True

    def test_is_leaf_false(self):
        """Should return False for multi-level manifest."""
        from manifest import Manifest

        data = {
            'name': 'test',
            'levels': [
                {'name': 'l1', 'env': 'test'},
                {'name': 'l2', 'env': 'test'}
            ]
        }
        manifest = Manifest.from_dict(data)

        assert manifest.is_leaf is False

    def test_get_current_level(self):
        """Should return first level."""
        from manifest import Manifest

        data = {
            'name': 'test',
            'levels': [
                {'name': 'first', 'env': 'test'},
                {'name': 'second', 'env': 'test'}
            ]
        }
        manifest = Manifest.from_dict(data)

        level = manifest.get_current_level()
        assert level.name == 'first'

    def test_get_remaining_manifest(self):
        """Should return manifest without first level."""
        from manifest import Manifest

        data = {
            'name': 'test',
            'levels': [
                {'name': 'first', 'env': 'env1'},
                {'name': 'second', 'env': 'env2'},
                {'name': 'third', 'env': 'env3'}
            ]
        }
        manifest = Manifest.from_dict(data)

        remaining = manifest.get_remaining_manifest()

        assert remaining.depth == 2
        assert remaining.levels[0].name == 'second'
        assert remaining.levels[1].name == 'third'

    def test_get_remaining_manifest_at_leaf_raises(self):
        """Should raise error when at leaf level."""
        from manifest import Manifest

        data = {
            'name': 'test',
            'levels': [{'name': 'leaf', 'env': 'test'}]
        }
        manifest = Manifest.from_dict(data)

        with pytest.raises(ConfigError, match='already at leaf level'):
            manifest.get_remaining_manifest()


class TestManifestSerialization:
    """Test manifest serialization."""

    def test_to_dict(self):
        """Should serialize to dictionary."""
        from manifest import Manifest

        data = {
            'schema_version': 1,
            'name': 'test',
            'description': 'Test manifest',
            'levels': [{'name': 'level1', 'env': 'test'}],
            'settings': {'verify_ssh': True}
        }
        manifest = Manifest.from_dict(data)

        result = manifest.to_dict()

        assert result['schema_version'] == 1
        assert result['name'] == 'test'
        assert result['description'] == 'Test manifest'
        assert len(result['levels']) == 1
        assert 'settings' in result

    def test_to_json(self):
        """Should serialize to JSON string."""
        from manifest import Manifest

        data = {
            'name': 'test',
            'levels': [{'name': 'level1', 'env': 'test'}]
        }
        manifest = Manifest.from_dict(data)

        json_str = manifest.to_json()
        parsed = json.loads(json_str)

        assert parsed['name'] == 'test'
        assert len(parsed['levels']) == 1

    def test_from_json(self):
        """Should deserialize from JSON string."""
        from manifest import Manifest

        json_str = '{"name": "test", "levels": [{"name": "l1", "env": "e1"}]}'

        manifest = Manifest.from_json(json_str)

        assert manifest.name == 'test'
        assert len(manifest.levels) == 1

    def test_roundtrip(self):
        """Should survive JSON roundtrip."""
        from manifest import Manifest

        original = {
            'schema_version': 1,
            'name': 'n2-quick',
            'description': 'Test',
            'levels': [
                {'name': 'inner', 'env': 'nested-pve', 'image': 'debian-13'},
                {'name': 'leaf', 'env': 'test', 'vmid_offset': 100}
            ],
            'settings': {'cleanup_on_failure': False}
        }
        manifest = Manifest.from_dict(original)

        # Roundtrip through JSON
        json_str = manifest.to_json()
        restored = Manifest.from_json(json_str)

        assert restored.name == manifest.name
        assert restored.depth == manifest.depth
        assert restored.settings.cleanup_on_failure is False


class TestManifestLoader:
    """Test ManifestLoader class."""

    def test_list_manifests(self):
        """Should list available manifests."""
        from manifest import ManifestLoader

        with tempfile.TemporaryDirectory() as tmpdir:
            manifests_dir = Path(tmpdir) / 'manifests'
            manifests_dir.mkdir()

            # Create test manifests
            (manifests_dir / 'n2-quick.yaml').write_text('name: n2-quick\nlevels:\n  - name: l1\n    env: test\n')
            (manifests_dir / 'n3-full.yaml').write_text('name: n3-full\nlevels:\n  - name: l1\n    env: test\n')

            loader = ManifestLoader(site_config_path=tmpdir)
            manifests = loader.list_manifests()

            assert 'n2-quick' in manifests
            assert 'n3-full' in manifests

    def test_load_manifest(self):
        """Should load manifest by name."""
        from manifest import ManifestLoader

        with tempfile.TemporaryDirectory() as tmpdir:
            manifests_dir = Path(tmpdir) / 'manifests'
            manifests_dir.mkdir()

            yaml_content = """
name: test-manifest
description: Test description
levels:
  - name: inner
    env: nested-pve
    image: debian-13-pve
"""
            (manifests_dir / 'test-manifest.yaml').write_text(yaml_content)

            loader = ManifestLoader(site_config_path=tmpdir)
            manifest = loader.load('test-manifest')

            assert manifest.name == 'test-manifest'
            assert manifest.description == 'Test description'
            assert manifest.levels[0].image == 'debian-13-pve'

    def test_load_nonexistent_raises_error(self):
        """Should raise error for nonexistent manifest."""
        from manifest import ManifestLoader

        with tempfile.TemporaryDirectory() as tmpdir:
            manifests_dir = Path(tmpdir) / 'manifests'
            manifests_dir.mkdir()

            loader = ManifestLoader(site_config_path=tmpdir)

            with pytest.raises(ConfigError, match='not found'):
                loader.load('nonexistent')


class TestLoadManifestFunction:
    """Test load_manifest convenience function."""

    def test_load_from_json(self):
        """Should load from JSON string."""
        from manifest import load_manifest

        json_str = '{"name": "inline", "levels": [{"name": "l1", "env": "e1"}]}'

        manifest = load_manifest(json_str=json_str)

        assert manifest.name == 'inline'

    def test_depth_limit(self):
        """Should apply depth limit."""
        from manifest import load_manifest

        json_str = '''
        {
            "name": "deep",
            "levels": [
                {"name": "l1", "env": "e1"},
                {"name": "l2", "env": "e2"},
                {"name": "l3", "env": "e3"}
            ]
        }
        '''

        manifest = load_manifest(json_str=json_str, depth=2)

        assert manifest.depth == 2
        assert manifest.levels[0].name == 'l1'
        assert manifest.levels[1].name == 'l2'
        # l3 should be truncated

    def test_depth_limit_larger_than_manifest(self):
        """Depth limit larger than manifest should not change it."""
        from manifest import load_manifest

        json_str = '{"name": "short", "levels": [{"name": "l1", "env": "e1"}]}'

        manifest = load_manifest(json_str=json_str, depth=5)

        assert manifest.depth == 1  # Not modified


# ============================================================================
# Schema v2 Tests
# ============================================================================


class TestManifestNode:
    """Test ManifestNode dataclass."""

    def test_from_dict_minimal(self):
        """Should create node with minimal required fields."""
        from manifest import ManifestNode

        data = {'name': 'test', 'type': 'vm'}
        node = ManifestNode.from_dict(data)

        assert node.name == 'test'
        assert node.type == 'vm'
        assert node.spec is None
        assert node.preset is None
        assert node.image is None
        assert node.vmid is None
        assert node.disk is None
        assert node.parent is None
        assert node.execution_mode is None

    def test_from_dict_full(self):
        """Should create node with all fields."""
        from manifest import ManifestNode

        data = {
            'name': 'nested-pve',
            'type': 'pve',
            'spec': 'pve',
            'preset': 'vm-large',
            'image': 'debian-13-pve',
            'vmid': 99011,
            'disk': 64,
            'parent': None,
            'execution': {'mode': 'push'},
        }
        node = ManifestNode.from_dict(data)

        assert node.name == 'nested-pve'
        assert node.type == 'pve'
        assert node.spec == 'pve'
        assert node.preset == 'vm-large'
        assert node.image == 'debian-13-pve'
        assert node.vmid == 99011
        assert node.disk == 64
        assert node.parent is None
        assert node.execution_mode == 'push'

    def test_from_dict_with_parent(self):
        """Should create child node with parent reference."""
        from manifest import ManifestNode

        data = {
            'name': 'test',
            'type': 'vm',
            'preset': 'vm-small',
            'image': 'debian-12',
            'vmid': 99021,
            'parent': 'nested-pve',
        }
        node = ManifestNode.from_dict(data)

        assert node.parent == 'nested-pve'

    def test_to_dict_roundtrip(self):
        """Should survive dict roundtrip."""
        from manifest import ManifestNode

        original = {
            'name': 'test',
            'type': 'vm',
            'preset': 'vm-small',
            'image': 'debian-12',
            'vmid': 99021,
            'parent': 'nested-pve',
        }
        node = ManifestNode.from_dict(original)
        result = node.to_dict()

        assert result['name'] == 'test'
        assert result['type'] == 'vm'
        assert result['parent'] == 'nested-pve'
        assert result['vmid'] == 99021

    def test_to_dict_omits_none_fields(self):
        """Should omit None optional fields in serialization."""
        from manifest import ManifestNode

        node = ManifestNode(name='test', type='vm')
        result = node.to_dict()

        assert 'spec' not in result
        assert 'parent' not in result
        assert 'disk' not in result
        assert 'execution' not in result

    def test_to_dict_includes_execution_mode(self):
        """Should include execution mode when set."""
        from manifest import ManifestNode

        node = ManifestNode(name='test', type='vm', execution_mode='pull')
        result = node.to_dict()

        assert result['execution'] == {'mode': 'pull'}


class TestManifestV2:
    """Test v2 manifest parsing (graph-based nodes)."""

    def test_from_dict_flat(self):
        """Should parse flat (single-level) v2 manifest."""
        from manifest import Manifest

        data = {
            'schema_version': 2,
            'name': 'n1-basic-v2',
            'pattern': 'flat',
            'nodes': [
                {'name': 'edge', 'type': 'vm', 'preset': 'vm-small', 'image': 'debian-12', 'vmid': 99001}
            ]
        }
        manifest = Manifest.from_dict(data)

        assert manifest.schema_version == 2
        assert manifest.name == 'n1-basic-v2'
        assert manifest.pattern == 'flat'
        assert manifest.nodes is not None
        assert len(manifest.nodes) == 1
        assert manifest.nodes[0].name == 'edge'
        # Should also have levels (backward compat)
        assert len(manifest.levels) == 1
        assert manifest.levels[0].name == 'edge'

    def test_from_dict_tiered(self):
        """Should parse tiered (parent-child) v2 manifest."""
        from manifest import Manifest

        data = {
            'schema_version': 2,
            'name': 'n2-quick-v2',
            'pattern': 'tiered',
            'nodes': [
                {'name': 'root-pve', 'type': 'pve', 'preset': 'vm-large', 'image': 'debian-13-pve', 'vmid': 99011},
                {'name': 'edge', 'type': 'vm', 'preset': 'vm-medium', 'image': 'debian-12', 'vmid': 99021, 'parent': 'root-pve'}
            ]
        }
        manifest = Manifest.from_dict(data)

        assert manifest.schema_version == 2
        assert manifest.pattern == 'tiered'
        assert len(manifest.nodes) == 2
        # Levels should be in topo order (parent before child)
        assert manifest.levels[0].name == 'root-pve'
        assert manifest.levels[1].name == 'edge'

    def test_from_dict_three_level(self):
        """Should parse 3-level tiered manifest."""
        from manifest import Manifest

        data = {
            'schema_version': 2,
            'name': 'n3-full-v2',
            'pattern': 'tiered',
            'nodes': [
                {'name': 'root-pve', 'type': 'pve', 'preset': 'vm-large', 'image': 'debian-13-pve', 'vmid': 99011},
                {'name': 'leaf-pve', 'type': 'pve', 'preset': 'vm-medium', 'image': 'debian-13-pve', 'vmid': 99021, 'parent': 'root-pve'},
                {'name': 'edge', 'type': 'vm', 'preset': 'vm-small', 'image': 'debian-12', 'vmid': 99031, 'parent': 'leaf-pve'}
            ]
        }
        manifest = Manifest.from_dict(data)

        assert len(manifest.nodes) == 3
        assert manifest.depth == 3
        assert manifest.levels[0].name == 'root-pve'
        assert manifest.levels[1].name == 'leaf-pve'
        assert manifest.levels[2].name == 'edge'

    def test_pve_nodes_get_post_scenario(self):
        """PVE nodes should get pve-setup as post_scenario in converted levels."""
        from manifest import Manifest

        data = {
            'schema_version': 2,
            'name': 'test',
            'nodes': [
                {'name': 'nested-pve', 'type': 'pve', 'preset': 'vm-large', 'image': 'debian-13-pve', 'vmid': 99011},
                {'name': 'test', 'type': 'vm', 'preset': 'vm-small', 'image': 'debian-12', 'vmid': 99021, 'parent': 'nested-pve'}
            ]
        }
        manifest = Manifest.from_dict(data)

        # PVE node should have pve-setup post_scenario
        assert manifest.levels[0].post_scenario == 'pve-setup'
        assert manifest.levels[0].post_scenario_args == ['--local', '--skip-preflight']
        # VM node should not
        assert manifest.levels[1].post_scenario is None

    def test_vm_preset_prefix_stripped(self):
        """v2 presets (vm-prefixed) should have prefix stripped for v1 compat."""
        from manifest import Manifest

        data = {
            'schema_version': 2,
            'name': 'test',
            'nodes': [
                {'name': 'test', 'type': 'vm', 'preset': 'vm-small', 'image': 'debian-12', 'vmid': 99001}
            ]
        }
        manifest = Manifest.from_dict(data)

        # v1 level should have prefix-stripped preset
        assert manifest.levels[0].vm_preset == 'small'

    def test_default_pattern_is_flat(self):
        """Should default to flat pattern when not specified."""
        from manifest import Manifest

        data = {
            'schema_version': 2,
            'name': 'test',
            'nodes': [
                {'name': 'test', 'type': 'vm', 'vmid': 99001}
            ]
        }
        manifest = Manifest.from_dict(data)

        assert manifest.pattern == 'flat'

    def test_default_execution_mode_is_push(self):
        """Should default to push execution mode."""
        from manifest import Manifest

        data = {
            'schema_version': 2,
            'name': 'test',
            'nodes': [
                {'name': 'test', 'type': 'vm', 'vmid': 99001}
            ]
        }
        manifest = Manifest.from_dict(data)

        assert manifest.execution_mode == 'push'

    def test_missing_nodes_raises_error(self):
        """Should raise error when nodes missing."""
        from manifest import Manifest

        data = {'schema_version': 2, 'name': 'test'}

        with pytest.raises(ConfigError, match='missing required field: nodes'):
            Manifest.from_dict(data)

    def test_empty_nodes_raises_error(self):
        """Should raise error when nodes empty."""
        from manifest import Manifest

        data = {'schema_version': 2, 'name': 'test', 'nodes': []}

        with pytest.raises(ConfigError, match='must have at least one node'):
            Manifest.from_dict(data)

    def test_node_missing_name_raises_error(self):
        """Should raise error when node missing name."""
        from manifest import Manifest

        data = {
            'schema_version': 2,
            'name': 'test',
            'nodes': [{'type': 'vm'}]
        }

        with pytest.raises(ConfigError, match='Node 0 missing required field: name'):
            Manifest.from_dict(data)

    def test_node_missing_type_raises_error(self):
        """Should raise error when node missing type."""
        from manifest import Manifest

        data = {
            'schema_version': 2,
            'name': 'test',
            'nodes': [{'name': 'test'}]
        }

        with pytest.raises(ConfigError, match='missing required field: type'):
            Manifest.from_dict(data)

    def test_settings_with_on_error(self):
        """Should parse on_error setting."""
        from manifest import Manifest

        data = {
            'schema_version': 2,
            'name': 'test',
            'nodes': [{'name': 'test', 'type': 'vm'}],
            'settings': {'on_error': 'rollback'}
        }
        manifest = Manifest.from_dict(data)

        assert manifest.settings.on_error == 'rollback'


class TestManifestV2GraphValidation:
    """Test graph validation for v2 manifests."""

    def test_duplicate_names_raises_error(self):
        """Should raise error for duplicate node names."""
        from manifest import Manifest

        data = {
            'schema_version': 2,
            'name': 'test',
            'nodes': [
                {'name': 'test', 'type': 'vm'},
                {'name': 'test', 'type': 'vm'}
            ]
        }

        with pytest.raises(ConfigError, match="Duplicate node name: 'test'"):
            Manifest.from_dict(data)

    def test_dangling_parent_raises_error(self):
        """Should raise error for dangling parent reference."""
        from manifest import Manifest

        data = {
            'schema_version': 2,
            'name': 'test',
            'nodes': [
                {'name': 'test', 'type': 'vm', 'parent': 'nonexistent'}
            ]
        }

        with pytest.raises(ConfigError, match="references unknown parent 'nonexistent'"):
            Manifest.from_dict(data)

    def test_cycle_raises_error(self):
        """Should raise error for cycles in parent graph."""
        from manifest import Manifest

        data = {
            'schema_version': 2,
            'name': 'test',
            'nodes': [
                {'name': 'a', 'type': 'vm', 'parent': 'b'},
                {'name': 'b', 'type': 'vm', 'parent': 'a'}
            ]
        }

        with pytest.raises(ConfigError, match='Cycle detected'):
            Manifest.from_dict(data)

    def test_self_reference_raises_error(self):
        """Should raise error for self-referencing parent."""
        from manifest import Manifest

        data = {
            'schema_version': 2,
            'name': 'test',
            'nodes': [
                {'name': 'a', 'type': 'vm', 'parent': 'a'}
            ]
        }

        with pytest.raises(ConfigError, match='Cycle detected'):
            Manifest.from_dict(data)

    def test_valid_tree_passes(self):
        """Should accept valid tree structure."""
        from manifest import Manifest

        data = {
            'schema_version': 2,
            'name': 'test',
            'nodes': [
                {'name': 'root', 'type': 'pve'},
                {'name': 'child1', 'type': 'vm', 'parent': 'root'},
                {'name': 'child2', 'type': 'vm', 'parent': 'root'}
            ]
        }
        manifest = Manifest.from_dict(data)

        assert len(manifest.nodes) == 3


class TestManifestV2TopologicalSort:
    """Test topological sort ordering for v2 manifests."""

    def test_parent_before_child(self):
        """Parents should appear before children in levels."""
        from manifest import Manifest

        data = {
            'schema_version': 2,
            'name': 'test',
            'nodes': [
                # Intentionally reversed order in input
                {'name': 'child', 'type': 'vm', 'parent': 'parent'},
                {'name': 'parent', 'type': 'pve'}
            ]
        }
        manifest = Manifest.from_dict(data)

        assert manifest.levels[0].name == 'parent'
        assert manifest.levels[1].name == 'child'

    def test_three_level_chain(self):
        """Should order 3-level chain correctly."""
        from manifest import Manifest

        data = {
            'schema_version': 2,
            'name': 'test',
            'nodes': [
                # Reversed order
                {'name': 'leaf', 'type': 'vm', 'parent': 'middle'},
                {'name': 'root', 'type': 'pve'},
                {'name': 'middle', 'type': 'pve', 'parent': 'root'}
            ]
        }
        manifest = Manifest.from_dict(data)

        assert manifest.levels[0].name == 'root'
        assert manifest.levels[1].name == 'middle'
        assert manifest.levels[2].name == 'leaf'

    def test_flat_multiple_roots(self):
        """Multiple root nodes should all appear in levels."""
        from manifest import Manifest

        data = {
            'schema_version': 2,
            'name': 'test',
            'pattern': 'flat',
            'nodes': [
                {'name': 'vm1', 'type': 'vm'},
                {'name': 'vm2', 'type': 'vm'},
                {'name': 'vm3', 'type': 'vm'}
            ]
        }
        manifest = Manifest.from_dict(data)

        assert manifest.depth == 3
        names = [l.name for l in manifest.levels]
        assert 'vm1' in names
        assert 'vm2' in names
        assert 'vm3' in names


class TestManifestV2Serialization:
    """Test v2 manifest serialization."""

    def test_to_dict_v2(self):
        """Should serialize v2 manifest correctly."""
        from manifest import Manifest

        data = {
            'schema_version': 2,
            'name': 'test',
            'pattern': 'tiered',
            'nodes': [
                {'name': 'pve', 'type': 'pve', 'vmid': 99011},
                {'name': 'test', 'type': 'vm', 'vmid': 99021, 'parent': 'pve'}
            ]
        }
        manifest = Manifest.from_dict(data)
        result = manifest.to_dict()

        assert result['schema_version'] == 2
        assert result['pattern'] == 'tiered'
        assert len(result['nodes']) == 2
        assert result['nodes'][0]['name'] == 'pve'
        assert result['nodes'][1]['parent'] == 'pve'

    def test_json_roundtrip_v2(self):
        """Should survive JSON roundtrip for v2 manifests."""
        from manifest import Manifest

        data = {
            'schema_version': 2,
            'name': 'n2-quick-v2',
            'description': 'Test',
            'pattern': 'tiered',
            'nodes': [
                {'name': 'root-pve', 'type': 'pve', 'preset': 'vm-large', 'image': 'debian-13-pve', 'vmid': 99011},
                {'name': 'edge', 'type': 'vm', 'preset': 'vm-small', 'image': 'debian-12', 'vmid': 99021, 'parent': 'root-pve'}
            ],
            'settings': {'on_error': 'rollback'}
        }
        manifest = Manifest.from_dict(data)

        json_str = manifest.to_json()
        restored = Manifest.from_json(json_str)

        assert restored.schema_version == 2
        assert restored.name == manifest.name
        assert restored.pattern == 'tiered'
        assert len(restored.nodes) == 2
        assert restored.settings.on_error == 'rollback'


class TestManifestV1Regression:
    """Verify v1 manifests still work identically after v2 changes."""

    def test_v1_still_parses(self):
        """v1 manifest should parse exactly as before."""
        from manifest import Manifest

        data = {
            'schema_version': 1,
            'name': 'n2-quick',
            'levels': [
                {'name': 'inner', 'env': 'nested-pve', 'image': 'debian-13-pve'},
                {'name': 'leaf', 'env': 'test'}
            ]
        }
        manifest = Manifest.from_dict(data)

        assert manifest.schema_version == 1
        assert manifest.nodes is None
        assert manifest.pattern is None
        assert len(manifest.levels) == 2

    def test_v1_implicit_version(self):
        """Manifest without schema_version should default to v1."""
        from manifest import Manifest

        data = {
            'name': 'test',
            'levels': [{'name': 'level1', 'env': 'test'}]
        }
        manifest = Manifest.from_dict(data)

        assert manifest.schema_version == 1

    def test_v1_to_dict_unchanged(self):
        """v1 serialization format should be unchanged."""
        from manifest import Manifest

        data = {
            'schema_version': 1,
            'name': 'test',
            'levels': [{'name': 'l1', 'vm_preset': 'small', 'vmid': 99001, 'image': 'debian-12'}]
        }
        manifest = Manifest.from_dict(data)
        result = manifest.to_dict()

        assert result['schema_version'] == 1
        assert 'levels' in result
        assert 'nodes' not in result

    def test_v1_json_roundtrip_unchanged(self):
        """v1 JSON roundtrip should produce identical results."""
        from manifest import Manifest

        original = {
            'name': 'test',
            'levels': [
                {'name': 'inner', 'env': 'nested-pve', 'image': 'debian-13'},
                {'name': 'leaf', 'env': 'test', 'vmid_offset': 100}
            ],
            'settings': {'cleanup_on_failure': False}
        }
        manifest = Manifest.from_dict(original)
        json_str = manifest.to_json()
        restored = Manifest.from_json(json_str)

        assert restored.name == manifest.name
        assert restored.depth == manifest.depth
        assert restored.settings.cleanup_on_failure is False
