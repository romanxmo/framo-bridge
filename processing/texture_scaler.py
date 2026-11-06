"""
Texture Scaler Module using Blender's Native Functions

This module provides texture scaling using ONLY Blender's built-in image operations.
No external dependencies (Pillow, PIL, etc.) required.

Primary Purpose:
- Scale large textures (e.g., 4K → 1K) before GLB export to reduce memory usage
- WebP compression is handled automatically by Blender's glTF exporter

Features:
- Image resizing/scaling using image.scale()
- Maintains aspect ratios automatically
- Non-destructive (creates new images, originals preserved)
- Optional format conversion (JPEG, PNG, WebP) if needed for testing
- Handles transparency (alpha channels)

Note: In production use, this module only scales images. The glTF exporter handles
WebP compression via the 'export_image_format': 'WEBP' parameter.
"""

import bpy
import os
import tempfile
from typing import Optional, Tuple, Dict, List, Set


def is_webp_supported() -> bool:
    """
    Check if Blender supports WebP format.

    WebP support was added in Blender 3.0+

    Returns:
        bool: True if WebP is supported
    """
    # Check Blender version
    version = bpy.app.version
    major, minor = version[0], version[1]

    # WebP support added in Blender 3.0
    return major >= 3


def get_image_format_settings(scene: bpy.types.Scene) -> Dict[str, any]:
    """
    Get current image format settings from scene render settings.

    Args:
        scene: Blender scene

    Returns:
        dict: Current format settings for restoration later
    """
    settings = scene.render.image_settings

    return {
        'file_format': settings.file_format,
        'quality': settings.quality,
        'compression': settings.compression,
        'color_mode': settings.color_mode,
        'color_depth': settings.color_depth,
    }


def restore_image_format_settings(scene: bpy.types.Scene, settings: Dict[str, any]) -> None:
    """
    Restore previous image format settings to scene.

    Args:
        scene: Blender scene
        settings: Settings dictionary from get_image_format_settings()
    """
    render_settings = scene.render.image_settings

    render_settings.file_format = settings['file_format']
    render_settings.quality = settings['quality']
    render_settings.compression = settings['compression']
    render_settings.color_mode = settings['color_mode']
    render_settings.color_depth = settings['color_depth']


def get_texture_size(image: bpy.types.Image) -> Tuple[int, int]:
    """
    Get the size of a texture image.

    Args:
        image: bpy.types.Image object

    Returns:
        tuple: (width, height) in pixels, or (0, 0) if invalid
    """
    if not image:
        return (0, 0)

    if hasattr(image, 'size') and len(image.size) >= 2:
        return (image.size[0], image.size[1])

    return (0, 0)


def has_transparency(image: bpy.types.Image) -> bool:
    """
    Check if an image has an alpha channel with meaningful transparency.

    Args:
        image: bpy.types.Image object

    Returns:
        bool: True if image has transparency
    """
    if not image:
        return False

    # Check if image has alpha channel
    if not hasattr(image, 'channels') or image.channels < 4:
        return False

    # Note: We assume if it has 4 channels, it has alpha
    # A more thorough check would require analyzing pixel data
    # which is expensive - left to actual compression step
    return image.channels == 4


