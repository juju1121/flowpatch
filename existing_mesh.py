import hashlib
import json
from copy import deepcopy
from dataclasses import dataclass


EXISTING_MESH_SCHEMA_VERSION = 1
EXISTING_MESH_DATA_KEY = "flowpatch_existing_mesh_regions_v1"


class ExistingMeshError(RuntimeError):
    def __init__(self, reason_code, message):
        super().__init__(message)
        self.reason_code = str(reason_code)


@dataclass(frozen=True)
class QuadIslandPlan:
    face_ids: tuple
    boundary_loops: tuple
    inferred_sides: tuple
    grid_vertex_ids: tuple
    signature: str

    def as_dict(self):
        return {
            "face_ids": list(self.face_ids),
            "boundary_loops": [
                list(loop) for loop in self.boundary_loops
            ],
            "inferred_sides": [
                list(side) for side in self.inferred_sides
            ],
            "grid_vertex_ids": [
                list(row) for row in self.grid_vertex_ids
            ],
            "signature": self.signature,
        }


@dataclass(frozen=True)
class AdoptionPlan:
    islands: tuple

    def as_dict(self):
        return {
            "islands": [island.as_dict() for island in self.islands],
        }


@dataclass(frozen=True)
class BoundaryBridgePlan:
    loop_a: tuple
    loop_b: tuple
    faces: tuple
    signature: str

    def as_dict(self):
        return {
            "loop_a": list(self.loop_a),
            "loop_b": list(self.loop_b),
            "faces": [list(face) for face in self.faces],
            "signature": self.signature,
        }


def canonical_edge(start, end):
    start = int(start)
    end = int(end)
    if start == end:
        raise ExistingMeshError(
            "COLLAPSED_EDGE",
            "A selected face contains a collapsed edge.",
        )
    return (start, end) if start < end else (end, start)


def _canonical_cycle(values):
    cycle = tuple(int(value) for value in values)
    if len(cycle) > 1 and cycle[0] == cycle[-1]:
        cycle = cycle[:-1]
    if len(cycle) < 3 or len(set(cycle)) != len(cycle):
        raise ExistingMeshError(
            "MALFORMED_FACE",
            "A selected face has repeated or missing vertices.",
        )
    rotations = []
    for candidate in (cycle, tuple(reversed(cycle))):
        rotations.extend(
            candidate[index:] + candidate[:index]
            for index in range(len(candidate))
        )
    return min(rotations)


def _canonical_loop(values):
    loop = tuple(int(value) for value in values)
    if len(loop) > 1 and loop[0] == loop[-1]:
        loop = loop[:-1]
    if len(loop) < 3 or len(set(loop)) != len(loop):
        raise ExistingMeshError(
            "MALFORMED_BOUNDARY_LOOP",
            "A selected boundary loop is collapsed or self-repeating.",
        )
    rotations = []
    for candidate in (loop, tuple(reversed(loop))):
        rotations.extend(
            candidate[index:] + candidate[:index]
            for index in range(len(candidate))
        )
    return min(rotations)


def _sha256_payload(payload):
    encoded = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest().upper()


def island_signature(face_cycles):
    canonical = sorted(
        _canonical_cycle(cycle) for cycle in face_cycles
    )
    if not canonical:
        raise ExistingMeshError(
            "EMPTY_ISLAND",
            "An adopted island must contain at least one face.",
        )
    return _sha256_payload({"faces": canonical})


def _selected_components(selected, edge_uses, face_edges):
    adjacency = {face_id: set() for face_id in selected}
    for face_id in selected:
        for edge in face_edges[face_id]:
            for neighbor in edge_uses[edge]:
                if neighbor in selected and neighbor != face_id:
                    adjacency[face_id].add(neighbor)

    components = []
    remaining = set(selected)
    while remaining:
        start = min(remaining)
        stack = [start]
        component = set()
        while stack:
            face_id = stack.pop()
            if face_id in component:
                continue
            component.add(face_id)
            stack.extend(sorted(adjacency[face_id] - component, reverse=True))
        remaining.difference_update(component)
        components.append(tuple(sorted(component)))
    return tuple(components)


