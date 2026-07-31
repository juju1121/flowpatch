from dataclasses import dataclass
from dataclasses import field

import bmesh
from mathutils import Vector


VERTEX_LAYER_NAME = "flowpatch_vertex_patch_id"
EDGE_LAYER_NAME = "flowpatch_edge_patch_id"
FACE_LAYER_NAME = "flowpatch_face_patch_id"
EDGE_ROLE_LAYER_NAME = "flowpatch_edge_role"

EDGE_ROLE_NONE = 0
EDGE_ROLE_RAIL = 1
EDGE_ROLE_SPOKE = 2


class FlowPatchGeometryError(RuntimeError):
    pass


@dataclass
class BoundaryChain:
    verts: list
    edges: list
    face_traverses_forward: list
    material_indices: list


@dataclass
class NodeBinding:
    rail_index: int
    candidate_index: int
    action: str
    candidate_world: Vector


@dataclass
class EdgeCutBinding:
    rail_index: int
    start_index: int
    end_index: int
    factor: float
    point_world: Vector


@dataclass
class PreviewPatch:
    rails_world: list
    node_bindings: dict = field(default_factory=dict)
    edge_cut_bindings: dict = field(default_factory=dict)
    warnings: list = field(default_factory=list)

    @property
    def segment_count(self):
        if not self.rails_world:
            return 0
        return max(0, len(self.rails_world[0]) - 1)

    @property
    def row_count(self):
        return max(0, len(self.rails_world) - 1)

    @property
    def magnet_points(self):
        points = [
            binding.candidate_world for binding in self.node_bindings.values()
        ]
        points.extend(
            binding.point_world for binding in self.edge_cut_bindings.values()
        )
        return points


@dataclass
class CommitResult:
    patch_id: int
    created_vertices: int
    created_edges: int
    created_faces: int
    merged_vertices: int
    cut_edges: int
    outer_verts: list
    outer_edges: list


@dataclass
class PatchSubdivideResult:
    patch_id: int
    levels: int
    created_vertices: int
    created_edges: int
    created_faces: int


def _selected_boundary_edges(bm):
    selected = [
        edge
        for edge in bm.edges
        if edge.select and not edge.hide and len(edge.link_faces) <= 1
    ]
    if selected:
        return selected

    return [
        edge
        for edge in bm.edges
        if not edge.hide
        and len(edge.link_faces) <= 1
        and edge.verts[0].select
        and edge.verts[1].select
    ]


def ordered_selected_boundary(bm):
    edges = _selected_boundary_edges(bm)
    if not edges:
        raise FlowPatchGeometryError(
            "Select one connected open boundary edge chain in Edit Mode."
        )

    adjacency = {}
    for edge in edges:
        for vert in edge.verts:
            adjacency.setdefault(vert, []).append(edge)

    branched = [vert for vert, linked in adjacency.items() if len(linked) > 2]
    if branched:
        raise FlowPatchGeometryError(
            "The selected boundary branches. Select one chain without forks."
        )

    endpoints = [vert for vert, linked in adjacency.items() if len(linked) == 1]
    if len(endpoints) != 2:
        raise FlowPatchGeometryError(
            "The selection must be open, with exactly two endpoints."
        )

    visited_edges = set()
    ordered_verts = [endpoints[0]]
    ordered_edges = []
    current = endpoints[0]

    while True:
        candidates = [
            edge for edge in adjacency[current] if edge not in visited_edges
        ]
        if not candidates:
            break
        edge = candidates[0]
        visited_edges.add(edge)
        ordered_edges.append(edge)
        current = edge.other_vert(current)
        ordered_verts.append(current)

    if len(visited_edges) != len(edges):
        raise FlowPatchGeometryError(
            "The selected boundary contains disconnected chains."
        )

    face_traverses_forward = []
    material_indices = []
    for index, edge in enumerate(ordered_edges):
        face = edge.link_faces[0] if len(edge.link_faces) == 1 else None
        face_traverses_forward.append(
            _face_traverses(face, ordered_verts[index], ordered_verts[index + 1])
        )
        material_indices.append(face.material_index if face is not None else 0)
    return BoundaryChain(
        ordered_verts,
        ordered_edges,
        face_traverses_forward,
        material_indices,
    )


