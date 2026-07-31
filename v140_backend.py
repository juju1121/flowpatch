"""Pure geometry and selection helpers for FlowPatch 1.4.

This module intentionally has no Blender dependency. Keeping the graph and
topology rules independent makes them deterministic, testable, and safe to use
from both foreground modal tools and background validation.
"""

from __future__ import annotations

from collections import deque
from math import sqrt


def _point_tuple(point):
    return tuple(float(value) for value in point)


def _distance_squared(a, b):
    return sum((left - right) ** 2 for left, right in zip(a, b))


def dedupe_closed_ring(points, epsilon=1.0e-6):
    """Return an open representation of a closed ring without tiny duplicates."""
    epsilon_squared = float(epsilon) ** 2
    cleaned = []
    for point in points:
        current = _point_tuple(point)
        if not cleaned or _distance_squared(current, cleaned[-1]) > epsilon_squared:
            cleaned.append(current)

    if len(cleaned) > 1 and _distance_squared(cleaned[0], cleaned[-1]) <= epsilon_squared:
        cleaned.pop()

    if len(cleaned) < 3:
        raise ValueError("A patch contour needs at least three unique points")
    return cleaned


def radial_quad_fan(points, epsilon=1.0e-6):
    """Decompose any simple closed contour into an all-quad radial layout.

    Vertex order is:
      0..n-1       contour corners
      n..2n-1      edge midpoints
      2n           center

    Each contour corner owns one quad. This is a robust baseline decomposition
    for triangles, circles, stars, and general n-gons. More sophisticated field
    layouts can replace it later without changing the preview/commit contract.
    """
    ring = dedupe_closed_ring(points, epsilon=epsilon)
    count = len(ring)
    dimensions = len(ring[0])
    if dimensions < 2:
        raise ValueError("Contour points need at least two dimensions")
    if any(len(point) != dimensions for point in ring):
        raise ValueError("All contour points must have the same dimensions")

    midpoints = []
    for index, point in enumerate(ring):
        next_point = ring[(index + 1) % count]
        midpoints.append(
            tuple((point[axis] + next_point[axis]) * 0.5 for axis in range(dimensions))
        )

    center = tuple(
        sum(point[axis] for point in ring) / count for axis in range(dimensions)
    )
    vertices = tuple(ring + midpoints + [center])
    center_index = count * 2
    quads = tuple(
        (
            index,
            count + index,
            center_index,
            count + ((index - 1) % count),
        )
        for index in range(count)
    )
    return vertices, quads


def _project_ring_2d(points):
    dimensions = len(points[0])
    if dimensions == 2:
        return tuple((point[0], point[1]) for point in points)
    if dimensions < 2:
        raise ValueError("Contour points need at least two dimensions")

    normal = [0.0, 0.0, 0.0]
    for point, next_point in zip(points, points[1:] + points[:1]):
        normal[0] += (point[1] - next_point[1]) * (point[2] + next_point[2])
        normal[1] += (point[2] - next_point[2]) * (point[0] + next_point[0])
        normal[2] += (point[0] - next_point[0]) * (point[1] + next_point[1])
    drop_axis = max(range(3), key=lambda axis: abs(normal[axis]))
    keep_axes = tuple(axis for axis in range(3) if axis != drop_axis)
    return tuple(
        (point[keep_axes[0]], point[keep_axes[1]]) for point in points
    )


def _signed_area_2d(points):
    return 0.5 * sum(
        point[0] * next_point[1] - next_point[0] * point[1]
        for point, next_point in zip(points, points[1:] + points[:1])
    )


def is_convex_ring(points, epsilon=1.0e-6):
    """Return whether a simple planar contour has one consistent turn."""
    ring = dedupe_closed_ring(points, epsilon=epsilon)
    projected = _project_ring_2d(ring)
    turn_sign = 0
    for index in range(len(projected)):
        before = projected[index - 1]
        point = projected[index]
        after = projected[(index + 1) % len(projected)]
        cross = (
            (point[0] - before[0]) * (after[1] - point[1])
            - (point[1] - before[1]) * (after[0] - point[0])
        )
        if abs(cross) <= epsilon:
            continue
        current_sign = 1 if cross > 0.0 else -1
        if turn_sign and current_sign != turn_sign:
            return False
        turn_sign = current_sign
    return bool(turn_sign)


