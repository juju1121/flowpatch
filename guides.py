import json
import math
from copy import deepcopy
from dataclasses import dataclass

import bmesh
from mathutils import Vector
from mathutils.geometry import tessellate_polygon

from .geometry import EDGE_ROLE_RAIL
from .geometry import EDGE_ROLE_SPOKE
from .geometry import FlowPatchGeometryError
from .geometry import _ensure_layers
from .geometry import _next_patch_id
from .geometry import ordered_selected_boundary
from .guide_graph import BoundarySide
from .guide_graph import GuideCycle
from .guide_graph import GuideGraphBudgetError
from .guide_graph import GuidePath
from .guide_graph import SurfaceAnchor
from .guide_graph import closest_points_on_segments
from .guide_graph import clone_guides
from .guide_graph import ensure_graph_ids
from .guide_graph import find_closed_cycles
from .guide_graph import find_four_sided_cycles
from .guide_graph import graph_nodes
from .guide_graph import next_guide_id
from .guide_graph import next_logical_side_id
from .guide_graph import next_node_id
from .guide_graph import oriented_side_points
from .guide_graph import replace_node_id
from .guide_graph import split_guide
from .guide_graph import split_intersections
from .guide_graph import with_normalized_winding
from .region_solver import RegionSolverError
from .region_solver import bounded_face_signature
from .region_solver import normalize_target_local_winding
from .mesh_sync import audit_grid_record
from .mesh_sync import canonical_edge
from .mesh_sync import canonical_face_cycle
from .mesh_sync import detached_record
from .mesh_sync import frozen_record
from .mesh_sync import grid_topology
from .mesh_sync import interpolate_control_bindings
from .mesh_sync import MeshSyncError
from .mesh_sync import SYNC_DETACHED
from .mesh_sync import SYNC_FROZEN
from .mesh_sync import SYNC_PARAMETRIC
from .mesh_sync import SyncDecision


GUIDE_DATA_KEY = "flowpatch_guide_network_v1"
BUILT_CELL_DATA_KEY = "flowpatch_built_guide_cells_v1"
BOUNDARY_REGISTRY_KEY = "flowpatch_boundary_registry_v1"
NODE_VERTEX_REGISTRY_KEY = "flowpatch_node_vertex_registry_v1"
VERTEX_UID_COUNTER_KEY = "flowpatch_vertex_uid_counter_v1"
VERTEX_UID_LAYER = "flowpatch_vertex_uid"
MAX_PREVIEW_CELLS = 128
MAX_PREVIEW_FACES = 100_000
from .v140_backend import (
    is_convex_ring,
    matched_annular_quad_patch,
    radial_quad_fan,
    triangulated_quad_patch,
)

ENABLE_LEGACY_POLYGON_PATCHES = True


@dataclass
class GuidePatchPreview:
    cycle_key: str
    edge_ids: tuple
    side_keys: tuple
    side_forward: tuple
    side_source_uids: tuple
    corner_node_ids: tuple
    rails_world: list
    u_segments: int
    v_segments: int
    projection_mode: str
    surface_follow_strength: float = 1.0
    surface_tighten_strength: float = 0.0
    surface_smoothing_radius: int = 1
    surface_offset: float = 0.0
    detail_ignore_threshold: float = 0.0
    validation_status: str = "VALID"
    topology_kind: str = "GRID"
    polygon_world: tuple = ()
    polygon_triangles: tuple = ()
    quad_vertices_world: tuple = ()
    polygon_quads: tuple = ()
    side_world_points: tuple = ()
    boundary_loops_world: tuple = ()
    boundary_loop_keys: tuple = ()
    boundary_loop_node_ids: tuple = ()
    source_cycle_keys: tuple = ()
    side_edge_ids: tuple = ()

    def __post_init__(self):
        if self.topology_kind != "POLYGON" or not self.polygon_world:
            return
        if self.quad_vertices_world and self.polygon_quads:
            return
        # A radial fan inserts one midpoint on every contour segment. Drawn
        # guide sides must own those points so adjacent cells reuse the full
        # split boundary, not only its corner vertices.
        side_points = tuple(self.side_world_points or ())
        drawn_boundary = (
            side_points
            and len(side_points) == len(self.side_source_uids)
            and all(not values for values in self.side_source_uids)
        )
        contour_count = len(self.polygon_world)
        segment_count = sum(max(len(points) - 1, 0) for points in side_points)
        try:
            convex = is_convex_ring(self.polygon_world)
            if not convex and drawn_boundary and segment_count == contour_count:
                vertices, quads = triangulated_quad_patch(
                    self.polygon_world,
                    self.polygon_triangles,
                )
            else:
                vertices, quads = radial_quad_fan(self.polygon_world)
        except ValueError:
            return

        if drawn_boundary and segment_count == contour_count:
            if convex:
                corners = tuple(vertices[:contour_count])
                midpoints = tuple(vertices[contour_count : contour_count * 2])
                center = vertices[contour_count * 2]
                boundary_ring = tuple(
                    value
                    for index in range(contour_count)
                    for value in (corners[index], midpoints[index])
                )
                center_index = len(boundary_ring)
                ordered_vertices = boundary_ring + (center,)
                ordered_quads = tuple(
                    (
                        index * 2,
                        index * 2 + 1,
                        center_index,
                        (index * 2 - 1) % center_index,
                    )
                    for index in range(contour_count)
                )
            else:
                ordered_vertices = tuple(vertices)
                ordered_quads = tuple(quads)
            refined_sides = []
            for points in side_points:
                refined = []
                values = tuple(Vector(point) for point in points)
                for start, end in zip(values, values[1:]):
                    refined.extend((start.copy(), (start + end) * 0.5))
                refined.append(values[-1].copy())
                refined_sides.append(tuple(refined))
            object.__setattr__(
                self,
                "quad_vertices_world",
                ordered_vertices,
            )
            object.__setattr__(self, "polygon_quads", ordered_quads)
            object.__setattr__(self, "side_world_points", tuple(refined_sides))
        else:
            object.__setattr__(self, "quad_vertices_world", tuple(vertices))
            object.__setattr__(self, "polygon_quads", tuple(quads))
        object.__setattr__(self, "polygon_triangles", ())

    @property
    def segment_count(self):
        return int(self.u_segments)

    @property
    def row_count(self):
        return int(self.v_segments)


@dataclass
class GuideCommitResult:
    cycle_key: str
    patch_id: int
    created_vertices: int
    created_edges: int
    created_faces: int
    reused_vertices: int
    cell_record: dict


def _vector_payload(vector):
    return [round(float(component), 8) for component in vector]


def _anchor_from_payload(payload):
    if not isinstance(payload, dict):
        raise ValueError("Surface anchor payload is malformed.")
    return SurfaceAnchor(
        target_object_uuid=str(payload.get("target_object_uuid", "")),
        face_index=int(payload.get("face_index", -1)),
        triangle_index=int(payload.get("triangle_index", -1)),
        barycentric=tuple(
            float(value) for value in payload.get("barycentric", ())
        ),
        local_position=Vector(payload.get("local_position", (0.0, 0.0, 0.0))),
        local_normal=Vector(payload.get("local_normal", (0.0, 0.0, 1.0))),
        normal_offset=float(payload.get("normal_offset", 0.0)),
        topology_revision=int(payload.get("topology_revision", 0)),
    )


def _anchor_payload(anchor):
    return {
        "target_object_uuid": str(anchor.target_object_uuid),
        "face_index": int(anchor.face_index),
        "triangle_index": int(anchor.triangle_index),
        "barycentric": [
            round(float(value), 8) for value in anchor.barycentric
        ],
        "local_position": _vector_payload(anchor.local_position),
        "local_normal": _vector_payload(anchor.local_normal),
        "normal_offset": round(float(anchor.normal_offset), 8),
        "topology_revision": int(anchor.topology_revision),
    }


def refresh_guide_surface_anchors(
    obj,
    guides,
    projector,
    target_object_uuid,
    normal_offset=0.0,
):
    staged = []
    for guide in guides:
        anchors = []
        for point_local in guide.points_local:
            point_world = obj.matrix_world @ Vector(point_local)
            nearest = projector.nearest_target_local(point_world)
            if nearest is None:
                raise FlowPatchGeometryError(
                    "A guide control has no valid target-surface anchor."
                )
            location, normal, face_index, _distance = nearest
            anchors.append(
                SurfaceAnchor(
                    target_object_uuid=str(target_object_uuid),
                    face_index=int(face_index),
                    triangle_index=-1,
                    barycentric=(),
                    local_position=Vector(location),
                    local_normal=Vector(normal).normalized(),
                    normal_offset=float(normal_offset),
                    topology_revision=int(projector.topology_revision),
                )
            )
        staged.append(tuple(anchors))
    for guide, anchors in zip(guides, staged):
        guide.anchors = anchors
    return sum(len(anchors) for anchors in staged)


def load_guides(obj):
    raw = obj.get(GUIDE_DATA_KEY, "")
    if not raw:
        return []
    try:
        payload = json.loads(raw)
        records = payload.get("guides", [])
        guides = []
        for record in records:
            points = [Vector(point) for point in record.get("points", [])]
            if len(points) >= 2:
                anchors = tuple(
                    _anchor_from_payload(value)
                    for value in record.get("anchors", ())
                )
                if len(anchors) != len(points):
                    anchors = ()
                guides.append(
                    GuidePath(
                        guide_id=int(record["id"]),
                        points_local=points,
                        start_node=int(record.get("start_node", 0)),
                        end_node=int(record.get("end_node", 0)),
                        logical_side_id=int(
                            record.get("logical_side_id", record["id"])
                        ),
                        source_kind=str(record.get("source_kind", "DRAWN")),
                        source_vertex_uids=tuple(
                            int(value)
                            for value in record.get("source_vertex_uids", [])
                        ),
                        anchors=anchors,
                    )
                )
        ensure_graph_ids(guides)
        return guides
    except (KeyError, TypeError, ValueError, json.JSONDecodeError):
        return []


def save_guides(obj, guides):
    ensure_graph_ids(guides)
    payload = {
        "version": 3,
        "guides": [
            {
                "id": int(guide.guide_id),
                "points": [_vector_payload(point) for point in guide.points_local],
                "start_node": int(guide.start_node),
                "end_node": int(guide.end_node),
                "logical_side_id": int(guide.logical_side_id),
                "source_kind": str(guide.source_kind),
                "source_vertex_uids": [
                    int(value) for value in guide.source_vertex_uids
                ],
                "anchors": (
                    [_anchor_payload(anchor) for anchor in guide.anchors]
                    if len(guide.anchors) == len(guide.points_local)
                    else []
                ),
            }
            for guide in guides
            if len(guide.points_local) >= 2
        ],
    }
    obj[GUIDE_DATA_KEY] = json.dumps(payload, separators=(",", ":"))


def load_built_cells(obj):
    raw = obj.get(BUILT_CELL_DATA_KEY, "")
    if not raw:
        return {}
    try:
        payload = json.loads(raw)
        cells = {}
        for key, value in payload.get("cells", {}).items():
            if isinstance(value, dict):
                record = dict(value)
                record["patch_id"] = int(record.get("patch_id", 0))
                record["u_segments"] = int(record.get("u_segments", 0))
                record["v_segments"] = int(record.get("v_segments", 0))
                record["edge_ids"] = [
                    int(item) for item in record.get("edge_ids", [])
                ]
                record["side_keys"] = [
                    str(item) for item in record.get("side_keys", [])
                ]
                for field, default in (
                    ("surface_follow_strength", 1.0),
                    ("surface_tighten_strength", 0.0),
                    ("surface_offset", 0.0),
                    ("detail_ignore_threshold", 0.0),
                ):
                    record[field] = float(record.get(field, default))
                record["surface_smoothing_radius"] = int(
                    record.get("surface_smoothing_radius", 1)
                )
                record["state"] = str(record.get("state", "PARAMETRIC"))
                cells[str(key)] = record
            else:
                cells[str(key)] = {
                    "patch_id": int(value),
                    "u_segments": 0,
                    "v_segments": 0,
                    "edge_ids": [
                        int(item)
                        for item in str(key).split(":")
                        if str(item).isdigit()
                    ],
                    "side_keys": [],
                    "state": "LEGACY",
                    "revision": 0,
                }
        return cells
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}


def save_built_cells(obj, cells):
    serializable = {}
    for key, value in cells.items():
        if isinstance(value, dict):
            serializable[str(key)] = value
        else:
            serializable[str(key)] = {
                "patch_id": int(value),
                "state": "LEGACY",
                "revision": 0,
            }
    payload = {
        "version": 2,
        "cells": serializable,
    }
    obj[BUILT_CELL_DATA_KEY] = json.dumps(payload, separators=(",", ":"))


def _load_json_mapping(obj, key):
    raw = obj.get(key, "")
    if not raw:
        return {}
    try:
        payload = json.loads(raw)
        return dict(payload) if isinstance(payload, dict) else {}
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}


def _save_json_mapping(obj, key, mapping):
    obj[key] = json.dumps(mapping, separators=(",", ":"))


def load_boundary_registry(obj):
    registry = _load_json_mapping(obj, BOUNDARY_REGISTRY_KEY)
    output = {}
    for key, record in registry.items():
        if not isinstance(record, dict):
            continue
        output[str(key)] = {
            "count": int(record.get("count", 0)),
            "uids": [int(value) for value in record.get("uids", [])],
            "revision": int(record.get("revision", 1)),
            "source_kind": str(record.get("source_kind", "DRAWN")),
            "closed": bool(record.get("closed", False)),
            "axis": (
                str(record.get("axis", "")).upper()
                if str(record.get("axis", "")).upper() in {"U", "V"}
                else ""
            ),
        }
    return output


def save_boundary_registry(obj, registry):
    _save_json_mapping(obj, BOUNDARY_REGISTRY_KEY, registry)


def load_node_vertex_registry(obj):
    return {
        str(key): int(value)
        for key, value in _load_json_mapping(
            obj,
            NODE_VERTEX_REGISTRY_KEY,
        ).items()
    }


def save_node_vertex_registry(obj, registry):
    _save_json_mapping(obj, NODE_VERTEX_REGISTRY_KEY, registry)


def guides_world(obj, guides):
    matrix = obj.matrix_world
    return [
        [matrix @ point for point in guide.points_local]
        for guide in guides
    ]


def _clean_polyline(points, minimum_distance=1.0e-6):
    clean = []
    for point in points:
        value = Vector(point)
        if not clean or (value - clean[-1]).length > minimum_distance:
            clean.append(value)
    return clean


def _nearest_existing_segment(obj, guides, point_world, snap_distance):
    best = None
    matrix = obj.matrix_world
    for guide in guides:
        for segment_index, (start_local, end_local) in enumerate(
            zip(guide.points_local, guide.points_local[1:])
        ):
            candidate_world, _unused, factor, _unused_factor = (
                closest_points_on_segments(
                    matrix @ start_local,
                    matrix @ end_local,
                    point_world,
                    point_world,
                )
            )
            distance = (candidate_world - point_world).length
            if distance <= snap_distance and (
                best is None or distance < best[0]
            ):
                best = (
                    distance,
                    int(guide.guide_id),
                    int(segment_index),
                    float(factor),
                    candidate_world.copy(),
                )
    return best