def resample_polyline(points, count):
    points = [Vector(point) for point in points]
    if count < 2:
        raise FlowPatchGeometryError("A rail requires at least two vertices.")
    if len(points) < 2:
        raise FlowPatchGeometryError("Draw a longer stroke across the surface.")

    cumulative = [0.0]
    for start, end in zip(points, points[1:]):
        cumulative.append(cumulative[-1] + (end - start).length)

    total_length = cumulative[-1]
    if total_length <= 1.0e-8:
        raise FlowPatchGeometryError("The drawn stroke is too short.")

    result = []
    segment_index = 0
    for index in range(count):
        target_distance = total_length * index / (count - 1)
        while (
            segment_index < len(points) - 2
            and cumulative[segment_index + 1] < target_distance
        ):
            segment_index += 1

        start_distance = cumulative[segment_index]
        end_distance = cumulative[segment_index + 1]
        span = max(end_distance - start_distance, 1.0e-12)
        factor = (target_distance - start_distance) / span
        result.append(points[segment_index].lerp(points[segment_index + 1], factor))
    return result


def orient_rail(boundary_world, rail_world):
    direct = (
        (boundary_world[0] - rail_world[0]).length_squared
        + (boundary_world[-1] - rail_world[-1]).length_squared
    )
    reversed_cost = (
        (boundary_world[0] - rail_world[-1]).length_squared
        + (boundary_world[-1] - rail_world[0]).length_squared
    )
    if reversed_cost < direct:
        return list(reversed(rail_world))
    return rail_world


def build_preview(
    boundary_world,
    stroke_world,
    rows,
    projection_mode,
    projector=None,
    surface_offset=0.0,
    surface_follow_strength=0.5,
):
    if len(boundary_world) < 2:
        raise FlowPatchGeometryError("The selected boundary is too short.")

    rows = max(1, int(rows))
    outer_rail = resample_polyline(stroke_world, len(boundary_world))
    outer_rail = orient_rail(boundary_world, outer_rail)
    rails = [[Vector(point) for point in boundary_world]]

    if projection_mode == "SURFACE":
        projection_strength = 1.0
    elif projection_mode == "BALANCED":
        projection_strength = max(0.0, min(1.0, surface_follow_strength))
    else:
        projection_strength = 0.0

    for row_index in range(1, rows + 1):
        factor = row_index / rows
        rail = []
        for boundary_point, outer_point in zip(boundary_world, outer_rail):
            linear_point = Vector(boundary_point).lerp(Vector(outer_point), factor)
            point = linear_point
            if projection_strength > 0.0 and projector is not None:
                projected = projector.nearest_world(linear_point, surface_offset)
                if projected is not None:
                    point = linear_point.lerp(
                        projected[0],
                        projection_strength,
                    )
            rail.append(point)
        rails.append(rail)

    return PreviewPatch(rails)


def _face_traverses(face, start_vert, end_vert):
    if face is None:
        return False
    for loop in face.loops:
        if loop.vert is start_vert and loop.link_loop_next.vert is end_vert:
            return True
    return False


def _closest_point_on_segment(point, start, end):
    segment = end - start
    length_squared = segment.length_squared
    if length_squared <= 1.0e-16:
        return start.copy(), 0.0
    factor = max(0.0, min(1.0, (point - start).dot(segment) / length_squared))
    return start.lerp(end, factor), factor