def triangulated_quad_patch(points, triangles, epsilon=1.0e-6):
    """Split a polygon triangulation into welded quads.

    The full boundary ring is stored first in corner/midpoint order. Every
    triangle then contributes one centroid and three quads. Midpoints on
    shared triangulation edges are reused, so adjacent triangle patches remain
    welded without placing a radial center outside a concave contour.
    """
    ring = dedupe_closed_ring(points, epsilon=epsilon)
    dimensions = len(ring[0])
    if any(len(point) != dimensions for point in ring):
        raise ValueError("All contour points must have the same dimensions")
    projected = _project_ring_2d(ring)
    winding = _signed_area_2d(projected)
    if abs(winding) <= epsilon:
        raise ValueError("The contour has no stable planar winding")

    boundary_count = len(ring)
    vertices = []
    midpoint_indices = {}
    for index, point in enumerate(ring):
        next_index = (index + 1) % boundary_count
        next_point = ring[next_index]
        vertices.append(point)
        midpoint_indices[tuple(sorted((index, next_index)))] = len(vertices)
        vertices.append(
            tuple(
                (point[axis] + next_point[axis]) * 0.5
                for axis in range(dimensions)
            )
        )

    def ring_index(value):
        point = _point_tuple(value)
        distances = tuple(_distance_squared(point, candidate) for candidate in ring)
        index = min(range(boundary_count), key=distances.__getitem__)
        if distances[index] > float(epsilon) ** 2:
            raise ValueError(
                "A triangulation vertex does not belong to the contour ring"
            )
        return index

    def midpoint_index(left, right):
        key = tuple(sorted((left, right)))
        existing = midpoint_indices.get(key)
        if existing is not None:
            return existing
        left_point = ring[left]
        right_point = ring[right]
        index = len(vertices)
        vertices.append(
            tuple(
                (left_point[axis] + right_point[axis]) * 0.5
                for axis in range(dimensions)
            )
        )
        midpoint_indices[key] = index
        return index

    quads = []
    for triangle in triangles:
        if len(triangle) != 3:
            raise ValueError("Polygon triangulation entries must be triangles")
        indices = [ring_index(point) for point in triangle]
        if len(set(indices)) != 3:
            raise ValueError("Polygon triangulation contains a collapsed triangle")
        a, b, c = indices
        pa, pb, pc = projected[a], projected[b], projected[c]
        triangle_winding = (
            (pb[0] - pa[0]) * (pc[1] - pa[1])
            - (pb[1] - pa[1]) * (pc[0] - pa[0])
        )
        if abs(triangle_winding) <= epsilon:
            raise ValueError("Polygon triangulation contains a flat triangle")
        if triangle_winding * winding < 0.0:
            b, c = c, b

        midpoint_ab = midpoint_index(a, b)
        midpoint_bc = midpoint_index(b, c)
        midpoint_ca = midpoint_index(c, a)
        centroid_index = len(vertices)
        vertices.append(
            tuple(
                (ring[a][axis] + ring[b][axis] + ring[c][axis]) / 3.0
                for axis in range(dimensions)
            )
        )
        quads.extend(
            (
                (a * 2, midpoint_ab, centroid_index, midpoint_ca),
                (b * 2, midpoint_bc, centroid_index, midpoint_ab),
                (c * 2, midpoint_ca, centroid_index, midpoint_bc),
            )
        )

    if not quads:
        raise ValueError("Polygon triangulation did not produce any quads")
    return tuple(vertices), tuple(quads)


def _segments_touch_or_cross_2d(a, b, c, d, epsilon):
    def orientation(first, second, third):
        return (
            (second[0] - first[0]) * (third[1] - first[1])
            - (second[1] - first[1]) * (third[0] - first[0])
        )

    def on_segment(point, start, end):
        if abs(orientation(start, end, point)) > epsilon:
            return False
        return (
            min(start[0], end[0]) - epsilon
            <= point[0]
            <= max(start[0], end[0]) + epsilon
            and min(start[1], end[1]) - epsilon
            <= point[1]
            <= max(start[1], end[1]) + epsilon
        )

    values = (
        orientation(a, b, c),
        orientation(a, b, d),
        orientation(c, d, a),
        orientation(c, d, b),
    )
    if (
        (values[0] > epsilon and values[1] < -epsilon)
        or (values[0] < -epsilon and values[1] > epsilon)
    ) and (
        (values[2] > epsilon and values[3] < -epsilon)
        or (values[2] < -epsilon and values[3] > epsilon)
    ):
        return True
    return (
        on_segment(a, c, d)
        or on_segment(b, c, d)
        or on_segment(c, a, b)
        or on_segment(d, a, b)
    )