def _resolve_frozen_segment_match(obj, guides, snap_spec):
    """Resolve a previously selected segment without searching unrelated guides."""
    if not snap_spec or snap_spec.get("kind") != "GUIDE_SEGMENT":
        return None

    frozen_guide_id = int(snap_spec.get("guide_id", 0) or 0)
    frozen_side_id = int(snap_spec.get("logical_side_id", 0) or 0)
    point_world = snap_spec.get("point_world")
    if point_world is None or (frozen_guide_id <= 0 and frozen_side_id <= 0):
        return None

    point_world = Vector(point_world)
    matrix = obj.matrix_world
    best = None
    for guide in guides:
        same_guide = (
            frozen_guide_id > 0
            and int(guide.guide_id) == frozen_guide_id
        )
        same_side = (
            frozen_side_id > 0
            and int(guide.logical_side_id) == frozen_side_id
        )
        if not same_guide and not same_side:
            continue
        for segment_index, (start_local, end_local) in enumerate(
            zip(guide.points_local, guide.points_local[1:])
        ):
            candidate_world, _unused, factor, _unused_factor = (
                closest_points_on_segments(
                    matrix @ start_local,
                    matrix @ end_local,
                    point_world,
                    point_world,
                )
            )
            distance = (candidate_world - point_world).length
            if best is None or distance < best[0]:
                best = (
                    distance,
                    int(guide.guide_id),
                    int(segment_index),
                    float(factor),
                    candidate_world.copy(),
                )
    return best


def _node_at_segment_boundary(guide, segment_index, factor):
    epsilon = 1.0e-5
    if segment_index == 0 and factor <= epsilon:
        return int(guide.start_node)
    if (
        segment_index == len(guide.points_local) - 2
        and factor >= 1.0 - epsilon
    ):
        return int(guide.end_node)
    return 0


def append_guide_world(
    obj,
    guides,
    points_world,
    snap_distance,
    endpoint_node_ids=None,
    endpoint_snap_specs=None,
):
    clean_world = _clean_polyline(points_world)
    if len(clean_world) < 2:
        raise FlowPatchGeometryError("Draw a longer guide on the target surface.")

    requested_nodes = list(endpoint_node_ids or (0, 0))
    if len(requested_nodes) != 2:
        requested_nodes = [0, 0]
    frozen_specs = None
    if endpoint_snap_specs is not None:
        frozen_specs = list(endpoint_snap_specs)
        if len(frozen_specs) != 2:
            frozen_specs = [None, None]
    close_tolerance = max(1.0e-5, float(snap_distance))
    closed_requested = (
        len(clean_world) >= 3
        and (clean_world[-1] - clean_world[0]).length <= close_tolerance
    ) or (
        int(requested_nodes[0] or 0) > 0
        and int(requested_nodes[0] or 0)
        == int(requested_nodes[1] or 0)
    )
    if closed_requested:
        clean_world[-1] = clean_world[0].copy()

    ensure_graph_ids(guides)
    inverse = obj.matrix_world.inverted_safe()
    snapped_local = [inverse @ point for point in clean_world]
    endpoint_nodes = [0, 0]
    for endpoint_slot, endpoint_index in enumerate(
        (0, len(snapped_local) - 1)
    ):
        if closed_requested and endpoint_slot == 1:
            endpoint_nodes[1] = endpoint_nodes[0]
            snapped_local[-1] = snapped_local[0].copy()
            continue
        requested_node_id = int(requested_nodes[endpoint_slot] or 0)
        if requested_node_id > 0:
            node = graph_nodes(guides).get(requested_node_id)
            if node is not None:
                endpoint_nodes[endpoint_slot] = requested_node_id
                snapped_local[endpoint_index] = node.point_local.copy()
                continue
        point_world = obj.matrix_world @ snapped_local[endpoint_index]
        if frozen_specs is None:
            match = _nearest_existing_segment(
                obj,
                guides,
                point_world,
                snap_distance,
            )
        else:
            match = _resolve_frozen_segment_match(
                obj,
                guides,
                frozen_specs[endpoint_slot],
            )
        if match is None:
            continue
        _distance, guide_id, segment_index, factor, snapped_world = match
        guide = next(
            guide
            for guide in guides
            if guide.guide_id == guide_id
        )
        node_id = _node_at_segment_boundary(guide, segment_index, factor)
        if node_id <= 0:
            node_id = next_node_id(guides)
            split_guide(
                guides,
                guide_id,
                segment_index,
                factor,
                inverse @ snapped_world,
                node_id,
            )
        endpoint_nodes[endpoint_slot] = node_id
        snapped_local[endpoint_index] = inverse @ snapped_world

    if closed_requested:
        snapped_local[-1] = snapped_local[0].copy()
        endpoint_nodes[1] = endpoint_nodes[0]
        unique_points = []
        for point in snapped_local[:-1]:
            if not any((point - other).length <= 1.0e-6 for other in unique_points):
                unique_points.append(point)
        if len(unique_points) < 3:
            raise FlowPatchGeometryError(
                "A closed guide needs at least three distinct points."
            )
    elif (snapped_local[-1] - snapped_local[0]).length <= 1.0e-7:
        raise FlowPatchGeometryError(
            "A guide side needs two different connection points."
        )

    start_node = endpoint_nodes[0] or next_node_id(guides)
    guide = GuidePath(
        guide_id=next_guide_id(guides),
        points_local=_clean_polyline(snapped_local),
        start_node=start_node,
        end_node=0,
        logical_side_id=next_logical_side_id(guides),
    )
    guide.end_node = (
        start_node
        if closed_requested
        else endpoint_nodes[1] or next_node_id(guides + [guide])
    )
    if guide.start_node == guide.end_node and not closed_requested:
        raise FlowPatchGeometryError(
            "A guide cannot start and end at the same graph node."
        )
    guides.append(guide)
    intersection_tolerance = max(
        1.0e-7,
        min(1.0e-4, float(snap_distance) * 0.02),
    )
    split_intersections(
        guides,
        obj.matrix_world,
        intersection_tolerance,
    )
    ensure_graph_ids(guides, tolerance=intersection_tolerance)
    return guide


def append_selected_mesh_boundary(obj, bm, guides):
    """Register one selected open mesh boundary as a logical guide side."""
    uid_layer, next_uid = _ensure_vertex_uids(obj, bm)
    obj[VERTEX_UID_COUNTER_KEY] = int(next_uid)
    chain = ordered_selected_boundary(bm)

    source_uids = tuple(int(vert[uid_layer]) for vert in chain.verts)
    if len(source_uids) < 2:
        raise FlowPatchGeometryError(
            "Continue On needs at least one selected boundary edge."
        )
    canonical = min(source_uids, tuple(reversed(source_uids)))
    for guide in guides:
        existing = tuple(int(value) for value in guide.source_vertex_uids)
        if not existing:
            continue
        if min(existing, tuple(reversed(existing))) == canonical:
            raise FlowPatchGeometryError(
                "That mesh boundary is already registered as a guide side."
            )

    ensure_graph_ids(guides)
    guide = GuidePath(
        guide_id=next_guide_id(guides),
        points_local=[vert.co.copy() for vert in chain.verts],
        start_node=next_node_id(guides),
        end_node=0,
        logical_side_id=next_logical_side_id(guides),
        source_kind="MESH",
        source_vertex_uids=source_uids,
    )
    guide.end_node = next_node_id(guides + [guide])
    guides.append(guide)
    ensure_graph_ids(guides)
    return guide


def _resample_polyline(points, count):
    if count < 2:
        raise FlowPatchGeometryError("A guide side needs at least two samples.")
    if len(points) < 2:
        raise FlowPatchGeometryError("A guide side is too short.")

    lengths = [0.0]
    for start, end in zip(points, points[1:]):
        lengths.append(lengths[-1] + (end - start).length)
    total = lengths[-1]
    if total <= 1.0e-9:
        raise FlowPatchGeometryError("A guide side has zero length.")

    output = []
    segment = 0
    for index in range(count):
        target = total * index / (count - 1)
        while segment < len(lengths) - 2 and lengths[segment + 1] < target:
            segment += 1
        span = lengths[segment + 1] - lengths[segment]
        factor = 0.0 if span <= 1.0e-9 else (target - lengths[segment]) / span
        output.append(points[segment].lerp(points[segment + 1], factor))
    return output


def _side_world_points(obj, side, guide_by_id):
    try:
        local_points = oriented_side_points(side, guide_by_id)
    except ValueError as exc:
        raise FlowPatchGeometryError(str(exc)) from exc
    return [obj.matrix_world @ point for point in local_points]


def _cycle_boundary_world_points(obj, cycle, guide_by_id):
    boundary = []
    for side in cycle.sides:
        points = _side_world_points(obj, side, guide_by_id)
        if boundary:
            points = points[1:]
        boundary.extend(points)
    return boundary


def _normalize_detected_cycle(obj, cycle, guide_by_id, projector):
    boundary_world = _cycle_boundary_world_points(obj, cycle, guide_by_id)
    if projector is not None and hasattr(projector, "nearest_target_local"):
        boundary_local = []
        for point_world in boundary_world:
            nearest = projector.nearest_target_local(point_world)
            if nearest is None:
                raise RegionSolverError(
                    "SURFACE_ANCHOR_UNAVAILABLE",
                    "The bounded region lost its target-local surface anchor.",
                )
            boundary_local.append(tuple(nearest[0]))

        def normal_at(point_local):
            nearest = projector.nearest_local(Vector(point_local))
            return None if nearest is None else tuple(nearest[1])

    else:
        inverse = obj.matrix_world.inverted_safe()
        boundary_local = [
            tuple(inverse @ point_world)
            for point_world in boundary_world
        ]
        normal_at = None

    decision = normalize_target_local_winding(
        boundary_local,
        normal_at=normal_at,
    )
    return with_normalized_winding(
        cycle,
        reverse=decision.reversed,
        target_local_area=decision.signed_area,
    )


def find_bounded_regions(
    obj,
    guides,
    projector,
    validation_errors=None,
):
    candidates, edge_nodes, guide_by_id = find_closed_cycles(guides)
    regions = []
    for cycle in candidates:
        try:
            bounded_face_signature(cycle.edge_ids)
            regions.append(
                _normalize_detected_cycle(
                    obj,
                    cycle,
                    guide_by_id,
                    projector,
                )
            )
        except RegionSolverError as exc:
            if exc.reason_code in {
                "DEGENERATE_REGION_WINDING",
                "AMBIGUOUS_REGION_WINDING",
            }:
                # Existing boundary validation owns the specific explanation
                # for collapsed or self-crossing contours.
                regions.append(cycle)
                continue
            if validation_errors is not None:
                validation_errors.append((cycle.key, str(exc)))
    return regions, edge_nodes, guide_by_id


def _side_source_uids(side, guide_by_id):
    values = []
    current = side.start_node
    for edge_id in side.edge_ids:
        guide = guide_by_id[edge_id]
        start = guide.start_node
        end = guide.end_node
        source = list(guide.source_vertex_uids)
        if not source:
            return []
        if start == current:
            following = end
        elif end == current:
            following = start
            source.reverse()
        else:
            raise FlowPatchGeometryError(
                "An existing mesh boundary has inconsistent connectivity."
            )
        if values:
            source = source[1:]
        values.extend(source)
        current = following
    if current != side.end_node:
        raise FlowPatchGeometryError(
            "An existing mesh boundary ends at the wrong graph node."
        )
    return values


def _side_fixed_count(side, guide_by_id, boundary_registry):
    record = boundary_registry.get(side.key)
    if record is not None and int(record.get("count", 0)) > 0:
        return int(record["count"])
    source_uids = _side_source_uids(side, guide_by_id)
    if source_uids:
        return len(source_uids) - 1
    return 0


def _axis_for_side_index(side_index):
    return "U" if int(side_index) in {0, 2} else "V"


def _boundary_axis_hint(
    side,
    guide_by_id,
    boundary_registry,
    requested_u,
    requested_v,
):
    record = boundary_registry.get(side.key)
    if record is not None:
        axis = str(record.get("axis", "")).upper()
        if axis in {"U", "V"}:
            return axis

    count = _side_fixed_count(side, guide_by_id, boundary_registry)
    requested_u = max(1, int(requested_u))
    requested_v = max(1, int(requested_v))
    if count and requested_u != requested_v:
        if count == requested_u:
            return "U"
        if count == requested_v:
            return "V"
    return ""


def _oriented_cycle_sides(
    cycle,
    guide_by_id,
    boundary_registry,
    requested_u,
    requested_v,
):
    sides = tuple(cycle.sides)
    if len(sides) != 4:
        raise FlowPatchGeometryError(
            "A patch candidate must have exactly four logical sides."
        )

    candidates = []
    for offset in range(4):
        rotated = sides[offset:] + sides[:offset]
        mismatches = 0
        matches = 0
        for side_index, side in enumerate(rotated):
            hint = _boundary_axis_hint(
                side,
                guide_by_id,
                boundary_registry,
                requested_u,
                requested_v,
            )
            if not hint:
                continue
            matches += 1
            if hint != _axis_for_side_index(side_index):
                mismatches += 1
        candidates.append((mismatches, -matches, offset, rotated))

    mismatches, _negative_matches, _offset, oriented = min(
        candidates,
        key=lambda item: item[:3],
    )
    if mismatches:
        raise FlowPatchGeometryError(
            "Shared boundaries cannot agree on U/V density orientation."
        )
    return oriented


def _resolved_axis_count(
    requested,
    first_side,
    second_side,
    guide_by_id,
    boundary_registry,
):
    first = _side_fixed_count(
        first_side,
        guide_by_id,
        boundary_registry,
    )
    second = _side_fixed_count(
        second_side,
        guide_by_id,
        boundary_registry,
    )
    if first and second and first != second:
        raise FlowPatchGeometryError(
            "Opposite cell sides have incompatible boundary subdivisions."
        )
    return max(1, first or second or int(requested))


def reconcile_shared_segment_count(
    requested_counts,
    fixed_count=0,
    minimum=1,
    maximum=64,
):
    """Resolve one density for a connected editable boundary component."""
    minimum = max(1, int(minimum))
    maximum = max(minimum, int(maximum))
    fixed_count = int(fixed_count or 0)
    if fixed_count:
        return max(minimum, min(maximum, fixed_count))

    values = [
        max(minimum, min(maximum, int(value)))
        for value in requested_counts
        if int(value) > 0
    ]
    if not values:
        return minimum
    mean = sum(values) / len(values)
    return max(minimum, min(maximum, int(math.floor(mean + 0.5))))


def _requested_cycle_density(
    cycle_key,
    axis,
    density_overrides,
    default_value,
):
    record = (density_overrides or {}).get(str(cycle_key), {})
    value = record.get(str(axis).upper(), default_value)
    return max(1, min(64, int(value)))


