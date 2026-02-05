"""Manifest loading and validation for infrastructure orchestration.

Manifests define deployment topologies for VM/PVE provisioning.
They reference site-config entities (envs, vms, presets, specs) via foreign keys.

Schema v1: Linear levels array (v0.39+) - used by recursive-pve scenarios
Schema v2: Graph-based nodes with parent references (#143) - used by operator engine
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
SUPPORTED_SCHEMA_VERSIONS = {1, 2}


@dataclass
class ManifestLevel:
    """A single level in the recursion manifest.

    Supports three modes:
    1. Preset mode (simplest): name + vm_preset + vmid + image
       - vm_preset references vms/presets/*.yaml for resources (cores/memory/disk)
       - image specifies the base image
    2. Template mode: name + template + vmid
       - template references vms/*.yaml for resources
    3. Env mode (legacy): name + env
       - env references envs/*.yaml which contains VM definitions

    Attributes:
        name: VM hostname (inline modes) or level identifier (env mode)
        vm_preset: FK to site-config/vms/presets/*.yaml (vm_preset mode)
        template: FK to site-config/vms/*.yaml (template mode)
        vmid: Explicit VM ID (inline modes)
        env: FK to site-config/envs/*.yaml (env mode, optional)
        image: Image name (required for vm_preset mode, optional override for others)
        vmid_offset: Offset from env's vmid_base (env mode only, deprecated)
        post_scenario: Optional scenario to run after bootstrap
        post_scenario_args: Arguments for post_scenario
    """
    name: str
    vm_preset: Optional[str] = None
    template: Optional[str] = None
    vmid: Optional[int] = None
    env: Optional[str] = None
    image: Optional[str] = None
    vmid_offset: int = 0
    post_scenario: Optional[str] = None
    post_scenario_args: list[str] = field(default_factory=list)

    @property
    def is_inline(self) -> bool:
        """True if using inline mode (vm_preset or template specified, no env)."""
        return (self.vm_preset is not None or self.template is not None) and self.env is None

    @property
    def is_vm_preset_mode(self) -> bool:
        """True if using vm_preset mode (vm_preset specified, no template)."""
        return self.vm_preset is not None and self.template is None and self.env is None

    @property
    def vm_name(self) -> str:
        """Get the VM hostname for this level.

        In inline mode, this is the level name.
        In env mode, this must be resolved from the env file.
        """
        if self.is_inline:
            return self.name
        # In env mode, caller must resolve from env file
        # Return name as fallback (may not match actual VM name)
        return self.name

    @classmethod
    def from_dict(cls, data: dict) -> 'ManifestLevel':
        """Create ManifestLevel from dictionary."""
        return cls(
            name=data['name'],
            vm_preset=data.get('vm_preset'),
            template=data.get('template'),
            vmid=data.get('vmid'),
            env=data.get('env'),
            image=data.get('image'),
            vmid_offset=data.get('vmid_offset', 0),
            post_scenario=data.get('post_scenario'),
            post_scenario_args=data.get('post_scenario_args', [])
        )


@dataclass
class ManifestNode:
    """A node in a v2 graph-based manifest.

    Nodes define VMs/CTs with parent references forming a deployment tree.
    parent=None means the node is deployed on the target host (root node).

    Attributes:
        name: Node identifier (VM hostname and context key prefix)
        type: Node type (vm, ct, pve)
        spec: FK to v2/specs/{value}.yaml
        preset: FK to v2/presets/{value}.yaml (vm- prefixed)
        image: Cloud image name
        vmid: Explicit VM ID
        disk: Disk size override in GB
        parent: FK to another node name (None = root node)
        execution_mode: Per-node execution mode override (push/pull)
    """
    name: str
    type: str
    spec: Optional[str] = None
    preset: Optional[str] = None
    image: Optional[str] = None
    vmid: Optional[int] = None
    disk: Optional[int] = None
    parent: Optional[str] = None
    execution_mode: Optional[str] = None

    @classmethod
    def from_dict(cls, data: dict) -> 'ManifestNode':
        """Create ManifestNode from dictionary."""
        execution = data.get('execution', {})
        return cls(
            name=data['name'],
            type=data['type'],
            spec=data.get('spec'),
            preset=data.get('preset'),
            image=data.get('image'),
            vmid=data.get('vmid'),
            disk=data.get('disk'),
            parent=data.get('parent'),
            execution_mode=execution.get('mode') if execution else None,
        )

    def to_dict(self) -> dict:
        """Convert to dictionary for serialization."""
        d: dict[str, Any] = {
            'name': self.name,
            'type': self.type,
        }
        if self.spec is not None:
            d['spec'] = self.spec
        if self.preset is not None:
            d['preset'] = self.preset
        if self.image is not None:
            d['image'] = self.image
        if self.vmid is not None:
            d['vmid'] = self.vmid
        if self.disk is not None:
            d['disk'] = self.disk
        if self.parent is not None:
            d['parent'] = self.parent
        if self.execution_mode is not None:
            d['execution'] = {'mode': self.execution_mode}
        return d


@dataclass
class ManifestSettings:
    """Optional settings for manifest execution.

    Attributes:
        verify_ssh: Verify SSH at each level (default: True)
        cleanup_on_failure: Destroy levels on failure (default: True)
        timeout_buffer: Seconds to subtract per level (default: 60)
        on_error: Error handling strategy (default: 'stop')
    """
    verify_ssh: bool = True
    cleanup_on_failure: bool = True
    timeout_buffer: int = 60
    on_error: str = 'stop'

    @classmethod
    def from_dict(cls, data: Optional[dict]) -> 'ManifestSettings':
        """Create ManifestSettings from dictionary."""
        if not data:
            return cls()
        return cls(
            verify_ssh=data.get('verify_ssh', True),
            cleanup_on_failure=data.get('cleanup_on_failure', True),
            timeout_buffer=data.get('timeout_buffer', 60),
            on_error=data.get('on_error', 'stop'),
        )


@dataclass
class Manifest:
    """Infrastructure deployment manifest.

    Schema v1: Defines recursion levels for recursive-pve scenarios.
    Schema v2: Defines a graph of nodes with parent references for the operator engine.

    Attributes:
        schema_version: Manifest schema version (1 or 2)
        name: Human-readable manifest name
        description: Optional description
        levels: Ordered list of recursion levels (v1, or converted from v2 nodes)
        settings: Optional execution settings
        source_path: Path where manifest was loaded from (for debugging)
        pattern: Topology shape (v2 only: 'flat' or 'tiered')
        execution_mode: Default execution mode (v2 only: 'push' or 'pull')
        nodes: Graph-based node definitions (v2 only)
    """
    schema_version: int
    name: str
    levels: list[ManifestLevel]
    description: str = ''
    settings: ManifestSettings = field(default_factory=ManifestSettings)
    source_path: Optional[Path] = None
    pattern: Optional[str] = None
    execution_mode: str = 'push'
    nodes: Optional[list[ManifestNode]] = None

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
        if self.schema_version == 2 and self.nodes is not None:
            # Serialize as v2 format
            result: dict[str, Any] = {
                'schema_version': 2,
                'name': self.name,
                'description': self.description,
                'pattern': self.pattern or 'flat',
                'nodes': [n.to_dict() for n in self.nodes],
                'settings': {
                    'verify_ssh': self.settings.verify_ssh,
                    'cleanup_on_failure': self.settings.cleanup_on_failure,
                    'timeout_buffer': self.settings.timeout_buffer,
                    'on_error': self.settings.on_error,
                }
            }
            if self.execution_mode != 'push':
                result['execution'] = {'default_mode': self.execution_mode}
            return result

        # v1 format
        levels_data = []
        for level in self.levels:
            level_dict: dict[str, Any] = {
                'name': level.name,
                'post_scenario': level.post_scenario,
                'post_scenario_args': level.post_scenario_args
            }
            # Include mode-specific fields
            if level.vm_preset is not None:
                level_dict['vm_preset'] = level.vm_preset
            if level.template is not None:
                level_dict['template'] = level.template
            if level.vmid is not None:
                level_dict['vmid'] = level.vmid
            if level.env is not None:
                level_dict['env'] = level.env
            if level.image is not None:
                level_dict['image'] = level.image
            if level.vmid_offset != 0:
                level_dict['vmid_offset'] = level.vmid_offset
            levels_data.append(level_dict)

        return {
            'schema_version': self.schema_version,
            'name': self.name,
            'description': self.description,
            'levels': levels_data,
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

        if schema_version == 2:
            return cls._from_dict_v2(data, source_path)

        return cls._from_dict_v1(data, source_path)

    @classmethod
    def _from_dict_v1(cls, data: dict, source_path: Optional[Path] = None) -> 'Manifest':
        """Parse v1 manifest (linear levels array)."""
        if 'levels' not in data:
            raise ConfigError("Manifest missing required field: levels")
        if not data['levels']:
            raise ConfigError("Manifest must have at least one level")

        # Parse levels
        levels = []
        for i, level_data in enumerate(data['levels']):
            if 'name' not in level_data:
                raise ConfigError(f"Level {i} missing required field: name")
            # Require one of: vm_preset, template (inline modes) or env (legacy mode)
            has_vm_preset = 'vm_preset' in level_data
            has_template = 'template' in level_data
            has_env = 'env' in level_data
            if not has_vm_preset and not has_template and not has_env:
                raise ConfigError(
                    f"Level {i} ({level_data.get('name', 'unnamed')}) requires 'vm_preset', 'template', or 'env'"
                )
            levels.append(ManifestLevel.from_dict(level_data))

        return cls(
            schema_version=1,
            name=data['name'],
            description=data.get('description', ''),
            levels=levels,
            settings=ManifestSettings.from_dict(data.get('settings')),
            source_path=source_path
        )

    @classmethod
    def _from_dict_v2(cls, data: dict, source_path: Optional[Path] = None) -> 'Manifest':
        """Parse v2 manifest (graph-based nodes with parent references)."""
        if 'nodes' not in data:
            raise ConfigError("Manifest v2 missing required field: nodes")
        if not data['nodes']:
            raise ConfigError("Manifest v2 must have at least one node")

        # Parse nodes
        nodes = []
        for i, node_data in enumerate(data['nodes']):
            if 'name' not in node_data:
                raise ConfigError(f"Node {i} missing required field: name")
            if 'type' not in node_data:
                raise ConfigError(f"Node {i} ({node_data.get('name', 'unnamed')}) missing required field: type")
            nodes.append(ManifestNode.from_dict(node_data))

        # Validate graph structure
        _validate_graph(nodes)

        # Convert nodes to levels via topological sort for backward compat
        levels = _nodes_to_levels(nodes)

        execution = data.get('execution', {})

        return cls(
            schema_version=2,
            name=data['name'],
            description=data.get('description', ''),
            levels=levels,
            settings=ManifestSettings.from_dict(data.get('settings')),
            source_path=source_path,
            pattern=data.get('pattern', 'flat'),
            execution_mode=execution.get('default_mode', 'push'),
            nodes=nodes,
        )

    @classmethod
    def from_json(cls, json_str: str) -> 'Manifest':
        """Create Manifest from JSON string."""
        try:
            data = json.loads(json_str)
        except json.JSONDecodeError as e:
            raise ConfigError(f"Invalid manifest JSON: {e}")
        return cls.from_dict(data)


def _validate_graph(nodes: list[ManifestNode]) -> None:
    """Validate the graph structure of v2 manifest nodes.

    Checks for:
    - Duplicate node names
    - Dangling parent references
    - Cycles in the parent graph

    Raises:
        ConfigError: If validation fails
    """
    # Check for duplicate names
    names = [n.name for n in nodes]
    seen: set[str] = set()
    for name in names:
        if name in seen:
            raise ConfigError(f"Duplicate node name: '{name}'")
        seen.add(name)

    name_set = set(names)

    # Check for dangling parent references
    for node in nodes:
        if node.parent is not None and node.parent not in name_set:
            raise ConfigError(
                f"Node '{node.name}' references unknown parent '{node.parent}'"
            )

    # Check for cycles using DFS
    # Build adjacency: child -> parent
    visited: set[str] = set()
    in_stack: set[str] = set()
    parent_map = {n.name: n.parent for n in nodes}

    def _has_cycle(name: str) -> bool:
        if name in in_stack:
            return True
        if name in visited:
            return False
        visited.add(name)
        in_stack.add(name)
        parent = parent_map.get(name)
        if parent is not None and _has_cycle(parent):
            return True
        in_stack.discard(name)
        return False

    for node in nodes:
        if _has_cycle(node.name):
            raise ConfigError(f"Cycle detected in node graph involving '{node.name}'")


def _nodes_to_levels(nodes: list[ManifestNode]) -> list[ManifestLevel]:
    """Convert v2 graph nodes to v1 levels via topological sort.

    Produces parent-before-child ordering suitable for construction.
    Each ManifestNode is converted to a ManifestLevel with vm_preset mode.

    For PVE-type nodes, post_scenario is set to 'pve-setup'.
    """
    # Build parent->children map for topo sort
    children: dict[Optional[str], list[ManifestNode]] = {}
    for node in nodes:
        children.setdefault(node.parent, []).append(node)

    # BFS from roots (parent=None) for stable topological order
    ordered: list[ManifestNode] = []
    queue = list(children.get(None, []))
    while queue:
        current = queue.pop(0)
        ordered.append(current)
        queue.extend(children.get(current.name, []))

    # Convert to ManifestLevel
    levels = []
    for node in ordered:
        # Map v2 preset (vm-prefixed) to v1 preset (no prefix)
        vm_preset = node.preset
        if vm_preset and vm_preset.startswith('vm-'):
            vm_preset = vm_preset[3:]  # Strip 'vm-' prefix

        # PVE nodes get pve-setup as post_scenario
        post_scenario = None
        post_scenario_args: list[str] = []
        if node.type == 'pve':
            post_scenario = 'pve-setup'
            post_scenario_args = ['--local', '--skip-preflight']

        levels.append(ManifestLevel(
            name=node.name,
            vm_preset=vm_preset,
            vmid=node.vmid,
            image=node.image,
            post_scenario=post_scenario,
            post_scenario_args=post_scenario_args,
        ))

    return levels


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
