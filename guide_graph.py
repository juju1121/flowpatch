from collections import deque
from dataclasses import dataclass
from dataclasses import field

from mathutils import Vector


MAX_CYCLE_EDGES = 24
MAX_INTERSECTION_PASSES = 256
MAX_INTERSECTION_SEGMENT_TESTS = 250_000
MAX_CYCLE_COUNT = 128
MAX_CYCLE_VISITS = 100_000


class GuideGraphBudgetError(RuntimeError):
    """Raised when a guide operation would exceed a safe interactive budget."""


class GuideDeleteError(RuntimeError):
    """Raised when a guide deletion target is unsafe or ambiguous."""

    def __init__(self, reason_code, message):
        super().__init__(str(message))
        self.reason_code = str(reason_code)


@dataclass(frozen=True)
class GuideDeleteResult:
    target_kind: str
    changed_guide_ids: tuple = ()
    removed_guide_ids: tuple = ()
    removed_node_ids: tuple = ()
    message: str = ""

    @property
    def affected_guide_ids(self):
        return tuple(
            sorted(
                {
                    int(value)
                    for value in self.changed_guide_ids + self.removed_guide_ids
                }
            )
        )


def _segments_aabb_overlap(a0, a1, b0, b1, padding):
    for axis in range(3):
        if max(a0[axis], a1[axis]) + padding < min(b0[axis], b1[axis]):
            return False
        if max(b0[axis], b1[axis]) + padding < min(a0[axis], a1[axis]):
            return False
    return True


@dataclass
class SurfaceAnchor:
    target_object_uuid: str = ""
    face_index: int = -1
    triangle_index: int = -1
    barycentric: tuple = ()
    local_position: Vector = field(default_factory=Vector)
    local_normal: Vector = field(
        default_factory=lambda: Vector((0.0, 0.0, 1.0))
    )
    normal_offset: float = 0.0
    topology_revision: int = 0
    shell_component: int = -1

    def copy(self):
        return SurfaceAnchor(
            target_object_uuid=str(self.target_object_uuid),
            face_index=int(self.face_index),
            triangle_index=int(self.triangle_index),
            barycentric=tuple(float(value) for value in self.barycentric),
            local_position=self.local_position.copy(),
            local_normal=self.local_normal.copy(),
            normal_offset=float(self.normal_offset),
            topology_revision=int(self.topology_revision),
            shell_component=int(self.shell_component),
        )

    def interpolated(self, other, factor):
        factor = max(0.0, min(1.0, float(factor)))
        if str(self.target_object_uuid) != str(other.target_object_uuid):
            raise ValueError(
                "Cannot interpolate anchors owned by different targets."
            )
        normal = self.local_normal.lerp(other.local_normal, factor)
        if normal.length <= 1.0e-12:
            normal = self.local_normal.copy()
        if normal.length > 1.0e-12:
            normal.normalize()
        owner = self if factor < 0.5 else other
        return SurfaceAnchor(
            target_object_uuid=str(self.target_object_uuid),
            face_index=int(owner.face_index),
            triangle_index=int(owner.triangle_index),
            barycentric=(),
            local_position=self.local_position.lerp(
                other.local_position,
                factor,
            ),
            local_normal=normal,
            normal_offset=(
                float(self.normal_offset)
                + (float(other.normal_offset) - float(self.normal_offset))
                * factor
            ),
            topology_revision=max(
                int(self.topology_revision),
                int(other.topology_revision),
            ),
            shell_component=(
                int(self.shell_component)
                if int(self.shell_component) == int(other.shell_component)
                else -1
            ),
        )


@dataclass
class GuidePath:
    guide_id: int
    points_local: list
    start_node: int = 0
    end_node: int = 0
    logical_side_id: int = 0
    source_kind: str = "DRAWN"
    source_vertex_uids: tuple = ()
    anchors: tuple = ()

    def copy(self):
        return GuidePath(
            guide_id=int(self.guide_id),
            points_local=[point.copy() for point in self.points_local],
            start_node=int(self.start_node),
            end_node=int(self.end_node),
            logical_side_id=int(self.logical_side_id),
            source_kind=str(self.source_kind),
            source_vertex_uids=tuple(int(value) for value in self.source_vertex_uids),
            anchors=tuple(anchor.copy() for anchor in self.anchors),
        )


@dataclass(frozen=True)
class GuideNode:
    node_id: int
    point_local: Vector


