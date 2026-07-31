from dataclasses import dataclass

import bmesh
from mathutils import Vector

from .geometry import EDGE_LAYER_NAME
from .geometry import FACE_LAYER_NAME
from .geometry import VERTEX_LAYER_NAME
from .geometry import FlowPatchGeometryError
from .geometry import PreviewPatch

try:
    from .geometry import EDGE_ROLE_LAYER_NAME
    from .geometry import EDGE_ROLE_RAIL
    from .geometry import EDGE_ROLE_SPOKE
except ImportError:
    EDGE_ROLE_LAYER_NAME = None
    EDGE_ROLE_RAIL = 0
    EDGE_ROLE_SPOKE = 0


@dataclass
class SeedCommitResult:
    patch_id: int
    created_vertices: int
    created_edges: int
    created_faces: int
    outer_verts: list
    outer_edges: list


def _resample_screen_polyline(points, count):
    points = [Vector((point[0], point[1])) for point in points]
    if count < 2 or len(points) < 2:
        raise FlowPatchGeometryError("Draw a longer stroke on the surface.")

    cumulative = [0.0]
    for start, end in zip(points, points[1:]):
        cumulative.append(cumulative[-1] + (end - start).length)

    total_length = cumulative[-1]
    if total_length <= 1.0e-6:
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


def _screen_tangent(points, index):
    if index == 0:
        tangent = points[1] - points[0]
    elif index == len(points) - 1:
        tangent = points[-1] - points[-2]
    else:
        tangent = points[index + 1] - points[index - 1]
    if tangent.length_squared <= 1.0e-10:
        raise FlowPatchGeometryError("The stroke contains a collapsed segment.")
    return tangent.normalized()


def build_seed_preview(
    projector,
    region,
    region_3d,
    stroke_screen,
    segment_count,
    width_px,
    surface_offset=0.0,
):
    screen_points = _resample_screen_polyline(
        stroke_screen,
        max(2, int(segment_count) + 1),
    )
    half_width = max(2.0, float(width_px) * 0.5)
    left_rail = []
    right_rail = []
    reference_normals = []

    for index, center in enumerate(screen_points):
        tangent = _screen_tangent(screen_points, index)
        perpendicular = Vector((-tangent.y, tangent.x))
        center_hit = projector.raycast_region(
            region,
            region_3d,
            center,
            surface_offset,
        )
        if center_hit is None:
            raise FlowPatchGeometryError(
                "The stroke left the projection surface. Redraw inside its silhouette."
            )
        reference_normals.append(Vector(center_hit[1]))

        left_hit = None
        right_hit = None
        for width_factor in (1.0, 0.75, 0.5, 0.25):
            offset = perpendicular * half_width * width_factor
            left_hit = projector.raycast_region(
                region,
                region_3d,
                center + offset,
                surface_offset,
            )
            right_hit = projector.raycast_region(
                region,
                region_3d,
                center - offset,
                surface_offset,
            )
            if left_hit is not None and right_hit is not None:
                break

        if left_hit is None or right_hit is None:
            raise FlowPatchGeometryError(
                "The patch is too wide near the surface edge. Redraw farther inside."
            )
        left_rail.append(Vector(left_hit[0]))
        right_rail.append(Vector(right_hit[0]))

    preview = PreviewPatch([left_rail, right_rail])
    normal = Vector()
    for value in reference_normals:
        normal += value
    if normal.length_squared > 1.0e-10:
        normal.normalize()
    preview.reference_normal = normal
    return preview


def _next_patch_id(bm):
    layer = bm.faces.layers.int.get(FACE_LAYER_NAME)
    if layer is None:
        return 1
    return max((face[layer] for face in bm.faces), default=0) + 1


def commit_seed_patch(obj, bm, preview):
    if preview is None or preview.row_count != 1 or preview.segment_count < 1:
        raise FlowPatchGeometryError("There is no valid first-patch preview.")

    left_world, right_world = preview.rails_world
    if len(left_world) != len(right_world) or len(left_world) < 2:
        raise FlowPatchGeometryError("The first-patch rails are incompatible.")

    patch_id = _next_patch_id(bm)
    vert_layer = bm.verts.layers.int.get(VERTEX_LAYER_NAME)
    if vert_layer is None:
        vert_layer = bm.verts.layers.int.new(VERTEX_LAYER_NAME)
    edge_layer = bm.edges.layers.int.get(EDGE_LAYER_NAME)
    if edge_layer is None:
        edge_layer = bm.edges.layers.int.new(EDGE_LAYER_NAME)
    face_layer = bm.faces.layers.int.get(FACE_LAYER_NAME)
    if face_layer is None:
        face_layer = bm.faces.layers.int.new(FACE_LAYER_NAME)
    role_layer = None
    if EDGE_ROLE_LAYER_NAME:
        role_layer = bm.edges.layers.int.get(EDGE_ROLE_LAYER_NAME)
        if role_layer is None:
            role_layer = bm.edges.layers.int.new(EDGE_ROLE_LAYER_NAME)

    inverse_world = obj.matrix_world.inverted_safe()
    left_verts = []
    right_verts = []
    for world_point in left_world:
        vert = bm.verts.new(inverse_world @ Vector(world_point))
        vert[vert_layer] = patch_id
        left_verts.append(vert)
    for world_point in right_world:
        vert = bm.verts.new(inverse_world @ Vector(world_point))
        vert[vert_layer] = patch_id
        right_verts.append(vert)

    created_faces = []
    for index in range(len(left_verts) - 1):
        face = bm.faces.new(
            (
                left_verts[index],
                left_verts[index + 1],
                right_verts[index + 1],
                right_verts[index],
            )
        )
        face[face_layer] = patch_id
        created_faces.append(face)

    for face in created_faces:
        face.normal_update()

    reference_normal = getattr(preview, "reference_normal", Vector())
    if created_faces and reference_normal.length_squared > 1.0e-10:
        local_normal = created_faces[0].normal
        world_normal = (
            obj.matrix_world.inverted_safe().transposed().to_3x3() @ local_normal
        ).normalized()
        if world_normal.dot(reference_normal) < 0.0:
            for face in created_faces:
                face.normal_flip()

    created_edges = set()
    for face in created_faces:
        for edge in face.edges:
            edge[edge_layer] = patch_id
            created_edges.add(edge)

    left_edges = []
    right_edges = []
    for start, end in zip(left_verts, left_verts[1:]):
        edge = bm.edges.get((start, end))
        if edge is not None:
            left_edges.append(edge)
            if role_layer is not None:
                edge[role_layer] = EDGE_ROLE_RAIL
    for start, end in zip(right_verts, right_verts[1:]):
        edge = bm.edges.get((start, end))
        if edge is not None:
            right_edges.append(edge)
            if role_layer is not None:
                edge[role_layer] = EDGE_ROLE_RAIL
    if role_layer is not None:
        rail_edges = set(left_edges + right_edges)
        for edge in created_edges - rail_edges:
            edge[role_layer] = EDGE_ROLE_SPOKE

    for vert in bm.verts:
        vert.select_set(False)
    for edge in bm.edges:
        edge.select_set(False)
    for face in bm.faces:
        face.select_set(False)

    for vert in right_verts:
        vert.select_set(True)
    for edge in right_edges:
        edge.select_set(True)

    bmesh.update_edit_mesh(obj.data, loop_triangles=True, destructive=True)
    return SeedCommitResult(
        patch_id=patch_id,
        created_vertices=len(left_verts) + len(right_verts),
        created_edges=len(created_edges),
        created_faces=len(created_faces),
        outer_verts=right_verts,
        outer_edges=right_edges,
    )