def compress_image_native(
    image: bpy.types.Image,
    output_format: str = 'WEBP',
    quality: int = 90,
    max_dimension: Optional[int] = None
) -> Tuple[Optional[bpy.types.Image], Optional[str]]:
    """
    Compress an image using Blender's native operations.
    Creates a new compressed copy without modifying the original.

    Args:
        image: Source image to compress
        output_format: Output format ('WEBP', 'JPEG', 'PNG')
        quality: Compression quality (0-100, default: 90)
        max_dimension: Optional maximum dimension for scaling (None = no scaling)

    Returns:
        Tuple of (compressed_image: Optional[Image], error: Optional[str])
    """
    if not image:
        return None, "Image is None"

    try:
        scene = bpy.context.scene

        # Save current render settings
        previous_settings = get_image_format_settings(scene)

        # Get image dimensions
        width, height = get_texture_size(image)
        if width == 0 or height == 0:
            return None, f"Image {image.name} has invalid dimensions"

        # Check if scaling is needed
        should_scale = False
        new_width, new_height = width, height

        if max_dimension and (width > max_dimension or height > max_dimension):
            should_scale = True
            # Calculate new dimensions maintaining aspect ratio
            max_dim = max(width, height)
            scale = max_dimension / max_dim
            new_width = max(1, int(width * scale))
            new_height = max(1, int(height * scale))

        # Create a copy of the image for processing
        # We need to save and reload to create an actual copy
        original_filepath = image.filepath_raw

        # Determine if image has transparency
        has_alpha = has_transparency(image)

        # Choose appropriate format based on transparency and user preference
        if output_format == 'WEBP':
            if not is_webp_supported():
                # Fallback to PNG for transparency, JPEG for opaque
                output_format = 'PNG' if has_alpha else 'JPEG'
                print(f"WebP not supported, using {output_format} instead")
        elif output_format == 'JPEG' and has_alpha:
            # JPEG doesn't support transparency - use PNG
            output_format = 'PNG'
            print(f"JPEG doesn't support transparency, using PNG instead")

        # Configure render settings for output format
        settings = scene.render.image_settings
        settings.file_format = output_format

        if output_format == 'JPEG':
            settings.quality = quality
            settings.color_mode = 'RGB'
        elif output_format == 'PNG':
            # PNG compression: 0-100 (higher = more compression)
            settings.compression = min(100, quality)
            settings.color_mode = 'RGBA' if has_alpha else 'RGB'
        elif output_format == 'WEBP':
            settings.quality = quality
            settings.color_mode = 'RGBA' if has_alpha else 'RGB'

        # Create temporary file for output
        ext_map = {
            'JPEG': '.jpg',
            'PNG': '.png',
            'WEBP': '.webp'
        }
        ext = ext_map.get(output_format, '.png')

        with tempfile.NamedTemporaryFile(suffix=ext, delete=False) as tmp_file:
            tmp_path = tmp_file.name

        # If scaling is needed, create scaled copy first
        if should_scale:
            # Make a temporary copy for scaling
            temp_image = image.copy()
            temp_image.scale(new_width, new_height)

            # Save the scaled image with compression
            temp_image.save_render(tmp_path, scene=scene)

            # Remove temporary scaled image
            bpy.data.images.remove(temp_image)
        else:
            # Save directly with compression
            image.save_render(tmp_path, scene=scene)

        # Load the compressed image as a new Blender image
        compressed_image = bpy.data.images.load(tmp_path)

        # Generate a unique name
        format_suffix = output_format.capitalize()
        if should_scale:
            compressed_image.name = f"{image.name}_{new_width}x{new_height}_{format_suffix}"
        else:
            compressed_image.name = f"{image.name}_{format_suffix}"

        # Pack the image into the blend file
        compressed_image.pack()

        # Restore previous settings
        restore_image_format_settings(scene, previous_settings)

        # Clean up temp file
        try:
            os.unlink(tmp_path)
        except:
            pass

        # Log success
        if should_scale:
            print(f"Compressed and scaled {image.name}: {width}x{height} → {new_width}x{new_height} ({output_format}, quality: {quality})")
        else:
            print(f"Compressed {image.name}: {output_format} (quality: {quality})")

        return compressed_image, None

    except Exception as e:
        error_msg = f"Error compressing {image.name}: {str(e)}"
        print(error_msg)
        import traceback
        traceback.print_exc()
        return None, error_msg


def scale_image_native(
    image: bpy.types.Image,
    target_size: int = 1024
) -> Tuple[Optional[bpy.types.Image], Optional[str]]:
    """
    Scale an image to target maximum dimension using Blender's native operations.
    Creates a new scaled copy without modifying the original.

    Args:
        image: Source image to scale
        target_size: Target maximum dimension (default: 1024)

    Returns:
        Tuple of (scaled_image: Optional[Image], error: Optional[str])
    """
    if not image:
        return None, "Image is None"

    try:
        width, height = get_texture_size(image)

        if width == 0 or height == 0:
            return None, f"Image {image.name} has invalid dimensions"

        # Check if scaling is needed
        max_dim = max(width, height)
        if max_dim <= target_size:
            return None, f"Image {image.name} is already at or below target size"

        # Calculate new dimensions maintaining aspect ratio
        scale = target_size / max_dim
        new_width = max(1, int(width * scale))
        new_height = max(1, int(height * scale))

        # Create a copy of the image for scaling
        scaled_image = image.copy()

        # Scale the copy
        scaled_image.scale(new_width, new_height)

        # Update name
        scaled_image.name = f"{image.name}_Scaled"

        print(f"Scaled {image.name}: {width}x{height} → {new_width}x{new_height}")

        return scaled_image, None

    except Exception as e:
        error_msg = f"Error scaling {image.name}: {str(e)}"
        print(error_msg)
        import traceback
        traceback.print_exc()
        return None, error_msg


