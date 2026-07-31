from dataclasses import dataclass

import bmesh
from mathutils import Vector

from .geometry import EDGE_ROLE_LAYER_NAME
from .geometry import EDGE_ROLE_RAIL
from .geometry import EDGE_ROLE_SPOKE
from .geometry import FACE_LAYER_NAME
from .geometry import FlowPatchGeometryError
from .geometry import VERTEX_LAYER_NAME
from .geometry import EDGE_LAYER_NAME


@dataclass
class PatchFlattenResult:
    patch_id: int
    moved_vertices: int
    maximum_displacement: float


@dataclass
class PatchLoopCutResult:
    patch_id: int
    cuts: int
    slide: float
    row_bands: int
    created_vertices: int
    created_edges: int
    created_faces: int


def _patch_layers(bm):
    vert_layer = bm.verts.layers.int.get(VERTEX_LAYER_NAME)
    edge_layer = bm.edges.layers.int.get(EDGE_LAYER_NAME)
    face_layer = bm.faces.layers.int.get(FACE_LAYER_NAME)
    role_layer = bm.edges.layers.int.get(EDGE_ROLE_LAYER_NAME)
    if any(
        layer is None
        for layer in (vert_layer, edge_layer, face_layer, role_layer)
    ):
        raise FlowPatchGeometryError(
            "This mesh does not contain complete FlowPatch v1 metadata."
        )
    return vert_layer, edge_layer, face_layer, role_layer


def _patch_faces(bm, patch_id, face_layer):
    faces = [
        face
        for face in bm.faces
        if face.is_valid and face[face_layer] == patch_id
    ]
    if not faces:
        raise FlowPatchGeometryError(f"FlowPatch {patch_id} was not found.")
    if any(len(face.verts) != 4 for face in faces):
        raise FlowPatchGeometryError(
            "Patch refinement requires intact generated quads."
        )
    return faces


def flatten_patch_to_boundary_plane(obj, bm, patch_id, strength=1.0):
    _vert_layer, _edge_layer, face_layer, _role_layer = _patch_layers(bm)
    faces = _patch_faces(bm, patch_id, face_layer)
    patch_face_set = set(faces)
    patch_verts = {vert for face in faces for vert in face.verts}
    patch_edges = {edge for face in faces for edge in face.edges}
    boundary_edges = {
        edge
        for edge in patch_edges
        if sum(linked in patch_face_set for linked in edge.link_faces) == 1
    }
    boundary_verts = {vert for edge in boundary_edges for vert in edge.verts}
    interior_verts = patch_verts - boundary_verts
    if not interior_verts:
        raise FlowPatchGeometryError(
            "This patch has no interior vertices to flatten. Add row loops first."
        )

    boundary_world = [obj.matrix_world @ vert.co for vert in boundary_verts]
    centroid = sum(boundary_world, Vector()) / len(boundary_world)
    normal_matrix = obj.matrix_world.inverted_safe().transposed().to_3x3()
    normal = Vector()
    for face in faces:
        normal += (normal_matrix @ face.normal).normalized() * max(
            face.calc_area(), 1.0e-12
        )
    if normal.length_squared <= 1.0e-12:
        raise FlowPatchGeometryError("Could not determine a stable patch plane.")
    normal.normalize()

    strength = max(0.0, min(1.0, float(strength)))
    inverse_world = obj.matrix_world.inverted_safe()
    maximum = 0.0
    for vert in interior_verts:
        world = obj.matrix_world @ vert.co
        flattened = world - normal * (world - centroid).dot(normal)
        moved = world.lerp(flattened, strength)
        maximum = max(maximum, (moved - world).length)
        vert.co = inverse_world @ moved

    return PatchFlattenResult(
        patch_id=patch_id,
        moved_vertices=len(interior_verts),
        maximum_displacement=maximum,
    )


def _connected_rail_component(start_edge, allowed_edges):
    component_edges = set()
    component_verts = set()
    pending = [start_edge]
    while pending:
        edge = pending.pop()
        if edge in component_edges:
            continue
        component_edges.add(edge)
        component_verts.update(edge.verts)
        for vert in edge.verts:
            for linked in vert.link_edges:
                if linked in allowed_edges and linked not in component_edges:
                    pending.append(linked)
    return component_edges, component_verts


