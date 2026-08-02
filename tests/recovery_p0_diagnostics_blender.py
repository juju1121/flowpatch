import argparse
import json
from pathlib import Path
from types import SimpleNamespace
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


def activate(obj):
    for selected in tuple(bpy.context.selected_objects):
        selected.select_set(False)
    obj.select_set(True)
    bpy.context.view_layer.objects.active = obj


def unlink_from_scene(obj):
    for collection in tuple(obj.users_collection):
        collection.objects.unlink(obj)
    bpy.context.view_layer.update()


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


class BrokenPollContext:
    window_manager = object()

    @property
    def area(self):
        raise ReferenceError("stale area")


class DebugContextProxy:
    def __init__(self, base):
        self._base = base
        self.window_manager = SimpleNamespace(clipboard="")

    def __getattr__(self, name):
        return getattr(self._base, name)


args = parse_args()
if args.addon_root not in sys.path:
    sys.path.insert(0, args.addon_root)

import flowpatch_retopo
from flowpatch_retopo import operators
from flowpatch_retopo.project_store import bind_project
from flowpatch_retopo.project_store import clear_project_identity


clear_scene()
evidence = {}

# Valid project, rename, unlink, missing registry, and missing target all use
# the same persistent UUID identity while live objects are reacquired.
target = make_quad("P0Target")
retopo = make_quad("P0Retopo")
record = bind_project(target, retopo)
operators._remember_session_identity(
    SimpleNamespace(),
    record,
    target,
    retopo,
)
activate(retopo)

valid = operators.build_debug_state_snapshot(bpy.context)
assert valid["project_object_found"] is True
assert valid["target_object_found"] is True
assert valid["project"]["project_registry_found"] is True
assert valid["project"]["project_registry_matches_identity"] is True
evidence["valid_project"] = valid["project"]

retopo.name = "P0RetopoRenamed"
renamed = operators.build_debug_state_snapshot(bpy.context)
assert renamed["project"]["retopo_object"]["renamed"] is True
assert renamed["project_object_found"] is True
evidence["renamed_project"] = renamed["project"]["retopo_object"]

unlink_from_scene(retopo)
unlinked = operators.build_debug_state_snapshot(bpy.context)
assert unlinked["project_object_found"] is True
assert unlinked["project"]["retopo_object"]["in_view_layer"] is False
assert unlinked["project"]["retopo_object"]["source"] == "BLEND_DATA"
evidence["unlinked_project"] = unlinked["project"]["retopo_object"]

bpy.context.scene.collection.objects.link(retopo)
bpy.context.view_layer.update()
clear_project_identity(retopo, clear_object_uuid=False)
missing_registry = operators.build_debug_state_snapshot(bpy.context)
assert missing_registry["project_object_found"] is True
assert missing_registry["project"]["project_registry_found"] is False
assert "project registry missing" in missing_registry["warnings"]
evidence["missing_registry"] = missing_registry["project"]

bpy.data.objects.remove(target, do_unlink=True)
missing_target = operators.build_debug_state_snapshot(bpy.context)
assert missing_target["project_object_found"] is True
assert missing_target["target_object_found"] is False
assert "target object missing" in missing_target["warnings"]
evidence["missing_target"] = missing_target["project"]

# A live modal operation may still hold an ephemeral RNA wrapper. Deleting its
# object must not break diagnostics because the durable identity is UUID-only.
target2 = make_quad("P0TargetDeletedWrapper")
retopo2 = make_quad("P0RetopoDeletedWrapper")
record2 = bind_project(target2, retopo2)
session = SimpleNamespace(
    _session_alive=True,
    _cleaned=False,
    _retopo=retopo2,
    _target=target2,
    _scene=bpy.context.scene,
)
operators._remember_session_identity(
    session,
    record2,
    target2,
    retopo2,
)
operators.FLOWPATCH_OT_guide_session._active_instance = session
stale_wrapper = retopo2
bpy.data.objects.remove(retopo2, do_unlink=True)
assert operators._object_name(stale_wrapper) == ""

deleted = operators.build_debug_state_snapshot(bpy.context)
assert deleted["project_object_found"] is False
assert deleted["target_object_found"] is True
assert "project object missing" in deleted["warnings"]
json.dumps(deleted)
evidence["deleted_wrapper"] = deleted["project"]

reporter = ReportCapture()
debug_context = DebugContextProxy(bpy.context)
result = operators.FLOWPATCH_OT_copy_debug_state.execute(
    reporter,
    debug_context,
)
assert result == {"FINISHED"}
copied = json.loads(debug_context.window_manager.clipboard)
assert copied["project_object_found"] is False
assert copied["build_identity"]["addon_version"] == "1.4.21"
assert copied["build_identity"]["build_id"] == (
    "core-reset-cr00-20260802"
)
assert copied["build_identity"]["active_source_path"].endswith(
    "operators.py"
)
assert copied["build_identity"]["installed_extension_path"].endswith(
    "extensions\\user_default\\flowpatch_retopo"
)
assert copied["build_identity"]["active_source_is_real_install"] is False
evidence["copy_result"] = {
    "result": sorted(result),
    "reports": reporter.messages,
    "build_identity": copied["build_identity"],
}

assert operators.FLOWPATCH_OT_copy_debug_state.poll(BrokenPollContext()) is False
valid_poll_context = SimpleNamespace(
    window_manager=object(),
    area=SimpleNamespace(type="VIEW_3D"),
)
assert operators.FLOWPATCH_OT_copy_debug_state.poll(valid_poll_context) is True
evidence["poll_guard"] = {
    "stale_context": False,
    "view3d_context": True,
}

operators.FLOWPATCH_OT_guide_session._active_instance = None
run_dir = Path(args.run_dir)
run_dir.mkdir(parents=True, exist_ok=True)
(run_dir / "p0_diagnostics_evidence.json").write_text(
    json.dumps(evidence, indent=2, sort_keys=True),
    encoding="utf-8",
)
print("FLOWPATCH_P0_DIAGNOSTICS_PASS")