def apply_magnets(
    obj,
    bm,
    boundary,
    preview,
    node_mode="OFF",
    edge_mode="OFF",
    distance=0.05,
):
    if preview.row_count < 1:
        return preview

    distance = max(float(distance), 1.0e-8)
    bm.verts.ensure_lookup_table()
    bm.verts.index_update()
    boundary_set = set(boundary.verts)
    outer = preview.rails_world[-1]
    used_vertices = set()

    if node_mode != "OFF":
        candidates = []
        for vert in bm.verts:
            if vert.hide or vert in boundary_set or len(vert.link_faces) > 1:
                continue
            if node_mode == "SNAP" and len(vert.link_edges) != 0:
                continue
            candidates.append((vert, obj.matrix_world @ vert.co))

        for rail_index, point in enumerate(outer):
            available = [
                (vert, world)
                for vert, world in candidates
                if vert not in used_vertices
            ]
            if not available:
                break
            candidate, candidate_world = min(
                available,
                key=lambda item: (item[1] - point).length_squared,
            )
            if (candidate_world - point).length > distance:
                continue
            outer[rail_index] = candidate_world.copy()
            preview.node_bindings[rail_index] = NodeBinding(
                rail_index=rail_index,
                candidate_index=candidate.index,
                action=node_mode,
                candidate_world=candidate_world.copy(),
            )
            used_vertices.add(candidate)

    if edge_mode in {"SNAP", "CUT"}:
        bm.edges.index_update()
        used_edges = set()
        endpoint_indices = {0, len(outer) - 1}
        loose_edges = [
            edge
            for edge in bm.edges
            if not edge.hide
            and len(edge.link_faces) == 0
            and not any(vert in boundary_set for vert in edge.verts)
        ]
        for rail_index in endpoint_indices:
            if rail_index in preview.node_bindings:
                continue
            point = outer[rail_index]
            best = None
            for edge in loose_edges:
                if edge in used_edges:
                    continue
                start, end = edge.verts
                start_world = obj.matrix_world @ start.co
                end_world = obj.matrix_world @ end.co
                closest, factor = _closest_point_on_segment(
                    point,
                    start_world,
                    end_world,
                )
                if factor <= 0.05 or factor >= 0.95:
                    continue
                candidate = (
                    (closest - point).length_squared,
                    edge,
                    start,
                    end,
                    closest,
                    factor,
                )
                if best is None or candidate[0] < best[0]:
                    best = candidate
            if best is None or best[0] > distance * distance:
                continue
            _distance, edge, start, end, closest, factor = best
            outer[rail_index] = closest.copy()
            if edge_mode == "SNAP":
                used_edges.add(edge)
                continue
            preview.edge_cut_bindings[rail_index] = EdgeCutBinding(
                rail_index=rail_index,
                start_index=start.index,
                end_index=end.index,
                factor=factor,
                point_world=closest.copy(),
            )
            used_edges.add(edge)

    return preview


def _next_patch_id(bm):
    layer = bm.faces.layers.int.get(FACE_LAYER_NAME)
    if layer is None:
        return 1
    values = [face[layer] for face in bm.faces]
    return max(values, default=0) + 1


def _face_refs(start, end, next_start, next_end, traverses_forward):
    if traverses_forward:
        return (end, start, next_start, next_end)
    return (start, end, next_end, next_start)


def _prevalidate_commit(bm, boundary, preview):
    bm.verts.ensure_lookup_table()
    bm.verts.index_update()
    boundary_indices = {vert.index for vert in boundary.verts}
    candidate_indices = set()
    for binding in preview.node_bindings.values():
        if binding.action != "MERGE":
            continue
        if binding.candidate_index in boundary_indices:
            raise FlowPatchGeometryError(
                "A magnet tried to merge back into the starting boundary."
            )
        if binding.candidate_index in candidate_indices:
            raise FlowPatchGeometryError(
                "Two generated nodes resolved to the same merge candidate."
            )
        candidate_indices.add(binding.candidate_index)

    rail_refs = [list(boundary.verts)]
    for row_index in range(1, preview.row_count + 1):
        rail = []
        for column_index in range(len(boundary.verts)):
            binding = (
                preview.node_bindings.get(column_index)
                if row_index == preview.row_count
                else None
            )
            if binding is not None and binding.action == "MERGE":
                try:
                    rail.append(bm.verts[binding.candidate_index])
                except IndexError as exc:
                    raise FlowPatchGeometryError(
                        "A magnet target changed while drawing."
                    ) from exc
            else:
                rail.append(("new", row_index, column_index))
        rail_refs.append(rail)

    planned_edge_uses = {}
    for row_index in range(preview.row_count):
        current_rail = rail_refs[row_index]
        next_rail = rail_refs[row_index + 1]
        for segment_index in range(preview.segment_count):
            face_refs = _face_refs(
                current_rail[segment_index],
                current_rail[segment_index + 1],
                next_rail[segment_index],
                next_rail[segment_index + 1],
                boundary.face_traverses_forward[segment_index],
            )
            if len(set(face_refs)) != 4:
                raise FlowPatchGeometryError(
                    "A magnet would create a degenerate quad."
                )
            if all(isinstance(item, bmesh.types.BMVert) for item in face_refs):
                face_set = set(face_refs)
                if any(set(face.verts) == face_set for face in bm.faces):
                    raise FlowPatchGeometryError(
                        "A magnet would duplicate an existing face."
                    )
            for start, end in zip(face_refs, face_refs[1:] + face_refs[:1]):
                key = frozenset((start, end))
                planned_edge_uses[key] = planned_edge_uses.get(key, 0) + 1

    for key, planned_uses in planned_edge_uses.items():
        if not all(isinstance(item, bmesh.types.BMVert) for item in key):
            continue
        if len(key) != 2:
            raise FlowPatchGeometryError("A generated edge would collapse.")
        start, end = tuple(key)
        edge = bm.edges.get((start, end))
        existing_faces = len(edge.link_faces) if edge is not None else 0
        if existing_faces + planned_uses > 2:
            raise FlowPatchGeometryError(
                "A merge candidate would create a non-manifold edge."
            )


