# Changelog

All notable changes to Framo Bridge will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.5.0] - 2026-10-04

### Security
- **The local server now answers only Framo.** Until now it told every browser that any website could read its answers, so any page you visited while Blender was open could download the last model you sent to Framo (`/latest-model`) and change which Framo user Blender showed as connected (`/connect-user`). Every route now checks who is asking before it does anything:
  - the request must come from Framo itself (`https://framo.app`, `https://www.framo.app`) or a local Framo dev server (`http://localhost:<port>`, `http://127.0.0.1:<port>`); anything else, including a request with no origin, gets `403` and nothing else;
  - it must be addressed to `localhost:8080` or `127.0.0.1:8080`, which stops a website from reaching the server under its own domain name (DNS rebinding);
  - the answer names that one origin instead of `*`.
  Two things still work without an origin: `GET /ping` (the health check, unreadable to other sites) and the add-on's own upload of an exported model from inside Blender.

### Added
- **Framo can drive Blender (off by default).** A new switch in the Framo Bridge panel, "Allow Framo to control Blender", lets a Framo agent read your scene, build in it, run Python with the full Blender API, take screenshots (flat for layout, or rendered with the scene's own engine for light and materials) and render clips. The switch is off every time Blender starts and is never saved in your .blend file; with it off, Framo's commands are refused. The panel shows the last command Framo ran.
- **Full Python access while the switch is on.** Framo's agent can run any Python in Blender, so it can model, texture, light and set up renders like a person at the keyboard. That also means it can change anything in the open file and read or write files on your computer: switch control on only while you want Framo working in Blender. Every step is one undo step (Ctrl+Z).
- **The scene builder keeps your objects apart.** What it builds goes into a "Framo Scene" collection and is marked as Framo's, and it never moves, edits or deletes anything else. (Python is not bound by this.)
- **Scene builder.** Boxes, spheres, cylinders, cones and planes in metres with coloured materials, sun/point/area/spot lights, a camera that moves through keyframes while looking at a target, a world colour and the render's frame range, frame rate and resolution. Sending the same scene twice changes nothing; sending an edited scene changes only what was edited.
- **Library assets.** Models from Framo's asset library are downloaded only from Framo's own storage (https, `*.convex.cloud` / `*.convex.site`), cached by asset id in your temp folder, and placed and scaled to a given height. Downloads respect Blender's "Allow Online Access" setting.
- **Preview renders.** A clip of the camera move renders to an H.264 MP4 with Workbench, EEVEE, Cycles, or the scene's own render settings; Blender puts back every render setting it borrowed afterwards. Works with Blender 5.x's new video output settings.
- New routes: `GET /bridge-info`, `POST /command`, `GET /render/<jobId>`, `GET /render/<jobId>/file`; `POST /disconnect-user`, which Framo already calls on sign-out, now clears the connected user.

### Changed
- The server handles requests in parallel, so a long command no longer holds up `/ping` or the model routes.
- The panel redraw after Framo connects now happens on Blender's main thread instead of being scheduled from the server thread.

## [0.4.6] - 2025-11-29

### Fixed
- **Fixed panel showing wrong version** - Version constant was duplicated in two places and out of sync. Now `ADDON_VERSION` is dynamically imported from `bl_info`, ensuring single source of truth.

## [0.4.5] - 2025-11-28

### Fixed
- **Fixed missing submodules in build** - Updated build script to include all package folders (`core/`, `processing/`, `services/`, `ui/`, `utils/`) that were missing from the zip, causing "No module named 'framo_bridge.core'" error.

## [0.4.4] - 2025-11-28

### Fixed
- **Fixed addon installation error** - Changed package folder name from `framo-bridge` to `framo_bridge` (underscore instead of hyphen). Python module names cannot contain hyphens.

## [0.4.3] - 2025-11-22

### Fixed
- **GLB compression now works by default** - Fixed issue where Medium compression preset appeared selected but wasn't active until manually reselected. Draco compression settings now correctly initialize to match the default Medium preset.

## [0.4.2] - 2025-11-22

### Changed
- **Renamed temp objects** - Temporary proxy objects now use `FRAMO_` prefix instead of `PROXY_`
- **Simplified update UI** - Shortened update success message in Preferences for narrow panels

## [0.4.1] - 2025-11-22

### Fixed
- **Critical crash fix** - Resolved Blender crash after export when many objects are selected
  - Root cause: Selection restoration triggered immediately after mesh data swaps caused Blender to access invalid state
  - Solution: Deferred both cleanup and selection restoration to timer callbacks that run after the operator returns
  - Export now reliably completes without crashes regardless of selection size

### Changed
- **Deferred cleanup system** - Proxy objects and temporary meshes are now cleaned up via timer callback (0.1s delay)
- **Deferred selection restore** - Original selection is restored via timer callback (0.2s delay) after cleanup completes
- **Improved state management** - All object references now stored by name to prevent stale reference issues
- **Logging improvements** - Added immediate-flush file logging to `logs/framo.log` for debugging crash scenarios

### Technical Details
- Updated `context_managers.py` - `preserve_blender_state` now uses deferred restoration
- Updated `export_service.py` - Cleanup moved to `deferred_cleanup()` timer callback
- Updated `decimation.py`, `uv_atlas.py` - Store object names instead of references for state restoration
- Silenced `clear_export_status` timer exceptions (Blender context restriction, non-critical)

## [0.4.0] - 2025-01-13

### Added - UV Atlas System (New Feature)
- **Material-based UV atlas packing** - Intelligent grouping and packing system to reduce draw calls
  - Automatically groups objects sharing the same material
  - Creates optimized UV atlases using Lightmap Pack algorithm
  - Significantly improves rendering performance in game engines and WebGL
  - Smart fallback to individual unwrapping for small groups or complex cases
- **Advanced UV atlas features**
  - Configurable minimum group size threshold (default: 2 objects)
  - Adjustable atlas texture size (default: 1024px)
  - Customizable margin between UV islands (default: 0.05)
  - Preserves original objects and UV maps (non-destructive workflow)
  - Respects existing UV maps on objects
- **Comprehensive statistics and reporting**
  - Tracks atlases created and objects packed
  - Reports individual unwraps and skipped objects
  - Detailed console output with progress indicators
  - Success/failure tracking for each operation
- **Intelligent material grouping**
  - Identifies primary (most-used) material per object
  - Groups objects by shared materials automatically
  - Filters groups by minimum size threshold
  - Falls back to individual processing for small groups

### Technical Details
**New Files:**
- `uv_atlas.py` - Material-based UV atlas module (505 lines)
  - `has_uv_map()` - Check if object has UV map
  - `get_primary_material()` - Get most-used material from object
  - `group_objects_by_material()` - Group objects by shared materials
  - `create_temp_joined_mesh()` - Join objects for atlas creation
  - `apply_lightmap_pack()` - Apply Lightmap Pack UV unwrapping
  - `smart_uv_unwrap_individual()` - Fallback individual unwrapping
  - `auto_unwrap_with_atlasing()` - Main entry point with full control

**Benefits:**
- ✅ Reduces draw calls by grouping objects with same material
- ✅ Optimizes UV packing for better texture utilization
- ✅ Improves WebGL and game engine performance
- ✅ Non-destructive workflow preserves original scene
- ✅ Smart fallback system handles edge cases gracefully

## [0.3.0] - 2025-01-12

### Added - Subdivision Control (New Feature)
- **Individual subdivision overrides** - Advanced per-object subdivision control system
  - Global override mode with single slider (applies to all objects)
  - Individual override mode with per-object sliders (up to level 6)
  - Checkbox toggles between global and individual control
  - Smart logic: only reduces subdivision, never increases
  - Prevents accidental over-subdivision during export
- **Enhanced subdivision UI** - Professional collapsible interface
  - Summary showing count of affected objects
  - Expandable list with all objects and their settings
  - Visual distinction between global and individual overrides
  - Sliders auto-initialize with object's current subdivision level

### Fixed
- **Instance export compatibility** - Collection instances now export correctly with mesh objects
- **Update system stability** - Improved cross-platform update installation

### Changed
- **Subdivision defaults** - Optimized default values for better workflow
  - Global override default changed from 3 to 2
  - Decimation disabled by default for cleaner exports
  - Maximum global override reduced to 4 (individual can still reach 6)

## [0.2.12] - 2025-01-11

### Fixed
- **Instance export bug** - Fixed issue where collection instances were not exported when selected together with mesh objects
  - When decimation or UV unwrapping was enabled, only mesh objects were being selected for export
  - Non-mesh objects (like collection instances) were deselected during temp copy creation and never re-selected
  - Now properly preserves and includes all non-mesh objects in the export selection
  - Fix ensures both mesh objects and instances are exported correctly when selected together

## [0.2.11] - 2025-01-11

### Changed - UI and Defaults
- **Renamed "Compression" to "GLB Compression"** - More descriptive label for compression settings
- **Decimation disabled by default** - Decimation is now toggled off by default for cleaner exports
- **Subdivision override default set to 2** - Changed default subdivision override level from 3 to 2

## [0.2.10] - 2025-01-11

### Changed - Update System UI
- **Improved update success message** - Cleaner post-installation UI
  - Combined success message into single line
  - Removed red alert styling from success message
  - Hide "Update Available" header and "Install Update Now" button after successful installation
  - Only show success message after installation completes

## [0.2.9] - 2025-01-11

### Added - Update System
- **Update notification in main panel** - Update alerts now appear in the main Framo Bridge sidebar
  - Prominent update notification box at top of panel
  - Shows update version and install button
  - Real-time download and installation progress
  - Success message with restart instructions
- **Automatic update checks** - Updates are checked automatically on every Blender startup
  - No configuration needed - always enabled
  - Runs in background (non-blocking)
  - Silent operation with clear notifications when updates are found

### Added - Subdivision Override
- **Individual subdivision overrides** - Set custom subdivision levels per object
  - Checkbox to toggle between global override (checked) and individual override (unchecked)
  - Individual override sliders for each object (max level 6)
  - Sliders initialize with object's actual subdivision level
  - Objects with individual overrides are not affected by global override
- **Improved subdivision override logic** - Only reduces subdivision, never increases
  - Override only applies if override value is smaller than object's current level
  - Objects with lower subdivision levels remain unchanged
  - Prevents accidental subdivision increases during export
- **Enhanced UI** - Collapsible dropdown showing all affected objects
  - Summary shows count of objects that will be overridden
  - Expandable list with checkboxes and individual override controls
  - Clear visual distinction between global and individual overrides

### Changed - Update System
- **Simplified Preferences** - Removed "Automatically check for updates" option
  - Update checks are now always enabled by default
  - Cleaner Preferences UI with just manual check button
  - Status display shows checking/available/up-to-date states
- **Enhanced restart messaging** - Clear instructions after update installation
  - Prominent success message in main panel and Preferences
  - Shows restart requirement with alternative option (disable/re-enable addon)
  - Formatted console messages for better visibility
- **Subdivision override slider** - Reduced global override max from 6 to 4
  - Individual overrides can still go up to level 6
  - Better default range for most use cases

### Fixed - Update System
- **Installation flow** - Improved update installation process
  - Better error handling and user feedback
  - Clear progress indicators during download and installation
  - Success confirmation with restart instructions

## [0.2.7] - 2025-01-11

### Testing
- **Test release** - Testing auto-installation feature

## [0.2.6] - 2025-01-11

### Added - Update System
- **Auto-installation** - Updates are now automatically downloaded and installed when detected
  - Automatic download and installation on update detection
  - Falls back to manual installation if auto-installation fails
  - Seamless update experience
- **"Install Update Now" button** - Manual installation option in Preferences
  - Shows download progress (percentage)
  - Shows installation status
  - Available when update is detected
  - Button disabled during download/install process

### Changed - Update System
- **Enhanced update flow** - Improved user experience
  - Automatic installation eliminates need for manual steps
  - Clear status indicators during download and installation
  - Better error handling with fallback to manual installation

## [0.2.5] - 2025-01-11

### Testing
- **Test release** - Testing auto-update system functionality

## [0.2.4] - 2025-01-11

### Fixed - Auto-Update System
- **macOS autoupdate fixes** - Comprehensive fixes for update system on macOS
  - Fixed addon directory detection using `__file__` method (works without bpy.context)
  - Added multiple fallback methods for finding addon directory
  - Fixed context access issues in background threads
  - Improved error handling and logging throughout update process
- **"Check for Updates Now" button** - Now works correctly on all platforms
  - Added user-visible status display in Preferences UI
  - Shows checking status, update availability, and error messages
  - Enhanced error reporting with detailed console logging
  - Better network error handling with macOS-specific guidance
  - Added User-Agent header for GitHub API requests
  - Improved exception handling and error propagation

### Changed - Update System
- **Enhanced debugging** - Detailed logging at each step of update process
  - Console logs show API URL, connection status, response details
  - Full traceback on errors for easier troubleshooting
  - Clear error messages for network, HTTP, and parsing issues

## [0.2.3] - 2025-01-11

### Summary
Comprehensive update consolidating all texture optimization improvements, auto-update fixes, and enhanced mesh processing capabilities. This release completes the transition to dependency-free texture handling and improves cross-platform compatibility.

### Added - Texture System
- **Native texture scaling** (`texture_scaler.py`) - Zero dependencies required
  - Uses Blender's built-in `image.scale()` function
  - Automatic aspect ratio preservation
  - Non-destructive workflow
  - WebP format detection (Blender 3.0+)
  - Smart fallback system to Pillow if needed
- **Two-stage optimization pipeline**
  - Stage 1: Pre-export scaling (texture_scaler.py)
  - Stage 2: WebP compression (Blender's glTF exporter)
- Export messages now show method used (Native/Pillow)

### Added - Subdivision Control
- **Subdivision override system** for export-time control
  - Temporarily set subdivision levels during export
  - Individual object override capabilities
  - Exclude specific objects from subdivision changes
  - Non-destructive (original modifiers restored after export)

### Changed - Update System
- **Silent auto-updates** - No more intrusive UI
  - Removed "Update Now" button from panel
  - Removed progress bars and notification messages
  - Updates happen automatically in background on Blender startup
  - Cleaner, less intrusive user experience

### Fixed - Core Issues
- **macOS auto-update support** - Now works on all platforms
  - Platform-independent addon directory detection
  - Dynamic path resolution using `addon_prefs.module.__file__`
  - Multiple fallback locations checked
  - No more hardcoded paths
- **Non-manifold geometry handling** in decimation
  - Aggressive merge-by-distance strategy
  - Progressive distance attempts (0.001, 0.01, 0.1)
  - Dissolve degenerate faces and edges
  - Better error reporting and recovery

### Removed
- **Pillow dependency** (optional fallback only)
  - All texture scaling now uses Blender native functions
  - WebP conversion handled by glTF exporter
  - No pip install required for core functionality
- Manual update UI components
  - Update notification box
  - View Changes button
  - Download progress indicators
  - Error retry UI

### Technical Details
**New Files:**
- `texture_scaler.py` - Native texture scaling module (600+ lines)
  - `compress_image_native()` - Compression with optional scaling
  - `scale_image_native()` - Pure scaling function
  - `process_textures_native()` - Batch processing
  - `is_webp_supported()` - Version detection
  - `get_all_texture_images()` - Material traversal
  - `replace_image_in_materials()` - Reference updates

**Updated Files:**
- `__init__.py` - Core addon integration
  - Smart texture scaler detection and fallback
  - Subdivision override UI and operators
  - Removed update notification UI (lines 1014-1060)
  - Added SubdivExcludeObject and SubdivIndividualOverride property groups
- `updater.py` - Cross-platform update support
  - New `get_addon_directory()` method for dynamic path detection
  - Platform-independent installation
  - Multiple fallback location checks
- `texture_analyzer.py` - Legacy Pillow support
  - Added missing `is_webp_available()` function
  - Retained as fallback option
- `decimation.py` - Enhanced mesh processing
  - Improved non-manifold geometry detection
  - Progressive merge strategies
  - Better error handling
- `build_zip.py` - Distribution configuration
  - Added `texture_scaler.py` to include list
  - Updated comments for clarity
- `material_analyzer.py` - Collection instance support
  - Added support for analyzing materials in collection instances
- `README.md` - Complete documentation rewrite
  - Two-stage optimization pipeline explained
  - Native vs Pillow comparison
  - Installation simplified (no dependencies section)

**Export Pipeline:**
```
1. Pre-Export Scaling (texture_scaler.py)
   └─> Scales 4K → 1K using image.scale()
        └─> Native Blender, no dependencies

2. WebP Compression (glTF Exporter)
   └─> Converts all textures to WebP
        └─> export_image_format: 'WEBP'
```

**Platform Support:**
- ✅ Windows - Fully tested
- ✅ macOS - Auto-update now works
- ✅ Linux - All features functional

**Breaking Changes:**
- None - All changes are backward compatible

**Migration Notes:**
- Users with Pillow installed: No changes needed, will use native scaler
- Users without Pillow: No action required, everything works
- macOS users: Auto-updates will now work correctly

## [0.2.2] - 2025-01-11

### Fixed
- **macOS auto-update support** - Fixed auto-update system not working on macOS
  - Added platform-independent addon directory detection
  - New `get_addon_directory()` method finds addon path dynamically
  - No longer assumes hardcoded addon path
  - Works on all platforms: Windows, macOS, Linux
  - Falls back to multiple standard addon locations if needed

### Technical
- Updated `updater.py`: Added `UpdateInstaller.get_addon_directory()` method
- Uses `addon_prefs.module.__file__` to get actual addon location
- Checks multiple possible addon locations (addons/, addons_contrib/)
- Fixes line 371 hardcoded path issue that caused macOS failures

## [0.2.1] - 2025-01-11

### Changed
- **Streamlined update system** - Removed manual update UI from panel
  - Updates now happen silently in background on Blender startup
  - No more "Update Now" button or update notifications in UI
  - Cleaner, less intrusive user experience
  - Auto-update still fully functional - just invisible to user

### Removed
- Manual update notification UI (Update Now button, progress bars, error messages)
- View Changes button for update changelogs
- All update-related UI elements from main panel

### Technical
- Kept automatic update check on startup (`check_pending_update_on_startup`)
- Updates download and install silently without user interaction
- Panel UI simplified by removing lines 1014-1060 (update notification box)

## [0.2.0] - 2025-01-11

### Changed
- **Removed Pillow dependency** - All texture optimization now uses Blender's native functions
- Texture scaling handled by new `texture_scaler.py` module (no external dependencies required)
- WebP compression now exclusively handled by Blender's glTF exporter via `export_image_format: 'WEBP'`
- Export messages now show which method was used (Native/Pillow) for transparency

### Added
- **Native texture scaling** with `texture_scaler.py` module
  - Uses Blender's built-in `image.scale()` function
  - No external dependencies (Pillow, numpy, etc.)
  - Maintains aspect ratios automatically
  - Non-destructive workflow (creates new images, originals preserved)
  - WebP format support detection (Blender 3.0+)
- **Smart fallback system** - Automatically uses native scaler, falls back to Pillow if unavailable
- **Two-stage optimization pipeline**:
  - Stage 1: Pre-export scaling (texture_scaler.py)
  - Stage 2: WebP compression (Blender's glTF exporter)

### Technical
- New file: `texture_scaler.py` - Dependency-free texture scaling module
- Updated: `texture_analyzer.py` - Retained as legacy fallback, added missing `is_webp_available()` function
- Updated: `__init__.py` - Integrated native scaler with smart fallback
- Updated: `build_zip.py` - Includes texture_scaler.py in distribution
- Updated: `README.md` - Documented native approach and two-stage pipeline

### Benefits
- ✅ No installation required - Works out of the box
- ✅ Faster setup - No pip install needed
- ✅ More reliable - No Pillow version conflicts
- ✅ Smaller footprint - No external libraries
- ✅ Easier maintenance - Pure Blender code

## [0.1.1] - 2025-01-10

### Fixed
- Test release to validate auto-update system functionality

## [0.1.0] - 2025-01-10

### Added
- **Auto-Update System**: Seamless updates directly from within Blender
  - Check for updates from GitHub releases
  - One-click download and installation
  - Automatic update check on startup (configurable)
  - View changelog before updating
  - Update notification UI in main panel
  - Restart-based installation for safety
  - Background download with progress indicator
- **Mesh Decimation**: Advanced polygon reduction with multiple algorithms
  - Collapse decimation (edge collapse)
  - Un-Subdivide (remove subdivision levels)
  - Planar decimation (architectural models)
  - Feature preservation (sharp edges, UV seams)
  - Adaptive decimation based on complexity
  - Real-time polygon count reporting
- **Texture Optimization**: Automatic texture scaling and format conversion
  - Scale textures to target size (2K, 1K, 512px, 256px)
  - Automatic WebP conversion via Blender's glTF exporter
  - Material exclusion list for textures that shouldn't be optimized
  - Non-destructive workflow (originals preserved)
  - Real-time texture analysis in UI
- **Material Readiness Analyzer**: Pre-export material validation
  - Detects unsupported shader nodes
  - Identifies missing textures
  - Validates PBR material setup
  - One-click material replacement
  - Opens materials directly in Shading workspace
  - Expandable material issue details
- **Auto UV Unwrapping**: Automatic UV map generation for meshes without UVs
  - Smart UV Project algorithm
  - Configurable angle limits and island margins
  - Statistics reporting
- **Advanced Compression Presets**:
  - None, Low, Medium, High, Custom
  - Custom quantization controls for position/normal/texcoord
- **Dependency Management**: Automatic installation of Python packages
  - One-click Pillow installation
  - Installation status indicators
  - Error handling and fallback options
- **User Connection System**: framo.app integration
  - User authentication display
  - Connect/disconnect status
  - Export blocked until user connects
- **Export Status Indicators**: Real-time export progress
  - Loading indicators during export
  - Detailed status messages
  - Auto-clearing success/error messages
- **Export Metadata**: Rich metadata sent with models
  - Compression settings
  - Decimation statistics
  - Material analysis results
  - Object counts and file sizes

### Changed
- **UI Improvements**:
  - Reorganized export settings into logical sections
  - Material Readiness panel with expandable details
  - Disabled UI sections when no objects selected
  - Better visual hierarchy with boxes and icons
  - Export button only enabled when user connected and materials ready
- **Non-Destructive Workflow**: All optimizations work on temporary copies
  - Original geometry never modified
  - Original textures restored after export
  - Temp objects automatically cleaned up
- **Enhanced Error Handling**:
  - Detailed error messages
  - Graceful fallbacks when modules unavailable
  - User-friendly warnings
- **Export Process**: More robust and informative
  - Better progress reporting
  - Comprehensive metadata
  - Material validation before export

### Fixed
- Server stability improvements
- Better cleanup of temporary data
- Proper handling of missing dependencies
- Material analysis edge cases

## [0.1.0] - 2025-10-XX

### Added
- Initial release
- Basic GLB export functionality
- Draco mesh compression support
- Built-in HTTP server (localhost:8080)
- Web viewer with Three.js
- Real-time model preview in browser
- Basic compression settings
- File size reporting

### Features
- One-click export from Blender to web
- Support for selected objects or full scene
- Automatic file size optimization
- Server endpoints for model retrieval
- Professional studio lighting in viewer
- Auto-centering and scaling

---

## Version Numbering

- **Major version** (X.0.0): Breaking changes, major feature overhauls
- **Minor version** (0.X.0): New features, non-breaking changes
- **Patch version** (0.0.X): Bug fixes, minor improvements

[0.1.0]: https://github.com/r0m4nm/framo-bridge/releases/tag/v0.1.0
