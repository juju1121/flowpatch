from .binding_model import BindingRegistry
from .binding_model import BindingRegistryError
from .binding_model import RegionBinding
from .binding_model import canonical_boundary_record
from .binding_model import canonical_uuid
from .binding_model import new_uuid
from .binding_model import normalized_boundary_key
from .binding_model import positive_int


def _guide_value(guide, name, default=0):
    if isinstance(guide, dict):
        return guide.get(name, default)
    return getattr(guide, name, default)


def active_graph_ids(guides):
    edge_ids = {
        int(_guide_value(guide, "guide_id"))
        for guide in tuple(guides or ())
        if int(_guide_value(guide, "guide_id")) > 0
    }
    node_ids = {
        int(value)
        for guide in tuple(guides or ())
        for value in (
            _guide_value(guide, "start_node"),
            _guide_value(guide, "end_node"),
        )
        if int(value) > 0
    }
    return node_ids, edge_ids


def _fresh_uuid(uuid_factory, used, label):
    for _attempt in range(128):
        value = canonical_uuid(uuid_factory(), label)
        if value not in used:
            used.add(value)
            return value
    raise BindingRegistryError(
        "BINDING_UID_COLLISION",
        f"{label} allocation repeatedly returned an existing UUID.",
    )


def reconcile_guide_identities(registry, guides, uuid_factory=new_uuid):
    if not isinstance(registry, BindingRegistry):
        raise TypeError("registry must be a BindingRegistry")
    node_ids, edge_ids = active_graph_ids(guides)
    used = set(registry.guide_node_local_ids)
    used.update(registry.guide_edge_local_ids)
    used.update(registry.anchor_local_ids)
    used.update(registry.regions)
    used.update(registry.retired_guide_node_uuids)
    used.update(registry.retired_guide_edge_uuids)
    used.update(registry.retired_anchor_uuids)
    used.update(registry.retired_region_uuids)
    changed = False

    for graph_id in tuple(registry.guide_node_uuid_by_graph_id):
        if int(graph_id) in node_ids:
            continue
        retired = registry.guide_node_uuid_by_graph_id.pop(graph_id)
        registry.guide_node_local_ids.pop(retired, None)
        anchor_uuid = registry.anchor_uuid_by_key.pop(
            f"node:{retired}",
            None,
        )
        if anchor_uuid:
            registry.anchor_local_ids.pop(anchor_uuid, None)
            registry.retired_anchor_uuids = tuple(
                sorted(set(registry.retired_anchor_uuids) | {anchor_uuid})
            )
        registry.retired_guide_node_uuids = tuple(
            sorted(set(registry.retired_guide_node_uuids) | {retired})
        )
        changed = True

    for graph_id in sorted(node_ids):
        key = str(graph_id)
        node_uuid = registry.guide_node_uuid_by_graph_id.get(key)
        if not node_uuid:
            node_uuid = _fresh_uuid(
                uuid_factory,
                used,
                "Generated GuideNode UUID",
            )
            registry.guide_node_uuid_by_graph_id[key] = node_uuid
            changed = True
        if node_uuid not in registry.guide_node_local_ids:
            registry.guide_node_local_ids[node_uuid] = (
                registry.allocator.allocate("GUIDE_NODE")
            )
            changed = True
        anchor_key = f"node:{node_uuid}"
        if anchor_key not in registry.anchor_uuid_by_key:
            anchor_uuid = _fresh_uuid(
                uuid_factory,
                used,
                "Generated Anchor UUID",
            )
            registry.anchor_uuid_by_key[anchor_key] = anchor_uuid
            registry.anchor_local_ids[anchor_uuid] = (
                registry.allocator.allocate("ANCHOR")
            )
            changed = True

    for graph_id in tuple(registry.guide_edge_uuid_by_graph_id):
        if int(graph_id) in edge_ids:
            continue
        retired = registry.guide_edge_uuid_by_graph_id.pop(graph_id)
        registry.guide_edge_local_ids.pop(retired, None)
        registry.retired_guide_edge_uuids = tuple(
            sorted(set(registry.retired_guide_edge_uuids) | {retired})
        )
        changed = True

    for graph_id in sorted(edge_ids):
        key = str(graph_id)
        edge_uuid = registry.guide_edge_uuid_by_graph_id.get(key)
        if not edge_uuid:
            edge_uuid = _fresh_uuid(
                uuid_factory,
                used,
                "Generated GuideEdge UUID",
            )
            registry.guide_edge_uuid_by_graph_id[key] = edge_uuid
            changed = True
        if edge_uuid not in registry.guide_edge_local_ids:
            registry.guide_edge_local_ids[edge_uuid] = (
                registry.allocator.allocate("GUIDE_EDGE")
            )
            changed = True

    if changed:
        registry.revision += 1
    return changed


