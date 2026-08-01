import ast
import importlib.util
import json
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = (
    ROOT
    / "pen_state.py"
)
OPERATORS_PATH = (
    ROOT
    / "operators.py"
)
SPEC = importlib.util.spec_from_file_location(
    "flowpatch_pen_state_under_test",
    MODULE_PATH,
)
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)

PenPhase = MODULE.PenPhase
PenState = MODULE.PenState


class PenStateTests(unittest.TestCase):
    def test_happy_path_and_one_finalize_guard(self):
        state = PenState()

        started = state.begin()
        self.assertTrue(started.accepted)
        self.assertEqual(state.phase, PenPhase.POINTER_DOWN)
        self.assertTrue(state.pointer_captured)

        self.assertTrue(state.projected_sample().accepted)
        self.assertEqual(state.phase, PenPhase.DRAWING)
        self.assertEqual(state.projected_sample_count, 1)

        self.assertTrue(state.claim_finalize().accepted)
        self.assertEqual(state.phase, PenPhase.FINALIZING)
        self.assertFalse(state.pointer_captured)
        self.assertFalse(state.claim_finalize().accepted)
        self.assertEqual(
            state.claim_finalize().reason_code,
            "STROKE_ALREADY_FINALIZED",
        )

        state.reset()
        self.assertEqual(state.phase, PenPhase.IDLE)

    def test_cancel_does_not_mutate_persistent_metadata(self):
        metadata = {
            "guide_uuid": "guide-17",
            "shared_boundary_uids": [11, 12, 13],
            "face_count": 0,
        }
        before = json.loads(json.dumps(metadata))
        state = PenState()

        state.begin()
        state.projected_sample()
        cancelled = state.cancel("ESC")

        self.assertTrue(cancelled.accepted)
        self.assertEqual(state.phase, PenPhase.CANCELLING)
        self.assertEqual(state.cancel_reason, "ESC")
        self.assertEqual(metadata, before)

        state.reset()
        self.assertEqual(state.phase, PenPhase.IDLE)

    def test_invalid_transitions_are_non_destructive(self):
        state = PenState()
        before = state.snapshot()

        self.assertFalse(state.projected_sample().accepted)
        self.assertFalse(state.projection_miss().accepted)
        self.assertFalse(state.claim_finalize().accepted)
        self.assertEqual(state.snapshot(), before)

        state.begin()
        active = state.snapshot()
        duplicate = state.begin()
        self.assertFalse(duplicate.accepted)
        self.assertEqual(
            duplicate.reason_code,
            "STROKE_ALREADY_ACTIVE",
        )
        self.assertEqual(state.snapshot(), active)

    def test_local_ray_miss_preserves_active_stroke(self):
        state = PenState()
        capabilities = {
            "DRAW": True,
            "EDIT": True,
            "BUILD": False,
        }
        before = dict(capabilities)

        state.begin()
        state.projected_sample()
        missed = state.projection_miss()

        self.assertTrue(missed.accepted)
        self.assertEqual(state.phase, PenPhase.DRAWING)
        self.assertTrue(state.pointer_captured)
        self.assertTrue(state.projection_gap)
        self.assertEqual(capabilities, before)

        state.projected_sample()
        self.assertFalse(state.projection_gap)
        self.assertEqual(state.projected_sample_count, 2)

    def test_repeat_operations_use_new_serials(self):
        state = PenState()
        serials = []

        for _index in range(128):
            self.assertTrue(state.begin().accepted)
            serials.append(state.stroke_serial)
            self.assertTrue(state.projected_sample().accepted)
            self.assertTrue(state.claim_finalize().accepted)
            self.assertFalse(state.claim_finalize().accepted)
            state.reset()

        self.assertEqual(serials, list(range(1, 129)))
        self.assertEqual(state.phase, PenPhase.IDLE)
        self.assertFalse(state.pointer_captured)

    def test_snapshot_is_json_safe_and_consistent(self):
        state = PenState()
        state.begin()
        state.projected_sample()
        state.projection_miss()

        snapshot = state.snapshot()
        encoded = json.dumps(snapshot, sort_keys=True)

        self.assertIn('"phase": "DRAWING"', encoded)
        self.assertEqual(snapshot["stroke_serial"], 1)
        self.assertEqual(snapshot["finalized_stroke_serial"], 0)
        self.assertTrue(snapshot["projection_gap"])
        self.assertEqual(snapshot["projected_sample_count"], 1)

    def test_guide_modal_routes_lifecycle_through_pen_state(self):
        source = OPERATORS_PATH.read_text(encoding="utf-8")
        tree = ast.parse(source)
        guide_class = next(
            node
            for node in tree.body
            if isinstance(node, ast.ClassDef)
            and node.name == "FLOWPATCH_OT_guide_session"
        )
        owned_methods = {
            "_suspend_for_native_ui",
            "_append_surface_sample",
            "_finish_surface_stroke",
            "_clear_stroke",
            "_cleanup",
            "_modal_impl",
        }
        lifecycle_fields = {
            "_pointer_state",
            "_drawing",
            "_mouse_captured",
            "_stroke_had_projection_gap",
            "_stroke_serial",
            "_finalized_stroke_serial",
        }
        direct_assignments = []
        for method in guide_class.body:
            if (
                not isinstance(method, ast.FunctionDef)
                or method.name not in owned_methods
            ):
                continue
            for node in ast.walk(method):
                targets = []
                if isinstance(node, ast.Assign):
                    targets = node.targets
                elif isinstance(node, ast.AnnAssign):
                    targets = [node.target]
                elif isinstance(node, ast.AugAssign):
                    targets = [node.target]
                for target in targets:
                    if (
                        isinstance(target, ast.Attribute)
                        and isinstance(target.value, ast.Name)
                        and target.value.id == "self"
                        and target.attr in lifecycle_fields
                    ):
                        direct_assignments.append(
                            (method.name, target.attr)
                        )

        self.assertEqual(direct_assignments, [])
        for required in (
            "_pen_state = PenState()",
            "_pen_begin(self)",
            "_pen_claim_finalize(self)",
            '_cancel_surface_stroke("ESC")',
            '_cancel_surface_stroke("RMB")',
            '_cancel_surface_stroke("TOOLBAR_ACTION")',
            "_pen_can_append(self)",
            "_pen_projection_miss(self)",
        ):
            self.assertIn(required, source)

    def test_toolbar_capabilities_do_not_depend_on_pointer_hit_state(self):
        tree = ast.parse(OPERATORS_PATH.read_text(encoding="utf-8"))
        guide_class = next(
            node
            for node in tree.body
            if isinstance(node, ast.ClassDef)
            and node.name == "FLOWPATCH_OT_guide_session"
        )
        method = next(
            node
            for node in guide_class.body
            if isinstance(node, ast.FunctionDef)
            and node.name == "_toolbar_capabilities"
        )
        referenced_attributes = {
            node.attr
            for node in ast.walk(method)
            if isinstance(node, ast.Attribute)
        }
        self.assertTrue(
            referenced_attributes.isdisjoint(
                {
                    "_pen_state",
                    "_pointer_state",
                    "_drawing",
                    "_mouse_captured",
                    "_stroke_had_projection_gap",
                }
            )
        )

    def test_f7_stop_uses_owned_cleanup_without_nested_operator_poll(self):
        source = OPERATORS_PATH.read_text(encoding="utf-8")
        tree = ast.parse(source)
        toggle_class = next(
            node
            for node in tree.body
            if isinstance(node, ast.ClassDef)
            and node.name == "FLOWPATCH_OT_toggle_tool"
        )
        invoke = next(
            node
            for node in toggle_class.body
            if isinstance(node, ast.FunctionDef)
            and node.name == "invoke"
        )
        invoke_source = ast.get_source_segment(source, invoke)
        self.assertIn("_stop_active_session(", invoke_source)
        self.assertNotIn(
            'bpy.ops.flowpatch.stop_session("EXEC_DEFAULT")',
            invoke_source,
        )
        self.assertNotIn(
            "active_session._finalize_session",
            invoke_source,
        )

    def test_stop_session_poll_accepts_an_active_object_mode_session(self):
        source = OPERATORS_PATH.read_text(encoding="utf-8")
        tree = ast.parse(source)
        stop_class = next(
            node
            for node in tree.body
            if isinstance(node, ast.ClassDef)
            and node.name == "FLOWPATCH_OT_stop_session"
        )
        poll = next(
            node
            for node in stop_class.body
            if isinstance(node, ast.FunctionDef)
            and node.name == "poll"
        )
        poll_source = ast.get_source_segment(source, poll)
        self.assertIn("_active_instance", poll_source)
        self.assertNotIn("_edit_mesh_poll", poll_source)

    def test_f7_start_preflights_edit_mode_surface(self):
        source = OPERATORS_PATH.read_text(encoding="utf-8")
        tree = ast.parse(source)
        toggle_class = next(
            node
            for node in tree.body
            if isinstance(node, ast.ClassDef)
            and node.name == "FLOWPATCH_OT_toggle_tool"
        )
        invoke = next(
            node
            for node in toggle_class.body
            if isinstance(node, ast.FunctionDef)
            and node.name == "invoke"
        )
        invoke_source = ast.get_source_segment(source, invoke)
        self.assertIn(
            "_resolve_session_target(",
            invoke_source,
        )
        self.assertIn(
            "Choose a mesh Surface in the FlowPatch panel",
            invoke_source,
        )

    def test_f7_start_contains_nested_operator_runtime_errors(self):
        source = OPERATORS_PATH.read_text(encoding="utf-8")
        tree = ast.parse(source)
        toggle_class = next(
            node
            for node in tree.body
            if isinstance(node, ast.ClassDef)
            and node.name == "FLOWPATCH_OT_toggle_tool"
        )
        invoke = next(
            node
            for node in toggle_class.body
            if isinstance(node, ast.FunctionDef)
            and node.name == "invoke"
        )
        runtime_handlers = [
            handler
            for node in ast.walk(invoke)
            if isinstance(node, ast.Try)
            for handler in node.handlers
            if isinstance(handler.type, ast.Name)
            and handler.type.id == "RuntimeError"
        ]
        self.assertTrue(runtime_handlers)
        handler_source = ast.get_source_segment(
            source,
            runtime_handlers[0],
        )
        self.assertIn("NESTED_OPERATOR_CANCELLED", handler_source)
        self.assertIn('return {"CANCELLED"}', handler_source)


if __name__ == "__main__":
    unittest.main()
