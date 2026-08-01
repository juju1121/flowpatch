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


def make_quad(name):
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
    bpy.context.scene.collection.objects.link(obj)
    return obj


class ReportCapture:
    def __init__(self):
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
from flowpatch_retopo.project_store import OBJECT_UUID_KEY
from flowpatch_retopo.project_store import PROJECT_RECORD_KEY
from flowpatch_retopo.project_store import PROJECT_UUID_KEY
from flowpatch_retopo.project_store import ProjectStoreError
from flowpatch_retopo.project_store import read_project_record


clear_scene()
flowpatch_retopo.register()
settings = bpy.context.scene.flowpatch_retopo

target = make_quad("LaunchGuardTarget")
retopo = operators.create_session_object(bpy.context, target)
settings.target = None
resolved = operators._resolve_session_target(bpy.context, retopo)
assert resolved is target
assert settings.target is target
resolved_name = resolved.name

record_before = read_project_record(retopo)
record_payload_before = retopo[PROJECT_RECORD_KEY]
project_uuid_before = retopo[PROJECT_UUID_KEY]
target_geometry_before = (
    len(target.data.vertices),
    len(target.data.edges),
    len(target.data.polygons),
)
retopo_geometry_before = (
    len(retopo.data.vertices),
    len(retopo.data.edges),
    len(retopo.data.polygons),
)
copies = []
for index in range(3):
    clone = target.copy()
    clone.data = target.data.copy()
    clone.name = f"LaunchGuardTargetCopy{index + 1}"
    bpy.context.scene.collection.objects.link(clone)
    copies.append(clone)
assert all(
    clone[OBJECT_UUID_KEY] == record_before.target_object_uuid
    for clone in copies
)

settings.target = target
resolved_after_copies = operators._resolve_session_target(
    bpy.context,
    retopo,
)
assert resolved_after_copies is target
assert all(OBJECT_UUID_KEY not in clone for clone in copies)
assert read_project_record(retopo) == record_before
assert retopo[PROJECT_RECORD_KEY] == record_payload_before
assert retopo[PROJECT_UUID_KEY] == project_uuid_before
assert target[OBJECT_UUID_KEY] == record_before.target_object_uuid
assert target_geometry_before == (
    len(target.data.vertices),
    len(target.data.edges),
    len(target.data.polygons),
)
assert retopo_geometry_before == (
    len(retopo.data.vertices),
    len(retopo.data.edges),
    len(retopo.data.polygons),
)
assert audit_projects(tuple(bpy.data.objects)) == ()

protected = target.copy()
protected.data = target.data.copy()
protected.name = "LaunchGuardProtectedCopy"
protected[PROJECT_UUID_KEY] = project_uuid_before
protected[PROJECT_RECORD_KEY] = record_payload_before
bpy.context.scene.collection.objects.link(protected)
protected_before = {
    key: protected[key]
    for key in protected.keys()
}
try:
    operators._resolve_session_target(bpy.context, retopo)
except ProjectStoreError as exc:
    protected_reason = exc.reason_code
else:
    raise AssertionError("Project-bearing copied UUID was not rejected")
assert protected_reason == "DUPLICATE_OBJECT_UUID_HAS_PROJECT"
assert {
    key: protected[key]
    for key in protected.keys()
} == protected_before
assert read_project_record(retopo) == record_before

copy_names = [clone.name for clone in copies]
clear_scene()
plain_retopo = make_quad("LaunchGuardPlainRetopo")
plain_retopo.select_set(True)
bpy.context.view_layer.objects.active = plain_retopo
bpy.ops.object.mode_set(mode="EDIT")
settings.target = None
settings.continue_on = None
operators.FLOWPATCH_OT_guide_session._active_instance = None

properties_before = {
    str(key): plain_retopo[key]
    for key in plain_retopo.keys()
    if str(key).startswith("flowpatch_")
}
reporter = ReportCapture()
result = operators.FLOWPATCH_OT_toggle_tool.invoke(
    reporter,
    bpy.context,
    None,
)
properties_after = {
    str(key): plain_retopo[key]
    for key in plain_retopo.keys()
    if str(key).startswith("flowpatch_")
}

