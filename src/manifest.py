"""Manifest loading and validation for recursive PVE scenarios.

Manifests define recursion levels for multi-level nested PVE deployments.
They reference existing site-config entities (envs, vms) via foreign keys.

Schema v1: Linear levels array (v0.39+)
Schema v2: Tree structure with parent references (future, #115)
"""

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

try:
    import yaml
except ImportError:
    yaml = None  # type: ignore[assignment]

from config import ConfigError, get_site_config_dir

logger = logging.getLogger(__name__)

# Default manifest name when none specified
DEFAULT_MANIFEST = 'n2-quick'

# Supported schema versions
SUPPORTED_SCHEMA_VERSIONS = {1}


@dataclass
class ManifestLevel:
    """A single level in the recursion manifest.

    Attributes:
        name: Level identifier (used in context keys, e.g., 'inner-pve')
        env: FK to site-config/envs/*.yaml
        image: Optional image override (FK to packer image)
        vmid_offset: Optional offset from env's vmid_base
        post_scenario: Optional scenario to run after bootstrap
        post_scenario_args: Arguments for post_scenario
    """
    name: str
    env: str
    image: Optional[str] = None
    vmid_offset: int = 0
    post_scenario: Optional[str] = None
    post_scenario_args: list[str] = field(default_factory=list)

    @classmethod
    def from_dict(cls, data: dict) -> 'ManifestLevel':
        """Create ManifestLevel from dictionary."""
        return cls(
            name=data['name'],
            env=data['env'],
            image=data.get('image'),
            vmid_offset=data.get('vmid_offset', 0),
            post_scenario=data.get('post_scenario'),
            post_scenario_args=data.get('post_scenario_args', [])
        )


@dataclass
class ManifestSettings:
    """Optional settings for manifest execution.

    Attributes:
        verify_ssh: Verify SSH at each level (default: True)
        cleanup_on_failure: Destroy levels on failure (default: True)
        timeout_buffer: Seconds to subtract per level (default: 60)
    """
    verify_ssh: bool = True
    cleanup_on_failure: bool = True
    timeout_buffer: int = 60

    @classmethod
    def from_dict(cls, data: Optional[dict]) -> 'ManifestSettings':
        """Create ManifestSettings from dictionary."""
        if not data:
            return cls()
        return cls(
            verify_ssh=data.get('verify_ssh', True),
            cleanup_on_failure=data.get('cleanup_on_failure', True),
            timeout_buffer=data.get('timeout_buffer', 60)
        )


@dataclass
class Manifest:
    """Recursion manifest for multi-level nested PVE.

    Defines the levels of recursion for recursive-pve scenarios.
    Each level references site-config entities (envs, vms) via FK.

    Attributes:
        schema_version: Manifest schema version (default: 1)
        name: Human-readable manifest name
        description: Optional description
        levels: Ordered list of recursion levels (first = outermost)
        settings: Optional execution settings
        source_path: Path where manifest was loaded from (for debugging)
    """
    schema_version: int
    name: str
    levels: list[ManifestLevel]
    description: str = ''
    settings: ManifestSettings = field(default_factory=ManifestSettings)
    source_path: Optional[Path] = None

    @property
    def depth(self) -> int:
        """Number of recursion levels."""
        return len(self.levels)

    @property
    def is_leaf(self) -> bool:
        """True if this manifest has only one level (leaf node)."""
        return len(self.levels) == 1

    def get_current_level(self) -> ManifestLevel:
        """Get the first (current) level."""
        if not self.levels:
            raise ConfigError("Manifest has no levels")
        return self.levels[0]

    def get_remaining_manifest(self) -> 'Manifest':
        """Get manifest with levels[1:] for recursion.

        Returns a new Manifest with the first level removed,
        suitable for passing to inner recursive execution.
        """
        if len(self.levels) <= 1:
            raise ConfigError("Cannot get remaining manifest: already at leaf level")

        return Manifest(
            schema_version=self.schema_version,
            name=f"{self.name}[1:]",
            description=self.description,
            levels=[ManifestLevel.from_dict(vars(l)) for l in self.levels[1:]],
            settings=ManifestSettings(
                verify_ssh=self.settings.verify_ssh,
                cleanup_on_failure=self.settings.cleanup_on_failure,
                timeout_buffer=self.settings.timeout_buffer
            ),
            source_path=self.source_path
        )

    def to_dict(self) -> dict:
        """Convert manifest to dictionary (for JSON serialization)."""
        return {
            'schema_version': self.schema_version,
            'name': self.name,
            'description': self.description,
            'levels': [
                {
                    'name': level.name,
                    'env': level.env,
                    'image': level.image,
                    'vmid_offset': level.vmid_offset,
                    'post_scenario': level.post_scenario,
                    'post_scenario_args': level.post_scenario_args
                }
                for level in self.levels
            ],
            'settings': {
                'verify_ssh': self.settings.verify_ssh,
                'cleanup_on_failure': self.settings.cleanup_on_failure,
                'timeout_buffer': self.settings.timeout_buffer
            }
        }

    def to_json(self) -> str:
        """Serialize manifest to JSON string."""
        return json.dumps(self.to_dict())

    @classmethod
    def from_dict(cls, data: dict, source_path: Optional[Path] = None) -> 'Manifest':
        """Create Manifest from dictionary.

        Args:
            data: Manifest data dictionary
            source_path: Optional source path for error messages

        Returns:
            Validated Manifest instance

        Raises:
            ConfigError: If manifest is invalid
        """
        # Validate schema version
        schema_version = data.get('schema_version', 1)
        if schema_version not in SUPPORTED_SCHEMA_VERSIONS:
            raise ConfigError(
                f"Unsupported manifest schema version: {schema_version}. "
                f"Supported versions: {sorted(SUPPORTED_SCHEMA_VERSIONS)}"
            )

        # Validate required fields
        if 'name' not in data:
            raise ConfigError("Manifest missing required field: name")
        if 'levels' not in data:
            raise ConfigError("Manifest missing required field: levels")
        if not data['levels']:
            raise ConfigError("Manifest must have at least one level")

        # Parse levels
        levels = []
        for i, level_data in enumerate(data['levels']):
            if 'name' not in level_data:
                raise ConfigError(f"Level {i} missing required field: name")
            if 'env' not in level_data:
                raise ConfigError(f"Level {i} ({level_data.get('name', 'unnamed')}) missing required field: env")
            levels.append(ManifestLevel.from_dict(level_data))

        return cls(
            schema_version=schema_version,
            name=data['name'],
            description=data.get('description', ''),
            levels=levels,
            settings=ManifestSettings.from_dict(data.get('settings')),
            source_path=source_path
        )

    @classmethod
    def from_json(cls, json_str: str) -> 'Manifest':
        """Create Manifest from JSON string."""
        try:
            data = json.loads(json_str)
        except json.JSONDecodeError as e:
            raise ConfigError(f"Invalid manifest JSON: {e}")
        return cls.from_dict(data)


