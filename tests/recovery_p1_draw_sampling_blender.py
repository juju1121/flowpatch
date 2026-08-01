import argparse
import json
from pathlib import Path
from types import SimpleNamespace
import sys

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

from flowpatch_retopo.operators import FLOWPATCH_OT_guide_session


class FakeProjector:
    def __init__(self):
        self.calls = []

    def raycast_region_continuous(
        self,
        _region,
        _region_3d,
        mouse,
        *,
        previous_anchor,
        **_kwargs,
    ):
        self.calls.append(
            {
                "mouse_x": float(mouse.x),
                "has_previous_anchor": previous_anchor is not None,
            }
        )
        if previous_anchor is not None and mouse.x >= 40.0:
            return None
        world = Vector((mouse.x, mouse.y, 0.0))
        return (world, Vector((0.0, 0.0, 1.0)), 0, 0, world.copy())


projector = FakeProjector()
settings = SimpleNamespace(
    sample_spacing_px=10.0,
    surface_offset=0.0,
    frontface_epsilon=0.0,
    max_surface_step=1.0,
    normal_continuity_cos=0.0,
)
context = SimpleNamespace(scene=SimpleNamespace(flowpatch_retopo=settings))
session = SimpleNamespace(
    _target_object_uuid="test-target",
    _stroke_anchors=[Vector((0.0, 0.0, 0.0))],
    _stroke_screen=[Vector((0.0, 0.0))],
    _stroke_world=[Vector((0.0, 0.0, 0.0))],
    _stroke_event_mouse=Vector((0.0, 0.0)),
    _stroke_had_projection_gap=False,
    _stroke_serial=1,
    _projector=projector,
    _region=None,
    _region_3d=None,
    _renderer=SimpleNamespace(stroke=[]),
    _region_mouse=lambda event: Vector(
        (event.mouse_region_x, event.mouse_region_y)
    ),
    _stabilized_mouse=lambda mouse: mouse.copy(),
    _tag_redraw=lambda: None,
    _set_status=lambda _context, _message: None,
    report=lambda _level, _message: None,
)
event = SimpleNamespace(mouse_region_x=50.0, mouse_region_y=0.0)

appended = FLOWPATCH_OT_guide_session._append_surface_sample(
    session,
    context,
    event,
    force=True,
)
assert appended is True
assert len(session._stroke_world) > 1
assert projector.calls[0]["has_previous_anchor"] is False
assert any(call["has_previous_anchor"] for call in projector.calls[1:])
assert max(point.x for point in session._stroke_world) < 40.0

run_dir = Path(args.run_dir)
run_dir.mkdir(parents=True, exist_ok=True)
(run_dir / "p1_draw_sampling_evidence.json").write_text(
    json.dumps(
        {
            "appended": appended,
            "committed_sample_count": len(session._stroke_world),
            "preflight_uses_previous_anchor": projector.calls[0][
                "has_previous_anchor"
            ],
            "candidate_continuity_enforced": any(
                call["has_previous_anchor"] for call in projector.calls[1:]
            ),
            "last_committed_x": max(
                point.x for point in session._stroke_world
            ),
        },
        indent=2,
    ),
    encoding="utf-8",
)
print("FLOWPATCH_P1_DRAW_SAMPLING_PASS")
