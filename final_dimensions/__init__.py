"""Final Dimensions: live, non-destructive mesh dimensions in the 3D View."""

from __future__ import annotations

import bpy
import time
import textwrap
from bpy.app.handlers import persistent
from bpy.props import BoolProperty

from .measure import format_length, measure_object
from . import overlay, hover, ruler, ruler_style, offset_cut


bl_info = {
    "name": "Final Dimensions",
    "author": "Final Dimensions contributors",
    "version": (0, 8, 0),
    "blender": (4, 5, 0),
    "location": "3D View > Sidebar > Final Dimensions",
    "description": "Measure evaluated mesh dimensions and snap a ruler to final surfaces",
    "category": "3D View",
}


_TIMER_INTERVAL = 0.35
_cache = {}  # window pointer -> primitive-only measurement and context signature
_draw_interest = {}  # window pointer -> latest time this panel was drawn
_epoch = 0
_registered = False
_measuring = False


def _context_key(window, scene, view_layer, obj):
    return (
        window.as_pointer(),
        scene.as_pointer(),
        view_layer.as_pointer(),
        obj.as_pointer() if obj is not None else 0,
    )


def _signature(obj):
    if obj is None:
        return None
    return (
        obj.mode,
        obj.data.as_pointer() if obj.data is not None else 0,
        tuple(value for row in obj.matrix_world for value in row),
    )


def _tag_viewports(window):
    for area in window.screen.areas:
        if area.type == "VIEW_3D":
            area.tag_redraw()


def _panel_visible(window):
    """Only evaluate while our sidebar category is active."""
    category_available = False
    for area in window.screen.areas:
        if area.type != "VIEW_3D":
            continue
        for region in area.regions:
            if region.type != "UI":
                continue
            if hasattr(region, "active_panel_category"):
                category_available = True
                if (
                    area.spaces.active.show_region_ui
                    and region.active_panel_category == "Final Dimensions"
                ):
                    return True
    if category_available:
        return False
    # On Blender versions without the category property, a recent draw is
    # enough to keep this window active. Closed panels stop requesting work.
    return time.monotonic() - _draw_interest.get(window.as_pointer(), -1e9) < 1.0


def _timer_tick():
    global _measuring
    if not _registered:
        return None
    try:
        windows = tuple(bpy.context.window_manager.windows)
    except (AttributeError, ReferenceError):
        return _TIMER_INTERVAL

    live_windows = set()
    for window in windows:
        try:
            pointer = window.as_pointer()
            live_windows.add(pointer)
            scene = window.scene
            view_layer = window.view_layer
            obj = view_layer.objects.active
            hover.ensure_window(window)
            ruler.tick_window(window, _epoch)
            overlay_changed = False
            overlay_active = (
                obj is not None
                and obj.type == "MESH"
                and obj.mode == "EDIT"
                and bpy.context.window_manager.final_dimensions_show_overlay
                and any(
                    area.type == "VIEW_3D"
                    and area.spaces.active.overlay.show_overlays
                    for area in window.screen.areas
                )
            )
            if overlay_active:
                _measuring = True
                try:
                    with bpy.context.temp_override(
                        window=window, scene=scene, view_layer=view_layer
                    ):
                        overlay_changed = overlay.update(
                            bpy.context, window, obj, _epoch
                        )
                finally:
                    _measuring = False
            else:
                overlay_changed = overlay.clear_window(window)
            if overlay_changed:
                _tag_viewports(window)
            if not _panel_visible(window):
                continue
            key = _context_key(window, scene, view_layer, obj)
            signature = _signature(obj)
            cached = _cache.get(pointer)
            if cached and cached["key"] == key and cached["signature"] == signature and cached["epoch"] == _epoch:
                # Keep the fallback draw request alive in Blender versions
                # where the active sidebar category is not exposed to Python.
                if pointer in _draw_interest and not any(
                    hasattr(region, "active_panel_category")
                    for area in window.screen.areas
                    if area.type == "VIEW_3D"
                    for region in area.regions
                    if region.type == "UI"
                ):
                    _tag_viewports(window)
                continue

            if obj is None or obj.type != "MESH":
                _cache[pointer] = {
                    "key": key,
                    "signature": signature,
                    "epoch": _epoch,
                    "result": None,
                }
                _tag_viewports(window)
                continue

            _measuring = True
            try:
                with bpy.context.temp_override(
                    window=window, scene=scene, view_layer=view_layer
                ):
                    result = measure_object(bpy.context, obj)
            except (RuntimeError, ValueError, ReferenceError) as exc:
                result = {"warnings": [f"Measurement failed: {exc}"]}
            finally:
                _measuring = False
            _cache[pointer] = {
                "key": key,
                "signature": signature,
                "epoch": _epoch,
                "result": result,
            }
            _tag_viewports(window)
        except (RuntimeError, ReferenceError):
            # An undo or file load may invalidate window RNA during this tick.
            _cache.pop(pointer, None)

    for pointer in tuple(_cache):
        if pointer not in live_windows:
            del _cache[pointer]
    for pointer in tuple(_draw_interest):
        if pointer not in live_windows:
            del _draw_interest[pointer]
    overlay.prune(live_windows)
    hover.prune(live_windows)
    ruler.prune(live_windows)
    return _TIMER_INTERVAL