def _boundary_loops(boundary_edges):
    adjacency = {}
    for start, end in boundary_edges:
        adjacency.setdefault(start, set()).add(end)
        adjacency.setdefault(end, set()).add(start)
    if not adjacency:
        raise ExistingMeshError(
            "BOUNDARY_REQUIRED",
            "An adopted island must have at least one boundary loop.",
        )
    if any(len(neighbors) != 2 for neighbors in adjacency.values()):
        raise ExistingMeshError(
            "BOUNDARY_BRANCH",
            "The selected island boundary branches or is open.",
        )

    unvisited = set(boundary_edges)
    loops = []
    while unvisited:
        start = min(vertex for edge in unvisited for vertex in edge)
        neighbors = sorted(adjacency[start])
        candidates = []
        for first in neighbors:
            ordered = [start]
            previous = None
            current = start
            next_vertex = first
            traversed = set()
            while True:
                edge = canonical_edge(current, next_vertex)
                if edge in traversed:
                    break
                traversed.add(edge)
                previous, current = current, next_vertex
                ordered.append(current)
                if current == start:
                    break
                following = sorted(adjacency[current] - {previous})
                if len(following) != 1:
                    raise ExistingMeshError(
                        "BOUNDARY_BRANCH",
                        "The selected island boundary cannot be ordered.",
                    )
                next_vertex = following[0]
            if ordered[-1] != start:
                raise ExistingMeshError(
                    "BOUNDARY_OPEN",
                    "The selected island boundary is not closed.",
                )
            candidates.append(
                (_canonical_loop(ordered[:-1]), traversed)
            )
        loop, traversed = min(candidates, key=lambda item: item[0])
        if not traversed.issubset(unvisited):
            raise ExistingMeshError(
                "BOUNDARY_OVERLAP",
                "Adopted boundary loops overlap.",
            )
        unvisited.difference_update(traversed)
        loops.append(loop)
    return tuple(sorted(loops))


def _inferred_grid_sides(loop, component, face_cycles):
    face_degree = {}
    for face_id in component:
        for vertex_id in face_cycles[face_id]:
            face_degree[vertex_id] = face_degree.get(vertex_id, 0) + 1
    corners = {
        vertex_id
        for vertex_id in loop
        if face_degree.get(vertex_id, 0) == 1
    }
    if len(corners) != 4:
        return ()

    corner_indices = [
        index for index, vertex_id in enumerate(loop) if vertex_id in corners
    ]
    if len(corner_indices) != 4:
        return ()
    start_index = min(
        corner_indices,
        key=lambda index: loop[index],
    )
    ordered = loop[start_index:] + loop[:start_index]
    corner_indices = [
        index for index, vertex_id in enumerate(ordered)
        if vertex_id in corners
    ]
    corner_indices.append(len(ordered))
    sides = []
    for index in range(4):
        start = corner_indices[index]
        end = corner_indices[index + 1]
        side = (
            list(ordered[start : end + 1])
            if end < len(ordered)
            else list(ordered[start:]) + [ordered[0]]
        )
        if len(side) < 2:
            return ()
        sides.append(tuple(side))
    return tuple(sides)


def infer_rectangular_quad_grid(face_cycles, face_ids, inferred_sides):
    cycles = {
        int(face_id): tuple(int(vertex_id) for vertex_id in cycle)
        for face_id, cycle in dict(face_cycles).items()
    }
    component = tuple(sorted(int(face_id) for face_id in face_ids))
    sides = tuple(
        tuple(int(vertex_id) for vertex_id in side)
        for side in tuple(inferred_sides or ())
    )
    if (
        len(sides) != 4
        or any(len(side) < 2 for side in sides)
        or len(sides[0]) != len(sides[2])
        or len(sides[1]) != len(sides[3])
    ):
        return ()
    u_segments = len(sides[0]) - 1
    v_segments = len(sides[1]) - 1
    if len(component) != u_segments * v_segments:
        return ()
    if any(
        face_id not in cycles
        or len(cycles[face_id]) != 4
        or len(set(cycles[face_id])) != 4
        for face_id in component
    ):
        return ()

    component_set = set(component)
    edge_faces = {}
    for face_id in component:
        cycle = cycles[face_id]
        for start, end in zip(cycle, cycle[1:] + cycle[:1]):
            edge_faces.setdefault(canonical_edge(start, end), set()).add(
                face_id
            )

    rows = [tuple(sides[0])]
    used_faces = set()
    for _row_index in range(v_segments):
        current = rows[-1]
        next_row = []
        for column in range(u_segments):
            start = current[column]
            end = current[column + 1]
            candidates = sorted(
                (
                    edge_faces.get(canonical_edge(start, end), set())
                    & component_set
                )
                - used_faces
            )
            if len(candidates) != 1:
                return ()
            face_id = candidates[0]
            cycle = cycles[face_id]
            start_index = cycle.index(start)
            end_index = cycle.index(end)
            if (start_index - end_index) % 4 not in {1, 3}:
                return ()
            start_neighbors = {
                cycle[(start_index - 1) % 4],
                cycle[(start_index + 1) % 4],
            } - {end}
            end_neighbors = {
                cycle[(end_index - 1) % 4],
                cycle[(end_index + 1) % 4],
            } - {start}
            if len(start_neighbors) != 1 or len(end_neighbors) != 1:
                return ()
            below_start = next(iter(start_neighbors))
            below_end = next(iter(end_neighbors))
            if canonical_edge(below_start, below_end) not in edge_faces:
                return ()
            if column == 0:
                next_row.append(below_start)
            elif next_row[-1] != below_start:
                return ()
            next_row.append(below_end)
            used_faces.add(face_id)
        rows.append(tuple(next_row))

    grid = tuple(rows)
    flattened = tuple(vertex_id for row in grid for vertex_id in row)
    if len(set(flattened)) != len(flattened):
        return ()
    if used_faces != component_set:
        return ()
    if grid[-1] != tuple(reversed(sides[2])):
        return ()
    if tuple(row[-1] for row in grid) != sides[1]:
        return ()
    if tuple(row[0] for row in reversed(grid)) != sides[3]:
        return ()

    expected_faces = {
        _canonical_cycle(cycles[face_id]) for face_id in component
    }
    inferred_faces = {
        _canonical_cycle(
            (
                grid[row][column],
                grid[row][column + 1],
                grid[row + 1][column + 1],
                grid[row + 1][column],
            )
        )
        for row in range(v_segments)
        for column in range(u_segments)
    }
    return grid if inferred_faces == expected_faces else ()


