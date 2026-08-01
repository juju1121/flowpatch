import argparse
import json
import math
from pathlib import Path
import sys

import bpy
from mathutils import Vector


def parse_args():
    argv = sys.argv
    argv = argv[argv.index("--") + 1 :] if "--" in argv else []
    parser = argparse.ArgumentParser()
    parser.add_argument("--addon-root", required=True)
    parser.add_argument("--run-dir", required=True)
    return parser.parse_args(argv)


def clear_scene():
    if bpy.context.mode != "OBJECT":
        bpy.ops.object.mode_set(mode="OBJECT")
    for obj in tuple(bpy.data.objects):
        bpy.data.objects.remove(obj, do_unlink=True)


def make_mesh(name, vertices, faces):
    mesh = bpy.data.meshes.new(f"{name}Mesh")
    mesh.from_pydata(vertices, [], faces)
    mesh.update()
    obj = bpy.data.objects.new(name, mesh)
    bpy.context.scene.collection.objects.link(obj)
    bpy.context.view_layer.update()
    return obj


def plane_geometry(z=0.0, extent=3.0):
    return (
        [
            (-extent, -extent, z),
            (extent, -extent, z),
            (extent, extent, z),
            (-extent, extent, z),
        ],
        [(0, 1, 2, 3)],
    )


def cube_geometry():
    vertices = [
        (-1.0, -1.0, -1.0),
        (-1.0, -1.0, 1.0),
        (-1.0, 1.0, -1.0),
        (-1.0, 1.0, 1.0),
        (1.0, -1.0, -1.0),
        (1.0, -1.0, 1.0),
        (1.0, 1.0, -1.0),
        (1.0, 1.0, 1.0),
    ]
    faces = [
        (0, 2, 3, 1),
        (4, 5, 7, 6),
        (0, 1, 5, 4),
        (2, 6, 7, 3),
        (0, 4, 6, 2),
        (1, 3, 7, 5),
    ]
    return vertices, faces


def cylinder_geometry(segments=16):
    vertices = []
    for z in (-1.0, 1.0):
        vertices.extend(
            (
                math.cos(2.0 * math.pi * index / segments),
                math.sin(2.0 * math.pi * index / segments),
                z,
            )
            for index in range(segments)
        )
    faces = [
        (
            index,
            (index + 1) % segments,
            segments + (index + 1) % segments,
            segments + index,
        )
        for index in range(segments)
    ]
    return vertices, faces


def anchored_guide(
    projector,
    guide_path_type,
    guide_id,
    points,
    start_node,
    end_node,
    target_uuid,
):
    anchors = []
    previous = None
    for point in points:
        nearest = projector.nearest_world_continuous(
            Vector(point),
            target_object_uuid=target_uuid,
            previous_anchor=previous,
            max_projection_distance=2.0,
            max_surface_step=2.0,
            normal_continuity_cos=-0.05,
        )
        assert nearest is not None
        anchors.append(nearest[4].copy())
        previous = nearest[4]
    return guide_path_type(
        guide_id=guide_id,
        points_local=[Vector(point) for point in points],
        start_node=start_node,
        end_node=end_node,
        logical_side_id=guide_id,
        anchors=tuple(anchors),
    )


args = parse_args()
if args.addon_root not in sys.path:
    sys.path.insert(0, args.addon_root)

from flowpatch_retopo.guide_graph import BoundarySide
from flowpatch_retopo.guide_graph import GuideCycle
from flowpatch_retopo.guide_graph import GuidePath
from flowpatch_retopo.guides import build_cycle_preview
from flowpatch_retopo.guides import fair_guides_tangent
from flowpatch_retopo.geometry import FlowPatchGeometryError
from flowpatch_retopo.projection import SurfaceProjector


clear_scene()
evidence = {}
depsgraph = bpy.context.evaluated_depsgraph_get()