def _ensure_layers(bm):
    vert_layer = bm.verts.layers.int.get(VERTEX_LAYER_NAME)
    if vert_layer is None:
        vert_layer = bm.verts.layers.int.new(VERTEX_LAYER_NAME)
    edge_layer = bm.edges.layers.int.get(EDGE_LAYER_NAME)
    if edge_layer is None:
        edge_layer = bm.edges.layers.int.new(EDGE_LAYER_NAME)
    face_layer = bm.faces.layers.int.get(FACE_LAYER_NAME)
    if face_layer is None:
        face_layer = bm.faces.layers.int.new(FACE_LAYER_NAME)
    edge_role_layer = bm.edges.layers.int.get(EDGE_ROLE_LAYER_NAME)
    if edge_role_layer is None:
        edge_role_layer = bm.edges.layers.int.new(EDGE_ROLE_LAYER_NAME)
    return vert_layer, edge_layer, face_layer, edge_role_layer


def commit_quad_strip(obj, bm, boundary, preview):
    if preview.row_count < 1 or preview.segment_count < 1:
        raise FlowPatchGeometryError("There is no valid quad preview to commit.")
    if len(boundary.verts) != len(preview.rails_world[0]):
        raise FlowPatchGeometryError("Boundary and preview vertex counts differ.")
    if not all(vert.is_valid for vert in boundary.verts):
        raise FlowPatchGeometryError(
            "The boundary changed while drawing. Start the operation again."
        )

    _prevalidate_commit(bm, boundary, preview)
    bm.verts.index_update()
    boundary_vertex_indices = [vert.index for vert in boundary.verts]
    edge_direction_flags = list(boundary.face_traverses_forward)
    material_indices = list(boundary.material_indices)
    node_candidate_indices = {
        rail_index: binding.candidate_index
        for rail_index, binding in preview.node_bindings.items()
        if binding.action == "MERGE"
    }
    edge_cut_specs = {
        rail_index: (
            binding.start_index,
            binding.end_index,
            binding.factor,
        )
        for rail_index, binding in preview.edge_cut_bindings.items()
    }
    patch_id = _next_patch_id(bm)
    existing_edges = set(bm.edges)
    vert_layer, edge_layer, face_layer, edge_role_layer = _ensure_layers(bm)

    # CustomData creation can invalidate wrappers. Resolve by pre-mutation index.
    bm.verts.ensure_lookup_table()
    resolved_boundary = [bm.verts[index] for index in boundary_vertex_indices]
    resolved_nodes = {
        rail_index: bm.verts[index]
        for rail_index, index in node_candidate_indices.items()
    }

    edge_cut_verts = {}
    cut_edges = 0
    for rail_index, (start_index, end_index, factor) in edge_cut_specs.items():
        start = bm.verts[start_index]
        end = bm.verts[end_index]
        edge = bm.edges.get((start, end))
        if edge is None or len(edge.link_faces) != 0:
            raise FlowPatchGeometryError(
                "A loose edge magnet target changed before commit."
            )
        new_edge, new_vert = bmesh.utils.edge_split(edge, start, factor)
        new_vert[vert_layer] = patch_id
        edge[edge_layer] = patch_id
        edge[edge_role_layer] = EDGE_ROLE_NONE
        new_edge[edge_layer] = patch_id
        new_edge[edge_role_layer] = EDGE_ROLE_NONE
        edge_cut_verts[rail_index] = new_vert
        cut_edges += 1

    inverse_world = obj.matrix_world.inverted_safe()
    rail_verts = [resolved_boundary]
    created_verts = list(edge_cut_verts.values())

    for row_index, rail_world in enumerate(preview.rails_world[1:], start=1):
        new_rail = []
        is_outer = row_index == preview.row_count
        for column_index, world_point in enumerate(rail_world):
            if is_outer and column_index in resolved_nodes:
                vert = resolved_nodes[column_index]
            elif is_outer and column_index in edge_cut_verts:
                vert = edge_cut_verts[column_index]
            else:
                vert = bm.verts.new(inverse_world @ Vector(world_point))
                vert[vert_layer] = patch_id
                created_verts.append(vert)
            new_rail.append(vert)
        rail_verts.append(new_rail)

    created_faces = []
    for row_index in range(preview.row_count):
        current_rail = rail_verts[row_index]
        next_rail = rail_verts[row_index + 1]
        for segment_index in range(preview.segment_count):
            face_verts = _face_refs(
                current_rail[segment_index],
                current_rail[segment_index + 1],
                next_rail[segment_index],
                next_rail[segment_index + 1],
                edge_direction_flags[segment_index],
            )
            try:
                face = bm.faces.new(face_verts)
            except ValueError as exc:
                raise FlowPatchGeometryError(
                    "A generated face overlaps existing geometry."
                ) from exc
            face.material_index = material_indices[segment_index]
            face[face_layer] = patch_id
            created_faces.append(face)

    for rail in rail_verts:
        for start, end in zip(rail, rail[1:]):
            edge = bm.edges.get((start, end))
            if edge is not None:
                if edge[edge_layer] == 0:
                    edge[edge_layer] = patch_id
                edge[edge_role_layer] = EDGE_ROLE_RAIL

    for current_rail, next_rail in zip(rail_verts, rail_verts[1:]):
        for start, end in zip(current_rail, next_rail):
            edge = bm.edges.get((start, end))
            if edge is not None:
                if edge[edge_layer] == 0:
                    edge[edge_layer] = patch_id
                edge[edge_role_layer] = EDGE_ROLE_SPOKE

    created_edges = set(bm.edges) - existing_edges
    for edge in created_edges:
        if edge[edge_layer] == 0:
            edge[edge_layer] = patch_id

    for vert in bm.verts:
        vert.select_set(False)
    for edge in bm.edges:
        edge.select_set(False)
    for face in bm.faces:
        face.select_set(False)

    outer_verts = rail_verts[-1]
    outer_edges = []
    for start, end in zip(outer_verts, outer_verts[1:]):
        edge = bm.edges.get((start, end))
        if edge is None:
            raise FlowPatchGeometryError(
                "The committed outer boundary could not be recovered."
            )
        start.select_set(True)
        end.select_set(True)
        edge.select_set(True)
        outer_edges.append(edge)

    bmesh.update_edit_mesh(obj.data, loop_triangles=True, destructive=True)
    return CommitResult(
        patch_id=patch_id,
        created_vertices=len(created_verts),
        created_edges=len(created_edges),
        created_faces=len(created_faces),
        merged_vertices=len(resolved_nodes),
        cut_edges=cut_edges,
        outer_verts=outer_verts,
        outer_edges=outer_edges,
    )