def _planned_preview_densities(
    cycles,
    guide_by_id,
    boundary_registry,
    requested_u,
    requested_v,
    density_overrides=None,
):
    """Plan all editable cells before any preview claims a shared boundary."""
    cycles = sorted(cycles, key=lambda cycle: str(cycle.key))
    orientation_registry = deepcopy(boundary_registry)
    oriented_sides = {}

    # Orientation is propagated through shared guide sides first. Planning
    # records carry no density, so they cannot masquerade as committed borders.
    for cycle in cycles:
        cycle_u = _requested_cycle_density(
            cycle.key,
            "U",
            density_overrides,
            requested_u,
        )
        cycle_v = _requested_cycle_density(
            cycle.key,
            "V",
            density_overrides,
            requested_v,
        )
        sides = _oriented_cycle_sides(
            cycle,
            guide_by_id,
            orientation_registry,
            cycle_u,
            cycle_v,
        )
        oriented_sides[cycle.key] = sides
        for side_index, side in enumerate(sides):
            axis = _axis_for_side_index(side_index)
            record = orientation_registry.get(side.key)
            if record is None:
                orientation_registry[side.key] = {
                    "count": 0,
                    "uids": [],
                    "revision": 0,
                    "source_kind": "PLANNING",
                    "axis": axis,
                }
                continue
            record_axis = str(record.get("axis", "")).upper()
            if record_axis and record_axis != axis:
                raise FlowPatchGeometryError(
                    "Connected cells cannot agree on shared boundary orientation."
                )
            record.setdefault("axis", axis)

    parent = {}

    def find(item):
        parent.setdefault(item, item)
        while parent[item] != item:
            parent[item] = parent[parent[item]]
            item = parent[item]
        return item

    def union(first, second):
        first_root = find(first)
        second_root = find(second)
        if first_root != second_root:
            parent[second_root] = first_root

    side_variables = {}
    fixed_by_variable = {}
    requested_by_variable = {}
    for cycle in cycles:
        for axis, default in (("U", requested_u), ("V", requested_v)):
            variable = (str(cycle.key), axis)
            find(variable)
            requested_by_variable[variable] = _requested_cycle_density(
                cycle.key,
                axis,
                density_overrides,
                default,
            )
            fixed_by_variable[variable] = []

        for side_index, side in enumerate(oriented_sides[cycle.key]):
            axis = _axis_for_side_index(side_index)
            variable = (str(cycle.key), axis)
            side_variables.setdefault(side.key, []).append(variable)
            fixed = _side_fixed_count(
                side,
                guide_by_id,
                boundary_registry,
            )
            if fixed:
                fixed_by_variable[variable].append(int(fixed))

    for variables in side_variables.values():
        for variable in variables[1:]:
            union(variables[0], variable)

    grouped_variables = {}
    for variable in requested_by_variable:
        grouped_variables.setdefault(find(variable), []).append(variable)

    planned_counts = {}
    for variables in grouped_variables.values():
        fixed_values = {
            int(value)
            for variable in variables
            for value in fixed_by_variable.get(variable, ())
            if int(value) > 0
        }
        if len(fixed_values) > 1:
            raise FlowPatchGeometryError(
                "Connected cells contain incompatible committed boundary "
                "subdivision counts."
            )
        fixed_count = next(iter(fixed_values), 0)
        resolved = reconcile_shared_segment_count(
            [requested_by_variable[variable] for variable in variables],
            fixed_count=fixed_count,
        )
        for variable in variables:
            planned_counts[variable] = resolved

    planned_registry = deepcopy(boundary_registry)
    for cycle in cycles:
        for side_index, side in enumerate(oriented_sides[cycle.key]):
            axis = _axis_for_side_index(side_index)
            count = planned_counts[(str(cycle.key), axis)]
            record = planned_registry.get(side.key)
            if record is not None:
                existing_count = int(record.get("count", 0))
                if existing_count and existing_count != count:
                    raise FlowPatchGeometryError(
                        "A planned cell conflicts with a committed boundary "
                        "subdivision count."
                    )
                record_axis = str(record.get("axis", "")).upper()
                if record_axis and record_axis != axis:
                    raise FlowPatchGeometryError(
                        "A planned cell conflicts with a committed boundary "
                        "orientation."
                    )
                record.setdefault("axis", axis)
                if not existing_count:
                    record["count"] = int(count)
                    record["source_kind"] = "PREVIEW"
                continue
            planned_registry[side.key] = {
                "count": int(count),
                "uids": [],
                "revision": 0,
                "source_kind": "PREVIEW",
                "axis": axis,
            }

    return oriented_sides, planned_counts, planned_registry


def _polygon_normal(points):
    normal = Vector((0.0, 0.0, 0.0))
    for current, following in zip(points, points[1:] + points[:1]):
        normal.x += (current.y - following.y) * (current.z + following.z)
        normal.y += (current.z - following.z) * (current.x + following.x)
        normal.z += (current.x - following.x) * (current.y + following.y)
    if normal.length <= 1.0e-10:
        raise FlowPatchGeometryError(
            "The guide cell has no stable surface orientation."
        )
    normal.normalize()
    return normal


def _project_to_plane_2d(points, normal):
    reference = (
        Vector((1.0, 0.0, 0.0))
        if abs(normal.x) < 0.8
        else Vector((0.0, 1.0, 0.0))
    )
    axis_u = normal.cross(reference)
    if axis_u.length <= 1.0e-10:
        reference = Vector((0.0, 0.0, 1.0))
        axis_u = normal.cross(reference)
    axis_u.normalize()
    axis_v = normal.cross(axis_u).normalized()
    origin = points[0]
    return [
        Vector(((point - origin).dot(axis_u), (point - origin).dot(axis_v)))
        for point in points
    ]


def _orientation_2d(a, b, c):
    return (b.x - a.x) * (c.y - a.y) - (b.y - a.y) * (c.x - a.x)


def _segments_intersect_2d(a, b, c, d, epsilon=1.0e-8):
    o1 = _orientation_2d(a, b, c)
    o2 = _orientation_2d(a, b, d)
    o3 = _orientation_2d(c, d, a)
    o4 = _orientation_2d(c, d, b)
    return (
        (o1 > epsilon and o2 < -epsilon or o1 < -epsilon and o2 > epsilon)
        and (o3 > epsilon and o4 < -epsilon or o3 < -epsilon and o4 > epsilon)
    )


def _point_on_segment_2d(point, start, end, epsilon=1.0e-8):
    if abs(_orientation_2d(start, end, point)) > epsilon:
        return False
    return (
        min(start.x, end.x) - epsilon
        <= point.x
        <= max(start.x, end.x) + epsilon
        and min(start.y, end.y) - epsilon
        <= point.y
        <= max(start.y, end.y) + epsilon
    )


def _point_in_polygon_2d(point, polygon, epsilon=1.0e-8):
    inside = False
    for start, end in zip(polygon, polygon[1:] + polygon[:1]):
        if _point_on_segment_2d(point, start, end, epsilon):
            return False
        if (start.y > point.y) == (end.y > point.y):
            continue
        crossing_x = start.x + (
            (point.y - start.y)
            * (end.x - start.x)
            / (end.y - start.y)
        )
        if crossing_x > point.x + epsilon:
            inside = not inside
    return inside


def _rings_touch_or_cross_2d(first, second, epsilon=1.0e-8):
    for first_start, first_end in zip(first, first[1:] + first[:1]):
        for second_start, second_end in zip(
            second,
            second[1:] + second[:1],
        ):
            if _segments_intersect_2d(
                first_start,
                first_end,
                second_start,
                second_end,
                epsilon,
            ):
                return True
            if (
                _point_on_segment_2d(
                    first_start,
                    second_start,
                    second_end,
                    epsilon,
                )
                or _point_on_segment_2d(
                    first_end,
                    second_start,
                    second_end,
                    epsilon,
                )
                or _point_on_segment_2d(
                    second_start,
                    first_start,
                    first_end,
                    epsilon,
                )
                or _point_on_segment_2d(
                    second_end,
                    first_start,
                    first_end,
                    epsilon,
                )
            ):
                return True
    return False


def _bounds_2d(points):
    return (
        min(point.x for point in points),
        min(point.y for point in points),
        max(point.x for point in points),
        max(point.y for point in points),
    )


def _bounds_strictly_contains(outer, inner, epsilon=1.0e-8):
    return (
        outer[0] < inner[0] - epsilon
        and outer[1] < inner[1] - epsilon
        and outer[2] > inner[2] + epsilon
        and outer[3] > inner[3] + epsilon
    )


def validate_boundary_sides(side_points, tolerance=1.0e-5):
    if len(side_points) != 4:
        raise FlowPatchGeometryError(
            "A patch candidate must have exactly four logical sides."
        )
    if any(len(points) < 2 for points in side_points):
        raise FlowPatchGeometryError("Every logical side needs two points.")

    corners = [points[0] for points in side_points]
    for index, points in enumerate(side_points):
        following = side_points[(index + 1) % 4]
        if (points[-1] - following[0]).length > tolerance:
            raise FlowPatchGeometryError("The guide cell boundary has a gap.")
    for index, corner in enumerate(corners):
        for other in corners[index + 1 :]:
            if (corner - other).length <= tolerance:
                raise FlowPatchGeometryError(
                    "The guide cell has duplicate corners."
                )

    boundary = []
    for points in side_points:
        boundary.extend(points[:-1])
    if len(boundary) < 4:
        raise FlowPatchGeometryError("The guide cell boundary is incomplete.")
    normal = _polygon_normal(corners)
    boundary_2d = _project_to_plane_2d(boundary, normal)
    segment_count = len(boundary_2d)
    for first in range(segment_count):
        a = boundary_2d[first]
        b = boundary_2d[(first + 1) % segment_count]
        for second in range(first + 1, segment_count):
            if second in {
                first,
                (first + 1) % segment_count,
                (first - 1) % segment_count,
            }:
                continue
            if first == 0 and second == segment_count - 1:
                continue
            c = boundary_2d[second]
            d = boundary_2d[(second + 1) % segment_count]
            if _segments_intersect_2d(a, b, c, d):
                raise FlowPatchGeometryError(
                    "The guide cell boundary crosses itself."
                )
    return normal


def validate_preview_grid(rails, reference_normal):
    if len(rails) < 2 or len(rails[0]) < 2:
        raise FlowPatchGeometryError("The patch grid needs at least one quad.")
    width = len(rails[0])
    if any(len(rail) != width for rail in rails):
        raise FlowPatchGeometryError("The patch grid is not rectangular.")

    for row_index in range(len(rails) - 1):
        for column_index in range(width - 1):
            p00 = rails[row_index][column_index]
            p10 = rails[row_index][column_index + 1]
            p11 = rails[row_index + 1][column_index + 1]
            p01 = rails[row_index + 1][column_index]
            first = (p10 - p00).cross(p01 - p00)
            second = (p11 - p10).cross(p01 - p10)
            if first.length <= 1.0e-10 or second.length <= 1.0e-10:
                raise FlowPatchGeometryError(
                    "The patch preview contains a collapsed quad."
                )
            if first.dot(reference_normal) <= 1.0e-10:
                raise FlowPatchGeometryError(
                    "The patch preview folds or reverses its winding."
                )
            if second.dot(reference_normal) <= 1.0e-10:
                raise FlowPatchGeometryError(
                    "The patch preview folds or reverses its winding."
                )


def _project_grid_interior(base_rails, projector, surface_offset):
    projected = [
        [point.copy() for point in rail]
        for rail in base_rails
    ]
    if projector is None:
        return projected
    last_row = len(projected) - 1
    last_column = len(projected[0]) - 1
    for row_index in range(1, last_row):
        for column_index in range(1, last_column):
            nearest = projector.nearest_world(
                base_rails[row_index][column_index],
                surface_offset,
            )
            if nearest is None:
                raise FlowPatchGeometryError(
                    "The target projection failed inside this cell."
                )
            projected[row_index][column_index] = Vector(nearest[0])
    return projected


def _smoothstep(edge_zero, edge_one, value):
    if edge_one <= edge_zero:
        return 1.0 if value >= edge_one else 0.0
    factor = max(
        0.0,
        min(1.0, (value - edge_zero) / (edge_one - edge_zero)),
    )
    return factor * factor * (3.0 - 2.0 * factor)


def _average_vectors(samples):
    if not samples:
        return Vector()
    total = Vector((0.0, 0.0, 0.0))
    for sample in samples:
        total += sample
    return total / len(samples)


def _surface_fit_grid(
    base_rails,
    raw_rails,
    surface_follow_strength,
    surface_tighten_strength,
    surface_smoothing_radius,
    detail_ignore_threshold,
):
    output = [
        [point.copy() for point in rail]
        for rail in base_rails
    ]
    row_count = len(raw_rails)
    column_count = len(raw_rails[0])
    radius = max(1, int(surface_smoothing_radius))
    follow = max(0.0, min(1.0, float(surface_follow_strength)))
    tighten = max(0.0, min(1.0, float(surface_tighten_strength)))
    threshold = max(0.0, float(detail_ignore_threshold))

    for row_index in range(1, row_count - 1):
        for column_index in range(1, column_count - 1):
            samples = []
            for sample_row in range(
                max(0, row_index - radius),
                min(row_count, row_index + radius + 1),
            ):
                for sample_column in range(
                    max(0, column_index - radius),
                    min(column_count, column_index + radius + 1),
                ):
                    samples.append(raw_rails[sample_row][sample_column])
            neighborhood = _average_vectors(samples)
            broad = neighborhood.copy()
            broad = broad.lerp(
                base_rails[row_index][column_index],
                tighten,
            )
            raw = raw_rails[row_index][column_index]
            local_detail = (raw - neighborhood).length
            detail_weight = (
                _smoothstep(threshold, threshold * 2.0, local_detail)
                if threshold > 0.0
                else 1.0
            )
            output[row_index][column_index] = broad.lerp(
                raw,
                follow * detail_weight,
            )
    return output


