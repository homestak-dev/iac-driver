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