def patch_id_from_selection(bm, fallback_last=True):
    vert_layer = bm.verts.layers.int.get(VERTEX_LAYER_NAME)
    edge_layer = bm.edges.layers.int.get(EDGE_LAYER_NAME)
    face_layer = bm.faces.layers.int.get(FACE_LAYER_NAME)
    if face_layer is None:
        return 0

    active = bm.select_history.active
    if isinstance(active, bmesh.types.BMFace) and active.is_valid:
        patch_id = active[face_layer]
        if patch_id > 0:
            return patch_id
    if (
        isinstance(active, bmesh.types.BMEdge)
        and active.is_valid
        and edge_layer is not None
    ):
        patch_id = active[edge_layer]
        if patch_id > 0:
            return patch_id
    if (
        isinstance(active, bmesh.types.BMVert)
        and active.is_valid
        and vert_layer is not None
    ):
        patch_id = active[vert_layer]
        if patch_id > 0:
            return patch_id

    for face in bm.faces:
        if face.select and face[face_layer] > 0:
            return face[face_layer]
    if edge_layer is not None:
        for edge in bm.edges:
            if edge.select and edge[edge_layer] > 0:
                return edge[edge_layer]
    if vert_layer is not None:
        for vert in bm.verts:
            if vert.select and vert[vert_layer] > 0:
                return vert[vert_layer]

    if fallback_last:
        return max((face[face_layer] for face in bm.faces), default=0)
    return 0


