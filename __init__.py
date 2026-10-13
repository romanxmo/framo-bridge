bl_info = {
    "name": "Framo Bridge",
    "author": "Roman Moor",
    "version": (0, 5, 0),
    "blender": (3, 0, 0),
    "location": "View3D > Sidebar > Framo Bridge",
    "description": "Export optimized GLB models directly to web applications with Draco compression, mesh decimation, and native texture scaling (no dependencies required)",
    "category": "Import-Export",
    "doc_url": "https://github.com/r0m4nm/framo-bridge",
    "tracker_url": "https://github.com/r0m4nm/framo-bridge/issues",
    "support": "COMMUNITY",
}

import bpy
from .core import properties, operators, dependencies
from .ui import panels, icons
from .services import preview_server, update_service
from .control import commands as control_commands
from .utils import logging_config
from .utils.thread_safety import set_control_enabled

classes = [
    properties.FramoBridgePreferences,
    properties.TextureExcludeMaterial,
    properties.SubdivExcludeObject,
    properties.SubdivIndividualOverride,
    properties.DecimateExcludeObject,
    properties.DecimateIndividualOverride,
    properties.MaterialExpandedState,
    properties.FramoExportSettings,
    
    operators.FRAMO_OT_export_to_web,
    operators.FRAMO_OT_reset_export_settings,
    operators.FRAMO_OT_analyze_materials,
    operators.FRAMO_OT_toggle_material_expanded,
    operators.FRAMO_OT_replace_material,
    operators.FRAMO_OT_replace_material_execute,
    operators.FRAMO_OT_open_material_in_shading,
    operators.FRAMO_OT_add_excluded_material,
    operators.FRAMO_OT_remove_excluded_material,
    operators.FRAMO_OT_add_excluded_subdiv_object,
    operators.FRAMO_OT_remove_excluded_subdiv_object,
    operators.FRAMO_OT_toggle_subdiv_exclusion,
    operators.FRAMO_OT_add_individual_subdiv_override,
    operators.FRAMO_OT_remove_individual_subdiv_override,
    operators.FRAMO_OT_toggle_decimate_exclusion,
    operators.FRAMO_OT_add_individual_decimate_override,
    operators.FRAMO_OT_remove_individual_decimate_override,
    operators.FRAMO_OT_check_for_updates,
    operators.FRAMO_OT_install_update,
    operators.FRAMO_OT_download_update,
    operators.FRAMO_OT_view_changelog,
    
    panels.FRAMO_PT_export_panel
]

if hasattr(dependencies, 'FRAMO_OT_install_dependencies'):
    classes.append(dependencies.FRAMO_OT_install_dependencies)

def register():
    # Setup logging
    logger = logging_config.setup_logging()
    logger.info("Framo Bridge addon registering...")

    # Load icons
    icons.load_custom_icons()
    
    # Register classes
    for cls in classes:
        try:
            bpy.utils.register_class(cls)
        except ValueError as e:
            # If duplicate registration (can happen during development/hot-reload), 
            # try to unregister first then register again
            if "already registered" in str(e) or "is a subclass" in str(e):
                try:
                    bpy.utils.unregister_class(cls)
                    bpy.utils.register_class(cls)
                except Exception as e2:
                    print(f"Warning: Failed to re-register {cls.__name__}: {e2}")
            else:
                print(f"Error registering {cls.__name__}: {e}")
    
    # Register properties
    bpy.types.Scene.framo_export_settings = bpy.props.PointerProperty(type=properties.FramoExportSettings)
    # Session-only: no storage of its own (get/set), so never saved in a .blend
    bpy.types.WindowManager.framo_control_enabled = properties.framo_control_enabled_property()

    # Main-thread runner for commands Framo sends to /command
    control_commands.register()

    # Start server
    preview_server.start_server()
    
    logger.info("Framo Bridge registered successfully")
    print("Framo Bridge registered")

def unregister():
    # Stop server
    preview_server.stop_server()

    # Stop taking commands; anything still queued is answered with an error
    set_control_enabled(False)
    control_commands.unregister()

    # Unregister properties
    if hasattr(bpy.types.Scene, 'framo_export_settings'):
        del bpy.types.Scene.framo_export_settings
    if hasattr(bpy.types.WindowManager, 'framo_control_enabled'):
        del bpy.types.WindowManager.framo_control_enabled
    
    # Unregister classes
    for cls in reversed(classes):
        try:
            bpy.utils.unregister_class(cls)
        except Exception:
            pass
            
    # Unload icons
    icons.unregister_custom_icons()
    
    print("Framo Bridge unregistered")

if __name__ == "__main__":
    register()