def _active_band(faces, active_face, role_layer):
    rail_edges = {
        edge
        for face in faces
        for edge in face.edges
        if edge[role_layer] == EDGE_ROLE_RAIL
    }
    active_rails = [
        edge for edge in active_face.edges if edge[role_layer] == EDGE_ROLE_RAIL
    ]
    if len(active_rails) != 2:
        raise FlowPatchGeometryError(
            "The active face no longer has two FlowPatch rail edges."
        )

    rail_a_edges, rail_a_verts = _connected_rail_component(
        active_rails[0], rail_edges
    )
    rail_b_edges, rail_b_verts = _connected_rail_component(
        active_rails[1], rail_edges
    )
    if rail_a_edges & rail_b_edges:
        raise FlowPatchGeometryError("The selected row band is not separable.")

    band_faces = []
    for face in faces:
        has_a = any(edge in rail_a_edges for edge in face.edges)
        has_b = any(edge in rail_b_edges for edge in face.edges)
        if has_a and has_b:
            band_faces.append(face)
    if not band_faces:
        raise FlowPatchGeometryError("Could not recover the selected patch row.")
    allowed = set(band_faces)
    pending = [band_faces[0]]
    visited = set()
    while pending:
        face = pending.pop()
        if face in visited:
            continue
        visited.add(face)
        for edge in face.edges:
            if edge[role_layer] != EDGE_ROLE_SPOKE:
                continue
            pending.extend(
                linked
                for linked in edge.link_faces
                if linked in allowed and linked not in visited
            )
    if visited != allowed:
        raise FlowPatchGeometryError(
            "The selected row contains disconnected or branched islands."
        )
    return band_faces, rail_a_verts, rail_b_verts


def _split_spoke(edge, rail_a_verts, factors, layers, patch_id):
    vert_layer, edge_layer, _face_layer, role_layer = layers
    if edge.verts[0] in rail_a_verts:
        start = edge.verts[0]
        end = edge.verts[1]
    elif edge.verts[1] in rail_a_verts:
        start = edge.verts[1]
        end = edge.verts[0]
    else:
        raise FlowPatchGeometryError(
            "A row spoke is not connected to the selected source rail."
        )

    created = []
    current_start = start
    previous_factor = 0.0
    current_edge = edge
    for factor in factors:
        relative = (factor - previous_factor) / (1.0 - previous_factor)
        _new_edge, midpoint = bmesh.utils.edge_split(
            current_edge,
            current_start,
            relative,
        )
        midpoint[vert_layer] = patch_id
        for linked in midpoint.link_edges:
            linked[edge_layer] = patch_id
            linked[role_layer] = EDGE_ROLE_SPOKE
        created.append(midpoint)
        current_start = midpoint
        previous_factor = factor
        current_edge = next(
            (
                linked
                for linked in midpoint.link_edges
                if end in linked.verts
            ),
            None,
        )
        if current_edge is None and factor != factors[-1]:
            raise FlowPatchGeometryError(
                "The patch spoke changed while inserting row loops."
            )
    return created


def _loop_cut_factors(cuts, slide):
    cuts = int(cuts)
    slide = float(slide)
    if not 1 <= cuts <= 64:
        raise FlowPatchGeometryError("Loop cuts must be between 1 and 64.")
    if not -0.95 <= slide <= 0.95:
        raise FlowPatchGeometryError("Loop slide must be between -0.95 and 0.95.")
    if cuts > 1:
        if abs(slide) > 1.0e-8:
            raise FlowPatchGeometryError(
                "Sliding is available only when inserting one row cut."
            )
        return [index / (cuts + 1) for index in range(1, cuts + 1)]
    return [0.5 * (slide + 1.0)]


def patch_row_preview_segments(
    obj,
    bm,
    patch_id,
    cuts=1,
    slide=0.0,
    active_face=None,
):
    _vert_layer, _edge_layer, face_layer, role_layer = _patch_layers(bm)
    faces = _patch_faces(bm, patch_id, face_layer)
    if active_face is None or active_face not in faces:
        active_face = next((face for face in faces if face.select), faces[0])

    band_faces, rail_a_verts, _rail_b_verts = _active_band(
        faces,
        active_face,
        role_layer,
    )
    factors = _loop_cut_factors(cuts, slide)
    cuts = len(factors)

    segments = []
    for face in band_faces:
        spokes = [
            edge
            for edge in face.edges
            if edge[role_layer] == EDGE_ROLE_SPOKE
        ]
        if len(spokes) != 2:
            raise FlowPatchGeometryError(
                "A selected row face does not have two compatible spokes."
            )
        spoke_points = []
        for edge in spokes:
            if edge.verts[0] in rail_a_verts:
                start, end = edge.verts
            elif edge.verts[1] in rail_a_verts:
                end, start = edge.verts
            else:
                raise FlowPatchGeometryError(
                    "A row spoke is not connected to the selected source rail."
                )
            spoke_points.append(
                [
                    obj.matrix_world @ start.co.lerp(end.co, factor)
                    for factor in factors
                ]
            )
        for index in range(cuts):
            segments.append(
                (spoke_points[0][index], spoke_points[1][index])
            )
    return segments


