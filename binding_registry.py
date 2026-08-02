"""CR-00 BindingRegistry compatibility facade.

This module exposes the small identity foundation only. It deliberately does
not hook guide edits, mesh edits, Build, resume, or undo; those transactions
belong to CR-01 after foreground acceptance of the restored v1.4.19 behavior.
"""

from .binding_audit import audit_binding_registry
from .binding_audit import audit_binding_registry_object
from .binding_layers import EDGE_INT_LAYERS
from .binding_layers import FACE_INT_LAYERS
from .binding_layers import FP_ANCHOR_LOCAL_ID
from .binding_layers import FP_CELL_LOCAL_ID
from .binding_layers import FP_EDGE_BOUNDARY_KEY
from .binding_layers import FP_EDGE_GENERATION
from .binding_layers import FP_EDGE_REGION
from .binding_layers import FP_EDGE_ROLE
from .binding_layers import FP_EDGE_UID
from .binding_layers import FP_FACE_GENERATION
from .binding_layers import FP_FACE_REGION
from .binding_layers import FP_FACE_UID
from .binding_layers import FP_GUIDE_EDGE_LOCAL_ID
from .binding_layers import FP_GUIDE_NODE_LOCAL_ID
from .binding_layers import FP_PARAM_U
from .binding_layers import FP_PARAM_V
from .binding_layers import FP_SOLVER_KIND
from .binding_layers import FP_VERTEX_GENERATION
from .binding_layers import FP_VERTEX_REGION
from .binding_layers import FP_VERTEX_ROLE
from .binding_layers import FP_VERTEX_UID
from .binding_layers import VERTEX_FLOAT_LAYERS
from .binding_layers import VERTEX_INT_LAYERS
from .binding_layers import allocate_element_uid
from .binding_layers import binding_layers
from .binding_layers import domain_uid_state
from .binding_layers import ensure_binding_layers
from .binding_layers import missing_binding_layers
from .binding_migration import MigrationPlan
from .binding_migration import RekeyBundle
from .binding_migration import plan_legacy_migration
from .binding_migration import rekey_binding_registry
from .binding_migration import rekey_project_bundle
from .binding_model import BINDING_SCHEMA_VERSION
from .binding_model import BindingMigrationState
from .binding_model import BindingRegistry
from .binding_model import BindingRegistryError
from .binding_model import EdgeRole
from .binding_model import ElementUIDAllocator
from .binding_model import RegionBinding
from .binding_model import VertexRole
from .binding_model import canonical_boundary_record
from .binding_model import canonical_uuid
from .binding_model import new_uuid
from .binding_model import normalized_boundary_key
from .binding_model import positive_int
from .binding_storage import BINDING_REGISTRY_KEY
from .binding_storage import clone_binding_registry
from .binding_storage import encode_binding_registry
from .binding_storage import load_binding_registry
from .binding_storage import parse_binding_registry
from .binding_storage import restore_binding_property
from .binding_storage import save_binding_registry
from .binding_storage import snapshot_binding_property
from .binding_writer import active_graph_ids
from .binding_writer import reconcile_guide_identities
from .binding_writer import register_boundary
from .binding_writer import retire_region_binding
from .binding_writer import upsert_grid_region


BINDING_FOUNDATION_STATE = "FOUNDATION_READY_NOT_WIRED"
BINDING_FOUNDATION_SCOPE = (
    "Stable IDs, CustomData schema, serialization, explicit writers, "
    "copy/rekey, and read-only audits. Guide-mesh synchronization is CR-01."
)


def new_binding_registry(project_uuid):
    return BindingRegistry.empty(project_uuid)


def initialize_binding_registry(obj, project_uuid):
    """Explicitly create storage; never called by guide or mesh edits in CR-00."""
    existing = load_binding_registry(obj)
    if existing is not None:
        if existing.project_uuid != canonical_uuid(
            project_uuid,
            "Project UUID",
        ):
            raise BindingRegistryError(
                "BINDING_PROJECT_UUID_MISMATCH",
                "The stored registry belongs to a different project.",
            )
        return existing
    registry = new_binding_registry(project_uuid)
    save_binding_registry(obj, registry)
    return registry


def foundation_status():
    return {
        "state": BINDING_FOUNDATION_STATE,
        "scope": BINDING_FOUNDATION_SCOPE,
        "schema_version": BINDING_SCHEMA_VERSION,
        "guide_to_mesh_sync": False,
        "mesh_to_guide_sync": False,
    }


__all__ = (
    "BINDING_FOUNDATION_SCOPE",
    "BINDING_FOUNDATION_STATE",
    "BINDING_REGISTRY_KEY",
    "BINDING_SCHEMA_VERSION",
    "BindingMigrationState",
    "BindingRegistry",
    "BindingRegistryError",
    "EDGE_INT_LAYERS",
    "EdgeRole",
    "ElementUIDAllocator",
    "FACE_INT_LAYERS",
    "FP_ANCHOR_LOCAL_ID",
    "FP_CELL_LOCAL_ID",
    "FP_EDGE_BOUNDARY_KEY",
    "FP_EDGE_GENERATION",
    "FP_EDGE_REGION",
    "FP_EDGE_ROLE",
    "FP_EDGE_UID",
    "FP_FACE_GENERATION",
    "FP_FACE_REGION",
    "FP_FACE_UID",
    "FP_GUIDE_EDGE_LOCAL_ID",
    "FP_GUIDE_NODE_LOCAL_ID",
    "FP_PARAM_U",
    "FP_PARAM_V",
    "FP_SOLVER_KIND",
    "FP_VERTEX_GENERATION",
    "FP_VERTEX_REGION",
    "FP_VERTEX_ROLE",
    "FP_VERTEX_UID",
    "MigrationPlan",
    "RegionBinding",
    "RekeyBundle",
    "VERTEX_FLOAT_LAYERS",
    "VERTEX_INT_LAYERS",
    "VertexRole",
    "active_graph_ids",
    "allocate_element_uid",
    "audit_binding_registry",
    "audit_binding_registry_object",
    "binding_layers",
    "canonical_boundary_record",
    "canonical_uuid",
    "clone_binding_registry",
    "domain_uid_state",
    "encode_binding_registry",
    "ensure_binding_layers",
    "foundation_status",
    "initialize_binding_registry",
    "load_binding_registry",
    "missing_binding_layers",
    "new_binding_registry",
    "new_uuid",
    "normalized_boundary_key",
    "parse_binding_registry",
    "plan_legacy_migration",
    "positive_int",
    "reconcile_guide_identities",
    "register_boundary",
    "rekey_binding_registry",
    "rekey_project_bundle",
    "restore_binding_property",
    "retire_region_binding",
    "save_binding_registry",
    "snapshot_binding_property",
    "upsert_grid_region",
)
