from .binding_layers import FP_EDGE_UID
from .binding_layers import FP_FACE_UID
from .binding_layers import FP_VERTEX_UID
from .binding_layers import binding_layers
from .binding_layers import domain_uid_state
from .binding_layers import missing_binding_layers
from .binding_model import BINDING_SCHEMA_VERSION
from .binding_model import BindingMigrationState
from .binding_model import BindingRegistry
from .binding_model import canonical_uuid
from .binding_model import normalized_boundary_key
from .binding_storage import encode_binding_registry
from .binding_storage import load_binding_registry
from .binding_storage import parse_binding_registry


def _issue(reason_code, message, region_uuid=""):
    return {
        "reason_code": str(reason_code),
        "message": str(message),
        "region_uuid": str(region_uuid or ""),
    }


def _duplicate_values(values):
    seen = set()
    duplicates = set()
    for value in values:
        if value in seen:
            duplicates.add(value)
        else:
            seen.add(value)
    return duplicates


def _mapping_issues(mapping, owners, label):
    issues = []
    duplicate_uuids = _duplicate_values(mapping.values())
    if duplicate_uuids:
        issues.append(
            _issue(
                "BINDING_IDENTITY_NOT_BIJECTIVE",
                f"{label} graph IDs map to duplicate UUIDs.",
            )
        )
    unknown = sorted(set(mapping.values()) - set(owners))
    if unknown:
        issues.append(
            _issue(
                "BINDING_IDENTITY_MISSING",
                f"{label} graph IDs reference unknown UUIDs.",
            )
        )
    return issues


def _local_id_issues(mapping, label):
    values = [int(value) for value in mapping.values()]
    issues = []
    if any(value <= 0 for value in values):
        issues.append(
            _issue(
                "BINDING_LOCAL_ID_INVALID",
                f"{label} local IDs must be positive.",
            )
        )
    if _duplicate_values(values):
        issues.append(
            _issue(
                "BINDING_LOCAL_ID_COLLISION",
                f"{label} local IDs are not unique.",
            )
        )
    return issues


def _allocator_issues(registry):
    checks = (
        ("GUIDE_NODE", registry.guide_node_local_ids.values()),
        ("GUIDE_EDGE", registry.guide_edge_local_ids.values()),
        ("ANCHOR", registry.anchor_local_ids.values()),
        (
            "REGION",
            (region.region_local_id for region in registry.regions.values()),
        ),
        ("BOUNDARY", registry.boundary_local_ids.values()),
        ("SOLVER_KIND", registry.solver_kind_local_ids.values()),
    )
    issues = []
    for domain, values in checks:
        values = tuple(int(value) for value in values)
        maximum = max(values, default=0)
        field_name = registry.allocator._FIELD_BY_DOMAIN[domain]
        if int(getattr(registry.allocator, field_name)) <= maximum:
            issues.append(
                _issue(
                    "BINDING_ALLOCATOR_REUSE_RISK",
                    f"{domain} allocator does not exceed assigned IDs.",
                )
            )
    return issues