def insert_patch_row_loops(
    obj,
    bm,
    patch_id,
    cuts=1,
    slide=0.0,
    active_face=None,
):
    layers = _patch_layers(bm)
    vert_layer, edge_layer, face_layer, role_layer = layers
    faces = _patch_faces(bm, patch_id, face_layer)
    if active_face is None or active_face not in faces:
        active_face = next((face for face in faces if face.select), faces[0])

    band_faces, rail_a_verts, _rail_b_verts = _active_band(
        faces,
        active_face,
        role_layer,
    )
    factors = _loop_cut_factors(cuts, slide)
    cuts = len(factors)

    spoke_edges = {
        edge
        for face in band_faces
        for edge in face.edges
        if edge[role_layer] == EDGE_ROLE_SPOKE
    }
    if not spoke_edges:
        raise FlowPatchGeometryError("The selected patch row has no spoke edges.")
    patch_face_set = set(faces)
    unsafe_spokes = [
        edge
        for edge in spoke_edges
        if any(linked not in patch_face_set for linked in edge.link_faces)
    ]
    if unsafe_spokes:
        raise FlowPatchGeometryError(
            "This row shares a spoke with non-patch geometry. "
            "Detach or complete that boundary before cutting."
        )

    face_spokes = {}
    for face in band_faces:
        spokes = tuple(
            edge
            for edge in face.edges
            if edge[role_layer] == EDGE_ROLE_SPOKE
        )
        if len(spokes) != 2:
            raise FlowPatchGeometryError(
                "A selected row face does not have two compatible spokes."
            )
        face_spokes[face] = spokes

    split_points = {}
    for edge in list(spoke_edges):
        split_points[edge] = _split_spoke(
            edge,
            rail_a_verts,
            factors,
            layers,
            patch_id,
        )

    original_face_count = len(bm.faces)
    created_rails = []
    for original_face in list(band_faces):
        original_spokes = face_spokes[original_face]
        material_index = original_face.material_index
        for cut_index in range(cuts):
            first = split_points[original_spokes[0]][cut_index]
            second = split_points[original_spokes[1]][cut_index]
            candidate_faces = [
                face
                for face in set(first.link_faces) & set(second.link_faces)
                if face.is_valid and face[face_layer] == patch_id
            ]
            if len(candidate_faces) != 1:
                raise FlowPatchGeometryError(
                    "Could not isolate the quad band during loop insertion."
                )
            face = candidate_faces[0]
            split_result = bmesh.utils.face_split(face, first, second)
            if split_result is None:
                raise FlowPatchGeometryError(
                    "Could not connect a generated patch row loop."
                )
            new_face, _loop = split_result
            face.material_index = material_index
            new_face.material_index = material_index
            face[face_layer] = patch_id
            new_face[face_layer] = patch_id
            rail = bm.edges.get((first, second))
            if rail is None:
                raise FlowPatchGeometryError(
                    "The inserted row loop edge could not be recovered."
                )
            rail[edge_layer] = patch_id
            rail[role_layer] = EDGE_ROLE_RAIL
            created_rails.append(rail)

    for edge in created_rails:
        edge.select_set(True)
        for vert in edge.verts:
            vert.select_set(True)

    invalid = [
        face
        for face in bm.faces
        if face[face_layer] == patch_id and len(face.verts) != 4
    ]
    if invalid:
        raise FlowPatchGeometryError("Loop insertion produced non-quad faces.")
    if any(len(edge.link_faces) > 2 for edge in bm.edges):
        raise FlowPatchGeometryError(
            "Loop insertion produced a non-manifold face fan."
        )

    return PatchLoopCutResult(
        patch_id=patch_id,
        cuts=cuts,
        slide=float(slide),
        row_bands=1,
        created_vertices=len(spoke_edges) * cuts,
        created_edges=len(spoke_edges) * cuts + len(created_rails),
        created_faces=len(bm.faces) - original_face_count,
    )
