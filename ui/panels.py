import bpy
from ..utils.thread_safety import get_update_state_copy, get_user_info_copy, safe_material_states_access, get_control_state_copy
from ..ui.icons import get_icon_id
from ..utils.constants import ADDON_VERSION
from ..processing import material_analyzer, texture_scaler

# Check availability of modules
try:
    from ..core import dependencies
    DEPENDENCIES_AVAILABLE = True
except ImportError:
    DEPENDENCIES_AVAILABLE = False

try:
    from ..processing import uv_unwrap
    UV_UNWRAP_AVAILABLE = True
except ImportError:
    UV_UNWRAP_AVAILABLE = False

try:
    from ..processing import uv_atlas
    UV_ATLAS_AVAILABLE = True
except ImportError:
    UV_ATLAS_AVAILABLE = False

try:
    from ..processing import texture_scaler
    TEXTURE_SCALER_AVAILABLE = True
except ImportError:
    TEXTURE_SCALER_AVAILABLE = False

MATERIAL_ANALYZER_AVAILABLE = True

class FRAMO_PT_export_panel(bpy.types.Panel):
    bl_label = f"Framo Bridge v{ADDON_VERSION[0]}.{ADDON_VERSION[1]}.{ADDON_VERSION[2]}"
    bl_idname = "FRAMO_PT_export_panel"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "Framo Bridge"

    def draw(self, context):
        update_state = get_update_state_copy()
        framo_user_info = get_user_info_copy()
        
        layout = self.layout

        if not hasattr(context.scene, 'framo_export_settings'):
            layout.label(text="Error: Settings not initialized.", icon='ERROR')
            return

        settings = context.scene.framo_export_settings

        # Update System
        if update_state.get("pending_restart"):
            update_box = layout.box()
            update_box.alert = False
            row = update_box.row()
            row.scale_y = 1.3
            row.label(text="✓ Update installed! Restart Blender.", icon='CHECKMARK')
            layout.separator()
        elif update_state.get("update_available"):
            latest = update_state.get("latest_version")
            if latest:
                version_str = f"{latest[0]}.{latest[1]}.{latest[2]}"
                update_box = layout.box()
                update_box.alert = True
                row = update_box.row()
                row.label(text=f"Update Available: v{version_str}", icon='IMPORT')
                
                if update_state.get("downloading"):
                    row = update_box.row()
                    progress = update_state.get("download_progress", 0.0)
                    row.label(text=f"Downloading... {int(progress * 100)}%", icon='TIME')
                elif update_state.get("installing"):
                    row = update_box.row()
                    row.label(text="Installing update...", icon='TIME')
                else:
                    row = update_box.row()
                    row.scale_y = 1.5
                    op = row.operator("framo.install_update", text="Install Update Now", icon='IMPORT')
                
                if update_state.get("download_error"):
                    row = update_box.row()
                    row.alert = True
                    error_msg = update_state["download_error"]
                    if len(error_msg) > 50:
                        error_msg = error_msg[:47] + "..."
                    row.label(text=f"Error: {error_msg}", icon='ERROR')
                layout.separator()

        # Connected user status
        box = layout.box()
        row = box.row()
        if framo_user_info['name']:
            row.label(text=f"Connected: {framo_user_info['name']}", icon='LINKED')
        else:
            row.label(text="Disconnected", icon='UNLINKED')

        # Framo control (session-only switch, off on every start)
        control = get_control_state_copy()
        control_box = layout.box()
        control_box.prop(context.window_manager, "framo_control_enabled")
        col = control_box.column(align=True)
        col.scale_y = 0.8
        col.label(text="Framo can run Python here: full Blender access.")
        col.label(text="Ctrl+Z undoes each step.")
        if control["last_at"]:
            outcome = "ok" if control["last_ok"] else "failed"
            col.label(
                text=f"Last: {control['last_op']} at {control['last_at']:%H:%M:%S} ({outcome})",
                icon='CHECKMARK' if control["last_ok"] else 'ERROR',
            )
        elif control["enabled"]:
            col.label(text="Waiting for Framo...", icon='TIME')
        layout.separator()
        
        # Dependencies Status
        if DEPENDENCIES_AVAILABLE:
            dep_status = dependencies.check_all_dependencies()
            missing_required = [k for k, v in dep_status.items() if not v['installed'] and not v['optional']]
            
            if missing_required:
                box = layout.box()
                box.label(text="Dependencies", icon='PACKAGE')
                col = box.column(align=True)
                for key in missing_required:
                    dep = dep_status[key]
                    row = col.row()
                    row.label(text=f"{dep['name']}: Missing", icon='ERROR')
                col.separator()
                row = col.row()
                row.scale_y = 1.2
                row.operator("framo.install_dependencies", text="Install Required Dependencies", icon='IMPORT')
                
                missing_optional = [k for k, v in dep_status.items() if not v['installed'] and v['optional']]
                if missing_optional:
                    col.separator()
                    col.label(text="Optional:", icon='INFO')
                    for key in missing_optional:
                        dep = dep_status[key]
                        row = col.row()
                        row.label(text=f"{dep['name']}: {dep['description']}")
                        op = row.operator("framo.install_dependencies", text="Install", icon='IMPORT')
                        op.package = dep['name']

        # Export Settings
        row = layout.row()
        row.label(text="Export Settings:")
        row.operator("framo.reset_export_settings", text="", icon='FILE_REFRESH', emboss=False)
        main_box = layout.box()
        main_box.enabled = len(context.selected_objects) > 0
        
        # Compression preset
        compression_box = main_box.box()
        row = compression_box.row()
        row.label(text="GLB Compression", icon='MODIFIER')
        row.prop(settings, "compression_preset", text="")
        
        if settings.compression_preset == 'CUSTOM':
            col = compression_box.column(align=True)
            col.prop(settings, "use_draco")
            if settings.use_draco:
                col.separator()
                col.prop(settings, "draco_compression_level")
                col.separator()
                col.label(text="Quantization Bits:")
                col.prop(settings, "draco_quantization_position")
                col.prop(settings, "draco_quantization_normal")
                col.prop(settings, "draco_quantization_texcoord")
        
        # Auto UV Unwrap
        uv_box = main_box.box()
        row = uv_box.row()
        row.label(text="Auto UV Unwrap (if no uv map present)", icon='UV')
        row.prop(settings, "enable_auto_uv", text="", emboss=True)

        if settings.enable_auto_uv:
            if not UV_UNWRAP_AVAILABLE and not UV_ATLAS_AVAILABLE:
                col = uv_box.column(align=True)
                col.scale_y = 0.85
                col.label(text="UV unwrap modules not available", icon='ERROR')
            else:
                col = uv_box.column(align=True)
                col.separator()
                row = col.row()
                row.prop(settings, "enable_uv_atlasing", text="Material-Based UV Atlasing")

                if settings.enable_uv_atlasing and UV_ATLAS_AVAILABLE:
                    atlas_col = col.column(align=True)
                    atlas_col.separator()
                    atlas_col.prop(settings, "atlas_min_objects")
                    atlas_col.prop(settings, "atlas_texture_size")
                    atlas_col.prop(settings, "atlas_margin")
                elif settings.enable_uv_atlasing and not UV_ATLAS_AVAILABLE:
                    col.separator()
                    col.label(text="UV atlas module not available", icon='ERROR')
                    col.label(text="Falling back to individual unwrap", icon='INFO')

        # Decimate
        decimate_box = main_box.box()
        row = decimate_box.row()
        row.label(text="Decimate", icon='MOD_DECIM')
        row.prop(settings, "enable_decimation", text="", emboss=True)
        
        if settings.enable_decimation:
            col = decimate_box.column(align=True)
            col.prop(settings, "decimate_ratio", slider=True)
            
            if context.selected_objects:
                excluded_objects = [item.object_name for item in settings.decimate_exclude_objects if item.object_name]
                all_mesh_objects = []
                for obj in context.selected_objects:
                    if obj.type == 'MESH' and obj.data:
                        all_mesh_objects.append((obj.name, len(obj.data.polygons)))
                    elif obj.type == 'EMPTY' and obj.instance_type == 'COLLECTION' and obj.instance_collection:
                        for coll_obj in obj.instance_collection.all_objects:
                            if coll_obj.type == 'MESH' and coll_obj.data:
                                all_mesh_objects.append((coll_obj.name, len(coll_obj.data.polygons)))
                
                high_poly_objects = [(name, faces) for name, faces in all_mesh_objects if faces > 10]
                
                if high_poly_objects:
                    col.separator()
                    total_faces = sum(faces for _, faces in high_poly_objects)
                    col.label(text=f"Total faces: {total_faces:,}")
                    reduction = (1 - settings.decimate_ratio) * 100
                    col.label(text=f"Est. reduction: {reduction:.0f}%")
                    
                    objects_to_show = []
                    for name, faces in high_poly_objects:
                        is_excluded = name in excluded_objects
                        objects_to_show.append((name, faces, is_excluded))
                    
                    if objects_to_show:
                        non_excluded_count = sum(1 for _, _, excluded in objects_to_show if not excluded)
                        excluded_count = sum(1 for _, _, excluded in objects_to_show if excluded)
                        
                        dropdown_box = col.box()
                        dropdown_row = dropdown_box.row()
                        summary_text = f"{non_excluded_count} will be decimated, {excluded_count} excluded" if excluded_count > 0 else f"{non_excluded_count} object(s) will be decimated"
                        dropdown_row.label(text=summary_text)
                        dropdown_row.prop(settings, 'decimate_dropdown_expanded', 
                                        text="", 
                                        icon='TRIA_DOWN' if settings.decimate_dropdown_expanded else 'TRIA_RIGHT',
                                        emboss=False, toggle=True)
                        
                        if settings.decimate_dropdown_expanded:
                            dropdown_content = dropdown_box.column(align=True)
                            dropdown_content.scale_y = 0.85
                            individual_overrides = {item.object_name: item.override_ratio 
                                                  for item in settings.decimate_individual_overrides}
                            for obj_name, faces, is_excluded in objects_to_show:
                                info_row = dropdown_content.row(align=True)
                                if is_excluded:
                                    info_row.label(text=f"{obj_name}: {faces:,} faces")
                                else:
                                    has_individual_override = obj_name in individual_overrides
                                    toggle_op = info_row.operator("framo.toggle_decimate_exclusion", 
                                                                text="", 
                                                                icon='CHECKBOX_HLT' if not has_individual_override else 'CHECKBOX_DEHLT',
                                                                emboss=False)
                                    toggle_op.object_name = obj_name
                                    if has_individual_override:
                                        override_item = next((item for item in settings.decimate_individual_overrides if item.object_name == obj_name), None)
                                        if override_item:
                                            info_row.label(text=f"{obj_name}: {faces:,} faces →")
                                            slider_row = info_row.row(align=True)
                                            slider_row.scale_x = 1.0
                                            slider_row.prop(override_item, "override_ratio", text="", slider=True)
                                            op = info_row.operator("framo.remove_individual_decimate_override", text="", icon='X', emboss=False)
                                            op.object_name = obj_name
                                    else:
                                        reduction_pct = (1 - settings.decimate_ratio) * 100
                                        info_row.label(text=f"{obj_name}: {faces:,} faces → {reduction_pct:.0f}% reduction")
        
        # Subdivision Override
        subdiv_box = main_box.box()
        row = subdiv_box.row()
        row.label(text="Subdivision Override", icon='MOD_SUBSURF')
        row.prop(settings, "enable_subdiv_override", text="", emboss=True)
        if settings.enable_subdiv_override:
            col = subdiv_box.column(align=True)
            col.prop(settings, "subdiv_override_level", slider=True)

            # Show subdivision info for selected objects
            if context.selected_objects:
                excluded_objects = [item.object_name for item in settings.subdiv_exclude_objects if item.object_name]

                subdiv_objects = []
                for obj in context.selected_objects:
                    if obj.type == 'MESH':
                        for modifier in obj.modifiers:
                            if modifier.type == 'SUBSURF':
                                current_level = modifier.render_levels if hasattr(modifier, 'render_levels') and modifier.render_levels > 0 else modifier.levels
                                is_excluded = obj.name in excluded_objects
                                subdiv_objects.append((obj.name, current_level, is_excluded))
                                break
                    elif obj.type == 'EMPTY' and obj.instance_type == 'COLLECTION' and obj.instance_collection:
                        for coll_obj in obj.instance_collection.all_objects:
                            if coll_obj.type == 'MESH':
                                for modifier in coll_obj.modifiers:
                                    if modifier.type == 'SUBSURF':
                                        current_level = modifier.render_levels if hasattr(modifier, 'render_levels') and modifier.render_levels > 0 else modifier.levels
                                        is_excluded = coll_obj.name in excluded_objects
                                        subdiv_objects.append((coll_obj.name, current_level, is_excluded))
                                        break

                if subdiv_objects:
                    col.separator()

                    individual_overrides = {item.object_name: item.override_level
                                          for item in settings.subdiv_individual_overrides}

                    # Filter to show objects that will be reduced, are excluded, or have individual overrides
                    objects_to_show = []
                    for name, level, excluded in subdiv_objects:
                        has_individual_override = name in individual_overrides
                        global_will_reduce = level > settings.subdiv_override_level

                        if excluded or global_will_reduce or has_individual_override:
                            objects_to_show.append((name, level, excluded))

                    if objects_to_show:
                        non_excluded_count = sum(1 for _, level, excluded in objects_to_show if not excluded)
                        excluded_count = sum(1 for _, _, excluded in objects_to_show if excluded)

                        dropdown_box = col.box()
                        dropdown_row = dropdown_box.row()
                        summary_text = f"{non_excluded_count} will be overridden, {excluded_count} excluded" if excluded_count > 0 else f"{non_excluded_count} object(s) will be overridden"
                        dropdown_row.label(text=summary_text)
                        dropdown_row.prop(settings, 'subdiv_dropdown_expanded',
                                        text="",
                                        icon='TRIA_DOWN' if settings.subdiv_dropdown_expanded else 'TRIA_RIGHT',
                                        emboss=False, toggle=True)

                        if settings.subdiv_dropdown_expanded:
                            dropdown_content = dropdown_box.column(align=True)
                            dropdown_content.scale_y = 0.85

                            for obj_name, level, is_excluded in objects_to_show:
                                info_row = dropdown_content.row(align=True)

                                if is_excluded:
                                    info_row.label(text=f"{obj_name}: Level {level}")
                                else:
                                    has_individual_override = obj_name in individual_overrides

                                    toggle_op = info_row.operator("framo.toggle_subdiv_exclusion",
                                                                text="",
                                                                icon='CHECKBOX_HLT' if not has_individual_override else 'CHECKBOX_DEHLT',
                                                                emboss=False)
                                    toggle_op.object_name = obj_name

                                    if has_individual_override:
                                        override_item = next((item for item in settings.subdiv_individual_overrides if item.object_name == obj_name), None)
                                        if override_item:
                                            info_row.label(text=f"{obj_name}: Level {level} →")
                                            slider_row = info_row.row(align=True)
                                            slider_row.scale_x = 1.0
                                            slider_row.prop(override_item, "override_level", text="", slider=True)
                                            op = info_row.operator("framo.remove_individual_subdiv_override", text="", icon='X', emboss=False)
                                            op.object_name = obj_name
                                    else:
                                        info_row.label(text=f"{obj_name}: Level {level} → {settings.subdiv_override_level}")

        # Texture Optimization
        texture_box = main_box.box()
        row = texture_box.row()
        row.label(text="Optimize Textures", icon='IMAGE_DATA')
        row.prop(settings, "enable_texture_optimization", text="", emboss=True)
        if settings.enable_texture_optimization:
            col = texture_box.column(align=True)
            if not TEXTURE_SCALER_AVAILABLE:
                col.separator()
                col.label(text="Texture scaler module not available", icon='ERROR')
            else:
                col.prop(settings, "texture_max_size", text="Max Size")
                # ... (abbreviated texture analysis)

        layout.separator()
        
        # Material Readiness
        layout.label(text="Material Readiness:")
        box = layout.box()
        box.enabled = len(context.selected_objects) > 0
        
        has_unsupported_materials = False
        unsupported_count = 0
        
        if context.selected_objects and MATERIAL_ANALYZER_AVAILABLE:
            materials = material_analyzer.get_materials_to_analyze(context)
            if not materials:
                box.label(text="No materials found", icon='INFO')
            else:
                material_results = {}
                for material in materials:
                    result = material_analyzer.analyze_material_readiness(material)
                    material_results[material] = result
                    if not result['is_ready']:
                        has_unsupported_materials = True
                        unsupported_count += 1
                
                summary_row = box.row()
                if unsupported_count > 0:
                    summary_row.label(text=f"{unsupported_count} material(s) need attention", icon='ERROR')
                else:
                    summary_row.label(text=f"All {len(materials)} materials ready", icon='CHECKMARK')
                
                if unsupported_count > 0:
                    box.separator()
                    col = box.column(align=False)
                    for material in materials:
                        result = material_results[material]
                        if not result['is_ready']:
                            with safe_material_states_access() as material_expanded_states:
                                is_expanded = material_expanded_states.get(material.name, False)
                            
                            material_box = col.box()
                            material_col = material_box.column(align=False)
                            header_row = material_col.row(align=True)
                            header_row.label(icon='X')
                            name_split = header_row.split(factor=0.7)
                            name_split.label(text=material.name)
                            
                            button_split = header_row.split(factor=0.3)
                            op = button_split.operator("framo.open_material_in_shading", text="", icon='SHADING_RENDERED', emboss=False)
                            op.material_name = material.name
                            
                            op = button_split.operator("framo.toggle_material_expanded", text="", icon='TRIA_DOWN' if is_expanded else 'TRIA_RIGHT', emboss=False)
                            op.material_name = material.name
                            
                            if is_expanded:
                                material_col.separator()
                                if result['issues']:
                                    issues_col = material_col.column(align=False)
                                    for issue in result['issues']:
                                        issue_row = issues_col.row()
                                        issue_row.scale_y = 0.9
                                        issue_row.label(text=issue)
                                material_col.separator()
                                
                                replace_row = material_col.row()
                                replace_row.scale_y = 1.4
                                op = replace_row.operator("framo.replace_material", text="Replace Material", icon='MATERIAL')
                                op.old_material_name = material.name
                            col.separator()

        # Export status
        if settings.is_exporting and settings.export_status:
            layout.separator()
            status_box = layout.box()
            status_row = status_box.row()
            status_row.alignment = 'CENTER'
            status_row.label(text=settings.export_status, icon='TIME')
        
        # Export button
        layout.separator()
        row = layout.row()
        row.scale_y = 2.0
        row.enabled = not settings.is_exporting
        
        icon_id = get_icon_id('framo_icon')
        button_enabled = True
        user_connected = framo_user_info.get('name') is not None
        
        if not user_connected or not context.selected_objects:
            button_enabled = False
            
        if icon_id:
            row.operator("framo.export_to_web", text="Send to Framo", icon_value=icon_id)
        else:
            row.operator("framo.export_to_web", text="Send to Framo", icon='EXPORT')

        if not button_enabled:
            warning_row = layout.row()
            warning_row.scale_y = 0.9
            if not user_connected:
                warning_row.label(text="No user connected to framo.app", icon='INFO')
            elif not context.selected_objects:
                warning_row.label(text="Select at least one object to export", icon='ERROR')

