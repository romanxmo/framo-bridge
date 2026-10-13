import bpy
from bpy.props import BoolProperty, IntProperty, EnumProperty, FloatProperty, StringProperty, CollectionProperty
from bpy.types import PropertyGroup, AddonPreferences
from ..utils.thread_safety import get_update_state_copy, is_control_enabled, set_control_enabled
from ..utils.constants import ADDON_VERSION
from ..utils.logging_config import get_logger

log = get_logger()

def update_compression_preset(self, context):
    settings = context.scene.framo_export_settings
    
    if settings.compression_preset == 'NONE':
        settings.use_draco = False
    elif settings.compression_preset == 'LOW':
        settings.use_draco = True
        settings.draco_compression_level = 3
        settings.draco_quantization_position = 16
        settings.draco_quantization_normal = 12
        settings.draco_quantization_texcoord = 14
    elif settings.compression_preset == 'MEDIUM':
        settings.use_draco = True
        settings.draco_compression_level = 6
        settings.draco_quantization_position = 14
        settings.draco_quantization_normal = 10
        settings.draco_quantization_texcoord = 12
    elif settings.compression_preset == 'HIGH':
        settings.use_draco = True
        settings.draco_compression_level = 10
        settings.draco_quantization_position = 11
        settings.draco_quantization_normal = 8
        settings.draco_quantization_texcoord = 10

def update_export_status(context, status_text):
    """Update export status and force UI refresh"""
    settings = context.scene.framo_export_settings
    settings.export_status = status_text
    
    # Force UI refresh
    for area in context.screen.areas:
        if area.type == 'VIEW_3D':
            area.tag_redraw()
    
    # Force Blender to process events so UI updates immediately
    bpy.ops.wm.redraw_timer(type='DRAW_WIN_SWAP', iterations=1)

def clear_export_status(context=None):
    """Clear export status - used with timer. Context parameter is optional for backwards compatibility.

    Note: Timer callbacks run in a restricted context where writing to ID classes
    (like Scene properties) is not always allowed. This is a known Blender limitation.
    The status will clear on next user interaction if the timer fails.
    """
    try:
        # Try to access scene through window_manager
        wm = bpy.context.window_manager
        if wm and wm.windows:
            for window in wm.windows:
                if window.screen and window.screen.scene:
                    scene = window.screen.scene
                    if hasattr(scene, 'framo_export_settings'):
                        settings = scene.framo_export_settings
                        settings.export_status = ""
                        break

            # Force UI refresh
            for window in wm.windows:
                for area in window.screen.areas:
                    if area.type == 'VIEW_3D':
                        area.tag_redraw()
    except Exception:
        # Timer context restrictions - status will clear on next interaction
        pass

    return None  # Don't repeat the timer

def framo_control_enabled_property():
    """"Allow Framo to control Blender", on the WindowManager.

    Backed by a module global through get/set, so the property stores nothing:
    it is off on every Blender start, never saved into a .blend, and readable
    from the HTTP thread without touching bpy.
    """
    return BoolProperty(
        name="Allow Framo to control Blender",
        description=(
            "Let a Framo agent drive this Blender: read the scene, build, render, and run "
            "Python with the full Blender API, which can change anything in this file and "
            "read or write files on this computer. Each step can be undone with Ctrl+Z. "
            "Off again every time Blender starts"
        ),
        get=lambda self: is_control_enabled(),
        set=lambda self, value: set_control_enabled(value),
    )

class MaterialExpandedState(PropertyGroup):
    """Property group to track expanded state of materials in the UI"""
    expanded: BoolProperty(
        name="Expanded",
        description="Whether this material's details are expanded",
        default=False
    )

class TextureExcludeMaterial(PropertyGroup):
    """Property group to store material names to exclude from texture optimization"""
    material_name: StringProperty(
        name="Material Name",
        description="Name of the material to exclude from texture optimization",
        default=""
    )

class SubdivExcludeObject(PropertyGroup):
    """Property group to store object names to exclude from subdivision override"""
    object_name: StringProperty(
        name="Object Name",
        description="Name of the object to exclude from subdivision override",
        default=""
    )

class SubdivIndividualOverride(PropertyGroup):
    """Property group to store individual subdivision override levels per object"""
    object_name: StringProperty(
        name="Object Name",
        description="Name of the object with individual subdivision override",
        default=""
    )
    
    override_level: IntProperty(
        name="Override Level",
        description="Individual subdivision level override for this object",
        default=3,
        min=0,
        max=6
    )

class DecimateExcludeObject(PropertyGroup):
    """Property group to store object names to exclude from decimation"""
    object_name: StringProperty(
        name="Object Name",
        description="Name of the object to exclude from decimation",
        default=""
    )

