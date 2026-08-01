import math
import os
from collections import deque
from copy import deepcopy

import bmesh
import bpy
from bpy.app.handlers import persistent
from bpy_extras import view3d_utils
from bpy.props import BoolProperty
from bpy.props import StringProperty
from bpy.types import Operator
from mathutils import Matrix
from mathutils import Vector

from .build_identity import ADDON_VERSION_STRING
from .build_identity import BUILD_ID
from .build_identity import PACKAGE_PAYLOAD_HASH_SCOPE
from .build_identity import PACKAGE_PAYLOAD_SHA256
from .build_identity import SOURCE_BRANCH
from .auto_build import AutoBuildPlanError
from .auto_build import plan_auto_build
from .diagnostics import build_debug_document
from .diagnostics import CapabilityResult
from .diagnostics import debug_document_json
from .diagnostics import resolve_rna_object
from .diagnostics import safe_rna_attr
from .diagnostics import safe_rna_get
from .diagnostics import safe_rna_name
from .diagnostics import SESSION_BREADCRUMBS
from .diagnostics import TOOL_REGISTRY
from .drawing import FlowPatchPreviewRenderer
from .existing_mesh import add_registry_record
from .existing_mesh import encode_existing_mesh_registry
from .existing_mesh import EXISTING_MESH_DATA_KEY
from .existing_mesh import ExistingMeshError
from .existing_mesh import infer_rectangular_quad_grid
from .existing_mesh import island_signature
from .existing_mesh import parse_existing_mesh_registry
from .existing_mesh import plan_equal_count_bridge
from .existing_mesh import plan_quad_island_adoption
from .geometry import _ensure_layers
from .geometry import _next_patch_id
from .geometry import EDGE_ROLE_RAIL
from .geometry import EDGE_ROLE_SPOKE
from .geometry import FlowPatchGeometryError
from .geometry import EDGE_LAYER_NAME
from .geometry import EDGE_ROLE_LAYER_NAME
from .geometry import FACE_LAYER_NAME
from .geometry import VERTEX_LAYER_NAME
from .geometry import apply_magnets
from .geometry import build_preview
from .geometry import commit_quad_strip
from .geometry import ordered_selected_boundary
from .geometry import patch_id_from_selection
from .geometry import select_patch_elements
from .geometry import subdivide_patch_rows
from .guides import append_guide_world
from .guides import append_selected_mesh_boundary
from .guides import audit_built_cell_sync
from .guides import BOUNDARY_REGISTRY_KEY
from .guides import BUILT_CELL_DATA_KEY
from .guides import build_uncommitted_previews
from .guides import commit_guide_patches
from .guides import detach_frozen_sync
from .guides import find_bounded_regions
from .guides import fair_guides_tangent
from .guides import GUIDE_DATA_KEY
from .guides import guides_world
from .guides import load_built_cells
from .guides import load_guides
from .guides import NODE_VERTEX_REGISTRY_KEY
from .guides import refresh_guide_surface_anchors
from .guides import remove_built_cell_geometry
from .guides import save_built_cells
from .guides import save_guides
from .guides import synchronize_guides_from_mesh
from .guides import synchronize_mesh_from_guides
from .guides import VERTEX_UID_COUNTER_KEY
from .guides import VERTEX_UID_LAYER
from .guides import _ensure_vertex_uids
from .guide_graph import clone_guides
from .guide_graph import connected_guide_indices
from .guide_graph import delete_guide_edge
from .guide_graph import delete_guide_point
from .guide_graph import graph_nodes
from .guide_graph import GuideDeleteError
from .guide_graph import GuideGraphBudgetError
from .guide_graph import GuidePath
from .guide_graph import next_guide_id
from .guide_graph import next_logical_side_id
from .guide_graph import next_node_id
from .hover_transform import resolve_hover_transform
from .mesh_sync import frozen_record
from .mesh_sync import grid_topology
from .mesh_sync import SYNC_DETACHED
from .mesh_sync import SYNC_FROZEN
from .mesh_sync import SYNC_PARAMETRIC
from .pen_state import PenPhase
from .pen_state import PenState
from .projection import SurfaceProjector
from .project_store import audit_projects
from .project_store import audit_composite_session
from .project_store import audit_snapshot
from .project_store import bind_project
from .project_store import build_composite_session
from .project_store import clear_composite_session
from .project_store import clear_project_identity
from .project_store import COMPOSITE_SESSION_KEY
from .project_store import ensure_object_uuid
from .project_store import find_object_by_uuid
from .project_store import OBJECT_UUID_KEY
from .project_store import PROJECT_RECORD_KEY
from .project_store import PROJECT_SCHEMA_VERSION
from .project_store import PROJECT_UUID_KEY
from .project_store import ProjectStoreError
from .project_store import project_objects_for_target
from .project_store import repair_passive_target_uuid_copies
from .project_store import read_composite_session
from .project_store import read_project_record
from .project_store import resolve_composite_objects
from .project_store import without_composite_project
from .project_store import write_composite_session
from .refine import flatten_patch_to_boundary_plane
from .refine import insert_patch_row_loops
from .refine import patch_row_preview_segments
from .seed import build_seed_preview
from .seed import commit_seed_patch
from .snap_state import HoverSnapState
from .snap_state import select_snap_candidate


_POINTER_IDLE = PenPhase.IDLE.value
_POINTER_DOWN = PenPhase.POINTER_DOWN.value
_POINTER_DRAWING = PenPhase.DRAWING.value
_POINTER_FINALIZING = PenPhase.FINALIZING.value
_POINTER_CANCELLING = PenPhase.CANCELLING.value

_SNAP_HOVER_PX = 18.0
_SNAP_COMMIT_PX = 18.0
_SNAP_HARD_MAX_PX = 22.0
_MAX_STROKE_SAMPLES = 4096
_SESSION_EPOCH = 0
_LAST_PROJECT_AUDIT = audit_snapshot(())
_LAST_SESSION_IDENTITY = {
    "project_uuid": "",
    "retopo_object_uuid": "",
    "retopo_object_name_hint": "",
    "target_object_uuid": "",
    "target_object_name_hint": "",
}


def _capture_flowpatch_id_properties(obj):
    return {
        str(key): deepcopy(obj[key])
        for key in obj.keys()
        if str(key).startswith("flowpatch_")
    }


def _restore_flowpatch_id_properties(obj, snapshot):
    for key in tuple(obj.keys()):
        if str(key).startswith("flowpatch_") and str(key) not in snapshot:
            del obj[key]
    for key, value in snapshot.items():
        obj[key] = deepcopy(value)


def _begin_auto_build_mesh_snapshot(bm):
    backup = bpy.data.meshes.new("__FlowPatchAutoBuildRollback")
    try:
        bm.to_mesh(backup)
    except Exception:
        bpy.data.meshes.remove(backup)
        raise
    return backup


def _restore_auto_build_mesh_snapshot(obj, bm, backup, properties):
    bm.clear()
    bm.from_mesh(backup)
    _restore_flowpatch_id_properties(obj, properties)
    bmesh.update_edit_mesh(
        obj.data,
        loop_triangles=True,
        destructive=True,
    )


def _discard_auto_build_mesh_snapshot(backup):
    if backup is not None and backup.name in bpy.data.meshes:
        bpy.data.meshes.remove(backup)


def _load_existing_mesh_registry(obj):
    return parse_existing_mesh_registry(
        obj.get(EXISTING_MESH_DATA_KEY, "")
    )


def _save_existing_mesh_registry(obj, registry):
    obj[EXISTING_MESH_DATA_KEY] = encode_existing_mesh_registry(registry)


def _composite_objects(scene):
    try:
        record = read_composite_session(scene)
        if record is None:
            return ()
        return resolve_composite_objects(tuple(bpy.data.objects), record)
    except ProjectStoreError:
        return ()


def _composite_guide_paths_world(scene, active_object):
    paths = []
    for obj in _composite_objects(scene):
        if obj is active_object or obj.type != "MESH":
            continue
        for guide in load_guides(obj):
            paths.append(
                [obj.matrix_world @ point for point in guide.points_local]
            )
    return paths


def _composite_debug_state(scene):
    if scene is None:
        return {
            "active": False,
            "session_uuid": "",
            "member_count": 0,
            "members": [],
            "error": "",
        }
    try:
        record = read_composite_session(scene)
        if record is None:
            return {
                "active": False,
                "session_uuid": "",
                "member_count": 0,
                "members": [],
                "error": "",
            }
        objects = resolve_composite_objects(tuple(bpy.data.objects), record)
        return {
            "active": True,
            "session_uuid": record.session_uuid,
            "member_count": len(record.members),
            "members": [safe_rna_name(obj) for obj in objects],
            "error": "",
        }
    except (ProjectStoreError, ReferenceError, RuntimeError) as exc:
        return {
            "active": False,
            "session_uuid": "",
            "member_count": 0,
            "members": [],
            "error": str(exc),
        }


def _canonical_closed_loop(values):
    loop = tuple(int(value) for value in values)
    rotations = []
    for candidate in (loop, tuple(reversed(loop))):
        rotations.extend(
            candidate[index:] + candidate[:index]
            for index in range(len(candidate))
        )
    return min(rotations)


def _ordered_selected_closed_loops(bm):
    selected_edges = {
        edge
        for edge in bm.edges
        if edge.select and not edge.hide and len(edge.link_faces) <= 1
    }
    if not selected_edges:
        return ()
    components = []
    remaining = set(selected_edges)
    while remaining:
        seed = min(
            remaining,
            key=lambda edge: tuple(sorted(vert.index for vert in edge.verts)),
        )
        stack = [seed]
        component = set()
        while stack:
            edge = stack.pop()
            if edge in component:
                continue
            component.add(edge)
            for vert in edge.verts:
                stack.extend(
                    linked
                    for linked in vert.link_edges
                    if linked in remaining and linked not in component
                )
        remaining.difference_update(component)
        components.append(component)

    loops = []
    for component in components:
        adjacency = {}
        for edge in component:
            start, end = (vert.index for vert in edge.verts)
            adjacency.setdefault(start, set()).add(end)
            adjacency.setdefault(end, set()).add(start)
        if any(len(neighbors) != 2 for neighbors in adjacency.values()):
            raise FlowPatchGeometryError(
                "Each bridge boundary must be one closed loop without branches."
            )
        start = min(adjacency)
        candidates = []
        for first in sorted(adjacency[start]):
            ordered = [start]
            previous = None
            current = start
            following = first
            traversed = set()
            while True:
                edge_key = tuple(sorted((current, following)))
                if edge_key in traversed:
                    break
                traversed.add(edge_key)
                previous, current = current, following
                ordered.append(current)
                if current == start:
                    break
                next_values = sorted(adjacency[current] - {previous})
                if len(next_values) != 1:
                    raise FlowPatchGeometryError(
                        "A selected bridge boundary cannot be ordered."
                    )
                following = next_values[0]
            if ordered[-1] != start or len(traversed) != len(component):
                raise FlowPatchGeometryError(
                    "Each selected bridge boundary must be closed."
                )
            candidates.append(_canonical_closed_loop(ordered[:-1]))
        loops.append(min(candidates))
    return tuple(sorted(loops))


def _append_adopted_boundary_guides(
    bm,
    guides,
    inferred_sides,
    uid_layer,
):
    if len(inferred_sides) != 4:
        return ()
    side_uids = []
    for side in inferred_sides:
        uids = tuple(int(bm.verts[index][uid_layer]) for index in side)
        if len(uids) < 2 or any(uid <= 0 for uid in uids):
            return ()
        side_uids.append(uids)
    if (
        len(side_uids[0]) != len(side_uids[2])
        or len(side_uids[1]) != len(side_uids[3])
    ):
        return ()

    endpoint_nodes = {}
    for guide in guides:
        if len(guide.source_vertex_uids) != len(guide.points_local):
            continue
        if not guide.source_vertex_uids:
            continue
        if guide.start_node > 0:
            endpoint_nodes.setdefault(
                int(guide.source_vertex_uids[0]),
                int(guide.start_node),
            )
        if guide.end_node > 0:
            endpoint_nodes.setdefault(
                int(guide.source_vertex_uids[-1]),
                int(guide.end_node),
            )

    created = []
    for side, uids in zip(inferred_sides, side_uids):
        start_uid = int(uids[0])
        end_uid = int(uids[-1])
        start_node = endpoint_nodes.get(start_uid)
        if start_node is None:
            start_node = next_node_id(guides)
            endpoint_nodes[start_uid] = start_node
        end_node = endpoint_nodes.get(end_uid)
        if end_node is None:
            end_node = next_node_id(guides)
            if end_node == start_node:
                end_node += 1
            endpoint_nodes[end_uid] = end_node
        guide = GuidePath(
            guide_id=next_guide_id(guides),
            points_local=[
                bm.verts[index].co.copy() for index in side
            ],
            start_node=int(start_node),
            end_node=int(end_node),
            logical_side_id=next_logical_side_id(guides),
            source_kind="ADOPTED_BOUNDARY",
            source_vertex_uids=uids,
        )
        guides.append(guide)
        created.append(int(guide.guide_id))
    return tuple(created)


def _cycle_side_source_uids(cycle, guide_by_id):
    side_uids = []
    for side in cycle.sides:
        values = []
        current_node = int(side.start_node)
        for guide_id in side.edge_ids:
            guide = guide_by_id.get(int(guide_id))
            if guide is None:
                return ()
            source = list(int(value) for value in guide.source_vertex_uids)
            if len(source) != len(guide.points_local):
                return ()
            if int(guide.start_node) == current_node:
                current_node = int(guide.end_node)
            elif int(guide.end_node) == current_node:
                source.reverse()
                current_node = int(guide.start_node)
            else:
                return ()
            if values:
                source = source[1:]
            values.extend(source)
        if current_node != int(side.end_node) or len(values) < 2:
            return ()
        side_uids.append(tuple(values))
    return tuple(side_uids)


def _adopted_control_bindings(cycle, side_uids):
    bindings = {}
    for side, uids in zip(cycle.sides, side_uids):
        if len(side.edge_ids) != 1:
            return ()
        guide_id = int(side.edge_ids[0])
        for point_index in range(len(uids)):
            if point_index < len(uids) - 1:
                uid_a = int(uids[point_index])
                uid_b = int(uids[point_index + 1])
                factor = 0.0
            else:
                uid_a = int(uids[-2])
                uid_b = int(uids[-1])
                factor = 1.0
            bindings[(guide_id, point_index)] = {
                "guide_id": guide_id,
                "point_index": point_index,
                "uid_a": uid_a,
                "uid_b": uid_b,
                "factor": factor,
            }
    return tuple(bindings[key] for key in sorted(bindings))


def _adopt_selected_quad_islands(
    obj,
    bm,
    guides,
    built_cells,
    project_uuid,
    projector,
    settings,
):
    bm.verts.ensure_lookup_table()
    bm.verts.index_update()
    bm.faces.ensure_lookup_table()
    bm.faces.index_update()
    face_cycles = {
        int(face.index): tuple(int(vert.index) for vert in face.verts)
        for face in bm.faces
    }
    selected_face_ids = {
        int(face.index)
        for face in bm.faces
        if face.select and not face.hide
    }
    plan = plan_quad_island_adoption(face_cycles, selected_face_ids)
    registry = _load_existing_mesh_registry(obj)
    existing_uid_layer = bm.verts.layers.int.get(VERTEX_UID_LAYER)
    repeated_preflight = 0
    if existing_uid_layer is not None:
        for island in plan.islands:
            faces = [bm.faces[index] for index in island.face_ids]
            if any(
                int(vert[existing_uid_layer]) <= 0
                for face in faces
                for vert in face.verts
            ):
                continue
            face_uid_sets = sorted(
                tuple(
                    sorted(
                        int(vert[existing_uid_layer])
                        for vert in face.verts
                    )
                )
                for face in faces
            )
            stable_signature = island_signature(face_uid_sets)
            existing = registry["islands"].get(stable_signature)
            if existing is not None and existing.get(
                "face_vertex_uids"
            ) == [list(values) for values in face_uid_sets]:
                repeated_preflight += 1
    if repeated_preflight == len(plan.islands):
        return {
            "registry": registry,
            "guides": guides,
            "built_cells": built_cells,
            "adopted": 0,
            "repeated": repeated_preflight,
            "inferred_guides": 0,
            "patch_ids": (),
        }

    uid_layer, next_uid = _ensure_vertex_uids(obj, bm)
    (
        vertex_owner_layer,
        edge_owner_layer,
        face_owner_layer,
        edge_role_layer,
    ) = _ensure_layers(bm)
    bm.verts.ensure_lookup_table()
    bm.verts.index_update()
    bm.edges.ensure_lookup_table()
    bm.edges.index_update()
    bm.faces.ensure_lookup_table()
    bm.faces.index_update()

    staged_guides = clone_guides(guides)
    staged_built = deepcopy(built_cells)
    adopted = 0
    repeated = 0
    inferred_guide_count = 0
    patch_ids = []
    for island in plan.islands:
        faces = [bm.faces[index] for index in island.face_ids]
        face_uid_sets = sorted(
            tuple(sorted(int(vert[uid_layer]) for vert in face.verts))
            for face in faces
        )
        stable_signature = island_signature(face_uid_sets)
        existing = registry["islands"].get(stable_signature)
        if existing is not None:
            if existing.get("face_vertex_uids") != [
                list(values) for values in face_uid_sets
            ]:
                raise ExistingMeshError(
                    "REGISTRY_OWNERSHIP_CONFLICT",
                    "An adopted-island signature has conflicting UID ownership.",
                )
            repeated += 1
            continue

        component_edges = {
            edge for face in faces for edge in face.edges
        }
        component_verts = {
            vert for face in faces for vert in face.verts
        }
        if any(int(face[face_owner_layer]) > 0 for face in faces):
            raise ExistingMeshError(
                "FACE_ALREADY_OWNED",
                "A selected face already belongs to another FlowPatch region.",
            )
        if any(
            int(edge[edge_owner_layer]) > 0 for edge in component_edges
        ):
            raise ExistingMeshError(
                "EDGE_ALREADY_OWNED",
                "A selected island edge already has FlowPatch ownership.",
            )
        if any(
            int(vert[vertex_owner_layer]) > 0 for vert in component_verts
        ):
            raise ExistingMeshError(
                "VERTEX_ALREADY_OWNED",
                "A selected island vertex already has FlowPatch ownership.",
            )

        patch_id = int(_next_patch_id(bm))
        patch_ids.append(patch_id)
        component_face_set = set(faces)
        boundary_edges = {
            edge
            for edge in component_edges
            if sum(
                linked in component_face_set for linked in edge.link_faces
            )
            == 1
        }
        for face in faces:
            face[face_owner_layer] = patch_id
        for edge in component_edges:
            edge[edge_owner_layer] = patch_id
            edge[edge_role_layer] = (
                EDGE_ROLE_RAIL
                if edge in boundary_edges
                else EDGE_ROLE_SPOKE
            )
        for vert in component_verts:
            vert[vertex_owner_layer] = patch_id

        guide_ids = _append_adopted_boundary_guides(
            bm,
            staged_guides,
            island.inferred_sides,
            uid_layer,
        )
        inferred_guide_count += len(guide_ids)
        boundary_uid_loops = [
            [int(bm.verts[index][uid_layer]) for index in loop]
            for loop in island.boundary_loops
        ]
        record = {
            "project_uuid": str(project_uuid),
            "patch_id": patch_id,
            "face_count": len(faces),
            "face_vertex_uids": [
                list(values) for values in face_uid_sets
            ],
            "boundary_loops": boundary_uid_loops,
            "guide_ids": list(guide_ids),
            "guide_inference": (
                "FOUR_SIDED_GRID" if len(guide_ids) == 4 else "NONE"
            ),
            "state": "ADOPTED",
        }
        registry, added = add_registry_record(
            registry,
            "islands",
            stable_signature,
            record,
        )
        if not added:
            raise ExistingMeshError(
                "REGISTRY_REPEAT_MISMATCH",
                "Adopted-island repeat handling became inconsistent.",
            )
        if len(guide_ids) == 4 and island.grid_vertex_ids:
            cycle_key = ":".join(str(value) for value in sorted(guide_ids))
            if cycle_key in staged_built:
                raise ExistingMeshError(
                    "BUILT_CELL_OWNERSHIP_CONFLICT",
                    "Inferred adopted guides conflict with a built cell key.",
                )
            validation_errors = []
            cycles, _edge_nodes, guide_by_id = find_bounded_regions(
                obj,
                staged_guides,
                projector,
                validation_errors=validation_errors,
            )
            cycle = next(
                (
                    candidate
                    for candidate in cycles
                    if str(candidate.key) == cycle_key
                ),
                None,
            )
            if cycle is None or len(cycle.sides) != 4:
                raise ExistingMeshError(
                    "ADOPTED_GUIDE_CYCLE_INVALID",
                    (
                        "The inferred boundary guides did not resolve to one "
                        "unambiguous four-sided region."
                    ),
                )
            side_uids = _cycle_side_source_uids(cycle, guide_by_id)
            face_uid_cycles = {
                int(face.index): tuple(
                    int(vert[uid_layer]) for vert in face.verts
                )
                for face in faces
            }
            grid_vertex_uids = infer_rectangular_quad_grid(
                face_uid_cycles,
                island.face_ids,
                side_uids,
            )
            if not grid_vertex_uids:
                raise ExistingMeshError(
                    "ADOPTED_GRID_AMBIGUOUS",
                    (
                        "The selected all-quad island could not be ordered "
                        "against its normalized four-sided guide cycle."
                    ),
                )
            topology = grid_topology(grid_vertex_uids)
            control_bindings = _adopted_control_bindings(
                cycle,
                side_uids,
            )
            if not control_bindings:
                raise ExistingMeshError(
                    "ADOPTED_CONTROL_BINDINGS_INVALID",
                    "The adopted grid could not bind its boundary controls.",
                )
            staged_built[cycle_key] = {
                "patch_id": patch_id,
                "topology_kind": "GRID",
                "state": SYNC_PARAMETRIC,
                "sync_reason_code": "IN_SYNC",
                "sync_message": (
                    "Adopted grid guides and mesh positions are synchronized."
                ),
                "edge_ids": [int(value) for value in cycle.edge_ids],
                "side_keys": [str(side.key) for side in cycle.sides],
                "u_segments": len(grid_vertex_uids[0]) - 1,
                "v_segments": len(grid_vertex_uids) - 1,
                "projection_mode": str(settings.projection_mode),
                "surface_follow_strength": float(
                    settings.surface_follow_strength
                ),
                "surface_tighten_strength": float(
                    settings.surface_tighten_strength
                ),
                "surface_smoothing_radius": int(
                    settings.surface_smoothing_radius
                ),
                "surface_offset": float(settings.surface_offset),
                "detail_ignore_threshold": float(
                    settings.detail_ignore_threshold
                ),
                "revision": 1,
                "vertex_uids": list(topology.vertex_uids),
                "grid_vertex_uids": [
                    list(row) for row in grid_vertex_uids
                ],
                "topology_signature": topology.signature,
                "mesh_positions": {
                    str(int(vert[uid_layer])): [
                        float(value) for value in vert.co
                    ]
                    for vert in component_verts
                },
                "control_bindings": [
                    dict(binding) for binding in control_bindings
                ],
                "side_forward": [
                    bool(side.forward_key) for side in cycle.sides
                ],
                "corner_node_ids": [
                    int(value) for value in cycle.node_ids
                ],
                "side_edge_ids": [
                    [int(value) for value in side.edge_ids]
                    for side in cycle.sides
                ],
                "last_sync_direction": "ADOPT",
                "adoption_origin": "EXISTING_ALL_QUAD_ISLAND",
                "face_count": len(faces),
            }
        adopted += 1

    obj[VERTEX_UID_COUNTER_KEY] = int(next_uid)
    _save_existing_mesh_registry(obj, registry)
    save_guides(obj, staged_guides)
    save_built_cells(obj, staged_built)
    return {
        "registry": registry,
        "guides": staged_guides,
        "built_cells": staged_built,
        "adopted": adopted,
        "repeated": repeated,
        "inferred_guides": inferred_guide_count,
        "patch_ids": tuple(patch_ids),
    }


def _bridge_selected_boundary_loops(
    obj,
    bm,
    loops,
    built_cells,
    project_uuid,
):
    if len(loops) != 2:
        raise ExistingMeshError(
            "TWO_BOUNDARIES_REQUIRED",
            "Select exactly two closed mesh boundary loops to bridge.",
        )
    uid_layer, next_uid = _ensure_vertex_uids(obj, bm)
    (
        vertex_owner_layer,
        edge_owner_layer,
        face_owner_layer,
        edge_role_layer,
    ) = _ensure_layers(bm)
    bm.verts.ensure_lookup_table()
    bm.verts.index_update()
    bm.faces.ensure_lookup_table()
    bm.faces.index_update()

    uid_loops = tuple(
        tuple(int(bm.verts[index][uid_layer]) for index in loop)
        for loop in loops
    )
    uid_map = {
        int(vert[uid_layer]): vert
        for vert in bm.verts
        if int(vert[uid_layer]) > 0
    }
    positions = {
        uid: tuple(float(value) for value in vert.co)
        for uid, vert in uid_map.items()
    }
    plan = plan_equal_count_bridge(
        uid_loops[0],
        uid_loops[1],
        positions=positions,
        same_object=True,
    )
    registry = _load_existing_mesh_registry(obj)
    existing = registry["bridges"].get(plan.signature)
    if existing is not None:
        return {
            "registry": registry,
            "built_cells": deepcopy(built_cells),
            "created_faces": 0,
            "repeated": True,
            "patch_id": int(existing.get("patch_id", 0)),
        }

    current_faces = {
        frozenset(int(vert[uid_layer]) for vert in face.verts)
        for face in bm.faces
        if all(int(vert[uid_layer]) > 0 for vert in face.verts)
    }
    if any(frozenset(face) in current_faces for face in plan.faces):
        raise ExistingMeshError(
            "BRIDGE_FACE_ALREADY_EXISTS",
            "A planned bridge quad already exists.",
        )
    planned_edge_uses = {}
    for face in plan.faces:
        for start, end in zip(face, face[1:] + face[:1]):
            edge_key = tuple(sorted((int(start), int(end))))
            planned_edge_uses[edge_key] = (
                planned_edge_uses.get(edge_key, 0) + 1
            )
    for edge_key, planned_uses in planned_edge_uses.items():
        start = uid_map[edge_key[0]]
        end = uid_map[edge_key[1]]
        edge = bm.edges.get((start, end))
        existing_uses = len(edge.link_faces) if edge is not None else 0
        if existing_uses + planned_uses > 2:
            raise ExistingMeshError(
                "BRIDGE_NON_MANIFOLD",
                "The bridge would create a non-manifold edge.",
            )

    patch_id = int(_next_patch_id(bm))
    boundary_edge_keys = {
        tuple(sorted((loop[index], loop[(index + 1) % len(loop)])))
        for loop in (plan.loop_a, plan.loop_b)
        for index in range(len(loop))
    }
    created_faces = []
    for face_uids in plan.faces:
        verts = tuple(uid_map[int(uid)] for uid in face_uids)
        try:
            face = bm.faces.new(verts)
        except ValueError as exc:
            raise ExistingMeshError(
                "BRIDGE_FACE_CREATE_FAILED",
                "A bridge quad could not be created without overlap.",
            ) from exc
        face[face_owner_layer] = patch_id
        created_faces.append(face)
    for face in created_faces:
        for vert in face.verts:
            if int(vert[vertex_owner_layer]) <= 0:
                vert[vertex_owner_layer] = patch_id
        for edge in face.edges:
            uids = tuple(sorted(int(vert[uid_layer]) for vert in edge.verts))
            if int(edge[edge_owner_layer]) <= 0:
                edge[edge_owner_layer] = patch_id
            edge[edge_role_layer] = (
                EDGE_ROLE_RAIL
                if uids in boundary_edge_keys
                else EDGE_ROLE_SPOKE
            )
    if any(len(face.verts) != 4 for face in created_faces):
        raise ExistingMeshError(
            "BRIDGE_NON_QUAD_OUTPUT",
            "Bridge generation produced a non-quad face.",
        )

    record = {
        "project_uuid": str(project_uuid),
        "patch_id": patch_id,
        "loop_a": list(plan.loop_a),
        "loop_b": list(plan.loop_b),
        "faces": [list(face) for face in plan.faces],
        "face_count": len(created_faces),
        "state": "BRIDGED",
    }
    registry, added = add_registry_record(
        registry,
        "bridges",
        plan.signature,
        record,
    )
    if not added:
        raise ExistingMeshError(
            "REGISTRY_REPEAT_MISMATCH",
            "Bridge repeat handling became inconsistent.",
        )
    staged_built = deepcopy(built_cells)
    staged_built[f"bridge:{plan.signature}"] = {
        "patch_id": patch_id,
        "topology_kind": "BRIDGE",
        "state": SYNC_DETACHED,
        "sync_reason_code": "EXISTING_MESH_BRIDGE",
        "sync_message": (
            "Existing-mesh bridge is detached from guide synchronization."
        ),
        "vertex_uids": sorted(set(plan.loop_a) | set(plan.loop_b)),
        "face_count": len(created_faces),
    }
    obj[VERTEX_UID_COUNTER_KEY] = int(next_uid)
    _save_existing_mesh_registry(obj, registry)
    save_built_cells(obj, staged_built)
    return {
        "registry": registry,
        "built_cells": staged_built,
        "created_faces": len(created_faces),
        "repeated": False,
        "patch_id": patch_id,
    }


def _active_tool_id(context):
    try:
        tool = context.workspace.tools.from_space_view3d_mode(
            context.mode, create=False
        )
        return tool.idname if tool is not None else None
    except Exception:
        return None


def _edit_mesh_poll(context):
    return (
        context.mode == "EDIT_MESH"
        and context.edit_object is not None
        and context.edit_object.type == "MESH"
    )


def _selected_open_boundary_available(context):
    if not _edit_mesh_poll(context):
        return False
    try:
        bm = bmesh.from_edit_mesh(context.edit_object.data)
        ordered_selected_boundary(bm)
    except (FlowPatchGeometryError, RuntimeError, ValueError):
        return False
    return True


def _resolved_projection(settings):
    if settings.projection_mode == "FLATTENED":
        return "FLATTENED", 0.0
    if settings.projection_mode == "SMOOTH":
        return "SMOOTH", settings.surface_follow_strength
    return "RAW", 1.0


def _continuity_kwargs(settings):
    return {
        "max_projection_distance": float(settings.max_projection_distance),
        "max_surface_step": float(settings.max_surface_step),
        "normal_continuity_cos": float(settings.normal_continuity_cos),
    }


def _closest_point_2d(point, start, end):
    point = Vector(point)
    start = Vector(start)
    end = Vector(end)
    direction = end - start
    length_squared = direction.length_squared
    if length_squared <= 1.0e-12:
        return start.copy(), 0.0
    factor = max(
        0.0,
        min(1.0, (point - start).dot(direction) / length_squared),
    )
    return start + direction * factor, factor


def _interpolate_screen_segment(start, end, max_step):
    """Return bounded screen-space steps ending exactly at ``end``."""
    end = Vector(end)
    if start is None:
        return [end.copy()]
    start = Vector(start)
    distance = (end - start).length
    if distance <= 1.0e-8:
        return [end.copy()]
    step = max(1.0, float(max_step))
    count = max(1, int(math.ceil(distance / step)))
    return [
        start.lerp(end, float(index) / float(count))
        for index in range(1, count + 1)
    ]


def _simplify_polyline_indices(points, tolerance):
    """Return RDP-kept indices while preserving both endpoints."""
    values = [Vector(point) for point in points]
    if len(values) <= 2:
        return list(range(len(values)))

    tolerance = max(0.0, float(tolerance))
    keep = {0, len(values) - 1}
    pending = [(0, len(values) - 1)]
    while pending:
        start_index, end_index = pending.pop()
        start = values[start_index]
        end = values[end_index]
        best_index = -1
        best_distance = tolerance
        for index in range(start_index + 1, end_index):
            closest, _factor = _closest_point_2d(
                values[index],
                start,
                end,
            )
            distance = (values[index] - closest).length
            if distance > best_distance:
                best_distance = distance
                best_index = index
        if best_index < 0:
            continue
        keep.add(best_index)
        pending.append((start_index, best_index))
        pending.append((best_index, end_index))
    return sorted(keep)