def upsert_grid_region(
    registry,
    cycle_key,
    vertex_uids,
    edge_uids,
    face_uids,
    guide_node_to_vertex_uid=None,
    guide_edge_to_ordered_vertex_uids=None,
    uuid_factory=new_uuid,
):
    if not isinstance(registry, BindingRegistry):
        raise TypeError("registry must be a BindingRegistry")
    cycle_key = str(cycle_key or "").strip()
    if not cycle_key:
        raise BindingRegistryError(
            "BINDING_REGION_INVALID",
            "A GRID Region requires a stable cycle key.",
        )
    used = set(registry.regions)
    used.update(registry.retired_region_uuids)
    region_uuid = registry.region_uuid_by_cycle_key.get(cycle_key)
    existing = registry.regions.get(region_uuid) if region_uuid else None
    if existing is None:
        region_uuid = _fresh_uuid(
            uuid_factory,
            used,
            "Generated Region UUID",
        )
        existing = RegionBinding(
            project_uuid=registry.project_uuid,
            region_uuid=region_uuid,
            region_local_id=registry.allocator.allocate("REGION"),
            solver_kind="GRID",
            generation=1,
            cycle_key=cycle_key,
        )
        registry.regions[region_uuid] = existing
        registry.region_uuid_by_cycle_key[cycle_key] = region_uuid
    elif str(existing.solver_kind).upper() != "GRID":
        raise BindingRegistryError(
            "BINDING_UNSUPPORTED_SOLVER",
            "CR-00 can bind only deterministic four-sided GRID Regions.",
        )
    else:
        existing.generation += 1

    existing.vertex_uids = {
        positive_int(value, "Region vertex UID") for value in vertex_uids
    }
    existing.edge_uids = {
        positive_int(value, "Region edge UID") for value in edge_uids
    }
    existing.face_uids = {
        positive_int(value, "Region face UID") for value in face_uids
    }
    existing.guide_node_to_vertex_uid = {
        canonical_uuid(key, "GuideNode UUID"): positive_int(
            value,
            "Mapped vertex UID",
        )
        for key, value in dict(guide_node_to_vertex_uid or {}).items()
    }
    existing.guide_edge_to_ordered_vertex_uids = {
        canonical_uuid(key, "GuideEdge UUID"): tuple(
            positive_int(value, "Boundary vertex UID") for value in values
        )
        for key, values in dict(
            guide_edge_to_ordered_vertex_uids or {}
        ).items()
    }
    existing.mesh_revision += 1
    existing.state = "BOUND"
    registry.revision += 1
    return existing


def register_boundary(
    registry,
    guide_edge_uuids,
    endpoint_node_uuids,
    vertex_uids,
    edge_uids,
    region_uuids,
):
    guide_edge_uuids = tuple(guide_edge_uuids)
    endpoint_node_uuids = tuple(endpoint_node_uuids)
    vertex_uids = tuple(vertex_uids)
    edge_uids = tuple(edge_uids)
    region_uuids = tuple(region_uuids)
    segment_count = len(edge_uids)
    key, forward = normalized_boundary_key(
        registry.project_uuid,
        guide_edge_uuids,
        endpoint_node_uuids,
        segment_count,
    )
    owners = tuple(
        sorted(canonical_uuid(value, "Boundary Region UUID") for value in region_uuids)
    )
    if not owners or len(owners) > 2:
        raise BindingRegistryError(
            "BINDING_BOUNDARY_MISMATCH",
            "A boundary must belong to one exterior or two shared Regions.",
        )
    unknown = [value for value in owners if value not in registry.regions]
    if unknown:
        raise BindingRegistryError(
            "BINDING_BOUNDARY_MISMATCH",
            "A boundary references an unknown Region.",
        )
    guide_edges = guide_edge_uuids
    nodes = endpoint_node_uuids
    vertices = tuple(positive_int(value, "Boundary vertex UID") for value in vertex_uids)
    edges = tuple(positive_int(value, "Boundary edge UID") for value in edge_uids)
    if not forward:
        guide_edges = tuple(reversed(guide_edges))
        nodes = tuple(reversed(nodes))
        vertices = tuple(reversed(vertices))
        edges = tuple(reversed(edges))
    local_id = registry.boundary_local_ids.get(key)
    if local_id is None:
        local_id = registry.allocator.allocate("BOUNDARY")
        registry.boundary_local_ids[key] = local_id
    record = canonical_boundary_record(
        {
            "local_id": local_id,
            "guide_edge_uuids": guide_edges,
            "endpoint_node_uuids": nodes,
            "segment_count": segment_count,
            "vertex_uids": vertices,
            "edge_uids": edges,
            "region_uuids": owners,
        }
    )
    registry.boundary_bindings[key] = record
    for region_uuid in owners:
        region = registry.regions[region_uuid]
        if len(owners) == 2:
            region.shared_boundary_keys = tuple(
                sorted(set(region.shared_boundary_keys) | {key})
            )
        else:
            region.outer_boundary_keys = tuple(
                sorted(set(region.outer_boundary_keys) | {key})
            )
    registry.revision += 1
    return key


def retire_region_binding(registry, region_uuid):
    region_uuid = canonical_uuid(region_uuid, "Region UUID")
    region = registry.regions.pop(region_uuid, None)
    if region is None:
        return False
    if region.cycle_key:
        registry.region_uuid_by_cycle_key.pop(region.cycle_key, None)
    for key in tuple(region.outer_boundary_keys) + tuple(
        region.shared_boundary_keys
    ):
        record = registry.boundary_bindings.get(key)
        if record is None:
            continue
        owners = [
            value
            for value in record.get("region_uuids", ())
            if value != region_uuid
        ]
        if owners:
            record["region_uuids"] = owners
        else:
            registry.boundary_bindings.pop(key, None)
            registry.boundary_local_ids.pop(key, None)
            registry.retired_boundary_keys = tuple(
                sorted(set(registry.retired_boundary_keys) | {key})
            )
    registry.retired_region_uuids = tuple(
        sorted(set(registry.retired_region_uuids) | {region_uuid})
    )
    registry.revision += 1
    return True
