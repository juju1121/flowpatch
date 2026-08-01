import bpy
from bpy.props import BoolProperty
from bpy.props import EnumProperty
from bpy.props import FloatProperty
from bpy.props import IntProperty
from bpy.props import PointerProperty
from bpy.props import StringProperty
from bpy.types import PropertyGroup


def _poll_mesh_target(_self, obj):
    return obj is not None and obj.type == "MESH"


def _update_mirror(self, context):
    obj = context.edit_object or context.active_object
    if obj is None or obj.type != "MESH" or obj is self.target:
        return

    modifier = obj.modifiers.get("FlowPatch Mirror")
    if self.mirror_x:
        if modifier is None:
            modifier = obj.modifiers.new("FlowPatch Mirror", "MIRROR")
        modifier.use_axis[0] = True
        modifier.use_axis[1] = False
        modifier.use_axis[2] = False
        modifier.use_clip = True
        modifier.use_mirror_merge = True
        modifier.merge_threshold = 0.001
        modifier.show_in_editmode = True
    elif modifier is not None and modifier.type == "MIRROR":
        obj.modifiers.remove(modifier)


class FLOWPATCH_PG_settings(PropertyGroup):
    target: PointerProperty(
        name="Surface",
        description="High-resolution mesh used for ray projection",
        type=bpy.types.Object,
        poll=_poll_mesh_target,
    )
    continue_on: PointerProperty(
        name="Continue On",
        description="Optional existing retopology mesh to continue editing",
        type=bpy.types.Object,
        poll=_poll_mesh_target,
    )
    model_mode: EnumProperty(
        name="Model",
        description="Default surface response for newly generated patches",
        items=(
            (
                "ORGANIC",
                "Organic",
                "Follow broad organic form while preserving a smooth quad flow",
            ),
            (
                "HARD_SURFACE",
                "Hard Surface",
                "Favor straighter interpolation and leave small details for baking",
            ),
        ),
        default="ORGANIC",
    )
    path_follow_mode: EnumProperty(
        name="Follow",
        description="How newly generated paths respond to small surface details",
        items=(
            (
                "SURFACE",
                "Surface",
                "Project generated rails onto the target surface",
            ),
            (
                "DRAWING",
                "Drawing",
                "Preserve the artist-drawn interpolation across small surface detail",
            ),
        ),
        default="SURFACE",
    )
    mirror_x: BoolProperty(
        name="Mirror X",
        description="Show a non-destructive clipped X mirror while retopologizing",
        default=False,
        update=_update_mirror,
    )
    show_control_points: BoolProperty(
        name="Control Points",
        description="Show internal sampled guide controls while editing guides",
        default=False,
    )
    rows: IntProperty(
        name="Rows",
        description="Legacy strip row count; not used by the guide-cell workflow",
        default=1,
        min=1,
        max=32,
    )
    u_segments: IntProperty(
        name="U",
        description="Quad columns inside each valid guide cell",
        default=4,
        min=1,
        max=64,
    )
    v_segments: IntProperty(
        name="V",
        description="Quad rows inside each valid guide cell",
        default=4,
        min=1,
        max=64,
    )
    projection_mode: EnumProperty(
        name="Projection",
        description=(
            "How new retained guides and quad-cell interiors follow target detail"
        ),
        items=(
            (
                "RAW",
                "Raw",
                "Project every interior grid point to the target surface",
            ),
            (
                "SMOOTH",
                "Smoothed",
                (
                    "Filter small target bumps from guides and blend the broad "
                    "Coons surface with target projection"
                ),
            ),
            (
                "FLATTENED",
                "Flattened",
                (
                    "Keep broad guide and patch interpolation and leave small "
                    "detail for baking"
                ),
            ),
        ),
        default="RAW",
    )
    surface_follow_strength: FloatProperty(
        name="Follow Strength",
        description=(
            "How strongly a smoothed patch follows meaningful target form"
        ),
        default=0.65,
        min=0.0,
        max=1.0,
        subtype="FACTOR",
    )
    surface_tighten_strength: FloatProperty(
        name="Tighten Strength",
        description=(
            "Pull the filtered projection toward the broad Coons patch so "
            "small dents and protrusions can bake into a normal map"
        ),
        default=0.35,
        min=0.0,
        max=1.0,
        subtype="FACTOR",
    )
    surface_smoothing_radius: IntProperty(
        name="Smoothing Radius",
        description=(
            "Grid-neighborhood radius used to estimate the broad target form"
        ),
        default=1,
        min=1,
        max=4,
    )
    detail_ignore_threshold: FloatProperty(
        name="Detail Ignore",
        description=(
            "World-space projected detail smaller than this is filtered from "
            "the low-poly patch"
        ),
        default=0.01,
        min=0.0,
        soft_max=0.1,
        precision=4,
        subtype="DISTANCE",
        unit="LENGTH",
    )
    frontface_epsilon: FloatProperty(
        name="Front-Face Epsilon",
        description="Reject pointer hits that face away from the current view",
        default=0.001,
        min=0.0,
        soft_max=0.1,
        precision=4,
    )
    max_projection_distance: FloatProperty(
        name="Max Projection Distance",
        description=(
            "Maximum candidate-to-surface distance; zero uses a target-size "
            "adaptive limit"
        ),
        default=0.0,
        min=0.0,
        soft_max=1.0,
        precision=4,
        subtype="DISTANCE",
        unit="LENGTH",
    )
    max_surface_step: FloatProperty(
        name="Max Surface Step",
        description=(
            "Maximum same-side anchor movement per preview update; zero uses "
            "a target-size adaptive limit"
        ),
        default=0.0,
        min=0.0,
        soft_max=1.0,
        precision=4,
        subtype="DISTANCE",
        unit="LENGTH",
    )
    normal_continuity_cos: FloatProperty(
        name="Normal Continuity",
        description=(
            "Minimum normal cosine for same-side continuity; the default "
            "allows a ninety-degree hard corner but rejects an opposite side"
        ),
        default=-0.05,
        min=-1.0,
        max=1.0,
        precision=3,
    )
    guide_fair_strength: FloatProperty(
        name="Guide Fair Strength",
        description="Tangential guide fairing strength per iteration",
        default=0.35,
        min=0.0,
        max=1.0,
        subtype="FACTOR",
    )
    guide_fair_iterations: IntProperty(
        name="Guide Fair Iterations",
        description="Number of tangent-fair and same-side reprojection passes",
        default=2,
        min=1,
        max=12,
    )
    flat_target_tolerance: FloatProperty(
        name="Flat Target Tolerance",
        description="Maximum permitted patch deviation when its boundary is planar",
        default=0.0005,
        min=0.000001,
        soft_max=0.01,
        precision=6,
        subtype="DISTANCE",
        unit="LENGTH",
    )
    surface_offset: FloatProperty(
        name="Offset",
        description="World-space distance applied along the target normal",
        default=0.0,
        soft_min=-0.05,
        soft_max=0.05,
        precision=4,
        subtype="DISTANCE",
        unit="LENGTH",
    )
    density_mode: EnumProperty(
        name="Density",
        description="How FlowPatch chooses the number of generated rows",
        items=(
            (
                "MANUAL",
                "Manual Rows",
                "Use the explicit Rows value",
            ),
            (
                "TARGET_GROUP",
                "Target Vertex Group",
                "Map the average painted target weight to a row range",
            ),
        ),
        default="MANUAL",
    )
    density_group: StringProperty(
        name="Group",
        description="Vertex group painted on the projection target",
        default="flowpatch_density",
    )
    density_min_rows: IntProperty(
        name="Minimum Rows",
        description="Rows generated where the sampled density weight is zero",
        default=1,
        min=1,
        max=32,
    )
    density_max_rows: IntProperty(
        name="Maximum Rows",
        description="Rows generated where the sampled density weight is one",
        default=6,
        min=1,
        max=32,
    )
    last_effective_rows: IntProperty(
        name="Effective Rows",
        description="Rows used by the most recent valid preview",
        default=0,
        min=0,
        options={"SKIP_SAVE"},
    )
    session_active: BoolProperty(
        name="Session Active",
        default=False,
        options={"SKIP_SAVE"},
    )
    session_tool: StringProperty(
        name="Active Tool",
        default="",
        options={"SKIP_SAVE"},
    )
    session_status: StringProperty(
        name="Status",
        default="Idle",
        options={"SKIP_SAVE"},
    )
    session_validation: StringProperty(
        name="Validation",
        default="",
        options={"SKIP_SAVE"},
    )
    session_sync_state: StringProperty(
        name="Mesh Sync",
        default="",
        options={"SKIP_SAVE"},
    )
    session_sync_message: StringProperty(
        name="Mesh Sync Message",
        default="",
        options={"SKIP_SAVE"},
    )
    session_composite_state: StringProperty(
        name="Composite Session",
        default="",
        options={"SKIP_SAVE"},
    )
    session_composite_message: StringProperty(
        name="Composite Session Message",
        default="",
        options={"SKIP_SAVE"},
    )
    session_guide_count: IntProperty(
        name="Guides",
        default=0,
        min=0,
        options={"SKIP_SAVE"},
    )
    session_cell_count: IntProperty(
        name="Ready Cells",
        default=0,
        min=0,
        options={"SKIP_SAVE"},
    )
    session_active_cell: IntProperty(
        name="Active Cell",
        default=0,
        min=0,
        options={"SKIP_SAVE"},
    )
    show_legacy_tools: BoolProperty(
        name="Show Legacy Strip Experiments",
        description=(
            "Developer-only access to the retired one-stroke strip experiments"
        ),
        default=False,
    )
    node_magnet: EnumProperty(
        name="Node Magnet",
        description="How the drawn rail connects to existing open vertices",
        items=(
            ("OFF", "Off", "Do not attract the generated rail to existing nodes"),
            (
                "SNAP",
                "Snap Loose",
                "Align to nearby loose vertices while keeping topology distinct",
            ),
            (
                "MERGE",
                "Merge Open",
                "Reuse nearby compatible open vertices and weld the new patch",
            ),
        ),
        default="OFF",
    )
    edge_magnet: EnumProperty(
        name="Edge Magnet",
        description="How rail endpoints connect to existing guide edges",
        items=(
            ("OFF", "Off", "Do not attract endpoints to existing edges"),
            (
                "SNAP",
                "Snap",
                "Snap endpoints to a nearby edge without changing its topology",
            ),
            (
                "CUT",
                "Cut Loose Edge",
                "Split nearby loose guide edges and reuse the inserted endpoint",
            ),
        ),
        default="OFF",
    )
    magnet_distance: FloatProperty(
        name="Magnet Radius",
        description="World-space radius used to find compatible nodes and edges",
        default=0.05,
        min=0.00001,
        soft_max=0.5,
        precision=4,
        subtype="DISTANCE",
        unit="LENGTH",
    )
    sample_spacing_px: FloatProperty(
        name="Stroke Spacing",
        description="Minimum screen-space distance between stroke samples",
        default=6.0,
        min=1.0,
        max=64.0,
        subtype="PIXEL",
    )
    screen_snap_distance_px: FloatProperty(
        name="Screen Snap Radius",
        description=(
            "Viewport pixel radius used to highlight and connect guide nodes "
            "or split an existing guide"
        ),
        default=18.0,
        min=4.0,
        max=96.0,
        subtype="PIXEL",
    )
    stroke_stabilization: FloatProperty(
        name="Stroke Stabilization",
        description=(
            "Smooth pointer jitter before a retained guide is projected"
        ),
        default=0.68,
        min=0.0,
        max=0.95,
        subtype="FACTOR",
    )
    preview_opacity: FloatProperty(
        name="Preview Opacity",
        description="Opacity of the uncommitted quad preview",
        default=0.22,
        min=0.02,
        max=0.8,
        subtype="FACTOR",
    )
    subdivide_levels: IntProperty(
        name="Levels",
        description="Number of midpoint loop-subdivision passes for one patch",
        default=1,
        min=1,
        max=3,
    )
    loop_cuts: IntProperty(
        name="Cuts",
        description="Number of evenly spaced row loops inserted into a patch",
        default=1,
        min=1,
        max=64,
    )
    loop_slide: FloatProperty(
        name="Slide",
        description="Offset a single inserted row loop toward either neighboring rail",
        default=0.0,
        min=-0.95,
        max=0.95,
        subtype="FACTOR",
    )
    flatten_strength: FloatProperty(
        name="Harden Strength",
        description="Move patch interior toward its boundary plane",
        default=1.0,
        min=0.0,
        max=1.0,
        subtype="FACTOR",
    )
    last_patch_id: IntProperty(
        name="Last Patch",
        description="Identifier of the most recently committed patch",
        default=0,
        min=0,
        options={"SKIP_SAVE"},
    )


_CLASSES = (FLOWPATCH_PG_settings,)


def register():
    for cls in _CLASSES:
        bpy.utils.register_class(cls)
    bpy.types.Scene.flowpatch_retopo = PointerProperty(type=FLOWPATCH_PG_settings)


def unregister():
    if hasattr(bpy.types.Scene, "flowpatch_retopo"):
        del bpy.types.Scene.flowpatch_retopo
    for cls in reversed(_CLASSES):
        bpy.utils.unregister_class(cls)
