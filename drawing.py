import blf
import bpy
import gpu
from gpu_extras.batch import batch_for_shader
from gpu_extras.presets import draw_texture_2d
from pathlib import Path


_ICON_DIR = Path(__file__).resolve().parent / "icons"


class FlowPatchPreviewRenderer:
    def __init__(self, session_guard=None):
        self._session_guard = session_guard
        self._enabled = True
        self.boundary = []
        self.stroke = []
        self.preview = None
        self.preview_patches = []
        self.active_preview_index = -1
        self.guide_paths = []
        self.composite_guide_paths = []
        self.selected_guide_paths = []
        self.guide_endpoints = []
        self.control_points = []
        self.custom_segments = []
        self.node_points = []
        self.edge_cut_points = []
        self.hover_segments = []
        self.snap_points = []
        self.snap_kind = ""
        self.opacity = 0.22
        self.hud_title = ""
        self.hud_lines = []
        self.toolbar_items = []
        self.toolbar_active = ""
        self.toolbar_hover = ""
        self.toolbar_status = ""
        self._toolbar_rects = {}
        self._toolbar_enabled = {}
        self._icon_cache = {}
        self._view_handler = None
        self._pixel_handler = None

    def _guard_active(self):
        if not self._enabled:
            return False
        if self._session_guard is None:
            return True
        try:
            return bool(self._session_guard())
        except Exception:
            return False

    def install(self):
        self._enabled = True
        try:
            if self._view_handler is None:
                self._view_handler = bpy.types.SpaceView3D.draw_handler_add(
                    self.draw, (), "WINDOW", "POST_VIEW"
                )
            if self._pixel_handler is None:
                self._pixel_handler = bpy.types.SpaceView3D.draw_handler_add(
                    self.draw_hud, (), "WINDOW", "POST_PIXEL"
                )
        except Exception:
            self.remove()
            raise

    def remove(self):
        self._enabled = False
        if self._view_handler is not None:
            try:
                bpy.types.SpaceView3D.draw_handler_remove(
                    self._view_handler, "WINDOW"
                )
            except Exception:
                pass
            finally:
                self._view_handler = None
        if self._pixel_handler is not None:
            try:
                bpy.types.SpaceView3D.draw_handler_remove(
                    self._pixel_handler, "WINDOW"
                )
            except Exception:
                pass
            finally:
                self._pixel_handler = None
        self._release_icons()
        self._session_guard = None

    def _release_icons(self):
        for image, _texture in self._icon_cache.values():
            try:
                if image is not None and image.name in bpy.data.images:
                    bpy.data.images.remove(image)
            except Exception:
                pass
        self._icon_cache.clear()

    def _icon_texture(self, filename):
        if not filename:
            return None
        cached = self._icon_cache.get(filename)
        if cached is not None:
            return cached[1]
        path = _ICON_DIR / filename
        if not path.is_file():
            self._icon_cache[filename] = (None, None)
            return None
        image = bpy.data.images.load(str(path), check_existing=False)
        image.name = f"FlowPatch HUD {filename}"
        image.colorspace_settings.name = "sRGB"
        image["_flowpatch_runtime_icon"] = True
        texture = gpu.texture.from_image(image)
        self._icon_cache[filename] = (image, texture)
        return texture

    def _draw_rect(self, shader, left, bottom, width, height, color):
        shader.bind()
        shader.uniform_float("color", color)
        batch_for_shader(
            shader,
            "TRIS",
            {
                "pos": (
                    (left, bottom),
                    (left + width, bottom),
                    (left + width, bottom + height),
                    (left, bottom),
                    (left + width, bottom + height),
                    (left, bottom + height),
                )
            },
        ).draw(shader)

    def _draw_toolbar(self, shader):
        if not self.toolbar_items:
            self._toolbar_rects = {}
            self._toolbar_enabled = {}
            return

        region = getattr(bpy.context, "region", None)
        region_width = float(getattr(region, "width", 900))
        gap = 3.0
        button_sizes = [
            50.0
            if str(item.get("priority", "SECONDARY")).upper() == "PRIMARY"
            else 36.0
            for item in self.toolbar_items
        ]
        toolbar_height = max(button_sizes)
        total_width = (
            sum(button_sizes)
            + gap * (len(self.toolbar_items) - 1)
            + 18.0
        )
        left = max(12.0, (region_width - total_width) * 0.5)
        bottom = 24.0
        self._draw_rect(
            shader,
            left,
            bottom,
            total_width,
            toolbar_height + 12.0,
            (0.018, 0.022, 0.028, 0.94),
        )

        cursor = left + 9.0
        self._toolbar_rects = {}
        self._toolbar_enabled = {}
        for item, button_size in zip(self.toolbar_items, button_sizes):
            identifier = str(item["id"])
            icon_inset = 4.0 if button_size >= 50.0 else 3.0
            enabled = bool(item.get("enabled", True))
            active = bool(
                item.get("active", identifier == self.toolbar_active)
            )
            hovered = identifier == self.toolbar_hover
            border = (
                (0.70, 1.0, 0.02, 1.0)
                if active
                else (0.26, 0.62, 1.0, 1.0)
                if hovered
                else (0.10, 0.115, 0.14, 1.0)
            )
            self._draw_rect(
                shader,
                cursor,
                bottom + 6.0,
                button_size,
                button_size,
                border,
            )
            self._toolbar_rects[identifier] = (
                cursor,
                bottom + 6.0,
                cursor + button_size,
                bottom + 6.0 + button_size,
            )
            self._toolbar_enabled[identifier] = enabled
            texture = self._icon_texture(item.get("icon", ""))
            if texture is not None:
                draw_texture_2d(
                    texture,
                    (
                        cursor + icon_inset,
                        bottom + 6.0 + icon_inset,
                    ),
                    button_size - icon_inset * 2.0,
                    button_size - icon_inset * 2.0,
                    is_scene_linear_with_rec709_srgb_target=True,
                )
            if not enabled:
                self._draw_rect(
                    shader,
                    cursor,
                    bottom + 6.0,
                    button_size,
                    button_size,
                    (0.015, 0.02, 0.03, 0.64),
                )
            cursor += button_size + gap

        if self.toolbar_status:
            font_id = 0
            blf.size(font_id, 12.0)
            blf.color(font_id, 0.90, 0.93, 0.97, 1.0)
            text_width, _text_height = blf.dimensions(
                font_id,
                self.toolbar_status,
            )
            status_left = max(12.0, (region_width - text_width - 22.0) * 0.5)
            status_bottom = bottom + toolbar_height + 23.0
            self._draw_rect(
                shader,
                status_left,
                status_bottom,
                text_width + 22.0,
                27.0,
                (0.018, 0.022, 0.028, 0.88),
            )
            blf.position(
                font_id,
                status_left + 11.0,
                status_bottom + 7.0,
                0,
            )
            blf.draw(font_id, self.toolbar_status)

    def toolbar_hit_test(self, mouse_region):
        x, y = mouse_region
        for identifier, (left, bottom, right, top) in self._toolbar_rects.items():
            if left <= x <= right and bottom <= y <= top:
                if self._toolbar_enabled.get(identifier, True):
                    return identifier
                return None
        return None

    def toolbar_hover_test(self, mouse_region):
        x, y = mouse_region
        for identifier, (left, bottom, right, top) in self._toolbar_rects.items():
            if left <= x <= right and bottom <= y <= top:
                return identifier
        return None

    def draw_hud(self):
        if not self._guard_active():
            return
        if not self.hud_title and not self.hud_lines and not self.toolbar_items:
            return
        try:
            gpu.state.blend_set("ALPHA")
            shader = gpu.shader.from_builtin("UNIFORM_COLOR")
            self._draw_toolbar(shader)
            if not self.hud_title and not self.hud_lines:
                return
            width = 330.0
            height = 42.0 + 19.0 * len(self.hud_lines)
            left = 20.0
            bottom = 88.0 if self.toolbar_items else 34.0
            self._draw_rect(
                shader,
                left,
                bottom,
                width,
                height,
                (0.025, 0.03, 0.04, 0.88),
            )

            font_id = 0
            blf.size(font_id, 14.0)
            blf.color(font_id, 0.72, 1.0, 0.08, 1.0)
            blf.position(font_id, left + 14.0, bottom + height - 25.0, 0)
            blf.draw(font_id, self.hud_title)

            blf.size(font_id, 12.0)
            blf.color(font_id, 0.9, 0.92, 0.95, 1.0)
            y = bottom + height - 47.0
            for line in self.hud_lines:
                blf.position(font_id, left + 14.0, y, 0)
                blf.draw(font_id, line)
                y -= 19.0
        except Exception:
            return
        finally:
            try:
                gpu.state.blend_set("NONE")
            except Exception:
                pass

    def _draw_lines(self, shader, segments, color, width):
        if not segments:
            return
        positions = []
        for start, end in segments:
            positions.extend((tuple(start), tuple(end)))
        gpu.state.line_width_set(width)
        shader.bind()
        shader.uniform_float("color", color)
        batch_for_shader(shader, "LINES", {"pos": positions}).draw(shader)

    def _draw_faces(self, shader, preview, color=None):
        if preview is None:
            return
        positions = []
        if str(getattr(preview, "topology_kind", "GRID")).upper() in {
            "POLYGON",
            "ANNULAR",
        }:
            for triangle in preview.polygon_triangles:
                positions.extend(tuple(point) for point in triangle)
        else:
            rails = preview.rails_world
            for row_index in range(preview.row_count):
                current = rails[row_index]
                following = rails[row_index + 1]
                for index in range(preview.segment_count):
                    a = current[index]
                    b = current[index + 1]
                    c = following[index + 1]
                    d = following[index]
                    positions.extend(
                        (
                            tuple(a),
                            tuple(b),
                            tuple(c),
                            tuple(a),
                            tuple(c),
                            tuple(d),
                        )
                    )
        if not positions:
            return
        shader.bind()
        shader.uniform_float(
            "color",
            color
            or (
                0.08,
                0.42,
                1.0,
                min(0.34, max(0.26, self.opacity * 1.35)),
            ),
        )
        batch_for_shader(shader, "TRIS", {"pos": positions}).draw(shader)

    def _draw_points(self, shader, points, color, size):
        if not points:
            return
        gpu.state.point_size_set(size)
        shader.bind()
        shader.uniform_float("color", color)
        batch_for_shader(
            shader,
            "POINTS",
            {"pos": [tuple(point) for point in points]},
        ).draw(shader)

    def draw(self):
        if not self._guard_active():
            return
        try:
            shader = gpu.shader.from_builtin("UNIFORM_COLOR")
            gpu.state.blend_set("ALPHA")
            gpu.state.depth_test_set("LESS_EQUAL")
            self._draw_faces(shader, self.preview)
            for preview_index, preview in enumerate(self.preview_patches):
                self._draw_faces(
                    shader,
                    preview,
                    (
                        (
                            0.25,
                            0.65,
                            1.0,
                            min(0.38, max(0.30, self.opacity * 1.55)),
                        )
                        if preview_index == self.active_preview_index
                        else (
                            0.08,
                            0.42,
                            1.0,
                            min(0.24, max(0.18, self.opacity * 0.90)),
                        )
                    ),
                )

            # Faces obey scene depth, while guide and grid feedback remains
            # readable over the projection surface from every view angle.
            gpu.state.depth_test_set("NONE")
            boundary_segments = list(zip(self.boundary, self.boundary[1:]))
            stroke_segments = list(zip(self.stroke, self.stroke[1:]))
            guide_segments = []
            for guide in self.guide_paths:
                guide_segments.extend(zip(guide, guide[1:]))
            composite_guide_segments = []
            for guide in self.composite_guide_paths:
                composite_guide_segments.extend(zip(guide, guide[1:]))
            selected_guide_segments = []
            for guide in self.selected_guide_paths:
                selected_guide_segments.extend(zip(guide, guide[1:]))
            preview_segments = []
            active_preview_segments = []
            previews = [self.preview] if self.preview is not None else []
            previews.extend(self.preview_patches)
            for preview_index, preview in enumerate(previews):
                segments = []
                topology_kind = str(
                    getattr(preview, "topology_kind", "GRID")
                ).upper()
                if topology_kind == "ANNULAR":
                    vertices = list(preview.quad_vertices_world)
                    edge_indices = set()
                    for face in preview.polygon_quads:
                        for start, end in zip(
                            face,
                            face[1:] + face[:1],
                        ):
                            edge_indices.add(tuple(sorted((start, end))))
                    segments.extend(
                        (vertices[start], vertices[end])
                        for start, end in sorted(edge_indices)
                    )
                elif topology_kind == "POLYGON":
                    outline = list(preview.polygon_world)
                    if len(outline) >= 2:
                        segments.extend(
                            zip(outline, outline[1:] + outline[:1])
                        )
                else:
                    for rail in preview.rails_world:
                        segments.extend(zip(rail, rail[1:]))
                    for index in range(len(preview.rails_world[0])):
                        for row_index in range(preview.row_count):
                            segments.append(
                                (
                                    preview.rails_world[row_index][index],
                                    preview.rails_world[row_index + 1][index],
                                )
                            )
                patch_index = (
                    preview_index - 1
                    if self.preview is not None
                    else preview_index
                )
                if (
                    preview is not self.preview
                    and patch_index == self.active_preview_index
                ):
                    active_preview_segments.extend(segments)
                else:
                    preview_segments.extend(segments)

            self._draw_lines(
                shader, preview_segments, (0.25, 0.62, 1.0, 0.70), 1.7
            )
            self._draw_lines(
                shader,
                active_preview_segments,
                (0.30, 0.74, 1.0, 0.98),
                2.5,
            )
            self._draw_lines(
                shader,
                composite_guide_segments,
                (0.015, 0.02, 0.03, 0.76),
                4.0,
            )
            self._draw_lines(
                shader,
                composite_guide_segments,
                (0.58, 0.34, 0.92, 0.78),
                2.0,
            )
            self._draw_lines(
                shader, guide_segments, (0.015, 0.02, 0.03, 0.92), 4.6
            )
            self._draw_lines(
                shader, guide_segments, (0.82, 0.91, 1.0, 1.0), 2.5
            )
            self._draw_lines(
                shader,
                selected_guide_segments,
                (0.015, 0.02, 0.03, 0.96),
                7.0,
            )
            self._draw_lines(
                shader,
                selected_guide_segments,
                (0.70, 1.0, 0.02, 1.0),
                4.0,
            )
            self._draw_lines(
                shader, boundary_segments, (0.95, 0.95, 1.0, 1.0), 2.5
            )
            self._draw_lines(
                shader, stroke_segments, (0.015, 0.02, 0.03, 0.92), 5.4
            )
            self._draw_lines(
                shader, stroke_segments, (0.70, 1.0, 0.02, 1.0), 3.0
            )
            self._draw_lines(
                shader,
                self.custom_segments,
                (0.72, 1.0, 0.06, 1.0),
                3.0,
            )
            self._draw_points(
                shader,
                self.control_points,
                (0.62, 0.78, 1.0, 0.9),
                5.0,
            )
            self._draw_points(
                shader,
                self.guide_endpoints,
                (0.015, 0.02, 0.03, 0.95),
                10.0,
            )
            self._draw_points(
                shader,
                self.guide_endpoints,
                (0.96, 0.98, 1.0, 1.0),
                7.0,
            )
            self._draw_points(
                shader,
                self.node_points,
                (0.95, 0.82, 0.12, 1.0),
                9.0,
            )
            self._draw_points(
                shader,
                self.edge_cut_points,
                (1.0, 0.25, 0.08, 1.0),
                10.0,
            )
            snap_color = (
                (0.10, 0.86, 1.0, 1.0)
                if self.snap_kind == "GUIDE_EDGE"
                else (0.70, 1.0, 0.02, 1.0)
            )
            self._draw_lines(
                shader,
                self.hover_segments,
                (0.015, 0.02, 0.03, 1.0),
                8.0,
            )
            self._draw_lines(
                shader,
                self.hover_segments,
                snap_color,
                4.8,
            )
            self._draw_points(
                shader,
                self.snap_points,
                (0.015, 0.02, 0.03, 1.0),
                18.0,
            )
            self._draw_points(
                shader,
                self.snap_points,
                snap_color,
                13.0,
            )
            self._draw_points(
                shader,
                self.snap_points,
                (0.98, 1.0, 1.0, 1.0),
                4.0,
            )
        except Exception:
            # Drawing must never take down the modeling session.
            return
        finally:
            try:
                gpu.state.line_width_set(1.0)
                gpu.state.point_size_set(1.0)
                gpu.state.depth_test_set("NONE")
                gpu.state.blend_set("NONE")
            except Exception:
                pass
