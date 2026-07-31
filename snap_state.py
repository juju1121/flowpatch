import math
from dataclasses import dataclass
from enum import Enum


class SnapKind(str, Enum):
    CANONICAL_NODE = "CANONICAL_NODE"
    GUIDE_EDGE = "GUIDE_EDGE"
    TARGET_SURFACE = "TARGET_SURFACE"


_SNAP_PRIORITY = {
    SnapKind.CANONICAL_NODE.value: 0,
    SnapKind.GUIDE_EDGE.value: 1,
    SnapKind.TARGET_SURFACE.value: 2,
}


def bounded_snap_radius(radius_px, hard_max_px):
    try:
        radius = float(radius_px)
        hard_max = float(hard_max_px)
    except (TypeError, ValueError):
        return 0.0
    if not math.isfinite(radius) or not math.isfinite(hard_max):
        return 0.0
    return min(max(radius, 0.0), max(hard_max, 0.0))


def _candidate_key(candidate):
    kind = str(candidate.get("kind", ""))
    distance = float(candidate["distance"])
    stable_id = (
        _safe_int(candidate.get("node_id", 0), 0),
        _safe_int(candidate.get("guide_id", 0), 0),
        _safe_int(candidate.get("segment_index", -1), -1),
    )
    return _SNAP_PRIORITY[kind], distance, stable_id


def _safe_int(value, fallback):
    try:
        return int(value)
    except (TypeError, ValueError):
        return int(fallback)


def _candidate_is_valid(candidate, radius):
    if not isinstance(candidate, dict):
        return False
    kind = str(candidate.get("kind", ""))
    if kind not in _SNAP_PRIORITY:
        return False
    try:
        distance = float(candidate["distance"])
    except (KeyError, TypeError, ValueError):
        return False
    if not math.isfinite(distance) or distance < 0.0 or distance > radius:
        return False
    if "point_2d" not in candidate or "point_world" not in candidate:
        return False
    if kind == SnapKind.CANONICAL_NODE.value:
        return _safe_int(candidate.get("node_id", 0), 0) > 0
    if kind == SnapKind.GUIDE_EDGE.value:
        return (
            _safe_int(candidate.get("guide_id", 0), 0) > 0
            and _safe_int(candidate.get("segment_index", -1), -1) >= 0
            and "segment_world" in candidate
        )
    return True


def select_snap_candidate(candidates, radius_px, hard_max_px):
    radius = bounded_snap_radius(radius_px, hard_max_px)
    valid = [
        dict(candidate)
        for candidate in candidates
        if _candidate_is_valid(candidate, radius)
    ]
    if not valid:
        return None
    return min(valid, key=_candidate_key)


@dataclass
class HoverSnapState:
    candidate: dict | None = None
    generation: int = 0
    clear_reason: str = ""

    def update(self, candidates, radius_px, hard_max_px):
        self.candidate = select_snap_candidate(
            candidates,
            radius_px,
            hard_max_px,
        )
        self.generation += 1
        self.clear_reason = ""
        return self.candidate

    def clear(self, reason=""):
        self.candidate = None
        self.generation += 1
        self.clear_reason = str(reason)

    def snapshot(self):
        candidate = self.candidate
        return {
            "kind": (
                str(candidate.get("kind", ""))
                if candidate is not None
                else ""
            ),
            "distance_px": (
                float(candidate.get("distance", 0.0))
                if candidate is not None
                else 0.0
            ),
            "node_id": (
                int(candidate.get("node_id", 0))
                if candidate is not None
                else 0
            ),
            "guide_id": (
                int(candidate.get("guide_id", 0))
                if candidate is not None
                else 0
            ),
            "segment_index": (
                int(candidate.get("segment_index", -1))
                if candidate is not None
                else -1
            ),
            "generation": int(self.generation),
            "clear_reason": self.clear_reason,
        }