class DecimateIndividualOverride(PropertyGroup):
    """Property group to store individual decimation ratios per object"""
    object_name: StringProperty(
        name="Object Name",
        description="Name of the object with individual decimation override",
        default=""
    )
    
    override_ratio: FloatProperty(
        name="Override Ratio",
        description="Individual decimation ratio override for this object",
        default=0.1,
        min=0.01,
        max=1.0,
        precision=2
    )

class FramoExportSettings(PropertyGroup):
    # Compression Settings
    # NOTE: Default values match 'MEDIUM' preset since that's the default compression_preset
    use_draco: BoolProperty(
        name="Use Draco Compression",
        description="Enable Draco mesh compression for smaller file sizes",
        default=True  # MEDIUM preset enables Draco
    )

    draco_compression_level: IntProperty(
        name="Compression Level",
        description="Draco compression level (0=least compression, 10=most compression)",
        default=6,  # MEDIUM preset value
        min=0,
        max=10
    )

    draco_quantization_position: IntProperty(
        name="Position Quantization",
        description="Quantization bits for position values (higher=better quality)",
        default=14,  # MEDIUM preset value
        min=8,
        max=16
    )

    draco_quantization_normal: IntProperty(
        name="Normal Quantization",
        description="Quantization bits for normal values (higher=better quality)",
        default=10,  # MEDIUM preset value
        min=8,
        max=16
    )

    draco_quantization_texcoord: IntProperty(
        name="Texture Coord Quantization",
        description="Quantization bits for texture coordinates (higher=better quality)",
        default=12,  # MEDIUM preset value
        min=8,
        max=16
    )
    
    compression_preset: EnumProperty(
        name="Compression Preset",
        description="Quick compression presets",
        items=[
            ('NONE', "No Compression", "No compression, highest quality"),
            ('LOW', "Low Compression", "Minimal compression, high quality"),
            ('MEDIUM', "Medium Compression", "Balanced compression and quality"),
            ('HIGH', "High Compression", "Maximum compression, lower quality"),
            ('CUSTOM', "Custom", "Use custom compression settings"),
        ],
        default='MEDIUM',
        update=lambda self, context: update_compression_preset(self, context)
    )
    
    # Mesh Decimation Settings
    enable_decimation: BoolProperty(
        name="Enable Decimation",
        description="Reduce polygon count before export using decimate modifier",
        default=False
    )
    
    decimate_ratio: FloatProperty(
        name="Decimate Ratio",
        description="Ratio of faces to keep (1.0 = no reduction, 0.1 = 90% reduction)",
        default=0.1,
        min=0.01,
        max=1.0,
        precision=2
    )
    
    decimate_type: EnumProperty(
        name="Decimate Type",
        description="Type of decimation algorithm to use",
        items=[
            ('COLLAPSE', "Collapse", "Edge collapse decimation - best for most cases"),
            ('UNSUBDIV', "Un-Subdivide", "Remove subdivision levels - good for over-subdivided models"),
            ('DISSOLVE', "Planar", "Remove planar faces - good for architectural models"),
        ],
        default='COLLAPSE'
    )
    
    preserve_sharp_edges: BoolProperty(
        name="Preserve Sharp Edges",
        description="Try to preserve sharp edges during decimation",
        default=True
    )
    
    preserve_uv_seams: BoolProperty(
        name="Preserve UV Seams",
        description="Try to preserve UV seams during decimation",
        default=True
    )
    
    # UV Unwrapping Settings
    enable_auto_uv: BoolProperty(
        name="Auto UV Unwrap (if no UV map present)",
        description="Automatically unwrap meshes that don't have UV maps using Smart UV Project",
        default=True
    )

    enable_uv_atlasing: BoolProperty(
        name="Material-Based UV Atlasing",
        description="Group objects with the same material into shared UV atlases to reduce draw calls",
        default=True
    )

    atlas_min_objects: IntProperty(
        name="Min Objects for Atlas",
        description="Minimum number of objects required to create a UV atlas (objects below this are unwrapped individually)",
        default=2,
        min=2,
        max=10
    )

    atlas_texture_size: IntProperty(
        name="Atlas Texture Size",
        description="Target texture resolution for UV atlas packing (in pixels)",
        default=1024,
        min=512,
        max=4096
    )

    atlas_margin: FloatProperty(
        name="Atlas UV Margin",
        description="Margin between UV islands in atlas (prevents texture bleeding)",
        default=0.05,
        min=0.0,
        max=0.2,
        precision=3
    )

    adaptive_decimation: BoolProperty(
        name="Adaptive Decimation",
        description="Adjust decimation ratio based on object complexity",
        default=False
    )
    
    # Subdivision Override Settings
    enable_subdiv_override: BoolProperty(
        name="Override Subdivision Level",
        description="Temporarily set all subdivision modifiers to a specific level during export",
        default=True
    )
    
    subdiv_override_level: IntProperty(
        name="Subdivision Level",
        description="Subdivision level to use during export (0 = no subdivision, higher = more detail)",
        default=2,
        min=0,
        max=4
    )
    
    subdiv_exclude_objects: CollectionProperty(
        type=SubdivExcludeObject,
        name="Excluded Objects",
        description="Objects to exclude from subdivision override"
    )
    
    subdiv_dropdown_expanded: BoolProperty(
        name="Show Affected Objects",
        description="Expand to show all objects affected by subdivision override",
        default=False
    )
    
    subdiv_individual_overrides: CollectionProperty(
        type=SubdivIndividualOverride,
        name="Individual Overrides",
        description="Individual subdivision level overrides per object"
    )
    
    subdiv_exclude_objects_index: IntProperty(
        name="Excluded Objects Index",
        default=0
    )
    
    # Decimation Exclusion and Individual Override Settings
    decimate_exclude_objects: CollectionProperty(
        type=DecimateExcludeObject,
        name="Excluded Objects",
        description="Objects to exclude from decimation"
    )
    
    decimate_dropdown_expanded: BoolProperty(
        name="Show Affected Objects",
        description="Expand to show all objects affected by decimation",
        default=False
    )
    
    decimate_individual_overrides: CollectionProperty(
        type=DecimateIndividualOverride,
        name="Individual Overrides",
        description="Individual decimation ratio overrides per object"
    )
    
    # Texture Optimization Settings
    enable_texture_optimization: BoolProperty(
        name="Optimize Textures",
        description="Scale down textures and convert to JPEG for smaller file sizes",
        default=True
    )
    
    texture_max_size: EnumProperty(
        name="Max Texture Size",
        description="Maximum texture dimension (longest side will be scaled to this size)",
        items=[
            ('2048', "2K (2048px)", "Maximum texture size of 2048 pixels"),
            ('1024', "1K (1024px)", "Maximum texture size of 1024 pixels (recommended)"),
            ('512', "512px", "Maximum texture size of 512 pixels"),
            ('256', "256px", "Maximum texture size of 256 pixels"),
        ],
        default='1024'
    )
    
    texture_exclude_materials: CollectionProperty(
        type=TextureExcludeMaterial,
        name="Excluded Materials",
        description="Materials to exclude from texture optimization"
    )
    
    texture_exclude_materials_index: IntProperty(
        name="Excluded Materials Index",
        default=0
    )
    
    # Export status for loading indicator
    export_status: StringProperty(
        name="Export Status",
        description="Current export operation status",
        default=""
    )
    
    is_exporting: BoolProperty(
        name="Is Exporting",
        description="Whether an export operation is currently in progress",
        default=False
    )

