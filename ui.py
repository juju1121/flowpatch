from bpy.types import Panel

from .build_identity import compact_build_label
from .project_store import find_object_by_uuid
from .project_store import ProjectStoreError
from .project_store import read_composite_session
from .project_store import read_project_record


def _edit_mesh_ready(context):
    return (
        context.mode == "EDIT_MESH"
        and context.edit_object is not None
        and context.edit_object.type == "MESH"
    )


def _session_ready(context):
    if not _edit_mesh_ready(context):
        return False
    settings = context.scene.flowpatch_retopo
    return (
        settings.target is not None
        and settings.target.type == "MESH"
        and settings.target is not context.edit_object
    )


def _project_target(context, owner):
    if owner is None:
        return None
    try:
        record = read_project_record(owner)
        if record is not None:
            return find_object_by_uuid(
                tuple(context.blend_data.objects),
                record.target_object_uuid,
            )
    except ProjectStoreError:
        return None
    legacy_name = str(owner.get("flowpatch_target_name", ""))
    return context.blend_data.objects.get(legacy_name) if legacy_name else None


class VIEW3D_PT_flowpatch_retopo(Panel):
    bl_label = "FlowPatch Retopo"
    bl_idname = "VIEW3D_PT_flowpatch_retopo"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "FlowPatch"

    def draw(self, context):
        layout = self.layout
        settings = context.scene.flowpatch_retopo
        active = context.active_object

        title = layout.box()
        title.label(text="FLOWPATCH  V1", icon="MESH_GRID")
        title.label(text=compact_build_label(), icon="INFO")
        title.operator(
            "flowpatch.copy_debug_state",
            text="Copy Debug State",
        )

        if not _edit_mesh_ready(context):
            setup = layout.box()
            setup.label(text="Create Retopo", icon="ADD")
            setup.prop(settings, "target", text="Select Target")
            setup.prop(settings, "continue_on", text="Continue On")

            chosen_surface = settings.target
            project_object = settings.continue_on
            if (
                project_object is None
                and active is not None
                and active.type == "MESH"
                and (
                    active.get("flowpatch_project_record_v1", "")
                    or active.get("flowpatch_target_name", "")
                )
            ):
                project_object = active
            project_surface = _project_target(context, project_object)
            if project_surface is not None:
                chosen_surface = project_surface
            if (
                chosen_surface is None
                and active is not None
                and active.type == "MESH"
                and len(active.data.polygons) > 0
                and active is not settings.continue_on
            ):
                chosen_surface = active
            attach_active = (
                project_object is None
                and active is not None
                and active.type == "MESH"
                and active is not chosen_surface
                and settings.target is chosen_surface
            )

            button = setup.column(align=True)
            button.scale_y = 1.45
            button.enabled = (
                chosen_surface is not None
                and chosen_surface.type == "MESH"
                and len(chosen_surface.data.polygons) > 0
                and chosen_surface is not settings.continue_on
            )
            button.operator(
                "flowpatch.toggle_tool",
                text=(
                    "Resume FlowPatch Project  [F7]"
                    if project_object is not None
                    else "Attach Active Mesh + Draw  [F7]"
                    if attach_active
                    else "Create Retopo + Draw  [F7]"
                ),
                icon="PLAY",
            )
            setup.label(
                text=(
                    "Project UUID resumes guides on the same mesh"
                    if project_object is not None
                    else "Keeps the active mesh and attaches project metadata"
                    if attach_active
                    else "Creates a separate editable mesh"
                )
            )
            if project_object is not None:
                project = setup.box()
                project_uuid = str(
                    project_object.get("flowpatch_project_uuid_v1", "")
                )
                project.label(
                    text=(
                        f"Project {project_uuid[:8]}"
                        if project_uuid
                        else "Legacy/orphan project metadata"
                    ),
                    icon="LINKED",
                )
                row = project.row(align=True)
                row.operator(
                    "flowpatch.recover_project",
                    text="Recover Metadata",
                    icon="FILE_REFRESH",
                )
                delete = project.row()
                delete.alert = True
                delete.operator(
                    "flowpatch.delete_project",
                    text="Delete Project Metadata",
                    icon="TRASH",
                )
            composite_box = layout.box()
            try:
                composite = read_composite_session(context.scene)
            except ProjectStoreError as exc:
                composite = None
                composite_box.alert = True
                composite_box.label(text=str(exc), icon="ERROR")
            composite_box.label(
                text=(
                    f"Composite: {len(composite.members)} Projects"
                    if composite is not None
                    else "Composite Session"
                ),
                icon="GROUP",
            )
            composite_box.operator(
                "flowpatch.create_composite_session",
                text="Create / Update from Selected",
                icon="LINKED",
            )
            if composite is not None:
                composite_box.operator(
                    "flowpatch.clear_composite_session",
                    text="Clear Composite Link",
                    icon="UNLINKED",
                )
            if settings.session_composite_message:
                composite_box.label(
                    text=settings.session_composite_message,
                    icon="ERROR",
                )
            return

        edit_object = context.edit_object
        if settings.target is edit_object:
            warning = layout.box()
            warning.alert = True
            warning.label(text="Target and retopo must differ", icon="ERROR")
            warning.label(text="Return to Object Mode and choose the target.")
            return

        session = layout.box()
        row = session.row(align=True)
        row.label(
            text="Live Session" if settings.session_active else "Ready",
            icon="CHECKMARK" if settings.session_active else "MESH_GRID",
        )
        row.operator(
            (
                "flowpatch.stop_session"
                if settings.session_active
                else "flowpatch.guide_session"
            ),
            text="Stop" if settings.session_active else "Start",
            icon="CANCEL" if settings.session_active else "PLAY",
        )
        surface = session.row()
        surface.enabled = not settings.session_active
        surface.prop(settings, "target", text="Surface")
        if settings.session_active:
            session.label(
                text="Surface is locked while drawing. Stop to change it.",
                icon="LOCKED",
            )
        project_uuid = str(
            edit_object.get("flowpatch_project_uuid_v1", "")
        )
        if project_uuid:
            session.label(
                text=f"Project UUID: {project_uuid[:8]}",
                icon="LINKED",
            )
        if settings.session_composite_state:
            session.label(
                text=f"Composite: {settings.session_composite_state}",
                icon="GROUP",
            )
        if settings.session_active:
            row = session.row(align=True)
            row.label(text=f"{settings.session_guide_count} Guides")
            row.label(text=f"{settings.session_cell_count} Cells")
            boundary = session.row(align=True)
            boundary.operator(
                "flowpatch.capture_boundary",
                text="Continue Selected Boundary",
                icon="EDGESEL",
            )
            boundary.prop(settings, "mirror_x", text="Mirror X")
            sync = session.box()
            sync.label(
                text=settings.session_sync_state or "0 Synchronized Regions",
                icon="UV_SYNC_SELECT",
            )
            sync_actions = sync.row(align=True)
            sync_actions.operator(
                "flowpatch.sync_mesh_from_guides",
                text="Guides to Mesh",
                icon="FILE_REFRESH",
            )
            sync_actions.operator(
                "flowpatch.sync_guides_from_mesh",
                text="Mesh to Guides",
                icon="TRACKING",
            )
            if settings.session_sync_message:
                frozen = sync.row()
                frozen.alert = True
                frozen.label(
                    text=settings.session_sync_message,
                    icon="ERROR",
                )
                sync.operator(
                    "flowpatch.detach_frozen_sync",
                    text="Detach Frozen Regions",
                    icon="UNLINKED",
                )
            existing_mesh = session.box()
            existing_mesh.label(text="Existing Mesh", icon="MESH_DATA")
            existing_mesh.operator(
                "flowpatch.adopt_selected_quad_islands",
                text="Adopt Selected Quad Islands",
                icon="FACESEL",
            )
            existing_mesh.operator(
                "flowpatch.bridge_selected_boundaries",
                text="Bridge Equal Boundaries",
                icon="MOD_WIREFRAME",
            )
            existing_mesh.label(
                text="Bridge requires two closed loops with equal counts."
            )
        maintenance = session.row(align=True)
        maintenance.operator(
            "flowpatch.recover_project",
            text="Validate / Recover",
            icon="FILE_REFRESH",
        )
        maintenance.alert = True
        maintenance.operator(
            "flowpatch.delete_project",
            text="Delete Project",
            icon="TRASH",
        )
        if settings.session_validation:
            validation = session.row()
            validation.alert = True
            validation.label(
                text=settings.session_validation,
                icon="ERROR",
            )

        patch = layout.box()
        patch.label(text="Patch", icon="MESH_GRID")
        row = patch.row(align=True)
        row.prop(settings, "u_segments")
        row.prop(settings, "v_segments")
        patch.prop(settings, "projection_mode", expand=True)
        patch.prop(settings, "surface_offset")
        if not _session_ready(context):
            patch.label(text="Choose a different Surface.", icon="ERROR")


