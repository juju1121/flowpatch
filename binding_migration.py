from copy import deepcopy
from dataclasses import dataclass

from .binding_model import BindingMigrationState
from .binding_model import BindingRegistryError
from .binding_model import canonical_uuid
from .binding_model import new_uuid
from .binding_model import normalized_boundary_key
from .binding_storage import clone_binding_registry


@dataclass(frozen=True)
class MigrationPlan:
    state: str
    can_apply: bool
    reason_code: str
    message: str


@dataclass(frozen=True)
class RekeyBundle:
    registry: object
    built_cells: tuple
    uuid_maps: dict


def plan_legacy_migration(has_registry, has_guides, has_mesh):
    if has_registry:
        return MigrationPlan(
            BindingMigrationState.CURRENT.value,
            True,
            "CURRENT_REGISTRY",
            "The project already contains a current BindingRegistry.",
        )
    if has_guides:
        return MigrationPlan(
            BindingMigrationState.NEEDS_REBUILD_FROM_GUIDES.value,
            False,
            "GUIDE_REBUILD_REQUIRED",
            "Legacy guides are retained but must be rebuilt in CR-01.",
        )
    if has_mesh:
        return MigrationPlan(
            BindingMigrationState.UNBOUND_MESH_ONLY.value,
            False,
            "UNBOUND_MESH_ONLY",
            "Mesh geometry has no deterministic guide ownership.",
        )
    return MigrationPlan(
        BindingMigrationState.CURRENT.value,
        True,
        "EMPTY_PROJECT",
        "The empty project can initialize a new BindingRegistry.",
    )


def _uuid_map(values, uuid_factory, used, label):
    result = {}
    for old_value in values:
        for _attempt in range(128):
            candidate = canonical_uuid(uuid_factory(), label)
            if candidate not in used:
                used.add(candidate)
                result[old_value] = candidate
                break
        else:
            raise BindingRegistryError(
                "BINDING_UID_COLLISION",
                f"{label} allocation repeatedly returned an existing UUID.",
            )
    return result