def _restart_timer():
    if bpy.app.timers.is_registered(_timer_tick):
        bpy.app.timers.unregister(_timer_tick)
    if _registered:
        bpy.app.timers.register(
            _timer_tick, first_interval=_TIMER_INTERVAL, persistent=False
        )


@persistent
def _on_depsgraph_update(_scene, depsgraph):
    global _epoch
    if _measuring:
        return
    # The depsgraph event, not UI drawing, invalidates expensive measurements.
    if depsgraph.updates:
        _epoch += 1


@persistent
def _on_load_pre(_dummy):
    # Blender removes modal operators on file load; discard their registry too.
    hover.stop()
    ruler.stop_all()


@persistent
def _on_state_reset(_dummy):
    global _epoch
    _cache.clear()
    _draw_interest.clear()
    overlay.clear()
    hover.invalidate()
    ruler.stop_all()
    _epoch += 1
    _restart_timer()


def _axis_row(layout, label, values, formatter):
    column = layout.column(align=True)
    column.label(text=label)
    for axis, value in zip("XYZ", values if values is not None else (None,) * 3):
        column.label(text=f"  {axis}  {formatter(value)}")


class VIEW3D_PT_final_dimensions(bpy.types.Panel):
    bl_label = "Final Dimensions"
    bl_idname = "VIEW3D_PT_final_dimensions"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Final Dimensions"

    def draw(self, context):
        layout = self.layout
        ruler.draw_panel(layout, context)
        offset_cut.draw_panel(layout, context)
        layout.prop(
            context.window_manager,
            "final_dimensions_show_overlay",
            text="Selected edge labels",
        )
        box = layout.box()
        box.prop(context.window_manager, "final_dimensions_hover")
        if context.window_manager.final_dimensions_hover:
            box.prop(context.window_manager, "final_dimensions_section_axis", text="Plane")
            box.label(text="f = cursor diameter · ½ = half")
            if context.window_manager.final_dimensions_section_axis == 'EDGE':
                box.label(text="Select a transverse edge")
                box.label(text="Plane follows surface normal")
            status = hover.get(context.window, context.area)
            messages = [] if status is None else (
                [status['message']] if status['message'] else list(status['warnings']))
            if context.space_data.region_quadviews:
                messages.append("Hover sections require a single view.")
            for message in messages:
                for line in textwrap.wrap(message, width=28, break_long_words=False):
                    box.label(text=line, icon='INFO')
        obj = context.view_layer.objects.active
        if obj is None or obj.type != "MESH":
            layout.label(text="Select a mesh object", icon="INFO")
            return

        if obj.mode == "EDIT" and context.window_manager.final_dimensions_show_overlay:
            if overlay.selected_edge(obj) is None:
                layout.label(text="Select one edge or make", icon="INFO")
                layout.label(text="an edge active")
            elif context.window is not None:
                edge_cache = overlay.get(context.window)
                if edge_cache is not None:
                    for warning in edge_cache["warnings"]:
                        for line in textwrap.wrap(
                            "Edge final: " + warning,
                            width=28,
                            break_long_words=False,
                        ):
                            layout.label(text=line, icon="INFO")