def _ensure_polyline_support_indices(points, retained_indices, max_spacing):
    """Keep hidden support samples so a clean guide can follow broad depth."""
    values = [Vector(point) for point in points]
    retained = sorted(
        {
            max(0, min(len(values) - 1, int(index)))
            for index in retained_indices
        }
    )
    if len(values) <= 2 or len(retained) < 2:
        return retained

    max_spacing = max(1.0, float(max_spacing))
    supported = set(retained)
    for start_index, end_index in zip(retained, retained[1:]):
        distance_since_support = 0.0
        for index in range(start_index + 1, end_index):
            distance_since_support += (
                values[index] - values[index - 1]
            ).length
            if distance_since_support < max_spacing:
                continue
            supported.add(index)
            distance_since_support = 0.0
    return sorted(supported)


def _smooth_depth_series(values, radius, passes):
    """Low-pass a view-depth series while leaving both anchors untouched."""
    raw = [float(value) for value in values]
    if len(raw) <= 2:
        return raw

    radius = max(1, int(radius))
    output = list(raw)
    for _pass in range(max(1, int(passes))):
        source = list(output)
        for index in range(1, len(source) - 1):
            start = max(0, index - radius)
            end = min(len(source), index + radius + 1)
            weighted_sum = 0.0
            weight_total = 0.0
            for sample_index in range(start, end):
                weight = radius + 1 - abs(sample_index - index)
                weighted_sum += source[sample_index] * weight
                weight_total += weight
            output[index] = weighted_sum / max(weight_total, 1.0)
        output[0] = raw[0]
        output[-1] = raw[-1]
    return output


def _smoothstep_value(edge_zero, edge_one, value):
    if edge_one <= edge_zero:
        return 1.0 if value >= edge_one else 0.0
    factor = max(
        0.0,
        min(1.0, (value - edge_zero) / (edge_one - edge_zero)),
    )
    return factor * factor * (3.0 - 2.0 * factor)


def _filter_broad_form_depths(
    depths,
    projection_mode,
    surface_follow_strength,
    surface_tighten_strength,
    surface_smoothing_radius,
    detail_ignore_threshold,
):
    """Filter narrow surface bumps while retaining broad drawn form."""
    raw = [float(value) for value in depths]
    mode = str(projection_mode).upper()
    if mode in {"RAW", "SURFACE"} or len(raw) <= 2:
        return raw

    radius = max(1, int(surface_smoothing_radius))
    broad = _smooth_depth_series(
        raw,
        radius=radius,
        passes=radius + 1,
    )
    if mode == "FLATTENED":
        return broad

    follow = max(0.0, min(1.0, float(surface_follow_strength)))
    tighten = max(0.0, min(1.0, float(surface_tighten_strength)))
    threshold = max(0.0, float(detail_ignore_threshold))
    maximum_raw_mix = follow * (1.0 - tighten)
    output = list(raw)
    for index in range(1, len(raw) - 1):
        local_detail = abs(raw[index] - broad[index])
        detail_weight = (
            _smoothstep_value(
                threshold,
                threshold * 2.0,
                local_detail,
            )
            if threshold > 0.0
            else 1.0
        )
        output[index] = broad[index] + (
            raw[index] - broad[index]
        ) * maximum_raw_mix * detail_weight
    return output


def _filter_guide_world_by_view(
    points_world,
    points_screen,
    region,
    region_3d,
    projection_mode,
    surface_follow_strength,
    surface_tighten_strength,
    surface_smoothing_radius,
    detail_ignore_threshold,
):
    """Preserve the drawn screen path while filtering its hit depth."""
    world = [Vector(point) for point in points_world]
    if (
        str(projection_mode).upper() in {"RAW", "SURFACE"}
        or len(world) <= 2
    ):
        return [point.copy() for point in world]

    origins = []
    directions = []
    depths = []
    for point_screen, point_world in zip(points_screen, world):
        origin = view3d_utils.region_2d_to_origin_3d(
            region,
            region_3d,
            point_screen,
        )
        direction = view3d_utils.region_2d_to_vector_3d(
            region,
            region_3d,
            point_screen,
        ).normalized()
        depth = (point_world - origin).dot(direction)
        if depth <= 1.0e-8:
            return [point.copy() for point in world]
        origins.append(origin)
        directions.append(direction)
        depths.append(depth)

    filtered_depths = _filter_broad_form_depths(
        depths,
        projection_mode=projection_mode,
        surface_follow_strength=surface_follow_strength,
        surface_tighten_strength=surface_tighten_strength,
        surface_smoothing_radius=surface_smoothing_radius,
        detail_ignore_threshold=detail_ignore_threshold,
    )
    filtered = [
        origin + direction * depth
        for origin, direction, depth in zip(
            origins,
            directions,
            filtered_depths,
        )
    ]
    filtered[0] = world[0].copy()
    filtered[-1] = world[-1].copy()
    return filtered


def _toolbar_item(
    identifier,
    icon,
    tooltip,
    enabled=True,
    active=False,
    priority="SECONDARY",
    disabled_reason="",
):
    return {
        "id": identifier,
        "icon": icon,
        "tooltip": tooltip,
        "enabled": bool(enabled),
        "active": bool(active),
        "priority": str(priority).upper(),
        "disabled_reason": str(disabled_reason),
    }


def _safe_count(value):
    try:
        return len(value or ())
    except TypeError:
        return 0


def _safe_int(value, default=0):
    try:
        return int(value)
    except (TypeError, ValueError):
        return int(default)


def _object_name(value):
    return safe_rna_name(value)


def _identity_from_session(session):
    if session is not None:
        identity = {
            "project_uuid": str(
                safe_rna_attr(session, "_project_uuid", "") or ""
            ),
            "retopo_object_uuid": str(
                safe_rna_attr(session, "_retopo_object_uuid", "") or ""
            ),
            "retopo_object_name_hint": str(
                safe_rna_attr(
                    session,
                    "_retopo_object_name_hint",
                    "",
                )
                or ""
            ),
            "target_object_uuid": str(
                safe_rna_attr(session, "_target_object_uuid", "") or ""
            ),
            "target_object_name_hint": str(
                safe_rna_attr(
                    session,
                    "_target_object_name_hint",
                    "",
                )
                or ""
            ),
        }
        if any(identity.values()):
            return identity
    return {
        "project_uuid": "",
        "retopo_object_uuid": "",
        "retopo_object_name_hint": "",
        "target_object_uuid": "",
        "target_object_name_hint": "",
    }


def _remember_session_identity(session, record, target, retopo):
    global _LAST_SESSION_IDENTITY
    identity = {
        "project_uuid": str(record.project_uuid),
        "retopo_object_uuid": str(record.retopo_object_uuid),
        "retopo_object_name_hint": _object_name(retopo),
        "target_object_uuid": str(record.target_object_uuid),
        "target_object_name_hint": _object_name(target),
    }
    session._project_uuid = identity["project_uuid"]
    session._retopo_object_uuid = identity["retopo_object_uuid"]
    session._retopo_object_name_hint = identity[
        "retopo_object_name_hint"
    ]
    session._target_object_uuid = identity["target_object_uuid"]
    session._target_object_name_hint = identity[
        "target_object_name_hint"
    ]
    _LAST_SESSION_IDENTITY = dict(identity)
    return identity


def _identity_from_context(context):
    settings = safe_rna_attr(
        safe_rna_attr(context, "scene", None),
        "flowpatch_retopo",
        None,
    )
    candidates = (
        safe_rna_attr(context, "edit_object", None),
        safe_rna_attr(context, "active_object", None),
        safe_rna_attr(settings, "continue_on", None),
    )
    for candidate in candidates:
        if safe_rna_attr(candidate, "type", "") != "MESH":
            continue
        try:
            record = read_project_record(candidate)
        except (ProjectStoreError, ReferenceError, RuntimeError):
            continue
        if record is None:
            continue
        return {
            "project_uuid": str(record.project_uuid),
            "retopo_object_uuid": str(record.retopo_object_uuid),
            "retopo_object_name_hint": _object_name(candidate),
            "target_object_uuid": str(record.target_object_uuid),
            "target_object_name_hint": "",
        }
    return {
        "project_uuid": "",
        "retopo_object_uuid": "",
        "retopo_object_name_hint": "",
        "target_object_uuid": "",
        "target_object_name_hint": "",
    }


def _diagnostic_identity(context, session):
    identity = _identity_from_session(session)
    if any(identity.values()):
        return identity
    context_identity = _identity_from_context(context)
    last_identity = dict(_LAST_SESSION_IDENTITY)
    if (
        context_identity["project_uuid"]
        and context_identity["project_uuid"]
        == last_identity["project_uuid"]
    ):
        return last_identity
    if any(context_identity.values()):
        return context_identity
    return last_identity


def _diagnostic_object_sources(context):
    view_layer = safe_rna_attr(context, "view_layer", None)
    view_layer_objects = safe_rna_attr(view_layer, "objects", ())
    blend_data = safe_rna_attr(context, "blend_data", None)
    data_objects = safe_rna_attr(blend_data, "objects", None)
    if data_objects is None:
        data_objects = safe_rna_attr(
            safe_rna_attr(bpy, "data", None),
            "objects",
            (),
        )
    return view_layer_objects, data_objects


def _resolution_warning(label, status):
    if status["ambiguous"]:
        return (
            f"{label} object UUID is ambiguous "
            f"({status['match_count']} matches)"
        )
    if not status["found"]:
        return f"{label} object missing"
    if status["renamed"]:
        return (
            f"{label} object renamed from "
            f"{status['object_name_hint']} to {status['object_name']}"
        )
    if not status["in_view_layer"]:
        return f"{label} object is not linked to the active ViewLayer"
    return ""


def _project_diagnostic_state(context, session):
    identity = _diagnostic_identity(context, session)
    view_layer_objects, data_objects = _diagnostic_object_sources(context)
    retopo, retopo_status = resolve_rna_object(
        identity["retopo_object_uuid"],
        identity["retopo_object_name_hint"],
        view_layer_objects=view_layer_objects,
        data_objects=data_objects,
        uuid_key=OBJECT_UUID_KEY,
    )
    target, target_status = resolve_rna_object(
        identity["target_object_uuid"],
        identity["target_object_name_hint"],
        view_layer_objects=view_layer_objects,
        data_objects=data_objects,
        uuid_key=OBJECT_UUID_KEY,
    )

    warnings = []
    if not identity["retopo_object_uuid"]:
        warnings.append("project identity unavailable")
    else:
        warning = _resolution_warning("project", retopo_status)
        if warning:
            warnings.append(warning)
    if not identity["target_object_uuid"]:
        warnings.append("target identity unavailable")
    else:
        warning = _resolution_warning("target", target_status)
        if warning:
            warnings.append(warning)

    registry_found = False
    registry_matches_identity = False
    registry_error = ""
    if retopo is not None:
        try:
            record = read_project_record(retopo)
            registry_found = record is not None
            if record is not None:
                registry_matches_identity = bool(
                    record.project_uuid == identity["project_uuid"]
                    and record.retopo_object_uuid
                    == identity["retopo_object_uuid"]
                    and record.target_object_uuid
                    == identity["target_object_uuid"]
                )
        except (ProjectStoreError, ReferenceError, RuntimeError) as exc:
            registry_error = f"{type(exc).__name__}: {exc}"
        if not registry_found:
            warnings.append("project registry missing")
        elif not registry_matches_identity:
            warnings.append("project registry does not match session identity")
    if registry_error:
        warnings.append(f"project registry unreadable: {registry_error}")

    return (
        {
            "project_uuid": identity["project_uuid"],
            "project_schema_version": PROJECT_SCHEMA_VERSION,
            "project_object_found": bool(retopo_status["found"]),
            "target_object_found": bool(target_status["found"]),
            "project_registry_found": bool(registry_found),
            "project_registry_matches_identity": bool(
                registry_matches_identity
            ),
            "project_registry_error": registry_error,
            "retopo_object": retopo_status,
            "target_object": target_status,
        },
        warnings,
    )


def _expected_installed_extension_path():
    try:
        root = bpy.utils.user_resource("EXTENSIONS")
    except (AttributeError, ReferenceError, RuntimeError, TypeError):
        return ""
    if not root:
        return ""
    return os.path.abspath(
        os.path.join(root, "user_default", "flowpatch_retopo")
    )


def _build_identity_state():
    active_source_path = os.path.realpath(__file__)
    active_addon_root = os.path.dirname(active_source_path)
    installed_extension_path = _expected_installed_extension_path()
    installed_extension_resolved_path = (
        os.path.realpath(installed_extension_path)
        if installed_extension_path
        else ""
    )
    return {
        "addon_version": ADDON_VERSION_STRING,
        "build_id": BUILD_ID,
        "source_branch": SOURCE_BRANCH,
        "package_sha256": PACKAGE_PAYLOAD_SHA256,
        "package_sha256_scope": PACKAGE_PAYLOAD_HASH_SCOPE,
        "active_source_path": active_source_path,
        "active_addon_root": active_addon_root,
        "installed_extension_path": installed_extension_path,
        "installed_extension_resolved_path": (
            installed_extension_resolved_path
        ),
        "active_source_is_real_install": bool(
            installed_extension_resolved_path
            and os.path.normcase(active_addon_root)
            == os.path.normcase(installed_extension_resolved_path)
        ),
        "project_schema_version": PROJECT_SCHEMA_VERSION,
    }


def _last_exception_snapshot():
    for entry in reversed(SESSION_BREADCRUMBS.snapshot()):
        details = entry.get("details", {})
        error_type = str(details.get("error_type", "") or "")
        message = str(details.get("message", "") or "")
        if error_type or message:
            return {
                "sequence": int(entry.get("sequence", 0)),
                "event": str(entry.get("event", "")),
                "error_type": error_type,
                "message": message,
            }
    return None


def _pen_state_of(owner):
    state = getattr(owner, "_pen_state", None)
    return state if isinstance(state, PenState) else None


def _sync_pen_state_fields(owner):
    state = _pen_state_of(owner)
    if state is None:
        return
    owner._pointer_state = state.phase.value
    owner._drawing = state.drawing
    owner._mouse_captured = state.pointer_captured
    owner._stroke_had_projection_gap = state.projection_gap
    owner._stroke_serial = state.stroke_serial
    owner._finalized_stroke_serial = state.finalized_stroke_serial


def _pen_begin(owner):
    state = _pen_state_of(owner)
    if state is None:
        state = PenState()
        owner._pen_state = state
    transition = state.begin()
    _sync_pen_state_fields(owner)
    return transition


def _pen_projected_sample(owner):
    state = _pen_state_of(owner)
    if state is None:
        owner._pointer_state = _POINTER_DRAWING
        owner._stroke_had_projection_gap = False
        return None
    transition = state.projected_sample()
    _sync_pen_state_fields(owner)
    return transition


def _pen_projection_miss(owner):
    state = _pen_state_of(owner)
    if state is None:
        owner._stroke_had_projection_gap = True
        return None
    transition = state.projection_miss()
    _sync_pen_state_fields(owner)
    return transition


def _pen_claim_finalize(owner):
    state = _pen_state_of(owner)
    if state is None:
        stroke_serial = _safe_int(getattr(owner, "_stroke_serial", 0))
        finalized = _safe_int(
            getattr(owner, "_finalized_stroke_serial", 0)
        )
        if stroke_serial <= 0 or finalized == stroke_serial:
            return False
        owner._finalized_stroke_serial = stroke_serial
        owner._mouse_captured = False
        owner._drawing = False
        owner._pointer_state = _POINTER_FINALIZING
        return True
    transition = state.claim_finalize()
    _sync_pen_state_fields(owner)
    return transition.accepted


def _pen_cancel(owner, reason_code):
    state = _pen_state_of(owner)
    if state is None:
        owner._pointer_state = _POINTER_CANCELLING
        owner._mouse_captured = False
        owner._drawing = False
        return True
    transition = state.cancel(reason_code)
    _sync_pen_state_fields(owner)
    return transition.accepted


def _pen_reset(owner):
    state = _pen_state_of(owner)
    if state is None:
        owner._pointer_state = _POINTER_IDLE
        owner._mouse_captured = False
        owner._drawing = False
        owner._stroke_had_projection_gap = False
        return
    state.reset()
    _sync_pen_state_fields(owner)


def _pen_active(owner):
    state = _pen_state_of(owner)
    if state is not None:
        return state.active
    return bool(
        getattr(owner, "_drawing", False)
        or getattr(owner, "_mouse_captured", False)
    )


def _pen_can_append(owner):
    state = _pen_state_of(owner)
    if state is not None:
        return state.can_append
    return bool(
        getattr(owner, "_drawing", False)
        and getattr(owner, "_mouse_captured", False)
        and getattr(owner, "_pointer_state", _POINTER_IDLE)
        in {_POINTER_DOWN, _POINTER_DRAWING}
    )


def _session_debug_state(
    session,
    context=None,
    project_state=None,
):
    project_state = dict(project_state or {})
    scene = safe_rna_attr(context, "scene", None)
    if session is None:
        return {
            "active": False,
            "project_audit": deepcopy(_LAST_PROJECT_AUDIT),
            "composite": _composite_debug_state(scene),
            "identity": {
                "project_uuid": project_state.get("project_uuid", ""),
                "retopo_object_uuid": project_state.get(
                    "retopo_object",
                    {},
                ).get("object_uuid", ""),
                "target_object_uuid": project_state.get(
                    "target_object",
                    {},
                ).get("object_uuid", ""),
            },
        }
    state = _pen_state_of(session)
    hover_snap = getattr(session, "_hover_snap_state", None)
    built_cells = getattr(session, "_built_cells", None) or {}
    sync_counts = {
        state: sum(
            1
            for record in built_cells.values()
            if str(
                record.get("state", SYNC_FROZEN)
                if isinstance(record, dict)
                else SYNC_FROZEN
            ).upper()
            == state
        )
        for state in (SYNC_PARAMETRIC, SYNC_FROZEN, SYNC_DETACHED)
    }
    sync_reasons = [
        {
            "cycle_key": str(cycle_key),
            "reason_code": str(record.get("sync_reason_code", "")),
            "message": str(record.get("sync_message", "")),
        }
        for cycle_key, record in built_cells.items()
        if isinstance(record, dict)
        and str(record.get("state", "")).upper() == SYNC_FROZEN
    ]
    return {
        "active": bool(
            getattr(session, "_session_alive", False)
            and not getattr(session, "_cleaned", True)
        ),
        "session_epoch": _safe_int(
            safe_rna_attr(session, "_session_epoch", 0)
        ),
        "mode": str(getattr(session, "_mode", "")),
        "pointer_state": str(getattr(session, "_pointer_state", "")),
        "drawing": bool(getattr(session, "_drawing", False)),
        "mouse_captured": bool(getattr(session, "_mouse_captured", False)),
        "cleaned": bool(getattr(session, "_cleaned", True)),
        "stop_requested": bool(getattr(session, "_stop_requested", False)),
        "guide_count": _safe_count(getattr(session, "_guides", None)),
        "preview_count": _safe_count(getattr(session, "_previews", None)),
        "built_cell_count": _safe_count(
            getattr(session, "_built_cells", None)
        ),
        "mesh_sync": {
            "counts": sync_counts,
            "frozen_reasons": sync_reasons,
        },
        "composite": _composite_debug_state(scene),
        "validation_errors": list(
            getattr(session, "_validation_errors", None) or ()
        ),
        "preview_validation_metrics": [
            {
                "cycle_key": str(preview.cycle_key),
                "validation_status": str(preview.validation_status),
                **dict(preview.validation_metrics or {}),
            }
            for preview in tuple(
                getattr(session, "_previews", None) or ()
            )[:8]
        ],
        "pen_state": (
            state.snapshot()
            if state is not None
            else {
                "phase": str(
                    getattr(session, "_pointer_state", _POINTER_IDLE)
                ),
                "pointer_captured": bool(
                    getattr(session, "_mouse_captured", False)
                ),
            }
        ),
        "hover_snap": (
            hover_snap.snapshot()
            if hover_snap is not None
            else {
                "kind": "",
                "distance_px": 0.0,
                "generation": 0,
                "clear_reason": "",
            }
        ),
        "transform": {
            "mode": str(getattr(session, "_transform_mode", "")),
            "temporary_selection": bool(
                getattr(session, "_transform_temporary_selection", False)
            ),
            "ref_count": _safe_count(
                getattr(session, "_transform_refs", None)
            ),
            "return_mode": str(
                getattr(session, "_transform_return_mode", "")
            ),
        },
        "project_audit": deepcopy(_LAST_PROJECT_AUDIT),
        "identity": _identity_from_session(session),
        "retopo_object": project_state.get("retopo_object", {}).get(
            "object_name",
            "",
        ),
        "target_object": project_state.get("target_object", {}).get(
            "object_name",
            "",
        ),
    }


def _clear_hover_snap(owner, reason_code=""):
    state = getattr(owner, "_hover_snap_state", None)
    if state is not None:
        state.clear(reason_code)
    owner._hover_segment = None
    renderer = getattr(owner, "_renderer", None)
    if renderer is not None:
        renderer.hover_segments = []
        renderer.snap_points = []
        renderer.snap_kind = ""
    try:
        owner._tag_redraw()
    except (AttributeError, RuntimeError):
        pass


def build_debug_state_snapshot(context=None):
    context = context or getattr(bpy, "context", None)
    session = FLOWPATCH_OT_guide_session._active_instance
    project_state, warnings = _project_diagnostic_state(context, session)
    capabilities = None
    if session is not None:
        try:
            capabilities = session._toolbar_capabilities(context)
        except Exception as exc:
            warnings.append(
                "tool capability snapshot unavailable: "
                f"{type(exc).__name__}: {exc}"
            )
            SESSION_BREADCRUMBS.record(
                "debug_capability_snapshot_failed",
                error_type=type(exc).__name__,
                message=str(exc),
            )
    context_state = {
        "mode": str(safe_rna_attr(context, "mode", "") or ""),
        "active_object": _object_name(
            safe_rna_attr(context, "active_object", None)
        ),
        "edit_object": _object_name(
            safe_rna_attr(context, "edit_object", None)
        ),
        "area_type": str(
            safe_rna_attr(
                safe_rna_attr(context, "area", None),
                "type",
                "",
            )
            or ""
        ),
    }
    return build_debug_document(
        session_state=_session_debug_state(
            session,
            context=context,
            project_state=project_state,
        ),
        capabilities=capabilities,
        context_state=context_state,
        breadcrumbs=SESSION_BREADCRUMBS,
        build_identity=_build_identity_state(),
        project_state=project_state,
        warnings=warnings,
        last_exception=_last_exception_snapshot(),
    )


def _run_project_audit(source):
    global _LAST_PROJECT_AUDIT
    try:
        issues = tuple(audit_projects(tuple(bpy.data.objects)))
        scene = getattr(bpy.context, "scene", None)
        if scene is not None:
            issues += tuple(
                audit_composite_session(scene, tuple(bpy.data.objects))
            )
        _LAST_PROJECT_AUDIT = audit_snapshot(issues)
    except Exception as exc:
        issues = ()
        _LAST_PROJECT_AUDIT = {
            "issue_count": 1,
            "issues": [
                {
                    "reason_code": "PROJECT_AUDIT_FAILED",
                    "object_name": "",
                    "project_uuid": "",
                    "message": str(exc),
                }
            ],
            "truncated": False,
        }
    SESSION_BREADCRUMBS.record(
        "project_audit",
        source=str(source),
        issue_count=int(_LAST_PROJECT_AUDIT["issue_count"]),
    )
    return issues


@persistent
def _project_load_post(_unused):
    _run_project_audit("LOAD_POST")


def _project_target_for_object(owner, objects=None):
    if owner is None:
        return None
    try:
        record = read_project_record(owner)
    except ProjectStoreError:
        return None
    if record is None:
        return None
    try:
        return find_object_by_uuid(
            tuple(objects or bpy.data.objects),
            record.target_object_uuid,
        )
    except ProjectStoreError:
        return None


def _legacy_project_candidates(target):
    return tuple(
        sorted(
            (
                obj
                for obj in bpy.data.objects
                if obj is not target
                and obj.type == "MESH"
                and not obj.get(PROJECT_RECORD_KEY, "")
                and str(obj.get("flowpatch_target_name", ""))
                == str(target.name)
            ),
            key=lambda item: item.name,
        )
    )


def _activate_project_object(retopo, target):
    retopo.show_in_front = True
    retopo.hide_viewport = False
    retopo.hide_set(False)
    retopo["flowpatch_target_name"] = target.name
    retopo["flowpatch_session_active"] = False
    return retopo


def _bind_project_or_error(target, retopo):
    try:
        return bind_project(target, retopo)
    except ProjectStoreError as exc:
        raise FlowPatchGeometryError(str(exc)) from exc


_SESSION_START_OWNER_KEYS = (
    OBJECT_UUID_KEY,
    PROJECT_UUID_KEY,
    PROJECT_RECORD_KEY,
    "flowpatch_target_name",
    "flowpatch_session_active",
)

_SESSION_START_SETTING_KEYS = (
    "target",
    "continue_on",
    "session_active",
    "session_tool",
    "session_status",
    "session_validation",
    "session_guide_count",
    "session_cell_count",
    "session_active_cell",
    "session_sync_state",
    "session_sync_message",
)


class SessionStartTransactionError(FlowPatchGeometryError):
    def __init__(self, reason_code, step, message):
        super().__init__(message)
        self.reason_code = str(reason_code)
        self.step = str(step)


def _object_in_active_view_layer(context, obj):
    return (
        obj is not None
        and context.view_layer.objects.get(obj.name) is obj
    )


def _active_view_layer_collection(context):
    layer_collection = getattr(
        context.view_layer,
        "active_layer_collection",
        None,
    )
    collection = getattr(layer_collection, "collection", None)
    if collection is None:
        collection = getattr(context, "collection", None)
    if collection is None:
        collection = context.scene.collection
    return collection


def _ensure_session_object_in_view_layer(context, retopo):
    if _object_in_active_view_layer(context, retopo):
        return None

    collection = _active_view_layer_collection(context)
    added_link = False
    try:
        if collection.objects.get(retopo.name) is not retopo:
            collection.objects.link(retopo)
            added_link = True
        context.view_layer.update()
        if not _object_in_active_view_layer(context, retopo):
            raise SessionStartTransactionError(
                "VIEW_LAYER_MEMBERSHIP_FAILED",
                "verify_view_layer",
                (
                    f"Object '{retopo.name}' could not be linked to active "
                    f"ViewLayer '{context.view_layer.name}'."
                ),
            )
    except Exception:
        if added_link and collection.objects.get(retopo.name) is retopo:
            collection.objects.unlink(retopo)
            context.view_layer.update()
        raise
    return collection if added_link else None


def _snapshot_owner_properties(owner):
    return {
        key: (key in owner, deepcopy(owner.get(key)))
        for key in _SESSION_START_OWNER_KEYS
    }


def _restore_owner_properties(owner, snapshot):
    for key, (existed, value) in snapshot.items():
        if existed:
            owner[key] = deepcopy(value)
        elif key in owner:
            del owner[key]


class _SessionStartTransaction:
    def __init__(self, context, target, continue_on):
        self.context = context
        self.settings = context.scene.flowpatch_retopo
        self.start_mode = str(context.mode)
        self.start_active = context.view_layer.objects.active
        self.start_selected = tuple(context.selected_objects)
        self.setting_snapshot = {
            key: getattr(self.settings, key)
            for key in _SESSION_START_SETTING_KEYS
        }
        self.owner_snapshots = []
        self.visibility_snapshot = None
        self.added_links = []
        self.created_object = None
        self.created_mesh = None
        self.initial_objects = tuple(bpy.data.objects)
        self.initial_meshes = tuple(bpy.data.meshes)
        for owner in self.initial_objects:
            if (
                owner is target
                or owner is continue_on
                or any(key in owner for key in _SESSION_START_OWNER_KEYS)
            ):
                self.capture_owner(owner)

    def capture_owner(self, owner):
        if owner is None or any(
            existing is owner for existing, _snapshot in self.owner_snapshots
        ):
            return
        self.owner_snapshots.append(
            (owner, _snapshot_owner_properties(owner))
        )

    def capture_retopo(self, retopo):
        if not any(existing is retopo for existing in self.initial_objects):
            self.created_object = retopo
            mesh = getattr(retopo, "data", None)
            if mesh is not None and not any(
                existing is mesh for existing in self.initial_meshes
            ):
                self.created_mesh = mesh
            return
        self.capture_owner(retopo)
        hidden = None
        if _object_in_active_view_layer(self.context, retopo):
            hidden = retopo.hide_get(view_layer=self.context.view_layer)
        self.visibility_snapshot = (
            retopo,
            bool(retopo.show_in_front),
            bool(retopo.hide_viewport),
            hidden,
        )

    def record_link(self, collection, retopo):
        if collection is not None:
            self.added_links.append((collection, retopo))

    def rollback(self):
        errors = []
        context = self.context
        session_class = globals().get("FLOWPATCH_OT_guide_session")
        session = (
            getattr(session_class, "_active_instance", None)
            if session_class is not None
            else None
        )
        if session is not None:
            try:
                session._cleanup(context)
            except Exception as exc:
                errors.append(f"cleanup_modal_session: {exc}")
        if context.mode == "EDIT_MESH":
            try:
                bpy.ops.object.mode_set(mode="OBJECT")
            except Exception as exc:
                errors.append(f"leave_edit_mode: {exc}")

        for owner, snapshot in self.owner_snapshots:
            if bpy.data.objects.get(owner.name) is owner:
                try:
                    _restore_owner_properties(owner, snapshot)
                except Exception as exc:
                    errors.append(f"restore_owner:{owner.name}: {exc}")

        if self.visibility_snapshot is not None:
            retopo, show_in_front, hide_viewport, hidden = self.visibility_snapshot
            if bpy.data.objects.get(retopo.name) is retopo:
                try:
                    retopo.show_in_front = show_in_front
                    retopo.hide_viewport = hide_viewport
                    if hidden is not None and _object_in_active_view_layer(
                        context,
                        retopo,
                    ):
                        retopo.hide_set(
                            hidden,
                            view_layer=context.view_layer,
                        )
                except Exception as exc:
                    errors.append(f"restore_visibility:{retopo.name}: {exc}")

        for collection, retopo in reversed(self.added_links):
            if collection.objects.get(retopo.name) is retopo:
                try:
                    collection.objects.unlink(retopo)
                except Exception as exc:
                    errors.append(f"unlink:{retopo.name}: {exc}")

        if (
            self.created_object is not None
            and bpy.data.objects.get(self.created_object.name)
            is self.created_object
        ):
            try:
                bpy.data.objects.remove(self.created_object, do_unlink=True)
            except Exception as exc:
                errors.append(f"remove_object: {exc}")
        if self.created_mesh is not None and self.created_mesh.users == 0:
            try:
                bpy.data.meshes.remove(self.created_mesh)
            except Exception as exc:
                errors.append(f"remove_mesh: {exc}")

        for key, value in self.setting_snapshot.items():
            try:
                setattr(self.settings, key, value)
            except Exception as exc:
                errors.append(f"restore_setting:{key}: {exc}")

        try:
            context.view_layer.update()
        except Exception as exc:
            errors.append(f"update_view_layer: {exc}")

        try:
            for obj in tuple(context.selected_objects):
                obj.select_set(False, view_layer=context.view_layer)
            for obj in self.start_selected:
                if _object_in_active_view_layer(context, obj):
                    obj.select_set(True, view_layer=context.view_layer)
            if _object_in_active_view_layer(context, self.start_active):
                context.view_layer.objects.active = self.start_active
            else:
                context.view_layer.objects.active = None
            if (
                self.start_mode == "EDIT_MESH"
                and _object_in_active_view_layer(context, self.start_active)
            ):
                self.start_active.select_set(
                    True,
                    view_layer=context.view_layer,
                )
                bpy.ops.object.mode_set(mode="EDIT")
        except Exception as exc:
            errors.append(f"restore_context: {exc}")
        return tuple(errors)


