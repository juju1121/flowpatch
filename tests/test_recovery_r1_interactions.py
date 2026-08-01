import ast
import importlib.util
import math
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
OPERATORS_PATH = ROOT / "operators.py"
REGION_SOLVER_PATH = ROOT / "region_solver.py"
HOVER_TRANSFORM_PATH = ROOT / "hover_transform.py"


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


region_solver = load_module("flowpatch_r1_region_solver", REGION_SOLVER_PATH)
hover_transform = load_module("flowpatch_r1_hover_transform", HOVER_TRANSFORM_PATH)


class InteractionRecoveryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.operators = OPERATORS_PATH.read_text(encoding="utf-8")
        ast.parse(cls.operators, filename="operators.py")

    def test_irregular_closed_quad_is_recognized(self):
        points = (
            (0.0, 0.0),
            (3.0, 0.1),
            (7.0, -0.1),
            (10.0, 0.0),
            (10.2, 4.0),
            (10.0, 8.0),
            (6.0, 8.2),
            (0.0, 8.0),
            (-0.2, 4.0),
            (0.0, 0.0),
        )
        corners = region_solver.infer_closed_quad_corner_indices(
            points,
            close_tolerance=1.0,
        )
        self.assertEqual(len(corners), 4)
        self.assertEqual(set(corners), {0, 3, 5, 7})

    def test_circle_is_not_misclassified_as_quad(self):
        points = [
            (
                math.cos(index * math.tau / 24.0) * 100.0,
                math.sin(index * math.tau / 24.0) * 100.0,
            )
            for index in range(24)
        ]
        points.append(points[0])
        self.assertEqual(
            region_solver.infer_closed_quad_corner_indices(
                points,
                close_tolerance=1.0,
            ),
            (),
        )

    def test_hover_control_is_move_only(self):
        decision = hover_transform.resolve_hover_transform(
            "MOVE",
            hover_kind="GUIDE_CONTROL",
            hover_refs=((2, 4),),
        )
        self.assertTrue(decision.accepted)
        self.assertEqual(decision.refs, ((2, 4),))
        rejected = hover_transform.resolve_hover_transform(
            "ROTATE",
            hover_kind="GUIDE_CONTROL",
            hover_refs=((2, 4),),
        )
        self.assertFalse(rejected.accepted)
        self.assertEqual(rejected.reason_code, "HOVER_EDGE_REQUIRED")

    def test_closed_quad_enters_four_side_pipeline(self):
        self.assertIn("infer_closed_quad_corner_indices(", self.operators)
        self.assertIn("split_closed_guide_into_sides(", self.operators)
        self.assertIn('"closed_quad_normalized"', self.operators)

    def test_delete_and_selection_contract_is_explicit(self):
        self.assertIn("def _hover_delete_target(", self.operators)
        self.assertIn("def _segment_from_selected_points(", self.operators)
        self.assertIn('return "SHAPE", guide_indices, -1', self.operators)
        self.assertIn("delete_guide_segment(", self.operators)


if __name__ == "__main__":
    unittest.main()
