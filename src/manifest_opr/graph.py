"""Graph module for manifest-based orchestration.

Builds an execution graph from Manifest.nodes and computes traversal
orderings for create (parents first) and destroy (children first).
"""

import logging
from collections import deque
from dataclasses import dataclass, field
from typing import Optional

from manifest import Manifest, ManifestNode, ManifestSettings

logger = logging.getLogger(__name__)


@dataclass
class ExecutionNode:
    """A node in the execution graph with parent/children edges.

    Wraps a ManifestNode and adds graph structure for traversal.

    Attributes:
        manifest_node: The underlying ManifestNode definition
        parent: Reference to parent ExecutionNode (None for root)
        children: List of child ExecutionNodes
        depth: Distance from root (0 for root nodes)
    """
    manifest_node: ManifestNode
    parent: Optional['ExecutionNode'] = None
    children: list['ExecutionNode'] = field(default_factory=list)
    depth: int = 0

    @property
    def name(self) -> str:
        return self.manifest_node.name

    @property
    def type(self) -> str:
        return self.manifest_node.type

    @property
    def is_root(self) -> bool:
        return self.parent is None

    @property
    def is_leaf(self) -> bool:
        return len(self.children) == 0

    def __repr__(self) -> str:
        return f"ExecutionNode({self.name}, type={self.type}, depth={self.depth})"


class ManifestGraph:
    """Execution graph built from a Manifest's v2 nodes.

    Provides ordered traversal for lifecycle operations:
    - create_order(): parents before children (BFS)
    - destroy_order(): children before parents (reverse BFS)
    """

    def __init__(self, manifest: Manifest):
        """Build execution graph from manifest.

        Args:
            manifest: A v2 manifest with nodes defined

        Raises:
            ValueError: If manifest has no v2 nodes
        """
        if not manifest.nodes:
            raise ValueError("ManifestGraph requires a manifest with nodes")

        self.manifest = manifest
        self._nodes: dict[str, ExecutionNode] = {}
        self._roots: list[ExecutionNode] = []
        self._build_graph(manifest.nodes)

    def _build_graph(self, nodes: list[ManifestNode]) -> None:
        """Build ExecutionNode graph from ManifestNodes."""
        # Create ExecutionNodes
        for mn in nodes:
            self._nodes[mn.name] = ExecutionNode(manifest_node=mn)

        # Wire parent/children edges and compute depths
        for mn in nodes:
            exec_node = self._nodes[mn.name]
            if mn.parent is not None:
                parent_node = self._nodes[mn.parent]
                exec_node.parent = parent_node
                parent_node.children.append(exec_node)
            else:
                self._roots.append(exec_node)

        # Compute depths via BFS from roots
        queue: deque[ExecutionNode] = deque()
        for root in self._roots:
            root.depth = 0
            queue.append(root)

        while queue:
            node = queue.popleft()
            for child in node.children:
                child.depth = node.depth + 1
                queue.append(child)

    @property
    def roots(self) -> list[ExecutionNode]:
        """Root nodes (deployed on target host)."""
        return list(self._roots)

    @property
    def max_depth(self) -> int:
        """Maximum nesting depth."""
        if not self._nodes:
            return 0
        return max(n.depth for n in self._nodes.values())

    def get_node(self, name: str) -> ExecutionNode:
        """Get an ExecutionNode by name.

        Raises:
            KeyError: If node name not found
        """
        return self._nodes[name]

    def create_order(self) -> list[ExecutionNode]:
        """Return nodes in creation order (parents before children).

        Uses BFS from roots for stable, breadth-first ordering.
        """
        ordered: list[ExecutionNode] = []
        queue: deque[ExecutionNode] = deque(self._roots)

        while queue:
            node = queue.popleft()
            ordered.append(node)
            queue.extend(node.children)

        return ordered

    def destroy_order(self) -> list[ExecutionNode]:
        """Return nodes in destruction order (children before parents).

        Reverse of create_order.
        """
        return list(reversed(self.create_order()))

    def get_parent_ip_key(self, node: ExecutionNode) -> str:
        """Get the context key for the SSH target of a node.

        Root nodes use 'ssh_host' (the target host's IP).
        Non-root nodes use '{parent_name}_ip' (the parent's IP from context).

        Args:
            node: The ExecutionNode to get parent IP key for

        Returns:
            Context key string
        """
        if node.is_root:
            return 'ssh_host'
        return f'{node.parent.name}_ip'

    def extract_subtree(self, node_name: str) -> Manifest:
        """Extract descendants of a node as a new Manifest.

        Collects all descendants of the named node. Direct children get
        parent=None (promoted to roots). Deeper descendants keep their
        parent references unchanged.

        Args:
            node_name: Name of the node whose children form the subtree

        Returns:
            New Manifest with schema_version=2 containing the subtree nodes

        Raises:
            KeyError: If node_name not found in graph
            ValueError: If node has no children
        """
        parent_node = self._nodes[node_name]
        if not parent_node.children:
            raise ValueError(f"Node '{node_name}' has no children to extract")

        # Collect all descendants via BFS
        descendants: list[ExecutionNode] = []
        queue: deque[ExecutionNode] = deque(parent_node.children)
        while queue:
            node = queue.popleft()
            descendants.append(node)
            queue.extend(node.children)

        # Build new ManifestNode list with adjusted parent references
        subtree_nodes: list[ManifestNode] = []
        for desc in descendants:
            mn = desc.manifest_node
            # Direct children of the extracted node become roots (parent=None)
            # Deeper descendants keep their parent reference unchanged
            new_parent = None if desc.parent == parent_node else mn.parent

            subtree_nodes.append(ManifestNode(
                name=mn.name,
                type=mn.type,
                spec=mn.spec,
                preset=mn.preset,
                image=mn.image,
                vmid=mn.vmid,
                disk=mn.disk,
                parent=new_parent,
                execution_mode=mn.execution_mode,
            ))

        # Copy settings from original manifest
        orig = self.manifest.settings
        settings = ManifestSettings(
            verify_ssh=orig.verify_ssh,
            cleanup_on_failure=orig.cleanup_on_failure,
            timeout_buffer=orig.timeout_buffer,
            on_error=orig.on_error,
        )

        return Manifest.from_dict({
            'schema_version': 2,
            'name': f'{node_name}-subtree',
            'description': f'Subtree of {self.manifest.name} rooted at children of {node_name}',
            'pattern': self.manifest.pattern or 'flat',
            'nodes': [n.to_dict() for n in subtree_nodes],
            'settings': {
                'verify_ssh': settings.verify_ssh,
                'cleanup_on_failure': settings.cleanup_on_failure,
                'timeout_buffer': settings.timeout_buffer,
                'on_error': settings.on_error,
            },
        })