def plan_quad_island_adoption(
    face_cycles,
    selected_face_ids,
):
    cycles = {
        int(face_id): tuple(int(vertex_id) for vertex_id in cycle)
        for face_id, cycle in dict(face_cycles).items()
    }
    selected = {int(face_id) for face_id in selected_face_ids}
    if not selected:
        raise ExistingMeshError(
            "SELECTION_REQUIRED",
            "Select one or more complete all-quad mesh islands.",
        )
    missing = sorted(selected - set(cycles))
    if missing:
        raise ExistingMeshError(
            "MISSING_SELECTED_FACE",
            f"Selected face IDs are missing from the mesh: {missing[:8]}.",
        )
    for face_id in selected:
        cycle = cycles[face_id]
        if len(cycle) != 4 or len(set(cycle)) != 4:
            raise ExistingMeshError(
                "ALL_QUADS_REQUIRED",
                "Existing mesh adoption accepts all-quad islands only.",
            )

    edge_uses = {}
    face_edges = {}
    for face_id, cycle in cycles.items():
        if len(cycle) < 3:
            continue
        edges = tuple(
            canonical_edge(start, end)
            for start, end in zip(cycle, cycle[1:] + cycle[:1])
        )
        face_edges[face_id] = edges
        for edge in edges:
            edge_uses.setdefault(edge, set()).add(face_id)

    for face_id in selected:
        for edge in face_edges[face_id]:
            if len(edge_uses[edge]) > 2:
                raise ExistingMeshError(
                    "NON_MANIFOLD_ISLAND",
                    "A selected island contains a non-manifold edge.",
                )

    plans = []
    for component in _selected_components(selected, edge_uses, face_edges):
        component_set = set(component)
        for face_id in component:
            for edge in face_edges[face_id]:
                outside = edge_uses[edge] - component_set
                if outside:
                    raise ExistingMeshError(
                        "PARTIAL_ISLAND_SELECTION",
                        (
                            "Select the complete connected quad island; "
                            "the current selection stops inside mesh topology."
                        ),
                    )
        boundary_edges = {
            edge
            for face_id in component
            for edge in face_edges[face_id]
            if len(edge_uses[edge] & component_set) == 1
        }
        loops = _boundary_loops(boundary_edges)
        inferred = (
            _inferred_grid_sides(loops[0], component, cycles)
            if len(loops) == 1
            else ()
        )
        grid = infer_rectangular_quad_grid(
            cycles,
            component,
            inferred,
        )
        signature = island_signature(cycles[face_id] for face_id in component)
        plans.append(
            QuadIslandPlan(
                face_ids=component,
                boundary_loops=loops,
                inferred_sides=inferred,
                grid_vertex_ids=grid,
                signature=signature,
            )
        )
    return AdoptionPlan(
        islands=tuple(sorted(plans, key=lambda item: item.signature))
    )


def empty_existing_mesh_registry():
    return {
        "version": EXISTING_MESH_SCHEMA_VERSION,
        "islands": {},
        "bridges": {},
    }


