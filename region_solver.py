from dataclasses import dataclass
from itertools import combinations
from math import isfinite
from math import sqrt


class RegionSolverError(ValueError):
    def __init__(self, reason_code, message):
        super().__init__(str(message))
        self.reason_code = str(reason_code)


@dataclass(frozen=True)
class WindingDecision:
    winding: str
    reversed: bool
    signed_area: float
    point_count: int

    def as_dict(self):
        return {
            "winding": self.winding,
            "reversed": bool(self.reversed),
            "signed_area": float(self.signed_area),
            "point_count": int(self.point_count),
        }


def _point2(value):
    try:
        point = (float(value[0]), float(value[1]))
    except (IndexError, TypeError, ValueError):
        return None
    return point if all(isfinite(component) for component in point) else None


def _distance2(left, right):
    return sqrt(
        (left[0] - right[0]) ** 2 + (left[1] - right[1]) ** 2
    )


def _point_segment_distance2(point, start, end):
    direction = (end[0] - start[0], end[1] - start[1])
    length_squared = direction[0] ** 2 + direction[1] ** 2
    if length_squared <= 1.0e-12:
        return _distance2(point, start)
    factor = max(
        0.0,
        min(
            1.0,
            (
                (point[0] - start[0]) * direction[0]
                + (point[1] - start[1]) * direction[1]
            )
            / length_squared,
        ),
    )
    closest = (
        start[0] + direction[0] * factor,
        start[1] + direction[1] * factor,
    )
    return _distance2(point, closest)


def infer_closed_quad_corner_indices(
    points,
    close_tolerance=18.0,
    minimum_turn_score=0.18,
    maximum_side_deviation=0.14,
):
    """Return four stable source indices for an obvious closed quadrilateral."""
    cleaned = []
    source_indices = []
    for source_index, value in enumerate(tuple(points or ())):
        point = _point2(value)
        if point is None:
            return ()
        if cleaned and _distance2(point, cleaned[-1]) <= 1.0e-6:
            continue
        cleaned.append(point)
        source_indices.append(source_index)
    if len(cleaned) < 5:
        return ()
    if _distance2(cleaned[0], cleaned[-1]) > max(
        1.0e-6,
        float(close_tolerance),
    ):
        return ()
    cleaned.pop()
    source_indices.pop()
    count = len(cleaned)
    if count < 4:
        return ()
    if count == 4:
        return tuple(source_indices)

    lengths = [
        _distance2(cleaned[index], cleaned[(index + 1) % count])
        for index in range(count)
    ]
    perimeter = sum(lengths)
    if perimeter <= 1.0e-6:
        return ()
    scores = []
    for index in range(count):
        previous = cleaned[(index - 1) % count]
        current = cleaned[index]
        following = cleaned[(index + 1) % count]
        incoming = (current[0] - previous[0], current[1] - previous[1])
        outgoing = (following[0] - current[0], following[1] - current[1])
        incoming_length = sqrt(incoming[0] ** 2 + incoming[1] ** 2)
        outgoing_length = sqrt(outgoing[0] ** 2 + outgoing[1] ** 2)
        if incoming_length <= 1.0e-8 or outgoing_length <= 1.0e-8:
            scores.append(0.0)
            continue
        cosine = max(
            -1.0,
            min(
                1.0,
                (
                    incoming[0] * outgoing[0]
                    + incoming[1] * outgoing[1]
                )
                / (incoming_length * outgoing_length),
            ),
        )
        scores.append(1.0 - cosine)

    candidates = [
        index
        for index, score in enumerate(scores)
        if score >= float(minimum_turn_score)
    ]
    candidates = sorted(
        candidates,
        key=lambda index: (-scores[index], index),
    )[:16]
    if len(candidates) < 4:
        return ()

    def forward_indices(start, end):
        values = [start]
        current = start
        while current != end:
            current = (current + 1) % count
            values.append(current)
        return values

    best = None
    for chosen in combinations(sorted(candidates), 4):
        arc_lengths = []
        max_ratio = 0.0
        valid = True
        for start, end in zip(chosen, chosen[1:] + chosen[:1]):
            indices = forward_indices(start, end)
            arc_length = sum(lengths[index] for index in indices[:-1])
            arc_lengths.append(arc_length)
            chord = _distance2(cleaned[start], cleaned[end])
            if chord <= perimeter * 0.04:
                valid = False
                break
            deviation = max(
                (
                    _point_segment_distance2(
                        cleaned[index],
                        cleaned[start],
                        cleaned[end],
                    )
                    for index in indices[1:-1]
                ),
                default=0.0,
            )
            max_ratio = max(max_ratio, deviation / chord)
        if not valid or min(arc_lengths) < perimeter * 0.08:
            continue
        if max_ratio > float(maximum_side_deviation):
            continue
        rank = (sum(scores[index] for index in chosen), -max_ratio)
        if best is None or rank > best[0]:
            best = (rank, chosen)
    if best is None:
        return ()
    return tuple(source_indices[index] for index in best[1])


