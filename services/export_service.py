import bpy
import os
import tempfile
from datetime import datetime
from ..processing import decimation, uv_unwrap, material_cleaner, material_analyzer, texture_scaler, uv_atlas
from ..core.properties import update_export_status, clear_export_status
from ..utils.context_managers import preserve_blender_state
from ..utils.logging_config import get_logger
from . import framo_collection, live_link, preview_server, shader_graphs

# Get logger for this module
log = get_logger()


def gltf_params(filepath):
    """What every send asks the exporter for."""
    return {
        'filepath': filepath,
        'export_format': 'GLB',
        'export_apply': True,
        'export_extras': True,
        'export_image_format': 'WEBP',
        'export_texture_dir': '',
    }


def _export_gltf(params, report_callback):
    """Run the exporter, falling back through the image formats a texture may refuse."""
    try:
        bpy.ops.export_scene.gltf(**params)
        return
    except Exception as e:
        print(f"Export failed with WEBP image format: {e}")
    try:
        params['export_image_format'] = 'AUTO'
        bpy.ops.export_scene.gltf(**params)
        report_callback({'WARNING'}, "Some textures couldn't convert to WebP - exported with AUTO format")
        return
    except Exception as e:
        print(f"Export also failed with AUTO format: {e}")
    try:
        params['export_image_format'] = 'NONE'
        bpy.ops.export_scene.gltf(**params)
        report_callback({'WARNING'}, "Export completed with original texture formats (conversion failed)")
    except Exception as e:
        print(f"Export also failed with NONE format: {e}")
        raise Exception(f"GLB export failed: {e}")


def graphs_of(names):
    """The shader graphs a send carries. A graph that cannot be read costs the send its shaders, never the send."""
    try:
        return shader_graphs.graphs_for(sorted(set(names)))
    except Exception as e:
        log.error(f"Shader graphs left out of the send: {e}")
        return {"m": {}, "g": {}}


def _read_export(path):
    with open(path, 'rb') as f:
        glb_data = f.read()
    os.unlink(path)
    return glb_data


def send_metadata(context, object_count, live, draco_enabled, extra=None):
    """What the server says about a send, apart from what is only known once the bytes exist."""
    settings = context.scene.framo_export_settings
    blend_filepath = bpy.data.filepath
    filename = (os.path.splitext(os.path.basename(blend_filepath))[0] + ".glb") if blend_filepath else "untitled.glb"
    return {
        "filename": filename,
        "scene_name": context.scene.name,
        "export_settings": {
            "compression": settings.compression_preset,
            "draco_enabled": draco_enabled,
        },
        "object_count": object_count,
        "live": live,
        **(extra or {}),
    }


def publish(glb_data, metadata):
    """Hand the bytes to the server in memory; Framo polls them from there. Touches no bpy: a thread calls it."""
    if preview_server.server_instance is None:
        raise Exception("the Bridge server is not running (is port 8080 taken?)")
    preview_server.server_instance.latest_glb = glb_data
    preview_server.server_instance.latest_metadata = {
        **metadata,
        "timestamp": datetime.now().isoformat(),
        "size": len(glb_data),
        "size_mb": f"{len(glb_data)/(1024*1024):.2f}",
    }