def parse_existing_mesh_registry(raw):
    if raw in (None, ""):
        return empty_existing_mesh_registry()
    if isinstance(raw, str):
        try:
            payload = json.loads(raw)
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            raise ExistingMeshError(
                "MALFORMED_EXISTING_MESH_REGISTRY",
                "Existing-mesh metadata is not valid JSON.",
            ) from exc
    elif isinstance(raw, dict):
        payload = deepcopy(raw)
    else:
        raise ExistingMeshError(
            "MALFORMED_EXISTING_MESH_REGISTRY",
            "Existing-mesh metadata must be a JSON object.",
        )
    try:
        version = int(payload.get("version", 0))
    except (TypeError, ValueError) as exc:
        raise ExistingMeshError(
            "MALFORMED_EXISTING_MESH_SCHEMA",
            "Existing-mesh metadata has a malformed schema version.",
        ) from exc
    if version != EXISTING_MESH_SCHEMA_VERSION:
        raise ExistingMeshError(
            "UNSUPPORTED_EXISTING_MESH_SCHEMA",
            (
                f"Existing-mesh schema {version} is unsupported; "
                f"expected {EXISTING_MESH_SCHEMA_VERSION}."
            ),
        )
    islands = payload.get("islands", {})
    bridges = payload.get("bridges", {})
    if not isinstance(islands, dict) or not isinstance(bridges, dict):
        raise ExistingMeshError(
            "MALFORMED_EXISTING_MESH_REGISTRY",
            "Existing-mesh island and bridge records must be mappings.",
        )
    return {
        "version": EXISTING_MESH_SCHEMA_VERSION,
        "islands": deepcopy(islands),
        "bridges": deepcopy(bridges),
    }


def encode_existing_mesh_registry(registry):
    canonical = parse_existing_mesh_registry(registry)
    return json.dumps(
        canonical,
        sort_keys=True,
        separators=(",", ":"),
    )


def add_registry_record(registry, section, key, record):
    if section not in {"islands", "bridges"}:
        raise ExistingMeshError(
            "UNKNOWN_REGISTRY_SECTION",
            f"Unknown existing-mesh registry section: {section}.",
        )
    canonical = parse_existing_mesh_registry(registry)
    key = str(key)
    value = deepcopy(dict(record))
    existing = canonical[section].get(key)
    if existing is not None:
        if existing != value:
            raise ExistingMeshError(
                "REGISTRY_OWNERSHIP_CONFLICT",
                "Existing mesh ownership conflicts with the requested record.",
            )
        return canonical, False
    canonical[section][key] = value
    return canonical, True


def _position_tuple(value):
    result = tuple(float(component) for component in value)
    if len(result) != 3:
        raise ExistingMeshError(
            "MALFORMED_BRIDGE_POSITION",
            "Bridge alignment positions must contain three coordinates.",
        )
    return result


def _distance_squared(start, end):
    return sum(
        (float(a) - float(b)) ** 2 for a, b in zip(start, end)
    )


def plan_equal_count_bridge(
    loop_a,
    loop_b,
    positions=None,
    same_object=True,
):
    if not same_object:
        raise ExistingMeshError(
            "CROSS_OBJECT_BRIDGE_UNSUPPORTED",
            (
                "FlowPatch cannot weld geometry across separate mesh "
                "datablocks without destroying object identity."
            ),
        )
    first = _canonical_loop(loop_a)
    second = _canonical_loop(loop_b)
    if set(first) & set(second):
        raise ExistingMeshError(
            "BRIDGE_BOUNDARIES_OVERLAP",
            "The two bridge boundaries share vertices.",
        )
    if len(first) != len(second):
        raise ExistingMeshError(
            "BRIDGE_COUNT_MISMATCH",
            (
                "Selected bridge boundaries must have matching vertex counts "
                f"({len(first)} != {len(second)})."
            ),
        )

    candidates = []
    position_map = (
        {
            int(vertex_id): _position_tuple(position)
            for vertex_id, position in dict(positions).items()
        }
        if positions is not None
        else {}
    )
    if position_map and (
        not set(first).issubset(position_map)
        or not set(second).issubset(position_map)
    ):
        raise ExistingMeshError(
            "MISSING_BRIDGE_POSITION",
            "Every bridge boundary vertex needs an alignment position.",
        )
    for reverse in (False, True):
        candidate = tuple(reversed(second)) if reverse else second
        for shift in range(len(candidate)):
            aligned = candidate[shift:] + candidate[:shift]
            score = (
                sum(
                    _distance_squared(
                        position_map[first[index]],
                        position_map[aligned[index]],
                    )
                    for index in range(len(first))
                )
                if position_map
                else 0.0
            )
            candidates.append(
                (round(score, 12), int(reverse), shift, aligned)
            )
    _score, _reverse, _shift, aligned = min(candidates)
    faces = tuple(
        (
            first[index],
            first[(index + 1) % len(first)],
            aligned[(index + 1) % len(aligned)],
            aligned[index],
        )
        for index in range(len(first))
    )
    if any(len(set(face)) != 4 for face in faces):
        raise ExistingMeshError(
            "COLLAPSED_BRIDGE_FACE",
            "A planned bridge face would collapse.",
        )
    signature = _sha256_payload(
        {
            "loop_a": first,
            "loop_b": aligned,
            "faces": faces,
        }
    )
    return BoundaryBridgePlan(
        loop_a=first,
        loop_b=aligned,
        faces=faces,
        signature=signature,
    )
