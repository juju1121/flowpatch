import argparse
import json
from pathlib import Path
import sys

import bpy


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
    for scene in tuple(bpy.data.scenes):
        if scene is not bpy.context.scene:
            bpy.data.scenes.remove(scene)
    for collection in tuple(bpy.data.collections):
        if collection.users == 0:
            bpy.data.collections.remove(collection)


def make_quad(name, collection=None):
    mesh = bpy.data.meshes.new(f"{name}Mesh")
    mesh.from_pydata(
        [
            (-1.0, -1.0, 0.0),
            (1.0, -1.0, 0.0),
            (1.0, 1.0, 0.0),
            (-1.0, 1.0, 0.0),
        ],
        [],
        [(0, 1, 2, 3)],
    )
    mesh.update()
    obj = bpy.data.objects.new(name, mesh)
    (collection or bpy.context.scene.collection).objects.link(obj)
    return obj


def flowpatch_properties(owner):
    return {
        str(key): owner[key]
        for key in owner.keys()
        if str(key).startswith("flowpatch_")
    }


def activate(obj, edit=False):
    if bpy.context.mode != "OBJECT":
        bpy.ops.object.mode_set(mode="OBJECT")
    for selected in tuple(bpy.context.selected_objects):
        selected.select_set(False)
    obj.select_set(True)
    bpy.context.view_layer.objects.active = obj
    if edit:
        bpy.ops.object.mode_set(mode="EDIT")


class ReportCapture:
    def __init__(self, surface_name="", launch_modal=False):
        self.surface_name = surface_name
        self.launch_modal = launch_modal
        self.messages = []

    def report(self, levels, message):
        self.messages.append(
            {
                "levels": sorted(levels),
                "message": str(message),
            }
        )


args = parse_args()
if args.addon_root not in sys.path:
    sys.path.insert(0, args.addon_root)

import flowpatch_retopo
from flowpatch_retopo import operators
from flowpatch_retopo.project_store import audit_projects
from flowpatch_retopo.project_store import read_project_record


clear_scene()
flowpatch_retopo.register()
settings = bpy.context.scene.flowpatch_retopo

# Existing plain mesh outside the active ViewLayer must be linked before select.
target = make_quad("R1Target")
foreign_scene = bpy.data.scenes.new("R1ForeignScene")
foreign_collection = bpy.data.collections.new("R1ForeignCollection")
foreign_scene.collection.children.link(foreign_collection)
retopo = make_quad("R1ForeignRetopo", collection=foreign_collection)
assert bpy.context.view_layer.objects.get(retopo.name) is None
settings.target = target
settings.continue_on = retopo
activate(target)

reporter = ReportCapture(target.name)
result = operators.FLOWPATCH_OT_start_session.execute(reporter, bpy.context)
active_collection = bpy.context.view_layer.active_layer_collection.collection
record = read_project_record(retopo)

assert result == {"FINISHED"}
assert bpy.context.mode == "EDIT_MESH"
assert bpy.context.edit_object is retopo
assert bpy.context.view_layer.objects.get(retopo.name) is retopo
assert active_collection.objects.get(retopo.name) is retopo
assert retopo.select_get(view_layer=bpy.context.view_layer)
assert record is not None
assert settings.target is target
assert settings.continue_on is retopo
assert retopo["flowpatch_session_active"] is False
assert audit_projects(tuple(bpy.data.objects)) == ()
linked_result = sorted(result)

# A forced post-link failure on a pre-existing object must remove only the new
# active-collection link and restore all project identity.
bpy.ops.object.mode_set(mode="OBJECT")
active_collection.objects.unlink(retopo)
bpy.context.view_layer.update()
assert bpy.context.view_layer.objects.get(retopo.name) is None
for key in tuple(retopo.keys()):
    if str(key).startswith("flowpatch_"):
        del retopo[key]
for key in tuple(target.keys()):
    if str(key).startswith("flowpatch_"):
        del target[key]
settings.target = target
settings.continue_on = retopo
activate(target)
target_before = flowpatch_properties(target)
retopo_before = flowpatch_properties(retopo)
retopo_geometry_before = (
    len(retopo.data.vertices),
    len(retopo.data.edges),
    len(retopo.data.polygons),
)

original_select = operators._select_session_edit_members


def forced_selection_failure(_context, _retopo):
    raise operators.SessionStartTransactionError(
        "FORCED_SELECTION_FAILURE",
        "select_retopo",
        "Forced R1 selection failure.",
    )


operators._select_session_edit_members = forced_selection_failure
try:
    reporter = ReportCapture(target.name)
    result = operators.FLOWPATCH_OT_start_session.execute(reporter, bpy.context)
finally:
    operators._select_session_edit_members = original_select

assert result == {"CANCELLED"}
assert bpy.context.mode == "OBJECT"
assert bpy.context.active_object is target
assert target.select_get(view_layer=bpy.context.view_layer)
assert bpy.context.view_layer.objects.get(retopo.name) is None
assert active_collection.objects.get(retopo.name) is None
assert foreign_collection.objects.get(retopo.name) is retopo
assert flowpatch_properties(target) == target_before
assert flowpatch_properties(retopo) == retopo_before
assert retopo_geometry_before == (
    len(retopo.data.vertices),
    len(retopo.data.edges),
    len(retopo.data.polygons),
)
assert settings.target is target
assert settings.continue_on is retopo
assert reporter.messages[-1]["message"] == "Forced R1 selection failure."
existing_rollback_result = sorted(result)