def bounded_face_signature(edge_ids):
    try:
        identifiers = tuple(int(value) for value in edge_ids)
    except (TypeError, ValueError) as exc:
        raise RegionSolverError(
            "INVALID_EDGE_OWNERSHIP",
            "A bounded region contains an invalid guide-edge ID.",
        ) from exc
    if not identifiers or any(value <= 0 for value in identifiers):
        raise RegionSolverError(
            "INVALID_EDGE_OWNERSHIP",
            "A bounded region requires positive guide-edge IDs.",
        )
    if len(set(identifiers)) != len(identifiers):
        raise RegionSolverError(
            "DUPLICATE_BOUNDARY_EDGE",
            "A bounded region cannot traverse one guide edge twice.",
        )
    return ":".join(str(value) for value in sorted(identifiers))


def _point3(value, reason_code):
    try:
        point = tuple(float(value[index]) for index in range(3))
    except (IndexError, TypeError, ValueError) as exc:
        raise RegionSolverError(
            reason_code,
            "Target-local region data must contain finite 3D vectors.",
        ) from exc
    if not all(isfinite(component) for component in point):
        raise RegionSolverError(
            reason_code,
            "Target-local region data must contain finite 3D vectors.",
        )
    return point


def _add(left, right):
    return tuple(left[index] + right[index] for index in range(3))


def _subtract(left, right):
    return tuple(left[index] - right[index] for index in range(3))


def _scale(value, factor):
    return tuple(component * factor for component in value)


def _dot(left, right):
    return sum(left[index] * right[index] for index in range(3))


def _cross(left, right):
    return (
        left[1] * right[2] - left[2] * right[1],
        left[2] * right[0] - left[0] * right[2],
        left[0] * right[1] - left[1] * right[0],
    )


def _length_squared(value):
    return _dot(value, value)


def _normalized(value, reason_code):
    length_squared = _length_squared(value)
    if length_squared <= 1.0e-20:
        raise RegionSolverError(
            reason_code,
            "The target surface did not provide a stable local normal.",
        )
    return _scale(value, 1.0 / sqrt(length_squared))


def _clean_ring(points, epsilon):
    cleaned = []
    threshold_squared = float(epsilon) ** 2
    for value in points:
        point = _point3(value, "INVALID_REGION_POINT")
        if (
            cleaned
            and _length_squared(_subtract(point, cleaned[-1]))
            <= threshold_squared
        ):
            continue
        cleaned.append(point)
    if (
        len(cleaned) > 1
        and _length_squared(_subtract(cleaned[0], cleaned[-1]))
        <= threshold_squared
    ):
        cleaned.pop()
    if len(cleaned) < 3:
        raise RegionSolverError(
            "INCOMPLETE_BOUNDED_REGION",
            "A bounded region needs at least three distinct target-local points.",
        )
    return tuple(cleaned)


def _area_vector(points, center):
    area = (0.0, 0.0, 0.0)
    for current, following in zip(points, points[1:] + points[:1]):
        area = _add(
            area,
            _cross(
                _subtract(current, center),
                _subtract(following, center),
            ),
        )
    return area


def _canonical_normal(area_vector):
    normal = _normalized(area_vector, "DEGENERATE_REGION_WINDING")
    dominant = max(range(3), key=lambda index: abs(normal[index]))
    if normal[dominant] < 0.0:
        normal = _scale(normal, -1.0)
    return normal


def normalize_target_local_winding(
    points,
    normal_at=None,
    epsilon=1.0e-9,
):
    """Return a stable counterclockwise winding decision without mutating input."""
    ring = _clean_ring(points, epsilon)
    center = _scale(
        tuple(sum(point[index] for point in ring) for index in range(3)),
        1.0 / len(ring),
    )
    area_vector = _area_vector(ring, center)
    if _length_squared(area_vector) <= float(epsilon) ** 2:
        raise RegionSolverError(
            "DEGENERATE_REGION_WINDING",
            "The bounded region has no stable target-local area.",
        )

    if normal_at is None:
        fallback_normal = _canonical_normal(area_vector)

        def sample_normal(_point):
            return fallback_normal

    else:
        sample_normal = normal_at

    signed_twice_area = 0.0
    for current, following in zip(ring, ring[1:] + ring[:1]):
        midpoint = _scale(_add(current, following), 0.5)
        try:
            sampled = sample_normal(midpoint)
        except RegionSolverError:
            raise
        except Exception as exc:
            raise RegionSolverError(
                "SURFACE_NORMAL_UNAVAILABLE",
                "The target surface normal could not be sampled for this region.",
            ) from exc
        if sampled is None:
            raise RegionSolverError(
                "SURFACE_NORMAL_UNAVAILABLE",
                "The target surface normal could not be sampled for this region.",
            )
        normal = _normalized(
            _point3(sampled, "SURFACE_NORMAL_UNAVAILABLE"),
            "SURFACE_NORMAL_UNAVAILABLE",
        )
        signed_twice_area += _dot(
            _cross(
                _subtract(current, center),
                _subtract(following, center),
            ),
            normal,
        )

    signed_area = signed_twice_area * 0.5
    if abs(signed_area) <= float(epsilon):
        raise RegionSolverError(
            "AMBIGUOUS_REGION_WINDING",
            "The bounded region cannot establish one target-local winding.",
        )
    return WindingDecision(
        winding="TARGET_LOCAL_CCW",
        reversed=signed_area < 0.0,
        signed_area=abs(signed_area),
        point_count=len(ring),
    )