class FramoBridgePreferences(AddonPreferences):
    """Addon preferences for Framo Bridge"""
    bl_idname = "framo-bridge"
    
    def draw(self, context):
        update_state = get_update_state_copy()
        
        layout = self.layout

        box = layout.box()
        box.label(text="Update Settings", icon='IMPORT')

        row = box.row()
        op = row.operator("framo.check_for_updates", text="Check for Updates Now", icon='FILE_REFRESH')
        
        # Show update check status
        if update_state.get("checking"):
            row = box.row()
            row.label(text="Checking for updates...", icon='TIME')
        elif update_state.get("download_error"):
            row = box.row()
            row.alert = True
            error_msg = update_state["download_error"]
            # Truncate long error messages
            if len(error_msg) > 60:
                error_msg = error_msg[:57] + "..."
            row.label(text=f"Error: {error_msg}", icon='ERROR')
        elif update_state.get("pending_restart"):
            # Show success message after installation
            col = box.column(align=True)
            row = col.row()
            row.label(text="✓ Update installed!", icon='CHECKMARK')
            row = col.row()
            row.label(text="Restart Blender")
        elif update_state.get("last_check_time"):
            row = box.row()
            if update_state.get("update_available"):
                latest = update_state.get("latest_version")
                if latest:
                    version_str = f"{latest[0]}.{latest[1]}.{latest[2]}"
                    row.label(text=f"Update available: v{version_str}", icon='IMPORT')
                    # Add install button
                    row = box.row()
                    row.scale_y = 1.5
                    if update_state.get("downloading"):
                        row.enabled = False
                        progress = update_state.get("download_progress", 0.0)
                        row.label(text=f"Downloading... {int(progress * 100)}%", icon='TIME')
                    elif update_state.get("installing"):
                        row.enabled = False
                        row.label(text="Installing update...", icon='TIME')
                    else:
                        op = row.operator("framo.install_update", text="Install Update Now", icon='IMPORT')
            else:
                current = ADDON_VERSION
                version_str = f"{current[0]}.{current[1]}.{current[2]}"
                row.label(text=f"Up to date (v{version_str})", icon='CHECKMARK')