def rekey_binding_registry(registry, project_uuid, uuid_factory=new_uuid):
    registry = clone_binding_registry(registry)
    project_uuid = canonical_uuid(project_uuid, "Copied project UUID")
    used = {project_uuid}
    node_map = _uuid_map(
        registry.guide_node_local_ids,
        uuid_factory,
        used,
        "Copied GuideNode UUID",
    )
    edge_map = _uuid_map(
        registry.guide_edge_local_ids,
        uuid_factory,
        used,
        "Copied GuideEdge UUID",
    )
    anchor_map = _uuid_map(
        registry.anchor_local_ids,
        uuid_factory,
        used,
        "Copied Anchor UUID",
    )
    region_map = _uuid_map(
        registry.regions,
        uuid_factory,
        used,
        "Copied Region UUID",
    )

    registry.project_uuid = project_uuid
    registry.guide_node_local_ids = {
        node_map[key]: value
        for key, value in registry.guide_node_local_ids.items()
    }
    registry.guide_edge_local_ids = {
        edge_map[key]: value
        for key, value in registry.guide_edge_local_ids.items()
    }
    registry.anchor_local_ids = {
        anchor_map[key]: value
        for key, value in registry.anchor_local_ids.items()
    }
    registry.guide_node_uuid_by_graph_id = {
        key: node_map[value]
        for key, value in registry.guide_node_uuid_by_graph_id.items()
    }
    registry.guide_edge_uuid_by_graph_id = {
        key: edge_map[value]
        for key, value in registry.guide_edge_uuid_by_graph_id.items()
    }
    registry.anchor_uuid_by_key = {
        (
            f"node:{node_map[key.split(':', 1)[1]]}"
            if key.startswith("node:")
            and key.split(":", 1)[1] in node_map
            else key
        ): anchor_map[value]
        for key, value in registry.anchor_uuid_by_key.items()
    }

    new_regions = {}
    for old_uuid, region in registry.regions.items():
        region.region_uuid = region_map[old_uuid]
        region.project_uuid = project_uuid
        region.guide_node_to_vertex_uid = {
            node_map[key]: value
            for key, value in region.guide_node_to_vertex_uid.items()
        }
        region.guide_edge_to_ordered_vertex_uids = {
            edge_map[key]: value
            for key, value in (
                region.guide_edge_to_ordered_vertex_uids.items()
            )
        }
        region.vertex_uid_to_anchor_id = {
            key: anchor_map[value]
            for key, value in region.vertex_uid_to_anchor_id.items()
        }
        new_regions[region.region_uuid] = region
    registry.regions = new_regions
    registry.region_uuid_by_cycle_key = {
        key: region_map[value]
        for key, value in registry.region_uuid_by_cycle_key.items()
    }

    boundary_key_map = {}
    rebuilt_boundaries = {}
    rebuilt_local_ids = {}
    for old_key, value in registry.boundary_bindings.items():
        edge_uuids = tuple(
            edge_map[item] for item in value["guide_edge_uuids"]
        )
        node_uuids = tuple(
            node_map[item] for item in value["endpoint_node_uuids"]
        )
        new_key, forward = normalized_boundary_key(
            project_uuid,
            edge_uuids,
            node_uuids,
            value["segment_count"],
        )
        boundary_key_map[old_key] = new_key
        rebuilt = dict(value)
        rebuilt["guide_edge_uuids"] = list(
            edge_uuids if forward else tuple(reversed(edge_uuids))
        )
        rebuilt["endpoint_node_uuids"] = list(
            node_uuids if forward else tuple(reversed(node_uuids))
        )
        if not forward:
            rebuilt["vertex_uids"] = list(
                reversed(rebuilt["vertex_uids"])
            )
            rebuilt["edge_uids"] = list(reversed(rebuilt["edge_uids"]))
        rebuilt["region_uuids"] = [
            region_map[item] for item in rebuilt["region_uuids"]
        ]
        rebuilt_boundaries[new_key] = rebuilt
        rebuilt_local_ids[new_key] = int(rebuilt["local_id"])
    registry.boundary_bindings = rebuilt_boundaries
    registry.boundary_local_ids = rebuilt_local_ids
    for region in registry.regions.values():
        region.outer_boundary_keys = tuple(
            boundary_key_map[key]
            for key in region.outer_boundary_keys
            if key in boundary_key_map
        )
        region.shared_boundary_keys = tuple(
            boundary_key_map[key]
            for key in region.shared_boundary_keys
            if key in boundary_key_map
        )

    registry.retired_region_uuids = ()
    registry.retired_guide_node_uuids = ()
    registry.retired_guide_edge_uuids = ()
    registry.retired_anchor_uuids = ()
    registry.retired_boundary_keys = ()
    registry.migration_notes = tuple(
        sorted(
            set(registry.migration_notes)
            | {"Copied project identity was independently re-keyed."}
        )
    )
    registry.revision += 1
    maps = {
        "guide_nodes": node_map,
        "guide_edges": edge_map,
        "anchors": anchor_map,
        "regions": region_map,
        "boundaries": boundary_key_map,
    }
    return registry, maps


def rekey_project_bundle(
    registry,
    project_uuid,
    built_cells=(),
    mesh_users=1,
    uuid_factory=new_uuid,
):
    if int(mesh_users) != 1:
        raise BindingRegistryError(
            "BINDING_LINKED_MESH_COPY",
            "A linked multi-user mesh must be made single-user before re-keying.",
        )
    copied, maps = rekey_binding_registry(
        registry,
        project_uuid,
        uuid_factory=uuid_factory,
    )
    cells = []
    for value in tuple(built_cells or ()):
        record = deepcopy(dict(value or {}))
        old_region_uuid = str(record.get("region_uuid", "") or "")
        if old_region_uuid:
            if old_region_uuid not in maps["regions"]:
                raise BindingRegistryError(
                    "BINDING_COPY_INCOMPLETE",
                    "A built cell references a Region missing from the registry.",
                )
            record["region_uuid"] = maps["regions"][old_region_uuid]
        record["project_uuid"] = copied.project_uuid
        cells.append(record)
    return RekeyBundle(
        registry=copied,
        built_cells=tuple(cells),
        uuid_maps=maps,
    )