class ExportService:
    def export_to_web(self, context, report_callback=None):
        """
        Main export logic.
        report_callback: function(type, message) - e.g. self.report
        """
        if report_callback is None:
            report_callback = lambda t, m: print(f"{t}: {m}")

        # Get settings
        settings = context.scene.framo_export_settings
        # Framo's editor is following live: export for speed, not size (services/live_link.py).
        live = live_link.is_live()
        
        # Mark export as in progress
        settings.is_exporting = True
        update_export_status(context, "Preparing export...")
        
        # Initialize variables that need to be accessible in finally block
        temp_objects = [] # Keep for legacy/cleanup safety
        proxy_objects = [] # Proxy objects for off-scene processing
        subdiv_original_levels = []  # Store original subdivision levels for restoration
        texture_scaled_copies = {}  # Track scaled copies for cleanup
        data_swap_map = {}  # Map obj -> (original_data, original_slots, temp_data)
        
        # What goes to Framo is what is in the collection, whatever is selected.
        send_objects = framo_collection.visible_objects(context.view_layer)

        try:
            with preserve_blender_state():
                info_parts = []

                # The steps below read their objects from the selection, which is restored afterwards.
                for obj in list(context.selected_objects):
                    obj.select_set(False)
                for obj in send_objects:
                    obj.select_set(True)

                # 1. Identify Source Objects (Recursive)
                initial_objects = send_objects

                processed_objects = set()
                source_mesh_objects = []
                non_mesh_objects = []
                atlas_objects = [] 
                
                instance_sources = set()
                
                def collect_recursive(objs, is_from_instance=False):
                    for obj in objs:
                        if is_from_instance:
                            instance_sources.add(obj)

                        if obj in processed_objects:
                            continue
                        processed_objects.add(obj)
                        
                        if obj.type == 'MESH':
                            source_mesh_objects.append(obj)
                        
                        if obj.type == 'EMPTY' and obj.instance_type == 'COLLECTION' and obj.instance_collection:
                            collect_recursive(obj.instance_collection.all_objects, is_from_instance=True)
                
                collect_recursive(initial_objects)
                
                non_mesh_objects = [obj for obj in send_objects if obj.type != 'MESH']
                
                # 2. Apply Subdivision Override
                if settings.enable_subdiv_override:
                    update_export_status(context, "Applying subdivision override...")
                    
                    excluded_objects = [item.object_name for item in settings.subdiv_exclude_objects if item.object_name]
                    individual_overrides = {item.object_name: item.override_level 
                                          for item in settings.subdiv_individual_overrides}
                    
                    for obj in source_mesh_objects:
                        if obj.name in excluded_objects:
                            continue
                        
                        for modifier in obj.modifiers:
                            if modifier.type == 'SUBSURF':
                                current_viewport_level = modifier.levels
                                current_render_level = modifier.render_levels if hasattr(modifier, 'render_levels') and modifier.render_levels > 0 else modifier.levels
                                
                                override_level = individual_overrides.get(obj.name, settings.subdiv_override_level)
                                
                                should_override_viewport = override_level < current_viewport_level
                                should_override_render = override_level < current_render_level
                                
                                if should_override_viewport or should_override_render:
                                    original_viewport = modifier.levels
                                    original_render = modifier.render_levels if hasattr(modifier, 'render_levels') else modifier.levels
                                    subdiv_original_levels.append((obj, modifier, original_viewport, original_render))
                                    
                                    if should_override_viewport:
                                        modifier.levels = override_level
                                    if hasattr(modifier, 'render_levels') and should_override_render:
                                        modifier.render_levels = override_level
                    
                    if subdiv_original_levels:
                        info_parts.append(f"Subdiv Override: Level {settings.subdiv_override_level}")

                # 3. Data Swap
                obj_to_target = {}
                
                if (settings.enable_decimation or settings.enable_auto_uv or True) and source_mesh_objects: # Material cleaner always assumed available
                    update_export_status(context, "Creating temporary mesh data...")
                    
                    for obj in source_mesh_objects:
                        try:
                            original_data = obj.data
                            original_slots = [s.material for s in obj.material_slots]
                            
                            temp_data = obj.data.copy()
                            temp_data.name = f"TEMP_DATA_{obj.name}"
                            
                            obj.data = temp_data
                            
                            data_swap_map[obj] = (original_data, original_slots, temp_data)
                            
                            if obj.name in context.view_layer.objects:
                                obj_to_target[obj] = obj
                            else:
                                proxy = bpy.data.objects.new(f"FRAMO_{obj.name}", temp_data)
                                proxy.hide_viewport = True
                                proxy.hide_render = True
                                context.collection.objects.link(proxy)
                                proxy_objects.append(proxy)
                                obj_to_target[obj] = proxy
                                
                        except Exception as e:
                            print(f"Warning: Failed to swap data for {obj.name}: {e}")
                
                # 4. Clean unused materials
                if source_mesh_objects:
                    update_export_status(context, "Removing unused materials...")
                    targets_to_clean = [obj_to_target[obj] for obj in source_mesh_objects if obj in obj_to_target]
                    
                    if targets_to_clean:
                        cleaning_result = material_cleaner.clean_materials_batch(targets_to_clean, dry_run=False)
                        if cleaning_result['total_removed'] > 0:
                            info_parts.append(f"Removed {cleaning_result['total_removed']} unused materials")

                # 5. Auto UV
                if settings.enable_auto_uv and source_mesh_objects:
                    update_export_status(context, f"UV unwrapping {len(source_mesh_objects)} object(s)...")
                    targets_to_unwrap = [obj_to_target[obj] for obj in source_mesh_objects if obj in obj_to_target]
                    
                    if targets_to_unwrap:
                        uv_stats = uv_unwrap.auto_unwrap_objects(
                            targets_to_unwrap,
                            angle_limit=66.0,
                            island_margin=0.02,
                            verbose=False
                        )

                        if uv_stats['unwrapped'] > 0:
                            info_parts.append(f"UV unwrapped {uv_stats['unwrapped']} objects")
                
                # 6. Decimation
                if settings.enable_decimation and not live and source_mesh_objects:
                    excluded_objects = [item.object_name for item in settings.decimate_exclude_objects if item.object_name]
                    individual_overrides = {item.object_name: item.override_ratio 
                                          for item in settings.decimate_individual_overrides}
                    
                    objects_to_decimate = [obj for obj in source_mesh_objects if obj.name not in excluded_objects]
                    
                    if objects_to_decimate:
                        update_export_status(context, f"Decimating {len(objects_to_decimate)} mesh(es)...")
                        
                        decimated_count = 0
                        total_faces_before = 0
                        total_faces_after = 0
                        
                        meshes_list = sorted(objects_to_decimate, key=lambda x: x.name)
                        
                        for idx, obj in enumerate(meshes_list):
                            target_obj = obj_to_target.get(obj)
                            if not target_obj:
                                continue

                            if len(target_obj.data.polygons) > 10:
                                faces_before = len(target_obj.data.polygons)
                                total_faces_before += faces_before
                                
                                decimate_ratio = individual_overrides.get(obj.name, settings.decimate_ratio)
                                
                                update_export_status(context, f"Decimating {obj.name} ({idx+1}/{len(meshes_list)})...")
                                
                                success, _, faces_after, error_details = decimation.decimate_object(
                                    target_obj,
                                    target_ratio=decimate_ratio,
                                    method='bmesh',
                                    preserve_uv_seams=True,
                                    preserve_sharp_edges=True,
                                    aggression=7,
                                    preserve_border=True
                                )
                                
                                if success:
                                    total_faces_after += faces_after
                                    decimated_count += 1
                                else:
                                    if error_details:
                                        report_callback({'WARNING'}, f"{obj.name}: {error_details}")
                        
                        if decimated_count > 0:
                            if total_faces_before > 0:
                                reduction_pct = ((total_faces_before - total_faces_after) / total_faces_before) * 100
                                info_parts.append(f"Decimated {decimated_count} objects ({reduction_pct:.0f}% reduction)")
                            else:
                                info_parts.append(f"Decimated {decimated_count} objects")

                # 7. Material Analysis
                materials_to_analyze = []
                material_analysis_results = {}
                unsupported_materials = []
                
                update_export_status(context, "Analyzing materials...")
                
                materials_to_analyze = material_analyzer.get_materials_to_analyze(context)
                for material in materials_to_analyze:
                    result = material_analyzer.analyze_material_readiness(material)
                    material_analysis_results[material.name] = {
                        'is_ready': result['is_ready'],
                        'issues': result['issues'],
                        'warnings': result['warnings']
                    }
                    if not result['is_ready']:
                        unsupported_materials.append(material.name)
                
                if unsupported_materials:
                    material_list = ', '.join(unsupported_materials[:5])
                    if len(unsupported_materials) > 5:
                        material_list += f" (+{len(unsupported_materials) - 5} more)"
                    report_callback({'WARNING'}, f"{len(unsupported_materials)} unsupported material(s) detected: {material_list}. Check Material Readiness panel.")

                # 8. Texture Optimization
                if settings.enable_texture_optimization:
                    update_export_status(context, "Optimizing textures...")
                    try:
                        target_size = int(settings.texture_max_size)
                        excluded_materials = [item.material_name for item in settings.texture_exclude_materials if item.material_name]

                        texture_result = texture_scaler.process_textures_native(
                            context,
                            scale_to_target=True,
                            compress=False,
                            target_size=target_size,
                            excluded_materials=excluded_materials,
                            status_callback=lambda msg: update_export_status(context, msg)
                        )
                        texture_scaled_copies = texture_result.get('processed_images', {})

                        scaled_count = texture_result.get('scaled', 0)
                        if scaled_count > 0:
                            size_label = settings.texture_max_size
                            info_parts.append(f"Scaled {scaled_count} texture(s) to {size_label}px (Native)")
                            info_parts.append("WebP export by glTF exporter")

                        if texture_result['errors']:
                            for error in texture_result['errors'][:3]:
                                report_callback({'WARNING'}, error)
                    except Exception as e:
                        report_callback({'WARNING'}, f"Texture processing error: {str(e)}")

                update_export_status(context, "Exporting GLB...")
                
                # 9. GLB Export
                with tempfile.NamedTemporaryFile(suffix='.glb', delete=False) as tmp_file:
                    tmp_path = tmp_file.name
                
                # Select appropriate objects
                # This ensures that collection instances (which point to modified data) are exported with their transforms
                bpy.ops.object.select_all(action='DESELECT')
                
                for obj in send_objects:
                    # Skip objects that are sources for instances (to avoid duplication)
                    if obj in instance_sources:
                        continue

                    if obj.name in bpy.data.objects:
                        obj.select_set(True)
                
                # Ensure active object is set (use first selected)
                if context.selected_objects:
                    context.view_layer.objects.active = context.selected_objects[0]

                export_params = gltf_params(tmp_path)
                export_params['use_selection'] = True
                
                if settings.use_draco and not live:
                    export_params.update({
                        'export_draco_mesh_compression_enable': True,
                        'export_draco_mesh_compression_level': settings.draco_compression_level,
                        'export_draco_position_quantization': settings.draco_quantization_position,
                        'export_draco_normal_quantization': settings.draco_quantization_normal,
                        'export_draco_texcoord_quantization': settings.draco_quantization_texcoord,
                    })
                    info_parts.append(f"Draco Level {settings.draco_compression_level}")
                else:
                    export_params['export_draco_mesh_compression_enable'] = False
                    info_parts.append("Live, uncompressed" if live else "Uncompressed")
                
                _export_gltf(export_params, report_callback)
                glb_data = _read_export(tmp_path)
                glb_data = shader_graphs.embed(glb_data, graphs_of(shader_graphs.glb_material_names(glb_data)))
                
                extra_metadata = {}
                if materials_to_analyze and material_analysis_results:
                    extra_metadata["materials"] = {
                        "total": len(materials_to_analyze),
                        "ready": len([m for m in materials_to_analyze if material_analysis_results[m.name]['is_ready']]),
                        "unsupported": [],
                        "analysis": material_analysis_results
                    }
                
                update_export_status(context, "Uploading to Framo...")
                
                publish(glb_data, send_metadata(context, len(send_objects), live, settings.use_draco and not live, extra_metadata))

                size_mb = len(glb_data) / (1024 * 1024)
                info_str = f" ({', '.join(info_parts)})" if info_parts else ""
                
                update_export_status(context, f"✓ Export complete! ({size_mb:.2f}MB)")
                report_callback({'INFO'}, f"Exported {size_mb:.2f}MB{info_str} to Framo")

                bpy.app.timers.register(clear_export_status, first_interval=3.0)

                # === CLEANUP INSIDE preserve_blender_state context ===
                # This ensures cleanup happens BEFORE selection is restored,
                # preventing crashes from accessing freed mesh data
                log.debug("Starting cleanup phase (inside context)...")

                # Cleanup subdiv - use names to avoid stale references
                if subdiv_original_levels:
                    log.debug(f"Restoring {len(subdiv_original_levels)} subdiv modifiers...")
                    subdiv_restore_list = []
                    for obj, modifier, original_viewport, original_render in subdiv_original_levels:
                        try:
                            if obj and modifier:
                                subdiv_restore_list.append((obj.name, modifier.name, original_viewport, original_render))
                        except Exception:
                            pass

                    for obj_name, mod_name, original_viewport, original_render in subdiv_restore_list:
                        try:
                            if obj_name in bpy.data.objects:
                                obj = bpy.data.objects[obj_name]
                                if mod_name in obj.modifiers:
                                    mod = obj.modifiers[mod_name]
                                    if mod.type == 'SUBSURF':
                                        mod.levels = original_viewport
                                        if hasattr(mod, 'render_levels'):
                                            mod.render_levels = original_render
                        except Exception:
                            pass

                log.debug("Subdiv cleanup done")

                # Restore textures - use names to avoid stale references
                if texture_scaled_copies:
                    log.debug(f"Restoring {len(texture_scaled_copies)} textures...")
                    texture_scaler.restore_scaled_textures(context, texture_scaled_copies)

                log.debug("Texture cleanup done")

                # Step 1: Restore original mesh data (before removing anything)
                if data_swap_map:
                    log.debug(f"Step 1: Restoring {len(data_swap_map)} mesh data swaps...")
                    restore_list = []
                    for obj, (orig_data, _, temp_data) in data_swap_map.items():
                        try:
                            if obj and orig_data:
                                restore_list.append((obj.name, orig_data.name))
                        except Exception:
                            pass

                    for obj_name, orig_data_name in restore_list:
                        try:
                            if obj_name in bpy.data.objects and orig_data_name in bpy.data.meshes:
                                bpy.data.objects[obj_name].data = bpy.data.meshes[orig_data_name]
                        except Exception:
                            pass

                log.debug("Step 1 done - mesh data restored")

                # Collect names of objects/meshes to remove LATER (after operator returns)
                # Removing them now causes Blender to crash when it redraws
                proxy_names_to_remove = []
                if proxy_objects:
                    log.debug(f"Step 2: Collecting {len(proxy_objects)} proxy object names for deferred removal...")
                    for proxy in proxy_objects:
                        try:
                            proxy_names_to_remove.append(proxy.name)
                        except Exception:
                            pass

                temp_mesh_names_to_remove = []
                if data_swap_map:
                    log.debug("Step 3: Collecting temp mesh names for deferred removal...")
                    for obj, (_, _, temp_data) in data_swap_map.items():
                        try:
                            if temp_data:
                                temp_mesh_names_to_remove.append(temp_data.name)
                        except Exception:
                            pass

                log.debug(f"Collected {len(proxy_names_to_remove)} proxies and {len(temp_mesh_names_to_remove)} temp meshes for deferred cleanup")

                # Schedule deferred cleanup after Blender has finished processing
                def deferred_cleanup():
                    log.debug("Deferred cleanup: Starting...")
                    try:
                        # Remove proxy objects
                        for proxy_name in proxy_names_to_remove:
                            try:
                                if proxy_name in bpy.data.objects:
                                    bpy.data.objects.remove(bpy.data.objects[proxy_name], do_unlink=True)
                            except Exception:
                                pass
                        log.debug(f"Deferred cleanup: Removed {len(proxy_names_to_remove)} proxy objects")

                        # Remove temp meshes
                        for mesh_name in temp_mesh_names_to_remove:
                            try:
                                if mesh_name in bpy.data.meshes:
                                    mesh = bpy.data.meshes[mesh_name]
                                    if mesh.users == 0:
                                        bpy.data.meshes.remove(mesh)
                            except Exception:
                                pass
                        log.debug("Deferred cleanup: Removed temp meshes")

                        # Extra cleanup for orphaned meshes
                        orphaned = [m.name for m in bpy.data.meshes
                                   if m.name.startswith("TEMP_DATA_") and m.users == 0]
                        for mesh_name in orphaned:
                            try:
                                if mesh_name in bpy.data.meshes:
                                    bpy.data.meshes.remove(bpy.data.meshes[mesh_name])
                            except Exception:
                                pass
                        log.debug(f"Deferred cleanup: Removed {len(orphaned)} orphaned meshes")
                        log.debug("Deferred cleanup: Complete!")
                    except Exception as e:
                        log.debug(f"Deferred cleanup: Exception: {e}")
                    return None  # Don't repeat

                bpy.app.timers.register(deferred_cleanup, first_interval=0.1)
                log.debug("Deferred cleanup scheduled")

        except Exception as e:
            update_export_status(context, f"✗ Export failed: {str(e)}")
            report_callback({'ERROR'}, f"Export failed: {str(e)}")
            bpy.app.timers.register(clear_export_status, first_interval=5.0)
            export_result = {'CANCELLED'}
        else:
            export_result = {'FINISHED'}

        settings.is_exporting = False
        return export_result
