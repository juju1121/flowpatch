import ast
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
GUIDE_GRAPH_PATH = ROOT / "guide_graph.py"
GUIDES_PATH = ROOT / "guides.py"
OPERATORS_PATH = ROOT / "operators.py"
INIT_PATH = ROOT / "__init__.py"
MANIFEST_PATH = ROOT / "blender_manifest.toml"
BUILD_IDENTITY_PATH = ROOT / "build_identity.py"


class GuideDeleteStaticTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.guide_graph = GUIDE_GRAPH_PATH.read_text(encoding="utf-8")
        cls.guides = GUIDES_PATH.read_text(encoding="utf-8")
        cls.operators = OPERATORS_PATH.read_text(encoding="utf-8")
        for name, source in (
            ("guide_graph.py", cls.guide_graph),
            ("guides.py", cls.guides),
            ("operators.py", cls.operators),
        ):
            ast.parse(source, filename=name)

    def test_graph_delete_contract_is_topology_aware(self):
        for name in (
            "insert_guide_control",
            "delete_guide_control",
            "delete_guide_node",
            "delete_guide_point",
            "delete_guide_edge",
            "delete_guide_segment",
            "split_closed_guide_into_sides",
        ):
            self.assertIn(f"def {name}(", self.guide_graph)
        self.assertIn("JUNCTION_CONFIRM_REQUIRED", self.guide_graph)
        self.assertNotIn("PROTECTED_CORNER", self.guide_graph)
        self.assertIn("logical_side_id", self.guide_graph)

    def test_modal_delete_requires_an_exact_target(self):
        start = self.operators.index("def _selected_delete_target(")
        end = self.operators.index("def _adjust_density(", start)
        source = self.operators[start:end]
        self.assertIn("EXACT_SELECTION_REQUIRED", source)
        self.assertIn("SHARED_INTERIOR_EDGE_REQUIRES_REFLOW", source)
        self.assertNotIn("len(self._guides) - 1", source)
        self.assertIn("delete_guide_point(", source)
        self.assertIn("delete_guide_edge(", source)
        self.assertIn("delete_guide_segment(", source)
        self.assertIn('target_kind == "SHAPE"', source)

    def test_delete_is_mesh_and_metadata_transactional(self):
        start = self.operators.index("def _delete_guide(")
        end = self.operators.index("def _adjust_density(", start)
        source = self.operators[start:end]
        self.assertIn("_state_snapshot(include_mesh=True)", source)
        self.assertIn("remove_built_cell_geometry(", source)
        self.assertIn("preserve_history=True", source)
        self.assertIn("_restore_state(snapshot)", source)
        self.assertIn("guide_delete_rolled_back", source)

    def test_edge_hit_is_lower_priority_than_control_hit(self):
        control_index = self.operators.index(
            "selected = self._nearest_control(mouse)"
        )
        edge_index = self.operators.index(
            "self._nearest_guide_segment(mouse, radius_px=11.0)",
            control_index,
        )
        self.assertGreater(edge_index, control_index)
        self.assertIn('{"BACK_SPACE", "DEL", "X"}', self.operators)
        self.assertIn("_hover_delete_target(event)", self.operators)

    def test_selection_and_hover_move_use_exact_controls(self):
        self.assertIn("def _segment_from_selected_points(", self.operators)
        self.assertIn("self._selected_segment", self.operators)
        self.assertIn("insert_guide_control(", self.operators)
        self.assertIn('hover_hit["kind"] = "GUIDE_CONTROL"', self.operators)
        renderer_start = self.operators.index("def _selected_guide_paths_world(")
        renderer_end = self.operators.index("def _sync_scene_settings(", renderer_start)
        renderer_source = self.operators[renderer_start:renderer_end]
        self.assertIn("self._selected_guides", renderer_source)
        self.assertIn("self._selected_segment", renderer_source)
        self.assertNotIn("self._selection_guide_indices()", renderer_source)

    def test_auto_build_retains_mesh_backed_local_undo(self):
        start = self.operators.index("def _auto_build_new_previews(")
        end = self.operators.index("def _cycle_active_preview(", start)
        source = self.operators[start:end]
        self.assertIn("_begin_auto_build_mesh_snapshot", source)
        self.assertIn('history_snapshot["_mesh_backup"]', source)
        self.assertIn("preserve_history=True", source)

    def test_built_cell_removal_uses_uid_face_ownership(self):
        self.assertIn("def _grid_face_uid_keys(", self.guides)
        self.assertIn("def remove_built_cell_geometry(", self.guides)
        self.assertIn("Built-region topology changed", self.guides)
        self.assertIn("load_boundary_registry(obj).items()", self.guides)
        self.assertIn("load_node_vertex_registry(obj).items()", self.guides)

    def test_package_identity_is_unique_for_m0(self):
        self.assertIn(
            '"version": (1, 4, 21)',
            INIT_PATH.read_text(encoding="utf-8"),
        )
        self.assertIn(
            'version = "1.4.21"',
            MANIFEST_PATH.read_text(encoding="utf-8"),
        )
        build_identity = BUILD_IDENTITY_PATH.read_text(encoding="utf-8")
        self.assertIn('ADDON_VERSION = (1, 4, 21)', build_identity)
        self.assertIn(
            'BUILD_ID = "core-reset-cr00-20260802"',
            build_identity,
        )


if __name__ == "__main__":
    unittest.main()