assert result == {"CANCELLED"}
assert bpy.context.mode == "EDIT_MESH"
assert bpy.context.edit_object is plain_retopo
assert settings.target is None
assert settings.session_active is False
assert properties_after == properties_before
assert operators.FLOWPATCH_OT_guide_session._active_instance is None
assert reporter.messages == [
    {
        "levels": ["ERROR"],
        "message": (
            "Choose a mesh Surface in the FlowPatch panel before pressing F7."
        ),
    }
]


class ActiveSessionCapture:
    def __init__(self, retopo_object):
        self._retopo = retopo_object
        self._session_epoch = 41
        self.calls = []

    def _finalize_session(self, context, commit_pending=False):
        self.calls.append(
            {
                "context_mode": context.mode,
                "commit_pending": bool(commit_pending),
            }
        )
        operators.FLOWPATCH_OT_guide_session._active_instance = None


bpy.ops.object.mode_set(mode="OBJECT")
f7_stop_session = ActiveSessionCapture(plain_retopo)
operators.FLOWPATCH_OT_guide_session._active_instance = f7_stop_session
f7_stop_reporter = ReportCapture()
f7_stop_result = operators.FLOWPATCH_OT_toggle_tool.invoke(
    f7_stop_reporter,
    bpy.context,
    None,
)
assert f7_stop_result == {"FINISHED"}
assert f7_stop_session.calls == [
    {
        "context_mode": "OBJECT",
        "commit_pending": False,
    }
]
assert bpy.context.mode == "OBJECT"
assert operators.FLOWPATCH_OT_guide_session._active_instance is None
assert f7_stop_reporter.messages == [
    {
        "levels": ["INFO"],
        "message": "FlowPatch session stopped; geometry was preserved.",
    }
]

panel_stop_session = ActiveSessionCapture(plain_retopo)
operators.FLOWPATCH_OT_guide_session._active_instance = panel_stop_session
assert operators.FLOWPATCH_OT_stop_session.poll(bpy.context)
panel_stop_reporter = ReportCapture()
panel_stop_result = operators.FLOWPATCH_OT_stop_session.execute(
    panel_stop_reporter,
    bpy.context,
)
assert panel_stop_result == {"FINISHED"}
assert panel_stop_session.calls == [
    {
        "context_mode": "OBJECT",
        "commit_pending": False,
    }
]
assert bpy.context.mode == "OBJECT"
assert operators.FLOWPATCH_OT_guide_session._active_instance is None
assert panel_stop_reporter.messages == [
    {
        "levels": ["INFO"],
        "message": "FlowPatch session stopped; geometry was preserved.",
    }
]

evidence = {
    "status": "passed",
    "version": list(flowpatch_retopo.bl_info["version"]),
    "project_resume_target_resolved": resolved_name,
    "passive_uuid_copies_repaired": copy_names,
    "project_metadata_preserved": True,
    "geometry_preserved": True,
    "protected_copy_reason": protected_reason,
    "missing_surface_result": sorted(result),
    "missing_surface_report": reporter.messages,
    "mode_preserved": bpy.context.mode,
    "retopo_preserved": plain_retopo.name,
    "flowpatch_properties_unchanged": properties_after == properties_before,
    "session_started": settings.session_active,
    "f7_object_mode_stop_result": sorted(f7_stop_result),
    "f7_object_mode_stop_finalize_calls": f7_stop_session.calls,
    "panel_object_mode_stop_result": sorted(panel_stop_result),
    "panel_object_mode_stop_finalize_calls": panel_stop_session.calls,
}
run_dir = Path(args.run_dir)
run_dir.mkdir(parents=True, exist_ok=True)
(run_dir / "launch_guard_evidence.json").write_text(
    json.dumps(evidence, indent=2),
    encoding="utf-8",
)
print("FLOWPATCH_LAUNCH_GUARD_PASS", json.dumps(evidence, sort_keys=True))