def build_cycle_preview(
    obj,
    cycle,
    guide_by_id,
    projector,
    u_segments,
    v_segments,
    projection_mode="SURFACE",
    surface_offset=0.0,
    surface_follow_strength=0.5,
    surface_tighten_strength=0.35,
    surface_smoothing_radius=1,
    detail_ignore_threshold=0.0,
    boundary_registry=None,
):
    boundary_registry = boundary_registry or {}
    sides = _oriented_cycle_sides(
        cycle,
        guide_by_id,
        boundary_registry,
        u_segments,
        v_segments,
    )
    bottom_side, right_side, top_side_forward, left_side_forward = sides
    u_segments = _resolved_axis_count(
        u_segments,
        bottom_side,
        top_side_forward,
        guide_by_id,
        boundary_registry,
    )
    v_segments = _resolved_axis_count(
        v_segments,
        right_side,
        left_side_forward,
        guide_by_id,
        boundary_registry,
    )

    side_points = [
        _side_world_points(obj, side, guide_by_id)
        for side in sides
    ]
    reference_normal = validate_boundary_sides(side_points)
    bottom_world, right_world, top_forward_world, left_forward_world = side_points
    top_world = list(reversed(top_forward_world))
    left_world = list(reversed(left_forward_world))

    bottom = _resample_polyline(
        bottom_world,
        u_segments + 1,
    )
    top = _resample_polyline(
        top_world,
        u_segments + 1,
    )
    left = _resample_polyline(
        left_world,
        v_segments + 1,
    )
    right = _resample_polyline(
        right_world,
        v_segments + 1,
    )

    p00 = bottom[0]
    p10 = bottom[-1]
    p01 = top[0]
    p11 = top[-1]
    base_rails = []
    for row_index in range(v_segments + 1):
        v = row_index / v_segments
        rail = []
        for column_index in range(u_segments + 1):
            u = column_index / u_segments
            if row_index == 0:
                point = bottom[column_index].copy()
            elif row_index == v_segments:
                point = top[column_index].copy()
            elif column_index == 0:
                point = left[row_index].copy()
            elif column_index == u_segments:
                point = right[row_index].copy()
            else:
                ruled = (
                    bottom[column_index] * (1.0 - v)
                    + top[column_index] * v
                    + left[row_index] * (1.0 - u)
                    + right[row_index] * u
                )
                bilinear = (
                    p00 * ((1.0 - u) * (1.0 - v))
                    + p10 * (u * (1.0 - v))
                    + p01 * ((1.0 - u) * v)
                    + p11 * (u * v)
                )
                point = ruled - bilinear
            rail.append(point)
        base_rails.append(rail)

    resolved_mode = str(projection_mode).upper()
    if resolved_mode == "FLATTENED":
        rails = base_rails
    else:
        raw_rails = _project_grid_interior(
            base_rails,
            projector,
            surface_offset,
        )
        if resolved_mode in {"SURFACE", "RAW"}:
            rails = raw_rails
        else:
            rails = _surface_fit_grid(
                base_rails,
                raw_rails,
                surface_follow_strength,
                surface_tighten_strength,
                surface_smoothing_radius,
                detail_ignore_threshold,
            )

    validate_preview_grid(rails, reference_normal)
    return GuidePatchPreview(
        cycle_key=cycle.key,
        edge_ids=tuple(
            edge_id
            for side in sides
            for edge_id in side.edge_ids
        ),
        side_keys=tuple(side.key for side in sides),
        side_forward=tuple(side.forward_key for side in sides),
        side_source_uids=tuple(
            tuple(
                source_uids
                if side.forward_key
                else reversed(source_uids)
            )
            for side in sides
            for source_uids in [_side_source_uids(side, guide_by_id)]
        ),
        corner_node_ids=tuple(side.start_node for side in sides),
        side_edge_ids=tuple(
            tuple(int(value) for value in side.edge_ids)
            for side in sides
        ),
        rails_world=rails,
        u_segments=u_segments,
        v_segments=v_segments,
        projection_mode=resolved_mode,
        surface_follow_strength=float(surface_follow_strength),
        surface_tighten_strength=float(surface_tighten_strength),
        surface_smoothing_radius=int(surface_smoothing_radius),
        surface_offset=float(surface_offset),
        detail_ignore_threshold=float(detail_ignore_threshold),
    )


def build_generic_cycle_preview(
    obj,
    cycle,
    guide_by_id,
    projection_mode="SURFACE",
    surface_offset=0.0,
    surface_follow_strength=0.5,
    surface_tighten_strength=0.35,
    surface_smoothing_radius=1,
    detail_ignore_threshold=0.0,
):
    sides = tuple(cycle.sides)
    if not sides:
        raise FlowPatchGeometryError("A closed guide cell has no boundary.")

    side_points = tuple(
        tuple(_side_world_points(obj, side, guide_by_id))
        for side in sides
    )
    outline = []
    for points in side_points:
        if len(points) < 2:
            raise FlowPatchGeometryError(
                "Every guide side needs at least two points."
            )
        values = [Vector(point) for point in points]
        if outline and (outline[-1] - values[0]).length <= 1.0e-5:
            values = values[1:]
        outline.extend(values)
    if len(outline) >= 2 and (outline[-1] - outline[0]).length <= 1.0e-5:
        outline.pop()
    outline = _clean_polyline(outline)
    if len(outline) < 3:
        raise FlowPatchGeometryError(
            "A closed guide cell needs at least three distinct corners."
        )

    tessellated = tessellate_polygon(
        [[point.copy() for point in outline]]
    )
    if len(tessellated) != len(outline) - 2:
        raise FlowPatchGeometryError(
            "The closed guide outline is self-crossing or cannot be filled."
        )
    triangles = tuple(
        tuple(
            outline[int(value)].copy()
            if isinstance(value, int)
            else Vector(value)
            for value in triangle
        )
        for triangle in tessellated
    )

    resolved_mode = str(projection_mode).upper()
    return GuidePatchPreview(
        cycle_key=cycle.key,
        edge_ids=tuple(
            edge_id
            for side in sides
            for edge_id in side.edge_ids
        ),
        side_keys=tuple(side.key for side in sides),
        side_forward=tuple(side.forward_key for side in sides),
        side_source_uids=tuple(
            tuple(
                source_uids
                if side.forward_key
                else reversed(source_uids)
            )
            for side in sides
            for source_uids in [_side_source_uids(side, guide_by_id)]
        ),
        corner_node_ids=tuple(side.start_node for side in sides),
        rails_world=[],
        u_segments=1,
        v_segments=1,
        projection_mode=resolved_mode,
        surface_follow_strength=float(surface_follow_strength),
        surface_tighten_strength=float(surface_tighten_strength),
        surface_smoothing_radius=int(surface_smoothing_radius),
        surface_offset=float(surface_offset),
        detail_ignore_threshold=float(detail_ignore_threshold),
        topology_kind="POLYGON",
        polygon_world=tuple(point.copy() for point in outline),
        polygon_triangles=triangles,
        side_world_points=side_points,
    )


def _cycle_boundary_world(obj, cycle, guide_by_id):
    sides = tuple(cycle.sides)
    if not sides:
        raise FlowPatchGeometryError("A closed guide cell has no boundary.")

    side_points = tuple(
        tuple(_side_world_points(obj, side, guide_by_id))
        for side in sides
    )
    outline = []
    for points in side_points:
        if len(points) < 2:
            raise FlowPatchGeometryError(
                "Every guide side needs at least two points."
            )
        values = [Vector(point) for point in points]
        if outline and (outline[-1] - values[0]).length <= 1.0e-5:
            values = values[1:]
        outline.extend(values)
    if len(outline) >= 2 and (outline[-1] - outline[0]).length <= 1.0e-5:
        outline.pop()
    outline = _clean_polyline(outline)
    if len(outline) < 3:
        raise FlowPatchGeometryError(
            "A closed guide cell needs at least three distinct corners."
        )
    return side_points, outline


def _nested_cycle_pairs(obj, cycles, guide_by_id, tolerance=1.0e-5):
    boundaries = []
    for cycle in cycles:
        try:
            _side_points, outline = _cycle_boundary_world(
                obj,
                cycle,
                guide_by_id,
            )
            normal = _polygon_normal(outline)
        except FlowPatchGeometryError:
            continue
        boundaries.append((cycle, outline, normal))

    nested_pairs = []
    plane_tolerance = max(1.0e-5, float(tolerance) * 4.0)
    for index, (first_cycle, first, first_normal) in enumerate(boundaries):
        for second_cycle, second, second_normal in boundaries[index + 1 :]:
            if abs(first_normal.dot(second_normal)) < 1.0 - 1.0e-4:
                continue
            plane_origin = first[0]
            if any(
                abs((point - plane_origin).dot(first_normal))
                > plane_tolerance
                for point in first + second
            ):
                continue

            combined = _project_to_plane_2d(
                first + second,
                first_normal,
            )
            first_2d = combined[: len(first)]
            second_2d = combined[len(first) :]
            first_bounds = _bounds_2d(first_2d)
            second_bounds = _bounds_2d(second_2d)
            if not (
                _bounds_strictly_contains(first_bounds, second_bounds)
                or _bounds_strictly_contains(second_bounds, first_bounds)
            ):
                continue
            if _rings_touch_or_cross_2d(first_2d, second_2d):
                continue
            if _point_in_polygon_2d(second_2d[0], first_2d):
                nested_pairs.append((first_cycle, second_cycle))
            elif _point_in_polygon_2d(first_2d[0], second_2d):
                nested_pairs.append((second_cycle, first_cycle))
    return tuple(nested_pairs)


def _aligned_cycle_node_ids(
    obj,
    cycle,
    guide_by_id,
    aligned_points,
    tolerance=1.0e-5,
):
    candidates = []
    for side in cycle.sides:
        points = _side_world_points(obj, side, guide_by_id)
        if not points:
            continue
        candidates.append((Vector(points[0]), int(side.start_node)))
        candidates.append((Vector(points[-1]), int(side.end_node)))

    node_ids = []
    for point in aligned_points:
        point = Vector(point)
        matches = [
            (point - candidate).length
            for candidate, _node_id in candidates
        ]
        if not matches or min(matches) > tolerance:
            node_ids.append(0)
            continue
        node_ids.append(candidates[matches.index(min(matches))][1])
    return tuple(node_ids)


def build_annular_cycle_preview(
    obj,
    outer_cycle,
    inner_cycle,
    guide_by_id,
    projection_mode="SURFACE",
    surface_offset=0.0,
    surface_follow_strength=0.5,
    surface_tighten_strength=0.35,
    surface_smoothing_radius=1,
    detail_ignore_threshold=0.0,
):
    _outer_sides, outer = _cycle_boundary_world(
        obj,
        outer_cycle,
        guide_by_id,
    )
    _inner_sides, inner = _cycle_boundary_world(
        obj,
        inner_cycle,
        guide_by_id,
    )
    try:
        vertices, quads = matched_annular_quad_patch(outer, inner)
    except ValueError as exc:
        raise FlowPatchGeometryError(str(exc)) from exc

    loop_count = len(vertices) // 2
    outer_aligned = tuple(Vector(point) for point in vertices[:loop_count])
    inner_aligned = tuple(Vector(point) for point in vertices[loop_count:])
    outer_nodes = _aligned_cycle_node_ids(
        obj,
        outer_cycle,
        guide_by_id,
        outer_aligned,
    )
    inner_nodes = _aligned_cycle_node_ids(
        obj,
        inner_cycle,
        guide_by_id,
        inner_aligned,
    )
    triangles = []
    for first, second, third, fourth in quads:
        triangles.append(
            (
                Vector(vertices[first]),
                Vector(vertices[second]),
                Vector(vertices[third]),
            )
        )
        triangles.append(
            (
                Vector(vertices[first]),
                Vector(vertices[third]),
                Vector(vertices[fourth]),
            )
        )

    cycle_key = f"annular:{outer_cycle.key}|{inner_cycle.key}"
    loop_keys = (
        f"annular-loop:{outer_cycle.key}",
        f"annular-loop:{inner_cycle.key}",
    )
    return GuidePatchPreview(
        cycle_key=cycle_key,
        edge_ids=tuple(outer_cycle.edge_ids + inner_cycle.edge_ids),
        side_keys=loop_keys,
        side_forward=(True, True),
        side_source_uids=((), ()),
        corner_node_ids=(
            outer_nodes[0] if outer_nodes else 0,
            inner_nodes[0] if inner_nodes else 0,
        ),
        rails_world=[],
        u_segments=1,
        v_segments=1,
        projection_mode=str(projection_mode).upper(),
        surface_follow_strength=float(surface_follow_strength),
        surface_tighten_strength=float(surface_tighten_strength),
        surface_smoothing_radius=int(surface_smoothing_radius),
        surface_offset=float(surface_offset),
        detail_ignore_threshold=float(detail_ignore_threshold),
        topology_kind="ANNULAR",
        polygon_world=outer_aligned,
        polygon_triangles=tuple(triangles),
        quad_vertices_world=tuple(Vector(point) for point in vertices),
        polygon_quads=tuple(tuple(int(index) for index in face) for face in quads),
        boundary_loops_world=(outer_aligned, inner_aligned),
        boundary_loop_keys=loop_keys,
        boundary_loop_node_ids=(outer_nodes, inner_nodes),
        source_cycle_keys=(outer_cycle.key, inner_cycle.key),
    )


