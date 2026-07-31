from copy import deepcopy
from dataclasses import dataclass
import hashlib
import json
import math


SYNC_PARAMETRIC = "PARAMETRIC"
SYNC_FROZEN = "FROZEN"
SYNC_DETACHED = "DETACHED"


class MeshSyncError(ValueError):
    def __init__(self, reason_code, message):
        super().__init__(str(message))
        self.reason_code = str(reason_code)


@dataclass(frozen=True)
class GridTopology:
    vertex_uids: tuple
    edges: tuple
    faces: tuple
    signature: str


@dataclass(frozen=True)
class SyncDecision:
    state: str
    reason_code: str
    message: str
    changed_uids: tuple = ()

    @property
    def compatible(self):
        return self.state == SYNC_PARAMETRIC

    def as_dict(self):
        return {
            "state": self.state,
            "reason_code": self.reason_code,
            "message": self.message,
            "changed_uids": list(self.changed_uids),
        }


def canonical_edge(start_uid, end_uid):
    start_uid = int(start_uid)
    end_uid = int(end_uid)
    if start_uid <= 0 or end_uid <= 0 or start_uid == end_uid:
        raise MeshSyncError(
            "INVALID_EDGE_UIDS",
            "A synchronized edge needs two distinct positive vertex UIDs.",
        )
    return tuple(sorted((start_uid, end_uid)))


def canonical_face_cycle(vertex_uids):
    values = tuple(int(value) for value in vertex_uids)
    if len(values) < 3 or len(set(values)) != len(values):
        raise MeshSyncError(
            "INVALID_FACE_UIDS",
            "A synchronized face needs at least three unique vertex UIDs.",
        )
    if any(value <= 0 for value in values):
        raise MeshSyncError(
            "INVALID_FACE_UIDS",
            "A synchronized face references a missing vertex UID.",
        )
    forward = tuple(
        values[index:] + values[:index]
        for index in range(len(values))
    )
    reversed_values = tuple(reversed(values))
    backward = tuple(
        reversed_values[index:] + reversed_values[:index]
        for index in range(len(reversed_values))
    )
    return min(forward + backward)