def _registry_issues(registry):
    issues = []
    if int(registry.schema_version) != BINDING_SCHEMA_VERSION:
        issues.append(
            _issue(
                "BINDING_SCHEMA_MISMATCH",
                "The BindingRegistry schema is unsupported.",
            )
        )
    try:
        canonical_uuid(registry.project_uuid, "BindingRegistry project UUID")
    except Exception as exc:
        issues.append(_issue("BINDING_PROJECT_UUID_INVALID", str(exc)))

    valid_states = {value.value for value in BindingMigrationState}
    if str(registry.migration_state) not in valid_states:
        issues.append(
            _issue(
                "BINDING_MIGRATION_STATE_INVALID",
                "The BindingRegistry migration state is unknown.",
            )
        )

    for mapping, label in (
        (registry.guide_node_local_ids, "GuideNode"),
        (registry.guide_edge_local_ids, "GuideEdge"),
        (registry.anchor_local_ids, "Anchor"),
        (registry.boundary_local_ids, "Boundary"),
        (registry.solver_kind_local_ids, "Solver kind"),
    ):
        issues.extend(_local_id_issues(mapping, label))
    issues.extend(
        _mapping_issues(
            registry.guide_node_uuid_by_graph_id,
            registry.guide_node_local_ids,
            "GuideNode",
        )
    )
    issues.extend(
        _mapping_issues(
            registry.guide_edge_uuid_by_graph_id,
            registry.guide_edge_local_ids,
            "GuideEdge",
        )
    )

    anchor_values = tuple(registry.anchor_uuid_by_key.values())
    if _duplicate_values(anchor_values):
        issues.append(
            _issue(
                "BINDING_IDENTITY_NOT_BIJECTIVE",
                "Anchor keys map to duplicate UUIDs.",
            )
        )
    if set(anchor_values) - set(registry.anchor_local_ids):
        issues.append(
            _issue(
                "BINDING_IDENTITY_MISSING",
                "Anchor keys reference unknown UUIDs.",
            )
        )

    region_local_ids = [
        int(region.region_local_id) for region in registry.regions.values()
    ]
    if _duplicate_values(region_local_ids):
        issues.append(
            _issue(
                "BINDING_LOCAL_ID_COLLISION",
                "Region local IDs are not unique.",
            )
        )
    if _duplicate_values(registry.region_uuid_by_cycle_key.values()):
        issues.append(
            _issue(
                "BINDING_REGION_CYCLE_COLLISION",
                "Multiple cycle keys map to the same Region UUID.",
            )
        )
    if set(registry.region_uuid_by_cycle_key.values()) - set(
        registry.regions
    ):
        issues.append(
            _issue(
                "BINDING_REGION_MISSING",
                "A cycle key references an unknown Region UUID.",
            )
        )

    for region_uuid, region in registry.regions.items():
        if region_uuid != region.region_uuid:
            issues.append(
                _issue(
                    "BINDING_REGION_KEY_MISMATCH",
                    "The Region dictionary key differs from its record UUID.",
                    region_uuid,
                )
            )
        if region.project_uuid != registry.project_uuid:
            issues.append(
                _issue(
                    "BINDING_PROJECT_UUID_MISMATCH",
                    "A Region belongs to a different project UUID.",
                    region_uuid,
                )
            )
        if region.cycle_key and registry.region_uuid_by_cycle_key.get(
            region.cycle_key
        ) != region_uuid:
            issues.append(
                _issue(
                    "BINDING_REGION_CYCLE_MISMATCH",
                    "The Region cycle map is incomplete or inconsistent.",
                    region_uuid,
                )
            )
        if set(region.guide_node_to_vertex_uid) - set(
            registry.guide_node_local_ids
        ):
            issues.append(
                _issue(
                    "BINDING_GUIDE_NODE_MISSING",
                    "A Region references an unknown GuideNode UUID.",
                    region_uuid,
                )
            )
        if set(region.guide_edge_to_ordered_vertex_uids) - set(
            registry.guide_edge_local_ids
        ):
            issues.append(
                _issue(
                    "BINDING_GUIDE_EDGE_MISSING",
                    "A Region references an unknown GuideEdge UUID.",
                    region_uuid,
                )
            )
        mapped_vertices = set(region.guide_node_to_vertex_uid.values())
        mapped_vertices.update(
            value
            for values in region.guide_edge_to_ordered_vertex_uids.values()
            for value in values
        )
        if mapped_vertices - set(region.vertex_uids):
            issues.append(
                _issue(
                    "BINDING_VERTEX_OWNERSHIP_MISMATCH",
                    "A guide mapping references a vertex outside its Region.",
                    region_uuid,
                )
            )
        for key in tuple(region.outer_boundary_keys) + tuple(
            region.shared_boundary_keys
        ):
            if key not in registry.boundary_bindings:
                issues.append(
                    _issue(
                        "BINDING_BOUNDARY_MISSING",
                        "A Region references an unknown boundary.",
                        region_uuid,
                    )
                )

    for key, value in registry.boundary_bindings.items():
        if key not in registry.boundary_local_ids:
            issues.append(
                _issue(
                    "BINDING_BOUNDARY_LOCAL_ID_MISSING",
                    "A boundary has no local ID entry.",
                )
            )
        elif int(value["local_id"]) != int(
            registry.boundary_local_ids[key]
        ):
            issues.append(
                _issue(
                    "BINDING_BOUNDARY_LOCAL_ID_MISMATCH",
                    "A boundary local ID differs between indexes.",
                )
            )
        owners = tuple(value.get("region_uuids", ()))
        if len(owners) not in {1, 2} or set(owners) - set(registry.regions):
            issues.append(
                _issue(
                    "BINDING_BOUNDARY_OWNERSHIP_INVALID",
                    "A boundary must reference one or two known Regions.",
                )
            )
        try:
            expected, _forward = normalized_boundary_key(
                registry.project_uuid,
                value.get("guide_edge_uuids", ()),
                value.get("endpoint_node_uuids", ()),
                value.get("segment_count", 0),
            )
        except Exception as exc:
            issues.append(_issue("BINDING_BOUNDARY_INVALID", str(exc)))
        else:
            if expected != key:
                issues.append(
                    _issue(
                        "BINDING_BOUNDARY_KEY_MISMATCH",
                        "A boundary key does not match its canonical identity.",
                    )
                )

    retired_pairs = (
        (registry.regions, registry.retired_region_uuids, "Region"),
        (
            registry.guide_node_local_ids,
            registry.retired_guide_node_uuids,
            "GuideNode",
        ),
        (
            registry.guide_edge_local_ids,
            registry.retired_guide_edge_uuids,
            "GuideEdge",
        ),
        (registry.anchor_local_ids, registry.retired_anchor_uuids, "Anchor"),
        (
            registry.boundary_bindings,
            registry.retired_boundary_keys,
            "Boundary",
        ),
    )
    for active, retired, label in retired_pairs:
        if set(active).intersection(retired):
            issues.append(
                _issue(
                    "BINDING_RETIRED_ID_REUSED",
                    f"A retired {label} identity is active again.",
                )
            )
    issues.extend(_allocator_issues(registry))
    return issues


