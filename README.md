# Framo Bridge - Blender GLB Exporter

A powerful Blender addon for exporting optimized 3D models directly to web applications with advanced compression, mesh decimation, UV atlas packing, and texture optimization.

## Features

### ✅ **Core Export Features**
- One-click GLB export from Blender to web viewer
- Built-in HTTP server (localhost:8080)
- Real-time model viewing in browser with Three.js
- Support for selected objects or full scene export
- Automatic file size reporting

### ✅ **Advanced Compression**
- **Draco Mesh Compression** with quality presets
- **Custom compression settings** for position, normal, and UV quantization
- **Compression presets**: None, Low, Medium, High, Custom
- Typically achieves 50-90% file size reduction

### ✅ **Mesh Optimization**
- **Smart Decimation** with multiple algorithms (Collapse, Un-Subdivide, Planar)
- **Adaptive decimation** based on mesh complexity
- **Feature preservation** (sharp edges, UV seams)
- **Subdivision control** - per-object subdivision overrides during export
- **Real-time polygon count reporting**

### ✅ **UV Optimization**
- **Material-based UV Atlas Packing** - Groups objects with shared materials into optimized UV atlases
- **Reduces draw calls** significantly in game engines and WebGL
- **Lightmap Pack algorithm** for optimal UV island packing
- **Smart UV unwrapping** for objects without UV maps
- **Configurable atlas settings** (min objects, texture size, margins)

### ✅ **Texture Optimization**
- **Native texture scaling** using Blender's built-in functions (NO dependencies required)
- **WebP compression** handled automatically by Blender's glTF exporter
- **Scale textures** to target size (2K, 1K, 512px, 256px)
- **Material exclusion list** for textures that shouldn't be optimized
- **Non-destructive workflow** - originals preserved

### ✅ **Material Optimization**
- **Automatic unused material removal** - removes materials not applied to any faces
- **Massive file size reduction** (up to 10x for objects with texture-heavy unused materials)
- **Runs automatically in background** - zero configuration needed
- **Non-destructive** - only affects export, originals untouched

### ✅ **Web-Ready Features**
- **HDRI environment lighting** for realistic presentation
- **Draco decompression** support in web viewer
- **Professional studio lighting** setup
- **Auto-centering and scaling** of imported models
- **Real-time statistics** (file size, triangle count)

## Installation

### Simple Installation (Recommended)