@dataclass(frozen=True)
class BoundarySide:
    edge_ids: tuple
    start_node: int
    end_node: int
    logical_side_id: int

    @property
    def key(self):
        forward = tuple(int(value) for value in self.edge_ids)
        backward = tuple(reversed(forward))
        canonical = min(forward, backward)
        return ":".join(str(value) for value in canonical)

    @property
    def forward_key(self):
        forward = tuple(int(value) for value in self.edge_ids)
        backward = tuple(reversed(forward))
        if forward != backward:
            return forward < backward
        # A one-edge side has the same edge-id tuple in both directions. Use
        # graph node IDs as the stable tie-break so adjacent cells traverse the
        # shared boundary in opposite canonical directions.
        return int(self.start_node) <= int(self.end_node)


@dataclass(frozen=True)
class GuideCycle:
    key: str
    sides: tuple
    traversal_nodes: tuple
    winding: str = "UNNORMALIZED"
    target_local_area: float = 0.0

    @property
    def edge_ids(self):
        return tuple(
            edge_id
            for side in self.sides
            for edge_id in side.edge_ids
        )

    @property
    def node_ids(self):
        return tuple(side.start_node for side in self.sides)


def clone_guides(guides):
    return [guide.copy() for guide in guides]


def _next_identifier(values):
    return max((int(value) for value in values), default=0) + 1


def next_guide_id(guides):
    return _next_identifier(guide.guide_id for guide in guides)


def next_node_id(guides):
    return _next_identifier(
        node_id
        for guide in guides
        for node_id in (guide.start_node, guide.end_node)
        if node_id > 0
    )


def next_logical_side_id(guides):
    return _next_identifier(
        guide.logical_side_id
        for guide in guides
        if guide.logical_side_id > 0
    )


def replace_node_id(guides, old_node_id, new_node_id):
    old_node_id = int(old_node_id)
    new_node_id = int(new_node_id)
    if old_node_id == new_node_id:
        return
    for guide in guides:
        if guide.start_node == old_node_id:
            guide.start_node = new_node_id
        if guide.end_node == old_node_id:
            guide.end_node = new_node_id


def ensure_graph_ids(guides, tolerance=1.0e-6):
    next_node = next_node_id(guides)
    known = {}

    def assign(current_id, point):
        nonlocal next_node
        if current_id > 0:
            known.setdefault(int(current_id), point.copy())
            return int(current_id)
        for node_id, existing in known.items():
            if (existing - point).length <= tolerance:
                return node_id
        node_id = next_node
        next_node += 1
        known[node_id] = point.copy()
        return node_id

    for guide in sorted(guides, key=lambda item: item.guide_id):
        if len(guide.points_local) < 2:
            continue
        guide.start_node = assign(guide.start_node, guide.points_local[0])
        guide.end_node = assign(guide.end_node, guide.points_local[-1])
        if guide.logical_side_id <= 0:
            guide.logical_side_id = int(guide.guide_id)

    node_items = sorted(known.items())
    for index, (node_id, point) in enumerate(node_items):
        for other_id, other_point in node_items[index + 1 :]:
            if (other_point - point).length <= tolerance:
                replace_node_id(guides, other_id, node_id)


def graph_nodes(guides):
    nodes = {}
    for guide in guides:
        if len(guide.points_local) < 2:
            continue
        nodes.setdefault(guide.start_node, guide.points_local[0].copy())
        nodes.setdefault(guide.end_node, guide.points_local[-1].copy())
    return {
        node_id: GuideNode(node_id=node_id, point_local=point)
        for node_id, point in nodes.items()
    }


def connected_guide_indices(guides, seed_index):
    """Return every guide connected to the seed through shared graph nodes."""
    seed_index = int(seed_index)
    if not (0 <= seed_index < len(guides)):
        return ()

    node_to_guides = {}
    for guide_index, guide in enumerate(guides):
        for node_id in (int(guide.start_node), int(guide.end_node)):
            if node_id > 0:
                node_to_guides.setdefault(node_id, set()).add(guide_index)

    visited = {seed_index}
    pending = [seed_index]
    while pending:
        guide_index = pending.pop()
        guide = guides[guide_index]
        for node_id in (int(guide.start_node), int(guide.end_node)):
            if node_id <= 0:
                continue
            for neighbor_index in node_to_guides.get(node_id, ()):
                if neighbor_index in visited:
                    continue
                visited.add(neighbor_index)
                pending.append(neighbor_index)
    return tuple(sorted(visited))


def _guide_index_for_id(guides, guide_id):
    guide_id = int(guide_id)
    return next(
        (
            index
            for index, guide in enumerate(guides)
            if int(guide.guide_id) == guide_id
        ),
        None,
    )