def build_uncommitted_previews(
    obj,
    guides,
    built_cells,
    projector,
    u_segments,
    v_segments,
    projection_mode="SURFACE",
    surface_offset=0.0,
    surface_follow_strength=0.5,
    surface_tighten_strength=0.35,
    surface_smoothing_radius=1,
    detail_ignore_threshold=0.0,
    validation_errors=None,
    density_overrides=None,
):
    all_cycles, _edge_nodes, guide_by_id = find_bounded_regions(
        obj,
        guides,
        projector,
        validation_errors=validation_errors,
    )
    nested_pairs = _nested_cycle_pairs(obj, all_cycles, guide_by_id)
    nested_cycle_keys = {
        cycle.key
        for pair in nested_pairs
        for cycle in pair
    }
    nested_counts = {}
    for outer_cycle, inner_cycle in nested_pairs:
        nested_counts[outer_cycle.key] = (
            nested_counts.get(outer_cycle.key, 0) + 1
        )
        nested_counts[inner_cycle.key] = (
            nested_counts.get(inner_cycle.key, 0) + 1
        )

    previews = []
    for outer_cycle, inner_cycle in nested_pairs:
        pair_key = f"annular:{outer_cycle.key}|{inner_cycle.key}"
        if pair_key in built_cells:
            continue
        if (
            outer_cycle.key in built_cells
            or inner_cycle.key in built_cells
        ):
            if validation_errors is not None:
                validation_errors.append(
                    (
                        pair_key,
                        "A nested contour overlaps an already committed "
                        "individual cell.",
                    )
                )
            continue
        if (
            nested_counts[outer_cycle.key] != 1
            or nested_counts[inner_cycle.key] != 1
        ):
            if validation_errors is not None:
                validation_errors.append(
                    (
                        pair_key,
                        "Multi-level nested contours require explicit annular "
                        "pairing before they can preview.",
                    )
                )
            continue
        try:
            previews.append(
                build_annular_cycle_preview(
                    obj=obj,
                    outer_cycle=outer_cycle,
                    inner_cycle=inner_cycle,
                    guide_by_id=guide_by_id,
                    projection_mode=projection_mode,
                    surface_offset=surface_offset,
                    surface_follow_strength=surface_follow_strength,
                    surface_tighten_strength=surface_tighten_strength,
                    surface_smoothing_radius=surface_smoothing_radius,
                    detail_ignore_threshold=detail_ignore_threshold,
                )
            )
        except FlowPatchGeometryError as exc:
            if validation_errors is not None:
                validation_errors.extend(
                    (
                        cycle.key,
                        "Nested contours cannot form an annular patch: "
                        f"{exc}",
                    )
                    for cycle in (outer_cycle, inner_cycle)
                )

    cycles = [
        cycle for cycle in all_cycles
        if cycle.key not in built_cells
        and cycle.key not in nested_cycle_keys
    ]
    boundary_registry = deepcopy(load_boundary_registry(obj))
    if not cycles:
        return previews
    if len(cycles) + len(previews) > MAX_PREVIEW_CELLS:
        raise GuideGraphBudgetError(
            "Too many closed guide cells are waiting for preview. Build or "
            "delete part of the network before drawing more guides."
        )

    grid_cycles = [cycle for cycle in cycles if len(cycle.sides) == 4]
    generic_cycles = [cycle for cycle in cycles if len(cycle.sides) != 4]
    if generic_cycles and not ENABLE_LEGACY_POLYGON_PATCHES:
        if validation_errors is not None:
            validation_errors.extend(
                (
                    cycle.key,
                    "Only valid four-sided guide cells can preview or build "
                    "during FlowPatch stabilization.",
                )
                for cycle in generic_cycles
            )
        generic_cycles = []
    planned_counts = {}
    planned_registry = deepcopy(boundary_registry)
    if grid_cycles:
        try:
            _oriented_sides, planned_counts, planned_registry = (
                _planned_preview_densities(
                    grid_cycles,
                    guide_by_id,
                    boundary_registry,
                    u_segments,
                    v_segments,
                    density_overrides=density_overrides,
                )
            )
        except FlowPatchGeometryError as exc:
            if validation_errors is not None:
                validation_errors.extend(
                    (cycle.key, str(exc)) for cycle in grid_cycles
                )
            grid_cycles = []

    estimated_faces = (
        sum(len(preview.polygon_quads) for preview in previews)
        + len(generic_cycles)
    )
    if estimated_faces > MAX_PREVIEW_FACES:
        raise GuideGraphBudgetError(
            "Pending FlowPatch previews exceed the safe interactive face "
            "budget. Build cells or lower density before continuing."
        )
    for cycle in grid_cycles:
        estimated_faces += (
            int(planned_counts[(str(cycle.key), "U")])
            * int(planned_counts[(str(cycle.key), "V")])
        )
        if estimated_faces > MAX_PREVIEW_FACES:
            raise GuideGraphBudgetError(
                "Pending FlowPatch previews exceed the safe interactive face "
                "budget. Build cells or lower density before continuing."
            )

    for cycle in grid_cycles:
        try:
            preview = build_cycle_preview(
                obj=obj,
                cycle=cycle,
                guide_by_id=guide_by_id,
                projector=projector,
                u_segments=planned_counts[(str(cycle.key), "U")],
                v_segments=planned_counts[(str(cycle.key), "V")],
                projection_mode=projection_mode,
                surface_offset=surface_offset,
                surface_follow_strength=surface_follow_strength,
                surface_tighten_strength=surface_tighten_strength,
                surface_smoothing_radius=surface_smoothing_radius,
                detail_ignore_threshold=detail_ignore_threshold,
                boundary_registry=planned_registry,
            )
            for side_index, side_key in enumerate(preview.side_keys):
                side_axis = _axis_for_side_index(side_index)
                side_count = (
                    preview.u_segments
                    if side_index in {0, 2}
                    else preview.v_segments
                )
                record = planned_registry.get(side_key)
                if record is not None:
                    if int(record.get("count", 0)) != side_count:
                        raise FlowPatchGeometryError(
                            "Adjacent preview cells disagree on a shared "
                            "boundary subdivision count."
                        )
                    record_axis = str(record.get("axis", "")).upper()
                    if record_axis and record_axis != side_axis:
                        raise FlowPatchGeometryError(
                            "Adjacent preview cells disagree on shared "
                            "boundary U/V orientation."
                        )
                    record.setdefault("axis", side_axis)
                    continue
                planned_registry[side_key] = {
                    "count": int(side_count),
                    "uids": [],
                    "revision": 0,
                    "source_kind": "PREVIEW",
                    "axis": side_axis,
                }
            previews.append(preview)
        except FlowPatchGeometryError as exc:
            if validation_errors is not None:
                validation_errors.append((cycle.key, str(exc)))

    for cycle in generic_cycles:
        try:
            previews.append(
                build_generic_cycle_preview(
                    obj=obj,
                    cycle=cycle,
                    guide_by_id=guide_by_id,
                    projection_mode=projection_mode,
                    surface_offset=surface_offset,
                    surface_follow_strength=surface_follow_strength,
                    surface_tighten_strength=surface_tighten_strength,
                    surface_smoothing_radius=surface_smoothing_radius,
                    detail_ignore_threshold=detail_ignore_threshold,
                )
            )
        except FlowPatchGeometryError as exc:
            if validation_errors is not None:
                validation_errors.append((cycle.key, str(exc)))
    return previews


def _nearby_vert(obj, bm, point_world, distance):
    best = None
    matrix = obj.matrix_world
    for vert in bm.verts:
        candidate = matrix @ vert.co
        delta = (candidate - point_world).length
        if delta <= distance and (best is None or delta < best[0]):
            best = (delta, vert)
    return best[1] if best is not None else None


def _ensure_vertex_uids(obj, bm):
    layer = bm.verts.layers.int.get(VERTEX_UID_LAYER)
    if layer is None:
        layer = bm.verts.layers.int.new(VERTEX_UID_LAYER)
    bm.verts.ensure_lookup_table()
    bm.verts.index_update()
    used = set()
    for vert in bm.verts:
        value = int(vert[layer])
        if value > 0 and value not in used:
            used.add(value)
        elif value > 0:
            vert[layer] = 0
    next_uid = max(
        int(obj.get(VERTEX_UID_COUNTER_KEY, 1)),
        max(used, default=0) + 1,
    )
    for vert in sorted(bm.verts, key=lambda item: item.index):
        if int(vert[layer]) > 0:
            continue
        vert[layer] = next_uid
        used.add(next_uid)
        next_uid += 1
    return layer, next_uid


def _uid_map(bm, uid_layer):
    return {
        int(vert[uid_layer]): vert
        for vert in bm.verts
        if int(vert[uid_layer]) > 0
    }


def _side_grid_positions(preview, side_index):
    u_segments = preview.u_segments
    v_segments = preview.v_segments
    if side_index == 0:
        return [(0, column) for column in range(u_segments + 1)]
    if side_index == 1:
        return [(row, u_segments) for row in range(v_segments + 1)]
    if side_index == 2:
        return [
            (v_segments, column)
            for column in range(u_segments, -1, -1)
        ]
    return [(row, 0) for row in range(v_segments, -1, -1)]


def _uid_for_new_vert(vert, uid_layer, uid_map, next_uid):
    vert[uid_layer] = int(next_uid)
    uid_map[int(next_uid)] = vert
    return int(next_uid) + 1


def _cell_record(
    preview,
    patch_id,
    geometry,
    uid_layer,
    face_count=None,
):
    is_grid_geometry = bool(
        geometry and isinstance(geometry[0], (list, tuple))
    )
    if is_grid_geometry:
        vertices = [
            vert
            for row in geometry
            for vert in row
        ]
    else:
        vertices = list(geometry)
    vertex_uids = sorted(
        {
            int(vert[uid_layer])
            for vert in vertices
        }
    )
    topology_kind = str(
        getattr(preview, "topology_kind", "GRID")
    ).upper()
    grid_vertex_uids = []
    topology_signature = ""
    if topology_kind == "GRID" and is_grid_geometry:
        grid_vertex_uids = [
            [int(vert[uid_layer]) for vert in row]
            for row in geometry
        ]
        topology_signature = grid_topology(
            grid_vertex_uids
        ).signature
    mesh_positions = {
        str(int(vert[uid_layer])): _vector_payload(vert.co)
        for vert in vertices
    }
    return {
        "patch_id": int(patch_id),
        "u_segments": int(preview.u_segments),
        "v_segments": int(preview.v_segments),
        "edge_ids": [int(value) for value in preview.edge_ids],
        "side_keys": [str(value) for value in preview.side_keys],
        "projection_mode": str(preview.projection_mode),
        "surface_follow_strength": float(
            preview.surface_follow_strength
        ),
        "surface_tighten_strength": float(
            preview.surface_tighten_strength
        ),
        "surface_smoothing_radius": int(
            preview.surface_smoothing_radius
        ),
        "surface_offset": float(preview.surface_offset),
        "detail_ignore_threshold": float(
            preview.detail_ignore_threshold
        ),
        "state": "PARAMETRIC",
        "revision": 1,
        "topology_kind": topology_kind,
        "vertex_uids": vertex_uids,
        "grid_vertex_uids": grid_vertex_uids,
        "topology_signature": topology_signature,
        "mesh_positions": mesh_positions,
        "control_bindings": [],
        "side_forward": [
            bool(value) for value in preview.side_forward
        ],
        "corner_node_ids": [
            int(value) for value in preview.corner_node_ids
        ],
        "side_edge_ids": [
            [int(value) for value in side]
            for side in tuple(preview.side_edge_ids or ())
        ],
        "sync_reason_code": (
            "CONTROL_BINDINGS_PENDING"
            if topology_kind == "GRID"
            else "SYNC_TOPOLOGY_UNSUPPORTED"
        ),
        "sync_message": (
            "Guide control bindings have not been attached."
            if topology_kind == "GRID"
            else "Only four-sided GRID cells support live synchronization."
        ),
        "last_sync_direction": "BUILD",
        "face_count": int(
            face_count
            if face_count is not None
            else preview.u_segments * preview.v_segments
        ),
    }


def _attach_control_bindings(obj, preview, guides, record):
    if str(record.get("topology_kind", "")).upper() != "GRID":
        return record
    grid = tuple(
        tuple(int(value) for value in row)
        for row in record.get("grid_vertex_uids", ())
    )
    if not grid:
        raise FlowPatchGeometryError(
            "The committed GRID cell has no synchronization UID map."
        )
    side_edge_ids = tuple(preview.side_edge_ids or ())
    if len(side_edge_ids) != 4:
        raise FlowPatchGeometryError(
            "The committed GRID cell has no four-side guide ownership map."
        )
    guide_by_id = {int(guide.guide_id): guide for guide in guides}
    bindings = {}

    for side_index, edge_ids in enumerate(side_edge_ids):
        positions = _side_grid_positions(preview, side_index)
        boundary_uids = tuple(
            grid[row_index][column_index]
            for row_index, column_index in positions
        )
        if len(boundary_uids) < 2:
            raise FlowPatchGeometryError(
                "A synchronized guide side has no boundary UID chain."
            )

        current_node = int(preview.corner_node_ids[side_index])
        samples = []
        total_length = 0.0
        previous_world = None
        for edge_id in edge_ids:
            guide = guide_by_id.get(int(edge_id))
            if guide is None:
                raise FlowPatchGeometryError(
                    "A synchronized guide side references a missing guide."
                )
            if int(guide.start_node) == current_node:
                indices = tuple(range(len(guide.points_local)))
                current_node = int(guide.end_node)
            elif int(guide.end_node) == current_node:
                indices = tuple(
                    range(len(guide.points_local) - 1, -1, -1)
                )
                current_node = int(guide.start_node)
            else:
                raise FlowPatchGeometryError(
                    "A synchronized guide side has inconsistent connectivity."
                )
            for point_index in indices:
                point_world = (
                    obj.matrix_world
                    @ Vector(guide.points_local[point_index])
                )
                if previous_world is not None:
                    distance = (point_world - previous_world).length
                    if distance > 1.0e-9:
                        total_length += distance
                samples.append(
                    (
                        int(guide.guide_id),
                        int(point_index),
                        float(total_length),
                    )
                )
                previous_world = point_world
        if total_length <= 1.0e-9:
            raise FlowPatchGeometryError(
                "A synchronized guide side has zero length."
            )

        segment_count = len(boundary_uids) - 1
        for guide_id, point_index, distance in samples:
            scaled = max(
                0.0,
                min(
                    float(segment_count),
                    (distance / total_length) * segment_count,
                ),
            )
            segment_index = min(
                int(math.floor(scaled)),
                segment_count - 1,
            )
            factor = scaled - segment_index
            if scaled >= segment_count:
                segment_index = segment_count - 1
                factor = 1.0
            binding = {
                "guide_id": guide_id,
                "point_index": point_index,
                "uid_a": int(boundary_uids[segment_index]),
                "uid_b": int(boundary_uids[segment_index + 1]),
                "factor": round(float(factor), 8),
            }
            key = (guide_id, point_index)
            previous = bindings.get(key)
            if previous is not None and previous != binding:
                raise FlowPatchGeometryError(
                    "A guide control received conflicting mesh bindings."
                )
            bindings[key] = binding

    record["control_bindings"] = [
        bindings[key] for key in sorted(bindings)
    ]
    record["state"] = "PARAMETRIC"
    record["sync_reason_code"] = "IN_SYNC"
    record["sync_message"] = "Guide and mesh positions are synchronized."
    return record


def _attach_commit_sync_metadata(obj, previews, results, guides):
    if guides is None:
        return
    preview_by_key = {
        str(preview.cycle_key): preview for preview in previews
    }
    for result in results:
        preview = preview_by_key.get(str(result.cycle_key))
        if preview is None:
            raise FlowPatchGeometryError(
                "A committed cell lost its preview synchronization owner."
            )
        _attach_control_bindings(
            obj,
            preview,
            guides,
            result.cell_record,
        )


