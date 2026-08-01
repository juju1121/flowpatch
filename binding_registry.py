import hashlib
import json
import uuid
from dataclasses import dataclass
from dataclasses import field
from enum import Enum
from enum import IntEnum


BINDING_SCHEMA_VERSION = 1
BINDING_REGISTRY_KEY = "flowpatch_binding_registry_v1"

FP_VERTEX_UID = "fp_vertex_uid"
FP_VERTEX_REGION = "fp_vertex_region_local_id"
FP_VERTEX_ROLE = "fp_vertex_role"
FP_VERTEX_GENERATION = "fp_vertex_generation"
FP_GUIDE_NODE_LOCAL_ID = "fp_guide_node_local_id"
FP_ANCHOR_LOCAL_ID = "fp_anchor_local_id"
FP_PARAM_U = "fp_u"
FP_PARAM_V = "fp_v"

FP_EDGE_UID = "fp_edge_uid"
FP_EDGE_REGION = "fp_edge_region_local_id"
FP_EDGE_BOUNDARY_KEY = "fp_boundary_key"
FP_EDGE_GENERATION = "fp_edge_generation"
FP_GUIDE_EDGE_LOCAL_ID = "fp_guide_edge_local_id"
FP_EDGE_ROLE = "fp_edge_role"

FP_FACE_UID = "fp_face_uid"
FP_FACE_REGION = "fp_face_region_local_id"
FP_CELL_LOCAL_ID = "fp_cell_local_id"
FP_SOLVER_KIND = "fp_solver_kind"
FP_FACE_GENERATION = "fp_face_generation"

LEGACY_VERTEX_UID = "flowpatch_vertex_uid"


class BindingRegistryError(RuntimeError):
    def __init__(self, reason_code, message):
        super().__init__(message)
        self.reason_code = str(reason_code)


class BindingMigrationState(str, Enum):
    CURRENT = "CURRENT"
    RECONSTRUCTABLE_LEGACY = "RECONSTRUCTABLE_LEGACY"
    NEEDS_REBUILD_FROM_GUIDES = "NEEDS_REBUILD_FROM_GUIDES"
    UNBOUND_MESH_ONLY = "UNBOUND_MESH_ONLY"
    CORRUPT = "CORRUPT"


class VertexRole(IntEnum):
    UNKNOWN = 0
    CORNER = 1
    BOUNDARY = 2
    INTERIOR = 3
    POLE = 4
    ADOPTED = 5


class EdgeRole(IntEnum):
    UNKNOWN = 0
    OUTER_BOUNDARY = 1
    SHARED_BOUNDARY = 2
    INTERIOR = 3
    ADOPTED = 4


def new_uuid():
    return str(uuid.uuid4())


def _canonical_uuid(value, label):
    try:
        return str(uuid.UUID(str(value or "").strip()))
    except (AttributeError, TypeError, ValueError) as exc:
        raise BindingRegistryError(
            "BINDING_SERIALIZATION_FAILED",
            f"{label} is missing or malformed.",
        ) from exc


def _positive_int(value, label):
    try:
        result = int(value)
    except (TypeError, ValueError) as exc:
        raise BindingRegistryError(
            "BINDING_SERIALIZATION_FAILED",
            f"{label} is not an integer.",
        ) from exc
    if result <= 0:
        raise BindingRegistryError(
            "BINDING_SERIALIZATION_FAILED",
            f"{label} must be positive.",
        )
    return result


@dataclass
class ElementUIDAllocator:
    next_vertex_uid: int = 1
    next_edge_uid: int = 1
    next_face_uid: int = 1
    next_region_local_id: int = 1
    next_boundary_local_id: int = 1
    next_anchor_local_id: int = 1
    next_solver_kind_local_id: int = 1
    next_guide_node_local_id: int = 1
    next_guide_edge_local_id: int = 1

    _FIELD_BY_DOMAIN = {
        "VERTEX": "next_vertex_uid",
        "EDGE": "next_edge_uid",
        "FACE": "next_face_uid",
        "REGION": "next_region_local_id",
        "BOUNDARY": "next_boundary_local_id",
        "ANCHOR": "next_anchor_local_id",
        "SOLVER_KIND": "next_solver_kind_local_id",
        "GUIDE_NODE": "next_guide_node_local_id",
        "GUIDE_EDGE": "next_guide_edge_local_id",
    }

    def allocate(self, domain):
        field_name = self._FIELD_BY_DOMAIN.get(str(domain).upper())
        if field_name is None:
            raise ValueError(f"Unknown UID allocator domain: {domain}")
        value = max(1, int(getattr(self, field_name)))
        setattr(self, field_name, value + 1)
        return value

    def raise_above(self, domain, maximum):
        field_name = self._FIELD_BY_DOMAIN[str(domain).upper()]
        setattr(
            self,
            field_name,
            max(int(getattr(self, field_name)), int(maximum) + 1, 1),
        )

    def as_dict(self):
        return {
            name: max(1, int(getattr(self, name)))
            for name in self._FIELD_BY_DOMAIN.values()
        }

    @classmethod
    def from_dict(cls, payload):
        payload = dict(payload or {})
        values = {}
        for field_name in cls._FIELD_BY_DOMAIN.values():
            values[field_name] = _positive_int(
                payload.get(field_name, 1),
                f"Allocator field {field_name}",
            )
        return cls(**values)


def _int_set(values):
    return {int(value) for value in tuple(values or ())}


def _string_tuple(values):
    return tuple(str(value) for value in tuple(values or ()))


@dataclass
class RegionBinding:
    project_uuid: str
    region_uuid: str
    region_local_id: int
    solver_kind: str
    generation: int
    cycle_key: str = ""
    guide_node_to_vertex_uid: dict = field(default_factory=dict)
    guide_edge_to_ordered_vertex_uids: dict = field(default_factory=dict)
    vertex_uid_to_param_uv: dict = field(default_factory=dict)
    vertex_uid_to_anchor_id: dict = field(default_factory=dict)
    vertex_uids: set = field(default_factory=set)
    edge_uids: set = field(default_factory=set)
    face_uids: set = field(default_factory=set)
    outer_boundary_keys: tuple = ()
    shared_boundary_keys: tuple = ()
    guide_revision: int = 1
    mesh_revision: int = 1
    last_sync_source: str = "BUILD"
    state: str = "BOUND"

    def as_dict(self):
        return {
            "project_uuid": _canonical_uuid(
                self.project_uuid,
                "Region project UUID",
            ),
            "region_uuid": _canonical_uuid(
                self.region_uuid,
                "Region UUID",
            ),
            "region_local_id": _positive_int(
                self.region_local_id,
                "Region local ID",
            ),
            "solver_kind": str(self.solver_kind),
            "generation": _positive_int(
                self.generation,
                "Region generation",
            ),
            "cycle_key": str(self.cycle_key),
            "guide_node_to_vertex_uid": {
                str(key): int(value)
                for key, value in sorted(
                    self.guide_node_to_vertex_uid.items()
                )
            },
            "guide_edge_to_ordered_vertex_uids": {
                str(key): [int(value) for value in values]
                for key, values in sorted(
                    self.guide_edge_to_ordered_vertex_uids.items()
                )
            },
            "vertex_uid_to_param_uv": {
                str(int(key)): [float(value[0]), float(value[1])]
                for key, value in sorted(
                    self.vertex_uid_to_param_uv.items(),
                    key=lambda item: int(item[0]),
                )
            },
            "vertex_uid_to_anchor_id": {
                str(int(key)): str(value)
                for key, value in sorted(
                    self.vertex_uid_to_anchor_id.items(),
                    key=lambda item: int(item[0]),
                )
            },
            "vertex_uids": sorted(int(value) for value in self.vertex_uids),
            "edge_uids": sorted(int(value) for value in self.edge_uids),
            "face_uids": sorted(int(value) for value in self.face_uids),
            "outer_boundary_keys": sorted(
                str(value) for value in self.outer_boundary_keys
            ),
            "shared_boundary_keys": sorted(
                str(value) for value in self.shared_boundary_keys
            ),
            "guide_revision": max(0, int(self.guide_revision)),
            "mesh_revision": max(0, int(self.mesh_revision)),
            "last_sync_source": str(self.last_sync_source),
            "state": str(self.state),
        }

    @classmethod
    def from_dict(cls, payload):
        payload = dict(payload or {})
        return cls(
            project_uuid=_canonical_uuid(
                payload.get("project_uuid"),
                "Region project UUID",
            ),
            region_uuid=_canonical_uuid(
                payload.get("region_uuid"),
                "Region UUID",
            ),
            region_local_id=_positive_int(
                payload.get("region_local_id"),
                "Region local ID",
            ),
            solver_kind=str(payload.get("solver_kind", "GRID")),
            generation=_positive_int(
                payload.get("generation", 1),
                "Region generation",
            ),
            cycle_key=str(payload.get("cycle_key", "")),
            guide_node_to_vertex_uid={
                _canonical_uuid(key, "GuideNode UUID"): int(value)
                for key, value in dict(
                    payload.get("guide_node_to_vertex_uid", {})
                ).items()
            },
            guide_edge_to_ordered_vertex_uids={
                _canonical_uuid(key, "GuideEdge UUID"): tuple(
                    int(value) for value in values
                )
                for key, values in dict(
                    payload.get(
                        "guide_edge_to_ordered_vertex_uids",
                        {},
                    )
                ).items()
            },
            vertex_uid_to_param_uv={
                int(key): (float(value[0]), float(value[1]))
                for key, value in dict(
                    payload.get("vertex_uid_to_param_uv", {})
                ).items()
            },
            vertex_uid_to_anchor_id={
                int(key): _canonical_uuid(value, "Anchor UUID")
                for key, value in dict(
                    payload.get("vertex_uid_to_anchor_id", {})
                ).items()
            },
            vertex_uids=_int_set(payload.get("vertex_uids", ())),
            edge_uids=_int_set(payload.get("edge_uids", ())),
            face_uids=_int_set(payload.get("face_uids", ())),
            outer_boundary_keys=_string_tuple(
                payload.get("outer_boundary_keys", ())
            ),
            shared_boundary_keys=_string_tuple(
                payload.get("shared_boundary_keys", ())
            ),
            guide_revision=max(0, int(payload.get("guide_revision", 1))),
            mesh_revision=max(0, int(payload.get("mesh_revision", 1))),
            last_sync_source=str(payload.get("last_sync_source", "BUILD")),
            state=str(payload.get("state", "BOUND")),
        )


