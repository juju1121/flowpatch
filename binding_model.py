import hashlib
import json
import uuid
from dataclasses import dataclass
from dataclasses import field
from enum import Enum
from enum import IntEnum


BINDING_SCHEMA_VERSION = 1


class BindingRegistryError(RuntimeError):
    def __init__(self, reason_code, message):
        super().__init__(str(message))
        self.reason_code = str(reason_code)


class BindingMigrationState(str, Enum):
    CURRENT = "CURRENT"
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


def canonical_uuid(value, label="UUID"):
    try:
        return str(uuid.UUID(str(value or "").strip()))
    except (AttributeError, TypeError, ValueError) as exc:
        raise BindingRegistryError(
            "BINDING_SERIALIZATION_FAILED",
            f"{label} is missing or malformed.",
        ) from exc


def positive_int(value, label="Value"):
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


def _int_set(values):
    return {int(value) for value in tuple(values or ())}


def _string_tuple(values):
    return tuple(str(value) for value in tuple(values or ()))


def _uuid_tuple(values, label):
    return tuple(canonical_uuid(value, label) for value in tuple(values or ()))


def _uuid_int_mapping(payload, label):
    return {
        canonical_uuid(key, label): positive_int(value, f"{label} local ID")
        for key, value in dict(payload or {}).items()
    }


def _int_uuid_mapping(payload, label):
    return {
        str(positive_int(key, f"{label} graph ID")): canonical_uuid(
            value,
            label,
        )
        for key, value in dict(payload or {}).items()
    }


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
        field_name = self._FIELD_BY_DOMAIN.get(str(domain).upper())
        if field_name is None:
            raise ValueError(f"Unknown UID allocator domain: {domain}")
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
        return cls(
            **{
                name: positive_int(
                    payload.get(name, 1),
                    f"Allocator field {name}",
                )
                for name in cls._FIELD_BY_DOMAIN.values()
            }
        )