def _commit_polygon_preview(
    obj,
    bm,
    preview,
    patch_id,
    boundary_registry,
    node_registry,
    merge_distance,
    vert_layer,
    edge_layer,
    face_layer,
    edge_role_layer,
    uid_layer,
    uid_map,
    next_uid,
    existing_edges,
):
    side_count = len(preview.side_keys)
    if side_count < 1 or len(preview.side_world_points) != side_count:
        raise FlowPatchGeometryError(
            "A polygon cell has inconsistent guide-side metadata."
        )

    inverse = obj.matrix_world.inverted_safe()
    created_verts = []
    reused = set()
    side_vertices = []

    for side_index, canonical_source_uids in enumerate(
        preview.side_source_uids
    ):
        if not canonical_source_uids:
            continue
        traversal_source_uids = (
            list(canonical_source_uids)
            if preview.side_forward[side_index]
            else list(reversed(canonical_source_uids))
        )
        for node_id, uid in (
            (
                preview.corner_node_ids[side_index],
                traversal_source_uids[0],
            ),
            (
                preview.corner_node_ids[(side_index + 1) % side_count],
                traversal_source_uids[-1],
            ),
        ):
            if uid_map.get(int(uid)) is None:
                raise FlowPatchGeometryError(
                    "A Continue On boundary references deleted mesh geometry."
                )
            existing_uid = int(node_registry.get(str(node_id), 0))
            if existing_uid and existing_uid != int(uid):
                raise FlowPatchGeometryError(
                    "A graph corner disagrees with its Continue On "
                    "boundary vertex."
                )
            node_registry[str(node_id)] = int(uid)

    for side_index, side_key in enumerate(preview.side_keys):
        points = [
            Vector(point)
            for point in preview.side_world_points[side_index]
        ]
        if len(points) < 2:
            raise FlowPatchGeometryError(
                "A polygon boundary side needs at least two points."
            )
        expected_count = len(points) - 1
        record = boundary_registry.get(side_key)
        canonical_uids = []
        if record is not None:
            if int(record.get("count", 0)) != expected_count:
                raise FlowPatchGeometryError(
                    "A shared polygon boundary has a different point count."
                )
            canonical_uids = [
                int(value) for value in record.get("uids", [])
            ]
        if not canonical_uids:
            canonical_uids = [
                int(value)
                for value in preview.side_source_uids[side_index]
            ]
        if canonical_uids and len(canonical_uids) != len(points):
            raise FlowPatchGeometryError(
                "An existing polygon boundary cannot be resampled without "
                "creating a mesh T-junction."
            )

        traversal_uids = (
            list(canonical_uids)
            if preview.side_forward[side_index]
            else list(reversed(canonical_uids))
        )
        traversal_verts = []
        if traversal_uids:
            for uid in traversal_uids:
                vert = uid_map.get(uid)
                if vert is None:
                    raise FlowPatchGeometryError(
                        "A shared polygon boundary was manually changed."
                    )
                traversal_verts.append(vert)
                reused.add(vert)
        else:
            traversal_uids = []
            for point_index, point_world in enumerate(points):
                node_id = 0
                if point_index == 0:
                    node_id = int(preview.corner_node_ids[side_index])
                elif point_index == len(points) - 1:
                    node_id = int(
                        preview.corner_node_ids[
                            (side_index + 1) % side_count
                        ]
                    )

                vert = None
                if node_id:
                    uid = int(node_registry.get(str(node_id), 0))
                    if uid:
                        vert = uid_map.get(uid)
                        if vert is None:
                            raise FlowPatchGeometryError(
                                "A polygon graph corner references deleted "
                                "mesh geometry."
                            )
                if vert is None:
                    nearby = _nearby_vert(
                        obj,
                        bm,
                        point_world,
                        merge_distance,
                    )
                    if nearby is not None and node_id:
                        vert = nearby
                    elif nearby is not None:
                        raise FlowPatchGeometryError(
                            "A new polygon boundary touches existing mesh "
                            "geometry without shared guide ownership."
                        )
                    else:
                        vert = bm.verts.new(inverse @ point_world)
                        vert[vert_layer] = patch_id
                        next_uid = _uid_for_new_vert(
                            vert,
                            uid_layer,
                            uid_map,
                            next_uid,
                        )
                        created_verts.append(vert)
                else:
                    reused.add(vert)
                traversal_verts.append(vert)
                traversal_uids.append(int(vert[uid_layer]))
                if node_id:
                    node_registry[str(node_id)] = int(vert[uid_layer])

            canonical_uids = (
                list(traversal_uids)
                if preview.side_forward[side_index]
                else list(reversed(traversal_uids))
            )
            boundary_registry[side_key] = {
                "count": int(expected_count),
                "uids": canonical_uids,
                "revision": 1,
                "source_kind": (
                    "MESH"
                    if preview.side_source_uids[side_index]
                    else "DRAWN"
                ),
                "axis": "",
            }

        if side_key not in boundary_registry:
            boundary_registry[side_key] = {
                "count": int(expected_count),
                "uids": (
                    list(traversal_uids)
                    if preview.side_forward[side_index]
                    else list(reversed(traversal_uids))
                ),
                "revision": 1,
                "source_kind": "MESH",
                "axis": "",
            }

        node_registry[
            str(preview.corner_node_ids[side_index])
        ] = int(traversal_uids[0])
        node_registry[
            str(preview.corner_node_ids[(side_index + 1) % side_count])
        ] = int(traversal_uids[-1])
        side_vertices.append(traversal_verts)

    outline = []
    for traversal_verts in side_vertices:
        values = list(traversal_verts)
        if outline and values and outline[-1] is values[0]:
            values = values[1:]
        outline.extend(values)
    if len(outline) >= 2 and outline[-1] is outline[0]:
        outline.pop()
    if len(outline) < 3 or len(set(outline)) != len(outline):
        raise FlowPatchGeometryError(
            "The polygon guide cell collapses or crosses itself."
        )

    existing = next(
        (
            face
            for face in bm.faces
            if len(face.verts) == len(outline)
            and set(face.verts) == set(outline)
        ),
        None,
    )
    if existing is not None:
        raise FlowPatchGeometryError(
            "A polygon guide cell overlaps existing topology."
        )
    for start, end in zip(outline, outline[1:] + outline[:1]):
        edge = bm.edges.get((start, end))
        if edge is not None and len(edge.link_faces) >= 2:
            raise FlowPatchGeometryError(
                "A polygon guide cell would create a non-manifold edge."
            )
    quad_points = tuple(getattr(preview, "quad_vertices_world", ()) or ())
    quad_indices = tuple(getattr(preview, "polygon_quads", ()) or ())
    faces = []

    if quad_points and quad_indices:
        boundary_count = len(outline)
        if len(quad_points) < boundary_count:
            raise FlowPatchGeometryError(
                "The quad preview does not contain its full boundary ring."
            )

        mesh_verts = [None] * len(quad_points)
        unmatched_boundary = list(outline)
        boundary_tolerance = max(float(merge_distance) * 4.0, 1.0e-5)

        # The preview stores its boundary ring first. Match by world position so
        # guide-side traversal direction cannot scramble generated quad indices.
        for index, point_world in enumerate(quad_points[:boundary_count]):
            point_world = Vector(point_world)
            vert = min(
                unmatched_boundary,
                key=lambda candidate: (
                    (obj.matrix_world @ candidate.co) - point_world
                ).length_squared,
            )
            distance = ((obj.matrix_world @ vert.co) - point_world).length
            if distance > boundary_tolerance:
                raise FlowPatchGeometryError(
                    "The quad preview boundary no longer matches the owned guide "
                    "vertices."
                )
            mesh_verts[index] = vert
            unmatched_boundary.remove(vert)

        if unmatched_boundary:
            raise FlowPatchGeometryError(
                "The quad preview did not consume every owned boundary vertex."
            )

        for index, point_world in enumerate(
            quad_points[boundary_count:],
            start=boundary_count,
        ):
            vert = bm.verts.new(inverse @ Vector(point_world))
            vert[vert_layer] = patch_id
            next_uid = _uid_for_new_vert(
                vert,
                uid_layer,
                uid_map,
                next_uid,
            )
            created_verts.append(vert)
            mesh_verts[index] = vert

        for quad in quad_indices:
            indices = tuple(int(index) for index in quad)
            if (
                len(indices) != 4
                or len(set(indices)) != 4
                or min(indices) < 0
                or max(indices) >= len(mesh_verts)
            ):
                raise FlowPatchGeometryError(
                    "The quad preview contains an invalid face definition."
                )
            try:
                face = bm.faces.new(tuple(mesh_verts[index] for index in indices))
            except ValueError as exc:
                raise FlowPatchGeometryError(
                    "The polygon guide cell could not create its quad faces."
                ) from exc
            face[face_layer] = patch_id
            faces.append(face)
    else:
        if not ENABLE_LEGACY_POLYGON_PATCHES:
            raise FlowPatchGeometryError(
                "The polygon guide cell has no durable quad preview."
            )
        try:
            face = bm.faces.new(tuple(outline))
        except ValueError as exc:
            raise FlowPatchGeometryError(
                "The polygon guide cell could not create a valid face."
            ) from exc
        face[face_layer] = patch_id
        faces.append(face)

    bmesh.ops.recalc_face_normals(bm, faces=faces)
    created_edges = set(bm.edges) - existing_edges
    for edge in created_edges:
        if edge[edge_layer] == 0:
            edge[edge_layer] = patch_id
        edge[edge_role_layer] = EDGE_ROLE_RAIL
    existing_edges.update(created_edges)

    record = _cell_record(
        preview,
        patch_id,
        mesh_verts if quad_points and quad_indices else outline,
        uid_layer,
        face_count=len(faces),
    )
    return (
        GuideCommitResult(
            cycle_key=preview.cycle_key,
            patch_id=patch_id,
            created_vertices=len(created_verts),
            created_edges=len(created_edges),
            created_faces=len(faces),
            reused_vertices=len(reused),
            cell_record=record,
        ),
        next_uid,
        created_verts,
        faces,
    )


def _commit_annular_preview(
    obj,
    bm,
    preview,
    patch_id,
    boundary_registry,
    node_registry,
    merge_distance,
    vert_layer,
    edge_layer,
    face_layer,
    edge_role_layer,
    uid_layer,
    uid_map,
    next_uid,
    existing_edges,
):
    loops = tuple(preview.boundary_loops_world)
    loop_keys = tuple(preview.boundary_loop_keys)
    loop_node_ids = tuple(preview.boundary_loop_node_ids)
    if (
        len(loops) != 2
        or len(loop_keys) != 2
        or len(loop_node_ids) != 2
    ):
        raise FlowPatchGeometryError(
            "An annular preview needs two owned boundary loops."
        )

    inverse = obj.matrix_world.inverted_safe()
    created_verts = []
    reused = set()
    mesh_loops = []
    boundary_tolerance = max(float(merge_distance) * 4.0, 1.0e-5)

    for points_value, loop_key, node_ids_value in zip(
        loops,
        loop_keys,
        loop_node_ids,
    ):
        points = tuple(Vector(point) for point in points_value)
        node_ids = tuple(int(value) for value in node_ids_value)
        if len(points) < 4 or len(node_ids) != len(points):
            raise FlowPatchGeometryError(
                "An annular boundary loop has inconsistent ownership."
            )

        record = boundary_registry.get(loop_key)
        canonical_uids = []
        if record is not None:
            if (
                not bool(record.get("closed", False))
                or int(record.get("count", 0)) != len(points)
            ):
                raise FlowPatchGeometryError(
                    "An annular boundary loop changed its point count."
                )
            canonical_uids = [
                int(value) for value in record.get("uids", [])
            ]
            if len(canonical_uids) != len(points):
                raise FlowPatchGeometryError(
                    "An annular boundary registry is incomplete."
                )

        loop_verts = []
        loop_uids = []
        for point_world, node_id, existing_uid in zip(
            points,
            node_ids,
            canonical_uids or (0 for _point in points),
        ):
            vert = None
            if existing_uid:
                vert = uid_map.get(existing_uid)
                if vert is None:
                    raise FlowPatchGeometryError(
                        "An annular boundary references deleted mesh geometry."
                    )
                if (
                    (obj.matrix_world @ vert.co) - point_world
                ).length > boundary_tolerance:
                    raise FlowPatchGeometryError(
                        "An annular boundary was moved away from its guide."
                    )
            if vert is None and node_id:
                node_uid = int(node_registry.get(str(node_id), 0))
                if node_uid:
                    vert = uid_map.get(node_uid)
                    if vert is None:
                        raise FlowPatchGeometryError(
                            "An annular graph node references deleted geometry."
                        )
            if vert is None:
                nearby = _nearby_vert(
                    obj,
                    bm,
                    point_world,
                    merge_distance,
                )
                if nearby is not None and node_id:
                    vert = nearby
                elif nearby is not None:
                    raise FlowPatchGeometryError(
                        "An annular boundary touches existing geometry without "
                        "shared guide ownership."
                    )
                else:
                    vert = bm.verts.new(inverse @ point_world)
                    vert[vert_layer] = patch_id
                    next_uid = _uid_for_new_vert(
                        vert,
                        uid_layer,
                        uid_map,
                        next_uid,
                    )
                    created_verts.append(vert)
            else:
                reused.add(vert)

            uid = int(vert[uid_layer])
            loop_verts.append(vert)
            loop_uids.append(uid)
            if node_id:
                registered = int(node_registry.get(str(node_id), 0))
                if registered and registered != uid:
                    raise FlowPatchGeometryError(
                        "An annular graph node disagrees with its boundary UID."
                    )
                node_registry[str(node_id)] = uid

        if not canonical_uids:
            boundary_registry[loop_key] = {
                "count": len(points),
                "uids": list(loop_uids),
                "revision": 1,
                "source_kind": "DRAWN",
                "axis": "",
                "closed": True,
            }
        mesh_loops.append(tuple(loop_verts))

    mesh_verts = tuple(vert for loop in mesh_loops for vert in loop)
    quad_points = tuple(preview.quad_vertices_world)
    quad_indices = tuple(preview.polygon_quads)
    if len(mesh_verts) != len(quad_points) or not quad_indices:
        raise FlowPatchGeometryError(
            "The annular preview topology does not match its boundary loops."
        )
    for vert, point_world in zip(mesh_verts, quad_points):
        if (
            (obj.matrix_world @ vert.co) - Vector(point_world)
        ).length > boundary_tolerance:
            raise FlowPatchGeometryError(
                "The annular preview boundary no longer matches its guides."
            )

    faces = []
    for quad in quad_indices:
        indices = tuple(int(index) for index in quad)
        if (
            len(indices) != 4
            or len(set(indices)) != 4
            or min(indices) < 0
            or max(indices) >= len(mesh_verts)
        ):
            raise FlowPatchGeometryError(
                "The annular preview contains an invalid quad."
            )
        face_verts = tuple(mesh_verts[index] for index in indices)
        if any(
            len(face.verts) == 4 and set(face.verts) == set(face_verts)
            for face in bm.faces
        ):
            raise FlowPatchGeometryError(
                "The annular preview overlaps existing topology."
            )
        for start, end in zip(
            face_verts,
            face_verts[1:] + face_verts[:1],
        ):
            edge = bm.edges.get((start, end))
            if edge is not None and len(edge.link_faces) >= 2:
                raise FlowPatchGeometryError(
                    "The annular preview would create a non-manifold edge."
                )
        try:
            face = bm.faces.new(face_verts)
        except ValueError as exc:
            raise FlowPatchGeometryError(
                "The annular preview could not create its quad faces."
            ) from exc
        face[face_layer] = patch_id
        faces.append(face)

    boundary_edges = set()
    for loop in mesh_loops:
        for start, end in zip(loop, loop[1:] + loop[:1]):
            edge = bm.edges.get((start, end))
            if edge is not None:
                boundary_edges.add(edge)
                if edge[edge_layer] == 0:
                    edge[edge_layer] = patch_id
                edge[edge_role_layer] = EDGE_ROLE_RAIL

    bmesh.ops.recalc_face_normals(bm, faces=faces)
    created_edges = set(bm.edges) - existing_edges
    for edge in created_edges:
        if edge[edge_layer] == 0:
            edge[edge_layer] = patch_id
        if edge not in boundary_edges:
            edge[edge_role_layer] = EDGE_ROLE_SPOKE
    existing_edges.update(created_edges)

    record = _cell_record(
        preview,
        patch_id,
        mesh_verts,
        uid_layer,
        face_count=len(faces),
    )
    record["source_cycle_keys"] = [
        str(value) for value in preview.source_cycle_keys
    ]
    return (
        GuideCommitResult(
            cycle_key=preview.cycle_key,
            patch_id=patch_id,
            created_vertices=len(created_verts),
            created_edges=len(created_edges),
            created_faces=len(faces),
            reused_vertices=len(reused),
            cell_record=record,
        ),
        next_uid,
        created_verts,
        faces,
    )


