from collections import deque

from bpy_extras import view3d_utils
from mathutils import Vector
from mathutils.bvhtree import BVHTree

from .guide_graph import SurfaceAnchor


class SurfaceProjector:
    def __init__(self, target_object, depsgraph):
        self._group_indices = {
            group.name: group.index for group in target_object.vertex_groups
        }
        self._polygon_vertices = []
        self._polygon_normals = []
        self._vertices = []
        self._vertex_normals = []
        self._triangles = {}
        self._face_triangles = {}
        self._face_neighbors = {}
        self._face_components = {}
        self._vertex_group_weights = []
        self._matrix_world = None
        self._topology_revision = 0
        self._world_bounds_diagonal = 0.0
        self.bvh = None
        evaluated_target = None
        evaluated_mesh = None
        try:
            evaluated_target = target_object.evaluated_get(depsgraph)
            evaluated_mesh = evaluated_target.to_mesh(
                preserve_all_data_layers=True,
                depsgraph=depsgraph,
            )
            self._matrix_world = evaluated_target.matrix_world.copy()
            evaluated_mesh.calc_loop_triangles()
            vertices = [vertex.co.copy() for vertex in evaluated_mesh.vertices]
            self._vertices = [vertex.copy() for vertex in vertices]
            self._vertex_normals = [
                vertex.normal.copy().normalized()
                for vertex in evaluated_mesh.vertices
            ]
            self._polygon_vertices = [
                tuple(polygon.vertices)
                for polygon in evaluated_mesh.polygons
            ]
            self._polygon_normals = [
                polygon.normal.copy().normalized()
                for polygon in evaluated_mesh.polygons
            ]
            self._face_triangles = {
                index: [] for index in range(len(self._polygon_vertices))
            }
            for triangle_index, triangle in enumerate(
                evaluated_mesh.loop_triangles
            ):
                record = (
                    int(triangle.polygon_index),
                    tuple(int(value) for value in triangle.vertices),
                )
                self._triangles[int(triangle_index)] = record
                self._face_triangles[record[0]].append(int(triangle_index))
            self._build_face_connectivity()
            world_vertices = [
                self._matrix_world @ vertex for vertex in self._vertices
            ]
            if world_vertices:
                minimum = Vector(
                    tuple(min(point[axis] for point in world_vertices) for axis in range(3))
                )
                maximum = Vector(
                    tuple(max(point[axis] for point in world_vertices) for axis in range(3))
                )
                self._world_bounds_diagonal = float((maximum - minimum).length)
            revision = 2166136261
            for value in (
                len(vertices),
                len(self._polygon_vertices),
                *(
                    vertex_index + 1
                    for polygon in self._polygon_vertices
                    for vertex_index in (-1, *polygon)
                ),
            ):
                revision ^= int(value) & 0xFFFFFFFF
                revision = (revision * 16777619) & 0xFFFFFFFF
            self._topology_revision = int(revision)
            self._vertex_group_weights = [
                {
                    assignment.group: assignment.weight
                    for assignment in vertex.groups
                }
                for vertex in evaluated_mesh.vertices
            ]
            if not vertices or not self._polygon_vertices:
                raise ValueError(
                    f"The projection surface '{target_object.name}' has no faces."
                )
            self.bvh = BVHTree.FromPolygons(
                vertices,
                self._polygon_vertices,
                all_triangles=False,
                epsilon=0.0,
            )
        except Exception:
            self.close()
            raise
        finally:
            if evaluated_target is not None and evaluated_mesh is not None:
                evaluated_target.to_mesh_clear()

    def close(self):
        self.bvh = None
        self._matrix_world = None
        self._polygon_vertices = []
        self._polygon_normals = []
        self._vertices = []
        self._vertex_normals = []
        self._triangles = {}
        self._face_triangles = {}
        self._face_neighbors = {}
        self._face_components = {}
        self._vertex_group_weights = []
        self._group_indices = {}
        self._topology_revision = 0
        self._world_bounds_diagonal = 0.0

    @property
    def topology_revision(self):
        return int(self._topology_revision)

    @property
    def world_bounds_diagonal(self):
        return float(self._world_bounds_diagonal)

    def _build_face_connectivity(self):
        edge_faces = {}
        self._face_neighbors = {
            index: set() for index in range(len(self._polygon_vertices))
        }
        for face_index, polygon in enumerate(self._polygon_vertices):
            for start, end in zip(polygon, (*polygon[1:], polygon[0])):
                key = tuple(sorted((int(start), int(end))))
                edge_faces.setdefault(key, []).append(int(face_index))
        for owners in edge_faces.values():
            for face_index in owners:
                self._face_neighbors[face_index].update(
                    other for other in owners if other != face_index
                )

        self._face_components = {}
        component = 0
        for face_index in range(len(self._polygon_vertices)):
            if face_index in self._face_components:
                continue
            queue = deque((face_index,))
            self._face_components[face_index] = component
            while queue:
                current = queue.popleft()
                for neighbor in self._face_neighbors.get(current, ()):
                    if neighbor in self._face_components:
                        continue
                    self._face_components[neighbor] = component
                    queue.append(neighbor)
            component += 1

    @staticmethod
    def _closest_point_barycentric(point, first, second, third):
        point = Vector(point)
        first = Vector(first)
        second = Vector(second)
        third = Vector(third)
        edge_ab = second - first
        edge_ac = third - first
        to_point = point - first
        d1 = edge_ab.dot(to_point)
        d2 = edge_ac.dot(to_point)
        if d1 <= 0.0 and d2 <= 0.0:
            return first.copy(), (1.0, 0.0, 0.0)

        to_point = point - second
        d3 = edge_ab.dot(to_point)
        d4 = edge_ac.dot(to_point)
        if d3 >= 0.0 and d4 <= d3:
            return second.copy(), (0.0, 1.0, 0.0)

        edge_region = d1 * d4 - d3 * d2
        if edge_region <= 0.0 and d1 >= 0.0 and d3 <= 0.0:
            factor = d1 / max(d1 - d3, 1.0e-20)
            return first + edge_ab * factor, (1.0 - factor, factor, 0.0)

        to_point = point - third
        d5 = edge_ab.dot(to_point)
        d6 = edge_ac.dot(to_point)
        if d6 >= 0.0 and d5 <= d6:
            return third.copy(), (0.0, 0.0, 1.0)

        edge_region = d5 * d2 - d1 * d6
        if edge_region <= 0.0 and d2 >= 0.0 and d6 <= 0.0:
            factor = d2 / max(d2 - d6, 1.0e-20)
            return first + edge_ac * factor, (1.0 - factor, 0.0, factor)

        edge_region = d3 * d6 - d5 * d4
        if (
            edge_region <= 0.0
            and d4 - d3 >= 0.0
            and d5 - d6 >= 0.0
        ):
            factor = (d4 - d3) / max(
                (d4 - d3) + (d5 - d6),
                1.0e-20,
            )
            return second + (third - second) * factor, (
                0.0,
                1.0 - factor,
                factor,
            )

        denominator = edge_region + (d5 * d2 - d1 * d6) + (d1 * d4 - d3 * d2)
        if abs(denominator) <= 1.0e-20:
            choices = (
                ((point - first).length_squared, first, (1.0, 0.0, 0.0)),
                ((point - second).length_squared, second, (0.0, 1.0, 0.0)),
                ((point - third).length_squared, third, (0.0, 0.0, 1.0)),
            )
            _distance, location, weights = min(choices, key=lambda item: item[0])
            return location.copy(), weights
        inverse = 1.0 / denominator
        weight_second = (d5 * d2 - d1 * d6) * inverse
        weight_third = (d1 * d4 - d3 * d2) * inverse
        weight_first = 1.0 - weight_second - weight_third
        return (
            first + edge_ab * weight_second + edge_ac * weight_third,
            (weight_first, weight_second, weight_third),
        )

    def _triangle_normal(self, vertex_indices, barycentric):
        normal = Vector((0.0, 0.0, 0.0))
        if len(barycentric) == 3:
            for vertex_index, weight in zip(vertex_indices, barycentric):
                if 0 <= vertex_index < len(self._vertex_normals):
                    normal += self._vertex_normals[vertex_index] * float(weight)
        if normal.length <= 1.0e-12:
            first, second, third = (
                self._vertices[index] for index in vertex_indices
            )
            normal = (second - first).cross(third - first)
        if normal.length <= 1.0e-12:
            return Vector((0.0, 0.0, 1.0))
        return normal.normalized()

    def _candidate_on_faces(self, point_local, face_indices):
        best = None
        for face_index in face_indices:
            for triangle_index in self._face_triangles.get(int(face_index), ()):
                _owner, vertex_indices = self._triangles[triangle_index]
                first, second, third = (
                    self._vertices[index] for index in vertex_indices
                )
                location, barycentric = self._closest_point_barycentric(
                    point_local,
                    first,
                    second,
                    third,
                )
                distance = float((Vector(point_local) - location).length)
                if best is not None and distance >= best["distance"]:
                    continue
                best = {
                    "location": location,
                    "normal": self._triangle_normal(
                        vertex_indices,
                        barycentric,
                    ),
                    "face_index": int(face_index),
                    "triangle_index": int(triangle_index),
                    "barycentric": tuple(float(value) for value in barycentric),
                    "distance": distance,
                }
        return best

    def _anchor_from_candidate(
        self,
        candidate,
        target_object_uuid,
        normal_offset,
    ):
        return SurfaceAnchor(
            target_object_uuid=str(target_object_uuid),
            face_index=int(candidate["face_index"]),
            triangle_index=int(candidate["triangle_index"]),
            barycentric=tuple(candidate["barycentric"]),
            local_position=Vector(candidate["location"]),
            local_normal=Vector(candidate["normal"]).normalized(),
            normal_offset=float(normal_offset),
            topology_revision=int(self.topology_revision),
            shell_component=int(
                self._face_components.get(int(candidate["face_index"]), -1)
            ),
        )

    def _anchor_local_frame(self, anchor):
        if anchor is None:
            return None
        triangle = self._triangles.get(int(anchor.triangle_index))
        barycentric = tuple(float(value) for value in anchor.barycentric)
        if (
            int(anchor.topology_revision) == int(self.topology_revision)
            and triangle is not None
            and len(barycentric) == 3
        ):
            _face_index, vertex_indices = triangle
            location = sum(
                (
                    self._vertices[index] * weight
                    for index, weight in zip(vertex_indices, barycentric)
                ),
                Vector((0.0, 0.0, 0.0)),
            )
            normal = self._triangle_normal(vertex_indices, barycentric)
            return location, normal
        normal = Vector(anchor.local_normal)
        if normal.length <= 1.0e-12:
            return None
        return Vector(anchor.local_position), normal.normalized()

    def world_from_anchor(self, anchor):
        frame = self._anchor_local_frame(anchor)
        if frame is None or self._matrix_world is None:
            return None
        location_local, normal_local = frame
        normal_world = self._normal_to_world(normal_local)
        location_world = self._matrix_world @ location_local
        location_world += normal_world * float(anchor.normal_offset)
        return location_world, normal_world

    def _automatic_projection_distance(self):
        return max(float(self._world_bounds_diagonal) * 0.05, 1.0e-5)

    def _automatic_surface_step(self):
        return max(float(self._world_bounds_diagonal) * 0.25, 1.0e-5)

    def _accept_anchor(
        self,
        candidate_world,
        anchor,
        previous_anchor,
        max_projection_distance,
        max_surface_step,
        normal_continuity_cos,
    ):
        resolved = self.world_from_anchor(anchor)
        if resolved is None:
            return False
        location_world, normal_world = resolved
        projection_limit = (
            float(max_projection_distance)
            if float(max_projection_distance) > 0.0
            else self._automatic_projection_distance()
        )
        if (Vector(candidate_world) - location_world).length > projection_limit:
            return False
        if previous_anchor is None:
            return True
        if (
            str(previous_anchor.target_object_uuid)
            and str(anchor.target_object_uuid)
            != str(previous_anchor.target_object_uuid)
        ):
            return False
        previous_frame = self.world_from_anchor(previous_anchor)
        if previous_frame is None:
            return True
        previous_world, previous_normal = previous_frame
        previous_component = int(previous_anchor.shell_component)
        if (
            previous_component < 0
            and int(previous_anchor.topology_revision)
            == int(self.topology_revision)
        ):
            previous_component = int(
                self._face_components.get(
                    int(previous_anchor.face_index),
                    -1,
                )
            )
        if (
            previous_component >= 0
            and int(anchor.shell_component) >= 0
            and previous_component != int(anchor.shell_component)
        ):
            return False
        step_limit = (
            float(max_surface_step)
            if float(max_surface_step) > 0.0
            else self._automatic_surface_step()
        )
        if (location_world - previous_world).length > step_limit:
            return False
        if previous_normal.dot(normal_world) < float(normal_continuity_cos):
            return False
        return True

    def nearest_world_continuous(
        self,
        point_world,
        target_object_uuid,
        previous_anchor=None,
        surface_offset=0.0,
        max_projection_distance=0.0,
        max_surface_step=0.0,
        normal_continuity_cos=-0.05,
    ):
        if self._matrix_world is None:
            return None
        point_world = Vector(point_world)
        point_local = self._matrix_world.inverted_safe() @ point_world
        candidates = []
        if (
            previous_anchor is not None
            and int(previous_anchor.topology_revision) == int(self.topology_revision)
            and int(previous_anchor.face_index) in self._face_triangles
        ):
            local_faces = {int(previous_anchor.face_index)}
            local_faces.update(
                self._face_neighbors.get(int(previous_anchor.face_index), ())
            )
            local_candidate = self._candidate_on_faces(point_local, local_faces)
            if local_candidate is not None:
                candidates.append(local_candidate)

        nearest = self.nearest_local(point_local)
        if nearest is not None:
            _location, _normal, face_index, _distance = nearest
            global_candidate = self._candidate_on_faces(
                point_local,
                (int(face_index),),
            )
            if global_candidate is not None and not any(
                int(candidate["triangle_index"])
                == int(global_candidate["triangle_index"])
                for candidate in candidates
            ):
                candidates.append(global_candidate)

        for candidate in candidates:
            anchor = self._anchor_from_candidate(
                candidate,
                target_object_uuid,
                surface_offset,
            )
            if not self._accept_anchor(
                point_world,
                anchor,
                previous_anchor,
                max_projection_distance,
                max_surface_step,
                normal_continuity_cos,
            ):
                continue
            location_world, normal_world = self.world_from_anchor(anchor)
            return (
                location_world,
                normal_world,
                int(anchor.face_index),
                float((point_world - location_world).length),
                anchor,
            )
        return None

    def _normal_to_world(self, normal_local):
        inverse = self._matrix_world.inverted_safe()
        return (inverse.transposed().to_3x3() @ normal_local).normalized()

    def nearest_local(self, point_local):
        if self.bvh is None:
            return None
        location, normal, face_index, distance = self.bvh.find_nearest(
            Vector(point_local)
        )
        if location is None:
            return None
        return location.copy(), normal.normalized(), face_index, distance

    def nearest_target_local(self, point_world):
        if self._matrix_world is None:
            return None
        inverse = self._matrix_world.inverted_safe()
        return self.nearest_local(inverse @ Vector(point_world))

    def raycast_world(self, origin_world, direction_world, surface_offset=0.0):
        inverse = self._matrix_world.inverted_safe()
        origin_local = inverse @ Vector(origin_world)
        direction_local = (inverse.to_3x3() @ Vector(direction_world)).normalized()
        location, normal, face_index, distance = self.bvh.ray_cast(
            origin_local, direction_local
        )
        if location is None:
            return None

        world_normal = self._normal_to_world(normal)
        world_location = self._matrix_world @ location
        world_location += world_normal * surface_offset
        return world_location, world_normal, face_index, distance

    def raycast_world_continuous(
        self,
        origin_world,
        direction_world,
        target_object_uuid,
        previous_anchor=None,
        surface_offset=0.0,
        frontface_epsilon=0.001,
        max_surface_step=0.0,
        normal_continuity_cos=-0.05,
    ):
        if self.bvh is None or self._matrix_world is None:
            return None
        origin_world = Vector(origin_world)
        direction_world = Vector(direction_world)
        if direction_world.length <= 1.0e-12:
            return None
        direction_world.normalize()
        inverse = self._matrix_world.inverted_safe()
        origin_local = inverse @ origin_world
        direction_local = (inverse.to_3x3() @ direction_world).normalized()
        location, _normal, face_index, distance = self.bvh.ray_cast(
            origin_local,
            direction_local,
        )
        if location is None:
            return None
        candidate = self._candidate_on_faces(
            Vector(location),
            (int(face_index),),
        )
        if candidate is None:
            return None
        anchor = self._anchor_from_candidate(
            candidate,
            target_object_uuid,
            surface_offset,
        )
        location_world, normal_world = self.world_from_anchor(anchor)
        if normal_world.dot(direction_world) >= -abs(float(frontface_epsilon)):
            return None
        if not self._accept_anchor(
            location_world,
            anchor,
            previous_anchor,
            max_projection_distance=self._automatic_projection_distance(),
            max_surface_step=max_surface_step,
            normal_continuity_cos=normal_continuity_cos,
        ):
            return None
        return (
            location_world,
            normal_world,
            int(anchor.face_index),
            float(distance),
            anchor,
        )

    def nearest_world(self, point_world, surface_offset=0.0):
        nearest = self.nearest_target_local(point_world)
        if nearest is None:
            return None
        location, normal, face_index, distance = nearest

        world_normal = self._normal_to_world(normal)
        world_location = self._matrix_world @ location
        world_location += world_normal * surface_offset
        return world_location, world_normal, face_index, distance

    def sample_vertex_group_world(self, point_world, group_name):
        group_index = self._group_indices.get(group_name)
        if group_index is None:
            return None

        nearest = self.nearest_world(point_world)
        if nearest is None:
            return None
        face_index = nearest[2]
        if face_index < 0 or face_index >= len(self._polygon_vertices):
            return None

        polygon_vertices = self._polygon_vertices[face_index]
        weights = []
        for vertex_index in polygon_vertices:
            if vertex_index >= len(self._vertex_group_weights):
                continue
            weights.append(
                self._vertex_group_weights[vertex_index].get(group_index, 0.0)
            )
        if not weights:
            return None
        return sum(weights) / len(weights)

    def mean_vertex_group_world(self, points_world, group_name):
        values = []
        for point in points_world:
            value = self.sample_vertex_group_world(point, group_name)
            if value is not None:
                values.append(value)
        if not values:
            return None
        return sum(values) / len(values)

    def raycast_region(
        self,
        region,
        region_3d,
        mouse_region,
        surface_offset=0.0,
    ):
        origin = view3d_utils.region_2d_to_origin_3d(
            region, region_3d, mouse_region
        )
        direction = view3d_utils.region_2d_to_vector_3d(
            region, region_3d, mouse_region
        )
        return self.raycast_world(origin, direction, surface_offset)

    def raycast_region_continuous(
        self,
        region,
        region_3d,
        mouse_region,
        target_object_uuid,
        previous_anchor=None,
        surface_offset=0.0,
        frontface_epsilon=0.001,
        max_surface_step=0.0,
        normal_continuity_cos=-0.05,
    ):
        origin = view3d_utils.region_2d_to_origin_3d(
            region,
            region_3d,
            mouse_region,
        )
        direction = view3d_utils.region_2d_to_vector_3d(
            region,
            region_3d,
            mouse_region,
        )
        return self.raycast_world_continuous(
            origin,
            direction,
            target_object_uuid=target_object_uuid,
            previous_anchor=previous_anchor,
            surface_offset=surface_offset,
            frontface_epsilon=frontface_epsilon,
            max_surface_step=max_surface_step,
            normal_continuity_cos=normal_continuity_cos,
        )