@dataclass
class BindingRegistry:
    schema_version: int
    project_uuid: str
    allocator: ElementUIDAllocator
    regions: dict = field(default_factory=dict)
    guide_node_local_ids: dict = field(default_factory=dict)
    guide_edge_local_ids: dict = field(default_factory=dict)
    anchor_local_ids: dict = field(default_factory=dict)
    guide_node_uuid_by_graph_id: dict = field(default_factory=dict)
    guide_edge_uuid_by_graph_id: dict = field(default_factory=dict)
    anchor_uuid_by_key: dict = field(default_factory=dict)
    region_uuid_by_cycle_key: dict = field(default_factory=dict)
    boundary_local_ids: dict = field(default_factory=dict)
    boundary_bindings: dict = field(default_factory=dict)
    solver_kind_local_ids: dict = field(default_factory=dict)
    migration_state: str = BindingMigrationState.CURRENT.value
    migration_notes: tuple = ()
    revision: int = 1
    retired_region_uuids: tuple = ()
    retired_guide_node_uuids: tuple = ()
    retired_guide_edge_uuids: tuple = ()
    retired_anchor_uuids: tuple = ()
    retired_boundary_keys: tuple = ()

    @classmethod
    def empty(cls, project_uuid):
        return cls(
            schema_version=BINDING_SCHEMA_VERSION,
            project_uuid=_canonical_uuid(project_uuid, "Project UUID"),
            allocator=ElementUIDAllocator(),
        )

    def as_dict(self):
        return {
            "schema_version": BINDING_SCHEMA_VERSION,
            "project_uuid": _canonical_uuid(
                self.project_uuid,
                "BindingRegistry project UUID",
            ),
            "allocator": self.allocator.as_dict(),
            "regions": {
                str(key): value.as_dict()
                for key, value in sorted(self.regions.items())
            },
            "guide_node_local_ids": {
                str(key): int(value)
                for key, value in sorted(self.guide_node_local_ids.items())
            },
            "guide_edge_local_ids": {
                str(key): int(value)
                for key, value in sorted(self.guide_edge_local_ids.items())
            },
            "anchor_local_ids": {
                str(key): int(value)
                for key, value in sorted(self.anchor_local_ids.items())
            },
            "guide_node_uuid_by_graph_id": {
                str(key): str(value)
                for key, value in sorted(
                    self.guide_node_uuid_by_graph_id.items(),
                    key=lambda item: int(item[0]),
                )
            },
            "guide_edge_uuid_by_graph_id": {
                str(key): str(value)
                for key, value in sorted(
                    self.guide_edge_uuid_by_graph_id.items(),
                    key=lambda item: int(item[0]),
                )
            },
            "anchor_uuid_by_key": {
                str(key): str(value)
                for key, value in sorted(self.anchor_uuid_by_key.items())
            },
            "region_uuid_by_cycle_key": {
                str(key): str(value)
                for key, value in sorted(
                    self.region_uuid_by_cycle_key.items()
                )
            },
            "boundary_local_ids": {
                str(key): int(value)
                for key, value in sorted(self.boundary_local_ids.items())
            },
            "boundary_bindings": {
                str(key): _canonical_boundary_record(value)
                for key, value in sorted(self.boundary_bindings.items())
            },
            "solver_kind_local_ids": {
                str(key): int(value)
                for key, value in sorted(
                    self.solver_kind_local_ids.items()
                )
            },
            "migration_state": str(self.migration_state),
            "migration_notes": sorted(
                str(value) for value in self.migration_notes
            ),
            "revision": max(1, int(self.revision)),
            "retired_region_uuids": sorted(
                str(value) for value in self.retired_region_uuids
            ),
            "retired_guide_node_uuids": sorted(
                str(value) for value in self.retired_guide_node_uuids
            ),
            "retired_guide_edge_uuids": sorted(
                str(value) for value in self.retired_guide_edge_uuids
            ),
            "retired_anchor_uuids": sorted(
                str(value) for value in self.retired_anchor_uuids
            ),
            "retired_boundary_keys": sorted(
                str(value) for value in self.retired_boundary_keys
            ),
        }

    @classmethod
    def from_dict(cls, payload):
        payload = dict(payload or {})
        try:
            schema_version = int(payload.get("schema_version", 0))
        except (TypeError, ValueError) as exc:
            raise BindingRegistryError(
                "BINDING_SERIALIZATION_FAILED",
                "BindingRegistry schema is malformed.",
            ) from exc
        if schema_version != BINDING_SCHEMA_VERSION:
            raise BindingRegistryError(
                "BINDING_SERIALIZATION_FAILED",
                (
                    f"BindingRegistry schema {schema_version} is unsupported; "
                    f"expected {BINDING_SCHEMA_VERSION}."
                ),
            )
        project_uuid = _canonical_uuid(
            payload.get("project_uuid"),
            "BindingRegistry project UUID",
        )
        regions = {
            _canonical_uuid(key, "Region UUID"): RegionBinding.from_dict(value)
            for key, value in dict(payload.get("regions", {})).items()
        }
        return cls(
            schema_version=schema_version,
            project_uuid=project_uuid,
            allocator=ElementUIDAllocator.from_dict(
                payload.get("allocator", {})
            ),
            regions=regions,
            guide_node_local_ids=_uuid_int_mapping(
                payload.get("guide_node_local_ids", {}),
                "GuideNode UUID",
            ),
            guide_edge_local_ids=_uuid_int_mapping(
                payload.get("guide_edge_local_ids", {}),
                "GuideEdge UUID",
            ),
            anchor_local_ids=_uuid_int_mapping(
                payload.get("anchor_local_ids", {}),
                "Anchor UUID",
            ),
            guide_node_uuid_by_graph_id=_int_uuid_mapping(
                payload.get("guide_node_uuid_by_graph_id", {}),
                "GuideNode UUID",
            ),
            guide_edge_uuid_by_graph_id=_int_uuid_mapping(
                payload.get("guide_edge_uuid_by_graph_id", {}),
                "GuideEdge UUID",
            ),
            anchor_uuid_by_key={
                str(key): _canonical_uuid(value, "Anchor UUID")
                for key, value in dict(
                    payload.get("anchor_uuid_by_key", {})
                ).items()
            },
            region_uuid_by_cycle_key={
                str(key): _canonical_uuid(value, "Region UUID")
                for key, value in dict(
                    payload.get("region_uuid_by_cycle_key", {})
                ).items()
            },
            boundary_local_ids={
                str(key): _positive_int(value, "Boundary local ID")
                for key, value in dict(
                    payload.get("boundary_local_ids", {})
                ).items()
            },
            boundary_bindings={
                str(key): _canonical_boundary_record(value)
                for key, value in dict(
                    payload.get("boundary_bindings", {})
                ).items()
            },
            solver_kind_local_ids={
                str(key): _positive_int(value, "Solver kind local ID")
                for key, value in dict(
                    payload.get("solver_kind_local_ids", {})
                ).items()
            },
            migration_state=str(
                payload.get(
                    "migration_state",
                    BindingMigrationState.CURRENT.value,
                )
            ),
            migration_notes=_string_tuple(
                payload.get("migration_notes", ())
            ),
            revision=max(1, int(payload.get("revision", 1))),
            retired_region_uuids=_uuid_tuple(
                payload.get("retired_region_uuids", ()),
                "Retired Region UUID",
            ),
            retired_guide_node_uuids=_uuid_tuple(
                payload.get("retired_guide_node_uuids", ()),
                "Retired GuideNode UUID",
            ),
            retired_guide_edge_uuids=_uuid_tuple(
                payload.get("retired_guide_edge_uuids", ()),
                "Retired GuideEdge UUID",
            ),
            retired_anchor_uuids=_uuid_tuple(
                payload.get("retired_anchor_uuids", ()),
                "Retired Anchor UUID",
            ),
            retired_boundary_keys=_string_tuple(
                payload.get("retired_boundary_keys", ())
            ),
        )


def _uuid_tuple(values, label):
    return tuple(_canonical_uuid(value, label) for value in tuple(values or ()))


def _uuid_int_mapping(payload, label):
    return {
        _canonical_uuid(key, label): _positive_int(value, f"{label} local ID")
        for key, value in dict(payload or {}).items()
    }


def _int_uuid_mapping(payload, label):
    return {
        str(_positive_int(key, f"{label} graph ID")): _canonical_uuid(
            value,
            label,
        )
        for key, value in dict(payload or {}).items()
    }


def _canonical_boundary_record(value):
    value = dict(value or {})
    return {
        "local_id": _positive_int(
            value.get("local_id", 1),
            "Boundary local ID",
        ),
        "guide_edge_uuids": [
            _canonical_uuid(item, "Boundary GuideEdge UUID")
            for item in value.get("guide_edge_uuids", ())
        ],
        "endpoint_node_uuids": [
            _canonical_uuid(item, "Boundary GuideNode UUID")
            for item in value.get("endpoint_node_uuids", ())
        ],
        "segment_count": max(1, int(value.get("segment_count", 1))),
        "vertex_uids": [
            int(item) for item in value.get("vertex_uids", ())
        ],
        "edge_uids": [int(item) for item in value.get("edge_uids", ())],
        "region_uuids": sorted(
            _canonical_uuid(item, "Boundary Region UUID")
            for item in value.get("region_uuids", ())
        ),
    }


def parse_binding_registry(raw):
    if isinstance(raw, BindingRegistry):
        return raw
    if isinstance(raw, str):
        try:
            payload = json.loads(raw)
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            raise BindingRegistryError(
                "BINDING_SERIALIZATION_FAILED",
                "BindingRegistry metadata is not valid JSON.",
            ) from exc
    elif isinstance(raw, dict):
        payload = dict(raw)
    else:
        raise BindingRegistryError(
            "BINDING_SERIALIZATION_FAILED",
            "BindingRegistry metadata must be a JSON object.",
        )
    return BindingRegistry.from_dict(payload)