class VIEW3D_PT_final_dimensions_object(bpy.types.Panel):
    bl_label = "Object dimensions"
    bl_idname = "VIEW3D_PT_final_dimensions_object"
    bl_parent_id = "VIEW3D_PT_final_dimensions"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Final Dimensions"
    bl_options = {'DEFAULT_CLOSED'}

    @classmethod
    def poll(cls, context):
        obj = context.view_layer.objects.active
        return obj is not None and obj.type == "MESH"

    def draw(self, context):
        layout = self.layout
        obj = context.view_layer.objects.active
        layout.label(text="World axes · scene unit scale", icon="ORIENTATION_GLOBAL")
        window = context.window
        if window is None:
            layout.label(text="Waiting for viewport", icon="INFO")
            return
        _draw_interest[window.as_pointer()] = time.monotonic()
        cached = _cache.get(window.as_pointer())
        key = _context_key(window, context.scene, context.view_layer, obj)
        if cached is None or cached["key"] != key:
            layout.label(text="Measuring…", icon="TIME")
            return
        result = cached["result"]
        if result is None:
            layout.label(text="Measuring…", icon="TIME")
            return
        if cached["epoch"] != _epoch or cached["signature"] != _signature(obj):
            layout.label(text="Updating…", icon="TIME")

        length = lambda value: format_length(context.scene, value)
        signed = lambda value: (
            "—" if value is None else ("+" if value > 0 else "") + length(value)
        )
        percentage = lambda value: "—" if value is None else f"{value:+.2f}%"
        _axis_row(layout, "Cage", result.get("cage"), length)
        _axis_row(layout, "Final", result.get("final"), length)
        _axis_row(layout, "Delta", result.get("difference"), signed)
        _axis_row(layout, "%", result.get("percent"), percentage)
        _axis_row(layout, "Half", result.get("half"), length)
        layout.label(text="Half = half of final box span")
        for warning in result.get("warnings", ()):
            for index, line in enumerate(
                textwrap.wrap(warning, width=28, break_long_words=False)
            ):
                if index == 0:
                    layout.label(text=line, icon="INFO")
                else:
                    layout.label(text="  " + line)


_HANDLERS = (
    (bpy.app.handlers.load_pre, _on_load_pre),
    (bpy.app.handlers.depsgraph_update_post, _on_depsgraph_update),
    (bpy.app.handlers.load_post, _on_state_reset),
    (bpy.app.handlers.undo_post, _on_state_reset),
    (bpy.app.handlers.redo_post, _on_state_reset),
)


def register():
    global _registered
    bpy.types.WindowManager.final_dimensions_show_overlay = BoolProperty(
        name="Selected edge labels",
        description="Show selected edge and evaluated directional span in the viewport",
        default=True,
    )
    bpy.utils.register_class(VIEW3D_PT_final_dimensions)
    bpy.utils.register_class(VIEW3D_PT_final_dimensions_object)
    overlay.register()
    hover.register()
    ruler_style.register()
    ruler.register()
    offset_cut.register()
    _registered = True
    for handlers, callback in _HANDLERS:
        if callback not in handlers:
            handlers.append(callback)
    _restart_timer()


def unregister():
    global _registered
    _registered = False
    if bpy.app.timers.is_registered(_timer_tick):
        bpy.app.timers.unregister(_timer_tick)
    for handlers, callback in _HANDLERS:
        if callback in handlers:
            handlers.remove(callback)
    _cache.clear()
    _draw_interest.clear()
    # Remove consumers of the properties before unregistering their RNA types.
    bpy.utils.unregister_class(VIEW3D_PT_final_dimensions_object)
    bpy.utils.unregister_class(VIEW3D_PT_final_dimensions)
    offset_cut.unregister()
    overlay.unregister()
    ruler.unregister()
    ruler_style.unregister()
    hover.unregister()
    del bpy.types.WindowManager.final_dimensions_show_overlay