def _commit_previews_into_bmesh(
    obj,
    bm,
    previews,
    boundary_registry,
    node_registry,
    merge_distance,
    rollback_on_error,
):
    vert_layer, edge_layer, face_layer, edge_role_layer = _ensure_layers(bm)
    uid_layer, next_uid = _ensure_vertex_uids(obj, bm)
    uid_map = _uid_map(bm, uid_layer)
    existing_edges = set(bm.edges)
    inverse = obj.matrix_world.inverted_safe()
    results = []
    all_created_verts = []
    all_created_faces = []

    try:
        for preview in previews:
            patch_id = _next_patch_id(bm)
            topology_kind = str(
                getattr(preview, "topology_kind", "GRID")
            ).upper()
            if topology_kind == "ANNULAR":
                (
                    result,
                    next_uid,
                    created_verts,
                    created_faces,
                ) = _commit_annular_preview(
                    obj=obj,
                    bm=bm,
                    preview=preview,
                    patch_id=patch_id,
                    boundary_registry=boundary_registry,
                    node_registry=node_registry,
                    merge_distance=merge_distance,
                    vert_layer=vert_layer,
                    edge_layer=edge_layer,
                    face_layer=face_layer,
                    edge_role_layer=edge_role_layer,
                    uid_layer=uid_layer,
                    uid_map=uid_map,
                    next_uid=next_uid,
                    existing_edges=existing_edges,
                )
                all_created_verts.extend(created_verts)
                all_created_faces.extend(created_faces)
                results.append(result)
                continue
            if topology_kind == "POLYGON":
                if not ENABLE_LEGACY_POLYGON_PATCHES:
                    raise FlowPatchGeometryError(
                        "Polygon patch commits are disabled during FlowPatch "
                        "stabilization. The guide network remains editable."
                    )
                (
                    result,
                    next_uid,
                    created_verts,
                    created_faces,
                ) = _commit_polygon_preview(
                    obj=obj,
                    bm=bm,
                    preview=preview,
                    patch_id=patch_id,
                    boundary_registry=boundary_registry,
                    node_registry=node_registry,
                    merge_distance=merge_distance,
                    vert_layer=vert_layer,
                    edge_layer=edge_layer,
                    face_layer=face_layer,
                    edge_role_layer=edge_role_layer,
                    uid_layer=uid_layer,
                    uid_map=uid_map,
                    next_uid=next_uid,
                    existing_edges=existing_edges,
                )
                all_created_verts.extend(created_verts)
                all_created_faces.extend(created_faces)
                results.append(result)
                continue
            grid = [
                [None for _column in range(preview.u_segments + 1)]
                for _row in range(preview.v_segments + 1)
            ]
            created_verts = []
            reused = set()

            # A captured mesh side may appear after drawn sides in cycle order.
            # Seed its endpoint ownership first so those drawn sides can reuse
            # the legitimate shared corners without permitting arbitrary welds.
            for side_index, canonical_source_uids in enumerate(
                preview.side_source_uids
            ):
                if not canonical_source_uids:
                    continue
                traversal_source_uids = (
                    list(canonical_source_uids)
                    if preview.side_forward[side_index]
                    else list(reversed(canonical_source_uids))
                )
                for node_id, uid in (
                    (
                        preview.corner_node_ids[side_index],
                        traversal_source_uids[0],
                    ),
                    (
                        preview.corner_node_ids[(side_index + 1) % 4],
                        traversal_source_uids[-1],
                    ),
                ):
                    if uid_map.get(int(uid)) is None:
                        raise FlowPatchGeometryError(
                            "A Continue On boundary references deleted mesh "
                            "geometry."
                        )
                    existing_uid = int(node_registry.get(str(node_id), 0))
                    if existing_uid and existing_uid != int(uid):
                        raise FlowPatchGeometryError(
                            "A graph corner disagrees with its Continue On "
                            "boundary vertex."
                        )
                    node_registry[str(node_id)] = int(uid)

            for side_index, side_key in enumerate(preview.side_keys):
                positions = _side_grid_positions(preview, side_index)
                expected_count = len(positions) - 1
                expected_axis = _axis_for_side_index(side_index)
                record = boundary_registry.get(side_key)
                canonical_uids = []
                if record is not None:
                    if int(record.get("count", 0)) != expected_count:
                        raise FlowPatchGeometryError(
                            "A shared boundary has a different subdivision count."
                        )
                    record_axis = str(record.get("axis", "")).upper()
                    if record_axis and record_axis != expected_axis:
                        raise FlowPatchGeometryError(
                            "A shared boundary has a different U/V orientation."
                        )
                    record.setdefault("axis", expected_axis)
                    canonical_uids = [
                        int(value) for value in record.get("uids", [])
                    ]
                if not canonical_uids:
                    canonical_uids = [
                        int(value)
                        for value in preview.side_source_uids[side_index]
                    ]
                if canonical_uids and len(canonical_uids) != len(positions):
                    raise FlowPatchGeometryError(
                        "An existing boundary cannot be resampled without "
                        "creating a mesh T-junction."
                    )

                traversal_uids = (
                    list(canonical_uids)
                    if preview.side_forward[side_index]
                    else list(reversed(canonical_uids))
                )
                if traversal_uids:
                    for (row_index, column_index), uid in zip(
                        positions,
                        traversal_uids,
                    ):
                        vert = uid_map.get(uid)
                        if vert is None:
                            raise FlowPatchGeometryError(
                                "A shared boundary was manually changed; "
                                "freeze or rebuild the affected cell."
                            )
                        current = grid[row_index][column_index]
                        if current is not None and current is not vert:
                            raise FlowPatchGeometryError(
                                "Two boundary sides disagree at a cell corner."
                            )
                        grid[row_index][column_index] = vert
                        reused.add(vert)
                else:
                    traversal_uids = []
                    for position_index, (row_index, column_index) in enumerate(
                        positions
                    ):
                        vert = grid[row_index][column_index]
                        node_id = 0
                        if position_index == 0:
                            node_id = preview.corner_node_ids[side_index]
                        elif position_index == len(positions) - 1:
                            node_id = preview.corner_node_ids[
                                (side_index + 1) % 4
                            ]
                        if vert is None and node_id:
                            uid = int(node_registry.get(str(node_id), 0))
                            if uid:
                                vert = uid_map.get(uid)
                                if vert is None:
                                    raise FlowPatchGeometryError(
                                        "A graph corner references deleted mesh "
                                        "geometry."
                                    )
                        if vert is None:
                            world_point = preview.rails_world[row_index][
                                column_index
                            ]
                            nearby = _nearby_vert(
                                obj,
                                bm,
                                world_point,
                                merge_distance,
                            )
                            if nearby is not None and node_id:
                                vert = nearby
                            elif nearby is not None:
                                raise FlowPatchGeometryError(
                                    "A new guide boundary touches existing mesh "
                                    "geometry without Continue On ownership."
                                )
                            else:
                                vert = bm.verts.new(inverse @ Vector(world_point))
                                vert[vert_layer] = patch_id
                                next_uid = _uid_for_new_vert(
                                    vert,
                                    uid_layer,
                                    uid_map,
                                    next_uid,
                                )
                                created_verts.append(vert)
                                all_created_verts.append(vert)
                        else:
                            reused.add(vert)
                        grid[row_index][column_index] = vert
                        traversal_uids.append(int(vert[uid_layer]))
                        if node_id:
                            node_registry[str(node_id)] = int(vert[uid_layer])

                    canonical_uids = (
                        list(traversal_uids)
                        if preview.side_forward[side_index]
                        else list(reversed(traversal_uids))
                    )
                    boundary_registry[side_key] = {
                        "count": expected_count,
                        "uids": canonical_uids,
                        "revision": 1,
                        "source_kind": (
                            "MESH"
                            if preview.side_source_uids[side_index]
                            else "DRAWN"
                        ),
                        "axis": expected_axis,
                    }

                if side_key not in boundary_registry:
                    canonical_uids = (
                        list(traversal_uids)
                        if preview.side_forward[side_index]
                        else list(reversed(traversal_uids))
                    )
                    boundary_registry[side_key] = {
                        "count": expected_count,
                        "uids": canonical_uids,
                        "revision": 1,
                        "source_kind": "MESH",
                        "axis": expected_axis,
                    }

                start_uid = traversal_uids[0]
                end_uid = traversal_uids[-1]
                node_registry[
                    str(preview.corner_node_ids[side_index])
                ] = int(start_uid)
                node_registry[
                    str(preview.corner_node_ids[(side_index + 1) % 4])
                ] = int(end_uid)

            for row_index in range(1, preview.v_segments):
                for column_index in range(1, preview.u_segments):
                    if grid[row_index][column_index] is not None:
                        continue
                    vert = bm.verts.new(
                        inverse
                        @ Vector(
                            preview.rails_world[row_index][column_index]
                        )
                    )
                    vert[vert_layer] = patch_id
                    next_uid = _uid_for_new_vert(
                        vert,
                        uid_layer,
                        uid_map,
                        next_uid,
                    )
                    created_verts.append(vert)
                    all_created_verts.append(vert)
                    grid[row_index][column_index] = vert

            created_faces = []
            for row_index in range(preview.v_segments):
                for column_index in range(preview.u_segments):
                    face_verts = (
                        grid[row_index][column_index],
                        grid[row_index][column_index + 1],
                        grid[row_index + 1][column_index + 1],
                        grid[row_index + 1][column_index],
                    )
                    if None in face_verts or len(set(face_verts)) != 4:
                        raise FlowPatchGeometryError(
                            "Guide ownership would create a collapsed quad."
                        )
                    existing = next(
                        (
                            face
                            for face in bm.faces
                            if len(face.verts) == 4
                            and set(face.verts) == set(face_verts)
                        ),
                        None,
                    )
                    if existing is not None:
                        raise FlowPatchGeometryError(
                            "A guide cell overlaps existing topology."
                        )
                    for start, end in zip(
                        face_verts,
                        face_verts[1:] + face_verts[:1],
                    ):
                        edge = bm.edges.get((start, end))
                        if edge is not None and len(edge.link_faces) >= 2:
                            raise FlowPatchGeometryError(
                                "A guide cell would create a non-manifold edge."
                            )
                    try:
                        face = bm.faces.new(face_verts)
                    except ValueError as exc:
                        raise FlowPatchGeometryError(
                            "A guide cell overlaps existing topology."
                        ) from exc
                    face[face_layer] = patch_id
                    created_faces.append(face)
                    all_created_faces.append(face)

            for row in grid:
                for start, end in zip(row, row[1:]):
                    edge = bm.edges.get((start, end))
                    if edge is not None:
                        if edge[edge_layer] == 0:
                            edge[edge_layer] = patch_id
                        edge[edge_role_layer] = EDGE_ROLE_RAIL
            for current, following in zip(grid, grid[1:]):
                for start, end in zip(current, following):
                    edge = bm.edges.get((start, end))
                    if edge is not None:
                        if edge[edge_layer] == 0:
                            edge[edge_layer] = patch_id
                        edge[edge_role_layer] = EDGE_ROLE_SPOKE

            bmesh.ops.recalc_face_normals(bm, faces=created_faces)
            created_edges = set(bm.edges) - existing_edges
            for edge in created_edges:
                if edge[edge_layer] == 0:
                    edge[edge_layer] = patch_id
            existing_edges.update(created_edges)
            record = _cell_record(preview, patch_id, grid, uid_layer)
            results.append(
                GuideCommitResult(
                    cycle_key=preview.cycle_key,
                    patch_id=patch_id,
                    created_vertices=len(created_verts),
                    created_edges=len(created_edges),
                    created_faces=len(created_faces),
                    reused_vertices=len(reused),
                    cell_record=record,
                )
            )
    except Exception:
        if rollback_on_error:
            valid_faces = [
                face for face in all_created_faces if face.is_valid
            ]
            if valid_faces:
                bmesh.ops.delete(
                    bm,
                    geom=valid_faces,
                    context="FACES_ONLY",
                )
            valid_verts = [
                vert for vert in all_created_verts if vert.is_valid
            ]
            if valid_verts:
                bmesh.ops.delete(
                    bm,
                    geom=valid_verts,
                    context="VERTS",
                )
        raise
    return results, boundary_registry, node_registry, next_uid


def commit_guide_patches(
    obj,
    bm,
    previews,
    merge_distance=1.0e-5,
    guides=None,
):
    if not previews:
        raise FlowPatchGeometryError("No closed guide cell is ready.")

    initial_boundaries = load_boundary_registry(obj)
    initial_nodes = load_node_vertex_registry(obj)
    dry_bm = bm.copy()
    try:
        dry_results, _dry_boundaries, _dry_nodes, _dry_uid = (
            _commit_previews_into_bmesh(
            obj=obj,
            bm=dry_bm,
            previews=previews,
            boundary_registry=deepcopy(initial_boundaries),
            node_registry=deepcopy(initial_nodes),
            merge_distance=merge_distance,
            rollback_on_error=False,
            )
        )
        _attach_commit_sync_metadata(
            obj,
            previews,
            dry_results,
            guides,
        )
    finally:
        dry_bm.free()

    results, boundary_registry, node_registry, next_uid = (
        _commit_previews_into_bmesh(
            obj=obj,
            bm=bm,
            previews=previews,
            boundary_registry=deepcopy(initial_boundaries),
            node_registry=deepcopy(initial_nodes),
            merge_distance=merge_distance,
            rollback_on_error=True,
        )
    )
    _attach_commit_sync_metadata(
        obj,
        previews,
        results,
        guides,
    )
    save_boundary_registry(obj, boundary_registry)
    save_node_vertex_registry(obj, node_registry)
    obj[VERTEX_UID_COUNTER_KEY] = int(next_uid)

    _vert_layer, _edge_layer, face_layer, _edge_role_layer = _ensure_layers(bm)
    for vert in bm.verts:
        vert.select_set(False)
    for edge in bm.edges:
        edge.select_set(False)
    for face in bm.faces:
        face.select_set(False)
    for result in results:
        for face in bm.faces:
            if face[face_layer] == result.patch_id:
                face.select_set(True)

    bmesh.update_edit_mesh(obj.data, loop_triangles=True, destructive=True)
    return results


@dataclass
class GuideMeshSyncResult:
    built_cells: dict
    changed_cell_keys: tuple = ()
    frozen_cell_keys: tuple = ()
    changed_control_count: int = 0
    message: str = ""


def _current_mesh_sync_data(bm):
    uid_layer = bm.verts.layers.int.get(VERTEX_UID_LAYER)
    if uid_layer is None:
        raise MeshSyncError(
            "SYNC_UID_LAYER_MISSING",
            "The retopo mesh has no stable FlowPatch vertex UID layer.",
        )
    uid_map = {}
    duplicate_uids = set()
    for vert in bm.verts:
        uid = int(vert[uid_layer])
        if uid <= 0:
            continue
        if uid in uid_map:
            duplicate_uids.add(uid)
        else:
            uid_map[uid] = vert
    if duplicate_uids:
        raise MeshSyncError(
            "SYNC_DUPLICATE_VERTEX_UID",
            "The retopo mesh contains duplicate FlowPatch vertex UIDs.",
        )

    edges = []
    for edge in bm.edges:
        uids = tuple(int(vert[uid_layer]) for vert in edge.verts)
        if all(uid > 0 for uid in uids):
            edges.append(canonical_edge(*uids))
    faces = []
    for face in bm.faces:
        uids = tuple(int(vert[uid_layer]) for vert in face.verts)
        if all(uid > 0 for uid in uids):
            faces.append(canonical_face_cycle(uids))
    positions = {
        uid: tuple(float(value) for value in vert.co)
        for uid, vert in uid_map.items()
    }
    return uid_layer, uid_map, tuple(edges), tuple(faces), positions


def _known_sync_topology(built_cells):
    edges = set()
    faces = set()
    for record in built_cells.values():
        if not isinstance(record, dict):
            continue
        if str(record.get("state", "")).upper() == SYNC_DETACHED:
            continue
        if str(record.get("topology_kind", "")).upper() != "GRID":
            continue
        try:
            topology = grid_topology(record.get("grid_vertex_uids", ()))
        except MeshSyncError:
            continue
        edges.update(topology.edges)
        faces.update(topology.faces)
    return tuple(sorted(edges)), tuple(sorted(faces))


