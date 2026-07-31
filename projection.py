from bpy_extras import view3d_utils
from mathutils import Vector
from mathutils.bvhtree import BVHTree


class SurfaceProjector:
    def __init__(self, target_object, depsgraph):
        self._group_indices = {
            group.name: group.index for group in target_object.vertex_groups
        }
        self._polygon_vertices = []
        self._vertex_group_weights = []
        self._matrix_world = None
        self._topology_revision = 0
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
            vertices = [vertex.co.copy() for vertex in evaluated_mesh.vertices]
            self._polygon_vertices = [
                tuple(polygon.vertices)
                for polygon in evaluated_mesh.polygons
            ]
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
        self._vertex_group_weights = []
        self._group_indices = {}
        self._topology_revision = 0

    @property
    def topology_revision(self):
        return int(self._topology_revision)

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