def _ring_self_intersects_2d(points, epsilon):
    count = len(points)
    for first in range(count):
        a = points[first]
        b = points[(first + 1) % count]
        for second in range(first + 1, count):
            if second in {
                first,
                (first + 1) % count,
                (first - 1) % count,
            }:
                continue
            if first == 0 and second == count - 1:
                continue
            c = points[second]
            d = points[(second + 1) % count]
            if _segments_touch_or_cross_2d(a, b, c, d, epsilon):
                return True
    return False


def _point_in_ring_2d(point, ring, epsilon):
    inside = False
    for start, end in zip(ring, ring[1:] + ring[:1]):
        if _segments_touch_or_cross_2d(
            point,
            point,
            start,
            end,
            epsilon,
        ):
            return False
        if (start[1] > point[1]) == (end[1] > point[1]):
            continue
        crossing_x = start[0] + (
            (point[1] - start[1])
            * (end[0] - start[0])
            / (end[1] - start[1])
        )
        if crossing_x > point[0] + epsilon:
            inside = not inside
    return inside


def _matched_ring_projection_2d(outer, inner, epsilon):
    dimensions = len(outer[0])
    if dimensions == 2:
        return (
            [(point[0], point[1]) for point in outer],
            [(point[0], point[1]) for point in inner],
        )
    if dimensions != 3:
        raise ValueError("Matched annular contours must be 2D or 3D")

    normal = [0.0, 0.0, 0.0]
    for point, following in zip(outer, outer[1:] + outer[:1]):
        normal[0] += (point[1] - following[1]) * (point[2] + following[2])
        normal[1] += (point[2] - following[2]) * (point[0] + following[0])
        normal[2] += (point[0] - following[0]) * (point[1] + following[1])
    normal_length = sqrt(sum(value * value for value in normal))
    if normal_length <= epsilon:
        raise ValueError("The outer annular contour has no stable plane")
    unit_normal = tuple(value / normal_length for value in normal)
    scale = max(
        sqrt(_distance_squared(outer[0], point))
        for point in outer + inner
    )
    plane_tolerance = float(epsilon) * max(1.0, scale) * 10.0
    if any(
        abs(
            sum(
                (point[axis] - outer[0][axis]) * unit_normal[axis]
                for axis in range(3)
            )
        )
        > plane_tolerance
        for point in outer + inner
    ):
        raise ValueError("Matched annular contours must be coplanar")

    drop_axis = max(range(3), key=lambda axis: abs(normal[axis]))
    keep_axes = tuple(axis for axis in range(3) if axis != drop_axis)
    return (
        [
            (point[keep_axes[0]], point[keep_axes[1]])
            for point in outer
        ],
        [
            (point[keep_axes[0]], point[keep_axes[1]])
            for point in inner
        ],
    )


def matched_annular_quad_patch(outer_points, inner_points, epsilon=1.0e-6):
    """Bridge matched outer and inner contours with one all-quad annulus."""
    outer = dedupe_closed_ring(outer_points, epsilon=epsilon)
    inner = dedupe_closed_ring(inner_points, epsilon=epsilon)
    if len(outer) != len(inner):
        raise ValueError("Annular contours need matching boundary counts")
    if len(outer) < 4:
        raise ValueError("An annular patch needs at least four matched points")
    dimensions = len(outer[0])
    if any(len(point) != dimensions for point in outer + inner):
        raise ValueError("All annular contour points need matching dimensions")

    outer_2d, inner_2d = _matched_ring_projection_2d(
        outer,
        inner,
        epsilon,
    )
    outer_area = _signed_area_2d(outer_2d)
    inner_area = _signed_area_2d(inner_2d)
    if abs(outer_area) <= epsilon or abs(inner_area) <= epsilon:
        raise ValueError("Annular contours need stable planar winding")
    if (
        _ring_self_intersects_2d(outer_2d, epsilon)
        or _ring_self_intersects_2d(inner_2d, epsilon)
    ):
        raise ValueError("Annular contours cannot cross themselves")
    for outer_start, outer_end in zip(
        outer_2d,
        outer_2d[1:] + outer_2d[:1],
    ):
        for inner_start, inner_end in zip(
            inner_2d,
            inner_2d[1:] + inner_2d[:1],
        ):
            if _segments_touch_or_cross_2d(
                outer_start,
                outer_end,
                inner_start,
                inner_end,
                epsilon,
            ):
                raise ValueError("Annular contour boundaries cannot touch")
    if not all(
        _point_in_ring_2d(point, outer_2d, epsilon)
        for point in inner_2d
    ):
        raise ValueError("The inner annular contour must stay inside the outer")

    if outer_area * inner_area < 0.0:
        inner.reverse()
        inner_2d.reverse()
    count = len(outer)
    rotation = min(
        range(count),
        key=lambda offset: sum(
            _distance_squared(
                outer_2d[index],
                inner_2d[(index + offset) % count],
            )
            for index in range(count)
        ),
    )
    inner = inner[rotation:] + inner[:rotation]
    inner_2d = inner_2d[rotation:] + inner_2d[:rotation]

    vertices = tuple(outer + inner)
    quads = tuple(
        (
            index,
            (index + 1) % count,
            count + ((index + 1) % count),
            count + index,
        )
        for index in range(count)
    )
    for quad in quads:
        projected = [
            (outer_2d + inner_2d)[index]
            for index in quad
        ]
        if _signed_area_2d(projected) * outer_area <= epsilon:
            raise ValueError("Matched annular contours produce an inverted quad")
    return vertices, quads