class VIEW3D_PT_flowpatch_refine(Panel):
    bl_label = "Refine Patch"
    bl_idname = "VIEW3D_PT_flowpatch_refine"
    bl_parent_id = "VIEW3D_PT_flowpatch_retopo"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "FlowPatch"
    bl_options = {"DEFAULT_CLOSED"}

    @classmethod
    def poll(cls, context):
        return _session_ready(context)

    def draw(self, context):
        layout = self.layout
        settings = context.scene.flowpatch_retopo

        selection = layout.row(align=True)
        selection.operator(
            "flowpatch.select_patch",
            text="Select Patch",
            icon="RESTRICT_SELECT_OFF",
        )
        selection.operator(
            "flowpatch.select_last_patch",
            text="Select Last",
        )

        harden = layout.box()
        harden.label(text="Flatten / Harden")
        harden.prop(settings, "flatten_strength", text="Strength")
        harden.operator(
            "flowpatch.flatten_patch",
            text="Harden Selected Patch",
            icon="MODIFIER",
        )

        loop_cut = layout.box()
        loop_cut.label(text="Loop Cut")
        loop_cut.prop(settings, "loop_cuts", text="Cuts")
        if settings.loop_cuts == 1:
            loop_cut.prop(settings, "loop_slide", text="Initial Slide")
        loop_cut.operator(
            "flowpatch.loop_cut_patch",
            text="Start Patch Loop Cut",
            icon="MODIFIER",
        )