# Full per-point anchors on a planar target.
plane_vertices, plane_faces = plane_geometry()
plane = make_mesh("P1Plane", plane_vertices, plane_faces)
retopo = make_mesh("P1Retopo", [], [])
plane_uuid = "p1-plane-target"
plane_projector = SurfaceProjector(plane, depsgraph)
anchor_hit = plane_projector.nearest_world_continuous(
    Vector((0.25, -0.35, 0.2)),
    target_object_uuid=plane_uuid,
    max_projection_distance=1.0,
)
assert anchor_hit is not None
anchor = anchor_hit[4]
assert anchor.face_index >= 0
assert anchor.triangle_index >= 0
assert len(anchor.barycentric) == 3
assert abs(sum(anchor.barycentric) - 1.0) <= 1.0e-6
assert anchor.shell_component == 0
assert anchor.topology_revision == plane_projector.topology_revision
assert abs(anchor.local_position.z) <= 1.0e-7
evidence["full_anchor"] = {
    "face_index": anchor.face_index,
    "triangle_index": anchor.triangle_index,
    "barycentric_sum": sum(anchor.barycentric),
    "shell_component": anchor.shell_component,
    "topology_revision": anchor.topology_revision,
}
front_hit = plane_projector.raycast_world_continuous(
    Vector((0.0, 0.0, 1.0)),
    Vector((0.0, 0.0, -1.0)),
    target_object_uuid=plane_uuid,
)
back_hit = plane_projector.raycast_world_continuous(
    Vector((0.0, 0.0, -1.0)),
    Vector((0.0, 0.0, 1.0)),
    target_object_uuid=plane_uuid,
)
assert front_hit is not None
assert back_hit is None
evidence["view_raycast"] = {
    "frontface_accepted": True,
    "backface_rejected": True,
}

# Four unequal-control boundaries, with the stored top guide reversed.
guides = [
    anchored_guide(
        plane_projector,
        GuidePath,
        1,
        [(-2.0, -1.0, 0.0), (-1.1, -1.15, 0.0), (0.2, -0.9, 0.0), (2.0, -1.0, 0.0)],
        1,
        2,
        plane_uuid,
    ),
    anchored_guide(
        plane_projector,
        GuidePath,
        2,
        [(2.0, -1.0, 0.0), (2.15, -0.2, 0.0), (2.0, 1.0, 0.0)],
        2,
        3,
        plane_uuid,
    ),
    anchored_guide(
        plane_projector,
        GuidePath,
        3,
        [(-2.0, 1.0, 0.0), (-0.8, 1.1, 0.0), (0.1, 0.95, 0.0), (1.2, 1.12, 0.0), (2.0, 1.0, 0.0)],
        4,
        3,
        plane_uuid,
    ),
    anchored_guide(
        plane_projector,
        GuidePath,
        4,
        [(-2.0, 1.0, 0.0), (-2.2, 0.55, 0.0), (-2.05, -0.25, 0.0), (-2.0, -1.0, 0.0)],
        4,
        1,
        plane_uuid,
    ),
]
cycle = GuideCycle(
    key="1:2:3:4",
    sides=(
        BoundarySide((1,), 1, 2, 1),
        BoundarySide((2,), 2, 3, 2),
        BoundarySide((3,), 3, 4, 3),
        BoundarySide((4,), 4, 1, 4),
    ),
    traversal_nodes=(1, 2, 3, 4),
    winding="CCW",
    target_local_area=8.0,
)
preview = build_cycle_preview(
    obj=retopo,
    cycle=cycle,
    guide_by_id={guide.guide_id: guide for guide in guides},
    projector=plane_projector,
    u_segments=7,
    v_segments=5,
    projection_mode="SMOOTH",
    target_object_uuid=plane_uuid,
    max_projection_distance=1.0,
    max_surface_step=2.0,
    flat_target_tolerance=1.0e-6,
)
metrics = preview.validation_metrics
assert metrics["patch_max_plane_deviation"] <= 1.0e-6
assert metrics["min_quad_signed_area"] > 0.0
assert metrics["max_edge_ratio"] < 100.0
assert metrics["normal_flip_count"] == 0
assert max(
    abs(point.z) for rail in preview.rails_world for point in rail
) <= 1.0e-6
bottom_lengths = [
    (second - first).length
    for first, second in zip(
        preview.rails_world[0],
        preview.rails_world[0][1:],
    )
]
assert max(bottom_lengths) / min(bottom_lengths) < 1.25
evidence["planar_preview"] = {
    "u_segments": preview.u_segments,
    "v_segments": preview.v_segments,
    "metrics": metrics,
    "bottom_chord_ratio": max(bottom_lengths) / min(bottom_lengths),
    "reversed_stored_boundary": True,
}