def adjacency_from_edges(edges):
    adjacency = {}
    for left, right in edges:
        adjacency.setdefault(left, set()).add(right)
        adjacency.setdefault(right, set()).add(left)
    return adjacency


def shortest_path(adjacency, start, end):
    """Return the shortest connected path, or an empty tuple if disconnected."""
    if start == end:
        return (start,)
    if start not in adjacency or end not in adjacency:
        return ()

    parents = {start: None}
    queue = deque([start])
    while queue:
        node = queue.popleft()
        for neighbor in sorted(adjacency.get(node, ())):
            if neighbor in parents:
                continue
            parents[neighbor] = node
            if neighbor == end:
                path = [end]
                while parents[path[-1]] is not None:
                    path.append(parents[path[-1]])
                path.reverse()
                return tuple(path)
            queue.append(neighbor)
    return ()


def update_selection(
    current,
    hit,
    *,
    shift=False,
    ctrl=False,
    anchor=None,
    adjacency=None,
):
    """Apply Blender-like replace, toggle, path, and additive-path selection."""
    selected = set(current)
    path = ()
    if ctrl and anchor is not None and adjacency is not None:
        path = shortest_path(adjacency, anchor, hit)

    targets = set(path or (hit,))
    if ctrl and not shift:
        selected = targets
    elif ctrl and shift:
        selected.update(targets)
    elif shift:
        if hit in selected:
            selected.remove(hit)
        else:
            selected.add(hit)
    else:
        selected = {hit}

    next_anchor = hit
    return frozenset(selected), next_anchor


def blend_weights(selected, adjacency, rings=2):
    """Return pinned/blended falloff weights around a selected graph region."""
    selected = set(selected)
    if not selected:
        return {}

    max_rings = max(0, int(rings))
    distances = {node: 0 for node in selected}
    queue = deque(selected)
    while queue:
        node = queue.popleft()
        distance = distances[node]
        if distance >= max_rings:
            continue
        for neighbor in adjacency.get(node, ()):
            if neighbor in distances:
                continue
            distances[neighbor] = distance + 1
            queue.append(neighbor)

    denominator = float(max_rings + 1)
    return {
        node: max(0.0, 1.0 - (distance / denominator))
        for node, distance in distances.items()
    }


def connected_components(adjacency, nodes=None):
    remaining = set(nodes if nodes is not None else adjacency)
    components = []
    while remaining:
        seed = min(remaining)
        component = set()
        queue = deque([seed])
        remaining.remove(seed)
        while queue:
            node = queue.popleft()
            component.add(node)
            for neighbor in adjacency.get(node, ()):
                if neighbor in remaining:
                    remaining.remove(neighbor)
                    queue.append(neighbor)
        components.append(frozenset(component))
    return tuple(components)


def mirror_local_x(points):
    """Mirror local-space points over the object's local X=0 plane."""
    mirrored = []
    for point in points:
        values = list(_point_tuple(point))
        if not values:
            raise ValueError("Cannot mirror an empty point")
        values[0] = -values[0]
        mirrored.append(tuple(values))
    return tuple(mirrored)


def polyline_length(points):
    total = 0.0
    for left, right in zip(points, points[1:]):
        total += sqrt(_distance_squared(left, right))
    return total