def _node_incidence(guides, node_id):
    node_id = int(node_id)
    incidence = []
    for guide_index, guide in enumerate(guides):
        if int(guide.start_node) == node_id:
            incidence.append((guide_index, "START"))
        if int(guide.end_node) == node_id:
            incidence.append((guide_index, "END"))
    return tuple(incidence)


def _without_index(values, index):
    return tuple(value for value_index, value in enumerate(values) if value_index != index)


def delete_guide_control(guides, guide_id, point_index):
    guide_index = _guide_index_for_id(guides, guide_id)
    if guide_index is None:
        raise GuideDeleteError(
            "GUIDE_NOT_FOUND",
            "The selected guide no longer exists.",
        )
    guide = guides[guide_index]
    point_index = int(point_index)
    point_count = len(guide.points_local)
    if not (0 <= point_index < point_count):
        raise GuideDeleteError(
            "CONTROL_NOT_FOUND",
            "The selected guide control no longer exists.",
        )
    if point_index in {0, point_count - 1}:
        raise GuideDeleteError(
            "CONTROL_IS_NODE",
            "That control is a topological GuideNode, not an interior sample.",
        )

    guide.points_local = [
        point.copy()
        for index, point in enumerate(guide.points_local)
        if index != point_index
    ]
    if len(guide.anchors) == point_count:
        guide.anchors = tuple(
            anchor.copy()
            for index, anchor in enumerate(guide.anchors)
            if index != point_index
        )
    else:
        guide.anchors = ()
    if len(guide.source_vertex_uids) == point_count:
        guide.source_vertex_uids = _without_index(
            guide.source_vertex_uids,
            point_index,
        )
    elif guide.source_vertex_uids:
        guide.source_vertex_uids = ()
    return GuideDeleteResult(
        target_kind="CONTROL",
        changed_guide_ids=(int(guide.guide_id),),
        message="Guide control removed; endpoints were preserved.",
    )


def _oriented_toward_node(guide, endpoint):
    reverse = str(endpoint) == "START"
    points = [point.copy() for point in guide.points_local]
    anchors = tuple(anchor.copy() for anchor in guide.anchors)
    source_uids = tuple(int(value) for value in guide.source_vertex_uids)
    if reverse:
        points.reverse()
        anchors = tuple(reversed(anchors))
        source_uids = tuple(reversed(source_uids))
    outer_node = (
        int(guide.end_node)
        if str(endpoint) == "START"
        else int(guide.start_node)
    )
    return outer_node, points, anchors, source_uids


def delete_guide_node(guides, node_id):
    node_id = int(node_id)
    incidence = _node_incidence(guides, node_id)
    degree = len(incidence)
    if degree == 0:
        raise GuideDeleteError(
            "NODE_NOT_FOUND",
            "The selected GuideNode no longer exists.",
        )
    if degree > 2:
        raise GuideDeleteError(
            "JUNCTION_CONFIRM_REQUIRED",
            "A junction affects multiple branches; delete one edge or use a confirmed branch operation.",
        )

    if degree == 1:
        guide_index, _endpoint = incidence[0]
        removed_id = int(guides[guide_index].guide_id)
        del guides[guide_index]
        return GuideDeleteResult(
            target_kind="END_NODE",
            removed_guide_ids=(removed_id,),
            removed_node_ids=(node_id,),
            message="End node and its incident guide edge were removed.",
        )

    (left_index, left_endpoint), (right_index, right_endpoint) = incidence
    if left_index == right_index:
        raise GuideDeleteError(
            "CLOSED_LOOP_NODE_PROTECTED",
            "A closed-loop node cannot be collapsed as a degree-2 pass-through node.",
        )
    left_guide = guides[left_index]
    right_guide = guides[right_index]
    if int(left_guide.logical_side_id) != int(right_guide.logical_side_id):
        raise GuideDeleteError(
            "PROTECTED_CORNER",
            "This degree-2 node separates two logical sides and is protected as a patch corner.",
        )
    if str(left_guide.source_kind) != str(right_guide.source_kind):
        raise GuideDeleteError(
            "INCOMPATIBLE_EDGE_OWNERSHIP",
            "The two incident guide edges have incompatible ownership and cannot be merged safely.",
        )

    halves = []
    for guide, endpoint in (
        (left_guide, left_endpoint),
        (right_guide, right_endpoint),
    ):
        outer_node, points, anchors, source_uids = _oriented_toward_node(
            guide,
            endpoint,
        )
        halves.append(
            (
                outer_node,
                int(guide.guide_id),
                points,
                anchors,
                source_uids,
            )
        )
    halves.sort(key=lambda item: (item[0], item[1]))
    first, second = halves
    if first[0] == second[0]:
        raise GuideDeleteError(
            "PARALLEL_EDGE_NODE_PROTECTED",
            "Collapsing this node would create a closed or duplicate guide edge.",
        )

    first_points = first[2]
    second_points = list(reversed(second[2]))
    merged_points = first_points + second_points[1:]
    anchors_valid = (
        len(first[3]) == len(first_points)
        and len(second[3]) == len(second[2])
    )
    merged_anchors = ()
    if anchors_valid:
        merged_anchors = tuple(first[3]) + tuple(reversed(second[3]))[1:]
    source_uids_valid = (
        len(first[4]) == len(first_points)
        and len(second[4]) == len(second[2])
    )
    merged_source_uids = ()
    if source_uids_valid:
        merged_source_uids = tuple(first[4]) + tuple(reversed(second[4]))[1:]

    retained_id = min(int(left_guide.guide_id), int(right_guide.guide_id))
    removed_id = max(int(left_guide.guide_id), int(right_guide.guide_id))
    merged = GuidePath(
        guide_id=retained_id,
        points_local=merged_points,
        start_node=int(first[0]),
        end_node=int(second[0]),
        logical_side_id=int(left_guide.logical_side_id),
        source_kind=str(left_guide.source_kind),
        source_vertex_uids=tuple(merged_source_uids),
        anchors=tuple(merged_anchors),
    )
    insert_index = min(left_index, right_index)
    for guide_index in sorted((left_index, right_index), reverse=True):
        del guides[guide_index]
    guides.insert(insert_index, merged)
    return GuideDeleteResult(
        target_kind="DEGREE_2_NODE",
        changed_guide_ids=(retained_id,),
        removed_guide_ids=(removed_id,),
        removed_node_ids=(node_id,),
        message="Degree-2 node merged into one resampled logical guide side.",
    )


