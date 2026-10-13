"""Framo drives Blender: the command channel and the scene builder.

``scene_spec`` (validation) and ``asset_source`` (where assets may come from)
are pure Python. ``scene_builder`` and ``render`` need bpy but nothing from the
HTTP side, so they can run in a headless Blender too. ``ops`` is the fixed list
of operations; ``commands`` is the queue the HTTP thread hands them through.
"""