def create_session_object(context, target):
    if target is None or target.type != "MESH":
        raise FlowPatchGeometryError("Select the high-poly mesh first.")
    if len(target.data.polygons) == 0:
        raise FlowPatchGeometryError("The projection surface has no faces.")

    mesh = bpy.data.meshes.new(f"{target.name}_FlowPatchMesh")
    retopo = bpy.data.objects.new(f"{target.name}_FlowPatch", mesh)
    retopo.matrix_world = target.matrix_world.copy()
    retopo.show_in_front = False
    try:
        _ensure_session_object_in_view_layer(context, retopo)
        _bind_project_or_error(target, retopo)
    except Exception:
        bpy.data.objects.remove(retopo, do_unlink=True)
        if mesh.users == 0:
            bpy.data.meshes.remove(mesh)
        raise
    return retopo


def validate_session_inputs(target, continue_on=None):
    if target is None or target.type != "MESH":
        raise FlowPatchGeometryError("Select the high-poly mesh first.")
    if len(target.data.polygons) == 0:
        raise FlowPatchGeometryError("The projection surface has no faces.")
    if continue_on is None:
        return
    if continue_on.type != "MESH":
        raise FlowPatchGeometryError("Continue On must be an editable mesh object.")
    if continue_on is target:
        raise FlowPatchGeometryError(
            "The retopo mesh and projection Surface must be different objects."
        )


def prepare_session_object(context, target, continue_on=None):
    validate_session_inputs(target, continue_on)
    _run_project_audit("SESSION_START")
    if continue_on is not None:
        _bind_project_or_error(target, continue_on)
        return continue_on

    try:
        target_uuid = ensure_object_uuid(target)
        candidates = tuple(
            obj
            for obj in project_objects_for_target(
                tuple(bpy.data.objects),
                target_uuid,
            )
            if obj is not target and obj.type == "MESH"
        )
    except ProjectStoreError as exc:
        raise FlowPatchGeometryError(str(exc)) from exc

    if len(candidates) > 1:
        names = ", ".join(obj.name for obj in candidates[:4])
        raise FlowPatchGeometryError(
            (
                "More than one FlowPatch project targets this surface "
                f"({names}). Choose the intended mesh in Continue On."
            )
        )
    if candidates:
        _bind_project_or_error(target, candidates[0])
        return candidates[0]

    legacy = _legacy_project_candidates(target)
    if len(legacy) > 1:
        names = ", ".join(obj.name for obj in legacy[:4])
        raise FlowPatchGeometryError(
            (
                "Multiple legacy FlowPatch meshes match this target "
                f"({names}). Choose one in Continue On before recovery."
            )
        )
    if legacy:
        _bind_project_or_error(target, legacy[0])
        return legacy[0]

    return create_session_object(context, target)


def _legacy_target_for_object(owner):
    if owner is None:
        return None
    target = bpy.data.objects.get(
        str(owner.get("flowpatch_target_name", ""))
    )
    if (
        target is None
        or target.type != "MESH"
        or len(target.data.polygons) == 0
        or target is owner
    ):
        return None
    return target


def _start_target(settings, active, surface_name=""):
    continue_on = settings.continue_on
    if continue_on is not None:
        project_target = _project_target_for_object(continue_on)
        if project_target is not None:
            return project_target
        legacy_target = _legacy_target_for_object(continue_on)
        if legacy_target is not None:
            return legacy_target

    named = bpy.data.objects.get(surface_name) if surface_name else None
    for candidate in (named, settings.target, active):
        if candidate is None:
            continue
        project_target = _project_target_for_object(candidate)
        if project_target is not None:
            settings.continue_on = candidate
            return project_target
        if (
            candidate.type == "MESH"
            and len(candidate.data.polygons) > 0
            and candidate is not continue_on
        ):
            return candidate
    return None


def _resolve_session_target(context, retopo):
    settings = context.scene.flowpatch_retopo
    try:
        record = read_project_record(retopo)
    except ProjectStoreError as exc:
        SESSION_BREADCRUMBS.record(
            "project_resume_rejected",
            reason_code=exc.reason_code,
            message=str(exc),
        )
        raise
    if record is not None:
        try:
            target = find_object_by_uuid(
                tuple(bpy.data.objects),
                record.target_object_uuid,
            )
        except ProjectStoreError as exc:
            configured = settings.target
            if (
                exc.reason_code == "DUPLICATE_OBJECT_UUID"
                and configured is not None
                and configured is not retopo
            ):
                try:
                    repaired = repair_passive_target_uuid_copies(
                        tuple(bpy.data.objects),
                        configured,
                        retopo,
                    )
                    target = find_object_by_uuid(
                        tuple(bpy.data.objects),
                        record.target_object_uuid,
                    )
                except ProjectStoreError as repair_exc:
                    SESSION_BREADCRUMBS.record(
                        "project_resume_rejected",
                        reason_code=repair_exc.reason_code,
                        message=str(repair_exc),
                    )
                    raise
                SESSION_BREADCRUMBS.record(
                    "target_uuid_copies_repaired",
                    target=target.name,
                    copies=tuple(repaired),
                )
            else:
                SESSION_BREADCRUMBS.record(
                    "project_resume_rejected",
                    reason_code=exc.reason_code,
                    message=str(exc),
                )
                raise
        if target is None:
            SESSION_BREADCRUMBS.record(
                "project_resume_rejected",
                reason_code="MISSING_TARGET_OBJECT",
                target_uuid=record.target_object_uuid,
            )
            return None
        settings.target = target
        return target

    target = settings.target
    if target is None:
        target_name = retopo.get("flowpatch_target_name", "")
        target = bpy.data.objects.get(target_name)
    if target is not None and target is not retopo:
        settings.target = target
    return target


def _select_session_edit_members(context, retopo):
    if not _object_in_active_view_layer(context, retopo):
        raise SessionStartTransactionError(
            "VIEW_LAYER_MEMBERSHIP_LOST",
            "select_retopo",
            (
                f"Object '{retopo.name}' is not in active ViewLayer "
                f"'{context.view_layer.name}'."
            ),
        )

    for obj in tuple(context.selected_objects):
        obj.select_set(False, view_layer=context.view_layer)
    composite_members = _composite_objects(context.scene)
    edit_members = (
        composite_members
        if retopo in composite_members
        else (retopo,)
    )
    selected_member_count = 0
    for obj in edit_members:
        if not _object_in_active_view_layer(context, obj):
            continue
        obj.select_set(True, view_layer=context.view_layer)
        selected_member_count += 1

    if not retopo.select_get(view_layer=context.view_layer):
        retopo.select_set(True, view_layer=context.view_layer)
    context.view_layer.objects.active = retopo
    if (
        context.view_layer.objects.active is not retopo
        or not retopo.select_get(view_layer=context.view_layer)
    ):
        raise SessionStartTransactionError(
            "VIEW_LAYER_ACTIVATION_FAILED",
            "activate_retopo",
            f"Object '{retopo.name}' could not become active and selected.",
        )
    return selected_member_count


class FLOWPATCH_OT_start_session(Operator):
    bl_idname = "flowpatch.start_session"
    bl_label = "Start New FlowPatch Retopo"
    bl_description = (
        "Use the active high-poly mesh as the projection surface and create "
        "a separate empty retopology mesh"
    )
    bl_options = {"REGISTER", "UNDO"}

    surface_name: StringProperty(options={"HIDDEN"})
    launch_modal: BoolProperty(default=True, options={"HIDDEN"})

    @classmethod
    def poll(cls, context):
        active = context.active_object
        settings = getattr(context.scene, "flowpatch_retopo", None)
        configured = settings.target if settings is not None else None
        return (
            context.area is not None
            and context.area.type == "VIEW_3D"
            and context.mode in {"OBJECT", "EDIT_MESH"}
            and (
                (
                    active is not None
                    and active.type == "MESH"
                    and (
                        len(active.data.polygons) > 0
                        or bool(active.get(PROJECT_RECORD_KEY, ""))
                        or _legacy_target_for_object(active) is not None
                    )
                )
                or (
                    configured is not None
                    and configured.type == "MESH"
                    and len(configured.data.polygons) > 0
                )
            )
        )

    def execute(self, context):
        settings = context.scene.flowpatch_retopo
        transaction = _SessionStartTransaction(
            context,
            None,
            settings.continue_on,
        )
        step = "resolve_target"
        start_mode = context.mode
        try:
            active_candidate = (
                context.edit_object
                if start_mode == "EDIT_MESH"
                else context.active_object
            )
            target = _start_target(
                settings,
                context.active_object,
                self.surface_name,
            )
            continue_on = settings.continue_on
            if (
                continue_on is None
                and active_candidate is not None
                and active_candidate.type == "MESH"
                and active_candidate is not target
                and settings.target is target
            ):
                continue_on = active_candidate
            transaction.capture_owner(target)
            transaction.capture_owner(continue_on)
            attached_plain_mesh = bool(
                continue_on is not None
                and not continue_on.get(PROJECT_RECORD_KEY, "")
            )

            step = "validate_inputs"
            validate_session_inputs(target, continue_on)
            if context.mode == "EDIT_MESH":
                step = "leave_edit_mode"
                result = bpy.ops.object.mode_set(mode="OBJECT")
                if "FINISHED" not in result or context.mode != "OBJECT":
                    raise SessionStartTransactionError(
                        "OBJECT_MODE_REQUIRED",
                        step,
                        "FlowPatch could not leave Edit Mode before startup.",
                    )

            step = "prepare_project"
            retopo = prepare_session_object(
                context,
                target,
                continue_on=continue_on,
            )
            transaction.capture_retopo(retopo)

            step = "link_view_layer"
            added_collection = _ensure_session_object_in_view_layer(
                context,
                retopo,
            )
            transaction.record_link(added_collection, retopo)

            step = "activate_project"
            _activate_project_object(retopo, target)

            settings.target = target
            settings.continue_on = retopo
            step = "select_retopo"
            selected_member_count = _select_session_edit_members(
                context,
                retopo,
            )
            step = "enter_edit_mode"
            result = bpy.ops.object.mode_set(mode="EDIT")
            if (
                "FINISHED" not in result
                or context.mode != "EDIT_MESH"
                or context.edit_object is not retopo
            ):
                raise SessionStartTransactionError(
                    "EDIT_MODE_START_FAILED",
                    step,
                    f"Object '{retopo.name}' could not enter Edit Mode.",
                )
            if bool(getattr(self, "launch_modal", True)):
                step = "start_guide_session"
                try:
                    modal_result = bpy.ops.flowpatch.guide_session(
                        "INVOKE_DEFAULT"
                    )
                except RuntimeError as modal_exc:
                    modal_message = str(modal_exc).strip()
                    if modal_message.startswith("Error: "):
                        modal_message = modal_message[7:].strip()
                    raise SessionStartTransactionError(
                        "GUIDE_SESSION_START_FAILED",
                        step,
                        modal_message or "FlowPatch guide session could not start.",
                    ) from modal_exc
                active_session = FLOWPATCH_OT_guide_session._active_instance
                if (
                    "RUNNING_MODAL" not in modal_result
                    or active_session is None
                    or active_session._retopo is not retopo
                    or not settings.session_active
                    or not bool(retopo.get("flowpatch_session_active", False))
                ):
                    raise SessionStartTransactionError(
                        "GUIDE_SESSION_NOT_ACTIVE",
                        step,
                        "FlowPatch guide session did not become active.",
                    )
        except Exception as exc:
            rollback_errors = transaction.rollback()
            if isinstance(exc, SessionStartTransactionError):
                reason_code = exc.reason_code
                failed_step = exc.step
                message = str(exc)
            elif isinstance(exc, FlowPatchGeometryError):
                reason_code = "SESSION_START_REJECTED"
                failed_step = step
                message = str(exc)
            else:
                reason_code = "SESSION_START_EXCEPTION"
                failed_step = step
                message = f"FlowPatch could not start during {step}: {exc}"
            SESSION_BREADCRUMBS.record(
                "session_start_transaction_failed",
                reason_code=reason_code,
                step=failed_step,
                message=message,
                rollback_errors=rollback_errors,
            )
            self.report({"ERROR"}, message)
            return {"CANCELLED"}

        SESSION_BREADCRUMBS.record(
            "session_start_transaction_committed",
            retopo_object=retopo.name,
            target_object=target.name,
            view_layer=context.view_layer.name,
        )
        self.report(
            {"INFO"},
            (
                f"FlowPatch project {retopo.get(PROJECT_UUID_KEY, '')[:8]} "
                f"{'attached to the same mesh' if attached_plain_mesh else 'opened'} "
                f"on {target.name}; {selected_member_count} composite "
                "project object(s) remain in the editing context."
            ),
        )
        return {"FINISHED"}


def _stop_active_session(context, *, leave_edit_mode):
    """Finalize the owned modal session without relying on another operator poll."""
    session_class = globals().get("FLOWPATCH_OT_guide_session")
    session = (
        getattr(session_class, "_active_instance", None)
        if session_class is not None
        else None
    )
    if session is None:
        return False, False

    retopo = safe_rna_attr(session, "_retopo", None)
    SESSION_BREADCRUMBS.record(
        "session_stop_requested",
        session_epoch=_safe_int(getattr(session, "_session_epoch", 0)),
        context_mode=str(getattr(context, "mode", "")),
        retopo_object=_object_name(retopo),
    )
    session._finalize_session(context, commit_pending=False)

    left_edit_mode = False
    if (
        leave_edit_mode
        and _edit_mesh_poll(context)
        and context.edit_object is retopo
    ):
        result = bpy.ops.object.mode_set(mode="OBJECT")
        left_edit_mode = (
            "FINISHED" in result
            and str(getattr(context, "mode", "")) == "OBJECT"
        )
    if retopo is not None:
        try:
            retopo["flowpatch_session_active"] = False
        except Exception:
            pass
    return True, left_edit_mode


class FLOWPATCH_OT_stop_session(Operator):
    bl_idname = "flowpatch.stop_session"
    bl_label = "Stop FlowPatch Session"
    bl_description = (
        "Leave Edit Mode and preserve all committed retopology geometry"
    )
    bl_options = {"REGISTER"}

    @classmethod
    def poll(cls, context):
        session_class = globals().get("FLOWPATCH_OT_guide_session")
        return bool(
            session_class is not None
            and getattr(session_class, "_active_instance", None) is not None
        )

    def execute(self, context):
        try:
            stopped, left_edit_mode = _stop_active_session(
                context,
                leave_edit_mode=True,
            )
        except Exception as exc:
            SESSION_BREADCRUMBS.record(
                "session_stop_failed",
                error_type=type(exc).__name__,
                message=str(exc),
            )
            self.report({"ERROR"}, f"FlowPatch could not stop safely: {exc}")
            return {"CANCELLED"}
        if not stopped:
            self.report({"WARNING"}, "No active FlowPatch session is available to stop.")
            return {"CANCELLED"}
        if not left_edit_mode and _edit_mesh_poll(context):
            self.report(
                {"INFO"},
                "FlowPatch session stopped; current Edit Mode was left unchanged.",
            )
            return {"FINISHED"}
        self.report({"INFO"}, "FlowPatch session stopped; geometry was preserved.")
        return {"FINISHED"}


_PROJECT_OBJECT_DATA_KEYS = (
    GUIDE_DATA_KEY,
    BUILT_CELL_DATA_KEY,
    EXISTING_MESH_DATA_KEY,
    BOUNDARY_REGISTRY_KEY,
    NODE_VERTEX_REGISTRY_KEY,
    VERTEX_UID_COUNTER_KEY,
    "flowpatch_target_name",
    "flowpatch_session_active",
)


def _remove_project_from_composite(scene, project_uuid):
    record = read_composite_session(scene)
    if record is None:
        return False
    updated = without_composite_project(record, project_uuid)
    if updated is None:
        clear_composite_session(scene)
    elif updated != record:
        write_composite_session(scene, updated, allow_replace=True)
    else:
        return False
    return True

_PROJECT_MESH_ATTRIBUTE_NAMES = (
    VERTEX_UID_LAYER,
    VERTEX_LAYER_NAME,
    EDGE_LAYER_NAME,
    EDGE_ROLE_LAYER_NAME,
    FACE_LAYER_NAME,
)


def _context_project_object(context):
    session_class = globals().get("FLOWPATCH_OT_guide_session")
    session = (
        session_class._active_instance
        if session_class is not None
        else None
    )
    scene = safe_rna_attr(context, "scene", None)
    settings = safe_rna_attr(scene, "flowpatch_retopo", None)
    candidates = (
        safe_rna_attr(session, "_retopo", None),
        safe_rna_attr(context, "edit_object", None),
        safe_rna_attr(context, "active_object", None),
        safe_rna_attr(settings, "continue_on", None),
    )
    for obj in candidates:
        if safe_rna_attr(obj, "type", "") != "MESH":
            continue
        if (
            safe_rna_get(obj, PROJECT_RECORD_KEY, "")
            or safe_rna_get(obj, PROJECT_UUID_KEY, "")
            or safe_rna_get(obj, GUIDE_DATA_KEY, "")
            or safe_rna_get(obj, "flowpatch_target_name", "")
        ):
            return obj

    active = safe_rna_attr(context, "active_object", None)
    if safe_rna_attr(active, "type", "") != "MESH":
        return None
    target_uuid = safe_rna_get(active, OBJECT_UUID_KEY, "")
    if not target_uuid:
        return None
    try:
        matches = project_objects_for_target(
            tuple(bpy.data.objects),
            target_uuid,
        )
    except ProjectStoreError:
        return None
    return matches[0] if len(matches) == 1 else None


def _purge_project_object(retopo):
    removed_properties = list(clear_project_identity(retopo))
    for key in _PROJECT_OBJECT_DATA_KEYS:
        if key in retopo:
            del retopo[key]
            removed_properties.append(key)

    removed_attributes = []
    mesh = getattr(retopo, "data", None)
    attributes = getattr(mesh, "attributes", None)
    if attributes is not None:
        for name in _PROJECT_MESH_ATTRIBUTE_NAMES:
            attribute = attributes.get(name)
            if attribute is None:
                continue
            attributes.remove(attribute)
            removed_attributes.append(name)

    modifier = retopo.modifiers.get("FlowPatch Mirror")
    if modifier is not None and modifier.type == "MIRROR":
        retopo.modifiers.remove(modifier)
    retopo.show_in_front = False
    return {
        "properties": tuple(sorted(set(removed_properties))),
        "attributes": tuple(sorted(removed_attributes)),
        "mirror_removed": modifier is not None,
    }


class FLOWPATCH_OT_recover_project(Operator):
    bl_idname = "flowpatch.recover_project"
    bl_label = "Recover FlowPatch Project"
    bl_description = (
        "Attach a new stable project UUID to unambiguous legacy/orphan "
        "FlowPatch metadata while preserving guides and mesh"
    )
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return _context_project_object(context) is not None

    def execute(self, context):
        retopo = _context_project_object(context)
        if retopo is None:
            self.report({"ERROR"}, "No FlowPatch project mesh is available.")
            return {"CANCELLED"}

        target = context.scene.flowpatch_retopo.target
        if (
            target is None
            or target.type != "MESH"
            or target is retopo
            or len(target.data.polygons) == 0
        ):
            target = _legacy_target_for_object(retopo)
        if target is None:
            self.report(
                {"ERROR"},
                "Choose the original projection Surface before recovery.",
            )
            return {"CANCELLED"}

        try:
            existing_record = read_project_record(retopo)
        except ProjectStoreError:
            existing_record = None
        if existing_record is not None:
            try:
                repaired = repair_passive_target_uuid_copies(
                    tuple(bpy.data.objects),
                    target,
                    retopo,
                )
                existing_target = find_object_by_uuid(
                    tuple(bpy.data.objects),
                    existing_record.target_object_uuid,
                )
            except ProjectStoreError as exc:
                self.report({"ERROR"}, str(exc))
                return {"CANCELLED"}
            if existing_target is target:
                settings = context.scene.flowpatch_retopo
                settings.target = target
                settings.continue_on = retopo
                _run_project_audit("EXPLICIT_RECOVER")
                if repaired:
                    names = ", ".join(repaired)
                    self.report(
                        {"INFO"},
                        (
                            "Recovered copied Surface identity from "
                            f"{names}; project metadata was preserved."
                        ),
                    )
                else:
                    self.report(
                        {"INFO"},
                        (
                            f"Project {existing_record.project_uuid[:8]} is "
                            "already valid; no metadata changed."
                        ),
                    )
                return {"FINISHED"}

        existing_target = _project_target_for_object(retopo)
        if existing_target is not None:
            self.report(
                {"INFO"},
                (
                    f"Project {retopo.get(PROJECT_UUID_KEY, '')[:8]} is "
                    "already valid; no metadata changed."
                ),
            )
            return {"FINISHED"}

        before = {
            key: retopo.get(key)
            for key in (PROJECT_UUID_KEY, PROJECT_RECORD_KEY, OBJECT_UUID_KEY)
            if key in retopo
        }
        target_had_uuid = OBJECT_UUID_KEY in target
        target_uuid_before = target.get(OBJECT_UUID_KEY, "")
        try:
            clear_project_identity(retopo, clear_object_uuid=True)
            record = bind_project(target, retopo)
        except ProjectStoreError as exc:
            clear_project_identity(retopo, clear_object_uuid=True)
            for key, value in before.items():
                retopo[key] = value
            if target_had_uuid:
                target[OBJECT_UUID_KEY] = target_uuid_before
            elif OBJECT_UUID_KEY in target:
                del target[OBJECT_UUID_KEY]
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}

        retopo["flowpatch_target_name"] = target.name
        retopo["flowpatch_session_active"] = False
        settings = context.scene.flowpatch_retopo
        settings.target = target
        settings.continue_on = retopo
        _run_project_audit("EXPLICIT_RECOVER")
        self.report(
            {"INFO"},
            (
                f"Recovered FlowPatch project {record.project_uuid[:8]}; "
                "guides and mesh were preserved."
            ),
        )
        return {"FINISHED"}


class FLOWPATCH_OT_delete_project(Operator):
    bl_idname = "flowpatch.delete_project"
    bl_label = "Delete FlowPatch Project"
    bl_description = (
        "Remove FlowPatch guides, registries, ownership, overlays, and "
        "project links while keeping ordinary mesh geometry"
    )
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return _context_project_object(context) is not None

    def invoke(self, context, _event):
        return context.window_manager.invoke_confirm(self, _event)

    def execute(self, context):
        retopo = _context_project_object(context)
        if retopo is None:
            self.report({"ERROR"}, "No FlowPatch project mesh is available.")
            return {"CANCELLED"}
        try:
            project_record = read_project_record(retopo)
            if project_record is not None:
                _remove_project_from_composite(
                    context.scene,
                    project_record.project_uuid,
                )
        except ProjectStoreError as exc:
            self.report(
                {"ERROR"},
                f"Project delete stopped before mutation: {exc}",
            )
            return {"CANCELLED"}

        session = FLOWPATCH_OT_guide_session._active_instance
        if session is not None and session._retopo is retopo:
            session._finalize_session(context, commit_pending=False)
        if (
            context.mode == "EDIT_MESH"
            and context.edit_object is retopo
        ):
            bpy.ops.object.mode_set(mode="OBJECT")

        vertex_count = len(retopo.data.vertices)
        edge_count = len(retopo.data.edges)
        face_count = len(retopo.data.polygons)
        result = _purge_project_object(retopo)

        settings = context.scene.flowpatch_retopo
        if settings.continue_on is retopo:
            settings.continue_on = None
        settings.session_active = False
        settings.session_tool = ""
        settings.session_status = "Idle"
        settings.session_validation = ""
        settings.session_guide_count = 0
        settings.session_cell_count = 0
        settings.session_active_cell = 0
        settings.session_sync_state = ""
        settings.session_sync_message = ""
        composite_state = _composite_debug_state(context.scene)
        settings.session_composite_state = (
            f"{composite_state['member_count']} Projects"
            if composite_state["active"]
            else ""
        )
        settings.session_composite_message = composite_state["error"]
        _run_project_audit("EXPLICIT_DELETE")
        self.report(
            {"INFO"},
            (
                "FlowPatch project deleted; ordinary mesh kept "
                f"({vertex_count} verts, {edge_count} edges, "
                f"{face_count} faces; {len(result['properties'])} metadata "
                f"keys and {len(result['attributes'])} ownership attributes "
                "removed)."
            ),
        )
        return {"FINISHED"}


class FLOWPATCH_OT_create_composite_session(Operator):
    bl_idname = "flowpatch.create_composite_session"
    bl_label = "Create Composite from Selected"
    bl_description = (
        "Link selected project meshes into one persistent editing context; "
        "plain selected meshes attach to the configured Surface"
    )
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return (
            context.mode == "OBJECT"
            and len(
                [
                    obj
                    for obj in context.selected_objects
                    if obj.type == "MESH"
                ]
            )
            >= 2
        )

    def execute(self, context):
        selected = tuple(
            sorted(
                (
                    obj
                    for obj in context.selected_objects
                    if obj.type == "MESH"
                ),
                key=lambda obj: obj.name,
            )
        )
        settings = context.scene.flowpatch_retopo
        target = settings.target
        owners = set(selected)
        if target is not None:
            owners.add(target)
        object_snapshots = {
            obj: _capture_flowpatch_id_properties(obj)
            for obj in owners
        }
        scene_had_record = COMPOSITE_SESSION_KEY in context.scene
        scene_record_before = context.scene.get(COMPOSITE_SESSION_KEY, "")
        try:
            existing = read_composite_session(context.scene)
            records = []
            attached = 0
            for obj in selected:
                record = read_project_record(obj)
                if record is None:
                    if (
                        target is None
                        or target.type != "MESH"
                        or target is obj
                        or len(target.data.polygons) == 0
                    ):
                        raise ProjectStoreError(
                            "COMPOSITE_PLAIN_MESH_TARGET_REQUIRED",
                            (
                                f"{obj.name} is a plain mesh. Choose a "
                                "different projection Surface before adding it."
                            ),
                        )
                    record = bind_project(target, obj)
                    obj["flowpatch_target_name"] = target.name
                    obj["flowpatch_session_active"] = False
                    attached += 1
                records.append(record)
            composite = build_composite_session(
                records,
                session_uuid=(
                    existing.session_uuid if existing is not None else ""
                ),
            )
            write_composite_session(
                context.scene,
                composite,
                allow_replace=True,
            )
            resolve_composite_objects(tuple(bpy.data.objects), composite)
        except (ProjectStoreError, RuntimeError, ValueError) as exc:
            for obj, snapshot in object_snapshots.items():
                _restore_flowpatch_id_properties(obj, snapshot)
            if scene_had_record:
                context.scene[COMPOSITE_SESSION_KEY] = scene_record_before
            elif COMPOSITE_SESSION_KEY in context.scene:
                del context.scene[COMPOSITE_SESSION_KEY]
            settings.session_composite_message = str(exc)
            self.report(
                {"ERROR"},
                f"Composite creation stopped before overwrite: {exc}",
            )
            return {"CANCELLED"}

        settings.session_composite_state = (
            f"{len(composite.members)} Projects"
        )
        settings.session_composite_message = ""
        _run_project_audit("COMPOSITE_CREATE")
        self.report(
            {"INFO"},
            (
                f"Composite {composite.session_uuid[:8]} links "
                f"{len(composite.members)} projects; {attached} plain "
                "mesh object(s) attached without replacement."
            ),
        )
        return {"FINISHED"}


class FLOWPATCH_OT_clear_composite_session(Operator):
    bl_idname = "flowpatch.clear_composite_session"
    bl_label = "Clear Composite Session"
    bl_description = (
        "Remove only the shared session link; preserve every project and guide"
    )
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        try:
            return read_composite_session(context.scene) is not None
        except ProjectStoreError:
            return True

    def execute(self, context):
        clear_composite_session(context.scene)
        settings = context.scene.flowpatch_retopo
        settings.session_composite_state = ""
        settings.session_composite_message = ""
        session = FLOWPATCH_OT_guide_session._active_instance
        if session is not None:
            session._update_renderer()
        _run_project_audit("COMPOSITE_CLEAR")
        self.report(
            {"INFO"},
            "Composite link cleared; project metadata and guides were preserved.",
        )
        return {"FINISHED"}


class FLOWPATCH_OT_adopt_selected_quad_islands(Operator):
    bl_idname = "flowpatch.adopt_selected_quad_islands"
    bl_label = "Adopt Selected Quad Islands"
    bl_description = (
        "Attach complete selected all-quad islands to this project without "
        "replacing their mesh"
    )
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return (
            _edit_mesh_poll(context)
            and bool(context.edit_object.get(PROJECT_RECORD_KEY, ""))
        )

    def execute(self, context):
        obj = context.edit_object
        try:
            project = read_project_record(obj)
        except ProjectStoreError as exc:
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}
        if project is None:
            self.report({"ERROR"}, "Attach this mesh to a FlowPatch project first.")
            return {"CANCELLED"}

        bm = bmesh.from_edit_mesh(obj.data)
        rollback_mesh = None
        properties_before = _capture_flowpatch_id_properties(obj)
        session = FLOWPATCH_OT_guide_session._active_instance
        session_matches = session is not None and session._retopo is obj
        runtime_before = (
            {
                "guides": clone_guides(session._guides),
                "built_cells": deepcopy(session._built_cells),
                "previews": list(session._previews),
                "validation_errors": list(session._validation_errors),
            }
            if session_matches
            else None
        )
        try:
            rollback_mesh = _begin_auto_build_mesh_snapshot(bm)
            temporary_projector = None
            projector = session._projector if session_matches else None
            if projector is None:
                target = _project_target_for_object(obj)
                if target is None or target.type != "MESH":
                    raise FlowPatchGeometryError(
                        "The attached project target is unavailable."
                    )
                temporary_projector = SurfaceProjector(
                    target,
                    context.evaluated_depsgraph_get(),
                )
                projector = temporary_projector
            result = _adopt_selected_quad_islands(
                obj,
                bm,
                session._guides if session_matches else load_guides(obj),
                (
                    session._built_cells
                    if session_matches
                    else load_built_cells(obj)
                ),
                project.project_uuid,
                projector,
                context.scene.flowpatch_retopo,
            )
            bmesh.update_edit_mesh(
                obj.data,
                loop_triangles=True,
                destructive=True,
            )
            if session_matches:
                session._guides = result["guides"]
                session._built_cells = result["built_cells"]
                session._refresh_surface_anchors()
                save_guides(obj, session._guides)
                save_built_cells(obj, session._built_cells)
                session._rebuild_previews()
        except (
            ExistingMeshError,
            FlowPatchGeometryError,
            RuntimeError,
            ValueError,
        ) as exc:
            if rollback_mesh is not None:
                _restore_auto_build_mesh_snapshot(
                    obj,
                    bm,
                    rollback_mesh,
                    properties_before,
                )
            if session_matches and runtime_before is not None:
                session._guides = runtime_before["guides"]
                session._built_cells = runtime_before["built_cells"]
                session._previews = runtime_before["previews"]
                session._validation_errors = runtime_before[
                    "validation_errors"
                ]
                session._update_renderer()
            self.report(
                {"ERROR"},
                f"Existing-mesh adoption rolled back: {exc}",
            )
            return {"CANCELLED"}
        finally:
            if "temporary_projector" in locals() and temporary_projector:
                temporary_projector.close()
            _discard_auto_build_mesh_snapshot(rollback_mesh)

        self.report(
            {"INFO"},
            (
                f"Adopted {result['adopted']} all-quad island(s), "
                f"retained {result['repeated']} existing record(s), and "
                f"inferred {result['inferred_guides']} boundary guides."
            ),
        )
        return {"FINISHED"}