def get_all_texture_images(context: bpy.types.Context, excluded_materials: Optional[List[str]] = None) -> Set[bpy.types.Image]:
    """
    Get all image textures used in materials from selected objects only.

    Args:
        context: Blender context (must have selected_objects)
        excluded_materials: List of material names to exclude (optional)

    Returns:
        set: Set of bpy.types.Image objects from selected objects' materials
    """
    images = set()
    excluded_materials = excluded_materials if excluded_materials else []

    # Early return if no objects are selected
    if not context.selected_objects:
        return images

    # Get all materials from selected objects only
    materials = set()
    for obj in context.selected_objects:
        # Process mesh objects
        if obj.type == 'MESH' and obj.data:
            # Get materials from material slots of this selected object
            for slot in obj.material_slots:
                if slot.material and slot.material.name not in excluded_materials:
                    materials.add(slot.material)
        
        # Process collection instances (EMPTY objects that reference collections)
        elif obj.type == 'EMPTY' and obj.instance_type == 'COLLECTION' and obj.instance_collection:
            # Get materials from all mesh objects in the instanced collection
            for coll_obj in obj.instance_collection.all_objects:
                if coll_obj.type == 'MESH' and coll_obj.data:
                    for slot in coll_obj.material_slots:
                        if slot.material and slot.material.name not in excluded_materials:
                            materials.add(slot.material)

    # Traverse material node trees to find image textures
    for material in materials:
        if not material or not material.use_nodes or not material.node_tree:
            continue

        # Get all nodes in the tree
        for node in material.node_tree.nodes:
            if node.type == 'TEX_IMAGE':
                if hasattr(node, 'image') and node.image:
                    images.add(node.image)

    return images


def replace_image_in_materials(context: bpy.types.Context, old_image: bpy.types.Image, new_image: bpy.types.Image) -> None:
    """
    Replace all references to old_image with new_image in material node trees.
    Only replaces images in materials assigned to selected objects.

    Args:
        context: Blender context (must have selected_objects)
        old_image: Image to replace
        new_image: Image to use instead
    """
    # Early return if no objects are selected
    if not context.selected_objects:
        return

    # Get all materials from selected objects only
    materials = set()
    for obj in context.selected_objects:
        # Process mesh objects
        if obj.type == 'MESH' and obj.data:
            # Get materials from material slots of this selected object
            for slot in obj.material_slots:
                if slot.material:
                    materials.add(slot.material)
        
        # Process collection instances (EMPTY objects that reference collections)
        elif obj.type == 'EMPTY' and obj.instance_type == 'COLLECTION' and obj.instance_collection:
            # Get materials from all mesh objects in the instanced collection
            for coll_obj in obj.instance_collection.all_objects:
                if coll_obj.type == 'MESH' and coll_obj.data:
                    for slot in coll_obj.material_slots:
                        if slot.material:
                            materials.add(slot.material)

    # Replace image references in node trees
    for material in materials:
        if not material or not material.use_nodes or not material.node_tree:
            continue

        for node in material.node_tree.nodes:
            if node.type == 'TEX_IMAGE':
                if hasattr(node, 'image') and node.image == old_image:
                    node.image = new_image