def grid_topology(grid_vertex_uids):
    rows = tuple(
        tuple(int(value) for value in row)
        for row in tuple(grid_vertex_uids or ())
    )
    if len(rows) < 2:
        raise MeshSyncError(
            "SYNC_METADATA_MISSING",
            "The built cell has no synchronized vertex grid.",
        )
    width = len(rows[0])
    if width < 2 or any(len(row) != width for row in rows):
        raise MeshSyncError(
            "SYNC_GRID_INVALID",
            "The synchronized vertex grid is not rectangular.",
        )
    flattened = tuple(value for row in rows for value in row)
    if any(value <= 0 for value in flattened):
        raise MeshSyncError(
            "SYNC_GRID_INVALID",
            "The synchronized vertex grid contains a missing UID.",
        )
    if len(set(flattened)) != len(flattened):
        raise MeshSyncError(
            "SYNC_GRID_INVALID",
            "The synchronized vertex grid reuses a UID inside one cell.",
        )

    edges = set()
    faces = set()
    for row_index, row in enumerate(rows):
        for column_index in range(width - 1):
            edges.add(canonical_edge(row[column_index], row[column_index + 1]))
        if row_index >= len(rows) - 1:
            continue
        next_row = rows[row_index + 1]
        for column_index in range(width):
            edges.add(canonical_edge(row[column_index], next_row[column_index]))
        for column_index in range(width - 1):
            faces.add(
                canonical_face_cycle(
                    (
                        row[column_index],
                        row[column_index + 1],
                        next_row[column_index + 1],
                        next_row[column_index],
                    )
                )
            )

    payload = {
        "vertex_uids": sorted(flattened),
        "edges": sorted(edges),
        "faces": sorted(faces),
    }
    signature = hashlib.sha256(
        json.dumps(
            payload,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest().upper()
    return GridTopology(
        vertex_uids=tuple(sorted(flattened)),
        edges=tuple(sorted(edges)),
        faces=tuple(sorted(faces)),
        signature=signature,
    )


def _position_tuple(value):
    try:
        position = tuple(float(component) for component in value)
    except (TypeError, ValueError):
        return None
    if len(position) != 3 or not all(math.isfinite(item) for item in position):
        return None
    return position


def audit_grid_record(
    record,
    current_uids,
    current_edges,
    current_faces,
    current_positions,
    allowed_edges=(),
    allowed_faces=(),
    tolerance=1.0e-6,
):
    if not isinstance(record, dict):
        return SyncDecision(
            SYNC_FROZEN,
            "SYNC_METADATA_MISSING",
            "The built cell has no synchronization record.",
        )
    if str(record.get("state", "")).upper() == SYNC_DETACHED:
        return SyncDecision(
            SYNC_DETACHED,
            "SYNC_DETACHED",
            "The built cell is explicitly detached from guide synchronization.",
        )
    if str(record.get("topology_kind", "")).upper() != "GRID":
        return SyncDecision(
            SYNC_FROZEN,
            "SYNC_TOPOLOGY_UNSUPPORTED",
            "Only four-sided GRID cells support live synchronization.",
        )
    try:
        expected = grid_topology(record.get("grid_vertex_uids", ()))
    except MeshSyncError as exc:
        return SyncDecision(SYNC_FROZEN, exc.reason_code, str(exc))

    stored_signature = str(record.get("topology_signature", ""))
    if stored_signature and stored_signature != expected.signature:
        return SyncDecision(
            SYNC_FROZEN,
            "SYNC_SIGNATURE_MISMATCH",
            "The built cell synchronization signature is inconsistent.",
        )

    current_uid_set = {int(value) for value in current_uids}
    current_edge_set = {
        canonical_edge(*edge)
        for edge in tuple(current_edges or ())
    }
    try:
        current_face_set = {
            canonical_face_cycle(face)
            for face in tuple(current_faces or ())
        }
    except MeshSyncError as exc:
        return SyncDecision(SYNC_FROZEN, exc.reason_code, str(exc))
    expected_edges = set(expected.edges)
    expected_faces = set(expected.faces)
    allowed_edge_set = {
        canonical_edge(*edge)
        for edge in tuple(allowed_edges or expected.edges)
    }
    allowed_face_set = {
        canonical_face_cycle(face)
        for face in tuple(allowed_faces or expected.faces)
    }
    expected_uids = set(expected.vertex_uids)

    if not expected_uids.issubset(current_uid_set):
        return SyncDecision(
            SYNC_FROZEN,
            "SYNC_VERTEX_DELETED",
            "A UID-owned patch vertex was deleted.",
        )
    if not expected_edges.issubset(current_edge_set):
        return SyncDecision(
            SYNC_FROZEN,
            "SYNC_EDGE_TOPOLOGY_CHANGED",
            "A UID-owned patch edge was deleted or rewired.",
        )
    if not expected_faces.issubset(current_face_set):
        return SyncDecision(
            SYNC_FROZEN,
            "SYNC_FACE_TOPOLOGY_CHANGED",
            "A UID-owned quad face was deleted or rewired.",
        )

    unexpected_edges = {
        edge
        for edge in current_edge_set
        if expected_uids.intersection(edge) and edge not in allowed_edge_set
    }
    unexpected_faces = {
        face
        for face in current_face_set
        if expected_uids.intersection(face) and face not in allowed_face_set
    }
    if unexpected_edges or unexpected_faces:
        return SyncDecision(
            SYNC_FROZEN,
            "SYNC_DESTRUCTIVE_TOPOLOGY_EDIT",
            "New topology touches a synchronized patch region.",
        )

    stored_positions = record.get("mesh_positions", {})
    if not isinstance(stored_positions, dict):
        return SyncDecision(
            SYNC_FROZEN,
            "SYNC_POSITION_METADATA_MISSING",
            "The built cell has no last-synchronized vertex positions.",
        )
    changed = []
    tolerance_squared = float(tolerance) ** 2
    for uid in expected.vertex_uids:
        before = _position_tuple(stored_positions.get(str(uid)))
        after = _position_tuple(current_positions.get(uid))
        if before is None or after is None:
            return SyncDecision(
                SYNC_FROZEN,
                "SYNC_POSITION_METADATA_MISSING",
                "A synchronized vertex position is unavailable.",
            )
        distance_squared = sum(
            (left - right) ** 2
            for left, right in zip(before, after)
        )
        if distance_squared > tolerance_squared:
            changed.append(uid)

    if changed:
        return SyncDecision(
            SYNC_PARAMETRIC,
            "POSITION_EDIT",
            "Compatible vertex positions changed.",
            changed_uids=tuple(sorted(changed)),
        )
    return SyncDecision(
        SYNC_PARAMETRIC,
        "IN_SYNC",
        "Guide and mesh positions are synchronized.",
    )


def interpolate_control_bindings(
    bindings,
    uid_positions,
    tolerance=1.0e-6,
):
    proposals = {}
    tolerance_squared = float(tolerance) ** 2
    for binding in tuple(bindings or ()):
        if not isinstance(binding, dict):
            raise MeshSyncError(
                "CONTROL_BINDING_INVALID",
                "A guide control binding is malformed.",
            )
        try:
            guide_id = int(binding["guide_id"])
            point_index = int(binding["point_index"])
            uid_a = int(binding["uid_a"])
            uid_b = int(binding["uid_b"])
            factor = float(binding["factor"])
        except (KeyError, TypeError, ValueError) as exc:
            raise MeshSyncError(
                "CONTROL_BINDING_INVALID",
                "A guide control binding is incomplete.",
            ) from exc
        if guide_id <= 0 or point_index < 0 or not 0.0 <= factor <= 1.0:
            raise MeshSyncError(
                "CONTROL_BINDING_INVALID",
                "A guide control binding has an invalid owner or factor.",
            )
        start = _position_tuple(uid_positions.get(uid_a))
        end = _position_tuple(uid_positions.get(uid_b))
        if start is None or end is None:
            raise MeshSyncError(
                "CONTROL_BINDING_UID_MISSING",
                "A guide control binding references deleted mesh geometry.",
            )
        position = tuple(
            left + (right - left) * factor
            for left, right in zip(start, end)
        )
        key = (guide_id, point_index)
        previous = proposals.get(key)
        if previous is not None:
            distance_squared = sum(
                (left - right) ** 2
                for left, right in zip(previous, position)
            )
            if distance_squared > tolerance_squared:
                raise MeshSyncError(
                    "CONTROL_BINDING_CONFLICT",
                    "Adjacent regions propose different positions for one control.",
                )
        proposals[key] = position
    return proposals


def frozen_record(record, reason_code, message):
    updated = deepcopy(record) if isinstance(record, dict) else {}
    updated["state"] = SYNC_FROZEN
    updated["sync_reason_code"] = str(reason_code)
    updated["sync_message"] = str(message)
    updated["revision"] = int(updated.get("revision", 0)) + 1
    return updated


def detached_record(record):
    updated = deepcopy(record) if isinstance(record, dict) else {}
    updated["state"] = SYNC_DETACHED
    updated["sync_reason_code"] = "SYNC_DETACHED"
    updated["sync_message"] = (
        "Guide and mesh remain persistent but no longer update each other."
    )
    updated["revision"] = int(updated.get("revision", 0)) + 1
    return updated