1. **Download** the latest release ZIP from [GitHub Releases](https://github.com/r0m4nm/framo-bridge/releases)
2. **In Blender:**
   - Edit → Preferences → Add-ons
   - Click "Install..."
   - Select the downloaded ZIP file
   - Enable "Import-Export: Framo Bridge"

**That's it!** No dependencies required - the addon uses Blender's native functions for all operations.

### Verify Installation

1. In 3D Viewport, press `N` to open sidebar
2. Look for "Framo Bridge" tab
3. Server Status should show "Running"
4. All features work out of the box - no additional setup needed

## Usage

### Basic Export

1. **Select objects** you want to export (or select nothing for entire scene)
2. **Open the "Framo Bridge" tab** in 3D Viewport sidebar (press `N`)
3. **Configure settings** as needed:
   - **Compression Preset**: Choose quality level
   - **Mesh Optimization**: Enable decimation if needed
   - **UV Atlasing**: Group objects by material to reduce draw calls
   - **Texture Optimization**: Scale large textures for web
4. **Click "Export to Web"**
5. **Open `test_viewer.html`** in your browser to see the result

### Advanced Settings

#### **Compression Options**
- **None**: Original quality, larger files
- **Low**: Minimal compression, high quality
- **Medium**: Balanced (recommended for web)
- **High**: Maximum compression, smaller files
- **Custom**: Manual control over all parameters

#### **Mesh Optimization**
- **Decimate Ratio**: 0.5 = 50% face reduction
- **Decimate Type**:
  - Collapse (best for most cases)
  - Un-Subdivide (for over-subdivided models)
  - Planar (for architectural models)
- **Adaptive Decimation**: Automatically adjusts based on complexity
- **Subdivision Override**: Temporarily reduce subdivision levels during export

#### **UV Atlas Packing**
- **Enable Atlasing**: Groups objects with same material into shared UV atlases
- **Min Objects**: Minimum objects required to create an atlas (default: 2)
- **Atlas Texture Size**: Target resolution for UV packing (default: 1024px)
- **UV Margin**: Space between UV islands to prevent bleeding (default: 0.05)

#### **Texture Optimization**
- **Max Texture Size**: Target size for downscaling (4K/2K/1K/512px/256px)
- **WebP Conversion**: Automatic via Blender's glTF exporter
- **Material Exclusion**: Exclude specific materials from optimization

## Performance Optimizations

### File Size Reductions
- **Draco Compression**: 50-80% size reduction
- **Mesh Decimation**: 30-90% polygon reduction
- **Unused Material Removal**: Up to 10x reduction for objects with texture-heavy unused materials
- **Texture Optimization**: 30-50% reduction via WebP compression
- **Combined**: Often results in 90%+ file size reduction

### Web Performance
- **UV Atlasing**: Reduces draw calls by grouping objects with shared materials
- **Optimized polygon counts** for real-time rendering
- **Proper LOD support** through decimation
- **Fast loading** with compressed textures

## Technical Details

### Texture Optimization Pipeline

The addon uses a two-stage approach:

#### **Stage 1: Pre-Export Scaling** (`texture_scaler.py`)
- Scales large textures (e.g., 4K → 1K) before export
- Uses Blender's native `image.scale()` function
- **No dependencies required** - works out of the box
- Maintains aspect ratios automatically
- Non-destructive (creates new images, originals preserved)

#### **Stage 2: WebP Compression** (Blender's glTF Exporter)
- Automatically converts all textures to WebP during export
- Handled by `export_image_format: 'WEBP'` parameter
- Built into Blender's glTF exporter (Blender 3.0+)
- Supports transparency (alpha channels)
- 30-50% smaller files than JPEG/PNG

### UV Atlas System

Material-based grouping and packing:
1. **Groups objects** by their primary material
2. **Creates temporary joined meshes** for each material group
3. **Applies Lightmap Pack** algorithm for optimal UV packing
4. **Exports atlases** instead of individual objects
5. **Cleans up** temporary objects after export

Benefits:
- Fewer draw calls = better performance
- Better texture utilization
- Improved WebGL rendering
- Non-destructive workflow

### Compression Details
- **Position Quantization**: Controls vertex position precision
- **Normal Quantization**: Controls surface normal precision
- **Texture Coordinate Quantization**: Controls UV precision
- **Compression Levels**: 0-10 (higher = more compression)

### Decimation Details
- **Collapse Decimation**: Best general-purpose algorithm
- **Feature Preservation**: Maintains sharp edges and UV seams
- **Adaptive Processing**: Different settings for different complexity levels

## Framo Control

With **Allow Framo to control Blender** switched on in the panel, a Framo agent can read the scene, build in a "Framo Scene" collection, run Python with the full Blender API, take camera screenshots (flat or rendered) and render clips. Python can change anything in the open file and touch files on this computer, so the switch is off every time Blender starts and is never saved in a .blend file. Every step is one undo step.

## Server Endpoints

The server on `localhost:8080` answers only Framo (`https://framo.app`, `https://www.framo.app`, or a local dev server on `http://localhost:<port>` / `http://127.0.0.1:<port>`) and only requests addressed to `localhost:8080` / `127.0.0.1:8080`. Everything else gets `403`.

- `GET /ping` - Health check (the one route answered for any caller)
- `GET /latest-model` - Get latest GLB model (binary)
- `GET /latest-model-info` - Get model metadata (JSON)
- `POST /upload-model` - Upload GLB data (internal; Blender's own export)
- `GET /user-info`, `POST /connect-user`, `POST /disconnect-user` - The connected Framo user
- `GET /bridge-info` - Protocol and versions, and whether control is switched on
- `POST /command` - Run one Framo operation (`status`, `get_scene`, `build_scene`, `screenshot`, `render_start`, `run_python`); refused with `control_disabled` while the switch is off
- `GET /render/<jobId>`, `GET /render/<jobId>/file` - Preview render progress, and the finished MP4

## Tests

```bash
# Pure Python (no Blender): origin/host checks, scene validation, asset URLs
python3 -m unittest discover -s tests -p 'test_*.py'

# Real Blender, headless: builder, screenshot, render, HTTP server (needs port 8080 free)
/Applications/Blender.app/Contents/MacOS/Blender -b --factory-startup --python-exit-code 1 \
    --python tests/headless/run.py
```

`control/scene_spec.py` mirrors the scene format the Framo app validates (`shared/blender-scene.ts`); `tests/fixtures/` holds that parser's answers, regenerated with `node tests/fixtures/generate_scene_spec_cases.mjs <framo-app>/shared/blender-scene.ts`.

## Browser Compatibility

- Chrome 90+
- Firefox 88+
- Safari 14+
- Edge 90+
- WebGL 2.0 support required

## Troubleshooting

### Common Issues

**Server not running:**
- Disable and re-enable the addon in Blender preferences

**Port 8080 in use:**
- Close other applications using port 8080
- Restart Blender

**Model not appearing in browser:**
- Check browser console for errors
- Ensure `test_viewer.html` is opened in a web browser
- Verify server status shows "Connected"

**Export fails:**
- Ensure GLB exporter is enabled in Blender
- Check that objects have valid geometry
- Try with simpler models first

**Very large file sizes:**
- Enable Draco compression
- Use mesh decimation for high-poly models
- Enable texture optimization
- Check compression settings

## Technical Requirements

- **Blender**: 3.0+ (4.0+ recommended)
- **Browser**: Modern browser with WebGL 2.0 support
- **Network**: Local HTTP server (localhost:8080)
- **Dependencies**: None - uses Blender's native functions

## File Structure

```
framo-bridge/
├── __init__.py                    # Main addon file
├── decimation.py                  # Mesh decimation module
├── dependencies.py                # Dependency management (empty - no deps needed)
├── material_analyzer.py           # Material validation
├── material_cleaner.py            # Unused material removal
├── texture_scaler.py              # Texture scaling (native - no deps)
├── uv_unwrap.py                   # UV unwrapping utilities
├── uv_atlas.py                    # Material-based UV atlas packing
├── updater.py                     # Auto-update system
├── test_viewer.html               # Web viewer for testing
├── icons/                         # Custom icons
├── README.md                      # This file
├── INSTALL.md                     # Installation guide
├── CHANGELOG.md                   # Version history
└── LICENSE                        # MIT License
```

## Creating Distribution Zip

To create a distribution-ready zip, use the included build script:

```bash
python build_zip.py
```

This creates `builds/framo-bridge-v{version}.zip` with the correct structure.

## Contributing

Issues and pull requests welcome at: https://github.com/r0m4nm/framo-bridge

## License

MIT License - See [LICENSE](LICENSE) file for details

Copyright (c) 2025 Roman Moor

## Support

- **Issues**: https://github.com/r0m4nm/framo-bridge/issues
- **Documentation**: https://github.com/r0m4nm/framo-bridge
- **Releases**: https://github.com/r0m4nm/framo-bridge/releases

---

**Status**: Active Development
**Latest Version**: 0.5.0
**Blender**: 3.0+ (4.0+ recommended)