def delete_guide_point(guides, guide_index, point_index):
    guide_index = int(guide_index)
    point_index = int(point_index)
    if not (0 <= guide_index < len(guides)):
        raise GuideDeleteError(
            "GUIDE_NOT_FOUND",
            "The selected guide no longer exists.",
        )
    guide = guides[guide_index]
    if not (0 <= point_index < len(guide.points_local)):
        raise GuideDeleteError(
            "CONTROL_NOT_FOUND",
            "The selected guide control no longer exists.",
        )
    if point_index == 0:
        return delete_guide_node(guides, guide.start_node)
    if point_index == len(guide.points_local) - 1:
        return delete_guide_node(guides, guide.end_node)
    return delete_guide_control(guides, guide.guide_id, point_index)


def delete_guide_edge(guides, guide_id):
    guide_index = _guide_index_for_id(guides, guide_id)
    if guide_index is None:
        raise GuideDeleteError(
            "GUIDE_NOT_FOUND",
            "The selected GuideEdge no longer exists.",
        )
    removed_id = int(guides[guide_index].guide_id)
    del guides[guide_index]
    return GuideDeleteResult(
        target_kind="EDGE",
        removed_guide_ids=(removed_id,),
        message="Guide edge removed; dependent regions will be re-evaluated.",
    )