class ManifestLoader:
    """Loads manifests from site-config/manifests/ directory."""

    def __init__(self, site_config_path: Optional[str] = None):
        """Initialize loader with site-config path.

        Args:
            site_config_path: Path to site-config directory. If None, uses
                              auto-discovery (env var, sibling, /opt/homestak).
        """
        if yaml is None:
            raise ConfigError("PyYAML not installed. Run: apt install python3-yaml")

        if site_config_path:
            self.site_config_dir = Path(site_config_path)
        else:
            self.site_config_dir = get_site_config_dir()

        self.manifests_dir = self.site_config_dir / 'manifests'

    def list_manifests(self) -> list[str]:
        """List available manifest names."""
        if not self.manifests_dir.exists():
            return []
        return sorted([
            f.stem for f in self.manifests_dir.glob('*.yaml')
            if f.is_file()
        ])

    def load(self, name: str) -> Manifest:
        """Load manifest by name.

        Args:
            name: Manifest name (without .yaml extension)

        Returns:
            Manifest instance

        Raises:
            ConfigError: If manifest not found or invalid
        """
        path = self.manifests_dir / f'{name}.yaml'
        if not path.exists():
            available = self.list_manifests()
            raise ConfigError(
                f"Manifest '{name}' not found at {path}. "
                f"Available: {', '.join(available) if available else 'none'}"
            )

        return self.load_file(path)

    def load_file(self, path: Path) -> Manifest:
        """Load manifest from specific file path.

        Args:
            path: Path to manifest YAML file

        Returns:
            Manifest instance

        Raises:
            ConfigError: If file not found or invalid
        """
        if not path.exists():
            raise ConfigError(f"Manifest file not found: {path}")

        try:
            with open(path, encoding='utf-8') as f:
                data = yaml.safe_load(f)
        except yaml.YAMLError as e:
            raise ConfigError(f"Invalid YAML in manifest {path}: {e}")

        if not isinstance(data, dict):
            raise ConfigError(f"Manifest {path} must be a YAML object (dict)")

        return Manifest.from_dict(data, source_path=path)

    def get_default(self) -> Manifest:
        """Get the default manifest.

        Checks for site-config/manifests/default.yaml first,
        then falls back to DEFAULT_MANIFEST.

        Returns:
            Default Manifest instance

        Raises:
            ConfigError: If no default manifest available
        """
        # Check for explicit default.yaml
        default_path = self.manifests_dir / 'default.yaml'
        if default_path.exists():
            return self.load_file(default_path)

        # Fall back to built-in default
        return self.load(DEFAULT_MANIFEST)


def load_manifest(
    name: Optional[str] = None,
    file_path: Optional[str] = None,
    json_str: Optional[str] = None,
    depth: Optional[int] = None
) -> Manifest:
    """Load manifest from various sources.

    Priority:
    1. json_str - Inline JSON (for recursion)
    2. file_path - Specific file path
    3. name - Named manifest from site-config/manifests/
    4. Default manifest

    Args:
        name: Manifest name (without .yaml extension)
        file_path: Path to manifest file
        json_str: Inline JSON manifest string
        depth: Optional depth limit (use first N levels)

    Returns:
        Manifest instance

    Raises:
        ConfigError: If manifest not found or invalid
    """
    if json_str:
        manifest = Manifest.from_json(json_str)
    elif file_path:
        loader = ManifestLoader()
        manifest = loader.load_file(Path(file_path))
    elif name:
        loader = ManifestLoader()
        manifest = loader.load(name)
    else:
        loader = ManifestLoader()
        manifest = loader.get_default()

    # Apply depth limit if specified
    if depth is not None and depth > 0:
        if depth < len(manifest.levels):
            manifest = Manifest(
                schema_version=manifest.schema_version,
                name=f"{manifest.name}[:{depth}]",
                description=manifest.description,
                levels=manifest.levels[:depth],
                settings=manifest.settings,
                source_path=manifest.source_path
            )

    return manifest