@dataclass
class RegionBinding:
    project_uuid: str
    region_uuid: str
    region_local_id: int
    solver_kind: str = "GRID"
    generation: int = 1
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
            "project_uuid": canonical_uuid(
                self.project_uuid,
                "Region project UUID",
            ),
            "region_uuid": canonical_uuid(self.region_uuid, "Region UUID"),
            "region_local_id": positive_int(
                self.region_local_id,
                "Region local ID",
            ),
            "solver_kind": str(self.solver_kind),
            "generation": positive_int(self.generation, "Region generation"),
            "cycle_key": str(self.cycle_key),
            "guide_node_to_vertex_uid": {
                canonical_uuid(key, "GuideNode UUID"): int(value)
                for key, value in sorted(
                    self.guide_node_to_vertex_uid.items()
                )
            },
            "guide_edge_to_ordered_vertex_uids": {
                canonical_uuid(key, "GuideEdge UUID"): [
                    int(value) for value in values
                ]
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
                str(int(key)): canonical_uuid(value, "Anchor UUID")
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
            project_uuid=canonical_uuid(
                payload.get("project_uuid"),
                "Region project UUID",
            ),
            region_uuid=canonical_uuid(
                payload.get("region_uuid"),
                "Region UUID",
            ),
            region_local_id=positive_int(
                payload.get("region_local_id"),
                "Region local ID",
            ),
            solver_kind=str(payload.get("solver_kind", "GRID")),
            generation=positive_int(
                payload.get("generation", 1),
                "Region generation",
            ),
            cycle_key=str(payload.get("cycle_key", "")),
            guide_node_to_vertex_uid={
                canonical_uuid(key, "GuideNode UUID"): int(value)
                for key, value in dict(
                    payload.get("guide_node_to_vertex_uid", {})
                ).items()
            },
            guide_edge_to_ordered_vertex_uids={
                canonical_uuid(key, "GuideEdge UUID"): tuple(
                    int(value) for value in values
                )
                for key, values in dict(
                    payload.get("guide_edge_to_ordered_vertex_uids", {})
                ).items()
            },
            vertex_uid_to_param_uv={
                int(key): (float(value[0]), float(value[1]))
                for key, value in dict(
                    payload.get("vertex_uid_to_param_uv", {})
                ).items()
            },
            vertex_uid_to_anchor_id={
                int(key): canonical_uuid(value, "Anchor UUID")
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


def canonical_boundary_record(value):
    value = dict(value or {})
    return {
        "local_id": positive_int(
            value.get("local_id", 1),
            "Boundary local ID",
        ),
        "guide_edge_uuids": [
            canonical_uuid(item, "Boundary GuideEdge UUID")
            for item in value.get("guide_edge_uuids", ())
        ],
        "endpoint_node_uuids": [
            canonical_uuid(item, "Boundary GuideNode UUID")
            for item in value.get("endpoint_node_uuids", ())
        ],
        "segment_count": positive_int(
            value.get("segment_count", 1),
            "Boundary segment count",
        ),
        "vertex_uids": [
            positive_int(item, "Boundary vertex UID")
            for item in value.get("vertex_uids", ())
        ],
        "edge_uids": [
            positive_int(item, "Boundary edge UID")
            for item in value.get("edge_uids", ())
        ],
        "region_uuids": sorted(
            canonical_uuid(item, "Boundary Region UUID")
            for item in value.get("region_uuids", ())
        ),
    }


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
            project_uuid=canonical_uuid(project_uuid, "Project UUID"),
            allocator=ElementUIDAllocator(),
        )

    def as_dict(self):
        return {
            "schema_version": BINDING_SCHEMA_VERSION,
            "project_uuid": canonical_uuid(
                self.project_uuid,
                "BindingRegistry project UUID",
            ),
            "allocator": self.allocator.as_dict(),
            "regions": {
                canonical_uuid(key, "Region UUID"): value.as_dict()
                for key, value in sorted(self.regions.items())
            },
            "guide_node_local_ids": {
                canonical_uuid(key, "GuideNode UUID"): int(value)
                for key, value in sorted(self.guide_node_local_ids.items())
            },
            "guide_edge_local_ids": {
                canonical_uuid(key, "GuideEdge UUID"): int(value)
                for key, value in sorted(self.guide_edge_local_ids.items())
            },
            "anchor_local_ids": {
                canonical_uuid(key, "Anchor UUID"): int(value)
                for key, value in sorted(self.anchor_local_ids.items())
            },
            "guide_node_uuid_by_graph_id": dict(
                sorted(
                    self.guide_node_uuid_by_graph_id.items(),
                    key=lambda item: int(item[0]),
                )
            ),
            "guide_edge_uuid_by_graph_id": dict(
                sorted(
                    self.guide_edge_uuid_by_graph_id.items(),
                    key=lambda item: int(item[0]),
                )
            ),
            "anchor_uuid_by_key": dict(sorted(self.anchor_uuid_by_key.items())),
            "region_uuid_by_cycle_key": dict(
                sorted(self.region_uuid_by_cycle_key.items())
            ),
            "boundary_local_ids": {
                str(key): int(value)
                for key, value in sorted(self.boundary_local_ids.items())
            },
            "boundary_bindings": {
                str(key): canonical_boundary_record(value)
                for key, value in sorted(self.boundary_bindings.items())
            },
            "solver_kind_local_ids": {
                str(key): int(value)
                for key, value in sorted(self.solver_kind_local_ids.items())
            },
            "migration_state": str(self.migration_state),
            "migration_notes": sorted(str(v) for v in self.migration_notes),
            "revision": max(1, int(self.revision)),
            "retired_region_uuids": sorted(self.retired_region_uuids),
            "retired_guide_node_uuids": sorted(
                self.retired_guide_node_uuids
            ),
            "retired_guide_edge_uuids": sorted(
                self.retired_guide_edge_uuids
            ),
            "retired_anchor_uuids": sorted(self.retired_anchor_uuids),
            "retired_boundary_keys": sorted(self.retired_boundary_keys),
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
                f"Unsupported BindingRegistry schema {schema_version}.",
            )
        project_uuid = canonical_uuid(
            payload.get("project_uuid"),
            "BindingRegistry project UUID",
        )
        return cls(
            schema_version=schema_version,
            project_uuid=project_uuid,
            allocator=ElementUIDAllocator.from_dict(
                payload.get("allocator", {})
            ),
            regions={
                canonical_uuid(key, "Region UUID"): RegionBinding.from_dict(
                    value
                )
                for key, value in dict(payload.get("regions", {})).items()
            },
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
                str(key): canonical_uuid(value, "Anchor UUID")
                for key, value in dict(
                    payload.get("anchor_uuid_by_key", {})
                ).items()
            },
            region_uuid_by_cycle_key={
                str(key): canonical_uuid(value, "Region UUID")
                for key, value in dict(
                    payload.get("region_uuid_by_cycle_key", {})
                ).items()
            },
            boundary_local_ids={
                str(key): positive_int(value, "Boundary local ID")
                for key, value in dict(
                    payload.get("boundary_local_ids", {})
                ).items()
            },
            boundary_bindings={
                str(key): canonical_boundary_record(value)
                for key, value in dict(
                    payload.get("boundary_bindings", {})
                ).items()
            },
            solver_kind_local_ids={
                str(key): positive_int(value, "Solver kind local ID")
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


def normalized_boundary_key(
    project_uuid,
    guide_edge_uuids,
    endpoint_node_uuids,
    segment_count,
):
    project_uuid = canonical_uuid(project_uuid, "Boundary project UUID")
    edges = tuple(
        canonical_uuid(value, "Boundary GuideEdge UUID")
        for value in guide_edge_uuids
    )
    nodes = tuple(
        canonical_uuid(value, "Boundary GuideNode UUID")
        for value in endpoint_node_uuids
    )
    segment_count = positive_int(segment_count, "Boundary segment count")
    if not edges or len(nodes) != 2:
        raise BindingRegistryError(
            "BINDING_BOUNDARY_MISMATCH",
            "A boundary identity needs guide edges and two endpoint nodes.",
        )
    forward = (edges, nodes)
    backward = (tuple(reversed(edges)), tuple(reversed(nodes)))
    canonical = min(forward, backward)
    payload = {
        "project_uuid": project_uuid,
        "guide_edge_uuids": list(canonical[0]),
        "endpoint_node_uuids": list(canonical[1]),
        "segment_count": segment_count,
    }
    digest = hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode(
            "ascii"
        )
    ).hexdigest()
    return f"b1:{digest}", forward == canonical
