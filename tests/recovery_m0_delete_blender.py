import argparse
import json
from pathlib import Path
from types import SimpleNamespace
import sys

import bmesh
import bpy
from mathutils import Vector


def parse_args():
    argv = sys.argv
    argv = argv[argv.index("--") + 1 :] if "--" in argv else []
    parser = argparse.ArgumentParser()
    parser.add_argument("--addon-root", required=True)
    parser.add_argument("--run-dir", required=True)
    return parser.parse_args(argv)


args = parse_args()
if args.addon_root not in sys.path:
    sys.path.insert(0, args.addon_root)

from flowpatch_retopo.guide_graph import delete_guide_edge
from flowpatch_retopo.guide_graph import delete_guide_node
from flowpatch_retopo.guide_graph import delete_guide_point
from flowpatch_retopo.guide_graph import GuideDeleteError
from flowpatch_retopo.guide_graph import GuidePath
from flowpatch_retopo.guide_graph import SurfaceAnchor
from flowpatch_retopo.guides import load_boundary_registry
from flowpatch_retopo.guides import load_node_vertex_registry
from flowpatch_retopo.guides import FlowPatchGeometryError
from flowpatch_retopo.guides import remove_built_cell_geometry
from flowpatch_retopo.guides import save_boundary_registry
from flowpatch_retopo.guides import save_built_cells
from flowpatch_retopo.guides import save_guides
from flowpatch_retopo.guides import save_node_vertex_registry
from flowpatch_retopo.guides import VERTEX_UID_LAYER
from flowpatch_retopo.operators import FLOWPATCH_OT_guide_session


def anchor(x):
    return SurfaceAnchor(local_position=Vector((x, 0.0, 0.0)))


def guide(
    guide_id,
    points,
    start_node,
    end_node,
    logical_side_id,
    source_kind="DRAWN",
):
    return GuidePath(
        guide_id=guide_id,
        points_local=[Vector(point) for point in points],
        start_node=start_node,
        end_node=end_node,
        logical_side_id=logical_side_id,
        source_kind=source_kind,
        source_vertex_uids=tuple(range(100, 100 + len(points))),
        anchors=tuple(anchor(point[0]) for point in points),
    )


evidence = {}

control_guides = [
    guide(
        1,
        ((0, 0, 0), (1, 0.2, 0), (2, 0, 0)),
        1,
        2,
        10,
    )
]
result = delete_guide_point(control_guides, 0, 1)
assert result.target_kind == "CONTROL"
assert len(control_guides[0].points_local) == 2
assert tuple(point.x for point in control_guides[0].points_local) == (0.0, 2.0)
assert len(control_guides[0].anchors) == 2
assert control_guides[0].source_vertex_uids == (100, 102)
evidence["control_delete"] = result.message

end_guides = [guide(2, ((0, 0, 0), (1, 0, 0)), 10, 11, 20)]
result = delete_guide_node(end_guides, 10)
assert result.target_kind == "END_NODE"
assert not end_guides
evidence["end_node_delete"] = result.removed_guide_ids

merge_guides = [
    guide(8, ((0, 0, 0), (1, 0, 0)), 21, 22, 30),
    guide(4, ((1, 0, 0), (2, 0, 0)), 22, 23, 30),
]
result = delete_guide_node(merge_guides, 22)
assert result.target_kind == "DEGREE_2_NODE"
assert len(merge_guides) == 1
merged = merge_guides[0]
assert merged.guide_id == 4
assert (merged.start_node, merged.end_node) == (21, 23)
assert tuple(point.x for point in merged.points_local) == (0.0, 1.0, 2.0)
assert len(merged.anchors) == 3
evidence["degree_2_merge"] = {
    "changed": result.changed_guide_ids,
    "removed": result.removed_guide_ids,
}

protected = [
    guide(10, ((0, 0, 0), (1, 0, 0)), 31, 32, 40),
    guide(11, ((1, 0, 0), (2, 0, 0)), 32, 33, 41),
]
try:
    delete_guide_node(protected, 32)
except GuideDeleteError as exc:
    assert exc.reason_code == "PROTECTED_CORNER"
else:
    raise AssertionError("A logical patch corner was collapsed.")
assert len(protected) == 2
evidence["protected_corner"] = "rejected"

junction = [
    guide(20, ((0, 0, 0), (1, 0, 0)), 41, 42, 50),
    guide(21, ((1, 0, 0), (2, 1, 0)), 42, 43, 50),
    guide(22, ((1, 0, 0), (2, -1, 0)), 42, 44, 50),
]
try:
    delete_guide_node(junction, 42)
except GuideDeleteError as exc:
    assert exc.reason_code == "JUNCTION_CONFIRM_REQUIRED"
else:
    raise AssertionError("A junction was silently collapsed.")
assert len(junction) == 3
evidence["junction"] = "rejected"

edge_guides = [
    guide(30, ((0, 0, 0), (1, 0, 0)), 51, 52, 60),
    guide(31, ((0, 1, 0), (1, 1, 0)), 53, 54, 61),
]
result = delete_guide_edge(edge_guides, 30)
assert [item.guide_id for item in edge_guides] == [31]
evidence["edge_delete"] = result.removed_guide_ids

if bpy.context.mode != "OBJECT":
    bpy.ops.object.mode_set(mode="OBJECT")