# Tangent-only fairing keeps endpoints pinned and every control on the plane.
fair_guides = [
    anchored_guide(
        plane_projector,
        GuidePath,
        10,
        [
            (-2.0, 0.0, 0.0),
            (-1.0, 0.45, 0.0),
            (0.0, -0.35, 0.0),
            (1.0, 0.5, 0.0),
            (2.0, 0.0, 0.0),
        ],
        10,
        11,
        plane_uuid,
    )
]
before = [point.copy() for point in fair_guides[0].points_local]
moved_count = fair_guides_tangent(
    retopo,
    fair_guides,
    (0,),
    plane_projector,
    plane_uuid,
    strength=0.5,
    iterations=3,
    max_projection_distance=1.0,
    max_surface_step=2.0,
)
after = fair_guides[0].points_local
assert moved_count == 9
assert (after[0] - before[0]).length <= 1.0e-9
assert (after[-1] - before[-1]).length <= 1.0e-9
assert any(
    (after[index] - before[index]).length > 1.0e-5
    for index in range(1, len(after) - 1)
)
assert max(abs(point.z) for point in after) <= 1.0e-7
evidence["tangent_fairing"] = {
    "moved_control_iterations": moved_count,
    "endpoints_pinned": True,
    "max_plane_deviation": max(abs(point.z) for point in after),
}
rollback_guides = [guides[0].copy()]
rollback_before = [
    point.copy() for point in rollback_guides[0].points_local
]
try:
    fair_guides_tangent(
        retopo,
        rollback_guides,
        (0,),
        plane_projector,
        plane_uuid,
        strength=1.0,
        iterations=1,
        max_projection_distance=1.0,
        max_surface_step=1.0e-6,
    )
except FlowPatchGeometryError as exc:
    assert "REJECTED_PROJECTION" in str(exc)
else:
    raise AssertionError("Unsafe fairing should have been rejected.")
assert all(
    (before_point - after_point).length <= 1.0e-9
    for before_point, after_point in zip(
        rollback_before,
        rollback_guides[0].points_local,
    )
)
evidence["fairing_rollback"] = {
    "rejected_projection": True,
    "original_controls_preserved": True,
}

# Disconnected nearby shells cannot steal an existing anchor.
shell_vertices = [
    (-1.0, -1.0, 0.0),
    (1.0, -1.0, 0.0),
    (1.0, 1.0, 0.0),
    (-1.0, 1.0, 0.0),
    (-1.0, -1.0, -0.05),
    (1.0, -1.0, -0.05),
    (1.0, 1.0, -0.05),
    (-1.0, 1.0, -0.05),
]
shell_target = make_mesh(
    "P1DoubleShell",
    shell_vertices,
    [(0, 1, 2, 3), (4, 5, 6, 7)],
)
shell_projector = SurfaceProjector(shell_target, depsgraph)
top_hit = shell_projector.nearest_world_continuous(
    Vector((0.0, 0.0, 0.001)),
    target_object_uuid="p1-double-shell",
    max_projection_distance=0.02,
)
assert top_hit is not None
wrong_shell = shell_projector.nearest_world_continuous(
    Vector((0.0, 0.0, -0.049)),
    target_object_uuid="p1-double-shell",
    previous_anchor=top_hit[4],
    max_projection_distance=0.02,
    max_surface_step=1.0,
)
assert wrong_shell is None
evidence["double_shell"] = {
    "starting_component": top_hit[4].shell_component,
    "opposite_shell_rejected": True,
}