def encode_binding_registry(registry):
    if not isinstance(registry, BindingRegistry):
        registry = parse_binding_registry(registry)
    return json.dumps(
        registry.as_dict(),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    )


def clone_binding_registry(registry):
    return parse_binding_registry(encode_binding_registry(registry))


def load_binding_registry(obj):
    raw = obj.get(BINDING_REGISTRY_KEY, "")
    return parse_binding_registry(raw) if raw else None


def save_binding_registry(obj, registry):
    obj[BINDING_REGISTRY_KEY] = encode_binding_registry(registry)
    return registry


def _active_graph_ids(guides):
    edge_ids = {
        int(guide.guide_id)
        for guide in tuple(guides or ())
        if int(guide.guide_id) > 0
    }
    node_ids = {
        int(value)
        for guide in tuple(guides or ())
        for value in (guide.start_node, guide.end_node)
        if int(value) > 0
    }
    return node_ids, edge_ids


def reconcile_guide_identities(registry, guides, uuid_factory=new_uuid):
    node_ids, edge_ids = _active_graph_ids(guides)
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
            node_uuid = _canonical_uuid(
                uuid_factory(),
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
            anchor_uuid = _canonical_uuid(
                uuid_factory(),
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
            edge_uuid = _canonical_uuid(
                uuid_factory(),
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


def sync_guide_identities(obj, guides, uuid_factory=new_uuid):
    registry = load_binding_registry(obj)
    if registry is None:
        return None
    if reconcile_guide_identities(registry, guides, uuid_factory):
        save_binding_registry(obj, registry)
    return registry


def normalized_boundary_key(
    project_uuid,
    guide_edge_uuids,
    endpoint_node_uuids,
    segment_count,
):
    project_uuid = _canonical_uuid(project_uuid, "Boundary project UUID")
    edges = tuple(
        _canonical_uuid(value, "Boundary GuideEdge UUID")
        for value in guide_edge_uuids
    )
    nodes = tuple(
        _canonical_uuid(value, "Boundary GuideNode UUID")
        for value in endpoint_node_uuids
    )
    if not edges or len(nodes) != 2 or int(segment_count) <= 0:
        raise BindingRegistryError(
            "BINDING_BOUNDARY_MISMATCH",
            "A boundary identity needs guide edges, two nodes, and segments.",
        )
    forward = (edges, nodes)
    backward = (tuple(reversed(edges)), tuple(reversed(nodes)))
    canonical = min(forward, backward)
    payload = {
        "project_uuid": project_uuid,
        "guide_edge_uuids": list(canonical[0]),
        "endpoint_node_uuids": list(canonical[1]),
        "segment_count": int(segment_count),
    }
    digest = hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode(
            "ascii"
        )
    ).hexdigest()
    return f"b1:{digest}", forward == canonical


def _ensure_layer(collection, kind, name):
    layers = getattr(collection.layers, kind)
    layer = layers.get(name)
    return layer if layer is not None else layers.new(name)


def ensure_binding_layers(bm):
    # Blender 5.3 invalidates cached BM element wrappers when a new custom-data
    # layer is added. Create every layer first, then reacquire elements.
    for name in (
        FP_VERTEX_UID,
        FP_VERTEX_REGION,
        FP_VERTEX_ROLE,
        FP_VERTEX_GENERATION,
        FP_GUIDE_NODE_LOCAL_ID,
        FP_ANCHOR_LOCAL_ID,
    ):
        _ensure_layer(bm.verts, "int", name)
    for name in (FP_PARAM_U, FP_PARAM_V):
        _ensure_layer(bm.verts, "float", name)
    for name in (
        FP_EDGE_UID,
        FP_EDGE_REGION,
        FP_EDGE_BOUNDARY_KEY,
        FP_EDGE_GENERATION,
        FP_GUIDE_EDGE_LOCAL_ID,
        FP_EDGE_ROLE,
    ):
        _ensure_layer(bm.edges, "int", name)
    for name in (
        FP_FACE_UID,
        FP_FACE_REGION,
        FP_CELL_LOCAL_ID,
        FP_SOLVER_KIND,
        FP_FACE_GENERATION,
    ):
        _ensure_layer(bm.faces, "int", name)
    bm.verts.ensure_lookup_table()
    bm.edges.ensure_lookup_table()
    bm.faces.ensure_lookup_table()
    bm.verts.index_update()
    bm.edges.index_update()
    bm.faces.index_update()
    return binding_layers(bm)


def binding_layers(bm):
    return {
        "verts": {
            name: (
                bm.verts.layers.float.get(name)
                if name in {FP_PARAM_U, FP_PARAM_V}
                else bm.verts.layers.int.get(name)
            )
            for name in (
                FP_VERTEX_UID,
                FP_VERTEX_REGION,
                FP_VERTEX_ROLE,
                FP_VERTEX_GENERATION,
                FP_GUIDE_NODE_LOCAL_ID,
                FP_ANCHOR_LOCAL_ID,
                FP_PARAM_U,
                FP_PARAM_V,
            )
        },
        "edges": {
            name: bm.edges.layers.int.get(name)
            for name in (
                FP_EDGE_UID,
                FP_EDGE_REGION,
                FP_EDGE_BOUNDARY_KEY,
                FP_EDGE_GENERATION,
                FP_GUIDE_EDGE_LOCAL_ID,
                FP_EDGE_ROLE,
            )
        },
        "faces": {
            name: bm.faces.layers.int.get(name)
            for name in (
                FP_FACE_UID,
                FP_FACE_REGION,
                FP_CELL_LOCAL_ID,
                FP_SOLVER_KIND,
                FP_FACE_GENERATION,
            )
        },
    }


def _domain_uid_state(elements, layer):
    by_uid = {}
    duplicates = set()
    for element in elements:
        value = int(element[layer])
        if value <= 0:
            continue
        if value in by_uid:
            duplicates.add(value)
        else:
            by_uid[value] = element
    return by_uid, duplicates


def _allocate_element_uid(element, layer, registry, domain, used, candidate=0):
    existing = int(element[layer])
    if existing > 0:
        owner = used.get(existing)
        if owner is not None and owner is not element:
            raise BindingRegistryError(
                "BINDING_UID_COLLISION",
                f"Duplicate {domain.lower()} UID {existing}.",
            )
        used[existing] = element
        registry.allocator.raise_above(domain, existing)
        return existing
    candidate = int(candidate)
    if candidate > 0 and candidate not in used:
        value = candidate
        registry.allocator.raise_above(domain, value)
    else:
        value = registry.allocator.allocate(domain)
        while value in used:
            value = registry.allocator.allocate(domain)
    element[layer] = value
    used[value] = element
    return value


def _grid_from_record(record):
    grid = tuple(
        tuple(int(value) for value in row)
        for row in record.get("grid_vertex_uids", ())
    )
    if len(grid) < 2 or len(grid[0]) < 2:
        raise BindingRegistryError(
            "BINDING_MIGRATION_AMBIGUOUS",
            "A GRID Region has no deterministic vertex grid.",
        )
    width = len(grid[0])
    if any(len(row) != width for row in grid):
        raise BindingRegistryError(
            "BINDING_MIGRATION_AMBIGUOUS",
            "A GRID Region has inconsistent row widths.",
        )
    if any(value <= 0 for row in grid for value in row):
        raise BindingRegistryError(
            "BINDING_MIGRATION_AMBIGUOUS",
            "A GRID Region contains a missing legacy vertex UID.",
        )
    return grid


def _grid_side_positions(row_count, column_count, side_index):
    if side_index == 0:
        return [(0, column) for column in range(column_count)]
    if side_index == 1:
        return [(row, column_count - 1) for row in range(row_count)]
    if side_index == 2:
        return [
            (row_count - 1, column)
            for column in range(column_count - 1, -1, -1)
        ]
    return [(row, 0) for row in range(row_count - 1, -1, -1)]


def _guide_ids_for_side(record, side_index):
    sides = tuple(record.get("side_edge_ids", ()) or ())
    if len(sides) == 4 and side_index < len(sides):
        values = tuple(int(value) for value in sides[side_index])
        if values:
            return values
    side_keys = tuple(record.get("side_keys", ()) or ())
    if side_index < len(side_keys):
        return tuple(
            int(value)
            for value in str(side_keys[side_index]).split(":")
            if str(value).isdigit() and int(value) > 0
        )
    return ()


def _partition_uid_path(values, count):
    values = tuple(int(value) for value in values)
    count = max(1, int(count))
    segment_count = max(1, len(values) - 1)
    output = []
    for index in range(count):
        start = int(round((index / count) * segment_count))
        end = int(round(((index + 1) / count) * segment_count))
        start = max(0, min(start, len(values) - 2))
        end = max(start + 1, min(end, len(values) - 1))
        output.append(values[start : end + 1])
    return tuple(output)


def _solver_local_id(registry, solver_kind):
    key = str(solver_kind).upper()
    value = registry.solver_kind_local_ids.get(key)
    if value is None:
        value = registry.allocator.allocate("SOLVER_KIND")
        registry.solver_kind_local_ids[key] = value
    return int(value)


def _boundary_orientation(edge_uuids, node_uuids):
    forward = (tuple(edge_uuids), tuple(node_uuids))
    backward = (tuple(reversed(edge_uuids)), tuple(reversed(node_uuids)))
    return forward <= backward


def _bind_grid_region(
    registry,
    bm,
    layers,
    guides,
    cycle_key,
    record,
    uuid_factory,
):
    legacy_layer = bm.verts.layers.int.get(LEGACY_VERTEX_UID)
    if legacy_layer is None:
        raise BindingRegistryError(
            "BINDING_MIGRATION_AMBIGUOUS",
            "A built GRID Region has no legacy vertex UID layer.",
        )
    legacy_by_uid, legacy_duplicates = _domain_uid_state(
        bm.verts,
        legacy_layer,
    )
    if legacy_duplicates:
        raise BindingRegistryError(
            "BINDING_UID_COLLISION",
            "Legacy vertex UIDs are duplicated.",
        )
    grid_legacy = _grid_from_record(record)
    try:
        grid = tuple(
            tuple(legacy_by_uid[value] for value in row)
            for row in grid_legacy
        )
    except KeyError as exc:
        raise BindingRegistryError(
            "BINDING_ELEMENT_MISSING",
            f"GRID Region references missing legacy vertex UID {exc.args[0]}.",
        ) from exc

    vertex_uids, duplicate_vertices = _domain_uid_state(
        bm.verts,
        layers["verts"][FP_VERTEX_UID],
    )
    edge_uids, duplicate_edges = _domain_uid_state(
        bm.edges,
        layers["edges"][FP_EDGE_UID],
    )
    face_uids, duplicate_faces = _domain_uid_state(
        bm.faces,
        layers["faces"][FP_FACE_UID],
    )
    if duplicate_vertices or duplicate_edges or duplicate_faces:
        raise BindingRegistryError(
            "BINDING_UID_COLLISION",
            "Existing PF-02 element UIDs are duplicated.",
        )

    existing_region_uuid = registry.region_uuid_by_cycle_key.get(
        str(cycle_key)
    )
    previous = registry.regions.get(existing_region_uuid)
    if previous is None:
        region_uuid = _canonical_uuid(
            uuid_factory(),
            "Generated Region UUID",
        )
        region_local_id = registry.allocator.allocate("REGION")
        generation = 1
    else:
        region_uuid = previous.region_uuid
        region_local_id = previous.region_local_id
        generation = int(previous.generation) + 1

    row_count = len(grid)
    column_count = len(grid[0])
    vertex_uid_grid = []
    for row_index, row in enumerate(grid):
        output_row = []
        for column_index, vert in enumerate(row):
            uid = _allocate_element_uid(
                vert,
                layers["verts"][FP_VERTEX_UID],
                registry,
                "VERTEX",
                vertex_uids,
                candidate=int(vert[legacy_layer]),
            )
            output_row.append(uid)
            existing_owner = int(vert[layers["verts"][FP_VERTEX_REGION]])
            if existing_owner <= 0:
                vert[layers["verts"][FP_VERTEX_REGION]] = region_local_id
                vert[layers["verts"][FP_PARAM_U]] = (
                    column_index / max(1, column_count - 1)
                )
                vert[layers["verts"][FP_PARAM_V]] = (
                    row_index / max(1, row_count - 1)
                )
            vert[layers["verts"][FP_VERTEX_GENERATION]] = max(
                int(vert[layers["verts"][FP_VERTEX_GENERATION]]),
                generation,
            )
        vertex_uid_grid.append(tuple(output_row))
    vertex_uid_grid = tuple(vertex_uid_grid)

    edge_by_pair = {}
    for edge in bm.edges:
        pair = frozenset(
            int(vert[layers["verts"][FP_VERTEX_UID]])
            for vert in edge.verts
        )
        if len(pair) == 2:
            edge_by_pair[pair] = edge
    face_by_key = {}
    for face in bm.faces:
        key = frozenset(
            int(vert[layers["verts"][FP_VERTEX_UID]])
            for vert in face.verts
        )
        if len(key) == 4:
            if key in face_by_key:
                raise BindingRegistryError(
                    "BINDING_UID_COLLISION",
                    "Two faces share one vertex-UID ownership key.",
                )
            face_by_key[key] = face

    region_edges = set()
    horizontal = []
    vertical = []
    for row in grid:
        horizontal.extend(zip(row, row[1:]))
    for row, following in zip(grid, grid[1:]):
        vertical.extend(zip(row, following))
    for start, end in horizontal + vertical:
        pair = frozenset(
            (
                int(start[layers["verts"][FP_VERTEX_UID]]),
                int(end[layers["verts"][FP_VERTEX_UID]]),
            )
        )
        edge = edge_by_pair.get(pair)
        if edge is None:
            raise BindingRegistryError(
                "BINDING_ELEMENT_MISSING",
                "A GRID Region boundary or interior edge is missing.",
            )
        region_edges.add(edge)

    region_faces = []
    for row_index in range(row_count - 1):
        for column_index in range(column_count - 1):
            key = frozenset(
                (
                    vertex_uid_grid[row_index][column_index],
                    vertex_uid_grid[row_index][column_index + 1],
                    vertex_uid_grid[row_index + 1][column_index + 1],
                    vertex_uid_grid[row_index + 1][column_index],
                )
            )
            face = face_by_key.get(key)
            if face is None:
                raise BindingRegistryError(
                    "BINDING_ELEMENT_MISSING",
                    "A GRID Region face is missing or is not a quad.",
                )
            region_faces.append(face)

    boundary_verts = set()
    corner_verts = {
        grid[0][0],
        grid[0][-1],
        grid[-1][-1],
        grid[-1][0],
    }
    for side_index in range(4):
        boundary_verts.update(
            grid[row][column]
            for row, column in _grid_side_positions(
                row_count,
                column_count,
                side_index,
            )
        )
    for vert in {item for row in grid for item in row}:
        role = (
            VertexRole.CORNER
            if vert in corner_verts
            else VertexRole.BOUNDARY
            if vert in boundary_verts
            else VertexRole.INTERIOR
        )
        current_role = int(vert[layers["verts"][FP_VERTEX_ROLE]])
        if current_role == VertexRole.UNKNOWN or role == VertexRole.CORNER:
            vert[layers["verts"][FP_VERTEX_ROLE]] = int(role)

    region_edge_uids = set()
    for edge in region_edges:
        uid = _allocate_element_uid(
            edge,
            layers["edges"][FP_EDGE_UID],
            registry,
            "EDGE",
            edge_uids,
        )
        region_edge_uids.add(uid)
        existing_owner = int(edge[layers["edges"][FP_EDGE_REGION]])
        if existing_owner <= 0:
            edge[layers["edges"][FP_EDGE_REGION]] = region_local_id
        edge[layers["edges"][FP_EDGE_GENERATION]] = max(
            int(edge[layers["edges"][FP_EDGE_GENERATION]]),
            generation,
        )

    solver_kind = str(record.get("topology_kind", "GRID")).upper()
    solver_local_id = _solver_local_id(registry, solver_kind)
    region_face_uids = set()
    for cell_index, face in enumerate(region_faces, start=1):
        uid = _allocate_element_uid(
            face,
            layers["faces"][FP_FACE_UID],
            registry,
            "FACE",
            face_uids,
        )
        region_face_uids.add(uid)
        existing_owner = int(face[layers["faces"][FP_FACE_REGION]])
        if existing_owner not in {0, region_local_id}:
            raise BindingRegistryError(
                "BINDING_REGION_MISSING",
                "A committed face is already owned by another Region.",
            )
        face[layers["faces"][FP_FACE_REGION]] = region_local_id
        face[layers["faces"][FP_CELL_LOCAL_ID]] = cell_index
        face[layers["faces"][FP_SOLVER_KIND]] = solver_local_id
        face[layers["faces"][FP_FACE_GENERATION]] = generation

    node_ids = tuple(int(value) for value in record.get("corner_node_ids", ()))
    corner_positions = ((0, 0), (0, -1), (-1, -1), (-1, 0))
    guide_node_to_vertex_uid = {}
    vertex_uid_to_anchor_id = {}
    if len(node_ids) == 4:
        for node_id, (row, column) in zip(node_ids, corner_positions):
            node_uuid = registry.guide_node_uuid_by_graph_id.get(str(node_id))
            if not node_uuid:
                raise BindingRegistryError(
                    "BINDING_REGION_MISSING",
                    "A Region corner references an unknown GuideNode.",
                )
            vert = grid[row][column]
            vertex_uid = int(vert[layers["verts"][FP_VERTEX_UID]])
            guide_node_to_vertex_uid[node_uuid] = vertex_uid
            vert[layers["verts"][FP_GUIDE_NODE_LOCAL_ID]] = int(
                registry.guide_node_local_ids[node_uuid]
            )
            anchor_uuid = registry.anchor_uuid_by_key.get(f"node:{node_uuid}")
            if anchor_uuid:
                vertex_uid_to_anchor_id[vertex_uid] = anchor_uuid
                vert[layers["verts"][FP_ANCHOR_LOCAL_ID]] = int(
                    registry.anchor_local_ids[anchor_uuid]
                )

    guide_edge_to_uids = {}
    boundary_keys = []
    for side_index in range(4):
        positions = _grid_side_positions(
            row_count,
            column_count,
            side_index,
        )
        side_verts = tuple(grid[row][column] for row, column in positions)
        side_vertex_uids = tuple(
            int(vert[layers["verts"][FP_VERTEX_UID]])
            for vert in side_verts
        )
        guide_ids = _guide_ids_for_side(record, side_index)
        guide_uuids = tuple(
            registry.guide_edge_uuid_by_graph_id.get(str(value), "")
            for value in guide_ids
        )
        if not guide_uuids or any(not value for value in guide_uuids):
            raise BindingRegistryError(
                "BINDING_REGION_MISSING",
                "A Region side references an unknown GuideEdge.",
            )
        if len(node_ids) != 4:
            raise BindingRegistryError(
                "BINDING_REGION_MISSING",
                "A Region has no deterministic four-corner GuideNode map.",
            )
        side_nodes = (
            registry.guide_node_uuid_by_graph_id[str(node_ids[side_index])],
            registry.guide_node_uuid_by_graph_id[
                str(node_ids[(side_index + 1) % 4])
            ],
        )
        boundary_key, forward_is_canonical = normalized_boundary_key(
            registry.project_uuid,
            guide_uuids,
            side_nodes,
            len(side_vertex_uids) - 1,
        )
        boundary_keys.append(boundary_key)
        canonical_vertex_uids = (
            side_vertex_uids
            if forward_is_canonical
            else tuple(reversed(side_vertex_uids))
        )
        side_edges = []
        for start, end in zip(side_verts, side_verts[1:]):
            edge = bm.edges.get((start, end))
            if edge is None:
                raise BindingRegistryError(
                    "BINDING_ELEMENT_MISSING",
                    "A boundary edge disappeared during binding.",
                )
            side_edges.append(edge)
        canonical_edges = (
            tuple(side_edges)
            if forward_is_canonical
            else tuple(reversed(side_edges))
        )
        canonical_edge_uids = tuple(
            int(edge[layers["edges"][FP_EDGE_UID]])
            for edge in canonical_edges
        )
        existing_boundary = registry.boundary_bindings.get(boundary_key)
        if existing_boundary is None:
            boundary_local_id = registry.allocator.allocate("BOUNDARY")
            registry.boundary_local_ids[boundary_key] = boundary_local_id
            registry.boundary_bindings[boundary_key] = {
                "local_id": boundary_local_id,
                "guide_edge_uuids": list(
                    guide_uuids
                    if forward_is_canonical
                    else tuple(reversed(guide_uuids))
                ),
                "endpoint_node_uuids": list(
                    side_nodes
                    if forward_is_canonical
                    else tuple(reversed(side_nodes))
                ),
                "segment_count": len(canonical_vertex_uids) - 1,
                "vertex_uids": list(canonical_vertex_uids),
                "edge_uids": list(canonical_edge_uids),
                "region_uuids": [region_uuid],
            }
        else:
            boundary_local_id = int(existing_boundary["local_id"])
            existing_vertices = tuple(existing_boundary.get("vertex_uids", ()))
            existing_edges = tuple(existing_boundary.get("edge_uids", ()))
            if existing_vertices != canonical_vertex_uids or (
                existing_edges and existing_edges != canonical_edge_uids
            ):
                raise BindingRegistryError(
                    "BINDING_BOUNDARY_MISMATCH",
                    "A shared boundary resolves to different mesh UIDs.",
                )
            existing_boundary["region_uuids"] = sorted(
                set(existing_boundary.get("region_uuids", ()))
                | {region_uuid}
            )
        for edge in side_edges:
            current_boundary = int(
                edge[layers["edges"][FP_EDGE_BOUNDARY_KEY]]
            )
            if current_boundary not in {0, boundary_local_id}:
                raise BindingRegistryError(
                    "BINDING_BOUNDARY_MISMATCH",
                    "A mesh edge is claimed by two boundary identities.",
                )
            edge[layers["edges"][FP_EDGE_BOUNDARY_KEY]] = boundary_local_id
            edge[layers["edges"][FP_EDGE_ROLE]] = (
                int(EdgeRole.SHARED_BOUNDARY)
                if len(
                    registry.boundary_bindings[boundary_key]["region_uuids"]
                )
                > 1
                else int(EdgeRole.OUTER_BOUNDARY)
            )
        paths = _partition_uid_path(side_vertex_uids, len(guide_uuids))
        for guide_uuid, path in zip(
            guide_uuids,
            paths,
        ):
            guide_edge_to_uids[guide_uuid] = tuple(path)
            for uid_a, uid_b in zip(path, path[1:]):
                edge = edge_by_pair.get(frozenset((uid_a, uid_b)))
                if edge is not None:
                    edge[layers["edges"][FP_GUIDE_EDGE_LOCAL_ID]] = int(
                        registry.guide_edge_local_ids[guide_uuid]
                    )

    boundary_edge_set = {
        edge
        for edge in region_edges
        if int(edge[layers["edges"][FP_EDGE_BOUNDARY_KEY]]) > 0
    }
    for edge in region_edges - boundary_edge_set:
        edge[layers["edges"][FP_EDGE_ROLE]] = int(EdgeRole.INTERIOR)

    region_vertex_uids = {
        int(vert[layers["verts"][FP_VERTEX_UID]])
        for row in grid
        for vert in row
    }
    binding = RegionBinding(
        project_uuid=registry.project_uuid,
        region_uuid=region_uuid,
        region_local_id=region_local_id,
        solver_kind=solver_kind,
        generation=generation,
        cycle_key=str(cycle_key),
        guide_node_to_vertex_uid=guide_node_to_vertex_uid,
        guide_edge_to_ordered_vertex_uids=guide_edge_to_uids,
        vertex_uid_to_param_uv={
            vertex_uid_grid[row][column]: (
                column / max(1, column_count - 1),
                row / max(1, row_count - 1),
            )
            for row in range(row_count)
            for column in range(column_count)
        },
        vertex_uid_to_anchor_id=vertex_uid_to_anchor_id,
        vertex_uids=region_vertex_uids,
        edge_uids=region_edge_uids,
        face_uids=region_face_uids,
        outer_boundary_keys=tuple(boundary_keys),
        shared_boundary_keys=(),
        guide_revision=max(1, int(record.get("revision", 1))),
        mesh_revision=max(1, int(record.get("revision", 1))),
        last_sync_source=str(record.get("last_sync_direction", "BUILD")),
        state="BOUND",
    )
    registry.regions[region_uuid] = binding
    registry.region_uuid_by_cycle_key[str(cycle_key)] = region_uuid
    registry.allocator.raise_above("REGION", region_local_id)
    return binding


def _refresh_boundary_membership(registry):
    shared = {
        key
        for key, value in registry.boundary_bindings.items()
        if len(set(value.get("region_uuids", ()))) > 1
    }
    for region_uuid, region in registry.regions.items():
        owned = {
            key
            for key, value in registry.boundary_bindings.items()
            if region_uuid in set(value.get("region_uuids", ()))
        }
        region.shared_boundary_keys = tuple(sorted(owned & shared))
        region.outer_boundary_keys = tuple(sorted(owned - shared))


def _reconcile_allocator(registry, bm, layers):
    maxima = {
        "VERTEX": max(
            (
                int(item[layers["verts"][FP_VERTEX_UID]])
                for item in bm.verts
            ),
            default=0,
        ),
        "EDGE": max(
            (
                int(item[layers["edges"][FP_EDGE_UID]])
                for item in bm.edges
            ),
            default=0,
        ),
        "FACE": max(
            (
                int(item[layers["faces"][FP_FACE_UID]])
                for item in bm.faces
            ),
            default=0,
        ),
        "REGION": max(
            (region.region_local_id for region in registry.regions.values()),
            default=0,
        ),
        "BOUNDARY": max(registry.boundary_local_ids.values(), default=0),
        "ANCHOR": max(registry.anchor_local_ids.values(), default=0),
        "SOLVER_KIND": max(
            registry.solver_kind_local_ids.values(),
            default=0,
        ),
        "GUIDE_NODE": max(
            registry.guide_node_local_ids.values(),
            default=0,
        ),
        "GUIDE_EDGE": max(
            registry.guide_edge_local_ids.values(),
            default=0,
        ),
    }
    for domain, maximum in maxima.items():
        registry.allocator.raise_above(domain, maximum)


def _project_uuid_from_record(project_record):
    value = getattr(project_record, "project_uuid", None)
    if value is None and isinstance(project_record, dict):
        value = project_record.get("project_uuid")
    return _canonical_uuid(value, "Project UUID")


def _migrate_grid_region_if_deterministic(
    registry,
    bm,
    layers,
    guides,
    cycle_key,
    record,
    uuid_factory,
):
    trial_bm = bm.copy()
    generated_uuids = []

    def trial_uuid_factory():
        value = uuid_factory()
        generated_uuids.append(value)
        return value

    try:
        trial_registry = clone_binding_registry(registry)
        _bind_grid_region(
            trial_registry,
            trial_bm,
            binding_layers(trial_bm),
            guides,
            cycle_key,
            record,
            trial_uuid_factory,
        )
    except BindingRegistryError:
        return False
    finally:
        trial_bm.free()

    replay = iter(generated_uuids)
    _bind_grid_region(
        registry,
        bm,
        layers,
        guides,
        cycle_key,
        record,
        replay.__next__,
    )
    return True


def ensure_binding_registry(
    obj,
    bm,
    project_record,
    guides,
    built_cells,
    *,
    allow_project_rekey=False,
    uuid_factory=new_uuid,
):
    project_uuid = _project_uuid_from_record(project_record)
    layers = ensure_binding_layers(bm)
    registry = load_binding_registry(obj)
    migration_notes = []
    if registry is None:
        registry = BindingRegistry.empty(project_uuid)
        reconcile_guide_identities(registry, guides, uuid_factory)
        deterministic_count = 0
        ambiguous = []
        for cycle_key, record in sorted(dict(built_cells or {}).items()):
            if not isinstance(record, dict) or str(
                record.get("topology_kind", "GRID")
            ).upper() != "GRID":
                ambiguous.append(str(cycle_key))
                continue
            if not _migrate_grid_region_if_deterministic(
                registry,
                bm,
                layers,
                guides,
                str(cycle_key),
                record,
                uuid_factory,
            ):
                ambiguous.append(str(cycle_key))
                continue
            deterministic_count += 1
        if ambiguous:
            registry.migration_state = (
                BindingMigrationState.NEEDS_REBUILD_FROM_GUIDES.value
            )
            migration_notes.append(
                "Ambiguous legacy Regions preserved for explicit rebuild: "
                + ", ".join(sorted(ambiguous))
            )
        elif deterministic_count:
            registry.migration_state = BindingMigrationState.CURRENT.value
            migration_notes.append(
                f"Reconstructed {deterministic_count} deterministic legacy Region(s)."
            )
        elif len(bm.faces) > 0:
            registry.migration_state = (
                BindingMigrationState.UNBOUND_MESH_ONLY.value
            )
            migration_notes.append(
                "Mesh has no deterministic FlowPatch Region ownership; adoption is deferred."
            )
        else:
            registry.migration_state = BindingMigrationState.CURRENT.value
    else:
        if registry.project_uuid != project_uuid:
            if not allow_project_rekey:
                raise BindingRegistryError(
                    "BINDING_REGION_MISSING",
                    "BindingRegistry and FlowPatch project UUIDs disagree.",
                )
            registry = rekey_binding_registry(
                registry,
                project_uuid,
                uuid_factory=uuid_factory,
            )
            migration_notes.append(
                "Copied BindingRegistry was re-keyed into an independent project domain."
            )
        reconcile_guide_identities(registry, guides, uuid_factory)
    if migration_notes:
        registry.migration_notes = tuple(
            sorted(set(registry.migration_notes) | set(migration_notes))
        )
    _refresh_boundary_membership(registry)
    _reconcile_allocator(registry, bm, layers)
    registry.revision += 1
    audit = audit_binding_registry(
        obj,
        bm,
        guides=guides,
        registry=registry,
    )
    if audit["issues"]:
        raise BindingRegistryError(
            "BINDING_AUDIT_FAILED",
            audit["issues"][0]["message"],
        )
    save_binding_registry(obj, registry)
    return registry, audit


def record_committed_regions(
    obj,
    bm,
    project_record,
    guides,
    results,
    *,
    uuid_factory=new_uuid,
):
    registry = load_binding_registry(obj)
    if registry is None:
        raise BindingRegistryError(
            "BINDING_REGION_MISSING",
            "Build requires an initialized PF-02 BindingRegistry.",
        )
    project_uuid = _project_uuid_from_record(project_record)
    if registry.project_uuid != project_uuid:
        raise BindingRegistryError(
            "BINDING_REGION_MISSING",
            "Build project and BindingRegistry identities disagree.",
        )
    registry = clone_binding_registry(registry)
    reconcile_guide_identities(registry, guides, uuid_factory)
    layers = ensure_binding_layers(bm)
    for result in results:
        binding = _bind_grid_region(
            registry,
            bm,
            layers,
            guides,
            str(result.cycle_key),
            result.cell_record,
            uuid_factory,
        )
        result.cell_record["binding_schema_version"] = BINDING_SCHEMA_VERSION
        result.cell_record["region_uuid"] = binding.region_uuid
        result.cell_record["region_local_id"] = binding.region_local_id
        result.cell_record["binding_generation"] = binding.generation
    registry.migration_state = BindingMigrationState.CURRENT.value
    registry.migration_notes = tuple(
        sorted(
            (
                set(registry.migration_notes)
                - {
                    "Mesh has no deterministic FlowPatch Region ownership; "
                    "adoption is deferred."
                }
            )
            | {"Committed GRID Regions use PF-02 bindings."}
        )
    )
    _refresh_boundary_membership(registry)
    _reconcile_allocator(registry, bm, layers)
    registry.revision += 1
    audit = audit_binding_registry(
        obj,
        bm,
        guides=guides,
        registry=registry,
    )
    if audit["issues"]:
        raise BindingRegistryError(
            "BINDING_AUDIT_FAILED",
            audit["issues"][0]["message"],
        )
    save_binding_registry(obj, registry)
    return registry, audit


def _reassign_surviving_element_owners(registry, bm, layers):
    owner_maps = {
        "verts": {},
        "edges": {},
        "faces": {},
    }
    for region in registry.regions.values():
        for domain, values in (
            ("verts", region.vertex_uids),
            ("edges", region.edge_uids),
            ("faces", region.face_uids),
        ):
            for uid in values:
                owner_maps[domain].setdefault(int(uid), set()).add(
                    int(region.region_local_id)
                )

    for elements, domain, uid_layer, owner_layer in (
        (
            bm.verts,
            "verts",
            layers["verts"][FP_VERTEX_UID],
            layers["verts"][FP_VERTEX_REGION],
        ),
        (
            bm.edges,
            "edges",
            layers["edges"][FP_EDGE_UID],
            layers["edges"][FP_EDGE_REGION],
        ),
        (
            bm.faces,
            "faces",
            layers["faces"][FP_FACE_UID],
            layers["faces"][FP_FACE_REGION],
        ),
    ):
        for element in elements:
            uid = int(element[uid_layer])
            if uid <= 0:
                continue
            owners = owner_maps[domain].get(uid, ())
            element[owner_layer] = min(owners) if owners else 0

    edge_by_uid = {
        int(edge[layers["edges"][FP_EDGE_UID]]): edge
        for edge in bm.edges
        if int(edge[layers["edges"][FP_EDGE_UID]]) > 0
    }
    boundary_edge_uids = set()
    for boundary in registry.boundary_bindings.values():
        owner_count = len(set(boundary.get("region_uuids", ())))
        boundary_local_id = int(boundary["local_id"])
        role = (
            EdgeRole.SHARED_BOUNDARY
            if owner_count > 1
            else EdgeRole.OUTER_BOUNDARY
        )
        for uid in boundary.get("edge_uids", ()):
            uid = int(uid)
            edge = edge_by_uid.get(uid)
            if edge is None:
                continue
            boundary_edge_uids.add(uid)
            edge[layers["edges"][FP_EDGE_BOUNDARY_KEY]] = boundary_local_id
            edge[layers["edges"][FP_EDGE_ROLE]] = int(role)
    for uid, edge in edge_by_uid.items():
        if uid in boundary_edge_uids:
            continue
        edge[layers["edges"][FP_EDGE_BOUNDARY_KEY]] = 0
        edge[layers["edges"][FP_EDGE_ROLE]] = int(EdgeRole.INTERIOR)


def retire_region_bindings(obj, bm, cycle_keys):
    registry = load_binding_registry(obj)
    if registry is None:
        return None
    registry = clone_binding_registry(registry)
    removed = []
    for cycle_key in sorted({str(value) for value in cycle_keys}):
        region_uuid = registry.region_uuid_by_cycle_key.pop(cycle_key, None)
        if not region_uuid:
            continue
        registry.regions.pop(region_uuid, None)
        removed.append(region_uuid)
        for boundary_key in tuple(registry.boundary_bindings):
            record = registry.boundary_bindings[boundary_key]
            owners = [
                value
                for value in record.get("region_uuids", ())
                if value != region_uuid
            ]
            if owners:
                record["region_uuids"] = sorted(set(owners))
                continue
            registry.boundary_bindings.pop(boundary_key, None)
            registry.boundary_local_ids.pop(boundary_key, None)
            registry.retired_boundary_keys = tuple(
                sorted(set(registry.retired_boundary_keys) | {boundary_key})
            )
    if removed:
        registry.retired_region_uuids = tuple(
            sorted(set(registry.retired_region_uuids) | set(removed))
        )
        _refresh_boundary_membership(registry)
        layers = binding_layers(bm)
        missing_layers = [
            name
            for domain in layers.values()
            for name, layer in domain.items()
            if layer is None
        ]
        if missing_layers:
            raise BindingRegistryError(
                "BINDING_ELEMENT_MISSING",
                "Binding retirement requires all custom-data layers: "
                + ", ".join(sorted(set(missing_layers))),
            )
        _reassign_surviving_element_owners(registry, bm, layers)
        registry.revision += 1
        audit = audit_binding_registry(obj, bm, registry=registry)
        if audit["issues"]:
            raise BindingRegistryError(
                "BINDING_AUDIT_FAILED",
                audit["issues"][0]["message"],
            )
        save_binding_registry(obj, registry)
    return registry


def _issue(reason_code, message, region_uuid=""):
    return {
        "reason_code": str(reason_code),
        "message": str(message),
        "region_uuid": str(region_uuid),
    }


def audit_binding_registry(obj, bm, guides=None, registry=None):
    issues = []
    try:
        registry = registry or load_binding_registry(obj)
    except BindingRegistryError as exc:
        return {
            "binding_schema_version": 0,
            "migration_state": BindingMigrationState.CORRUPT.value,
            "region_count": 0,
            "bound_vertex_count": 0,
            "bound_edge_count": 0,
            "bound_face_count": 0,
            "duplicate_uid_count": 0,
            "missing_element_count": 0,
            "orphan_element_count": 0,
            "shared_boundary_mismatch_count": 0,
            "issue_count": 1,
            "issues": [_issue(exc.reason_code, str(exc))],
        }
    if registry is None:
        return {
            "binding_schema_version": 0,
            "migration_state": "MISSING",
            "region_count": 0,
            "bound_vertex_count": 0,
            "bound_edge_count": 0,
            "bound_face_count": 0,
            "duplicate_uid_count": 0,
            "missing_element_count": 0,
            "orphan_element_count": 0,
            "shared_boundary_mismatch_count": 0,
            "issue_count": 1,
            "issues": [
                _issue(
                    "BINDING_REGION_MISSING",
                    "FlowPatch project has no PF-02 BindingRegistry.",
                )
            ],
        }
    layers = binding_layers(bm)
    missing_layers = [
        name
        for domain in layers.values()
        for name, layer in domain.items()
        if layer is None
    ]
    if missing_layers:
        return {
            "binding_schema_version": BINDING_SCHEMA_VERSION,
            "migration_state": str(registry.migration_state),
            "registry_revision": int(registry.revision),
            "project_uuid": str(registry.project_uuid),
            "region_count": len(registry.regions),
            "bound_vertex_count": 0,
            "bound_edge_count": 0,
            "bound_face_count": 0,
            "duplicate_uid_count": 0,
            "missing_element_count": 0,
            "orphan_element_count": 0,
            "shared_boundary_count": 0,
            "shared_boundary_mismatch_count": 0,
            "allocator": registry.allocator.as_dict(),
            "migration_notes": list(registry.migration_notes),
            "issue_count": 1,
            "issues": [
                _issue(
                    "BINDING_ELEMENT_MISSING",
                    "Binding custom-data layer(s) are missing: "
                    + ", ".join(sorted(set(missing_layers))),
                )
            ],
        }
    for label, mapping in (
        ("GuideNode", registry.guide_node_local_ids),
        ("GuideEdge", registry.guide_edge_local_ids),
        ("Anchor", registry.anchor_local_ids),
        ("Boundary", registry.boundary_local_ids),
        ("Solver kind", registry.solver_kind_local_ids),
    ):
        values = tuple(int(value) for value in mapping.values())
        if len(values) != len(set(values)):
            issues.append(
                _issue(
                    "BINDING_UID_COLLISION",
                    f"{label} local IDs are duplicated.",
                )
            )
    region_local_ids = tuple(
        int(region.region_local_id) for region in registry.regions.values()
    )
    if len(region_local_ids) != len(set(region_local_ids)):
        issues.append(
            _issue(
                "BINDING_UID_COLLISION",
                "Region local IDs are duplicated.",
            )
        )
    active_retired_pairs = (
        (
            "Region",
            set(registry.regions),
            set(registry.retired_region_uuids),
        ),
        (
            "GuideNode",
            set(registry.guide_node_local_ids),
            set(registry.retired_guide_node_uuids),
        ),
        (
            "GuideEdge",
            set(registry.guide_edge_local_ids),
            set(registry.retired_guide_edge_uuids),
        ),
        (
            "Anchor",
            set(registry.anchor_local_ids),
            set(registry.retired_anchor_uuids),
        ),
        (
            "Boundary",
            set(registry.boundary_bindings),
            set(registry.retired_boundary_keys),
        ),
    )
    for label, active, retired in active_retired_pairs:
        if active & retired:
            issues.append(
                _issue(
                    "BINDING_UID_COLLISION",
                    f"Active and retired {label} identities overlap.",
                )
            )
    if not set(registry.guide_node_uuid_by_graph_id.values()).issubset(
        registry.guide_node_local_ids
    ):
        issues.append(
            _issue(
                "BINDING_REGION_MISSING",
                "GuideGraph references an unknown persistent GuideNode UUID.",
            )
        )
    if not set(registry.guide_edge_uuid_by_graph_id.values()).issubset(
        registry.guide_edge_local_ids
    ):
        issues.append(
            _issue(
                "BINDING_REGION_MISSING",
                "GuideGraph references an unknown persistent GuideEdge UUID.",
            )
        )
    if not set(registry.anchor_uuid_by_key.values()).issubset(
        registry.anchor_local_ids
    ):
        issues.append(
            _issue(
                "BINDING_REGION_MISSING",
                "Anchor lookup references an unknown persistent Anchor UUID.",
            )
        )
    vertex_map, duplicate_vertices = _domain_uid_state(
        bm.verts,
        layers["verts"][FP_VERTEX_UID],
    )
    edge_map, duplicate_edges = _domain_uid_state(
        bm.edges,
        layers["edges"][FP_EDGE_UID],
    )
    face_map, duplicate_faces = _domain_uid_state(
        bm.faces,
        layers["faces"][FP_FACE_UID],
    )
    duplicate_count = sum(
        len(values)
        for values in (
            duplicate_vertices,
            duplicate_edges,
            duplicate_faces,
        )
    )
    if duplicate_count:
        issues.append(
            _issue(
                "BINDING_UID_COLLISION",
                f"Found {duplicate_count} duplicated persistent element UID(s).",
            )
        )

    region_ids = {
        int(region.region_local_id): region
        for region in registry.regions.values()
    }
    bound_vertices = set()
    bound_edges = set()
    bound_faces = set()
    missing_count = 0
    face_owners = {}
    element_region_memberships = {
        "vertex": {},
        "edge": {},
        "face": {},
    }
    for region_uuid, region in sorted(registry.regions.items()):
        if region_uuid != region.region_uuid:
            issues.append(
                _issue(
                    "BINDING_REGION_MISSING",
                    "Region dictionary key does not match Region UUID.",
                    region_uuid,
                )
            )
        if region.project_uuid != registry.project_uuid:
            issues.append(
                _issue(
                    "BINDING_REGION_MISSING",
                    "Region project UUID does not match its registry.",
                    region_uuid,
                )
            )
        for uid, mapping, label in (
            (region.vertex_uids, vertex_map, "vertex"),
            (region.edge_uids, edge_map, "edge"),
            (region.face_uids, face_map, "face"),
        ):
            missing = sorted(set(uid) - set(mapping))
            if missing:
                missing_count += len(missing)
                issues.append(
                    _issue(
                        "BINDING_ELEMENT_MISSING",
                        f"Region references missing {label} UID(s): {missing[:8]}.",
                        region_uuid,
                    )
                )
        bound_vertices.update(region.vertex_uids)
        bound_edges.update(region.edge_uids)
        bound_faces.update(region.face_uids)
        for domain, values in (
            ("vertex", region.vertex_uids),
            ("edge", region.edge_uids),
            ("face", region.face_uids),
        ):
            for uid in values:
                element_region_memberships[domain].setdefault(
                    int(uid),
                    set(),
                ).add(int(region.region_local_id))
        for uid in region.face_uids:
            face_owners.setdefault(int(uid), set()).add(region_uuid)
        for guide_uuid, vertex_uid in region.guide_node_to_vertex_uid.items():
            if guide_uuid not in registry.guide_node_local_ids:
                issues.append(
                    _issue(
                        "BINDING_REGION_MISSING",
                        "Region maps an unknown GuideNode UUID.",
                        region_uuid,
                    )
                )
            if (
                int(vertex_uid) not in region.vertex_uids
                or int(vertex_uid) not in vertex_map
            ):
                issues.append(
                    _issue(
                        "BINDING_ELEMENT_MISSING",
                        "GuideNode mapping does not resolve inside its Region.",
                        region_uuid,
                    )
                )
        for guide_uuid, path in (
            region.guide_edge_to_ordered_vertex_uids.items()
        ):
            if guide_uuid not in registry.guide_edge_local_ids:
                issues.append(
                    _issue(
                        "BINDING_REGION_MISSING",
                        "Region maps an unknown GuideEdge UUID.",
                        region_uuid,
                    )
                )
            if len(path) < 2:
                issues.append(
                    _issue(
                        "BINDING_BOUNDARY_MISMATCH",
                        "GuideEdge mapping has no ordered vertex path.",
                        region_uuid,
                    )
                )
            if not set(int(value) for value in path).issubset(
                set(int(value) for value in region.vertex_uids)
            ):
                issues.append(
                    _issue(
                        "BINDING_ELEMENT_MISSING",
                        "GuideEdge mapping leaves its owning Region.",
                        region_uuid,
                    )
                )
            for start, end in zip(path, path[1:]):
                start_vert = vertex_map.get(int(start))
                end_vert = vertex_map.get(int(end))
                if (
                    start_vert is None
                    or end_vert is None
                    or bm.edges.get((start_vert, end_vert)) is None
                ):
                    issues.append(
                        _issue(
                            "BINDING_BOUNDARY_MISMATCH",
                            "GuideEdge ordered UID path is not continuous.",
                            region_uuid,
                        )
                    )
                    break
        for vertex_uid, anchor_uuid in region.vertex_uid_to_anchor_id.items():
            if anchor_uuid not in registry.anchor_local_ids:
                issues.append(
                    _issue(
                        "BINDING_REGION_MISSING",
                        "Region maps an unknown Anchor UUID.",
                        region_uuid,
                    )
                )
            if (
                int(vertex_uid) not in region.vertex_uids
                or int(vertex_uid) not in vertex_map
            ):
                issues.append(
                    _issue(
                        "BINDING_ELEMENT_MISSING",
                        "Anchor mapping does not resolve inside its Region.",
                        region_uuid,
                    )
                )
        if int(region.generation) <= 0:
            issues.append(
                _issue(
                    "BINDING_REGION_MISSING",
                    "Region generation must be positive.",
                    region_uuid,
                )
            )
        if int(region.guide_revision) <= 0 or int(region.mesh_revision) <= 0:
            issues.append(
                _issue(
                    "BINDING_REGION_MISSING",
                    "Region guide and mesh revisions must be positive.",
                    region_uuid,
                )
            )
        for uid in region.vertex_uids:
            vert = vertex_map.get(int(uid))
            if vert is not None and int(
                vert[layers["verts"][FP_VERTEX_GENERATION]]
            ) < int(region.generation):
                issues.append(
                    _issue(
                        "BINDING_REGION_MISSING",
                        "Bound vertex generation predates its Region.",
                        region_uuid,
                    )
                )
                break
        for uid in region.edge_uids:
            edge = edge_map.get(int(uid))
            if edge is not None and int(
                edge[layers["edges"][FP_EDGE_GENERATION]]
            ) < int(region.generation):
                issues.append(
                    _issue(
                        "BINDING_REGION_MISSING",
                        "Bound edge generation predates its Region.",
                        region_uuid,
                    )
                )
                break
        for uid in region.face_uids:
            face = face_map.get(int(uid))
            if face is None:
                continue
            if len(face.verts) != 4:
                issues.append(
                    _issue(
                        "BINDING_BOUNDARY_MISMATCH",
                        "A committed Region face is not a quad.",
                        region_uuid,
                    )
                )
            if int(face[layers["faces"][FP_FACE_GENERATION]]) != int(
                region.generation
            ):
                issues.append(
                    _issue(
                        "BINDING_REGION_MISSING",
                        "Bound face generation does not match its Region.",
                        region_uuid,
                    )
                )

    duplicate_face_owners = {
        uid: owners
        for uid, owners in face_owners.items()
        if len(owners) > 1
    }
    if duplicate_face_owners:
        issues.append(
            _issue(
                "BINDING_UID_COLLISION",
                "A committed face is owned by multiple Regions.",
            )
        )

    orphan_count = (
        len(set(vertex_map) - bound_vertices)
        + len(set(edge_map) - bound_edges)
        + len(set(face_map) - bound_faces)
    )
    if orphan_count:
        issues.append(
            _issue(
                "BINDING_REGION_MISSING",
                f"Found {orphan_count} persistent UID(s) without Region ownership.",
            )
        )

    for element, domain, uid_layer, region_layer in (
        (
            bm.verts,
            "vertex",
            layers["verts"][FP_VERTEX_UID],
            layers["verts"][FP_VERTEX_REGION],
        ),
        (
            bm.edges,
            "edge",
            layers["edges"][FP_EDGE_UID],
            layers["edges"][FP_EDGE_REGION],
        ),
        (
            bm.faces,
            "face",
            layers["faces"][FP_FACE_UID],
            layers["faces"][FP_FACE_REGION],
        ),
    ):
        for item in element:
            if int(item[uid_layer]) <= 0:
                continue
            uid = int(item[uid_layer])
            owner = int(item[region_layer])
            memberships = element_region_memberships[domain].get(uid, set())
            if (
                owner <= 0
                or owner not in region_ids
                or owner not in memberships
            ):
                issues.append(
                    _issue(
                        "BINDING_REGION_MISSING",
                        (
                            f"Persistent {domain} UID has no valid scalar "
                            "Region owner."
                        ),
                    )
                )
                break

    maxima = {
        "VERTEX": max(vertex_map, default=0),
        "EDGE": max(edge_map, default=0),
        "FACE": max(face_map, default=0),
        "REGION": max(region_ids, default=0),
        "BOUNDARY": max(registry.boundary_local_ids.values(), default=0),
        "ANCHOR": max(registry.anchor_local_ids.values(), default=0),
        "SOLVER_KIND": max(
            registry.solver_kind_local_ids.values(),
            default=0,
        ),
        "GUIDE_NODE": max(
            registry.guide_node_local_ids.values(),
            default=0,
        ),
        "GUIDE_EDGE": max(
            registry.guide_edge_local_ids.values(),
            default=0,
        ),
    }
    allocator_values = registry.allocator.as_dict()
    allocator_fields = ElementUIDAllocator._FIELD_BY_DOMAIN
    for domain, maximum in maxima.items():
        if allocator_values[allocator_fields[domain]] <= maximum:
            issues.append(
                _issue(
                    "BINDING_UID_COLLISION",
                    f"{domain.title()} allocator does not exceed UID {maximum}.",
                )
            )

    shared_mismatch_count = 0
    if set(registry.boundary_local_ids) != set(registry.boundary_bindings):
        shared_mismatch_count += 1
        issues.append(
            _issue(
                "BINDING_BOUNDARY_MISMATCH",
                "Boundary local-ID and binding tables have different keys.",
            )
        )
    boundary_paths = {}
    for boundary_key, boundary in registry.boundary_bindings.items():
        path = tuple(int(value) for value in boundary.get("vertex_uids", ()))
        edge_path = tuple(int(value) for value in boundary.get("edge_uids", ()))
        boundary_local_id = int(boundary.get("local_id", 0))
        if registry.boundary_local_ids.get(boundary_key) != boundary_local_id:
            shared_mismatch_count += 1
            issues.append(
                _issue(
                    "BINDING_BOUNDARY_MISMATCH",
                    f"Boundary {boundary_key} has inconsistent local identity.",
                )
            )
        try:
            expected_key, _forward = normalized_boundary_key(
                registry.project_uuid,
                boundary.get("guide_edge_uuids", ()),
                boundary.get("endpoint_node_uuids", ()),
                boundary.get("segment_count", 0),
            )
        except BindingRegistryError as exc:
            expected_key = ""
            shared_mismatch_count += 1
            issues.append(_issue(exc.reason_code, str(exc)))
        if expected_key and expected_key != boundary_key:
            shared_mismatch_count += 1
            issues.append(
                _issue(
                    "BINDING_BOUNDARY_MISMATCH",
                    f"Boundary {boundary_key} does not match its UUID chain.",
                )
            )
        canonical_path = min(
            (path, edge_path),
            (tuple(reversed(path)), tuple(reversed(edge_path))),
        )
        previous_key = boundary_paths.setdefault(canonical_path, boundary_key)
        if previous_key != boundary_key:
            shared_mismatch_count += 1
            issues.append(
                _issue(
                    "BINDING_BOUNDARY_MISMATCH",
                    "Two boundary identities claim the same committed path.",
                )
            )
        if len(path) < 2 or len(edge_path) != len(path) - 1:
            shared_mismatch_count += 1
            issues.append(
                _issue(
                    "BINDING_BOUNDARY_MISMATCH",
                    f"Boundary {boundary_key} has inconsistent UID counts.",
                )
            )
            continue
        for index, (start, end) in enumerate(zip(path, path[1:])):
            start_vert = vertex_map.get(start)
            end_vert = vertex_map.get(end)
            edge = (
                bm.edges.get((start_vert, end_vert))
                if start_vert is not None and end_vert is not None
                else None
            )
            if edge is None or int(
                edge[layers["edges"][FP_EDGE_UID]]
            ) != edge_path[index]:
                shared_mismatch_count += 1
                issues.append(
                    _issue(
                        "BINDING_BOUNDARY_MISMATCH",
                        f"Boundary {boundary_key} does not resolve continuously.",
                    )
                )
                break
        owners = set(boundary.get("region_uuids", ()))
        if not owners or not owners.issubset(registry.regions):
            shared_mismatch_count += 1
            issues.append(
                _issue(
                    "BINDING_REGION_MISSING",
                    f"Boundary {boundary_key} references an unknown Region.",
                )
            )
        if len(owners) > 2:
            shared_mismatch_count += 1
            issues.append(
                _issue(
                    "BINDING_BOUNDARY_MISMATCH",
                    f"Boundary {boundary_key} has more than two owners.",
                )
            )
        for owner_uuid in sorted(owners & set(registry.regions)):
            owner = registry.regions[owner_uuid]
            if not set(path).issubset(owner.vertex_uids) or not set(
                edge_path
            ).issubset(owner.edge_uids):
                shared_mismatch_count += 1
                issues.append(
                    _issue(
                        "BINDING_BOUNDARY_MISMATCH",
                        (
                            f"Boundary {boundary_key} is outside owning "
                            "Region elements."
                        ),
                        owner_uuid,
                    )
                )
        expected_role = (
            EdgeRole.SHARED_BOUNDARY
            if len(owners) > 1
            else EdgeRole.OUTER_BOUNDARY
        )
        for uid in edge_path:
            edge = edge_map.get(uid)
            if edge is None:
                continue
            if (
                int(edge[layers["edges"][FP_EDGE_BOUNDARY_KEY]])
                != boundary_local_id
                or int(edge[layers["edges"][FP_EDGE_ROLE]])
                != int(expected_role)
            ):
                shared_mismatch_count += 1
                issues.append(
                    _issue(
                        "BINDING_BOUNDARY_MISMATCH",
                        f"Boundary {boundary_key} custom data is inconsistent.",
                    )
                )
                break

    if guides is not None:
        node_ids, edge_ids = _active_graph_ids(guides)
        if set(int(key) for key in registry.guide_node_uuid_by_graph_id) != node_ids:
            issues.append(
                _issue(
                    "BINDING_REGION_MISSING",
                    "Active GuideNode identity table does not match GuideGraph.",
                )
            )
        if set(int(key) for key in registry.guide_edge_uuid_by_graph_id) != edge_ids:
            issues.append(
                _issue(
                    "BINDING_REGION_MISSING",
                    "Active GuideEdge identity table does not match GuideGraph.",
                )
            )

    unique_issues = {
        (
            issue["reason_code"],
            issue["message"],
            issue["region_uuid"],
        ): issue
        for issue in issues
    }
    issues = [unique_issues[key] for key in sorted(unique_issues)]
    return {
        "binding_schema_version": BINDING_SCHEMA_VERSION,
        "migration_state": str(registry.migration_state),
        "registry_revision": int(registry.revision),
        "project_uuid": str(registry.project_uuid),
        "region_count": len(registry.regions),
        "bound_vertex_count": len(bound_vertices),
        "bound_edge_count": len(bound_edges),
        "bound_face_count": len(bound_faces),
        "duplicate_uid_count": duplicate_count,
        "missing_element_count": missing_count,
        "orphan_element_count": orphan_count,
        "shared_boundary_count": sum(
            1
            for value in registry.boundary_bindings.values()
            if len(set(value.get("region_uuids", ()))) > 1
        ),
        "shared_boundary_mismatch_count": shared_mismatch_count,
        "allocator": registry.allocator.as_dict(),
        "migration_notes": list(registry.migration_notes),
        "issue_count": len(issues),
        "issues": issues,
    }


def audit_binding_registry_object(obj, guides=None):
    import bmesh

    if obj is None or getattr(obj, "type", "") != "MESH":
        return {
            "binding_schema_version": 0,
            "migration_state": "NO_PROJECT_OBJECT",
            "region_count": 0,
            "bound_vertex_count": 0,
            "bound_edge_count": 0,
            "bound_face_count": 0,
            "duplicate_uid_count": 0,
            "missing_element_count": 0,
            "orphan_element_count": 0,
            "shared_boundary_mismatch_count": 0,
            "issue_count": 0,
            "issues": [],
        }
    if getattr(obj, "mode", "") == "EDIT":
        return audit_binding_registry(
            obj,
            bmesh.from_edit_mesh(obj.data),
            guides=guides,
        )
    bm = bmesh.new()
    try:
        bm.from_mesh(obj.data)
        return audit_binding_registry(obj, bm, guides=guides)
    finally:
        bm.free()


def rekey_binding_registry(registry, project_uuid, uuid_factory=new_uuid):
    registry = clone_binding_registry(registry)
    project_uuid = _canonical_uuid(project_uuid, "Copied project UUID")
    node_map = {
        old: _canonical_uuid(uuid_factory(), "Copied GuideNode UUID")
        for old in registry.guide_node_local_ids
    }
    edge_map = {
        old: _canonical_uuid(uuid_factory(), "Copied GuideEdge UUID")
        for old in registry.guide_edge_local_ids
    }
    anchor_map = {
        old: _canonical_uuid(uuid_factory(), "Copied Anchor UUID")
        for old in registry.anchor_local_ids
    }
    region_map = {
        old: _canonical_uuid(uuid_factory(), "Copied Region UUID")
        for old in registry.regions
    }
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
            for key, value in region.guide_edge_to_ordered_vertex_uids.items()
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
    rebuilt_boundaries = {}
    rebuilt_local_ids = {}
    boundary_key_map = {}
    for old_key, value in registry.boundary_bindings.items():
        edge_uuids = tuple(edge_map[item] for item in value["guide_edge_uuids"])
        node_uuids = tuple(node_map[item] for item in value["endpoint_node_uuids"])
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
            rebuilt["vertex_uids"] = list(reversed(rebuilt["vertex_uids"]))
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
    return registry