def split_guide(
    guides,
    guide_id,
    segment_index,
    factor,
    point_local,
    node_id,
):
    guide_index = next(
        (
            index
            for index, guide in enumerate(guides)
            if guide.guide_id == guide_id
        ),
        None,
    )
    if guide_index is None:
        return None
    guide = guides[guide_index]
    if not (0 <= segment_index < len(guide.points_local) - 1):
        return None

    epsilon = 1.0e-6
    if factor <= epsilon and segment_index == 0:
        replace_node_id(guides, guide.start_node, node_id)
        guide.points_local[0] = point_local.copy()
        return guide.guide_id
    if factor >= 1.0 - epsilon and segment_index == len(guide.points_local) - 2:
        replace_node_id(guides, guide.end_node, node_id)
        guide.points_local[-1] = point_local.copy()
        return guide.guide_id

    points = [point.copy() for point in guide.points_local]
    anchors = tuple(anchor.copy() for anchor in guide.anchors)
    anchors_valid = len(anchors) == len(points)
    if factor <= epsilon:
        split_index = segment_index
        points[split_index] = point_local.copy()
        anchors_valid = False
    elif factor >= 1.0 - epsilon:
        split_index = segment_index + 1
        points[split_index] = point_local.copy()
        anchors_valid = False
    else:
        split_index = segment_index + 1
        points.insert(split_index, point_local.copy())
        if anchors_valid:
            anchors = (
                anchors[:split_index]
                + (
                    anchors[segment_index].interpolated(
                        anchors[segment_index + 1],
                        factor,
                    ),
                )
                + anchors[split_index:]
            )

    if split_index <= 0:
        replace_node_id(guides, guide.start_node, node_id)
        guide.points_local = points
        guide.anchors = anchors if anchors_valid else ()
        return guide.guide_id
    if split_index >= len(points) - 1:
        replace_node_id(guides, guide.end_node, node_id)
        guide.points_local = points
        guide.anchors = anchors if anchors_valid else ()
        return guide.guide_id

    original_end = guide.end_node
    original_source_uids = tuple(guide.source_vertex_uids)
    guide.points_local = points[: split_index + 1]
    guide.anchors = (
        tuple(anchor.copy() for anchor in anchors[: split_index + 1])
        if anchors_valid
        else ()
    )
    guide.end_node = int(node_id)

    second_source_uids = ()
    if original_source_uids and len(original_source_uids) == len(points):
        guide.source_vertex_uids = original_source_uids[: split_index + 1]
        second_source_uids = original_source_uids[split_index:]
    elif original_source_uids:
        guide.source_vertex_uids = ()

    second = GuidePath(
        guide_id=next_guide_id(guides),
        points_local=points[split_index:],
        start_node=int(node_id),
        end_node=int(original_end),
        logical_side_id=int(guide.logical_side_id),
        source_kind=str(guide.source_kind),
        source_vertex_uids=tuple(second_source_uids),
        anchors=(
            tuple(anchor.copy() for anchor in anchors[split_index:])
            if anchors_valid
            else ()
        ),
    )
    guides.insert(guide_index + 1, second)
    return second.guide_id


def closest_points_on_segments(a0, a1, b0, b1):
    a0 = Vector(a0)
    a1 = Vector(a1)
    b0 = Vector(b0)
    b1 = Vector(b1)
    d1 = a1 - a0
    d2 = b1 - b0
    r = a0 - b0
    a = d1.dot(d1)
    e = d2.dot(d2)
    f = d2.dot(r)
    epsilon = 1.0e-12

    if a <= epsilon and e <= epsilon:
        return a0.copy(), b0.copy(), 0.0, 0.0
    if a <= epsilon:
        s = 0.0
        t = max(0.0, min(1.0, f / e))
    else:
        c = d1.dot(r)
        if e <= epsilon:
            t = 0.0
            s = max(0.0, min(1.0, -c / a))
        else:
            b = d1.dot(d2)
            denominator = a * e - b * b
            s = (
                max(0.0, min(1.0, (b * f - c * e) / denominator))
                if abs(denominator) > epsilon
                else 0.0
            )
            t = (b * s + f) / e
            if t < 0.0:
                t = 0.0
                s = max(0.0, min(1.0, -c / a))
            elif t > 1.0:
                t = 1.0
                s = max(0.0, min(1.0, (b - c) / a))
    return a0 + d1 * s, b0 + d2 * t, s, t


def _segment_endpoint(guide, segment_index, factor, epsilon):
    if segment_index == 0 and factor <= epsilon:
        return int(guide.start_node), guide.points_local[0].copy()
    if (
        segment_index == len(guide.points_local) - 2
        and factor >= 1.0 - epsilon
    ):
        return int(guide.end_node), guide.points_local[-1].copy()
    return 0, None


def _set_node_point(guides, node_id, point_local):
    for guide in guides:
        if int(guide.start_node) == int(node_id):
            guide.points_local[0] = point_local.copy()
        if int(guide.end_node) == int(node_id):
            guide.points_local[-1] = point_local.copy()