class FLOWPATCH_OT_bridge_selected_boundaries(Operator):
    bl_idname = "flowpatch.bridge_selected_boundaries"
    bl_label = "Bridge Equal Boundaries"
    bl_description = (
        "Create a welded all-quad bridge after two same-object boundary "
        "loops pass equal-count validation"
    )
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return (
            _edit_mesh_poll(context)
            and bool(context.edit_object.get(PROJECT_RECORD_KEY, ""))
        )

    def execute(self, context):
        source_objects = []
        try:
            objects_in_mode = tuple(
                getattr(context, "objects_in_mode_unique_data", ())
                or (context.edit_object,)
            )
            for obj in objects_in_mode:
                if obj.type != "MESH":
                    continue
                bm = bmesh.from_edit_mesh(obj.data)
                bm.verts.ensure_lookup_table()
                bm.verts.index_update()
                loops = _ordered_selected_closed_loops(bm)
                if loops:
                    source_objects.append((obj, bm, loops))
            if len(source_objects) > 1:
                raise ExistingMeshError(
                    "CROSS_OBJECT_BRIDGE_UNSUPPORTED",
                    (
                        "Selected loops span separate mesh datablocks. "
                        "Bridge within one active project object."
                    ),
                )
            if len(source_objects) != 1:
                raise ExistingMeshError(
                    "TWO_BOUNDARIES_REQUIRED",
                    "Select exactly two closed boundary loops on one mesh.",
                )
            obj, bm, loops = source_objects[0]
            if obj is not context.edit_object:
                raise ExistingMeshError(
                    "ACTIVE_BRIDGE_OBJECT_REQUIRED",
                    "Make the project containing both loops the active object.",
                )
            project = read_project_record(obj)
            if project is None:
                raise ExistingMeshError(
                    "PROJECT_REQUIRED",
                    "The active mesh is not attached to a FlowPatch project.",
                )
        except (
            ExistingMeshError,
            FlowPatchGeometryError,
            ProjectStoreError,
            RuntimeError,
            ValueError,
        ) as exc:
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}

        rollback_mesh = None
        properties_before = _capture_flowpatch_id_properties(obj)
        session = FLOWPATCH_OT_guide_session._active_instance
        session_matches = session is not None and session._retopo is obj
        built_before = (
            deepcopy(session._built_cells)
            if session_matches
            else load_built_cells(obj)
        )
        try:
            rollback_mesh = _begin_auto_build_mesh_snapshot(bm)
            result = _bridge_selected_boundary_loops(
                obj,
                bm,
                loops,
                built_before,
                project.project_uuid,
            )
            bmesh.update_edit_mesh(
                obj.data,
                loop_triangles=True,
                destructive=True,
            )
            if session_matches:
                session._built_cells = result["built_cells"]
                session._rebuild_previews()
        except (
            ExistingMeshError,
            FlowPatchGeometryError,
            RuntimeError,
            ValueError,
        ) as exc:
            if rollback_mesh is not None:
                _restore_auto_build_mesh_snapshot(
                    obj,
                    bm,
                    rollback_mesh,
                    properties_before,
                )
            if session_matches:
                session._built_cells = built_before
                session._rebuild_previews()
            self.report(
                {"ERROR"},
                f"Boundary bridge rolled back: {exc}",
            )
            return {"CANCELLED"}
        finally:
            _discard_auto_build_mesh_snapshot(rollback_mesh)

        self.report(
            {"INFO"},
            (
                "Boundary bridge already existed; no topology changed."
                if result["repeated"]
                else (
                    f"FlowPatch {result['patch_id']}: created "
                    f"{result['created_faces']} welded quads."
                )
            ),
        )
        return {"FINISHED"}


def _clone_guides(guides):
    return clone_guides(guides)


def _preview_segments(preview):
    topology_kind = str(
        getattr(preview, "topology_kind", "GRID")
    ).upper()
    if topology_kind == "ANNULAR":
        vertices = list(preview.quad_vertices_world)
        edge_indices = set()
        for face in preview.polygon_quads:
            for start, end in zip(face, face[1:] + face[:1]):
                edge_indices.add(tuple(sorted((start, end))))
        return [
            (vertices[start], vertices[end])
            for start, end in sorted(edge_indices)
        ]
    if topology_kind == "POLYGON":
        outline = list(preview.polygon_world)
        if len(outline) < 2:
            return []
        return list(zip(outline, outline[1:] + outline[:1]))

    segments = []
    for rail in preview.rails_world:
        segments.extend(zip(rail, rail[1:]))
    if not preview.rails_world:
        return segments
    for column_index in range(len(preview.rails_world[0])):
        for row_index in range(preview.row_count):
            segments.append(
                (
                    preview.rails_world[row_index][column_index],
                    preview.rails_world[row_index + 1][column_index],
                )
            )
    return segments


