"""Scene-wide ruler appearance and temporary, per-ruler display options."""

import re
from decimal import Decimal, ROUND_HALF_UP, localcontext

import bpy
from bpy.props import (BoolProperty, FloatProperty, FloatVectorProperty,
                       IntProperty, PointerProperty, StringProperty)

from .measure import format_length


DEFAULT_COLOR = (1.0, 0.75, 0.16, 1.0)
_DECIMAL_NUMBER = re.compile(r'(?<![\w.])(-?\d+)\.(\d+)(?!\d)')


def _redraw_views(_self=None, _context=None):
    manager = bpy.context.window_manager
    if manager is None:
        return
    for window in manager.windows:
        for area in window.screen.areas:
            if area.type == 'VIEW_3D':
                area.tag_redraw()


class FinalDimensionsRulerAppearance(bpy.types.PropertyGroup):
    color: FloatVectorProperty(
        name='Line color', description='Default color for ruler lines',
        subtype='COLOR', size=4, min=0.0, max=1.0, default=DEFAULT_COLOR,
        update=_redraw_views)
    inactive_brightness: FloatProperty(
        name='Inactive brightness',
        description='Brightness of rulers other than the selected one',
        min=0.0, max=1.0, default=0.65, subtype='FACTOR', update=_redraw_views)
    show_appearance: BoolProperty(name='Appearance', default=False)


def _appearance(scene):
    return getattr(scene, 'final_dimensions_ruler_appearance', None)


def line_color(scene, item, selected):
    """Return RGBA for a ruler line; inactive dimming leaves alpha alone."""
    settings = _appearance(scene)
    custom = getattr(item, 'color', None)
    base = custom if custom is not None else (
        settings.color if settings is not None else DEFAULT_COLOR)
    brightness = 1.0 if selected else (
        settings.inactive_brightness if settings is not None else 0.65)
    return tuple(float(channel) * brightness for channel in base[:3]) + (float(base[3]),)


def distance_text(scene, item, distance):
    """Only changes the displayed value; stored distance remains untouched."""
    if distance is None:
        return 'Pick points'
    precision = max(0, min(6, int(getattr(item, 'precision', 3))))
    # Format with guard digits, then round the displayed number explicitly.
    # Binary floats otherwise turn an exact-looking 2.345 mm into 2.34 mm.
    value = format_length(scene, distance, precision=precision + 8)
    quantum = Decimal(1).scaleb(-precision)

    def round_number(match):
        with localcontext() as decimal_context:
            decimal_context.prec = max(28, len(match.group(0)) + precision + 3)
            number = Decimal(match.group(0)).quantize(quantum, rounding=ROUND_HALF_UP)
        result = f'{number:.{precision}f}'
        return result.rstrip('0').rstrip('.') if precision and getattr(item, 'trim_zeros', True) else result

    return _DECIMAL_NUMBER.sub(round_number, value)


def label_text(scene, item, index, distance):
    """Visible label for both viewport and ruler list rows."""
    value = distance_text(scene, item, distance)
    name = str(getattr(item, 'label', '')).strip()
    return f'{name}: {value}' if name else f'{index + 1} d: {value}'


class VIEW3D_OT_final_dimensions_ruler_style(bpy.types.Operator):
    """Edit the selected ruler's presentation without touching its anchors."""

    bl_idname = 'view3d.final_dimensions_ruler_style'
    bl_label = 'Label & Color…'
    bl_options = {'INTERNAL'}

    label: StringProperty(name='Name', description='Label shown before the distance')
    precision: IntProperty(name='Decimals', min=0, max=6, default=3)
    trim_zeros: BoolProperty(name='Hide trailing zeros', default=True)
    use_custom_color: BoolProperty(name='Custom line color', default=False)
    color: FloatVectorProperty(name='Line color', subtype='COLOR', size=4,
                               min=0.0, max=1.0, default=DEFAULT_COLOR)

    @classmethod
    def poll(cls, context):
        from . import ruler
        manager = ruler.collection(context.window)
        return manager is not None and manager.selected is not None

    def invoke(self, context, _event):
        from . import ruler
        manager = ruler.collection(context.window)
        item = manager.selected if manager is not None else None
        if item is None:
            return {'CANCELLED'}
        self._target_manager = manager
        self._target_item = item
        self.label = getattr(item, 'label', '')
        self.precision = getattr(item, 'precision', 3)
        self.trim_zeros = getattr(item, 'trim_zeros', True)
        custom = getattr(item, 'color', None)
        self.use_custom_color = custom is not None
        self.color = custom if custom is not None else line_color(context.scene, item, True)
        return context.window_manager.invoke_props_dialog(self, width=320)

    def draw(self, _context):
        layout = self.layout
        layout.prop(self, 'label')
        layout.prop(self, 'precision')
        layout.prop(self, 'trim_zeros')
        layout.prop(self, 'use_custom_color')
        if self.use_custom_color:
            layout.prop(self, 'color')

    def execute(self, context):
        from . import ruler
        manager = ruler.collection(context.window)
        target_manager = getattr(self, '_target_manager', None)
        item = getattr(self, '_target_item', None)
        if manager is not target_manager or item is None or not any(
                candidate is item for candidate in manager.items):
            self.report({'WARNING'}, 'Ruler no longer exists')
            return {'CANCELLED'}
        item.label = self.label.strip()
        item.precision = self.precision
        item.trim_zeros = self.trim_zeros
        item.color = tuple(self.color) if self.use_custom_color else None
        for area in context.window.screen.areas:
            if area.type == 'VIEW_3D':
                area.tag_redraw()
        self._target_manager = None
        self._target_item = None
        return {'FINISHED'}

    def cancel(self, _context):
        self._target_manager = None
        self._target_item = None


def draw_defaults(layout, context):
    settings = _appearance(context.scene)
    if settings is None:
        return
    box = layout.box()
    row = box.row()
    row.prop(settings, 'show_appearance', text='Appearance', emboss=False,
             icon='TRIA_DOWN' if settings.show_appearance else 'TRIA_RIGHT')
    if settings.show_appearance:
        box.prop(settings, 'color')
        box.prop(settings, 'inactive_brightness')


def draw_selected(layout, context):
    from . import ruler
    manager = ruler.collection(context.window)
    row = layout.row()
    row.enabled = manager is not None and manager.selected is not None
    row.operator(VIEW3D_OT_final_dimensions_ruler_style.bl_idname,
                 text='Label & Color…', icon='GREASEPENCIL')


_CLASSES = (FinalDimensionsRulerAppearance, VIEW3D_OT_final_dimensions_ruler_style)


def register():
    for cls in _CLASSES:
        bpy.utils.register_class(cls)
    bpy.types.Scene.final_dimensions_ruler_appearance = PointerProperty(
        type=FinalDimensionsRulerAppearance)


def unregister():
    del bpy.types.Scene.final_dimensions_ruler_appearance
    for cls in reversed(_CLASSES):
        bpy.utils.unregister_class(cls)