# A connected 90-degree cube corner is allowed; the opposite face is not.
cube_vertices, cube_faces = cube_geometry()
cube = make_mesh("P1Cube", cube_vertices, cube_faces)
cube_projector = SurfaceProjector(cube, depsgraph)
cube_top = cube_projector.nearest_world_continuous(
    Vector((0.0, 0.0, 1.02)),
    target_object_uuid="p1-cube",
    max_projection_distance=0.2,
)
assert cube_top is not None
cube_side = cube_projector.nearest_world_continuous(
    Vector((1.02, 0.0, 0.7)),
    target_object_uuid="p1-cube",
    previous_anchor=cube_top[4],
    max_projection_distance=0.2,
    max_surface_step=2.0,
    normal_continuity_cos=-0.05,
)
assert cube_side is not None
assert cube_top[1].dot(cube_side[1]) >= -1.0e-6
cube_opposite = cube_projector.nearest_world_continuous(
    Vector((0.0, 0.0, -1.02)),
    target_object_uuid="p1-cube",
    previous_anchor=cube_top[4],
    max_projection_distance=0.2,
    max_surface_step=3.0,
    normal_continuity_cos=-0.05,
)
assert cube_opposite is None
evidence["hard_corner"] = {
    "adjacent_face_accepted": True,
    "opposite_face_rejected": True,
}

# Connected curved faces retain component and normal continuity.
cylinder_vertices, cylinder_faces = cylinder_geometry()
cylinder = make_mesh("P1Cylinder", cylinder_vertices, cylinder_faces)
cylinder_projector = SurfaceProjector(cylinder, depsgraph)
previous = None
cylinder_samples = []
for step in range(5):
    angle = step * (math.pi / 8.0)
    candidate = Vector((math.cos(angle), math.sin(angle), 0.2))
    hit = cylinder_projector.nearest_world_continuous(
        candidate,
        target_object_uuid="p1-cylinder",
        previous_anchor=previous,
        max_projection_distance=0.25,
        max_surface_step=0.75,
        normal_continuity_cos=0.5,
    )
    assert hit is not None
    cylinder_samples.append(hit)
    previous = hit[4]
assert len({hit[4].shell_component for hit in cylinder_samples}) == 1
assert all(
    abs(math.hypot(hit[0].x, hit[0].y) - 1.0) <= 0.03
    for hit in cylinder_samples
)
evidence["curved_surface"] = {
    "sample_count": len(cylinder_samples),
    "single_component": True,
}

# A topology revision invalidates triangle ownership without dereferencing
# stale Blender RNA; the same physical point is safely reacquired.
old_revision = plane_projector.topology_revision
old_anchor = anchor.copy()
plane_projector.close()
plane.data.clear_geometry()
plane.data.from_pydata(
    plane_vertices,
    [],
    [(0, 1, 2), (0, 2, 3)],
)
plane.data.update()
bpy.context.view_layer.update()
revised_projector = SurfaceProjector(plane, depsgraph)
assert revised_projector.topology_revision != old_revision
revised_hit = revised_projector.nearest_world_continuous(
    Vector((0.25, -0.35, 0.01)),
    target_object_uuid=plane_uuid,
    previous_anchor=old_anchor,
    max_projection_distance=0.2,
    max_surface_step=1.0,
)
assert revised_hit is not None
assert revised_hit[4].topology_revision == revised_projector.topology_revision
evidence["topology_revision"] = {
    "old_revision": old_revision,
    "new_revision": revised_projector.topology_revision,
    "anchor_reacquired": True,
}

for projector in (
    revised_projector,
    shell_projector,
    cube_projector,
    cylinder_projector,
):
    projector.close()

run_dir = Path(args.run_dir)
run_dir.mkdir(parents=True, exist_ok=True)
(run_dir / "p1_surface_anchor_evidence.json").write_text(
    json.dumps(evidence, indent=2, sort_keys=True),
    encoding="utf-8",
)
print("FLOWPATCH_P1_SURFACE_ANCHOR_PASS")