def audit_built_cell_sync(
    bm,
    built_cells,
    allow_unfreeze=False,
):
    staged = deepcopy(built_cells)
    decisions = {}
    try:
        (
            _uid_layer,
            uid_map,
            edges,
            faces,
            positions,
        ) = _current_mesh_sync_data(bm)
        known_edges, known_faces = _known_sync_topology(staged)
    except MeshSyncError as exc:
        for cycle_key, record in tuple(staged.items()):
            if str(record.get("state", "")).upper() == SYNC_DETACHED:
                decisions[str(cycle_key)] = SyncDecision(
                    SYNC_DETACHED,
                    "SYNC_DETACHED",
                    "The built cell is detached from synchronization.",
                )
                continue
            staged[str(cycle_key)] = frozen_record(
                record,
                exc.reason_code,
                str(exc),
            )
            decisions[str(cycle_key)] = SyncDecision(
                SYNC_FROZEN,
                exc.reason_code,
                str(exc),
            )
        return staged, decisions, {}

    for cycle_key, record in tuple(staged.items()):
        key = str(cycle_key)
        decision = audit_grid_record(
            record,
            current_uids=uid_map,
            current_edges=edges,
            current_faces=faces,
            current_positions=positions,
            allowed_edges=known_edges,
            allowed_faces=known_faces,
        )
        prior_state = str(record.get("state", "")).upper()
        if (
            prior_state == SYNC_FROZEN
            and decision.compatible
            and not allow_unfreeze
        ):
            decision = SyncDecision(
                SYNC_FROZEN,
                str(record.get("sync_reason_code", "SYNC_FROZEN")),
                str(
                    record.get(
                        "sync_message",
                        "The built cell requires explicit resynchronization.",
                    )
                ),
                changed_uids=decision.changed_uids,
            )
        decisions[key] = decision
        if decision.state == SYNC_FROZEN:
            if (
                prior_state != SYNC_FROZEN
                or str(record.get("sync_reason_code", ""))
                != decision.reason_code
            ):
                staged[key] = frozen_record(
                    record,
                    decision.reason_code,
                    decision.message,
                )
        elif decision.state == SYNC_DETACHED:
            staged[key] = deepcopy(record)
        else:
            updated = deepcopy(record)
            updated["state"] = SYNC_PARAMETRIC
            updated["sync_reason_code"] = decision.reason_code
            updated["sync_message"] = decision.message
            staged[key] = updated
    return staged, decisions, uid_map


def _refresh_all_record_positions(built_cells, uid_map):
    for record in built_cells.values():
        if not isinstance(record, dict):
            continue
        positions = {}
        missing = False
        for uid in record.get("vertex_uids", ()):
            vert = uid_map.get(int(uid))
            if vert is None:
                missing = True
                break
            positions[str(int(uid))] = _vector_payload(vert.co)
        if not missing:
            record["mesh_positions"] = positions


def _committed_grid_previews(
    obj,
    guides,
    built_cells,
    projector,
    cycle_keys,
):
    validation_errors = []
    cycles, _edge_nodes, guide_by_id = find_bounded_regions(
        obj,
        guides,
        projector,
        validation_errors=validation_errors,
    )
    cycle_by_key = {str(cycle.key): cycle for cycle in cycles}
    boundary_registry = load_boundary_registry(obj)
    previews = []
    for cycle_key in cycle_keys:
        record = built_cells[str(cycle_key)]
        cycle = cycle_by_key.get(str(cycle_key))
        if cycle is None:
            raise FlowPatchGeometryError(
                "A synchronized built cell no longer has a closed guide region."
            )
        if len(cycle.sides) != 4:
            raise FlowPatchGeometryError(
                "Only four-sided GRID cells support guide-to-mesh sync."
            )
        preview = build_cycle_preview(
            obj=obj,
            cycle=cycle,
            guide_by_id=guide_by_id,
            projector=projector,
            u_segments=int(record.get("u_segments", 0)),
            v_segments=int(record.get("v_segments", 0)),
            projection_mode=str(record.get("projection_mode", "SURFACE")),
            surface_offset=float(record.get("surface_offset", 0.0)),
            surface_follow_strength=float(
                record.get("surface_follow_strength", 1.0)
            ),
            surface_tighten_strength=float(
                record.get("surface_tighten_strength", 0.0)
            ),
            surface_smoothing_radius=int(
                record.get("surface_smoothing_radius", 1)
            ),
            detail_ignore_threshold=float(
                record.get("detail_ignore_threshold", 0.0)
            ),
            boundary_registry=boundary_registry,
        )
        previews.append(preview)
    return previews


def synchronize_mesh_from_guides(
    obj,
    bm,
    guides,
    built_cells,
    projector,
    changed_guide_ids=None,
    force=False,
):
    staged, decisions, uid_map = audit_built_cell_sync(
        bm,
        built_cells,
        allow_unfreeze=force,
    )
    changed_ids = {
        int(value) for value in tuple(changed_guide_ids or ())
    }
    selected_keys = []
    frozen_keys = []
    for cycle_key, record in staged.items():
        state = str(record.get("state", "")).upper()
        if state == SYNC_DETACHED:
            continue
        if changed_ids and not changed_ids.intersection(
            int(value) for value in record.get("edge_ids", ())
        ):
            continue
        decision = decisions.get(str(cycle_key))
        if decision is None or not decision.compatible:
            frozen_keys.append(str(cycle_key))
            continue
        selected_keys.append(str(cycle_key))

    if frozen_keys:
        save_built_cells(obj, staged)
        return GuideMeshSyncResult(
            built_cells=staged,
            frozen_cell_keys=tuple(sorted(frozen_keys)),
            message="One or more dependent regions are frozen.",
        )
    if not selected_keys:
        if staged != built_cells:
            save_built_cells(obj, staged)
        return GuideMeshSyncResult(
            built_cells=staged,
            message="No compatible built region depends on that guide edit.",
        )

    previews = _committed_grid_previews(
        obj,
        guides,
        staged,
        projector,
        selected_keys,
    )
    inverse = obj.matrix_world.inverted_safe()
    proposals = {}
    tolerance_squared = 1.0e-10
    for preview in previews:
        record = staged[str(preview.cycle_key)]
        grid = tuple(
            tuple(int(value) for value in row)
            for row in record.get("grid_vertex_uids", ())
        )
        if (
            len(grid) != len(preview.rails_world)
            or any(
                len(uid_row) != len(point_row)
                for uid_row, point_row in zip(grid, preview.rails_world)
            )
        ):
            raise FlowPatchGeometryError(
                "The committed GRID UID map no longer matches its preview."
            )
        for uid_row, point_row in zip(grid, preview.rails_world):
            for uid, point_world in zip(uid_row, point_row):
                point_local = inverse @ Vector(point_world)
                previous = proposals.get(int(uid))
                if (
                    previous is not None
                    and (previous - point_local).length_squared
                    > tolerance_squared
                ):
                    raise FlowPatchGeometryError(
                        "Adjacent synchronized cells disagree on a shared vertex."
                    )
                proposals[int(uid)] = point_local

    missing = sorted(uid for uid in proposals if uid not in uid_map)
    if missing:
        raise FlowPatchGeometryError(
            "A synchronized patch vertex was deleted from the mesh."
        )
    coordinate_snapshot = {
        uid: uid_map[uid].co.copy() for uid in proposals
    }
    had_guide_property = GUIDE_DATA_KEY in obj
    guide_property_snapshot = obj.get(GUIDE_DATA_KEY, "")
    had_built_property = BUILT_CELL_DATA_KEY in obj
    built_property_snapshot = obj.get(BUILT_CELL_DATA_KEY, "")
    try:
        for uid, point_local in proposals.items():
            uid_map[uid].co = point_local
        bmesh.update_edit_mesh(
            obj.data,
            loop_triangles=True,
            destructive=False,
        )
        _refresh_all_record_positions(staged, uid_map)
        for cycle_key in selected_keys:
            record = staged[cycle_key]
            record["state"] = SYNC_PARAMETRIC
            record["sync_reason_code"] = "IN_SYNC"
            record["sync_message"] = (
                "Guide and mesh positions are synchronized."
            )
            record["last_sync_direction"] = "GUIDES_TO_MESH"
            record["revision"] = int(record.get("revision", 0)) + 1
        save_guides(obj, guides)
        save_built_cells(obj, staged)
    except Exception:
        for uid, point_local in coordinate_snapshot.items():
            uid_map[uid].co = point_local
        if had_guide_property:
            obj[GUIDE_DATA_KEY] = guide_property_snapshot
        elif GUIDE_DATA_KEY in obj:
            del obj[GUIDE_DATA_KEY]
        if had_built_property:
            obj[BUILT_CELL_DATA_KEY] = built_property_snapshot
        elif BUILT_CELL_DATA_KEY in obj:
            del obj[BUILT_CELL_DATA_KEY]
        bmesh.update_edit_mesh(
            obj.data,
            loop_triangles=True,
            destructive=False,
        )
        raise
    return GuideMeshSyncResult(
        built_cells=staged,
        changed_cell_keys=tuple(sorted(selected_keys)),
        message=f"Updated {len(selected_keys)} compatible mesh region(s).",
    )


def synchronize_guides_from_mesh(
    obj,
    bm,
    guides,
    built_cells,
    projector,
    target_object_uuid,
    normal_offset=0.0,
    force=False,
):
    staged_cells, decisions, uid_map = audit_built_cell_sync(
        bm,
        built_cells,
        allow_unfreeze=force,
    )
    position_keys = [
        cycle_key
        for cycle_key, decision in decisions.items()
        if decision.compatible and decision.changed_uids
    ]
    frozen_keys = [
        cycle_key
        for cycle_key, decision in decisions.items()
        if decision.state == SYNC_FROZEN
    ]
    if not position_keys:
        if staged_cells != built_cells:
            save_built_cells(obj, staged_cells)
        return GuideMeshSyncResult(
            built_cells=staged_cells,
            frozen_cell_keys=tuple(sorted(frozen_keys)),
            message=(
                "No supported mesh position edits need guide synchronization."
            ),
        )

    uid_positions = {
        uid: tuple(float(value) for value in vert.co)
        for uid, vert in uid_map.items()
    }
    supported_keys = []
    all_bindings = []
    for cycle_key in position_keys:
        record = staged_cells[cycle_key]
        bindings = tuple(record.get("control_bindings", ()))
        bound_uids = {
            int(binding[field])
            for binding in bindings
            if isinstance(binding, dict)
            for field in ("uid_a", "uid_b")
            if field in binding
        }
        changed_uids = set(decisions[cycle_key].changed_uids)
        if not bindings or not changed_uids.issubset(bound_uids):
            message = (
                "A position edit affected generated interior vertices with "
                "no corresponding guide controls."
            )
            staged_cells[cycle_key] = frozen_record(
                record,
                "SYNC_POSITION_EDIT_UNBOUND",
                message,
            )
            frozen_keys.append(cycle_key)
            continue
        supported_keys.append(cycle_key)
        all_bindings.extend(bindings)

    if not supported_keys:
        save_built_cells(obj, staged_cells)
        return GuideMeshSyncResult(
            built_cells=staged_cells,
            frozen_cell_keys=tuple(sorted(set(frozen_keys))),
            message="Position edits were frozen because they are not guide-bound.",
        )

    try:
        proposals = interpolate_control_bindings(
            all_bindings,
            uid_positions,
        )
    except MeshSyncError as exc:
        raise FlowPatchGeometryError(str(exc)) from exc
    staged_guides = clone_guides(guides)
    guide_by_id = {
        int(guide.guide_id): guide for guide in staged_guides
    }
    for (guide_id, point_index), position in proposals.items():
        guide = guide_by_id.get(int(guide_id))
        if guide is None or not 0 <= int(point_index) < len(guide.points_local):
            raise FlowPatchGeometryError(
                "A mesh-to-guide binding references a missing control."
            )
        guide.points_local[int(point_index)] = Vector(position)

    node_positions = {}
    for guide_id, point_index in proposals:
        guide = guide_by_id[int(guide_id)]
        if int(point_index) == 0:
            node_id = int(guide.start_node)
        elif int(point_index) == len(guide.points_local) - 1:
            node_id = int(guide.end_node)
        else:
            continue
        if node_id <= 0:
            continue
        point = guide.points_local[int(point_index)]
        previous = node_positions.get(node_id)
        if (
            previous is not None
            and (previous - point).length > 1.0e-5
        ):
            raise FlowPatchGeometryError(
                "Mesh edits propose conflicting positions for a shared node."
            )
        node_positions[node_id] = point.copy()
    for guide in staged_guides:
        if int(guide.start_node) in node_positions:
            guide.points_local[0] = node_positions[
                int(guide.start_node)
            ].copy()
        if int(guide.end_node) in node_positions:
            guide.points_local[-1] = node_positions[
                int(guide.end_node)
            ].copy()

    refresh_guide_surface_anchors(
        obj,
        staged_guides,
        projector,
        target_object_uuid,
        normal_offset=normal_offset,
    )
    guide_snapshot = clone_guides(guides)
    had_guide_property = GUIDE_DATA_KEY in obj
    guide_property_snapshot = obj.get(GUIDE_DATA_KEY, "")
    had_built_property = BUILT_CELL_DATA_KEY in obj
    built_property_snapshot = obj.get(BUILT_CELL_DATA_KEY, "")
    try:
        guides[:] = staged_guides
        save_guides(obj, guides)
        _refresh_all_record_positions(staged_cells, uid_map)
        for cycle_key in supported_keys:
            record = staged_cells[cycle_key]
            record["state"] = SYNC_PARAMETRIC
            record["sync_reason_code"] = "IN_SYNC"
            record["sync_message"] = (
                "Guide and mesh positions are synchronized."
            )
            record["last_sync_direction"] = "MESH_TO_GUIDES"
            record["revision"] = int(record.get("revision", 0)) + 1
        save_built_cells(obj, staged_cells)
    except Exception:
        guides[:] = guide_snapshot
        if had_guide_property:
            obj[GUIDE_DATA_KEY] = guide_property_snapshot
        elif GUIDE_DATA_KEY in obj:
            del obj[GUIDE_DATA_KEY]
        if had_built_property:
            obj[BUILT_CELL_DATA_KEY] = built_property_snapshot
        elif BUILT_CELL_DATA_KEY in obj:
            del obj[BUILT_CELL_DATA_KEY]
        raise
    return GuideMeshSyncResult(
        built_cells=staged_cells,
        changed_cell_keys=tuple(sorted(supported_keys)),
        frozen_cell_keys=tuple(sorted(set(frozen_keys))),
        changed_control_count=len(proposals),
        message=f"Updated {len(proposals)} guide control binding(s).",
    )


def detach_frozen_sync(obj, built_cells):
    staged = deepcopy(built_cells)
    detached = []
    for cycle_key, record in staged.items():
        if str(record.get("state", "")).upper() != SYNC_FROZEN:
            continue
        staged[cycle_key] = detached_record(record)
        detached.append(str(cycle_key))
    if detached:
        save_built_cells(obj, staged)
    return GuideMeshSyncResult(
        built_cells=staged,
        changed_cell_keys=tuple(sorted(detached)),
        message=(
            f"Detached {len(detached)} frozen region(s)."
            if detached
            else "No frozen region needs detaching."
        ),
    )
