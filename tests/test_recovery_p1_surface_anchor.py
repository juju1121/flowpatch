import ast
import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
GUIDE_GRAPH_PATH = ROOT / "guide_graph.py"
GUIDES_PATH = ROOT / "guides.py"
OPERATORS_PATH = ROOT / "operators.py"
PROJECTION_PATH = ROOT / "projection.py"
PROPERTIES_PATH = ROOT / "properties.py"
UI_PATH = ROOT / "ui.py"


class SurfaceAnchorStaticTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.guide_graph = GUIDE_GRAPH_PATH.read_text(encoding="utf-8")
        cls.guides = GUIDES_PATH.read_text(encoding="utf-8")
        cls.operators = OPERATORS_PATH.read_text(encoding="utf-8")
        cls.projection = PROJECTION_PATH.read_text(encoding="utf-8")
        cls.properties = PROPERTIES_PATH.read_text(encoding="utf-8")
        cls.ui = UI_PATH.read_text(encoding="utf-8")
        for source in (
            cls.guide_graph,
            cls.guides,
            cls.operators,
            cls.projection,
            cls.properties,
            cls.ui,
        ):
            ast.parse(source)

    def test_anchor_has_complete_persistent_surface_identity(self):
        for field_name in (
            "target_object_uuid",
            "face_index",
            "triangle_index",
            "barycentric",
            "local_position",
            "local_normal",
            "normal_offset",
            "topology_revision",
            "shell_component",
        ):
            self.assertRegex(
                self.guide_graph,
                re.compile(rf"\b{field_name}\b"),
            )
        self.assertIn('"shell_component"', self.guides)

    def test_projector_uses_local_continuity_and_last_valid_contract(self):
        self.assertIn("def nearest_world_continuous(", self.projection)
        self.assertIn("def raycast_world_continuous(", self.projection)
        self.assertIn("_face_neighbors", self.projection)
        self.assertIn("_face_components", self.projection)
        self.assertIn("normal_continuity_cos", self.projection)
        self.assertIn("world_from_anchor", self.operators)
        self.assertIn("REJECTED_PROJECTION", self.operators)
        self.assertIn(
            'retained.validation_status = "REJECTED_PROJECTION"',
            self.operators,
        )
        self.assertIn("rejected_previews = [", self.operators)

    def test_stroke_filter_is_followed_by_surface_reprojection(self):
        regularize_start = self.operators.index("def _regularize_stroke(")
        regularize_end = self.operators.index(
            "def _bounded_snap_radius(",
            regularize_start,
        )
        source = self.operators[regularize_start:regularize_end]
        filter_index = source.index("_filter_guide_world_by_view(")
        reprojection_index = source.index(
            "nearest_world_continuous(",
            filter_index,
        )
        self.assertGreater(reprojection_index, filter_index)
        self.assertIn("self._stroke_anchors = final_anchors", source)

    def test_draw_sampling_probes_endpoint_before_anchor_continuity(self):
        start = self.operators.index("def _append_surface_sample(")
        end = self.operators.index("previous_event_mouse =", start)
        source = self.operators[start:end]
        self.assertIn("# This is a visibility probe only.", source)
        self.assertIn("previous_anchor=None", source)
        self.assertIn("front-facing hit on the locked Surface", source)

    def test_active_session_locks_visible_surface_picker(self):
        self.assertIn("surface.enabled = not settings.session_active", self.ui)
        self.assertIn("Surface is locked while drawing.", self.ui)

    def test_preview_reports_and_enforces_planar_metrics(self):
        for metric in (
            "patch_max_plane_deviation",
            "min_quad_signed_area",
            "max_edge_ratio",
            "normal_flip_count",
        ):
            self.assertIn(metric, self.guides)
        self.assertIn("flat_target_tolerance", self.guides)
        self.assertIn("REJECTED_PROJECTION", self.guides)
        self.assertIn("_resample_polyline_with_anchors", self.guides)

    def test_fairing_is_tangent_only_and_transactional(self):
        start = self.guides.index("def fair_guides_tangent(")
        end = self.guides.index("def load_guides(", start)
        source = self.guides[start:end]
        self.assertIn("staged_guides = clone_guides(guides)", source)
        self.assertIn("displacement.dot(", source)
        self.assertIn("nearest_world_continuous(", source)
        self.assertIn("range(1, len(source) - 1)", source)

    def test_continuity_defaults_are_advanced_not_primary_ui(self):
        advanced_start = self.ui.index(
            "class VIEW3D_PT_flowpatch_advanced"
        )
        for property_name in (
            "frontface_epsilon",
            "max_projection_distance",
            "max_surface_step",
            "normal_continuity_cos",
            "guide_fair_strength",
            "guide_fair_iterations",
            "flat_target_tolerance",
        ):
            self.assertIn(property_name, self.properties)
            self.assertGreater(
                self.ui.index(property_name),
                advanced_start,
            )


if __name__ == "__main__":
    unittest.main()