def _mesh_issues(registry, bm):
    issues = []
    missing = missing_binding_layers(bm)
    if missing:
        return [
            _issue(
                "BINDING_LAYER_MISSING",
                f"Missing CustomData layers: {missing}",
            )
        ]
    layers = binding_layers(bm)
    states = {
        "VERTEX": domain_uid_state(
            bm.verts,
            layers["verts"][FP_VERTEX_UID],
        ),
        "EDGE": domain_uid_state(
            bm.edges,
            layers["edges"][FP_EDGE_UID],
        ),
        "FACE": domain_uid_state(
            bm.faces,
            layers["faces"][FP_FACE_UID],
        ),
    }
    for domain, state in states.items():
        if state["duplicates"]:
            issues.append(
                _issue(
                    "BINDING_UID_COLLISION",
                    f"Duplicate {domain.lower()} UIDs were found.",
                )
            )
    available = {
        domain: set(state["by_uid"]) for domain, state in states.items()
    }
    for region_uuid, region in registry.regions.items():
        for domain, expected in (
            ("VERTEX", region.vertex_uids),
            ("EDGE", region.edge_uids),
            ("FACE", region.face_uids),
        ):
            if set(expected) - available[domain]:
                issues.append(
                    _issue(
                        "BINDING_MESH_ELEMENT_MISSING",
                        f"A bound {domain.lower()} UID is missing from the mesh.",
                        region_uuid,
                    )
                )
        face_layer = layers["faces"][FP_FACE_UID]
        for face in bm.faces:
            if int(face[face_layer]) in region.face_uids and len(face.verts) != 4:
                issues.append(
                    _issue(
                        "BINDING_NON_QUAD_FACE",
                        "A bound Region contains a non-quad face.",
                        region_uuid,
                    )
                )
    return issues


def audit_binding_registry(registry, bm=None):
    if not isinstance(registry, BindingRegistry):
        registry = parse_binding_registry(registry)
    before = encode_binding_registry(registry)
    issues = _registry_issues(registry)
    if bm is not None:
        issues.extend(_mesh_issues(registry, bm))
    after = encode_binding_registry(registry)
    if before != after:
        issues.append(
            _issue(
                "BINDING_AUDIT_MUTATED_STATE",
                "The read-only binding audit changed registry state.",
            )
        )
    return {
        "status": "PASS" if not issues else "FAIL",
        "issue_count": len(issues),
        "issues": issues,
        "schema_version": int(registry.schema_version),
        "project_uuid": str(registry.project_uuid),
        "region_count": len(registry.regions),
        "revision": int(registry.revision),
    }


def audit_binding_registry_object(obj, bm=None):
    registry = load_binding_registry(obj)
    if registry is None:
        return {
            "status": "FOUNDATION_READY_NOT_WIRED",
            "issue_count": 0,
            "issues": [],
            "schema_version": BINDING_SCHEMA_VERSION,
            "project_uuid": "",
            "region_count": 0,
            "revision": 0,
        }
    return audit_binding_registry(registry, bm=bm)