for obj in tuple(bpy.data.objects):
    bpy.data.objects.remove(obj, do_unlink=True)

mesh = bpy.data.meshes.new("M0DeleteMesh")
mesh.from_pydata(
    (
        (0, 0, 0),
        (1, 0, 0),
        (2, 0, 0),
        (0, 1, 0),
        (1, 1, 0),
        (2, 1, 0),
    ),
    (),
    ((0, 1, 4, 3), (1, 2, 5, 4)),
)
obj = bpy.data.objects.new("M0DeleteObject", mesh)
bpy.context.scene.collection.objects.link(obj)
bpy.context.view_layer.objects.active = obj
obj.select_set(True)
bpy.ops.object.mode_set(mode="EDIT")
bm = bmesh.from_edit_mesh(mesh)
uid_layer = bm.verts.layers.int.new(VERTEX_UID_LAYER)
bm.verts.ensure_lookup_table()
for index, vert in enumerate(bm.verts, start=1):
    vert[uid_layer] = index
bmesh.update_edit_mesh(mesh, loop_triangles=True, destructive=False)

built_cells = {
    "A": {
        "state": "PARAMETRIC",
        "edge_ids": [1, 2, 3, 4],
        "vertex_uids": [1, 2, 4, 5],
        "grid_vertex_uids": [[1, 2], [4, 5]],
    },
    "B": {
        "state": "PARAMETRIC",
        "edge_ids": [5, 6, 7, 8],
        "vertex_uids": [2, 3, 5, 6],
        "grid_vertex_uids": [[2, 3], [5, 6]],
    },
}
save_built_cells(obj, built_cells)
save_boundary_registry(
    obj,
    {
        "removed": {"count": 2, "uids": [1, 4], "revision": 1},
        "shared": {"count": 2, "uids": [2, 5], "revision": 1},
        "kept": {"count": 2, "uids": [3, 6], "revision": 1},
    },
)
save_node_vertex_registry(obj, {"1": 1, "2": 2, "3": 3})
duplicate_cells = {
    "duplicate": {
        "state": "PARAMETRIC",
        "edge_ids": [90, 91, 92, 93],
        "vertex_uids": [1, 2, 4, 5],
        "grid_vertex_uids": [[1, 2], [4, 5], [1, 2]],
    }
}
try:
    remove_built_cell_geometry(obj, bm, duplicate_cells, ("duplicate",))
except FlowPatchGeometryError as exc:
    assert "repeats GRID face ownership" in str(exc)
else:
    raise AssertionError("Repeated GRID face ownership was accepted.")
assert len(bm.faces) == 2
evidence["duplicate_grid"] = "rejected_before_mutation"

removal = remove_built_cell_geometry(obj, bm, built_cells, ("A",))
bm = bmesh.from_edit_mesh(mesh)
assert removal.removed_faces == 1
assert removal.removed_vertices == 2
assert set(removal.built_cells) == {"B"}
assert len(bm.faces) == 1
remaining_uids = {int(vert[uid_layer]) for vert in bm.verts}
assert remaining_uids == {2, 3, 5, 6}
assert set(load_boundary_registry(obj)) == {"shared", "kept"}
assert load_node_vertex_registry(obj) == {"2": 2, "3": 3}
evidence["adjacent_cell_preserved"] = {
    "faces": len(bm.faces),
    "uids": sorted(remaining_uids),
}

snapshot_guides = [guide(70, ((0, 0, 0), (1, 0, 0)), 71, 72, 80)]
save_guides(obj, snapshot_guides)
obj["flowpatch_m0_marker"] = "before"
owner = SimpleNamespace(
    _retopo=obj,
    _guides=snapshot_guides,
    _u_segments=1,
    _v_segments=1,
    _built_cells=removal.built_cells,
    _density_overrides={},
    _selected_control=(0, 0),
    _selected_guides=set(),
    _selected_points={(0, 0)},
    _active_anchor=(0, 0),
    _scene=None,
    _sync_scene_settings=lambda: None,
)
snapshot = FLOWPATCH_OT_guide_session._state_snapshot(
    owner,
    include_mesh=True,
)
bm.verts.ensure_lookup_table()
original_coordinate = bm.verts[0].co.copy()
bm.verts[0].co.x += 5.0
bmesh.update_edit_mesh(mesh, loop_triangles=True, destructive=False)
owner._guides = []
obj["flowpatch_m0_marker"] = "after"
FLOWPATCH_OT_guide_session._restore_state(owner, snapshot, rebuild=False)
bm = bmesh.from_edit_mesh(mesh)
bm.verts.ensure_lookup_table()
assert len(owner._guides) == 1
assert (bm.verts[0].co - original_coordinate).length <= 1.0e-8
assert obj["flowpatch_m0_marker"] == "before"
FLOWPATCH_OT_guide_session._discard_state_snapshot(owner, snapshot)
evidence["mesh_undo_snapshot"] = "restored"

run_dir = Path(args.run_dir)
run_dir.mkdir(parents=True, exist_ok=True)
(run_dir / "m0_delete_evidence.json").write_text(
    json.dumps(evidence, indent=2, sort_keys=True),
    encoding="utf-8",
)
print("FLOWPATCH_M0_DELETE_PASS", json.dumps(evidence, sort_keys=True))
