"""A fake ``UILayout`` that validates every name against the live RNA.

Background Blender never draws a UI, so ``draw()`` is the one method that ships
untested -- and one typo'd property name in it makes Blender raise from inside
the draw call and the *whole panel* goes blank. Same for a wrong icon id. Calling
``draw()`` yourself against this stub turns both into an exception at the line
that caused them.

    from stub_layout import StubLayout, FakeSelf, Proxy, exercise_panel

    log = exercise_panel(MY_PT_panel, bpy.context)
    assert ("prop", "MySettings", "tolerance") in log

Import it from a script run inside Blender; it needs ``bpy``.
"""

import bpy

#: Every icon identifier Blender will accept, read off the RNA rather than
#: hardcoded, so it stays correct across versions.
ICONS = set(
    bpy.types.UILayout.bl_rna.functions["prop"].parameters["icon"].enum_items.keys()
)


class StubLayout:
    """Records draw calls; raises on any name the RNA does not have."""

    def __init__(self, log=None):
        self.log = [] if log is None else log
        self.alert = False
        self.enabled = True
        self.active = True
        self.scale_x = 1.0
        self.scale_y = 1.0
        self.use_property_split = False
        self.use_property_decorate = False
        self.operator_context = "INVOKE_DEFAULT"

    # -- validation --------------------------------------------------------

    @staticmethod
    def _check_icon(kwargs):
        icon = kwargs.get("icon")
        if icon is not None and icon != "NONE" and icon not in ICONS:
            raise ValueError(f"no such icon {icon!r}")

    # -- recorded calls ----------------------------------------------------

    def prop(self, data, name, **kwargs):
        if name not in data.bl_rna.properties:
            raise AttributeError(
                f"{data.bl_rna.identifier} has no property {name!r}"
            )
        self._check_icon(kwargs)
        self.log.append(("prop", data.bl_rna.identifier, name))

    prop_search = prop_menu_enum = prop_with_popover = prop

    def operator(self, idname, **kwargs):
        module, _, func = idname.partition(".")
        if not hasattr(getattr(bpy.ops, module, None), func):
            raise AttributeError(f"no operator {idname!r}")
        self._check_icon(kwargs)
        self.log.append(("operator", idname))
        return _OperatorProps()

    operator_menu_enum = operator

    def label(self, **kwargs):
        self._check_icon(kwargs)
        self.log.append(("label", kwargs.get("text", "")))

    def menu(self, menu, **kwargs):
        self._check_icon(kwargs)
        self.log.append(("menu", menu))

    def template_ID(self, data, name, **kwargs):
        self.log.append(("template_ID", name))

    def separator(self, **kwargs):
        pass

    separator_spacer = separator

    # -- containers --------------------------------------------------------

    def _child(self):
        return StubLayout(self.log)

    def row(self, *a, **k):
        return self._child()

    column = box = split = column_flow = grid_flow = row

    def panel(self, *a, **k):
        # 4.1+ layout.panel() returns (header, body); body is None when closed
        child = self._child()
        return child, child


class _OperatorProps:
    """What layout.operator() hands back: a bag you set properties on."""

    def __setattr__(self, key, value):
        object.__setattr__(self, key, value)


class FakeSelf:
    """bpy Panel subclasses cannot be instantiated; draw() only reads .layout."""

    def __init__(self, layout):
        self.layout = layout


class Proxy:
    """Real struct for attribute reads, stub layout for drawing.

    AddonPreferences.draw() reads its own properties off ``self`` as well as
    ``self.layout``, so FakeSelf is not enough for it.
    """

    def __init__(self, real, layout):
        object.__setattr__(self, "_real", real)
        object.__setattr__(self, "layout", layout)

    def __getattr__(self, key):
        return getattr(object.__getattribute__(self, "_real"), key)


def exercise_panel(cls, context, settings=None, **overrides):
    """Draw one panel and return the call log.

    ``overrides`` set properties on ``settings`` for the duration, so warning
    branches get drawn too -- and are put back in ``finally``. Without the
    restore, exercising one branch silently changes what every later check tests.
    """
    saved = {}
    if overrides:
        if settings is None:
            raise ValueError("overrides need a settings object")
        saved = {k: getattr(settings, k) for k in overrides}
        for key, value in overrides.items():
            setattr(settings, key, value)
    try:
        log = []
        cls.draw(FakeSelf(StubLayout(log)), context)
        assert log, f"{getattr(cls, 'bl_idname', cls.__name__)} drew nothing"
        return log
    finally:
        for key, value in saved.items():
            setattr(settings, key, value)


def exercise_preferences(prefs, context):
    log = []
    type(prefs).draw(Proxy(prefs, StubLayout(log)), context)
    assert log, "preferences drew nothing"
    return log
