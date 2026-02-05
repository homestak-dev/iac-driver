"""Operator engine for manifest-based infrastructure orchestration.

Walks a manifest graph to execute create/destroy/test lifecycle operations
using existing tofu and proxmox actions.

Package name uses 'manifest_opr' (short for operator) to avoid collision
with Python's stdlib 'operator' module.
"""