def _split_intersections_in_place(guides, matrix_world, tolerance):
    """Normalize real guide crossings without repeatedly splitting near misses."""
    tolerance = max(1.0e-7, float(tolerance))
    graph_tolerance = min(tolerance, 1.0e-5)
    ensure_graph_ids(guides, tolerance=graph_tolerance)
    inverse = matrix_world.inverted_safe()
    epsilon = 1.0e-5
    max_iterations = min(
        MAX_INTERSECTION_PASSES,
        max(32, len(guides) * 8),
    )
    segment_tests = 0

    for _iteration in range(max_iterations):
        changed = False
        snapshot = list(guides)
        nodes = graph_nodes(guides)
        world_points = {
            int(guide.guide_id): [matrix_world @ point for point in guide.points_local]
            for guide in snapshot
        }
        for left_index, left in enumerate(snapshot):
            for right in snapshot[left_index + 1 :]:
                if left.guide_id == right.guide_id:
                    continue
                shared_nodes = {
                    int(left.start_node),
                    int(left.end_node),
                }.intersection(
                    {
                        int(right.start_node),
                        int(right.end_node),
                    }
                )
                left_world = world_points[int(left.guide_id)]
                right_world = world_points[int(right.guide_id)]
                for left_segment, (left_a, left_b) in enumerate(
                    zip(left_world, left_world[1:])
                ):
                    for right_segment, (right_a, right_b) in enumerate(
                        zip(right_world, right_world[1:])
                    ):
                        segment_tests += 1
                        if segment_tests > MAX_INTERSECTION_SEGMENT_TESTS:
                            raise GuideGraphBudgetError(
                                "Guide intersections are too dense to normalize safely. "
                                "Build or simplify part of the network before adding more guides."
                            )
                        if not _segments_aabb_overlap(
                            left_a,
                            left_b,
                            right_a,
                            right_b,
                            tolerance,
                        ):
                            continue
                        point_a, point_b, factor_a, factor_b = (
                            closest_points_on_segments(
                                left_a,
                                left_b,
                                right_a,
                                right_b,
                            )
                        )
                        distance = (point_a - point_b).length
                        if distance > tolerance:
                            continue

                        contact_world = (point_a + point_b) * 0.5
                        if any(
                            node_id in nodes
                            and (
                                matrix_world @ nodes[node_id].point_local
                                - contact_world
                            ).length
                            <= tolerance * 1.5
                            for node_id in shared_nodes
                        ):
                            continue

                        left_node, left_point = _segment_endpoint(
                            left,
                            left_segment,
                            factor_a,
                            epsilon,
                        )
                        right_node, right_point = _segment_endpoint(
                            right,
                            right_segment,
                            factor_b,
                            epsilon,
                        )

                        if left_node and right_node:
                            if left_node == right_node:
                                continue
                            keep_node = min(left_node, right_node)
                            drop_node = max(left_node, right_node)
                            merged_local = (left_point + right_point) * 0.5
                            replace_node_id(guides, drop_node, keep_node)
                            _set_node_point(guides, keep_node, merged_local)
                            changed = True
                            break

                        if left_node:
                            split_guide(
                                guides,
                                right.guide_id,
                                right_segment,
                                factor_b,
                                left_point,
                                left_node,
                            )
                            _set_node_point(guides, left_node, left_point)
                            changed = True
                            break

                        if right_node:
                            split_guide(
                                guides,
                                left.guide_id,
                                left_segment,
                                factor_a,
                                right_point,
                                right_node,
                            )
                            _set_node_point(guides, right_node, right_point)
                            changed = True
                            break

                        node_id = next_node_id(guides)
                        point_local = inverse @ contact_world
                        split_guide(
                            guides,
                            left.guide_id,
                            left_segment,
                            factor_a,
                            point_local,
                            node_id,
                        )
                        split_guide(
                            guides,
                            right.guide_id,
                            right_segment,
                            factor_b,
                            point_local,
                            node_id,
                        )
                        _set_node_point(guides, node_id, point_local)
                        changed = True
                        break
                    if changed:
                        break
                if changed:
                    break
            if changed:
                break
        if not changed:
            ensure_graph_ids(guides, tolerance=graph_tolerance)
            return
    raise GuideGraphBudgetError(
        "Guide intersection normalization reached its safe pass limit. "
        "The previous valid guide network was kept."
    )


def split_intersections(guides, matrix_world, tolerance):
    """Split crossings atomically so failed normalization keeps prior ownership."""
    snapshot = clone_guides(guides)
    try:
        return _split_intersections_in_place(
            guides,
            matrix_world,
            tolerance,
        )
    except Exception:
        guides[:] = snapshot
        raise


def _edge_nodes(guide):
    return int(guide.start_node), int(guide.end_node)