class FLOWPATCH_OT_guide_session(Operator):
    bl_idname = "flowpatch.guide_session"
    bl_label = "FlowPatch Guide Session"
    bl_description = (
        "Draw retained surface guides; valid four-sided cells preview and "
        "auto-build durable all-quad patches"
    )
    bl_options = {"REGISTER", "UNDO"}

    _active_instance = None
    _area = None
    _region = None
    _region_3d = None
    _renderer = None
    _projector = None
    _retopo = None
    _target = None
    _project_uuid = ""
    _retopo_object_uuid = ""
    _retopo_object_name_hint = ""
    _target_object_uuid = ""
    _target_object_name_hint = ""
    _guides = None
    _built_cells = None
    _previews = None
    _density_overrides = None
    _stroke_world = None
    _stroke_screen = None
    _stroke_anchors = None
    _stroke_filter_mouse = None
    _stroke_event_mouse = None
    _pen_state = None
    _drawing = False
    _pointer_state = _POINTER_IDLE
    _stroke_snap_press = None
    _stroke_snap_release = None
    _stroke_had_projection_gap = False
    _mouse_captured = False
    _stroke_serial = 0
    _finalized_stroke_serial = 0
    _session_alive = False
    _session_epoch = 0
    _mode = "DRAW"
    _u_segments = 4
    _v_segments = 4
    _selected_control = None
    _selected_guides = None
    _selected_points = None
    _active_anchor = None
    _dragging_control = False
    _g_move = False
    _transform_mode = ""
    _transform_snapshot = None
    _transform_refs = None
    _transform_initial_world = None
    _transform_center_world = None
    _transform_origin_mouse = None
    _transform_origin_radius = 1.0
    _transform_origin_angle = 0.0
    _transform_start_location = None
    _transform_tangent_normal = None
    _transform_temporary_selection = False
    _transform_return_mode = ""
    _transform_projection_rejected = False
    _history = None
    _redo_history = None
    _move_snapshot = None
    _validation_errors = None
    _exit_armed = False
    _active_preview_index = 0
    _scene = None
    _settings_signature = None
    _cleaned = False
    _stop_requested = False
    _cursor_active = False
    _toolbar_summary = ""
    _hover_segment = None
    _hover_snap_state = None
    _stroke_endpoint_nodes = None
    _last_preview_warning = ""

    @classmethod
    def poll(cls, context):
        return (
            context.area is not None
            and context.area.type == "VIEW_3D"
            and _edit_mesh_poll(context)
        )

    def _set_status(self, context, text):
        try:
            context.workspace.status_text_set(text)
        except Exception:
            pass

    def _tag_redraw(self):
        if self._area is not None:
            self._area.tag_redraw()

    def _mouse_in_region(self, event):
        if self._region is None:
            return False
        return (
            self._region.x <= event.mouse_x < self._region.x + self._region.width
            and self._region.y <= event.mouse_y < self._region.y + self._region.height
        )

    def _mouse_over_native_ui(self, event):
        if self._area is None:
            return False
        for region in self._area.regions:
            if region.type == "WINDOW":
                continue
            if (
                region.x <= event.mouse_x < region.x + region.width
                and region.y <= event.mouse_y < region.y + region.height
            ):
                return True
        if self._region is None:
            return False
        local_x = event.mouse_x - self._region.x
        return 0 <= local_x < 52

    def _suspend_for_native_ui(self, context):
        if _pen_active(self):
            self._cancel_surface_stroke("NATIVE_UI_SUSPEND")
        if self._transform_mode:
            self._cancel_transform(context)
        if self._dragging_control or self._g_move:
            self._dragging_control = False
            self._g_move = False
            self._move_snapshot = None
        self._preserve_guides()
        self._clear_hover_feedback("NATIVE_UI_SUSPEND")
        self._restore_cursor(context)
        _pen_reset(self)

    def _cancel_surface_stroke(self, reason_code):
        if not _pen_active(self):
            return False
        SESSION_BREADCRUMBS.record(
            "stroke_cancelled",
            reason_code=str(reason_code),
            stroke_serial=_safe_int(
                getattr(self, "_stroke_serial", 0)
            ),
            sample_count=_safe_count(
                getattr(self, "_stroke_world", None)
            ),
        )
        _pen_cancel(self, reason_code)
        self._clear_stroke()
        return True

    def _region_mouse(self, event):
        return Vector(
            (
                event.mouse_x - self._region.x,
                event.mouse_y - self._region.y,
            )
        )

    def _set_cursor(self, context):
        cursor = (
            "PAINT_BRUSH"
            if self._mode == "DRAW"
            else "KNIFE"
            if self._mode == "CUT"
            else "CROSSHAIR"
        )
        try:
            context.window.cursor_modal_set(cursor)
            self._cursor_active = True
        except Exception:
            self._cursor_active = False

    def _restore_cursor(self, context):
        if not self._cursor_active:
            return
        try:
            context.window.cursor_modal_restore()
        except Exception:
            pass
        self._cursor_active = False

    def _stabilized_mouse(self, mouse):
        mouse = Vector(mouse)
        if self._stroke_filter_mouse is None:
            self._stroke_filter_mouse = mouse.copy()
            return mouse
        strength = max(
            0.0,
            min(
                0.95,
                float(self._scene.flowpatch_retopo.stroke_stabilization),
            ),
        )
        response = max(0.08, 1.0 - strength * 0.88)
        self._stroke_filter_mouse = self._stroke_filter_mouse.lerp(
            mouse,
            response,
        )
        return self._stroke_filter_mouse.copy()

    def _regularize_stroke(self, context):
        if len(self._stroke_screen) < 3:
            return
        strength = max(
            0.0,
            min(
                0.95,
                float(context.scene.flowpatch_retopo.stroke_stabilization),
            ),
        )
        passes = max(1, round(1.0 + strength * 3.0))
        smoothed = [point.copy() for point in self._stroke_screen]
        for _pass in range(passes):
            source = [point.copy() for point in smoothed]
            for index in range(1, len(source) - 1):
                neighborhood = (
                    source[index - 1]
                    + source[index]
                    + source[index + 1]
                ) / 3.0
                smoothed[index] = source[index].lerp(
                    neighborhood,
                    strength,
                )

        settings = context.scene.flowpatch_retopo
        simplify_tolerance = max(
            1.5,
            min(
                5.0,
                float(settings.sample_spacing_px) * 0.42
                + strength * 1.4,
            ),
        )
        retained_indices = _simplify_polyline_indices(
            smoothed,
            simplify_tolerance,
        )
        retained_indices = _ensure_polyline_support_indices(
            smoothed,
            retained_indices,
            max_spacing=max(
                24.0,
                min(48.0, float(settings.sample_spacing_px) * 5.0),
            ),
        )
        smoothed = [smoothed[index] for index in retained_indices]
        fallback_world = [
            self._stroke_world[index].copy()
            for index in retained_indices
        ]
        retained_anchors = (
            [self._stroke_anchors[index].copy() for index in retained_indices]
            if len(self._stroke_anchors or ()) == len(self._stroke_world)
            else [None for _index in retained_indices]
        )

        target_uuid = str(self._target_object_uuid)
        world = []
        anchors = []
        previous_anchor = None
        for index, point in enumerate(smoothed):
            seed_anchor = retained_anchors[index] or previous_anchor
            hit = self._projector.raycast_region_continuous(
                self._region,
                self._region_3d,
                point,
                target_object_uuid=target_uuid,
                previous_anchor=seed_anchor,
                surface_offset=settings.surface_offset,
                frontface_epsilon=settings.frontface_epsilon,
                max_surface_step=settings.max_surface_step,
                normal_continuity_cos=settings.normal_continuity_cos,
            )
            if hit is not None:
                point_world = Vector(hit[0])
                anchor = hit[4].copy()
            else:
                anchor = retained_anchors[index]
                resolved = (
                    self._projector.world_from_anchor(anchor)
                    if anchor is not None
                    else None
                )
                point_world = (
                    Vector(resolved[0])
                    if resolved is not None
                    else fallback_world[index].copy()
                )
            world.append(point_world)
            anchors.append(anchor.copy() if anchor is not None else None)
            if anchor is not None:
                previous_anchor = anchor
        filtered_world = _filter_guide_world_by_view(
            world,
            smoothed,
            self._region,
            self._region_3d,
            projection_mode=settings.projection_mode,
            surface_follow_strength=settings.surface_follow_strength,
            surface_tighten_strength=settings.surface_tighten_strength,
            surface_smoothing_radius=settings.surface_smoothing_radius,
            detail_ignore_threshold=settings.detail_ignore_threshold,
        )
        final_world = []
        final_anchors = []
        previous_anchor = None
        for index, candidate_world in enumerate(filtered_world):
            seed_anchor = anchors[index] or previous_anchor
            nearest = self._projector.nearest_world_continuous(
                candidate_world,
                target_object_uuid=target_uuid,
                previous_anchor=seed_anchor,
                surface_offset=settings.surface_offset,
                **_continuity_kwargs(settings),
            )
            if nearest is not None:
                point_world = Vector(nearest[0])
                anchor = nearest[4].copy()
            else:
                anchor = anchors[index]
                resolved = (
                    self._projector.world_from_anchor(anchor)
                    if anchor is not None
                    else None
                )
                point_world = (
                    Vector(resolved[0])
                    if resolved is not None
                    else world[index].copy()
                )
            final_world.append(point_world)
            final_anchors.append(
                anchor.copy() if anchor is not None else None
            )
            if anchor is not None:
                previous_anchor = anchor
        self._stroke_screen = smoothed
        self._stroke_world = final_world
        self._stroke_anchors = final_anchors
        self._renderer.stroke = list(final_world)

    def _bounded_snap_radius(self, radius_px):
        return min(
            max(0.0, float(radius_px)),
            _SNAP_HARD_MAX_PX,
        )

    def _nearest_guide_endpoint(self, mouse_region, radius_px=None):
        radius = self._bounded_snap_radius(
            radius_px if radius_px is not None else _SNAP_HOVER_PX
        )
        best = None
        matrix = self._retopo.matrix_world
        for node_id, node in graph_nodes(self._guides).items():
            point_world = matrix @ node.point_local
            point_2d = view3d_utils.location_3d_to_region_2d(
                self._region,
                self._region_3d,
                point_world,
            )
            if point_2d is None:
                continue
            point_2d = Vector(point_2d)
            distance = (point_2d - mouse_region).length
            if distance > radius or (
                best is not None and distance >= best["distance"]
            ):
                continue
            best = {
                "distance": distance,
                "kind": "CANONICAL_NODE",
                "node_id": int(node_id),
                "point_2d": point_2d,
                "point_world": point_world.copy(),
            }
        return best

    def _nearest_guide_segment(self, mouse_region, radius_px=None):
        radius = self._bounded_snap_radius(
            radius_px if radius_px is not None else _SNAP_HOVER_PX
        )
        best = None
        matrix = self._retopo.matrix_world
        for guide_index, guide in enumerate(self._guides):
            for segment_index, (start_local, end_local) in enumerate(
                zip(guide.points_local, guide.points_local[1:])
            ):
                start_world = matrix @ start_local
                end_world = matrix @ end_local
                start_2d = view3d_utils.location_3d_to_region_2d(
                    self._region,
                    self._region_3d,
                    start_world,
                )
                end_2d = view3d_utils.location_3d_to_region_2d(
                    self._region,
                    self._region_3d,
                    end_world,
                )
                if start_2d is None or end_2d is None:
                    continue
                closest_2d, factor = _closest_point_2d(
                    mouse_region,
                    start_2d,
                    end_2d,
                )
                distance = (closest_2d - mouse_region).length
                if distance > radius or (
                    best is not None and distance >= best["distance"]
                ):
                    continue
                best = {
                    "distance": distance,
                    "kind": "GUIDE_EDGE",
                    "guide_index": guide_index,
                    "guide_id": int(guide.guide_id),
                    "logical_side_id": int(guide.logical_side_id),
                    "segment_index": segment_index,
                    "factor": factor,
                    "point_2d": closest_2d,
                    "point_world": start_world.lerp(end_world, factor),
                    "segment_world": (start_world, end_world),
                }
        return best

    def _nearest_preview_vertex(self, mouse_region, radius_px=None):
        radius = self._bounded_snap_radius(
            radius_px if radius_px is not None else _SNAP_HOVER_PX
        )
        best = None
        seen = set()
        for preview in self._previews:
            for start_world, end_world in _preview_segments(preview):
                for point_world in (start_world, end_world):
                    key = tuple(round(float(value), 7) for value in point_world)
                    if key in seen:
                        continue
                    seen.add(key)
                    point_2d = view3d_utils.location_3d_to_region_2d(
                        self._region,
                        self._region_3d,
                        point_world,
                    )
                    if point_2d is None:
                        continue
                    point_2d = Vector(point_2d)
                    distance = (point_2d - mouse_region).length
                    if distance > radius or (
                        best is not None and distance >= best["distance"]
                    ):
                        continue
                    best = {
                        "distance": distance,
                        "kind": "COMMITTED_BOUNDARY_VERTEX",
                        "point_2d": point_2d,
                        "point_world": Vector(point_world).copy(),
                    }
        return best

    def _nearest_preview_segment(self, mouse_region, radius_px=None):
        radius = self._bounded_snap_radius(
            radius_px if radius_px is not None else _SNAP_HOVER_PX
        )
        best = None
        for preview in self._previews:
            for start_world, end_world in _preview_segments(preview):
                start_2d = view3d_utils.location_3d_to_region_2d(
                    self._region,
                    self._region_3d,
                    start_world,
                )
                end_2d = view3d_utils.location_3d_to_region_2d(
                    self._region,
                    self._region_3d,
                    end_world,
                )
                if start_2d is None or end_2d is None:
                    continue
                closest_2d, factor = _closest_point_2d(
                    mouse_region,
                    start_2d,
                    end_2d,
                )
                distance = (closest_2d - mouse_region).length
                if distance > radius or (
                    best is not None and distance >= best["distance"]
                ):
                    continue
                best = {
                    "distance": distance,
                    "kind": "COMMITTED_BOUNDARY_EDGE",
                    "point_2d": closest_2d,
                    "point_world": start_world.lerp(end_world, factor),
                    "segment_world": (start_world, end_world),
                }
        return best

    def _snap_candidate(self, mouse_region, radius_px):
        radius = self._bounded_snap_radius(radius_px)
        return select_snap_candidate(
            (
                self._nearest_guide_endpoint(mouse_region, radius),
                self._nearest_guide_segment(mouse_region, radius),
            ),
            radius,
            _SNAP_HARD_MAX_PX,
        )

    def _freeze_snap_candidate(self, mouse_region, radius_px):
        hit = self._snap_candidate(mouse_region, radius_px)
        if hit is None:
            return None
        frozen = {
            "kind": str(hit.get("kind", "TARGET")),
            "node_id": int(hit.get("node_id", 0)),
            "point_2d": Vector(hit["point_2d"]).copy(),
            "point_world": Vector(hit["point_world"]).copy(),
        }
        if "segment_world" in hit:
            frozen["segment_world"] = tuple(
                Vector(point).copy() for point in hit["segment_world"]
            )
        for key in (
            "distance",
            "guide_id",
            "logical_side_id",
            "segment_index",
            "factor",
        ):
            if key in hit:
                frozen[key] = hit[key]
        return frozen

    def _clear_hover_feedback(self, reason_code=""):
        _clear_hover_snap(self, reason_code)

    def _update_hover(self, mouse_region):
        if self._renderer is None:
            return
        candidates = (
            self._nearest_guide_endpoint(mouse_region, _SNAP_HOVER_PX),
            self._nearest_guide_segment(mouse_region, _SNAP_HOVER_PX),
        )
        hit = self._hover_snap_state.update(
            candidates,
            _SNAP_HOVER_PX,
            _SNAP_HARD_MAX_PX,
        )
        self._hover_segment = hit
        self._renderer.hover_segments = (
            [hit["segment_world"]]
            if hit is not None and "segment_world" in hit
            else []
        )
        self._renderer.snap_points = (
            [hit["point_world"]] if hit is not None else []
        )
        self._renderer.snap_kind = (
            str(hit.get("kind", "")) if hit is not None else ""
        )
        self._tag_redraw()

    def _apply_frozen_stroke_snaps(self):
        self._stroke_endpoint_nodes = [0, 0]
        if len(self._stroke_world) < 2:
            return
        for endpoint_slot, endpoint_index, hit in (
            (0, 0, self._stroke_snap_press),
            (1, len(self._stroke_world) - 1, self._stroke_snap_release),
        ):
            if hit is None:
                continue
            self._stroke_screen[endpoint_index] = hit["point_2d"].copy()
            self._stroke_world[endpoint_index] = hit["point_world"].copy()
            self._stroke_endpoint_nodes[endpoint_slot] = int(
                hit.get("node_id", 0)
            )
        self._renderer.stroke = list(self._stroke_world)

    def _set_toolbar_hover(self, mouse_region):
        if self._renderer is None:
            return False
        hovered = self._renderer.toolbar_hover_test(mouse_region)
        if hovered == self._renderer.toolbar_hover:
            return hovered is not None
        self._renderer.toolbar_hover = hovered or ""
        tooltip = ""
        if hovered is not None:
            item = next(
                (
                    item
                    for item in self._renderer.toolbar_items
                    if item.get("id") == hovered
                ),
                None,
            )
            if item is not None:
                tooltip = str(item.get("tooltip", ""))
                disabled_reason = str(item.get("disabled_reason", ""))
                if not item.get("enabled", True) and disabled_reason:
                    tooltip = f"{tooltip} Disabled: {disabled_reason}"
        self._renderer.toolbar_status = tooltip or self._toolbar_summary
        self._tag_redraw()
        return hovered is not None

    def _guide_is_locked(self, guide_id):
        guide_id = int(guide_id)
        for cycle_key, record in (self._built_cells or {}).items():
            if not isinstance(record, dict):
                if str(guide_id) in str(cycle_key).split(":"):
                    return True
                continue
            if guide_id not in {
                int(value) for value in record.get("edge_ids", [])
            }:
                continue
            state = str(record.get("state", "")).upper()
            if state == SYNC_DETACHED:
                continue
            if (
                state == SYNC_PARAMETRIC
                and str(record.get("topology_kind", "")).upper() == "GRID"
                and record.get("grid_vertex_uids")
            ):
                continue
            return True
        return False

    def _refresh_surface_anchors(self):
        if self._projector is None or self._target is None:
            return 0
        target_uuid = str(self._target.get(OBJECT_UUID_KEY, ""))
        if not target_uuid:
            raise FlowPatchGeometryError(
                "The projection target has no stable object UUID."
            )
        settings = self._scene.flowpatch_retopo
        return refresh_guide_surface_anchors(
            self._retopo,
            self._guides or [],
            self._projector,
            target_uuid,
            normal_offset=float(settings.surface_offset),
            **_continuity_kwargs(settings),
        )

    def _sync_state_counts(self):
        counts = {
            SYNC_PARAMETRIC: 0,
            SYNC_FROZEN: 0,
            SYNC_DETACHED: 0,
        }
        first_frozen_message = ""
        for record in (self._built_cells or {}).values():
            state = (
                str(record.get("state", SYNC_FROZEN)).upper()
                if isinstance(record, dict)
                else SYNC_FROZEN
            )
            counts[state if state in counts else SYNC_FROZEN] += 1
            if (
                not first_frozen_message
                and isinstance(record, dict)
                and state == SYNC_FROZEN
            ):
                first_frozen_message = str(
                    record.get(
                        "sync_message",
                        "A built region is frozen.",
                    )
                )
        return counts, first_frozen_message

    def _audit_mesh_sync(self, allow_unfreeze=False):
        if not self._built_cells:
            return {}
        bm = bmesh.from_edit_mesh(self._retopo.data)
        staged, decisions, _uid_map = audit_built_cell_sync(
            bm,
            self._built_cells,
            allow_unfreeze=allow_unfreeze,
        )
        if staged != self._built_cells:
            save_built_cells(self._retopo, staged)
            self._built_cells = staged
        return decisions

    def _sync_mesh_from_current_guides(
        self,
        changed_guide_ids=None,
        force=False,
        report_failure=True,
    ):
        guide_snapshot = _clone_guides(self._guides)
        try:
            self._refresh_surface_anchors()
            bm = bmesh.from_edit_mesh(self._retopo.data)
            result = synchronize_mesh_from_guides(
                self._retopo,
                bm,
                self._guides,
                self._built_cells,
                self._projector,
                changed_guide_ids=changed_guide_ids,
                force=force,
            )
        except (FlowPatchGeometryError, RuntimeError, ValueError) as exc:
            self._guides = guide_snapshot
            if report_failure:
                self.report({"ERROR"}, f"Guide-to-mesh sync stopped: {exc}")
            SESSION_BREADCRUMBS.record(
                "mesh_sync_failed",
                direction="GUIDES_TO_MESH",
                message=str(exc),
            )
            return None
        self._built_cells = result.built_cells
        if result.frozen_cell_keys:
            if report_failure:
                self.report(
                    {"WARNING"},
                    "Guide edit retained, but its built region is frozen; "
                    "use the Sync controls.",
                )
            SESSION_BREADCRUMBS.record(
                "mesh_sync_frozen",
                direction="GUIDES_TO_MESH",
                cycle_keys=result.frozen_cell_keys,
            )
            return None
        if not result.changed_cell_keys:
            save_guides(self._retopo, self._guides)
        SESSION_BREADCRUMBS.record(
            "mesh_sync_completed",
            direction="GUIDES_TO_MESH",
            cycle_keys=result.changed_cell_keys,
        )
        return result

    def _sync_guides_from_current_mesh(
        self,
        force=False,
        rebuild=True,
        report_result=True,
    ):
        try:
            bm = bmesh.from_edit_mesh(self._retopo.data)
            result = synchronize_guides_from_mesh(
                self._retopo,
                bm,
                self._guides,
                self._built_cells,
                self._projector,
                str(self._target.get(OBJECT_UUID_KEY, "")),
                normal_offset=float(
                    self._scene.flowpatch_retopo.surface_offset
                ),
                **_continuity_kwargs(self._scene.flowpatch_retopo),
                force=force,
            )
        except (FlowPatchGeometryError, RuntimeError, ValueError) as exc:
            if report_result:
                self.report({"ERROR"}, f"Mesh-to-guide sync stopped: {exc}")
            SESSION_BREADCRUMBS.record(
                "mesh_sync_failed",
                direction="MESH_TO_GUIDES",
                message=str(exc),
            )
            return None
        self._built_cells = result.built_cells
        if rebuild:
            self._rebuild_previews()
        if report_result:
            level = {"WARNING"} if result.frozen_cell_keys else {"INFO"}
            self.report(level, result.message)
        SESSION_BREADCRUMBS.record(
            "mesh_sync_completed",
            direction="MESH_TO_GUIDES",
            cycle_keys=result.changed_cell_keys,
            frozen_cycle_keys=result.frozen_cell_keys,
            changed_control_count=result.changed_control_count,
        )
        return result

    def _restore_rejected_guide_edit(self, snapshot):
        sync_cells = deepcopy(self._built_cells)
        self._restore_state(snapshot)
        self._built_cells = sync_cells
        save_built_cells(self._retopo, self._built_cells)
        if self._history:
            discarded = self._history.pop()
            self._discard_state_snapshot(discarded)

    def _changed_guide_ids_from_snapshot(self, snapshot):
        before = {
            int(guide.guide_id): guide
            for guide in snapshot.get("guides", ())
        }
        changed = []
        for guide in self._guides:
            previous = before.get(int(guide.guide_id))
            if previous is None or len(previous.points_local) != len(
                guide.points_local
            ):
                changed.append(int(guide.guide_id))
                continue
            if any(
                (left - right).length > 1.0e-8
                for left, right in zip(
                    previous.points_local,
                    guide.points_local,
                )
            ):
                changed.append(int(guide.guide_id))
        return tuple(sorted(changed))

    def _freeze_cells_for_guides(self, guide_ids, reason_code, message):
        guide_ids = {int(value) for value in guide_ids}
        staged = deepcopy(self._built_cells)
        changed = []
        for cycle_key, record in staged.items():
            if not isinstance(record, dict):
                continue
            if str(record.get("state", "")).upper() == SYNC_DETACHED:
                continue
            if not guide_ids.intersection(
                int(value) for value in record.get("edge_ids", ())
            ):
                continue
            staged[cycle_key] = frozen_record(
                record,
                reason_code,
                message,
            )
            changed.append(str(cycle_key))
        if changed:
            save_built_cells(self._retopo, staged)
            self._built_cells = staged
        return tuple(sorted(changed))

    def _state_snapshot(self, include_mesh=False):
        snapshot = {
            "guides": _clone_guides(self._guides),
            "u_segments": int(self._u_segments),
            "v_segments": int(self._v_segments),
            "built_cells": deepcopy(self._built_cells),
            "density_overrides": deepcopy(self._density_overrides),
            "selected_control": self._selected_control,
            "selected_guides": tuple(sorted(self._selected_guides or ())),
            "selected_points": tuple(sorted(self._selected_points or ())),
            "active_anchor": self._active_anchor,
        }
        if include_mesh:
            bm = bmesh.from_edit_mesh(self._retopo.data)
            snapshot["_mesh_backup"] = _begin_auto_build_mesh_snapshot(bm)
            snapshot["_flowpatch_properties"] = (
                _capture_flowpatch_id_properties(self._retopo)
            )
        return snapshot

    def _discard_state_snapshot(self, snapshot):
        if isinstance(snapshot, dict):
            _discard_auto_build_mesh_snapshot(snapshot.get("_mesh_backup"))

    def _discard_history_stack(self, stack):
        for snapshot in tuple(stack or ()):
            self._discard_state_snapshot(snapshot)
        if stack is not None:
            stack.clear()

    def _push_history(self, clear_redo=True, snapshot=None):
        self._history.append(
            snapshot if snapshot is not None else self._state_snapshot()
        )
        if len(self._history) > 32:
            discarded = self._history.pop(0)
            self._discard_state_snapshot(discarded)
        if clear_redo:
            self._discard_history_stack(self._redo_history)

    def _restore_state(self, snapshot, rebuild=True):
        mesh_backup = snapshot.get("_mesh_backup")
        if mesh_backup is not None:
            bm = bmesh.from_edit_mesh(self._retopo.data)
            _restore_auto_build_mesh_snapshot(
                self._retopo,
                bm,
                mesh_backup,
                snapshot.get("_flowpatch_properties", {}),
            )
        self._guides = _clone_guides(snapshot["guides"])
        self._u_segments = int(snapshot["u_segments"])
        self._v_segments = int(snapshot["v_segments"])
        self._built_cells = deepcopy(snapshot["built_cells"])
        self._density_overrides = deepcopy(
            snapshot.get("density_overrides", {})
        )
        save_guides(self._retopo, self._guides)
        save_built_cells(self._retopo, self._built_cells)
        self._selected_control = snapshot.get("selected_control")
        self._selected_guides = set(snapshot.get("selected_guides", ()))
        self._selected_points = set(snapshot.get("selected_points", ()))
        self._active_anchor = snapshot.get("active_anchor")
        self._sync_scene_settings()
        if rebuild:
            self._rebuild_previews()

    def _undo_guide_edit(self):
        if not self._history:
            return False
        snapshot = self._history.pop()
        redo_snapshot = self._state_snapshot(
            include_mesh="_mesh_backup" in snapshot
        )
        try:
            self._restore_state(snapshot)
        except Exception:
            self._discard_state_snapshot(redo_snapshot)
            self._history.append(snapshot)
            raise
        self._redo_history.append(redo_snapshot)
        self._discard_state_snapshot(snapshot)
        return True

    def _redo_guide_edit(self):
        if not self._redo_history:
            return False
        snapshot = self._redo_history.pop()
        undo_snapshot = self._state_snapshot(
            include_mesh="_mesh_backup" in snapshot
        )
        try:
            self._restore_state(snapshot)
        except Exception:
            self._discard_state_snapshot(undo_snapshot)
            self._redo_history.append(snapshot)
            raise
        self._history.append(undo_snapshot)
        self._discard_state_snapshot(snapshot)
        return True

    def _resolve_target(self, context):
        return _resolve_session_target(context, context.edit_object)

    def _append_surface_sample(self, context, event, force=False):
        settings = context.scene.flowpatch_retopo
        target_uuid = str(self._target_object_uuid)
        if self._stroke_anchors is None:
            self._stroke_anchors = []
        raw_mouse = self._region_mouse(event)
        mouse = self._stabilized_mouse(raw_mouse)
        had_projection_gap = bool(self._stroke_had_projection_gap)

        spacing = max(1.0, float(settings.sample_spacing_px))
        interpolation_step = max(2.0, min(8.0, spacing * 0.5))
        max_bridge_distance = max(18.0, spacing * 3.0)
        current_hit = self._projector.raycast_region_continuous(
            self._region,
            self._region_3d,
            mouse,
            target_object_uuid=target_uuid,
            # This is a visibility probe only. The candidate loop below
            # validates every committed sample against the prior anchor.
            previous_anchor=None,
            surface_offset=settings.surface_offset,
            frontface_epsilon=settings.frontface_epsilon,
            max_surface_step=settings.max_surface_step,
            normal_continuity_cos=settings.normal_continuity_cos,
        )
        if current_hit is None:
            if not had_projection_gap:
                SESSION_BREADCRUMBS.record(
                    "raycast_miss",
                    stroke_serial=_safe_int(
                        getattr(self, "_stroke_serial", 0)
                    ),
                    sample_count=_safe_count(
                        getattr(self, "_stroke_world", None)
                    ),
                )
                self._set_status(
                    context,
                    "FlowPatch needs a front-facing hit on the locked Surface.",
                )
            _pen_projection_miss(self)
            return False

        previous_event_mouse = (
            self._stroke_event_mouse.copy()
            if self._stroke_event_mouse is not None
            else None
        )
        if (
            self._stroke_had_projection_gap
            and previous_event_mouse is not None
            and (mouse - previous_event_mouse).length > max_bridge_distance
        ):
            SESSION_BREADCRUMBS.record(
                "raycast_recovery_blocked",
                stroke_serial=_safe_int(
                    getattr(self, "_stroke_serial", 0)
                ),
                reason_code="MAX_BRIDGE_DISTANCE",
            )
            return False

        candidates = _interpolate_screen_segment(
            previous_event_mouse,
            mouse,
            interpolation_step,
        )
        appended = False
        last_candidate_index = len(candidates) - 1

        for candidate_index, candidate in enumerate(candidates):
            if len(self._stroke_screen) >= _MAX_STROKE_SAMPLES:
                self.report(
                    {"WARNING"},
                    "FlowPatch stopped this stroke at its safety sample limit.",
                )
                break
            hit = self._projector.raycast_region_continuous(
                self._region,
                self._region_3d,
                candidate,
                target_object_uuid=target_uuid,
                previous_anchor=(
                    self._stroke_anchors[-1]
                    if self._stroke_anchors
                    else None
                ),
                surface_offset=settings.surface_offset,
                frontface_epsilon=settings.frontface_epsilon,
                max_surface_step=settings.max_surface_step,
                normal_continuity_cos=settings.normal_continuity_cos,
            )
            if hit is None:
                if not self._stroke_had_projection_gap:
                    SESSION_BREADCRUMBS.record(
                        "raycast_miss",
                        stroke_serial=_safe_int(
                            getattr(self, "_stroke_serial", 0)
                        ),
                        sample_count=_safe_count(
                            getattr(self, "_stroke_world", None)
                        ),
                    )
                _pen_projection_miss(self)
                break

            if self._stroke_screen:
                screen_distance = (
                    candidate - self._stroke_screen[-1]
                ).length
                if screen_distance > max_bridge_distance:
                    _pen_projection_miss(self)
                    break
                minimum_spacing = spacing
                if force and candidate_index == last_candidate_index:
                    minimum_spacing = 0.5
                if screen_distance < minimum_spacing:
                    continue

            self._stroke_screen.append(candidate.copy())
            self._stroke_world.append(Vector(hit[0]))
            self._stroke_anchors.append(hit[4].copy())
            self._stroke_event_mouse = candidate.copy()
            _pen_projected_sample(self)
            appended = True

        if not appended:
            return False
        if had_projection_gap:
            SESSION_BREADCRUMBS.record(
                "raycast_recovered",
                stroke_serial=_safe_int(
                    getattr(self, "_stroke_serial", 0)
                ),
                sample_count=_safe_count(
                    getattr(self, "_stroke_world", None)
                ),
            )
        self._renderer.stroke = list(self._stroke_world)
        self._tag_redraw()
        return True

    def _flatten_controls(self):
        points = []
        matrix = self._retopo.matrix_world
        for guide in self._guides:
            points.extend(matrix @ point for point in guide.points_local)
        return points

    def _graph_node_world(self):
        matrix = self._retopo.matrix_world
        return [
            matrix @ node.point_local
            for _node_id, node in sorted(graph_nodes(self._guides).items())
        ]

    def _selection_guide_indices(self):
        if self._selected_points:
            return tuple(
                guide_index
                for guide_index in sorted(
                    {
                        int(guide_index)
                        for guide_index, _point_index in self._selected_points
                    }
                )
                if 0 <= guide_index < len(self._guides)
            )
        if self._selected_guides:
            return tuple(
                index
                for index in sorted(self._selected_guides)
                if 0 <= index < len(self._guides)
            )
        if self._selected_control is None:
            return ()
        guide_index = int(self._selected_control[0])
        return (guide_index,) if 0 <= guide_index < len(self._guides) else ()

    def _expand_shared_endpoint_refs(self, ref):
        guide_index, point_index = int(ref[0]), int(ref[1])
        if not (0 <= guide_index < len(self._guides)):
            return set()
        guide = self._guides[guide_index]
        if not (0 <= point_index < len(guide.points_local)):
            return set()

        refs = {(guide_index, point_index)}
        node_id = 0
        if point_index == 0:
            node_id = int(guide.start_node)
        elif point_index == len(guide.points_local) - 1:
            node_id = int(guide.end_node)
        if node_id <= 0:
            return refs

        for candidate_index, candidate in enumerate(self._guides):
            if int(candidate.start_node) == node_id:
                refs.add((candidate_index, 0))
            if int(candidate.end_node) == node_id:
                refs.add(
                    (candidate_index, len(candidate.points_local) - 1)
                )
        return refs

    def _point_adjacency(self):
        adjacency = {}
        shared_nodes = {}
        for guide_index, guide in enumerate(self._guides):
            point_count = len(guide.points_local)
            for point_index in range(point_count):
                ref = (guide_index, point_index)
                neighbors = adjacency.setdefault(ref, set())
                if point_index > 0:
                    neighbors.add((guide_index, point_index - 1))
                if point_index + 1 < point_count:
                    neighbors.add((guide_index, point_index + 1))

            if point_count:
                if int(guide.start_node) > 0:
                    shared_nodes.setdefault(int(guide.start_node), set()).add(
                        (guide_index, 0)
                    )
                if int(guide.end_node) > 0:
                    shared_nodes.setdefault(int(guide.end_node), set()).add(
                        (guide_index, point_count - 1)
                    )

        for refs in shared_nodes.values():
            for ref in refs:
                adjacency.setdefault(ref, set()).update(refs - {ref})
        return adjacency

    def _shortest_point_path(self, start, end):
        start = (int(start[0]), int(start[1]))
        end = (int(end[0]), int(end[1]))
        adjacency = self._point_adjacency()
        if start not in adjacency or end not in adjacency:
            return ()

        queue = deque([start])
        previous = {start: None}
        while queue:
            current = queue.popleft()
            if current == end:
                break
            for candidate in sorted(adjacency[current]):
                if candidate in previous:
                    continue
                previous[candidate] = current
                queue.append(candidate)

        if end not in previous:
            return ()
        path = []
        current = end
        while current is not None:
            path.append(current)
            current = previous[current]
        path.reverse()
        return tuple(path)

    def _select_control_ref(self, ref, *, shift=False, ctrl=False):
        ref = (int(ref[0]), int(ref[1]))
        if not self._expand_shared_endpoint_refs(ref):
            return False
        if self._selected_points is None:
            self._selected_points = set()

        targets = set()
        if ctrl and self._active_anchor is not None:
            targets.update(self._shortest_point_path(self._active_anchor, ref))
        if not targets:
            targets.add(ref)

        expanded = set()
        for target in targets:
            expanded.update(self._expand_shared_endpoint_refs(target))

        if shift:
            if ctrl:
                self._selected_points.update(expanded)
            elif ref in self._selected_points:
                self._selected_points.difference_update(expanded)
            else:
                self._selected_points.update(expanded)
        else:
            self._selected_points = expanded

        self._selected_guides = set()
        if self._selected_points:
            self._selected_control = ref
            self._active_anchor = ref
        else:
            self._selected_control = None
            self._active_anchor = None
        return True

    def _select_guide_edge_index(self, guide_index, *, shift=False):
        guide_index = int(guide_index)
        if not (0 <= guide_index < len(self._guides)):
            return False
        selected = set(self._selected_guides or ())
        if shift:
            if guide_index in selected:
                selected.remove(guide_index)
            else:
                selected.add(guide_index)
        else:
            selected = {guide_index}
        self._selected_guides = selected
        self._selected_points = set()
        self._selected_control = None
        self._active_anchor = None
        return True

    def _clear_selection(self):
        self._selected_control = None
        self._selected_guides = set()
        self._selected_points = set()
        self._active_anchor = None

    def _selected_point_refs(self):
        if self._selected_points:
            refs = set()
            for ref in self._selected_points:
                refs.update(self._expand_shared_endpoint_refs(ref))
            return tuple(sorted(refs))

        refs = set()
        if self._selected_guides:
            for guide_index in self._selection_guide_indices():
                refs.update(
                    (guide_index, point_index)
                    for point_index in range(
                        len(self._guides[guide_index].points_local)
                    )
                )
            return tuple(sorted(refs))

        if self._selected_control is None:
            return ()
        refs.update(
            self._expand_shared_endpoint_refs(self._selected_control)
        )
        return tuple(sorted(refs))

    def _selected_world(self):
        matrix = self._retopo.matrix_world
        return [
            matrix @ self._guides[guide_index].points_local[point_index]
            for guide_index, point_index in self._selected_point_refs()
        ]

    def _selected_guide_paths_world(self):
        matrix = self._retopo.matrix_world
        return [
            [matrix @ point for point in self._guides[index].points_local]
            for index in self._selection_guide_indices()
        ]

    def _sync_scene_settings(self):
        if self._scene is None:
            return
        settings = self._scene.flowpatch_retopo
        settings.u_segments = int(self._u_segments)
        settings.v_segments = int(self._v_segments)
        settings.last_effective_rows = int(self._v_segments)
        settings.session_active = True
        settings.session_tool = str(self._mode)
        settings.session_guide_count = len(self._guides)
        settings.session_cell_count = len(self._previews)
        settings.session_active_cell = (
            self._active_preview_index + 1 if self._previews else 0
        )
        if self._validation_errors:
            settings.session_validation = self._validation_errors[0][1]
        else:
            settings.session_validation = ""
        sync_counts, sync_message = self._sync_state_counts()
        settings.session_sync_state = (
            f"{sync_counts[SYNC_PARAMETRIC]} Parametric | "
            f"{sync_counts[SYNC_FROZEN]} Frozen | "
            f"{sync_counts[SYNC_DETACHED]} Detached"
        )
        settings.session_sync_message = sync_message
        try:
            composite = read_composite_session(self._scene)
            if composite is None:
                settings.session_composite_state = ""
                settings.session_composite_message = ""
            else:
                resolve_composite_objects(
                    tuple(bpy.data.objects),
                    composite,
                )
                settings.session_composite_state = (
                    f"{len(composite.members)} Projects"
                )
                settings.session_composite_message = ""
        except ProjectStoreError as exc:
            settings.session_composite_state = "Composite Invalid"
            settings.session_composite_message = str(exc)
        settings.session_status = (
            "Exit armed: press Esc again."
            if self._exit_armed
            else "Drawing retained guides."
            if self._drawing
            else (
                f"Cell {self._active_preview_index + 1}/"
                f"{len(self._previews)} pending; Enter retries Build."
            )
            if self._previews
            else "Guides retained; close a logical cell to preview."
        )
        self._settings_signature = (
            int(settings.u_segments),
            int(settings.v_segments),
            str(settings.projection_mode),
            round(float(settings.surface_follow_strength), 6),
            round(float(settings.surface_tighten_strength), 6),
            int(settings.surface_smoothing_radius),
            round(float(settings.surface_offset), 6),
            round(float(settings.detail_ignore_threshold), 6),
        )

    def _sync_from_scene_settings(self):
        if self._scene is None:
            return False
        settings = self._scene.flowpatch_retopo
        signature = (
            int(settings.u_segments),
            int(settings.v_segments),
            str(settings.projection_mode),
            round(float(settings.surface_follow_strength), 6),
            round(float(settings.surface_tighten_strength), 6),
            int(settings.surface_smoothing_radius),
            round(float(settings.surface_offset), 6),
            round(float(settings.detail_ignore_threshold), 6),
        )
        if signature == self._settings_signature:
            return False
        self._u_segments = max(1, min(64, int(settings.u_segments)))
        self._v_segments = max(1, min(64, int(settings.v_segments)))
        self._rebuild_previews()
        return True

    def _toolbar_capabilities(self, context=None):
        has_guides = bool(self._guides)
        has_cells = bool(self._previews)
        has_built_cells = bool(self._built_cells)
        has_guide_selection = bool(
            self._selected_points
            or self._selected_guides
            or self._selected_control is not None
        )
        has_single_cell = len(self._previews or []) == 1
        capability_context = context or getattr(bpy, "context", None)
        has_selected_boundary = False
        if capability_context is not None:
            try:
                has_selected_boundary = _selected_open_boundary_available(
                    capability_context
                )
            except Exception:
                has_selected_boundary = False

        def available(state="AVAILABLE"):
            return CapabilityResult.available(state=state)

        def when(condition, reason_code, message, missing=(), state="AVAILABLE"):
            if condition:
                return available(state=state)
            return CapabilityResult.disabled(
                reason_code,
                message,
                missing=missing,
            )

        capabilities = {
            "DRAW": available(),
            "EDIT": when(
                has_guides,
                "GUIDES_REQUIRED",
                "Draw at least one retained guide first.",
                ("retained_guide",),
            ),
            "BUILD": when(
                has_cells,
                "VALID_CELL_REQUIRED",
                "Close a valid guide cell before building.",
                ("valid_pending_cell",),
            ),
            "DISSOLVE": when(
                has_cells,
                "VALID_CELL_REQUIRED",
                "Close a valid guide cell before dissolving.",
                ("valid_pending_cell",),
            ),
            "CUT": when(
                has_guides and not has_built_cells,
                "UNCOMMITTED_GUIDE_NETWORK_REQUIRED",
                "Cut Guides is available only before patch cells are committed.",
                ("retained_guides", "no_committed_cells"),
                state="EXPERIMENTAL",
            ),
            "TRIM": CapabilityResult.disabled(
                "DEFERRED_BATCH_17_TRIM_REFLOW",
                "Trim is disabled until welded boundary reflow is implemented.",
                ("trim_reflow",),
            ),
            "LOOP_CUT": CapabilityResult.disabled(
                "DEFERRED_BATCH_13_CONTOUR_TOOL",
                "Loop/Contour is disabled until the projected contour workflow is implemented.",
                ("line_project_contour",),
            ),
            "SURFACE_FOLLOW": when(
                has_single_cell,
                "SINGLE_PENDING_CELL_REQUIRED",
                "Surface Follow requires exactly one uncommitted guide cell.",
                ("one_pending_cell",),
            ),
            "SURFACE_TIGHTEN": when(
                has_single_cell,
                "SINGLE_PENDING_CELL_REQUIRED",
                "Surface Tighten requires exactly one uncommitted guide cell.",
                ("one_pending_cell",),
            ),
            "BOUNDARY": when(
                has_selected_boundary,
                "SELECTED_OPEN_BOUNDARY_REQUIRED",
                "Select one open mesh boundary before continuing from it.",
                ("selected_open_boundary",),
                state="EXPERIMENTAL",
            ),
            "DENSITY": when(
                has_cells,
                "VALID_CELL_REQUIRED",
                "Build or preview a valid cell first.",
                ("valid_pending_cell",),
            ),
            "RELAX": when(
                has_guide_selection,
                "GUIDE_SELECTION_REQUIRED",
                "Select one or more editable guides before relaxing.",
                ("selected_editable_guides",),
            ),
            "DELETE": when(
                has_guide_selection,
                "EXACT_GUIDE_SELECTION_REQUIRED",
                "Select one guide control, node, or edge before deleting.",
                ("selected_guide_element",),
            ),
            "MIRROR": available(state="EXPERIMENTAL"),
            "CONTROL_POINTS": available(),
        }
        return TOOL_REGISTRY.normalize(capabilities)

    def _update_renderer(self):
        if self._renderer is None:
            return
        self._renderer.preview = None
        self._renderer.preview_patches = list(self._previews)
        self._renderer.active_preview_index = (
            self._active_preview_index if self._previews else -1
        )
        self._renderer.guide_paths = guides_world(
            self._retopo,
            self._guides,
        )
        self._renderer.composite_guide_paths = (
            _composite_guide_paths_world(self._scene, self._retopo)
        )
        self._renderer.selected_guide_paths = (
            self._selected_guide_paths_world()
        )
        self._renderer.guide_endpoints = self._graph_node_world()
        self._renderer.control_points = (
            self._flatten_controls()
            if self._scene.flowpatch_retopo.show_control_points
            else []
        )
        self._renderer.node_points = self._selected_world()
        self._renderer.toolbar_active = self._mode
        settings = self._scene.flowpatch_retopo
        capabilities = self._toolbar_capabilities()

        def capability(identifier):
            return TOOL_REGISTRY.capability(identifier, capabilities)

        self._renderer.toolbar_items = [
            _toolbar_item(
                "DRAW",
                "01_draw_guide.png",
                "Draw Guide: add a retained surface guide; one guide creates no faces",
                active=self._mode == "DRAW",
                priority="PRIMARY",
            ),
            _toolbar_item(
                "EDIT",
                "02_edit_guides.png",
                "Edit Guides: select and move retained guide control points",
                enabled=capability("EDIT").enabled,
                active=self._mode == "EDIT",
                priority="PRIMARY",
                disabled_reason=capability("EDIT").message,
            ),
            _toolbar_item(
                "BUILD",
                "03_build_quad_patch.png",
                "Build Patch: retry the active valid guide cell as durable mesh",
                enabled=capability("BUILD").enabled,
                priority="PRIMARY",
                disabled_reason=capability("BUILD").message,
            ),
            _toolbar_item(
                "DISSOLVE",
                "20_adjacent_shared_cells.png",
                "Dissolve Patch: commit the connected guide cells as one welded "
                "quad patch while retaining their internal quad loops",
                enabled=capability("DISSOLVE").enabled,
                priority="PRIMARY",
                disabled_reason=capability("DISSOLVE").message,
            ),
            _toolbar_item(
                "CUT",
                "12_cut_trim_guide.png",
                "Cut Guides: draw a retained cutting guide through the guide network",
                enabled=capability("CUT").enabled,
                active=self._mode == "CUT",
                priority="PRIMARY",
                disabled_reason=capability("CUT").message,
            ),
            _toolbar_item(
                "TRIM",
                "12_cut_trim.png",
                "Trim: remove a selected side and rebuild its welded quad boundary",
                enabled=capability("TRIM").enabled,
                priority="PRIMARY",
                disabled_reason=capability("TRIM").message,
            ),
            _toolbar_item(
                "LOOP_CUT",
                "04_loop_cut.png",
                "Loop Cut: leave the guide session and start the committed-patch loop-cut tool",
                enabled=capability("LOOP_CUT").enabled,
                priority="PRIMARY",
                disabled_reason=capability("LOOP_CUT").message,
            ),
            _toolbar_item(
                "SURFACE_FOLLOW",
                "06_surface_fit_follow.png",
                "Surface Fit: follow the raw target surface, including dents and protrusions",
                enabled=capability("SURFACE_FOLLOW").enabled,
                active=settings.projection_mode == "RAW",
                disabled_reason=capability("SURFACE_FOLLOW").message,
            ),
            _toolbar_item(
                "SURFACE_TIGHTEN",
                "07_tighten_flatten_over_detail.png",
                "Surface Fit: tighten over small detail; Shift-click uses a frozen broad patch",
                enabled=capability("SURFACE_TIGHTEN").enabled,
                active=settings.projection_mode in {"SMOOTH", "FLATTENED"},
                disabled_reason=capability("SURFACE_TIGHTEN").message,
            ),
            _toolbar_item(
                "BOUNDARY",
                "08_continue_shared_boundary.png",
                "Continue On: register the selected existing mesh boundary as a guide side",
                enabled=capability("BOUNDARY").enabled,
                disabled_reason=capability("BOUNDARY").message,
            ),
            _toolbar_item(
                "DENSITY",
                "09_density_resolution.png",
                "Density: add U and V resolution; Shift-click removes resolution",
                enabled=capability("DENSITY").enabled,
                disabled_reason=capability("DENSITY").message,
            ),
            _toolbar_item(
                "RELAX",
                "10_relax_patch.png",
                "Relax Guides: redistribute guide points without changing Surface Fit",
                enabled=capability("RELAX").enabled,
                disabled_reason=capability("RELAX").message,
            ),
            _toolbar_item(
                "DELETE",
                "11_delete_cell.png",
                "Delete Guide: remove the exact selected control, node, or edge",
                enabled=capability("DELETE").enabled,
                disabled_reason=capability("DELETE").message,
            ),
            _toolbar_item(
                "MIRROR",
                "14_symmetry_mirror.png",
                "Mirror X: toggle the non-destructive X mirror for the retopo mesh",
                enabled=capability("MIRROR").enabled,
                active=settings.mirror_x,
                disabled_reason=capability("MIRROR").message,
            ),
            _toolbar_item(
                "CONTROL_POINTS",
                "18_edit_control_points.png",
                "Control Points: show or hide retained-guide points",
                active=settings.show_control_points,
            ),
        ]
        registry_issues = TOOL_REGISTRY.validate_ids(
            item.get("id", "") for item in self._renderer.toolbar_items
        )
        if registry_issues:
            SESSION_BREADCRUMBS.record(
                "toolbar_registry_mismatch",
                issues=registry_issues,
            )
        ready = len(self._previews)
        self._toolbar_summary = (
            f"{len(self._guides)} guides | "
            f"cell {self._active_preview_index + 1 if ready else 0}/{ready} | "
            f"U {self._u_segments} x V {self._v_segments}"
        )
        self._renderer.toolbar_status = self._toolbar_summary
        self._renderer.hud_title = ""
        self._renderer.hud_lines = []
        self._sync_scene_settings()
        self._tag_redraw()

    def _rebuild_previews(self, preferred_cycle_key=None):
        settings = self._scene.flowpatch_retopo
        projection_mode, follow_strength = _resolved_projection(settings)
        previous_previews = list(self._previews or [])
        previous_index = int(self._active_preview_index)
        if preferred_cycle_key is None and self._previews:
            preferred_cycle_key = self._previews[
                self._active_preview_index
            ].cycle_key
        validation_errors = []
        try:
            previews = build_uncommitted_previews(
                obj=self._retopo,
                guides=self._guides,
                built_cells=self._built_cells,
                projector=self._projector,
                u_segments=self._u_segments,
                v_segments=self._v_segments,
                projection_mode=projection_mode,
                surface_offset=settings.surface_offset,
                surface_follow_strength=follow_strength,
                surface_tighten_strength=settings.surface_tighten_strength,
                surface_smoothing_radius=settings.surface_smoothing_radius,
                detail_ignore_threshold=settings.detail_ignore_threshold,
                validation_errors=validation_errors,
                density_overrides=self._density_overrides,
                target_object_uuid=str(self._target_object_uuid),
                frontface_epsilon=settings.frontface_epsilon,
                max_projection_distance=settings.max_projection_distance,
                max_surface_step=settings.max_surface_step,
                normal_continuity_cos=settings.normal_continuity_cos,
                flat_target_tolerance=settings.flat_target_tolerance,
            )
        except GuideGraphBudgetError as exc:
            message = str(exc)
            self._previews = previous_previews
            self._active_preview_index = (
                min(previous_index, len(previous_previews) - 1)
                if previous_previews
                else 0
            )
            self._validation_errors = [("WORK_BUDGET", message)]
            SESSION_BREADCRUMBS.record(
                "preview_rebuild_rejected",
                reason_code="WORK_BUDGET",
                message=message,
                retained_preview_count=len(previous_previews),
            )
            if message != self._last_preview_warning:
                self.report({"WARNING"}, message)
                self._last_preview_warning = message
            self._update_renderer()
            return False
        rejected_projection_keys = {
            str(cycle_key)
            for cycle_key, message in validation_errors
            if "REJECTED_PROJECTION" in str(message)
        }
        preview_keys = {str(preview.cycle_key) for preview in previews}
        for previous in previous_previews:
            cycle_key = str(previous.cycle_key)
            if (
                cycle_key not in rejected_projection_keys
                or cycle_key in preview_keys
            ):
                continue
            retained = deepcopy(previous)
            retained.validation_status = "REJECTED_PROJECTION"
            previews.append(retained)
            preview_keys.add(cycle_key)
        self._previews = previews
        self._validation_errors = validation_errors
        SESSION_BREADCRUMBS.record(
            "preview_rebuilt",
            guide_count=_safe_count(self._guides),
            preview_count=len(previews),
            validation_count=len(validation_errors),
        )
        self._last_preview_warning = ""
        if self._previews and preferred_cycle_key is not None:
            self._active_preview_index = next(
                (
                    index
                    for index, preview in enumerate(self._previews)
                    if preview.cycle_key == preferred_cycle_key
                ),
                min(self._active_preview_index, len(self._previews) - 1),
            )
        elif self._previews:
            self._active_preview_index = min(
                self._active_preview_index,
                len(self._previews) - 1,
            )
        else:
            self._active_preview_index = 0
        self._update_renderer()
        return True

    def _auto_build_new_previews(self, context, previous_preview_keys):
        try:
            plan = plan_auto_build(
                self._previews,
                previous_preview_keys=previous_preview_keys,
                built_cell_keys=self._built_cells,
            )
        except AutoBuildPlanError as exc:
            message = f"Auto Build stopped safely: {exc}"
            self.report({"ERROR"}, message)
            SESSION_BREADCRUMBS.record(
                "auto_build_plan_rejected",
                reason_code=exc.reason_code,
                message=str(exc),
            )
            return 0

        SESSION_BREADCRUMBS.record(
            "auto_build_planned",
            eligible_cycle_keys=plan.cycle_keys,
            skipped=[item.as_dict() for item in plan.skipped],
        )
        if not plan.cycle_keys:
            return 0
        if not self._commit_ready_cells(
            context,
            cycle_keys=plan.cycle_keys,
            auto_build=True,
        ):
            SESSION_BREADCRUMBS.record(
                "auto_build_retained_for_retry",
                cycle_keys=plan.cycle_keys,
            )
            return 0
        SESSION_BREADCRUMBS.record(
            "auto_build_committed",
            cycle_keys=plan.cycle_keys,
        )
        return len(plan.cycle_keys)

    def _cycle_active_preview(self, delta=1):
        if not self._previews:
            self.report({"WARNING"}, "No valid preview cell is available.")
            return False
        self._active_preview_index = (
            self._active_preview_index + int(delta)
        ) % len(self._previews)
        self._update_renderer()
        return True

    def _finish_surface_stroke(self, context):
        state = _pen_state_of(self)
        if (
            state is not None
            and state.phase is not PenPhase.FINALIZING
            and not _pen_claim_finalize(self)
        ):
            SESSION_BREADCRUMBS.record(
                "stroke_finalize_rejected",
                reason_code="INVALID_PEN_STATE",
                pen_state=state.snapshot(),
            )
            return False
        if len(self._stroke_world) < 2:
            SESSION_BREADCRUMBS.record(
                "stroke_rejected",
                reason_code="INSUFFICIENT_PROJECTED_SAMPLES",
                stroke_serial=_safe_int(
                    getattr(self, "_stroke_serial", 0)
                ),
                sample_count=len(self._stroke_world),
            )
            self._clear_stroke()
            return False

        previous_preview_keys = tuple(
            str(preview.cycle_key)
            for preview in tuple(self._previews or ())
        )
        self._regularize_stroke(context)
        self._apply_frozen_stroke_snaps()
        self._push_history()
        try:
            append_guide_world(
                self._retopo,
                self._guides,
                self._stroke_world,
                context.scene.flowpatch_retopo.magnet_distance,
                endpoint_node_ids=self._stroke_endpoint_nodes,
                endpoint_snap_specs=(
                    self._stroke_snap_press,
                    self._stroke_snap_release,
                ),
            )
            self._refresh_surface_anchors()
        except (FlowPatchGeometryError, GuideGraphBudgetError) as exc:
            if self._history:
                self._restore_state(self._history.pop())
            self.report({"WARNING"}, str(exc))
            SESSION_BREADCRUMBS.record(
                "stroke_rejected",
                reason_code=type(exc).__name__,
                stroke_serial=_safe_int(
                    getattr(self, "_stroke_serial", 0)
                ),
                message=str(exc),
            )
            succeeded = False
        else:
            save_guides(self._retopo, self._guides)
            rebuilt = self._rebuild_previews()
            auto_built_count = (
                self._auto_build_new_previews(
                    context,
                    previous_preview_keys,
                )
                if rebuilt
                else 0
            )
            SESSION_BREADCRUMBS.record(
                "stroke_committed",
                stroke_serial=_safe_int(
                    getattr(self, "_stroke_serial", 0)
                ),
                guide_count=_safe_count(self._guides),
                preview_count=_safe_count(self._previews),
                auto_built_count=auto_built_count,
            )
            succeeded = True
        self._clear_stroke()
        return succeeded

    def _clear_stroke(self):
        self._stroke_world = []
        self._stroke_screen = []
        self._stroke_anchors = []
        self._stroke_filter_mouse = None
        self._stroke_event_mouse = None
        self._stroke_endpoint_nodes = [0, 0]
        self._stroke_snap_press = None
        self._stroke_snap_release = None
        _pen_reset(self)
        _clear_hover_snap(self, "STROKE_RESET")
        if self._renderer is not None:
            self._renderer.stroke = []
        self._tag_redraw()

    def _nearest_control(self, mouse_region, radius_px=13.0):
        best = None
        matrix = self._retopo.matrix_world
        for guide_index, guide in enumerate(self._guides):
            for point_index, point_local in enumerate(guide.points_local):
                point_2d = view3d_utils.location_3d_to_region_2d(
                    self._region,
                    self._region_3d,
                    matrix @ point_local,
                )
                if point_2d is None:
                    continue
                distance = (Vector(point_2d) - mouse_region).length
                if distance <= radius_px and (
                    best is None or distance < best[0]
                ):
                    best = (distance, guide_index, point_index)
        if best is None:
            return None
        return best[1], best[2]

    def _select_linked_guides(self, mouse_region):
        hit = self._nearest_guide_segment(mouse_region, radius_px=18.0)
        seed_index = (
            int(hit["guide_index"])
            if hit is not None
            else int(self._selected_control[0])
            if self._selected_control is not None
            else -1
        )
        selected = connected_guide_indices(self._guides, seed_index)
        if not selected:
            self.report({"INFO"}, "Hover a retained guide, then press L.")
            return False
        self._selected_guides = set(selected)
        self._selected_points = {
            (guide_index, point_index)
            for guide_index in selected
            for point_index in range(
                len(self._guides[guide_index].points_local)
            )
        }
        self._selected_control = None
        self._active_anchor = (
            min(self._selected_points) if self._selected_points else None
        )
        self._mode = "EDIT"
        self._update_renderer()
        self.report(
            {"INFO"},
            f"Selected linked guide island ({len(selected)} guide(s)).",
        )
        return True

    def _anchor_for_ref(self, ref):
        guide_index, point_index = ref
        guide = self._guides[guide_index]
        if len(guide.anchors) != len(guide.points_local):
            return None
        return guide.anchors[point_index]

    def _set_anchor_for_ref(self, ref, anchor):
        if anchor is None:
            return
        guide_index, point_index = ref
        guide = self._guides[guide_index]
        if len(guide.anchors) != len(guide.points_local):
            return
        anchors = list(guide.anchors)
        anchors[point_index] = anchor.copy()
        guide.anchors = tuple(anchors)

    def _project_transform_world(self, context, point_world, ref):
        settings = context.scene.flowpatch_retopo
        previous_anchor = self._anchor_for_ref(ref)
        nearest = self._projector.nearest_world_continuous(
            point_world,
            target_object_uuid=str(self._target_object_uuid),
            previous_anchor=previous_anchor,
            surface_offset=settings.surface_offset,
            **_continuity_kwargs(settings),
        )
        if nearest is not None:
            self._set_anchor_for_ref(ref, nearest[4])
            return Vector(nearest[0])
        self._transform_projection_rejected = True
        last_valid = (
            self._projector.world_from_anchor(previous_anchor)
            if previous_anchor is not None
            else None
        )
        if last_valid is not None:
            return Vector(last_valid[0])
        return Vector(
            self._retopo.matrix_world
            @ self._guides[ref[0]].points_local[ref[1]]
        )

    def _hover_transform_refs(self, hit):
        if not isinstance(hit, dict):
            return ()
        kind = str(hit.get("kind", "")).upper()
        if kind == "CANONICAL_NODE":
            node_id = int(hit.get("node_id", 0))
            if node_id <= 0:
                return ()
            refs = set()
            for guide_index, guide in enumerate(self._guides):
                if int(guide.start_node) == node_id:
                    refs.add((guide_index, 0))
                if int(guide.end_node) == node_id:
                    refs.add(
                        (guide_index, len(guide.points_local) - 1)
                    )
            return tuple(sorted(refs))
        if kind != "GUIDE_EDGE":
            return ()

        guide_id = int(hit.get("guide_id", 0))
        guide_index = next(
            (
                index
                for index, guide in enumerate(self._guides)
                if int(guide.guide_id) == guide_id
            ),
            -1,
        )
        if guide_index < 0:
            return ()
        refs = set()
        for point_index in range(
            len(self._guides[guide_index].points_local)
        ):
            refs.update(
                self._expand_shared_endpoint_refs(
                    (guide_index, point_index)
                )
            )
        return tuple(sorted(refs))

    def _restore_selection_from_snapshot(self, snapshot):
        if not isinstance(snapshot, dict):
            self._clear_selection()
            return
        self._selected_control = snapshot.get("selected_control")
        self._selected_guides = set(
            snapshot.get("selected_guides", ())
        )
        self._selected_points = set(
            snapshot.get("selected_points", ())
        )
        self._active_anchor = snapshot.get("active_anchor")

    def _start_transform(self, context, event, mode):
        mode = str(mode).upper()
        persistent_snapshot = self._state_snapshot()
        selected_refs = self._selected_point_refs()
        hover_hit = None
        hover_refs = ()
        if not selected_refs:
            hover_hit = self._snap_candidate(
                self._region_mouse(event),
                _SNAP_HOVER_PX,
            )
            hover_refs = self._hover_transform_refs(hover_hit)
        decision = resolve_hover_transform(
            mode,
            selected_refs=selected_refs,
            hover_kind=(
                str(hover_hit.get("kind", ""))
                if hover_hit is not None
                else ""
            ),
            hover_refs=hover_refs,
        )
        if not decision.accepted:
            self.report({"INFO"}, decision.message)
            return False
        refs = decision.refs

        selected_indices = {guide_index for guide_index, _point_index in refs}
        if any(
            self._guide_is_locked(self._guides[index].guide_id)
            for index in selected_indices
        ):
            self.report(
                {"WARNING"},
                "This committed region is frozen; use the Sync controls.",
            )
            return False

        matrix = self._retopo.matrix_world
        initial_world = {
            ref: matrix @ self._guides[ref[0]].points_local[ref[1]]
            for ref in refs
        }
        center_world = sum(
            (point for point in initial_world.values()),
            Vector((0.0, 0.0, 0.0)),
        ) / len(initial_world)
        center_screen = view3d_utils.location_3d_to_region_2d(
            self._region,
            self._region_3d,
            center_world,
        )
        if center_screen is None:
            self.report({"WARNING"}, "The selected guides are outside this view.")
            return False

        anchor_world = (
            Vector(hover_hit["point_world"])
            if decision.temporary
            and hover_hit is not None
            and "point_world" in hover_hit
            else center_world
        )
        seed_anchor = self._anchor_for_ref(refs[0])
        surface_anchor = (
            self._projector.world_from_anchor(seed_anchor)
            if seed_anchor is not None
            else None
        )
        if surface_anchor is None:
            settings = context.scene.flowpatch_retopo
            surface_anchor = self._projector.nearest_world_continuous(
                anchor_world,
                target_object_uuid=str(self._target_object_uuid),
                previous_anchor=None,
                surface_offset=settings.surface_offset,
                **_continuity_kwargs(settings),
            )
        if surface_anchor is None:
            self.report(
                {"WARNING"},
                "FlowPatch could not establish a surface tangent for this transform.",
            )
            return False
        tangent_normal = Vector(surface_anchor[1])
        if tangent_normal.length <= 1.0e-8:
            self.report(
                {"WARNING"},
                "FlowPatch received an invalid surface normal for this transform.",
            )
            return False
        tangent_normal.normalize()

        if decision.temporary:
            self._selected_points = set(refs)
            self._selected_guides = set()
            self._selected_control = (
                refs[0]
                if str(hover_hit.get("kind", "")).upper()
                == "CANONICAL_NODE"
                else None
            )
            self._active_anchor = refs[0]

        origin_mouse = self._region_mouse(event)
        screen_delta = origin_mouse - Vector(center_screen)
        self._transform_snapshot = persistent_snapshot
        self._push_history(snapshot=persistent_snapshot)
        self._transform_mode = mode
        self._transform_refs = refs
        self._transform_initial_world = initial_world
        self._transform_center_world = center_world
        self._transform_origin_mouse = origin_mouse
        self._transform_origin_radius = max(1.0, screen_delta.length)
        self._transform_origin_angle = math.atan2(
            screen_delta.y,
            screen_delta.x,
        )
        self._transform_start_location = (
            view3d_utils.region_2d_to_location_3d(
                self._region,
                self._region_3d,
                origin_mouse,
                center_world,
            )
        )
        self._transform_tangent_normal = tangent_normal
        self._transform_temporary_selection = decision.temporary
        self._transform_return_mode = self._mode
        self._transform_projection_rejected = False
        self._exit_armed = False
        self._clear_hover_feedback("TRANSFORM_START")
        self._set_status(
            context,
            (
                f"FlowPatch {mode.title()}: move pointer; "
                "left click/Enter confirms, right click/Esc cancels"
            ),
        )
        self._update_renderer()
        return True

    def _apply_transform_event(self, context, event):
        if not self._transform_mode or not self._transform_refs:
            return False
        self._transform_projection_rejected = False
        mouse = self._region_mouse(event)
        transformed = {}
        mode = self._transform_mode
        if mode in {"MOVE", "SLIDE"}:
            current_location = view3d_utils.region_2d_to_location_3d(
                self._region,
                self._region_3d,
                mouse,
                self._transform_center_world,
            )
            delta = current_location - self._transform_start_location
            transformed = {
                ref: point + delta
                for ref, point in self._transform_initial_world.items()
            }
        elif mode == "SCALE":
            center_screen = view3d_utils.location_3d_to_region_2d(
                self._region,
                self._region_3d,
                self._transform_center_world,
            )
            if center_screen is None:
                return False
            factor = max(
                0.01,
                (mouse - Vector(center_screen)).length
                / self._transform_origin_radius,
            )
            transformed = {
                ref: self._transform_center_world
                + (point - self._transform_center_world) * factor
                for ref, point in self._transform_initial_world.items()
            }
        elif mode == "ROTATE":
            center_screen = view3d_utils.location_3d_to_region_2d(
                self._region,
                self._region_3d,
                self._transform_center_world,
            )
            if center_screen is None:
                return False
            delta = mouse - Vector(center_screen)
            angle = math.atan2(delta.y, delta.x) - self._transform_origin_angle
            tangent_normal = Vector(self._transform_tangent_normal)
            if tangent_normal.length <= 1.0e-8:
                return False
            tangent_normal.normalize()
            rotation = Matrix.Rotation(angle, 4, tangent_normal)
            transformed = {
                ref: self._transform_center_world
                + rotation @ (point - self._transform_center_world)
                for ref, point in self._transform_initial_world.items()
            }
        else:
            return False

        inverse = self._retopo.matrix_world.inverted_safe()
        for ref, point_world in transformed.items():
            guide_index, point_index = ref
            projected = self._project_transform_world(
                context,
                point_world,
                ref,
            )
            self._guides[guide_index].points_local[point_index] = (
                inverse @ projected
            )
        if self._transform_projection_rejected:
            self._set_status(
                context,
                "FlowPatch: REJECTED_PROJECTION; holding last valid preview",
            )
        else:
            self._set_status(
                context,
                (
                    f"FlowPatch {mode.title()}: move pointer; "
                    "left click/Enter confirms, right click/Esc cancels"
                ),
            )
        self._rebuild_previews()
        return True

    def _clear_transform_state(self):
        self._transform_mode = ""
        self._transform_snapshot = None
        self._transform_refs = None
        self._transform_initial_world = None
        self._transform_center_world = None
        self._transform_origin_mouse = None
        self._transform_start_location = None
        self._transform_tangent_normal = None
        self._transform_temporary_selection = False
        self._transform_return_mode = ""
        self._transform_projection_rejected = False

    def _rollback_transform_state(self, rebuild=True):
        if not self._transform_mode or self._transform_snapshot is None:
            return False
        return_mode = self._transform_return_mode or self._mode
        self._restore_state(self._transform_snapshot, rebuild=rebuild)
        if self._history:
            self._history.pop()
        self._mode = return_mode
        self._clear_transform_state()
        return True

    def _cancel_transform(self, context):
        if not self._rollback_transform_state(rebuild=True):
            return False
        self._clear_hover_feedback("TRANSFORM_CANCEL")
        self._update_renderer()
        self._set_status(
            context,
            "FlowPatch Surface Pen: transform cancelled; guides unchanged",
        )
        return True

    def _finish_transform(self, context):
        if not self._transform_mode:
            return False
        snapshot = self._transform_snapshot
        temporary = bool(self._transform_temporary_selection)
        return_mode = self._transform_return_mode or self._mode
        if self._transform_projection_rejected:
            self._restore_rejected_guide_edit(snapshot)
            self._mode = return_mode
            self._clear_transform_state()
            self._rebuild_previews()
            self._set_status(
                context,
                "FlowPatch: REJECTED_PROJECTION; transform rolled back",
            )
            return True
        changed_guide_ids = self._changed_guide_ids_from_snapshot(snapshot)
        sync_result = self._sync_mesh_from_current_guides(
            changed_guide_ids=changed_guide_ids,
        )
        if sync_result is None:
            self._restore_rejected_guide_edit(snapshot)
            self._mode = return_mode
            self._clear_transform_state()
            self._rebuild_previews()
            self._set_status(
                context,
                "FlowPatch: guide edit rolled back; built region is frozen",
            )
            return True
        if temporary:
            self._restore_selection_from_snapshot(snapshot)
        self._mode = return_mode
        self._clear_transform_state()
        self._rebuild_previews()
        self._set_status(
            context,
            "FlowPatch Surface Pen: transform applied; hover to draw or edit",
        )
        return True

    def _move_selected_to_event(self, context, event, isolated=False):
        if self._selected_control is None:
            return False
        guide_index, point_index = self._selected_control
        settings = context.scene.flowpatch_retopo
        hit = self._projector.raycast_region_continuous(
            self._region,
            self._region_3d,
            self._region_mouse(event),
            target_object_uuid=str(self._target_object_uuid),
            previous_anchor=self._anchor_for_ref(
                (guide_index, point_index)
            ),
            surface_offset=settings.surface_offset,
            frontface_epsilon=settings.frontface_epsilon,
            max_surface_step=settings.max_surface_step,
            normal_continuity_cos=settings.normal_continuity_cos,
        )
        if hit is None:
            self._set_status(
                context,
                "FlowPatch: REJECTED_PROJECTION; keeping last valid control",
            )
            return False

        guide = self._guides[guide_index]
        new_local = self._retopo.matrix_world.inverted_safe() @ Vector(hit[0])
        new_anchor = hit[4].copy()
        node_id = 0
        if point_index == 0:
            node_id = int(guide.start_node)
        elif point_index == len(guide.points_local) - 1:
            node_id = int(guide.end_node)

        original_local = guide.points_local[point_index].copy()
        delta_local = new_local - original_local

        def projected_local(ref, point_local):
            previous_anchor = self._anchor_for_ref(ref)
            nearest = self._projector.nearest_world_continuous(
                self._retopo.matrix_world @ point_local,
                target_object_uuid=str(self._target_object_uuid),
                previous_anchor=previous_anchor,
                surface_offset=settings.surface_offset,
                **_continuity_kwargs(settings),
            )
            if nearest is None:
                return None
            self._set_anchor_for_ref(ref, nearest[4])
            return (
                self._retopo.matrix_world.inverted_safe()
                @ Vector(nearest[0])
            )

        def move_nearby(candidate_index, anchor_index):
            candidate = self._guides[candidate_index]
            radius = max(2, min(6, len(candidate.points_local) // 3))
            source = [point.copy() for point in candidate.points_local]
            for local_index in range(1, len(source) - 1):
                distance = abs(local_index - anchor_index)
                if distance > radius:
                    continue
                factor = 1.0 - (distance / (radius + 1.0))
                factor = factor * factor * (3.0 - 2.0 * factor)
                moved = (
                    source[local_index]
                    + delta_local * factor
                )
                projected = projected_local(
                    (candidate_index, local_index),
                    moved,
                )
                if projected is not None:
                    candidate.points_local[local_index] = projected

        if isolated and node_id:
            for candidate_index, candidate in enumerate(self._guides):
                if candidate.start_node == node_id:
                    candidate.points_local[0] = new_local.copy()
                    self._set_anchor_for_ref(
                        (candidate_index, 0),
                        new_anchor,
                    )
                if candidate.end_node == node_id:
                    candidate.points_local[-1] = new_local.copy()
                    self._set_anchor_for_ref(
                        (candidate_index, len(candidate.points_local) - 1),
                        new_anchor,
                    )
        elif isolated:
            guide.points_local[point_index] = new_local.copy()
            self._set_anchor_for_ref(
                (guide_index, point_index),
                new_anchor,
            )
        elif node_id:
            for candidate_index, candidate in enumerate(self._guides):
                if candidate.start_node == node_id:
                    candidate.points_local[0] = new_local.copy()
                    self._set_anchor_for_ref(
                        (candidate_index, 0),
                        new_anchor,
                    )
                    move_nearby(candidate_index, 0)
                if candidate.end_node == node_id:
                    candidate.points_local[-1] = new_local.copy()
                    self._set_anchor_for_ref(
                        (candidate_index, len(candidate.points_local) - 1),
                        new_anchor,
                    )
                    move_nearby(
                        candidate_index,
                        len(candidate.points_local) - 1,
                    )
        else:
            move_nearby(guide_index, point_index)
            guide.points_local[point_index] = new_local.copy()
            self._set_anchor_for_ref(
                (guide_index, point_index),
                new_anchor,
            )
        self._rebuild_previews()
        return True

    def _relax_guides(self, context):
        if not self._guides:
            self.report({"WARNING"}, "Draw guides before relaxing them.")
            return
        guide_indices = list(self._selection_guide_indices())
        if not guide_indices:
            self.report(
                {"WARNING"},
                "Select one or more editable guides before relaxing.",
            )
            return
        if any(
            self._guide_is_locked(self._guides[index].guide_id)
            for index in guide_indices
        ):
            self.report(
                {"WARNING"},
                "This committed region is frozen; use the Sync controls.",
            )
            return

        try:
            cycles, _edge_nodes, _guide_by_id = find_bounded_regions(
                self._retopo,
                self._guides,
                self._projector,
            )
        except (FlowPatchGeometryError, GuideGraphBudgetError) as exc:
            self.report({"WARNING"}, f"Relax stopped: {exc}")
            return
        edge_use_count = {}
        for cycle in cycles:
            for edge_id in cycle.edge_ids:
                edge_use_count[int(edge_id)] = (
                    edge_use_count.get(int(edge_id), 0) + 1
                )
        shared_guide_ids = {
            edge_id for edge_id, count in edge_use_count.items() if count > 1
        }
        guide_indices = [
            index
            for index in guide_indices
            if int(self._guides[index].guide_id) not in shared_guide_ids
        ]
        if not guide_indices:
            self.report(
                {"INFO"},
                "Shared patch boundaries stay pinned during Relax.",
            )
            return

        self._push_history()
        snapshot = self._history[-1]
        settings = context.scene.flowpatch_retopo
        try:
            fair_guides_tangent(
                self._retopo,
                self._guides,
                guide_indices,
                self._projector,
                str(self._target_object_uuid),
                strength=settings.guide_fair_strength,
                iterations=settings.guide_fair_iterations,
                surface_offset=settings.surface_offset,
                **_continuity_kwargs(settings),
            )
        except FlowPatchGeometryError as exc:
            self._restore_rejected_guide_edit(snapshot)
            self._rebuild_previews()
            self.report({"WARNING"}, str(exc))
            return
        changed_guide_ids = self._changed_guide_ids_from_snapshot(snapshot)
        if self._sync_mesh_from_current_guides(
            changed_guide_ids=changed_guide_ids,
        ) is None:
            self._restore_rejected_guide_edit(snapshot)
            self._rebuild_previews()
            return
        self._rebuild_previews()

    def _selected_delete_target(self):
        if (
            self._selected_guides
            and not self._selected_points
            and self._selected_control is None
        ):
            if len(self._selected_guides) != 1:
                raise GuideDeleteError(
                    "MULTIPLE_EDGE_SELECTION",
                    "Select exactly one GuideEdge before deleting.",
                )
            guide_index = next(iter(self._selected_guides))
            if not (0 <= guide_index < len(self._guides)):
                raise GuideDeleteError(
                    "GUIDE_NOT_FOUND",
                    "The selected GuideEdge no longer exists.",
                )
            return "EDGE", int(guide_index), -1

        if self._selected_control is None:
            raise GuideDeleteError(
                "EXACT_SELECTION_REQUIRED",
                "Select one guide control, node, or edge before deleting.",
            )
        active = (
            int(self._selected_control[0]),
            int(self._selected_control[1]),
        )
        equivalent_refs = self._expand_shared_endpoint_refs(active)
        selected_refs = set(self._selected_points or (active,))
        if not selected_refs or not selected_refs.issubset(equivalent_refs):
            raise GuideDeleteError(
                "MULTIPLE_POINT_SELECTION",
                "Select exactly one guide control or one canonical node before deleting.",
            )
        return "POINT", active[0], active[1]

    def _built_cell_keys_for_guides(self, guide_ids):
        guide_ids = {int(value) for value in guide_ids}
        return tuple(
            sorted(
                str(cycle_key)
                for cycle_key, record in self._built_cells.items()
                if isinstance(record, dict)
                and str(record.get("state", "")).upper() != SYNC_DETACHED
                and guide_ids.intersection(
                    int(value) for value in record.get("edge_ids", ())
                )
            )
        )

    def _delete_guide(self, context):
        if not self._guides:
            self.report({"WARNING"}, "There are no guides to delete.")
            return False
        try:
            target_kind, guide_index, point_index = (
                self._selected_delete_target()
            )
        except GuideDeleteError as exc:
            self.report({"WARNING"}, str(exc))
            return False

        staged_guides = clone_guides(self._guides)
        try:
            if target_kind == "EDGE":
                guide_id = int(self._guides[guide_index].guide_id)
                owner_keys = self._built_cell_keys_for_guides((guide_id,))
                if len(owner_keys) > 1:
                    raise GuideDeleteError(
                        "SHARED_INTERIOR_EDGE_REQUIRES_REFLOW",
                        "This GuideEdge is shared by built regions; use Dissolve/Reflow.",
                    )
                result = delete_guide_edge(staged_guides, guide_id)
            else:
                result = delete_guide_point(
                    staged_guides,
                    guide_index,
                    point_index,
                )
        except GuideDeleteError as exc:
            SESSION_BREADCRUMBS.record(
                "guide_delete_rejected",
                reason_code=exc.reason_code,
                message=str(exc),
            )
            self.report({"WARNING"}, str(exc))
            return False

        affected_guide_ids = set(result.affected_guide_ids)
        if any(
            self._guide_is_locked(guide.guide_id)
            for guide in self._guides
            if int(guide.guide_id) in affected_guide_ids
        ):
            self.report(
                {"WARNING"},
                "A selected guide belongs to a frozen built region; use Sync.",
            )
            return False

        affected_cell_keys = self._built_cell_keys_for_guides(
            affected_guide_ids
        )
        try:
            snapshot = self._state_snapshot(include_mesh=True)
        except Exception as exc:
            self.report(
                {"ERROR"},
                f"Delete stopped before mutation: rollback snapshot failed: {exc}",
            )
            return False
        self._push_history(snapshot=snapshot)
        try:
            self._guides = staged_guides
            save_guides(self._retopo, self._guides)
            if result.target_kind == "CONTROL":
                if self._sync_mesh_from_current_guides(
                    changed_guide_ids=result.changed_guide_ids,
                ) is None:
                    raise FlowPatchGeometryError(
                        "The affected built region could not synchronize after control deletion."
                    )
            else:
                if affected_cell_keys:
                    bm = bmesh.from_edit_mesh(self._retopo.data)
                    removal = remove_built_cell_geometry(
                        self._retopo,
                        bm,
                        self._built_cells,
                        affected_cell_keys,
                    )
                    self._built_cells = removal.built_cells
                if not self._rebuild_previews():
                    raise FlowPatchGeometryError(
                        "Guide deletion exceeded the safe region-rebuild budget."
                    )
                changed_ids = {int(value) for value in result.changed_guide_ids}
                rebuild_keys = tuple(
                    str(preview.cycle_key)
                    for preview in self._previews
                    if str(preview.validation_status).upper() == "VALID"
                    and changed_ids.intersection(
                        int(value) for value in preview.edge_ids
                    )
                )
                if rebuild_keys and not self._commit_ready_cells(
                    context,
                    cycle_keys=rebuild_keys,
                    auto_build=True,
                    preserve_history=True,
                ):
                    raise FlowPatchGeometryError(
                        "A valid affected region could not be rebuilt after deletion."
                    )
            self._clear_selection()
            if not self._rebuild_previews():
                raise FlowPatchGeometryError(
                    "Guide deletion could not refresh the affected region preview."
                )
        except Exception as exc:
            try:
                self._restore_state(snapshot)
            finally:
                if self._history and self._history[-1] is snapshot:
                    self._history.pop()
                self._discard_state_snapshot(snapshot)
            SESSION_BREADCRUMBS.record(
                "guide_delete_rolled_back",
                target_kind=result.target_kind,
                message=str(exc),
            )
            self.report({"ERROR"}, f"Delete rolled back: {exc}")
            return False

        SESSION_BREADCRUMBS.record(
            "guide_delete_committed",
            target_kind=result.target_kind,
            changed_guide_ids=result.changed_guide_ids,
            removed_guide_ids=result.removed_guide_ids,
            affected_cell_keys=affected_cell_keys,
        )
        self.report({"INFO"}, result.message)
        return True

    def _adjust_density(self, axis, delta, push_history=True):
        if push_history:
            self._push_history()
        axis = str(axis).upper()
        if self._previews:
            active = self._previews[self._active_preview_index]
            record = self._density_overrides.setdefault(
                active.cycle_key,
                {},
            )
            current = (
                active.u_segments if axis == "U" else active.v_segments
            )
            record[axis] = max(1, min(64, current + int(delta)))
            self._rebuild_previews(preferred_cycle_key=active.cycle_key)
            return
        if axis == "U":
            self._u_segments = max(
                1,
                min(64, self._u_segments + int(delta)),
            )
        else:
            self._v_segments = max(
                1,
                min(64, self._v_segments + int(delta)),
            )
        self._rebuild_previews()

    def _capture_selected_boundary(self, context):
        bm = bmesh.from_edit_mesh(self._retopo.data)
        self._push_history()
        try:
            append_selected_mesh_boundary(
                self._retopo,
                bm,
                self._guides,
            )
        except FlowPatchGeometryError as exc:
            self._history.pop()
            self.report({"WARNING"}, str(exc))
            return False
        self._refresh_surface_anchors()
        save_guides(self._retopo, self._guides)
        self._clear_selection()
        self._rebuild_previews()
        self.report(
            {"INFO"},
            "Selected mesh boundary registered as a retained guide side.",
        )
        return True

    @staticmethod
    def _preview_components(previews):
        pending = list(previews)
        components = []
        while pending:
            component = [pending.pop(0)]
            side_keys = set(component[0].side_keys)
            changed = True
            while changed:
                changed = False
                for preview in list(pending):
                    if side_keys.intersection(preview.side_keys):
                        pending.remove(preview)
                        component.append(preview)
                        side_keys.update(preview.side_keys)
                        changed = True
            components.append(component)
        return components

    @staticmethod
    def _normalize_component_patch_ids(retopo, bm, results, components):
        result_by_cycle = {result.cycle_key: result for result in results}
        vert_layer = bm.verts.layers.int.get(VERTEX_LAYER_NAME)
        edge_layer = bm.edges.layers.int.get(EDGE_LAYER_NAME)
        face_layer = bm.faces.layers.int.get(FACE_LAYER_NAME)

        for component in components:
            component_results = [
                result_by_cycle[preview.cycle_key]
                for preview in component
                if preview.cycle_key in result_by_cycle
            ]
            if not component_results:
                continue
            patch_ids = {int(result.patch_id) for result in component_results}
            component_patch_id = min(patch_ids)
            if len(patch_ids) > 1:
                if vert_layer is not None:
                    for vert in bm.verts:
                        if int(vert[vert_layer]) in patch_ids:
                            vert[vert_layer] = component_patch_id
                if edge_layer is not None:
                    for edge in bm.edges:
                        if int(edge[edge_layer]) in patch_ids:
                            edge[edge_layer] = component_patch_id
                if face_layer is not None:
                    for face in bm.faces:
                        if int(face[face_layer]) in patch_ids:
                            face[face_layer] = component_patch_id
            for result in component_results:
                result.patch_id = component_patch_id
                result.cell_record["patch_id"] = component_patch_id

        bmesh.update_edit_mesh(
            retopo.data,
            loop_triangles=True,
            destructive=False,
        )

    def _commit_ready_cells(
        self,
        context,
        commit_all=False,
        rebuild=True,
        dissolve=False,
        cycle_keys=None,
        auto_build=False,
        preserve_history=False,
    ):
        if not self._previews:
            self.report(
                {"WARNING"},
                "No geometry created: close a connected guide cell first.",
            )
            return False
        if cycle_keys is not None and (commit_all or dissolve):
            raise ValueError(
                "An explicit Build selection cannot also request Dissolve."
            )
        selected_cycle_keys = tuple(
            str(value) for value in tuple(cycle_keys or ())
        )
        if cycle_keys is not None and not selected_cycle_keys:
            return False

        if cycle_keys is not None:
            preview_by_key = {
                str(preview.cycle_key): preview
                for preview in self._previews
            }
            if len(preview_by_key) != len(self._previews):
                self.report(
                    {"ERROR"},
                    "Build stopped: duplicate preview ownership was detected.",
                )
                return False
            missing = [
                key for key in selected_cycle_keys
                if key not in preview_by_key
            ]
            if missing:
                self.report(
                    {"ERROR"},
                    "Build stopped: a selected preview is no longer available.",
                )
                return False
            selected_previews = [
                preview_by_key[key] for key in selected_cycle_keys
            ]
        else:
            active_preview = self._previews[self._active_preview_index]
            if commit_all:
                selected_previews = list(self._previews)
            elif dissolve:
                selected_previews = next(
                    component
                    for component in self._preview_components(self._previews)
                    if any(
                        preview.cycle_key == active_preview.cycle_key
                        for preview in component
                    )
                )
            else:
                selected_previews = [active_preview]

        rejected_previews = [
            preview
            for preview in selected_previews
            if str(preview.validation_status).upper() != "VALID"
        ]
        if rejected_previews:
            self.report(
                {"WARNING"},
                "Build stopped: REJECTED_PROJECTION retained the last valid "
                "preview without committing it.",
            )
            return False

        bm = bmesh.from_edit_mesh(self._retopo.data)
        rollback_mesh = None
        rollback_properties = None
        rollback_runtime = None
        settings = context.scene.flowpatch_retopo
        if auto_build:
            try:
                rollback_mesh = _begin_auto_build_mesh_snapshot(bm)
                rollback_properties = _capture_flowpatch_id_properties(
                    self._retopo
                )
                rollback_runtime = {
                    "built_cells": deepcopy(self._built_cells),
                    "density_overrides": deepcopy(self._density_overrides),
                    "previews": list(self._previews),
                    "active_preview_index": int(self._active_preview_index),
                    "validation_errors": list(self._validation_errors),
                    "last_preview_warning": str(self._last_preview_warning),
                    "u_segments": int(settings.u_segments),
                    "v_segments": int(settings.v_segments),
                    "last_patch_id": int(settings.last_patch_id),
                    "last_effective_rows": int(
                        settings.last_effective_rows
                    ),
                }
            except Exception as exc:
                _discard_auto_build_mesh_snapshot(rollback_mesh)
                self.report(
                    {"ERROR"},
                    f"Auto Build deferred; use Build Patch to retry: {exc}",
                )
                SESSION_BREADCRUMBS.record(
                    "auto_build_snapshot_rejected",
                    message=str(exc),
                )
                return False
        try:
            components = self._preview_components(selected_previews)
            results = commit_guide_patches(
                self._retopo,
                bm,
                selected_previews,
                merge_distance=max(
                    1.0e-5,
                    context.scene.flowpatch_retopo.magnet_distance,
                ),
                guides=self._guides,
            )
            self._normalize_component_patch_ids(
                self._retopo,
                bm,
                results,
                components,
            )
            updated_built_cells = deepcopy(self._built_cells)
            updated_density_overrides = deepcopy(self._density_overrides)
            for result in results:
                updated_built_cells[result.cycle_key] = deepcopy(
                    result.cell_record
                )
                updated_density_overrides.pop(result.cycle_key, None)
            save_built_cells(self._retopo, updated_built_cells)
            self._built_cells = updated_built_cells
            self._density_overrides = updated_density_overrides
            settings.u_segments = self._u_segments
            settings.v_segments = self._v_segments
            settings.last_patch_id = results[-1].patch_id
            settings.last_effective_rows = self._v_segments
            if rebuild:
                self._rebuild_previews()
        except Exception as exc:
            if auto_build:
                try:
                    _restore_auto_build_mesh_snapshot(
                        self._retopo,
                        bm,
                        rollback_mesh,
                        rollback_properties,
                    )
                    self._built_cells = rollback_runtime["built_cells"]
                    self._density_overrides = rollback_runtime[
                        "density_overrides"
                    ]
                    self._previews = rollback_runtime["previews"]
                    self._active_preview_index = rollback_runtime[
                        "active_preview_index"
                    ]
                    self._validation_errors = rollback_runtime[
                        "validation_errors"
                    ]
                    self._last_preview_warning = rollback_runtime[
                        "last_preview_warning"
                    ]
                    settings.u_segments = rollback_runtime["u_segments"]
                    settings.v_segments = rollback_runtime["v_segments"]
                    settings.last_patch_id = rollback_runtime["last_patch_id"]
                    settings.last_effective_rows = rollback_runtime[
                        "last_effective_rows"
                    ]
                    self._update_renderer()
                except Exception as rollback_exc:
                    SESSION_BREADCRUMBS.record(
                        "auto_build_rollback_failed",
                        message=str(rollback_exc),
                    )
                    raise RuntimeError(
                        "Auto Build failed and its rollback could not "
                        "restore the exact prior state."
                    ) from rollback_exc
            elif not isinstance(exc, FlowPatchGeometryError):
                raise
            self.report({"ERROR"}, str(exc))
            SESSION_BREADCRUMBS.record(
                "build_failed",
                build_kind="AUTO" if auto_build else "MANUAL",
                message=str(exc),
                retained_preview_count=len(self._previews),
            )
            return False
        finally:
            _discard_auto_build_mesh_snapshot(rollback_mesh)

        if not preserve_history:
            self._discard_history_stack(self._history)
            self._discard_history_stack(self._redo_history)
        try:
            bpy.ops.ed.undo_push(
                message=(
                    "FlowPatch Dissolve"
                    if dissolve or commit_all
                    else "FlowPatch Auto Build"
                    if auto_build
                    else "FlowPatch Build"
                )
            )
        except RuntimeError:
            pass
        self.report(
            {"INFO"},
            (
                (
                    f"Dissolved {len(results)} connected guide cell(s) into "
                    f"{len(components)} welded quad patch group(s), "
                    if dissolve or commit_all
                    else f"Auto-built {len(results)} valid guide cell(s), "
                    if auto_build
                    else f"Built {len(results)} closed guide cell(s), "
                )
                + f"{sum(result.created_faces for result in results)} face(s)."
            ),
        )
        SESSION_BREADCRUMBS.record(
            "build_committed",
            build_kind=(
                "DISSOLVE"
                if dissolve or commit_all
                else "AUTO"
                if auto_build
                else "MANUAL"
            ),
            cycle_keys=tuple(result.cycle_key for result in results),
            created_faces=sum(result.created_faces for result in results),
        )
        return True

    def _preserve_guides(self):
        if self._retopo is None:
            return False
        try:
            save_guides(self._retopo, self._guides or [])
            save_built_cells(self._retopo, self._built_cells or {})
            return True
        except Exception:
            return False

    def _finalize_session(self, context, commit_pending=False):
        if self._transform_mode:
            self._rollback_transform_state(rebuild=True)
        SESSION_BREADCRUMBS.record(
            "session_finalize",
            session_epoch=_safe_int(getattr(self, "_session_epoch", 0)),
            commit_pending=bool(commit_pending),
            guide_count=_safe_count(self._guides),
            preview_count=_safe_count(self._previews),
        )
        committed = True
        if (
            commit_pending
            and self._previews
            and _edit_mesh_poll(context)
            and context.edit_object is self._retopo
        ):
            committed = self._commit_ready_cells(
                context,
                commit_all=True,
                rebuild=False,
            )
        self._preserve_guides()
        self._stop_requested = True
        self._cleanup(context)
        return committed

    def _handle_toolbar(self, context, action, event=None):
        capability = TOOL_REGISTRY.capability(
            action,
            self._toolbar_capabilities(context),
        )
        SESSION_BREADCRUMBS.record(
            "toolbar_action",
            tool_id=str(action),
            enabled=capability.enabled,
            state=capability.state,
            reason_code=capability.reason_code,
        )
        if not capability.enabled:
            self.report({"WARNING"}, capability.message)
            return "KEEP"
        if action in {"DRAW", "EDIT", "CUT"}:
            self._mode = action
            self._exit_armed = False
            self._clear_selection()
            self._set_cursor(context)
            self._update_renderer()
            return "KEEP"
        if action == "BUILD":
            self._commit_ready_cells(context)
            return "KEEP"
        if action == "DISSOLVE":
            self._commit_ready_cells(context, dissolve=True)
            return "KEEP"
        if action == "LOOP_CUT":
            self._preserve_guides()
            self._stop_requested = True
            self._cleanup(context)
            bpy.ops.flowpatch.loop_cut_patch("INVOKE_DEFAULT")
            return "FINISH"
        if action == "SURFACE_FOLLOW":
            self._scene.flowpatch_retopo.projection_mode = "RAW"
            self._rebuild_previews()
            return "KEEP"
        if action == "SURFACE_TIGHTEN":
            self._scene.flowpatch_retopo.projection_mode = (
                "FLATTENED"
                if event is not None and bool(event.shift)
                else "SMOOTH"
            )
            self._rebuild_previews()
            return "KEEP"
        if action == "BOUNDARY":
            self._capture_selected_boundary(context)
            return "KEEP"
        if action == "DENSITY":
            delta = -1 if event is not None and bool(event.shift) else 1
            self._adjust_density("U", delta)
            self._adjust_density("V", delta, push_history=False)
            return "KEEP"
        if action == "RELAX":
            self._relax_guides(context)
            return "KEEP"
        if action == "DELETE":
            self._delete_guide(context)
            return "KEEP"
        if action == "MIRROR":
            settings = self._scene.flowpatch_retopo
            settings.mirror_x = not settings.mirror_x
            self._update_renderer()
            return "KEEP"
        if action == "UNDO":
            self._undo_guide_edit()
            return "KEEP"
        if action == "REDO":
            self._redo_guide_edit()
            return "KEEP"
        if action == "CONTROL_POINTS":
            settings = self._scene.flowpatch_retopo
            settings.show_control_points = not settings.show_control_points
            if settings.show_control_points:
                self._mode = "EDIT"
            self._update_renderer()
            return "KEEP"
        return "KEEP"

    def _cleanup(self, context):
        if self._cleaned:
            return
        SESSION_BREADCRUMBS.record(
            "session_cleanup",
            session_epoch=_safe_int(getattr(self, "_session_epoch", 0)),
            pointer_state=str(getattr(self, "_pointer_state", "")),
            stop_requested=bool(getattr(self, "_stop_requested", False)),
            drawing=bool(getattr(self, "_drawing", False)),
            mouse_captured=bool(getattr(self, "_mouse_captured", False)),
        )
        if _pen_active(self):
            self._cancel_surface_stroke("SESSION_CLEANUP")
        else:
            _pen_reset(self)
        if self._transform_mode:
            self._rollback_transform_state(rebuild=False)
        self._discard_history_stack(getattr(self, "_history", None))
        self._discard_history_stack(getattr(self, "_redo_history", None))
        self._clear_hover_feedback("SESSION_CLEANUP")
        self._session_alive = False
        self._stroke_snap_press = None
        self._stroke_snap_release = None
        self._cleaned = True
        renderer = self._renderer
        self._renderer = None
        if renderer is not None:
            try:
                renderer.remove()
            except Exception:
                pass
        projector = self._projector
        self._projector = None
        if projector is not None:
            try:
                projector.close()
            except Exception:
                pass
        if self._retopo is not None:
            try:
                self._retopo.show_in_front = False
                self._retopo["flowpatch_session_active"] = False
            except Exception:
                pass
        try:
            self._set_status(context, None)
        except Exception:
            pass
        self._restore_cursor(context)
        if self._scene is not None:
            try:
                settings = self._scene.flowpatch_retopo
                settings.session_active = False
                settings.session_tool = ""
                settings.session_status = "Idle"
                settings.session_validation = ""
                settings.session_guide_count = 0
                settings.session_cell_count = 0
                settings.session_active_cell = 0
                settings.session_sync_state = ""
                settings.session_sync_message = ""
            except Exception:
                pass
        try:
            self._tag_redraw()
        except Exception:
            pass
        if self.__class__._active_instance is self:
            self.__class__._active_instance = None
        self._area = None
        self._region = None
        self._region_3d = None
        self._retopo = None
        self._target = None
        self._scene = None
        self._guides = None
        self._built_cells = None
        self._previews = None
        self._density_overrides = None
        self._stroke_world = None
        self._stroke_screen = None
        self._stroke_filter_mouse = None
        self._hover_snap_state = None
        self._stroke_event_mouse = None
        self._history = None
        self._redo_history = None
        self._move_snapshot = None
        self._transform_snapshot = None
        self._transform_refs = None
        _pen_reset(self)

    def invoke(self, context, event):
        global _SESSION_EPOCH
        active_instance = self.__class__._active_instance
        SESSION_BREADCRUMBS.record(
            "session_invoke",
            context_mode=str(getattr(context, "mode", "")),
            edit_object=_object_name(getattr(context, "edit_object", None)),
            existing_active_session=active_instance is not None,
        )
        if active_instance is not None and active_instance is not self:
            SESSION_BREADCRUMBS.record(
                "session_start_rejected",
                reason_code="SESSION_ALREADY_ACTIVE",
            )
            self.report({"WARNING"}, "FlowPatch is already running in this window.")
            return {"CANCELLED"}

        self._cleaned = False
        self._stop_requested = False
        self._cursor_active = False
        self._scene = context.scene
        self._retopo = context.edit_object
        self._project_uuid = ""
        self._retopo_object_uuid = ""
        self._retopo_object_name_hint = _object_name(self._retopo)
        self._target_object_uuid = ""
        self._target_object_name_hint = ""
        try:
            self._target = self._resolve_target(context)
        except ProjectStoreError as exc:
            SESSION_BREADCRUMBS.record(
                "session_start_rejected",
                reason_code=exc.reason_code,
                message=str(exc),
            )
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}
        if self._target is None or self._target.type != "MESH":
            SESSION_BREADCRUMBS.record(
                "session_start_rejected",
                reason_code="TARGET_REQUIRED",
            )
            self.report({"ERROR"}, "Choose a mesh Surface before launching FlowPatch.")
            return {"CANCELLED"}
        if self._target is self._retopo:
            SESSION_BREADCRUMBS.record(
                "session_start_rejected",
                reason_code="TARGET_EQUALS_RETOPO",
            )
            self.report(
                {"ERROR"},
                "The retopo mesh and projection Surface must be different objects.",
            )
            return {"CANCELLED"}
        try:
            project_record = _bind_project_or_error(
                self._target,
                self._retopo,
            )
        except FlowPatchGeometryError as exc:
            SESSION_BREADCRUMBS.record(
                "project_resume_rejected",
                reason_code="PROJECT_BINDING_CONFLICT",
                message=str(exc),
            )
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}
        _remember_session_identity(
            self,
            project_record,
            self._target,
            self._retopo,
        )
        context.scene.flowpatch_retopo.continue_on = self._retopo
        _run_project_audit("GUIDE_SESSION_START")

        self._area = context.area
        self._region = next(
            (region for region in context.area.regions if region.type == "WINDOW"),
            None,
        )
        self._region_3d = context.space_data.region_3d
        if self._region is None or self._region_3d is None:
            SESSION_BREADCRUMBS.record(
                "session_start_rejected",
                reason_code="VIEWPORT_REGION_REQUIRED",
            )
            self.report({"ERROR"}, "A 3D Viewport window region is required.")
            return {"CANCELLED"}
        try:
            self._retopo.show_in_front = True
            self._projector = SurfaceProjector(
                self._target,
                context.evaluated_depsgraph_get(),
            )
            self._guides = load_guides(self._retopo)
            self._built_cells = load_built_cells(self._retopo)
            self._density_overrides = {}
            self._previews = []
            self._stroke_world = []
            self._stroke_screen = []
            self._stroke_anchors = []
            self._stroke_filter_mouse = None
            self._stroke_event_mouse = None
            self._stroke_endpoint_nodes = [0, 0]
            self._pen_state = PenState()
            _sync_pen_state_fields(self)
            self._mode = "DRAW"
            self._u_segments = max(
                1,
                int(context.scene.flowpatch_retopo.u_segments),
            )
            self._v_segments = max(
                1,
                int(context.scene.flowpatch_retopo.v_segments),
            )
            self._selected_control = None
            self._selected_guides = set()
            self._selected_points = set()
            self._active_anchor = None
            self._dragging_control = False
            self._g_move = False
            self._clear_transform_state()
            self._history = []
            self._redo_history = []
            self._move_snapshot = None
            self._validation_errors = []
            self._last_preview_warning = ""
            self._exit_armed = False
            self._active_preview_index = 0
            self._settings_signature = None
            self._hover_segment = None
            self._hover_snap_state = HoverSnapState()
            self._refresh_surface_anchors()
            save_guides(self._retopo, self._guides)
            self._sync_guides_from_current_mesh(
                force=False,
                rebuild=False,
                report_result=False,
            )
            _SESSION_EPOCH += 1
            self._session_epoch = _SESSION_EPOCH
            self._session_alive = True
            SESSION_BREADCRUMBS.record(
                "session_initialized",
                session_epoch=self._session_epoch,
                retopo_object=_object_name(self._retopo),
                target_object=_object_name(self._target),
                project_uuid=self._project_uuid,
                retopo_object_uuid=self._retopo_object_uuid,
                target_object_uuid=self._target_object_uuid,
                guide_count=_safe_count(self._guides),
                built_cell_count=_safe_count(self._built_cells),
            )
            self._stroke_snap_press = None
            self._stroke_snap_release = None
            session_epoch = self._session_epoch
            self.__class__._active_instance = self
            self._renderer = FlowPatchPreviewRenderer(
                session_guard=lambda: (
                    self._session_alive
                    and not self._cleaned
                    and self._session_epoch == session_epoch
                    and self.__class__._active_instance is self
                )
            )
            self._renderer.opacity = (
                context.scene.flowpatch_retopo.preview_opacity
            )
            self._rebuild_previews()
            self._renderer.install()
            if self._mouse_in_region(event):
                self._set_cursor(context)
            self._set_status(
                context,
                (
                    "FlowPatch: draw retained guide sides; closed cells preview "
                    "and auto-build; Enter retries pending Build; F7 stops"
                ),
            )
            context.window_manager.modal_handler_add(self)
            self._retopo["flowpatch_session_active"] = True
            SESSION_BREADCRUMBS.record(
                "session_started",
                session_epoch=self._session_epoch,
                toolbar_ids=TOOL_REGISTRY.ids,
            )
            self._tag_redraw()
            return {"RUNNING_MODAL"}
        except Exception as exc:
            SESSION_BREADCRUMBS.record(
                "session_start_failed",
                error_type=type(exc).__name__,
                message=str(exc),
            )
            self.report({"ERROR"}, f"FlowPatch could not start: {exc}")
            self._cleanup(context)
            return {"CANCELLED"}

    def _modal_impl(self, context, event):
        if self._cleaned or self._stop_requested:
            self._cleanup(context)
            return {"FINISHED"}

        if event.type == "F7" and event.value == "PRESS":
            self._finalize_session(context, commit_pending=False)
            return {"FINISHED"}

        if not _edit_mesh_poll(context) or context.edit_object is not self._retopo:
            self._suspend_for_native_ui(context)
            return {"PASS_THROUGH"}

        self._sync_from_scene_settings()

        if (
            _pen_active(self)
            and event.type == "LEFTMOUSE"
            and event.value == "RELEASE"
            and not self._mouse_in_region(event)
        ):
            if _pen_claim_finalize(self):
                self._finish_surface_stroke(context)
            return {"RUNNING_MODAL"}

        if self._mouse_over_native_ui(event):
            if event.type == "LEFTMOUSE" and event.value == "PRESS":
                self._suspend_for_native_ui(context)
            else:
                self._restore_cursor(context)
            return {"PASS_THROUGH"}

        if (
            event.type == "LEFTMOUSE"
            and event.value == "PRESS"
            and self._mouse_in_region(event)
            and self._renderer is not None
        ):
            mouse = self._region_mouse(event)
            action = self._renderer.toolbar_hit_test(mouse)
            if action is not None:
                self._cancel_surface_stroke("TOOLBAR_ACTION")
                self._cancel_transform(context)
                self._exit_armed = False
                outcome = self._handle_toolbar(context, action, event)
                if outcome == "FINISH":
                    return {"FINISHED"}
                return {"RUNNING_MODAL"}

        if not self._mouse_in_region(event):
            self._restore_cursor(context)
            return {"PASS_THROUGH"}
        if not self._cursor_active:
            self._set_cursor(context)

        if event.type == "ESC" and event.value == "PRESS":
            if self._cancel_transform(context):
                self._exit_armed = False
                return {"RUNNING_MODAL"}
            if self._cancel_surface_stroke("ESC"):
                self._exit_armed = False
                self._update_renderer()
                return {"RUNNING_MODAL"}
            if self._g_move and self._move_snapshot is not None:
                self._restore_state(self._move_snapshot)
                if self._history:
                    self._history.pop()
                self._g_move = False
                self._move_snapshot = None
                self._exit_armed = False
                return {"RUNNING_MODAL"}
            if not self._exit_armed:
                self._exit_armed = True
                self._update_renderer()
                self.report({"INFO"}, "Press Esc again to exit FlowPatch.")
                return {"RUNNING_MODAL"}
            self._finalize_session(context, commit_pending=False)
            return {"FINISHED"}

        if event.type == "Z" and event.value == "PRESS" and event.ctrl:
            self._exit_armed = False
            if event.shift and self._redo_guide_edit():
                return {"RUNNING_MODAL"}
            if not event.shift and self._undo_guide_edit():
                return {"RUNNING_MODAL"}
            return {"PASS_THROUGH"}

        if event.type == "D" and event.value == "PRESS":
            self._mode = "DRAW"
            self._exit_armed = False
            self._clear_selection()
            self._set_cursor(context)
            self._update_renderer()
            return {"RUNNING_MODAL"}

        if event.type == "TAB" and event.value == "PRESS":
            self._finalize_session(context, commit_pending=False)
            return {"PASS_THROUGH"}

        if event.type == "B" and event.value == "PRESS":
            self._exit_armed = False
            self._capture_selected_boundary(context)
            return {"RUNNING_MODAL"}

        if event.type == "L" and event.value == "PRESS":
            self._exit_armed = False
            self._select_linked_guides(self._region_mouse(event))
            return {"RUNNING_MODAL"}

        if event.type in {"LEFT_BRACKET", "RIGHT_BRACKET"} and event.value == "PRESS":
            self._exit_armed = False
            self._adjust_density(
                "V" if event.shift else "U",
                -1 if event.type == "LEFT_BRACKET" else 1,
            )
            return {"RUNNING_MODAL"}

        if event.type == "P" and event.value == "PRESS":
            self._exit_armed = False
            settings = self._scene.flowpatch_retopo
            modes = ("RAW", "SMOOTH", "FLATTENED")
            current = modes.index(settings.projection_mode)
            settings.projection_mode = modes[(current + 1) % len(modes)]
            self._rebuild_previews()
            return {"RUNNING_MODAL"}

        if event.type == "R" and event.value == "PRESS":
            self._exit_armed = False
            if self._start_transform(context, event, "ROTATE"):
                return {"RUNNING_MODAL"}
            return {"PASS_THROUGH"}

        if event.type == "S" and event.value == "PRESS":
            self._exit_armed = False
            if self._start_transform(context, event, "SCALE"):
                return {"RUNNING_MODAL"}
            return {"PASS_THROUGH"}

        if event.type == "G" and event.value == "PRESS":
            self._exit_armed = False
            if self._transform_mode == "MOVE":
                self._transform_mode = "SLIDE"
                self._set_status(
                    context,
                    "FlowPatch Surface Slide: projected movement; click/Enter confirms",
                )
                return {"RUNNING_MODAL"}
            if self._transform_mode:
                return {"RUNNING_MODAL"}
            if self._start_transform(context, event, "MOVE"):
                return {"RUNNING_MODAL"}
            return {"PASS_THROUGH"}

        if event.type in {"BACK_SPACE", "DEL", "X"} and event.value == "PRESS":
            self._exit_armed = False
            self._delete_guide(context)
            return {"RUNNING_MODAL"}

        if event.type in {"RET", "NUMPAD_ENTER"} and event.value == "PRESS":
            self._exit_armed = False
            if self._finish_transform(context):
                return {"RUNNING_MODAL"}
            self._commit_ready_cells(context)
            return {"RUNNING_MODAL"}

        if event.type in {
            "MIDDLEMOUSE",
            "WHEELUPMOUSE",
            "WHEELDOWNMOUSE",
            "NDOF_MOTION",
        }:
            return {"PASS_THROUGH"}

        if event.type == "RIGHTMOUSE" and event.value == "PRESS":
            if self._cancel_surface_stroke("RMB"):
                self._exit_armed = False
                self._update_renderer()
                return {"RUNNING_MODAL"}
            if self._cancel_transform(context):
                self._exit_armed = False
                return {"RUNNING_MODAL"}
            if self._g_move and self._move_snapshot is not None:
                self._restore_state(self._move_snapshot)
                if self._history:
                    self._history.pop()
                self._g_move = False
                self._move_snapshot = None
                self._exit_armed = False
                return {"RUNNING_MODAL"}
            return {"PASS_THROUGH"}

        if event.type == "MOUSEMOVE":
            mouse = self._region_mouse(event)
            over_toolbar = self._set_toolbar_hover(mouse)
            if _pen_can_append(self):
                self._append_surface_sample(context, event)
                return {"RUNNING_MODAL"}
            if self._transform_mode:
                self._apply_transform_event(context, event)
                return {"RUNNING_MODAL"}
            if self._dragging_control or self._g_move:
                self._move_selected_to_event(
                    context,
                    event,
                    isolated=bool(event.shift),
                )
                return {"RUNNING_MODAL"}
            if over_toolbar:
                self._clear_hover_feedback("TOOLBAR_HOVER")
            else:
                self._update_hover(mouse)
            return {"RUNNING_MODAL"}

        if event.type == "LEFTMOUSE":
            if not self._mouse_in_region(event):
                return {"PASS_THROUGH"}

            mouse = self._region_mouse(event)
            if event.value == "PRESS":
                if self._finish_transform(context):
                    return {"RUNNING_MODAL"}

                if self._g_move:
                    snapshot = self._move_snapshot
                    changed_guide_ids = (
                        self._changed_guide_ids_from_snapshot(snapshot)
                        if snapshot is not None
                        else ()
                    )
                    if self._sync_mesh_from_current_guides(
                        changed_guide_ids=changed_guide_ids,
                    ) is None:
                        if snapshot is not None:
                            self._restore_rejected_guide_edit(snapshot)
                        self._g_move = False
                        self._move_snapshot = None
                        self._rebuild_previews()
                        return {"RUNNING_MODAL"}
                    self._g_move = False
                    self._move_snapshot = None
                    self._rebuild_previews()
                    return {"RUNNING_MODAL"}

                if self._mode in {"DRAW", "CUT"}:
                    if _pen_active(self):
                        SESSION_BREADCRUMBS.record(
                            "stroke_start_rejected",
                            reason_code="STROKE_ALREADY_ACTIVE",
                            stroke_serial=_safe_int(
                                getattr(self, "_stroke_serial", 0)
                            ),
                        )
                        return {"RUNNING_MODAL"}
                    self._clear_stroke()
                    transition = _pen_begin(self)
                    if not transition.accepted:
                        SESSION_BREADCRUMBS.record(
                            "stroke_start_rejected",
                            reason_code=transition.reason_code,
                            pen_state=transition.as_dict(),
                        )
                        return {"RUNNING_MODAL"}
                    SESSION_BREADCRUMBS.record(
                        "stroke_started",
                        stroke_serial=_safe_int(
                            getattr(self, "_stroke_serial", 0)
                        ),
                        mode=str(getattr(self, "_mode", "")),
                    )
                    self._stroke_snap_press = self._freeze_snap_candidate(
                        mouse,
                        _SNAP_COMMIT_PX,
                    )
                    self._exit_armed = False
                    self._append_surface_sample(context, event, force=True)
                    return {"RUNNING_MODAL"}

                selected = self._nearest_control(mouse)
                self._exit_armed = False
                if selected is None:
                    edge_hit = (
                        self._nearest_guide_segment(mouse, radius_px=11.0)
                        if not event.ctrl
                        else None
                    )
                    if edge_hit is not None:
                        self._select_guide_edge_index(
                            edge_hit["guide_index"],
                            shift=event.shift,
                        )
                        self._mode = "EDIT"
                        self._update_renderer()
                        return {"RUNNING_MODAL"}
                    if not event.shift and not event.ctrl:
                        self._clear_selection()
                    self._update_renderer()
                    return {"RUNNING_MODAL"}
                if not self._select_control_ref(
                    selected,
                    shift=event.shift,
                    ctrl=event.ctrl,
                ):
                    return {"RUNNING_MODAL"}
                self._update_renderer()
                guide = self._guides[selected[0]]
                if self._guide_is_locked(guide.guide_id):
                    self.report(
                        {"WARNING"},
                        "This committed region is frozen; use the Sync controls.",
                    )
                    return {"RUNNING_MODAL"}
                if event.shift or event.ctrl:
                    return {"RUNNING_MODAL"}
                self._push_history()
                self._dragging_control = True
                return {"RUNNING_MODAL"}

            if event.value == "RELEASE":
                if (
                    self._mode in {"DRAW", "CUT"}
                    and _pen_active(self)
                ):
                    self._stroke_snap_release = self._freeze_snap_candidate(
                        mouse,
                        _SNAP_COMMIT_PX,
                    )
                    self._append_surface_sample(context, event, force=True)
                    if _pen_claim_finalize(self):
                        self._finish_surface_stroke(context)
                    return {"RUNNING_MODAL"}
                if self._dragging_control:
                    self._dragging_control = False
                    snapshot = self._history[-1] if self._history else None
                    changed_guide_ids = (
                        self._changed_guide_ids_from_snapshot(snapshot)
                        if snapshot is not None
                        else ()
                    )
                    if self._sync_mesh_from_current_guides(
                        changed_guide_ids=changed_guide_ids,
                    ) is None:
                        if snapshot is not None:
                            self._restore_rejected_guide_edit(snapshot)
                        self._rebuild_previews()
                        return {"RUNNING_MODAL"}
                    self._rebuild_previews()
                    return {"RUNNING_MODAL"}

        return {"PASS_THROUGH"}

    def modal(self, context, event):
        try:
            return self._modal_impl(context, event)
        except Exception as exc:
            SESSION_BREADCRUMBS.record(
                "modal_exception",
                session_epoch=_safe_int(
                    getattr(self, "_session_epoch", 0)
                ),
                event_type=str(getattr(event, "type", "")),
                error_type=type(exc).__name__,
                message=str(exc),
            )
            self.report({"ERROR"}, f"FlowPatch session stopped safely: {exc}")
            self._preserve_guides()
            self._cleanup(context)
            return {"CANCELLED"}

    def cancel(self, context):
        SESSION_BREADCRUMBS.record(
            "session_cancel",
            session_epoch=_safe_int(getattr(self, "_session_epoch", 0)),
        )
        self._preserve_guides()
        self._cleanup(context)