# A forced failure after creating a new retopo object must remove the object,
# its orphan Mesh datablock, project UUID changes, and restore Edit Mode.
settings.continue_on = None
settings.target = target
activate(target, edit=True)
target_before = flowpatch_properties(target)
object_names_before = tuple(sorted(obj.name for obj in bpy.data.objects))
mesh_names_before = tuple(sorted(mesh.name for mesh in bpy.data.meshes))

operators._select_session_edit_members = forced_selection_failure
try:
    reporter = ReportCapture(target.name)
    result = operators.FLOWPATCH_OT_start_session.execute(reporter, bpy.context)
finally:
    operators._select_session_edit_members = original_select

assert result == {"CANCELLED"}
assert bpy.context.mode == "EDIT_MESH"
assert bpy.context.edit_object is target
assert target.select_get(view_layer=bpy.context.view_layer)
assert flowpatch_properties(target) == target_before
assert tuple(sorted(obj.name for obj in bpy.data.objects)) == object_names_before
assert tuple(sorted(mesh.name for mesh in bpy.data.meshes)) == mesh_names_before
assert settings.target is target
assert settings.continue_on is None
assert reporter.messages[-1]["message"] == "Forced R1 selection failure."
created_rollback_result = sorted(result)

# Background mode cannot start the viewport modal. That failure must still
# remove the just-created object/mesh and leave no active session or metadata.
bpy.ops.object.mode_set(mode="OBJECT")
settings.target = target
settings.continue_on = None
activate(target)
target_before = flowpatch_properties(target)
object_names_before = tuple(sorted(obj.name for obj in bpy.data.objects))
mesh_names_before = tuple(sorted(mesh.name for mesh in bpy.data.meshes))
reporter = ReportCapture(target.name, launch_modal=True)
result = operators.FLOWPATCH_OT_start_session.execute(reporter, bpy.context)

assert result == {"CANCELLED"}
assert bpy.context.mode == "OBJECT"
assert bpy.context.active_object is target
assert target.select_get(view_layer=bpy.context.view_layer)
assert flowpatch_properties(target) == target_before
assert tuple(sorted(obj.name for obj in bpy.data.objects)) == object_names_before
assert tuple(sorted(mesh.name for mesh in bpy.data.meshes)) == mesh_names_before
assert settings.target is target
assert settings.continue_on is None
assert settings.session_active is False
assert operators.FLOWPATCH_OT_guide_session._active_instance is None
modal_rollback_result = sorted(result)

# The ordinary create-new path must finish with a linked, selected, editable,
# UUID-owned object and no orphan Mesh datablock.
bpy.ops.object.mode_set(mode="OBJECT")
settings.target = target
settings.continue_on = None
activate(target)
objects_before_success = tuple(bpy.data.objects)
reporter = ReportCapture(target.name)
result = operators.FLOWPATCH_OT_start_session.execute(reporter, bpy.context)
created_retopo = bpy.context.edit_object

assert result == {"FINISHED"}
assert created_retopo is not None and created_retopo is not target
assert not any(created_retopo is obj for obj in objects_before_success)
assert bpy.context.view_layer.objects.get(created_retopo.name) is created_retopo
assert active_collection.objects.get(created_retopo.name) is created_retopo
assert created_retopo.select_get(view_layer=bpy.context.view_layer)
assert bpy.context.view_layer.objects.active is created_retopo
assert bpy.context.mode == "EDIT_MESH"
assert read_project_record(created_retopo) is not None
assert created_retopo.data.users > 0
assert audit_projects(tuple(bpy.data.objects)) == ()
created_success_result = sorted(result)

evidence = {
    "status": "passed",
    "version": list(flowpatch_retopo.bl_info["version"]),
    "foreign_object_linked_before_selection": True,
    "linked_start_result": linked_result,
    "view_layer": bpy.context.view_layer.name,
    "active_collection": active_collection.name,
    "existing_link_rollback_result": existing_rollback_result,
    "existing_metadata_restored": True,
    "existing_geometry_preserved": True,
    "created_object_removed": True,
    "created_mesh_removed": True,
    "created_metadata_restored": True,
    "edit_mode_restored": bpy.context.mode,
    "modal_failure_rollback_result": modal_rollback_result,
    "modal_failure_left_active_session": False,
    "created_success_result": created_success_result,
    "created_success_object": created_retopo.name,
    "created_success_mesh_users": created_retopo.data.users,
    "project_audit_issues": [],
}
run_dir = Path(args.run_dir)
run_dir.mkdir(parents=True, exist_ok=True)
(run_dir / "viewlayer_transaction_evidence.json").write_text(
    json.dumps(evidence, indent=2),
    encoding="utf-8",
)
print("FLOWPATCH_R1_VIEWLAYER_PASS", json.dumps(evidence, sort_keys=True))