def _ordered_cycle_groups(
    edge_ids,
    node_ids,
    guide_by_id,
    required_sides=None,
):
    side_ids = [
        int(guide_by_id[edge_id].logical_side_id)
        for edge_id in edge_ids
    ]
    if not side_ids:
        return None

    start_index = next(
        (
            index
            for index, side_id in enumerate(side_ids)
            if side_ids[index - 1] != side_id
        ),
        0,
    )

    edge_ids = edge_ids[start_index:] + edge_ids[:start_index]
    node_ids = node_ids[start_index:] + node_ids[:start_index]
    side_ids = side_ids[start_index:] + side_ids[:start_index]
    groups = []
    for edge_id, node_id, side_id in zip(edge_ids, node_ids, side_ids):
        if not groups or groups[-1]["side_id"] != side_id:
            groups.append(
                {
                    "side_id": side_id,
                    "edge_ids": [edge_id],
                    "start_node": node_id,
                }
            )
        else:
            groups[-1]["edge_ids"].append(edge_id)
    if required_sides is not None and len(groups) != int(required_sides):
        return None

    sides = []
    for index, group in enumerate(groups):
        sides.append(
            BoundarySide(
                edge_ids=tuple(group["edge_ids"]),
                start_node=int(group["start_node"]),
                end_node=int(
                    groups[(index + 1) % len(groups)]["start_node"]
                ),
                logical_side_id=int(group["side_id"]),
            )
        )
    return tuple(sides), tuple(node_ids)


def _cycle_has_chord(edge_ids, node_ids, adjacency):
    cycle_edges = set(edge_ids)
    node_set = set(node_ids)
    consecutive = {
        frozenset((node_ids[index], node_ids[(index + 1) % len(node_ids)]))
        for index in range(len(node_ids))
    }
    for node_id in node_ids:
        for edge_id, neighbor in adjacency.get(node_id, []):
            if edge_id in cycle_edges or neighbor not in node_set:
                continue
            if frozenset((node_id, neighbor)) not in consecutive:
                return True
    return False


def find_closed_cycles(
    guides,
    max_edges=MAX_CYCLE_EDGES,
    max_cycles=MAX_CYCLE_COUNT,
    max_visits=MAX_CYCLE_VISITS,
):
    ensure_graph_ids(guides)
    guide_by_id = {
        guide.guide_id: guide
        for guide in guides
        if len(guide.points_local) >= 2
    }
    self_loops = {
        guide_id: guide
        for guide_id, guide in guide_by_id.items()
        if guide.start_node == guide.end_node
    }
    traversable_guides = {
        guide_id: guide
        for guide_id, guide in guide_by_id.items()
        if guide.start_node != guide.end_node
    }
    edge_nodes = {
        guide_id: _edge_nodes(guide)
        for guide_id, guide in traversable_guides.items()
    }
    adjacency = {}
    for edge_id, (start, end) in edge_nodes.items():
        adjacency.setdefault(start, []).append((edge_id, end))
        adjacency.setdefault(end, []).append((edge_id, start))

    cycles = {}
    for edge_id, guide in self_loops.items():
        node_id = int(guide.start_node)
        side = BoundarySide(
            edge_ids=(int(edge_id),),
            start_node=node_id,
            end_node=node_id,
            logical_side_id=int(guide.logical_side_id),
        )
        key = f"closed:{int(edge_id)}"
        cycles[key] = GuideCycle(
            key=key,
            sides=(side,),
            traversal_nodes=(node_id,),
        )
        if len(cycles) > max_cycles:
            raise GuideGraphBudgetError(
                "The guide network contains too many closed cells to preview safely."
            )

    visit_count = 0

    def register_cycle(edge_ids, node_ids):
        if len(edge_ids) < 3:
            return
        key = ":".join(str(value) for value in sorted(edge_ids))
        if key in cycles:
            return
        if _cycle_has_chord(edge_ids, node_ids, adjacency):
            return
        grouped = _ordered_cycle_groups(
            list(edge_ids),
            list(node_ids),
            traversable_guides,
        )
        if grouped is None:
            return
        sides, traversal_nodes = grouped
        cycles[key] = GuideCycle(
            key=key,
            sides=sides,
            traversal_nodes=traversal_nodes,
        )
        if len(cycles) > max_cycles:
            raise GuideGraphBudgetError(
                "The guide network contains too many closed cells to preview safely."
            )

    # Find local cells by removing one edge and searching for the shortest
    # alternative path between its endpoints. This avoids enumerating every
    # simple cycle in a guide grid, which grows exponentially.
    max_paths_per_edge = 32
    for excluded_edge in sorted(traversable_guides):
        edge_start, edge_end = edge_nodes[excluded_edge]
        source = edge_end
        goal = edge_start
        distances = {source: 0}
        predecessors = {source: []}
        queue = deque([source])

        while queue:
            current = queue.popleft()
            current_distance = distances[current]
            if current_distance >= max_edges - 1:
                continue
            for candidate_edge, neighbor in adjacency.get(current, []):
                visit_count += 1
                if visit_count > max_visits:
                    raise GuideGraphBudgetError(
                        "Closed-cell discovery exceeded its safe search budget. "
                        "Build or simplify part of the guide network before continuing."
                    )
                if candidate_edge == excluded_edge:
                    continue
                next_distance = current_distance + 1
                if next_distance > max_edges - 1:
                    continue
                known_distance = distances.get(neighbor)
                if known_distance is None:
                    distances[neighbor] = next_distance
                    predecessors[neighbor] = [(current, candidate_edge)]
                    queue.append(neighbor)
                elif known_distance == next_distance:
                    predecessors[neighbor].append((current, candidate_edge))

        if goal not in distances or distances[goal] + 1 < 3:
            continue

        path_count = 0

        def collect_paths(node, reverse_nodes, reverse_edges):
            nonlocal path_count
            if path_count >= max_paths_per_edge:
                return
            if node == source:
                path_nodes = list(reversed(reverse_nodes + [source]))
                path_edges = list(reversed(reverse_edges))
                cycle_edges = [excluded_edge] + path_edges
                cycle_nodes = [edge_start] + path_nodes[:-1]
                register_cycle(cycle_edges, cycle_nodes)
                path_count += 1
                return
            for parent, path_edge in predecessors.get(node, []):
                collect_paths(
                    parent,
                    reverse_nodes + [node],
                    reverse_edges + [path_edge],
                )
                if path_count >= max_paths_per_edge:
                    break

        collect_paths(goal, [], [])

    return list(cycles.values()), edge_nodes, guide_by_id