class FLOWPATCH_OT_copy_debug_state(Operator):
    bl_idname = "flowpatch.copy_debug_state"
    bl_label = "Copy FlowPatch Debug State"
    bl_description = (
        "Copy a sanitized FlowPatch session, capability, and breadcrumb snapshot"
    )
    bl_options = {"REGISTER"}

    @classmethod
    def poll(cls, context):
        try:
            area = safe_rna_attr(context, "area", None)
            return bool(
                safe_rna_attr(context, "window_manager", None) is not None
                and safe_rna_attr(area, "type", "") == "VIEW_3D"
            )
        except (ReferenceError, RuntimeError):
            return False

    def execute(self, context):
        session = FLOWPATCH_OT_guide_session._active_instance
        SESSION_BREADCRUMBS.record(
            "debug_state_requested",
            session_active=session is not None,
        )
        try:
            document = build_debug_state_snapshot(context)
            text = debug_document_json(document)
            context.window_manager.clipboard = text
        except Exception as exc:
            SESSION_BREADCRUMBS.record(
                "debug_state_copy_failed",
                error_type=type(exc).__name__,
                message=str(exc),
            )
            self.report(
                {"ERROR"},
                f"FlowPatch debug state could not be copied: {exc}",
            )
            return {"CANCELLED"}
        self.report(
            {"INFO"},
            f"Copied FlowPatch debug state ({len(text)} characters).",
        )
        return {"FINISHED"}


