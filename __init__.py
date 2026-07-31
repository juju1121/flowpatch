bl_info = {
    "name": "FlowPatch Retopo",
    "author": "Independent clean-room implementation",
    "version": (1, 4, 13),
    "blender": (4, 3, 0),
    "location": "3D Viewport > FlowPatch panel or F7",
    "description": "Build editable quad patches from retained surface guides",
    "category": "Mesh",
}

from . import operators
from . import properties
from . import ui


def register():
    properties.register()
    operators.register()
    ui.register()


def unregister():
    ui.unregister()
    operators.unregister()
    properties.unregister()