class VIEW3D_PT_flowpatch_density(Panel):
    bl_label = "Density"
    bl_idname = "VIEW3D_PT_flowpatch_density"
    bl_parent_id = "VIEW3D_PT_flowpatch_retopo"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "FlowPatch"
    bl_options = {"DEFAULT_CLOSED"}

    @classmethod
    def poll(cls, context):
        return _session_ready(context)

    def draw(self, context):
        layout = self.layout
        settings = context.scene.flowpatch_retopo
        row = layout.row(align=True)
        row.prop(settings, "u_segments")
        row.prop(settings, "v_segments")

        painted = layout.box()
        painted.label(text="Optional Painted Density", icon="WPAINT_HLT")
        painted.prop(settings, "density_mode", text="")
        if settings.density_mode == "TARGET_GROUP":
            painted.prop(settings, "density_group")
            row = painted.row(align=True)
            row.prop(settings, "density_min_rows", text="Low")
            row.prop(settings, "density_max_rows", text="High")
        layout.label(text=f"Last generated rows: {settings.last_effective_rows}")


class VIEW3D_PT_flowpatch_advanced(Panel):
    bl_label = "Advanced"
    bl_idname = "VIEW3D_PT_flowpatch_advanced"
    bl_parent_id = "VIEW3D_PT_flowpatch_retopo"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "FlowPatch"
    bl_options = {"DEFAULT_CLOSED"}

    @classmethod
    def poll(cls, context):
        return _session_ready(context)

    def draw(self, context):
        layout = self.layout
        settings = context.scene.flowpatch_retopo
        layout.prop(settings, "surface_offset")
        layout.prop(settings, "surface_follow_strength")
        layout.prop(settings, "surface_tighten_strength")
        layout.prop(settings, "surface_smoothing_radius")
        layout.prop(settings, "detail_ignore_threshold")
        layout.separator()
        layout.label(text="Surface Continuity")
        layout.prop(settings, "frontface_epsilon")
        layout.prop(settings, "max_projection_distance")
        layout.prop(settings, "max_surface_step")
        layout.prop(settings, "normal_continuity_cos")
        layout.prop(settings, "guide_fair_strength")
        layout.prop(settings, "guide_fair_iterations")
        layout.prop(settings, "flat_target_tolerance")
        layout.prop(settings, "magnet_distance")
        layout.prop(settings, "screen_snap_distance_px")
        layout.prop(settings, "sample_spacing_px")
        layout.prop(settings, "stroke_stabilization")
        layout.prop(settings, "preview_opacity")
        layout.prop(settings, "show_control_points")


_CLASSES = (
    VIEW3D_PT_flowpatch_retopo,
    VIEW3D_PT_flowpatch_refine,
    VIEW3D_PT_flowpatch_density,
    VIEW3D_PT_flowpatch_advanced,
)


def register():
    import bpy

    for cls in _CLASSES:
        bpy.utils.register_class(cls)


def unregister():
    import bpy

    for cls in reversed(_CLASSES):
        bpy.utils.unregister_class(cls)