class _FLOWPATCH_OT_sync_base:
    @classmethod
    def poll(cls, context):
        session = FLOWPATCH_OT_guide_session._active_instance
        return (
            session is not None
            and bool(getattr(session, "_session_alive", False))
            and not bool(getattr(session, "_cleaned", True))
            and context.mode == "EDIT_MESH"
            and context.edit_object is getattr(session, "_retopo", None)
        )


class FLOWPATCH_OT_sync_mesh_from_guides(
    _FLOWPATCH_OT_sync_base,
    Operator,
):
    bl_idname = "flowpatch.sync_mesh_from_guides"
    bl_label = "Sync Mesh From Guides"
    bl_description = (
        "Explicitly regenerate compatible parametric mesh positions from guides"
    )
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        session = FLOWPATCH_OT_guide_session._active_instance
        result = session._sync_mesh_from_current_guides(force=True)
        session._rebuild_previews()
        if result is None:
            return {"CANCELLED"}
        self.report({"INFO"}, result.message)
        return {"FINISHED"}


class FLOWPATCH_OT_sync_guides_from_mesh(
    _FLOWPATCH_OT_sync_base,
    Operator,
):
    bl_idname = "flowpatch.sync_guides_from_mesh"
    bl_label = "Sync Guides From Mesh"
    bl_description = (
        "Read supported UID-bound mesh position edits back into guide controls"
    )
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        session = FLOWPATCH_OT_guide_session._active_instance
        result = session._sync_guides_from_current_mesh(
            force=True,
            rebuild=True,
            report_result=False,
        )
        if result is None:
            return {"CANCELLED"}
        level = {"WARNING"} if result.frozen_cell_keys else {"INFO"}
        self.report(level, result.message)
        return {"FINISHED"}


class FLOWPATCH_OT_detach_frozen_sync(
    _FLOWPATCH_OT_sync_base,
    Operator,
):
    bl_idname = "flowpatch.detach_frozen_sync"
    bl_label = "Detach Frozen Regions"
    bl_description = (
        "Keep frozen mesh and guides persistent but stop bidirectional updates"
    )
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        session = FLOWPATCH_OT_guide_session._active_instance
        result = detach_frozen_sync(
            session._retopo,
            session._built_cells,
        )
        session._built_cells = result.built_cells
        session._rebuild_previews()
        self.report({"INFO"}, result.message)
        return {"FINISHED"}


class FLOWPATCH_OT_capture_boundary(Operator):
    bl_idname = "flowpatch.capture_boundary"
    bl_label = "Use Selected Boundary"
    bl_description = (
        "Register the selected open mesh boundary as one retained logical side"
    )
    bl_options = {"REGISTER"}

    @classmethod
    def poll(cls, context):
        return (
            _edit_mesh_poll(context)
            and FLOWPATCH_OT_guide_session._active_instance is not None
        )

    def execute(self, context):
        session = FLOWPATCH_OT_guide_session._active_instance
        if session is None:
            self.report({"ERROR"}, "Launch the FlowPatch guide session first.")
            return {"CANCELLED"}
        if not session._capture_selected_boundary(context):
            return {"CANCELLED"}
        return {"FINISHED"}


class FLOWPATCH_OT_toggle_tool(Operator):
    bl_idname = "flowpatch.toggle_tool"
    bl_label = "Toggle FlowPatch"
    bl_description = (
        "Start or stop the retained-guide FlowPatch tool; a single line never "
        "creates geometry"
    )
    bl_options = {"REGISTER"}

    @classmethod
    def poll(cls, context):
        return (
            context.area is not None
            and context.area.type == "VIEW_3D"
            and context.mode in {"OBJECT", "EDIT_MESH"}
        )

    def invoke(self, context, _event):
        active_session = FLOWPATCH_OT_guide_session._active_instance
        if active_session is not None:
            try:
                stopped, _left_edit_mode = _stop_active_session(
                    context,
                    leave_edit_mode=True,
                )
            except Exception as exc:
                SESSION_BREADCRUMBS.record(
                    "session_stop_failed",
                    error_type=type(exc).__name__,
                    message=str(exc),
                    launch="F7",
                )
                self.report({"ERROR"}, f"FlowPatch could not stop safely: {exc}")
                return {"CANCELLED"}
            if not stopped:
                self.report({"WARNING"}, "No active FlowPatch session is available to stop.")
                return {"CANCELLED"}
            self.report({"INFO"}, "FlowPatch session stopped; geometry was preserved.")
            return {"FINISHED"}

        if context.mode == "EDIT_MESH":
            try:
                target = _resolve_session_target(
                    context,
                    context.edit_object,
                )
            except ProjectStoreError as exc:
                SESSION_BREADCRUMBS.record(
                    "session_start_rejected",
                    reason_code=exc.reason_code,
                    message=str(exc),
                    launch="F7",
                )
                self.report({"ERROR"}, str(exc))
                return {"CANCELLED"}
            if target is None or target.type != "MESH":
                SESSION_BREADCRUMBS.record(
                    "session_start_rejected",
                    reason_code="TARGET_REQUIRED",
                    launch="F7",
                )
                self.report(
                    {"ERROR"},
                    (
                        "Choose a mesh Surface in the FlowPatch panel "
                        "before pressing F7."
                    ),
                )
                return {"CANCELLED"}
            if target is context.edit_object:
                SESSION_BREADCRUMBS.record(
                    "session_start_rejected",
                    reason_code="TARGET_EQUALS_RETOPO",
                    launch="F7",
                )
                self.report(
                    {"ERROR"},
                    (
                        "The retopo mesh and projection Surface must be "
                        "different objects."
                    ),
                )
                return {"CANCELLED"}

        try:
            if context.mode == "OBJECT":
                settings = context.scene.flowpatch_retopo
                active = context.active_object
                target = _start_target(settings, active)
                if target is None:
                    self.report({"ERROR"}, "Select the projection mesh first.")
                    return {"CANCELLED"}
                result = bpy.ops.flowpatch.start_session(
                    "EXEC_DEFAULT",
                    surface_name=target.name,
                    launch_modal=True,
                )
                if "FINISHED" not in result:
                    return {"CANCELLED"}
                return {"FINISHED"}

            result = bpy.ops.flowpatch.guide_session("INVOKE_DEFAULT")
        except RuntimeError as exc:
            message = str(exc).strip()
            if message.startswith("Error: "):
                message = message[7:].strip()
            message = message or "FlowPatch could not start."
            SESSION_BREADCRUMBS.record(
                "session_start_rejected",
                reason_code="NESTED_OPERATOR_CANCELLED",
                message=message,
                launch="F7",
            )
            self.report({"ERROR"}, message)
            return {"CANCELLED"}
        if "RUNNING_MODAL" not in result:
            return {"CANCELLED"}
        return {"FINISHED"}


