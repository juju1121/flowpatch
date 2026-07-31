from dataclasses import dataclass
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
