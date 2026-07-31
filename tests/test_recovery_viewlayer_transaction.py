import ast
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
OPERATORS_PATH = ROOT / "operators.py"


class ViewLayerTransactionStaticTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = OPERATORS_PATH.read_text(encoding="utf-8")
        cls.tree = ast.parse(cls.source)

    def _function(self, name):
        return next(
            node
            for node in self.tree.body
            if isinstance(node, (ast.FunctionDef, ast.ClassDef))
            and node.name == name
        )

    def test_membership_helper_links_updates_and_verifies(self):
        helper = self._function("_ensure_session_object_in_view_layer")
        segment = ast.get_source_segment(self.source, helper)
        self.assertLess(
            segment.index("collection.objects.link(retopo)"),
            segment.index("context.view_layer.update()"),
        )
        self.assertIn(
            "if not _object_in_active_view_layer(context, retopo):",
            segment,
        )
        self.assertIn("VIEW_LAYER_MEMBERSHIP_FAILED", segment)

    def test_start_orders_membership_before_selection(self):
        start_class = self._function("FLOWPATCH_OT_start_session")
        execute = next(
            node
            for node in start_class.body
            if isinstance(node, ast.FunctionDef) and node.name == "execute"
        )
        segment = ast.get_source_segment(self.source, execute)
        self.assertLess(
            segment.index("_ensure_session_object_in_view_layer("),
            segment.index("_select_session_edit_members("),
        )
        self.assertLess(
            segment.index("_select_session_edit_members("),
            segment.index('bpy.ops.object.mode_set(mode="EDIT")'),
        )

    def test_start_failure_always_routes_through_rollback(self):
        start_class = self._function("FLOWPATCH_OT_start_session")
        execute = next(
            node
            for node in start_class.body
            if isinstance(node, ast.FunctionDef) and node.name == "execute"
        )
        segment = ast.get_source_segment(self.source, execute)
        self.assertIn("rollback_errors = transaction.rollback()", segment)
        self.assertIn("session_start_transaction_failed", segment)
        self.assertIn("return {\"CANCELLED\"}", segment)

    def test_new_object_cleanup_is_owned_by_transaction(self):
        transaction = self._function("_SessionStartTransaction")
        segment = ast.get_source_segment(self.source, transaction)
        for required in (
            "bpy.data.objects.remove(self.created_object, do_unlink=True)",
            "bpy.data.meshes.remove(self.created_mesh)",
            "_restore_owner_properties(owner, snapshot)",
            "collection.objects.unlink(retopo)",
            'bpy.ops.object.mode_set(mode="EDIT")',
        ):
            self.assertIn(required, segment)

    def test_object_session_flag_waits_for_modal_start(self):
        self.assertIn(
            'retopo["flowpatch_session_active"] = False',
            self.source,
        )
        modal_index = self.source.index("context.window_manager.modal_handler_add(self)")
        active_index = self.source.index(
            'self._retopo["flowpatch_session_active"] = True',
            modal_index,
        )
        self.assertGreater(active_index, modal_index)


if __name__ == "__main__":
    unittest.main()