class FLOWPATCH_OT_seed_patch(Operator):
    bl_idname = "flowpatch.seed_patch"
    bl_label = "Sketch First Patch"
    bl_description = (
        "Retired one-stroke strip experiment; FlowPatch V1 uses retained guides"
    )
    bl_options = {"REGISTER", "UNDO"}

    _area = None
    _region = None
    _region_3d = None
    _renderer = None
    _projector = None
    _stroke_world = None
    _stroke_screen = None
    _preview = None
    _drawing = False
    _initial_tool = None
    _width_px = 48.0

    @classmethod
    def poll(cls, _context):
        return False

    def _set_status(self, context, text):
        try:
            context.workspace.status_text_set(text)
        except Exception:
            pass

    def _tag_redraw(self):
        if self._area is not None:
            self._area.tag_redraw()

    def _update_hud(self):
        if self._renderer is None:
            return
        self._renderer.hud_title = "FLOWPATCH / FIRST PATCH"
        self._renderer.hud_lines = [
            "Drag LMB on the high-poly surface",
            f"[ / ]  Patch width: {self._width_px:.0f} px",
            "Enter  Commit    Backspace  Redraw    Esc  Cancel",
        ]

    def _cleanup(self, context):
        if self._renderer is not None:
            self._renderer.remove()
            self._renderer = None
        if self._projector is not None:
            self._projector.close()
            self._projector = None
        self._set_status(context, None)
        self._tag_redraw()

    def _mouse_in_region(self, event):
        if self._region is None:
            return False
        return (
            self._region.x <= event.mouse_x < self._region.x + self._region.width
            and self._region.y <= event.mouse_y < self._region.y + self._region.height
        )

    def _region_mouse(self, event):
        return Vector(
            (
                event.mouse_x - self._region.x,
                event.mouse_y - self._region.y,
            )
        )

    def _append_surface_sample(self, context, event, force=False):
        mouse = self._region_mouse(event)
        settings = context.scene.flowpatch_retopo
        if self._stroke_screen and not force:
            if (mouse - self._stroke_screen[-1]).length < settings.sample_spacing_px:
                return False
        hit = self._projector.raycast_region(
            self._region,
            self._region_3d,
            mouse,
            settings.surface_offset,
        )
        if hit is None:
            return False
        self._stroke_screen.append(mouse)
        self._stroke_world.append(Vector(hit[0]))
        self._renderer.stroke = list(self._stroke_world)
        self._renderer.control_points = (
            list(self._stroke_world) if settings.show_control_points else []
        )
        self._tag_redraw()
        return True

    def _rebuild_preview(self, context):
        if len(self._stroke_screen) < 2:
            raise FlowPatchGeometryError("Draw a longer stroke on the surface.")
        length = sum(
            (end - start).length
            for start, end in zip(self._stroke_screen, self._stroke_screen[1:])
        )
        segments = max(2, min(24, round(length / 28.0)))
        settings = context.scene.flowpatch_retopo
        self._preview = build_seed_preview(
            projector=self._projector,
            region=self._region,
            region_3d=self._region_3d,
            stroke_screen=self._stroke_screen,
            segment_count=segments,
            width_px=self._width_px,
            surface_offset=settings.surface_offset,
        )
        self._renderer.preview = self._preview
        self._tag_redraw()

    def _clear_preview(self):
        self._stroke_world.clear()
        self._stroke_screen.clear()
        self._preview = None
        self._renderer.stroke = []
        self._renderer.control_points = []
        self._renderer.preview = None
        self._drawing = False
        self._tag_redraw()

    def invoke(self, context, _event):
        settings = context.scene.flowpatch_retopo
        target = settings.target
        if target is None or target.type != "MESH":
            self.report({"ERROR"}, "Choose a high-poly Surface first.")
            return {"CANCELLED"}
        if target is context.edit_object:
            self.report(
                {"ERROR"},
                (
                    "You are editing the high-poly surface. Return to Object Mode "
                    "and click Start New Retopo."
                ),
            )
            return {"CANCELLED"}

        self._area = context.area
        self._region = next(
            (region for region in context.area.regions if region.type == "WINDOW"),
            None,
        )
        self._region_3d = context.space_data.region_3d
        if self._region is None or self._region_3d is None:
            self.report({"ERROR"}, "A 3D Viewport window region is required.")
            return {"CANCELLED"}

        try:
            self._projector = SurfaceProjector(
                target, context.evaluated_depsgraph_get()
            )
        except Exception as exc:
            self.report({"ERROR"}, f"Could not build target projection: {exc}")
            return {"CANCELLED"}

        self._stroke_world = []
        self._stroke_screen = []
        self._preview = None
        self._drawing = False
        self._initial_tool = _active_tool_id(context)
        self._width_px = 48.0
        self._renderer = FlowPatchPreviewRenderer()
        self._renderer.opacity = settings.preview_opacity
        self._update_hud()
        self._renderer.install()
        self._set_status(
            context,
            "FlowPatch: drag the first strip, Enter confirms, Esc cancels",
        )
        context.window_manager.modal_handler_add(self)
        self._tag_redraw()
        return {"RUNNING_MODAL"}

    def modal(self, context, event):
        if not _edit_mesh_poll(context):
            self._cleanup(context)
            return {"CANCELLED"}
        current_tool = _active_tool_id(context)
        if (
            self._initial_tool is not None
            and current_tool is not None
            and current_tool != self._initial_tool
        ):
            self._cleanup(context)
            return {"CANCELLED"}

        if event.type == "ESC" and event.value == "PRESS":
            self._cleanup(context)
            return {"CANCELLED"}

        if event.type in {"LEFT_BRACKET", "RIGHT_BRACKET"} and event.value == "PRESS":
            delta = -4.0 if event.type == "LEFT_BRACKET" else 4.0
            self._width_px = max(8.0, min(160.0, self._width_px + delta))
            self._update_hud()
            if self._preview is not None:
                try:
                    self._rebuild_preview(context)
                except FlowPatchGeometryError as exc:
                    self.report({"WARNING"}, str(exc))
            return {"RUNNING_MODAL"}

        if event.type in {"RET", "NUMPAD_ENTER"} and event.value == "PRESS":
            if self._preview is None:
                self.report({"WARNING"}, "Draw a valid first patch before confirming.")
                return {"RUNNING_MODAL"}
            try:
                bm = bmesh.from_edit_mesh(context.edit_object.data)
                result = commit_seed_patch(context.edit_object, bm, self._preview)
            except FlowPatchGeometryError as exc:
                self.report({"ERROR"}, str(exc))
                self._cleanup(context)
                return {"CANCELLED"}
            settings = context.scene.flowpatch_retopo
            if hasattr(settings, "last_patch_id"):
                settings.last_patch_id = result.patch_id
            self.report(
                {"INFO"},
                f"FlowPatch {result.patch_id}: {result.created_faces} starter quads",
            )
            self._cleanup(context)
            return {"FINISHED"}

        if event.type == "BACK_SPACE" and event.value == "PRESS":
            self._clear_preview()
            return {"RUNNING_MODAL"}

        if event.type in {
            "MIDDLEMOUSE",
            "WHEELUPMOUSE",
            "WHEELDOWNMOUSE",
            "NDOF_MOTION",
        }:
            return {"PASS_THROUGH"}

        if event.type == "LEFTMOUSE":
            if not self._mouse_in_region(event):
                return {"PASS_THROUGH"}
            if event.value == "PRESS":
                self._clear_preview()
                self._drawing = True
                self._append_surface_sample(context, event, force=True)
                return {"RUNNING_MODAL"}
            if event.value == "RELEASE" and self._drawing:
                self._append_surface_sample(context, event, force=True)
                self._drawing = False
                try:
                    self._rebuild_preview(context)
                except FlowPatchGeometryError as exc:
                    self.report({"WARNING"}, str(exc))
                    self._preview = None
                    self._renderer.preview = None
                return {"RUNNING_MODAL"}

        if event.type == "MOUSEMOVE" and self._drawing:
            self._append_surface_sample(context, event)
            return {"RUNNING_MODAL"}

        return {"PASS_THROUGH"}

    def cancel(self, context):
        self._cleanup(context)


class FLOWPATCH_OT_draw_extension(Operator):
    bl_idname = "flowpatch.draw_extension"
    bl_label = "Draw Quad Extension"
    bl_description = (
        "Retired one-stroke strip experiment; FlowPatch V1 uses retained guides"
    )
    bl_options = {"REGISTER", "UNDO"}

    _area = None
    _region = None
    _region_3d = None
    _renderer = None
    _projector = None
    _boundary = None
    _stroke_world = None
    _stroke_screen = None
    _preview = None
    _drawing = False
    _initial_tool = None

    @classmethod
    def poll(cls, _context):
        return False

    def _set_status(self, context, text):
        try:
            context.workspace.status_text_set(text)
        except Exception:
            pass

    def _tag_redraw(self):
        if self._area is not None:
            self._area.tag_redraw()

    def _cleanup(self, context):
        if self._renderer is not None:
            self._renderer.remove()
            self._renderer = None
        if self._projector is not None:
            self._projector.close()
            self._projector = None
        self._set_status(context, None)
        self._tag_redraw()

    def _mouse_in_region(self, event):
        if self._region is None:
            return False
        return (
            self._region.x <= event.mouse_x < self._region.x + self._region.width
            and self._region.y <= event.mouse_y < self._region.y + self._region.height
        )

    def _region_mouse(self, event):
        return (
            event.mouse_x - self._region.x,
            event.mouse_y - self._region.y,
        )

    def _append_surface_sample(self, context, event, force=False):
        mouse = self._region_mouse(event)
        settings = context.scene.flowpatch_retopo
        if self._stroke_screen and not force:
            previous = self._stroke_screen[-1]
            distance = math.hypot(mouse[0] - previous[0], mouse[1] - previous[1])
            if distance < settings.sample_spacing_px:
                return False

        hit = self._projector.raycast_region(
            self._region,
            self._region_3d,
            mouse,
            settings.surface_offset,
        )
        if hit is None:
            return False
        self._stroke_screen.append(mouse)
        self._stroke_world.append(Vector(hit[0]))
        self._renderer.stroke = list(self._stroke_world)
        self._renderer.control_points = (
            list(self._stroke_world) if settings.show_control_points else []
        )
        self._tag_redraw()
        return True

    def _effective_rows(self, settings):
        if settings.density_mode != "TARGET_GROUP":
            return settings.rows, None

        density = self._projector.mean_vertex_group_world(
            self._stroke_world,
            settings.density_group,
        )
        if density is None:
            return (
                settings.rows,
                (
                    f"Density group '{settings.density_group}' was not sampled; "
                    f"using {settings.rows} manual rows"
                ),
            )

        minimum = min(settings.density_min_rows, settings.density_max_rows)
        maximum = max(settings.density_min_rows, settings.density_max_rows)
        rows = round(minimum + density * (maximum - minimum))
        return max(1, rows), None

    def _rebuild_preview(self, context):
        settings = context.scene.flowpatch_retopo
        boundary_world = [
            context.edit_object.matrix_world @ vert.co
            for vert in self._boundary.verts
        ]
        rows, warning = self._effective_rows(settings)
        settings.last_effective_rows = rows
        projection_mode, follow_strength = _resolved_projection(settings)
        self._preview = build_preview(
            boundary_world=boundary_world,
            stroke_world=self._stroke_world,
            rows=rows,
            projection_mode=projection_mode,
            projector=self._projector,
            surface_offset=settings.surface_offset,
            surface_follow_strength=follow_strength,
        )
        if warning:
            self._preview.warnings.append(warning)

        bm = bmesh.from_edit_mesh(context.edit_object.data)
        apply_magnets(
            obj=context.edit_object,
            bm=bm,
            boundary=self._boundary,
            preview=self._preview,
            node_mode=settings.node_magnet,
            edge_mode=settings.edge_magnet,
            distance=settings.magnet_distance,
        )
        self._renderer.preview = self._preview
        self._renderer.node_points = [
            binding.candidate_world
            for binding in self._preview.node_bindings.values()
        ]
        self._renderer.edge_cut_points = [
            binding.point_world
            for binding in self._preview.edge_cut_bindings.values()
        ]
        self._tag_redraw()

    def _clear_preview(self):
        self._stroke_world.clear()
        self._stroke_screen.clear()
        self._preview = None
        self._renderer.stroke = []
        self._renderer.control_points = []
        self._renderer.preview = None
        self._renderer.node_points = []
        self._renderer.edge_cut_points = []
        self._drawing = False
        self._tag_redraw()

    def invoke(self, context, _event):
        settings = context.scene.flowpatch_retopo
        target = settings.target
        if target is None or target.type != "MESH":
            self.report({"ERROR"}, "Choose a mesh Surface in the FlowPatch panel.")
            return {"CANCELLED"}
        if target is context.edit_object:
            self.report(
                {"ERROR"},
                "The retopo mesh and projection Surface must be different objects.",
            )
            return {"CANCELLED"}

        bm = bmesh.from_edit_mesh(context.edit_object.data)
        try:
            self._boundary = ordered_selected_boundary(bm)
        except FlowPatchGeometryError as exc:
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}

        self._area = context.area
        self._region = next(
            (region for region in context.area.regions if region.type == "WINDOW"),
            None,
        )
        self._region_3d = context.space_data.region_3d
        if self._region is None or self._region_3d is None:
            self.report({"ERROR"}, "A 3D Viewport window region is required.")
            return {"CANCELLED"}

        try:
            self._projector = SurfaceProjector(
                target, context.evaluated_depsgraph_get()
            )
        except Exception as exc:
            self.report({"ERROR"}, f"Could not build target projection: {exc}")
            return {"CANCELLED"}

        boundary_world = [
            context.edit_object.matrix_world @ vert.co
            for vert in self._boundary.verts
        ]
        self._stroke_world = []
        self._stroke_screen = []
        self._preview = None
        self._drawing = False
        self._initial_tool = _active_tool_id(context)
        self._renderer = FlowPatchPreviewRenderer()
        self._renderer.boundary = boundary_world
        self._renderer.opacity = settings.preview_opacity
        self._renderer.hud_title = "FLOWPATCH / EXTEND"
        self._renderer.hud_lines = [
            "Drag LMB to draw the opposite rail",
            "Enter  Commit    Backspace  Redraw    Esc  Cancel",
        ]
        self._renderer.install()
        self._set_status(
            context,
            "FlowPatch: drag LMB, Enter confirms, Backspace redraws, Esc cancels",
        )
        context.window_manager.modal_handler_add(self)
        self._tag_redraw()
        return {"RUNNING_MODAL"}

    def modal(self, context, event):
        if not _edit_mesh_poll(context):
            self._cleanup(context)
            return {"CANCELLED"}
        current_tool = _active_tool_id(context)
        if (
            self._initial_tool is not None
            and current_tool is not None
            and current_tool != self._initial_tool
        ):
            self._cleanup(context)
            return {"CANCELLED"}

        if event.type == "ESC" and event.value == "PRESS":
            self._cleanup(context)
            return {"CANCELLED"}

        if event.type in {"RET", "NUMPAD_ENTER"} and event.value == "PRESS":
            if self._preview is None:
                self.report({"WARNING"}, "Draw a valid surface rail first.")
                return {"RUNNING_MODAL"}
            try:
                bm = bmesh.from_edit_mesh(context.edit_object.data)
                result = commit_quad_strip(
                    context.edit_object,
                    bm,
                    self._boundary,
                    self._preview,
                )
            except FlowPatchGeometryError as exc:
                self.report({"ERROR"}, str(exc))
                self._cleanup(context)
                return {"CANCELLED"}
            settings = context.scene.flowpatch_retopo
            settings.last_patch_id = result.patch_id
            detail = []
            if result.merged_vertices:
                detail.append(f"{result.merged_vertices} merged")
            if result.cut_edges:
                detail.append(f"{result.cut_edges} edge cuts")
            suffix = f" ({', '.join(detail)})" if detail else ""
            self.report(
                {"INFO"},
                (
                    f"FlowPatch {result.patch_id}: "
                    f"{result.created_faces} quads committed{suffix}"
                ),
            )
            self._cleanup(context)
            return {"FINISHED"}

        if event.type == "BACK_SPACE" and event.value == "PRESS":
            self._clear_preview()
            return {"RUNNING_MODAL"}

        if event.type in {
            "MIDDLEMOUSE",
            "WHEELUPMOUSE",
            "WHEELDOWNMOUSE",
            "NDOF_MOTION",
        }:
            return {"PASS_THROUGH"}

        if event.type == "LEFTMOUSE":
            if not self._mouse_in_region(event):
                return {"PASS_THROUGH"}
            if event.value == "PRESS":
                self._clear_preview()
                self._drawing = True
                self._append_surface_sample(context, event, force=True)
                return {"RUNNING_MODAL"}
            if event.value == "RELEASE" and self._drawing:
                self._append_surface_sample(context, event, force=True)
                self._drawing = False
                try:
                    self._rebuild_preview(context)
                except FlowPatchGeometryError as exc:
                    self.report({"WARNING"}, str(exc))
                    self._preview = None
                    self._renderer.preview = None
                return {"RUNNING_MODAL"}

        if event.type == "MOUSEMOVE" and self._drawing:
            self._append_surface_sample(context, event)
            return {"RUNNING_MODAL"}

        return {"PASS_THROUGH"}

    def cancel(self, context):
        self._cleanup(context)


class FLOWPATCH_OT_select_patch(Operator):
    bl_idname = "flowpatch.select_patch"
    bl_label = "Select FlowPatch"
    bl_description = "Select the generated patch represented by the active element"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return _edit_mesh_poll(context)

    def execute(self, context):
        obj = context.edit_object
        bm = bmesh.from_edit_mesh(obj.data)
        patch_id = patch_id_from_selection(bm, fallback_last=False)
        if patch_id <= 0:
            self.report({"ERROR"}, "Select an element that belongs to a FlowPatch.")
            return {"CANCELLED"}
        try:
            face_count = select_patch_elements(bm, patch_id)
        except FlowPatchGeometryError as exc:
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}
        bmesh.update_edit_mesh(obj.data, loop_triangles=False, destructive=False)
        context.scene.flowpatch_retopo.last_patch_id = patch_id
        self.report({"INFO"}, f"Selected FlowPatch {patch_id}: {face_count} quads")
        return {"FINISHED"}


class FLOWPATCH_OT_select_last_patch(Operator):
    bl_idname = "flowpatch.select_last_patch"
    bl_label = "Select Last FlowPatch"
    bl_description = "Select the most recently generated patch on this mesh"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return _edit_mesh_poll(context)

    def execute(self, context):
        obj = context.edit_object
        bm = bmesh.from_edit_mesh(obj.data)
        patch_id = patch_id_from_selection(bm, fallback_last=True)
        if patch_id <= 0:
            self.report({"ERROR"}, "This mesh does not contain FlowPatch metadata.")
            return {"CANCELLED"}
        try:
            face_count = select_patch_elements(bm, patch_id)
        except FlowPatchGeometryError as exc:
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}
        bmesh.update_edit_mesh(obj.data, loop_triangles=False, destructive=False)
        context.scene.flowpatch_retopo.last_patch_id = patch_id
        self.report({"INFO"}, f"Selected FlowPatch {patch_id}: {face_count} quads")
        return {"FINISHED"}


class FLOWPATCH_OT_subdivide_patch_rows(Operator):
    bl_idname = "flowpatch.subdivide_patch_rows"
    bl_label = "Subdivide Patch Rows"
    bl_description = (
        "Insert midpoint rails through the selected generated quad patch"
    )
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return _edit_mesh_poll(context)

    def execute(self, context):
        obj = context.edit_object
        settings = context.scene.flowpatch_retopo
        bm = bmesh.from_edit_mesh(obj.data)
        patch_id = patch_id_from_selection(bm, fallback_last=True)
        if patch_id <= 0:
            self.report({"ERROR"}, "Select a generated FlowPatch first.")
            return {"CANCELLED"}
        try:
            result = subdivide_patch_rows(
                obj,
                bm,
                patch_id,
                levels=settings.subdivide_levels,
            )
        except FlowPatchGeometryError as exc:
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}
        settings.last_patch_id = patch_id
        self.report(
            {"INFO"},
            (
                f"FlowPatch {patch_id}: {result.created_faces} quads added "
                f"across {result.levels} subdivision level(s)"
            ),
        )
        return {"FINISHED"}


class FLOWPATCH_OT_flatten_patch(Operator):
    bl_idname = "flowpatch.flatten_patch"
    bl_label = "Flatten / Harden Patch"
    bl_description = (
        "Planarize the selected patch interior while preserving its boundary"
    )
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return _edit_mesh_poll(context)

    def execute(self, context):
        obj = context.edit_object
        settings = context.scene.flowpatch_retopo
        bm = bmesh.from_edit_mesh(obj.data)
        patch_id = patch_id_from_selection(bm, fallback_last=True)
        if patch_id <= 0:
            self.report({"ERROR"}, "Select a generated FlowPatch first.")
            return {"CANCELLED"}
        try:
            result = flatten_patch_to_boundary_plane(
                obj,
                bm,
                patch_id,
                strength=settings.flatten_strength,
            )
        except FlowPatchGeometryError as exc:
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}
        bmesh.update_edit_mesh(
            obj.data,
            loop_triangles=False,
            destructive=False,
        )
        settings.last_patch_id = patch_id
        self.report(
            {"INFO"},
            (
                f"FlowPatch {patch_id}: hardened {result.moved_vertices} "
                f"interior control points"
            ),
        )
        return {"FINISHED"}


class FLOWPATCH_OT_loop_cut_patch(Operator):
    bl_idname = "flowpatch.loop_cut_patch"
    bl_label = "Loop Cut Patch Row"
    bl_description = (
        "Preview Blender-style row cuts: wheel changes count, move slides, "
        "click confirms, Esc cancels"
    )
    bl_options = {"REGISTER", "UNDO"}

    _area = None
    _region = None
    _renderer = None
    _initial_tool = None
    _patch_id = 0
    _active_face_index = -1
    _cuts = 1
    _slide = 0.0
    _start_mouse_x = 0

    @classmethod
    def poll(cls, context):
        return (
            context.area is not None
            and context.area.type == "VIEW_3D"
            and _edit_mesh_poll(context)
        )

    def _set_status(self, context, text):
        try:
            context.workspace.status_text_set(text)
        except Exception:
            pass

    def _active_face(self, bm):
        bm.faces.ensure_lookup_table()
        if 0 <= self._active_face_index < len(bm.faces):
            face = bm.faces[self._active_face_index]
            if face.is_valid:
                return face
        return None

    def _update_preview(self, context):
        bm = bmesh.from_edit_mesh(context.edit_object.data)
        active_face = self._active_face(bm)
        segments = patch_row_preview_segments(
            context.edit_object,
            bm,
            self._patch_id,
            cuts=self._cuts,
            slide=self._slide,
            active_face=active_face,
        )
        self._renderer.custom_segments = segments
        self._renderer.hud_title = "FLOWPATCH / LOOP CUT"
        slide_text = (
            f"{self._slide:+.2f}" if self._cuts == 1 else "centered"
        )
        self._renderer.hud_lines = [
            f"Wheel  Cuts: {self._cuts}    Move  Slide: {slide_text}",
            "LMB / Enter  Confirm    RMB / Esc  Cancel",
        ]
        if self._area is not None:
            self._area.tag_redraw()

    def _cleanup(self, context):
        if self._renderer is not None:
            self._renderer.remove()
            self._renderer = None
        self._set_status(context, None)
        if self._area is not None:
            self._area.tag_redraw()

    def _mouse_in_region(self, event):
        if self._region is None:
            return False
        return (
            self._region.x <= event.mouse_x < self._region.x + self._region.width
            and self._region.y <= event.mouse_y < self._region.y + self._region.height
        )

    def _refresh_preview(self, context):
        try:
            self._update_preview(context)
        except FlowPatchGeometryError as exc:
            self.report({"ERROR"}, str(exc))
            self._cleanup(context)
            return False
        except Exception as exc:
            self.report({"ERROR"}, f"Loop-cut preview failed safely: {exc}")
            self._cleanup(context)
            return False
        return True

    def invoke(self, context, event):
        obj = context.edit_object
        settings = context.scene.flowpatch_retopo
        bm = bmesh.from_edit_mesh(obj.data)
        self._patch_id = patch_id_from_selection(bm, fallback_last=True)
        if self._patch_id <= 0:
            self.report({"ERROR"}, "Select a generated FlowPatch row first.")
            return {"CANCELLED"}
        face_layer = bm.faces.layers.int.get("flowpatch_face_patch_id")
        active_face = bm.faces.active
        if (
            active_face is None
            or face_layer is None
            or active_face[face_layer] != self._patch_id
        ):
            active_face = next(
                (
                    face
                    for face in bm.faces
                    if face.select
                    and face_layer is not None
                    and face[face_layer] == self._patch_id
                ),
                None,
            )
        if active_face is None:
            active_face = next(
                (
                    face
                    for face in bm.faces
                    if face_layer is not None
                    and face[face_layer] == self._patch_id
                ),
                None,
            )
        if active_face is None:
            self.report({"ERROR"}, "The selected FlowPatch has no quad faces.")
            return {"CANCELLED"}
        bm.faces.ensure_lookup_table()
        self._active_face_index = active_face.index
        self._cuts = max(1, min(64, settings.loop_cuts))
        self._slide = settings.loop_slide if self._cuts == 1 else 0.0
        self._start_mouse_x = event.mouse_x
        self._area = context.area
        self._region = next(
            (region for region in context.area.regions if region.type == "WINDOW"),
            None,
        )
        if self._region is None:
            self.report({"ERROR"}, "A 3D Viewport window region is required.")
            return {"CANCELLED"}
        self._initial_tool = _active_tool_id(context)
        self._renderer = FlowPatchPreviewRenderer()
        if not self._refresh_preview(context):
            return {"CANCELLED"}
        self._renderer.install()
        self._set_status(
            context,
            "FlowPatch Loop Cut: wheel count, move slide, click confirm, Esc cancel",
        )
        context.window_manager.modal_handler_add(self)
        return {"RUNNING_MODAL"}

    def _modal_impl(self, context, event):
        if not _edit_mesh_poll(context):
            self._cleanup(context)
            return {"CANCELLED"}
        current_tool = _active_tool_id(context)
        if (
            self._initial_tool is not None
            and current_tool is not None
            and current_tool != self._initial_tool
        ):
            self._cleanup(context)
            return {"CANCELLED"}

        if event.type in {"ESC", "RIGHTMOUSE"} and event.value == "PRESS":
            self._cleanup(context)
            return {"CANCELLED"}

        if (
            event.type == "LEFTMOUSE"
            and event.value == "PRESS"
            and not self._mouse_in_region(event)
        ):
            return {"PASS_THROUGH"}

        if event.type in {"WHEELUPMOUSE", "WHEELDOWNMOUSE"}:
            delta = 1 if event.type == "WHEELUPMOUSE" else -1
            self._cuts = max(1, min(64, self._cuts + delta))
            if self._cuts > 1:
                self._slide = 0.0
            if not self._refresh_preview(context):
                return {"CANCELLED"}
            return {"RUNNING_MODAL"}

        if event.type == "MOUSEMOVE" and self._cuts == 1:
            self._slide = max(
                -0.95,
                min(0.95, (event.mouse_x - self._start_mouse_x) / 300.0),
            )
            if not self._refresh_preview(context):
                return {"CANCELLED"}
            return {"RUNNING_MODAL"}

        if (
            event.type in {"LEFTMOUSE", "RET", "NUMPAD_ENTER"}
            and event.value == "PRESS"
        ):
            obj = context.edit_object
            bm = bmesh.from_edit_mesh(obj.data)
            try:
                result = insert_patch_row_loops(
                    obj,
                    bm,
                    self._patch_id,
                    cuts=self._cuts,
                    slide=self._slide,
                    active_face=self._active_face(bm),
                )
            except FlowPatchGeometryError as exc:
                self.report({"ERROR"}, str(exc))
                self._cleanup(context)
                return {"CANCELLED"}
            bmesh.update_edit_mesh(
                obj.data,
                loop_triangles=True,
                destructive=True,
            )
            settings = context.scene.flowpatch_retopo
            settings.loop_cuts = self._cuts
            settings.loop_slide = self._slide
            settings.last_patch_id = self._patch_id
            self.report(
                {"INFO"},
                (
                    f"FlowPatch {result.patch_id}: inserted "
                    f"{result.cuts} row cut(s)"
                ),
            )
            self._cleanup(context)
            return {"FINISHED"}

        if event.type in {"MIDDLEMOUSE", "NDOF_MOTION"}:
            return {"PASS_THROUGH"}
        return {"RUNNING_MODAL"}

    def modal(self, context, event):
        try:
            return self._modal_impl(context, event)
        except Exception as exc:
            self.report({"ERROR"}, f"FlowPatch loop cut stopped safely: {exc}")
            self._cleanup(context)
            return {"CANCELLED"}

    def cancel(self, context):
        self._cleanup(context)


_CLASSES = (
    FLOWPATCH_OT_start_session,
    FLOWPATCH_OT_stop_session,
    FLOWPATCH_OT_recover_project,
    FLOWPATCH_OT_delete_project,
    FLOWPATCH_OT_create_composite_session,
    FLOWPATCH_OT_clear_composite_session,
    FLOWPATCH_OT_adopt_selected_quad_islands,
    FLOWPATCH_OT_bridge_selected_boundaries,
    FLOWPATCH_OT_guide_session,
    FLOWPATCH_OT_copy_debug_state,
    FLOWPATCH_OT_sync_mesh_from_guides,
    FLOWPATCH_OT_sync_guides_from_mesh,
    FLOWPATCH_OT_detach_frozen_sync,
    FLOWPATCH_OT_capture_boundary,
    FLOWPATCH_OT_toggle_tool,
    FLOWPATCH_OT_seed_patch,
    FLOWPATCH_OT_draw_extension,
    FLOWPATCH_OT_select_patch,
    FLOWPATCH_OT_select_last_patch,
    FLOWPATCH_OT_subdivide_patch_rows,
    FLOWPATCH_OT_flatten_patch,
    FLOWPATCH_OT_loop_cut_patch,
)

_ADDON_KEYMAPS = []


def register():
    for cls in _CLASSES:
        bpy.utils.register_class(cls)
    if _project_load_post not in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.append(_project_load_post)
    _run_project_audit("REGISTER")
    keyconfig = bpy.context.window_manager.keyconfigs.addon
    if keyconfig is None:
        return
    for keymap_name in ("Object Mode", "Mesh"):
        keymap = keyconfig.keymaps.new(
            name=keymap_name,
            space_type="EMPTY",
        )
        item = keymap.keymap_items.new(
            FLOWPATCH_OT_toggle_tool.bl_idname,
            "F7",
            "PRESS",
        )
        _ADDON_KEYMAPS.append((keymap, item))


def shutdown_active_session():
    session = FLOWPATCH_OT_guide_session._active_instance
    if session is None:
        return
    session._stop_requested = True
    try:
        session._clear_stroke()
    except Exception:
        pass
    try:
        session._cleanup(bpy.context)
    except Exception:
        FLOWPATCH_OT_guide_session._active_instance = None


def unregister():
    shutdown_active_session()
    if _project_load_post in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.remove(_project_load_post)
    for keymap, item in reversed(_ADDON_KEYMAPS):
        try:
            keymap.keymap_items.remove(item)
        except Exception:
            pass
    _ADDON_KEYMAPS.clear()
    for cls in reversed(_CLASSES):
        bpy.utils.unregister_class(cls)