def select_patch_elements(bm, patch_id):
    face_layer = bm.faces.layers.int.get(FACE_LAYER_NAME)
    if face_layer is None or patch_id <= 0:
        raise FlowPatchGeometryError("No FlowPatch metadata exists on this mesh.")

    faces = [face for face in bm.faces if face[face_layer] == patch_id]
    if not faces:
        raise FlowPatchGeometryError(f"FlowPatch {patch_id} was not found.")

    for vert in bm.verts:
        vert.select_set(False)
    for edge in bm.edges:
        edge.select_set(False)
    for face in bm.faces:
        face.select_set(False)
    for face in faces:
        face.select_set(True)
        for edge in face.edges:
            edge.select_set(True)
        for vert in face.verts:
            vert.select_set(True)
    return len(faces)


def _subdivide_patch_rows_once(bm, patch_id, layers):
    vert_layer, edge_layer, face_layer, edge_role_layer = layers
    faces = [
        face
        for face in bm.faces
        if face.is_valid and face[face_layer] == patch_id
    ]
    if not faces:
        raise FlowPatchGeometryError(f"FlowPatch {patch_id} was not found.")

    face_spokes = {}
    spoke_edges = set()
    for face in faces:
        spokes = [
            edge
            for edge in face.edges
            if edge[edge_role_layer] == EDGE_ROLE_SPOKE
        ]
        if len(spokes) != 2:
            raise FlowPatchGeometryError(
                "This patch cannot be subdivided because its row metadata "
                "was changed or predates FlowPatch v1."
            )
        face_spokes[face] = tuple(spokes)
        spoke_edges.update(spokes)

    midpoint_by_edge = {}
    for edge in list(spoke_edges):
        if not edge.is_valid:
            raise FlowPatchGeometryError(
                "Patch topology changed during loop subdivision."
            )
        start = edge.verts[0]
        new_edge, midpoint = bmesh.utils.edge_split(edge, start, 0.5)
        midpoint[vert_layer] = patch_id
        edge[edge_layer] = patch_id
        edge[edge_role_layer] = EDGE_ROLE_SPOKE
        new_edge[edge_layer] = patch_id
        new_edge[edge_role_layer] = EDGE_ROLE_SPOKE
        midpoint_by_edge[edge] = midpoint

    new_faces = []
    for face, spokes in face_spokes.items():
        if not face.is_valid:
            raise FlowPatchGeometryError(
                "A patch face became invalid during loop subdivision."
            )
        midpoint_a = midpoint_by_edge[spokes[0]]
        midpoint_b = midpoint_by_edge[spokes[1]]
        material_index = face.material_index
        split_result = bmesh.utils.face_split(
            face,
            midpoint_a,
            midpoint_b,
        )
        if split_result is None:
            raise FlowPatchGeometryError("Could not split a generated quad.")
        new_face, _loop = split_result
        face.material_index = material_index
        new_face.material_index = material_index
        face[face_layer] = patch_id
        new_face[face_layer] = patch_id
        connecting = bm.edges.get((midpoint_a, midpoint_b))
        if connecting is None:
            raise FlowPatchGeometryError(
                "Loop subdivision did not create its connecting rail."
            )
        connecting[edge_layer] = patch_id
        connecting[edge_role_layer] = EDGE_ROLE_RAIL
        new_faces.append(new_face)

    invalid = [
        face
        for face in bm.faces
        if face[face_layer] == patch_id and len(face.verts) != 4
    ]
    if invalid:
        raise FlowPatchGeometryError(
            "Loop subdivision produced a non-quad patch."
        )
    return len(spoke_edges), len(new_faces), len(new_faces)


def subdivide_patch_rows(obj, bm, patch_id, levels=1):
    if levels < 1:
        raise FlowPatchGeometryError("Subdivision levels must be at least one.")
    layers = _ensure_layers(bm)
    total_vertices = 0
    total_edges = 0
    total_faces = 0

    for _level in range(levels):
        created_vertices, created_edges, created_faces = (
            _subdivide_patch_rows_once(bm, patch_id, layers)
        )
        total_vertices += created_vertices
        total_edges += created_edges
        total_faces += created_faces

    select_patch_elements(bm, patch_id)
    bmesh.update_edit_mesh(obj.data, loop_triangles=True, destructive=True)
    return PatchSubdivideResult(
        patch_id=patch_id,
        levels=levels,
        created_vertices=total_vertices,
        created_edges=total_edges,
        created_faces=total_faces,
    )
