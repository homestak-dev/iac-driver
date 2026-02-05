"""Graph module for manifest-based orchestration.

Builds an execution graph from Manifest.nodes and computes traversal
orderings for create (parents first) and destroy (children first).
"""

import logging
from collections import deque
from dataclasses import dataclass, field
from typing import Optional

from manifest import Manifest, ManifestNode

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
        if manifest.nodes is None:
            raise ValueError("ManifestGraph requires a v2 manifest with nodes")

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
