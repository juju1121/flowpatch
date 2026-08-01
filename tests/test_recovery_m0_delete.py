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
            "delete_guide_control",
            "delete_guide_node",
            "delete_guide_point",
            "delete_guide_edge",
        ):
            self.assertIn(f"def {name}(", self.guide_graph)
        self.assertIn("JUNCTION_CONFIRM_REQUIRED", self.guide_graph)
        self.assertIn("PROTECTED_CORNER", self.guide_graph)
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

    def test_built_cell_removal_uses_uid_face_ownership(self):
        self.assertIn("def _grid_face_uid_keys(", self.guides)
        self.assertIn("def remove_built_cell_geometry(", self.guides)
        self.assertIn("Built-region topology changed", self.guides)
        self.assertIn("load_boundary_registry(obj).items()", self.guides)
        self.assertIn("load_node_vertex_registry(obj).items()", self.guides)

    def test_package_identity_is_unique_for_m0(self):
        self.assertIn(
            '"version": (1, 4, 18)',
            INIT_PATH.read_text(encoding="utf-8"),
        )
        self.assertIn(
            'version = "1.4.18"',
            MANIFEST_PATH.read_text(encoding="utf-8"),
        )
        build_identity = BUILD_IDENTITY_PATH.read_text(encoding="utf-8")
        self.assertIn('ADDON_VERSION = (1, 4, 18)', build_identity)
        self.assertIn('BUILD_ID = "recovery-m0-guide-delete-20260731"', build_identity)


if __name__ == "__main__":
    unittest.main()