def find_four_sided_cycles(guides, max_edges=MAX_CYCLE_EDGES):
    cycles, edge_nodes, guide_by_id = find_closed_cycles(
        guides,
        max_edges=max_edges,
    )
    return (
        [cycle for cycle in cycles if len(cycle.sides) == 4],
        edge_nodes,
        guide_by_id,
    )


def oriented_edge_points(guide, start_node, end_node):
    actual_start, actual_end = _edge_nodes(guide)
    if (actual_start, actual_end) == (start_node, end_node):
        return [point.copy() for point in guide.points_local]
    if (actual_end, actual_start) == (start_node, end_node):
        return [point.copy() for point in reversed(guide.points_local)]
    raise ValueError("Guide edge is not connected to the requested nodes.")


def oriented_edge_anchors(guide, start_node, end_node):
    if len(guide.anchors) != len(guide.points_local):
        return []
    actual_start, actual_end = _edge_nodes(guide)
    if (actual_start, actual_end) == (start_node, end_node):
        return [anchor.copy() for anchor in guide.anchors]
    if (actual_end, actual_start) == (start_node, end_node):
        return [anchor.copy() for anchor in reversed(guide.anchors)]
    raise ValueError("Guide edge is not connected to the requested nodes.")


def oriented_side_points(side, guide_by_id):
    points = []
    current = side.start_node
    for edge_id in side.edge_ids:
        guide = guide_by_id[edge_id]
        start, end = _edge_nodes(guide)
        if start == current:
            following = end
        elif end == current:
            following = start
        else:
            raise ValueError("Logical guide side has inconsistent connectivity.")
        edge_points = oriented_edge_points(guide, current, following)
        if points:
            edge_points = edge_points[1:]
        points.extend(edge_points)
        current = following
    if current != side.end_node:
        raise ValueError("Logical guide side ends at the wrong graph node.")
    return points


def oriented_side_anchors(side, guide_by_id):
    anchors = []
    current = side.start_node
    for edge_id in side.edge_ids:
        guide = guide_by_id[edge_id]
        start, end = _edge_nodes(guide)
        if start == current:
            following = end
        elif end == current:
            following = start
        else:
            raise ValueError("Logical guide side has inconsistent connectivity.")
        edge_anchors = oriented_edge_anchors(guide, current, following)
        if not edge_anchors:
            return []
        if anchors:
            edge_anchors = edge_anchors[1:]
        anchors.extend(edge_anchors)
        current = following
    if current != side.end_node:
        raise ValueError("Logical guide side ends at the wrong graph node.")
    return anchors


def with_normalized_winding(cycle, reverse, target_local_area):
    if reverse:
        sides = tuple(
            BoundarySide(
                edge_ids=tuple(reversed(side.edge_ids)),
                start_node=int(side.end_node),
                end_node=int(side.start_node),
                logical_side_id=int(side.logical_side_id),
            )
            for side in reversed(cycle.sides)
        )
    else:
        sides = tuple(cycle.sides)
    return GuideCycle(
        key=str(cycle.key),
        sides=sides,
        traversal_nodes=tuple(int(side.start_node) for side in sides),
        winding="TARGET_LOCAL_CCW",
        target_local_area=float(target_local_area),
    )