def process_textures_native(
    context: bpy.types.Context,
    scale_to_target: bool = True,
    compress: bool = True,
    target_size: int = 1024,
    output_format: str = 'WEBP',
    quality: int = 90,
    excluded_materials: Optional[List[str]] = None,
    status_callback: Optional[any] = None
) -> Dict[str, any]:
    """
    Process all textures from selected objects using Blender's native operations.
    NON-DESTRUCTIVE: Original images remain untouched.

    Args:
        context: Blender context (must have selected_objects)
        scale_to_target: Whether to scale textures above target size
        compress: Whether to compress textures
        target_size: Target maximum dimension in pixels (default: 1024)
        output_format: Output format ('WEBP', 'JPEG', 'PNG')
        quality: Compression quality (0-100, default: 90)
        excluded_materials: List of material names to exclude (optional)
        status_callback: Optional callback function(status_text) for progress updates

    Returns:
        dict: {
            'processed': int,
            'scaled': int,
            'compressed': int,
            'processed_images': dict mapping original image to processed image,
            'errors': list of error messages
        }
    """
    # Get textures only from selected objects' materials
    images = get_all_texture_images(context, excluded_materials=excluded_materials)

    result = {
        'processed': 0,
        'scaled': 0,
        'compressed': 0,
        'processed_images': {},  # original_image -> processed_image
        'errors': []
    }

    total_images = len(images)

    for idx, image in enumerate(images):
        try:
            width, height = get_texture_size(image)
            max_dim = max(width, height)

            # Determine if we need to process this image
            needs_scaling = scale_to_target and max_dim > target_size
            needs_compression = compress

            if not needs_scaling and not needs_compression:
                continue

            if status_callback:
                status_callback(f"Processing texture {image.name} ({idx+1}/{total_images})...")

            processed_image = None

            # Option 1: Both scaling and compression
            if needs_scaling and needs_compression:
                # Use compress_image_native with max_dimension parameter
                # This does both in one operation
                processed_image, error = compress_image_native(
                    image,
                    output_format=output_format,
                    quality=quality,
                    max_dimension=target_size
                )

                if processed_image:
                    result['scaled'] += 1
                    result['compressed'] += 1
                elif error:
                    result['errors'].append(error)
                    continue

            # Option 2: Only scaling
            elif needs_scaling:
                processed_image, error = scale_image_native(image, target_size=target_size)

                if processed_image:
                    result['scaled'] += 1
                elif error:
                    result['errors'].append(error)
                    continue

            # Option 3: Only compression
            elif needs_compression:
                processed_image, error = compress_image_native(
                    image,
                    output_format=output_format,
                    quality=quality,
                    max_dimension=None
                )

                if processed_image:
                    result['compressed'] += 1
                elif error:
                    result['errors'].append(error)
                    continue

            # Replace references if we created a processed image
            if processed_image:
                replace_image_in_materials(context, image, processed_image)
                result['processed_images'][image] = processed_image
                result['processed'] += 1

        except Exception as e:
            result['errors'].append(f"Error processing {image.name}: {str(e)}")
            import traceback
            traceback.print_exc()

    return result


def restore_scaled_textures(context: bpy.types.Context, scaled_copies: Dict) -> None:
    """Put the original images back after an export and remove the scaled copies.

    Works by name: the references in ``scaled_copies`` may have gone stale.
    ``context`` must hold the objects the copies were made for.
    """
    restore_list = []
    for original_img, scaled_copy in scaled_copies.items():
        try:
            if original_img and scaled_copy:
                restore_list.append((original_img.name, scaled_copy.name))
        except Exception:
            pass

    for orig_name, scaled_name in restore_list:
        try:
            if orig_name in bpy.data.images and scaled_name in bpy.data.images:
                original_img = bpy.data.images[orig_name]
                scaled_copy = bpy.data.images[scaled_name]
                replace_image_in_materials(context, scaled_copy, original_img)
                if scaled_name in bpy.data.images:
                    bpy.data.images.remove(bpy.data.images[scaled_name])
        except Exception:
            pass


def analyze_textures_native(context: bpy.types.Context, excluded_materials: Optional[List[str]] = None) -> Dict[str, any]:
    """
    Analyze all textures from selected objects' materials.

    Args:
        context: Blender context (must have selected_objects)
        excluded_materials: List of material names to exclude (optional)

    Returns:
        dict: {
            'total': int,
            'above_target': list of image objects,
            'sizes': dict mapping image names to (width, height),
            'has_alpha': dict mapping image names to bool,
            'webp_supported': bool
        }
    """
    images = get_all_texture_images(context, excluded_materials=excluded_materials)

    result = {
        'total': len(images),
        'above_target': [],
        'sizes': {},
        'has_alpha': {},
        'webp_supported': is_webp_supported()
    }

    for image in images:
        width, height = get_texture_size(image)
        result['sizes'][image.name] = (width, height)
        result['has_alpha'][image.name] = has_transparency(image)

        # Check if above 1k resolution
        if max(width, height) > 1024:
            result['above_target'].append(image)

    return result
