"""Mouse-driven sections of cached evaluated geometry; never changes the mesh."""

from __future__ import annotations

import math
import sys
import time

import bpy
import gpu
from bpy.props import BoolProperty, EnumProperty
from bpy_extras import view3d_utils
from gpu_extras.batch import batch_for_shader
from mathutils import Vector

from . import overlay
from .measure import format_length
from .section import Geometry, cursor_diameter


_INTERVAL = 0.08
_operators = {}  # one passive mouse listener per window
_results = {}  # window -> result for exactly one viewport
_handles = []
_generation = 0
_registered = False


def _parent():
    return sys.modules[__package__]


def temporarily_suppressed(window):
    """Avoid a second preview while native loop tools or Loop Offset run."""
    if window is None:
        return False
    from . import loop_offset
    if loop_offset.preview_active(window):
        return True
    for operator in window.modal_operators:
        identifier = getattr(getattr(operator, 'bl_rna', None), 'identifier', '')
        if not identifier:
            identifier = getattr(operator, 'bl_idname', '')
        identifier = str(identifier).lower().replace('_ot_', '.')
        if identifier in {'mesh.loopcut_slide', 'mesh.loopcut',
                          'transform.edge_slide', 'transform.vert_slide',
                          'mesh.final_dimensions_loop_offset'}:
            return True
    return False


def plane_normal(obj, edge, hit_normal, mode):
    """A visible, user-controlled slice, not an inferred universal tube axis.

    EDGE: the plane contains the transverse edge direction and the local
    surface normal. X/Y/Z: normal to that local object axis in world space.
    Near-parallel edge/normal does not define a stable plane; do not guess.
    """
    if mode != 'EDGE':
        axis = Vector(tuple(1.0 if c == mode else 0.0 for c in 'XYZ'))
        transform = obj.matrix_world.to_3x3()
        if abs(transform.determinant()) < 1e-18:
            raise ValueError("Object scale is zero; section axis is undefined")
        return (transform.inverted().transposed() @ axis).normalized()
    if edge is None:
        raise ValueError("Select a transverse edge, or choose a section axis")
    normal = Vector(edge['direction']).cross(Vector(hit_normal))
    if normal.length < 0.08:
        raise ValueError("Edge is parallel to surface normal; choose X, Y or Z")
    return normal.normalized()


def _viewport_at(window, mouse):
    if mouse is None:
        return None
    x, y = mouse
    for area in window.screen.areas:
        if area.type != 'VIEW_3D':
            continue
        # UI, toolbar and headers must not keep a stale measurement visible.
        for region in area.regions:
            if (region.type == 'WINDOW' and region.x <= x < region.x + region.width
                    and region.y <= y < region.y + region.height):
                # Sidebar is an overlapping region with transparent UI enabled.
                if any(r.type in {'UI', 'TOOLS'} and r.width > 1
                       and r.x <= x < r.x + r.width and r.y <= y < r.y + r.height
                       for r in area.regions):
                    return None
                return area, region
    return None


def _redraw(window):
    for area in window.screen.areas:
        if area.type == 'VIEW_3D':
            area.tag_redraw()


def get(window, area=None):
    if window is None or temporarily_suppressed(window):
        return None
    value = _results.get(window.as_pointer())
    if value is not None and area is not None and value['area'] != area.as_pointer():
        return None
    return value


class Probe:
    """No RNA references survive a tick. Expensive BVH is reused while idle."""

    def __init__(self):
        self.mouse = None
        self.maximum_snap = False
        self.reset()

    def clear_lock(self):
        self.locked_value = None
        self.locked_key = None

    def set_maximum_snap(self, enabled):
        enabled = bool(enabled)
        if enabled == self.maximum_snap:
            return False
        self.maximum_snap = enabled
        self.query_key = None
        self.clear_lock()
        return True

    def reset(self):
        self.clear_lock()
        self.geometry = None
        self.geometry_key = None
        self.query_key = None
        self.geometry_error = None

    def update(self, context, epoch):
        from . import ruler
        window = context.window
        pointer = window.as_pointer()
        target = _viewport_at(window, self.mouse)
        obj = window.view_layer.objects.active
        wm = context.window_manager
        if (temporarily_suppressed(window) or ruler.is_running(window)
                or not wm.final_dimensions_hover or target is None or obj is None
                or obj.type != 'MESH' or not obj.visible_get(view_layer=window.view_layer)):
            self.clear_lock()
            self.query_key = None
            if _results.pop(pointer, None) is not None:
                _redraw(window)
            return
        area, region = target
        space = area.spaces.active
        # Quad view requires matching region-specific RegionView3D.
        if space.region_quadviews:
            self.clear_lock()
            _results.pop(pointer, None)
            self.query_key = None
            return
        if not space.overlay.show_overlays:
            self.clear_lock()
            _results.pop(pointer, None)
            self.query_key = None
            return
        rv3d = space.region_3d
        edge = overlay.selected_edge(obj)
        mode = wm.final_dimensions_section_axis
        geometry_key = (obj.as_pointer(), obj.mode, obj.data.as_pointer(), epoch,
                        window.scene.as_pointer(), window.view_layer.as_pointer(),
                        tuple(v for row in obj.matrix_world for v in row))
        coordinate = (self.mouse[0] - region.x, self.mouse[1] - region.y)
        lock_key = (geometry_key, area.as_pointer(),
                    edge['signature'] if edge else None, mode)
        if self.maximum_snap and self.locked_value is not None and self.locked_key == lock_key:
            _results[pointer] = self.locked_value
            return
        self.clear_lock()
        query_key = (geometry_key, area.as_pointer(), coordinate, region.width,
                     region.height, tuple(v for row in rv3d.perspective_matrix for v in row),
                     edge['signature'] if edge else None, mode, self.maximum_snap)
        if query_key == self.query_key:
            return
        self.query_key = query_key
        value = {'area': area.as_pointer(), 'object': obj.as_pointer(), 'epoch': epoch,
                 'mouse': coordinate, 'section': None, 'message': '', 'warnings': ()}
        try:
            if mode == 'EDGE' and edge is None:
                raise ValueError("Select a transverse edge, or choose a section axis")
            if geometry_key != self.geometry_key:
                self.geometry_key = geometry_key
                self.geometry = None
                self.geometry_error = None
                try:
                    self.geometry = Geometry.from_object(context, obj)
                except (ValueError, RuntimeError) as exc:
                    self.geometry_error = str(exc)
            if self.geometry is None:
                raise ValueError(self.geometry_error or "No evaluated mesh")
            direction = view3d_utils.region_2d_to_vector_3d(region, rv3d, coordinate)
            # Finite orthographic origin avoids precision loss at huge far clips.
            origin = view3d_utils.region_2d_to_origin_3d(
                region, rv3d, coordinate, clamp=max(space.clip_end, 1.0))
            hit = self.geometry.ray_cast(origin, direction)
            if hit is None:
                _results.pop(pointer, None)
                _redraw(window)
                return
            # Do not measure the active mesh through another visible object.
            depsgraph = context.evaluated_depsgraph_get()
            front = context.scene.ray_cast(depsgraph, origin, direction)
            if front[0] and front[4] is not None:
                front_obj = getattr(front[4], 'original', front[4])
                tolerance = max(self.geometry.scale * 1e-5, 1e-7)
                if (front_obj.as_pointer() != obj.as_pointer()
                        and (front[1] - origin).length + tolerance
                        < (Vector(hit['point']) - origin).length):
                    _results.pop(pointer, None)
                    _redraw(window)
                    return
            normal = plane_normal(obj, edge, hit['normal'], mode)
            result = self.geometry.section(hit['point'], normal, hit['triangle'])
            if result is None or not result['segments'] or result['diameter'] <= 0:
                raise ValueError("No measurable section at this point")
            if self.maximum_snap:
                if not result.get('closed'):
                    raise ValueError('Maximum diameter needs a closed, manifold section')
            else:
                result = cursor_diameter(result, hit['point'], normal)
            value['section'] = result
            value['maximum_snap'] = self.maximum_snap
            value['hit'] = tuple(hit['point'])
            value['warnings'] = tuple(dict.fromkeys(
                (*self.geometry.warnings, *result.get('warnings', ()))))
        except (ValueError, RuntimeError, ReferenceError) as exc:
            value['message'] = str(exc)
        _results[pointer] = value
        if self.maximum_snap and value['section'] is not None:
            self.locked_key, self.locked_value = lock_key, value
        _redraw(window)


class VIEW3D_OT_final_dimensions_hover(bpy.types.Operator):
    bl_idname = 'view3d.final_dimensions_hover'
    bl_label = 'Final Dimensions hover listener'
    bl_options = {'INTERNAL'}

    def invoke(self, context, event):
        pointer = context.window.as_pointer()
        if pointer in _operators or not _registered:
            return {'CANCELLED'}
        self._window_pointer = pointer
        self._generation = _generation
        self._probe = Probe()
        # Invoke's coordinates are sometimes stale (app timer invocation).
        # First real mouse event supplies the location.
        self._timer = context.window_manager.event_timer_add(_INTERVAL, window=context.window)
        self._last_tick = 0.0
        _operators[pointer] = self
        context.window_manager.modal_handler_add(self)
        return {'RUNNING_MODAL'}

    def modal(self, context, event):
        if (not _registered or self._generation != _generation
                or not context.window_manager.final_dimensions_hover):
            self.cancel(context)
            return {'CANCELLED'}
        if event.type in {'MOUSEMOVE', 'INBETWEEN_MOUSEMOVE'}:
            self._probe.mouse = (event.mouse_x, event.mouse_y)
        snap_changed = False
        if event.type in {'MOUSEMOVE', 'INBETWEEN_MOUSEMOVE', 'LEFT_CTRL', 'RIGHT_CTRL'}:
            snap_changed = self._probe.set_maximum_snap(event.ctrl)
        if event.type == 'WINDOW_DEACTIVATE':
            self._probe.mouse = None
            self._probe.set_maximum_snap(False)
            self._probe.clear_lock()
            _results.pop(self._window_pointer, None)
            _redraw(context.window)
        # Blender Event exposes the event type, not the originating Timer.
        # A monotonic throttle also handles TIMER events from other add-ons.
        if event.type == 'TIMER' or snap_changed:
            now = time.monotonic()
            if snap_changed or now - self._last_tick >= _INTERVAL * 0.9:
                self._last_tick = now
                parent = _parent()
                previous = parent._measuring
                parent._measuring = True
                try:
                    self._probe.update(context, parent._epoch)
                except (ReferenceError, RuntimeError):
                    self._probe.reset()
                    _results.pop(self._window_pointer, None)
                finally:
                    parent._measuring = previous
        return {'PASS_THROUGH'}

    def cancel(self, context):
        if getattr(self, '_timer', None) is not None:
            context.window_manager.event_timer_remove(self._timer)
            self._timer = None
        self._probe.reset()
        if _operators.get(self._window_pointer) is self:
            _operators.pop(self._window_pointer, None)
            _results.pop(self._window_pointer, None)


def ensure_window(window):
    if (bpy.app.background or not _registered
            or not bpy.context.window_manager.final_dimensions_hover):
        return
    if window.as_pointer() in _operators:
        return
    area = next((a for a in window.screen.areas if a.type == 'VIEW_3D'), None)
    if area is None:
        return
    region = next((r for r in area.regions if r.type == 'WINDOW'), None)
    if region is not None:
        with bpy.context.temp_override(window=window, area=area, region=region):
            bpy.ops.view3d.final_dimensions_hover('INVOKE_DEFAULT')


def invalidate():
    _results.clear()
    for operator in _operators.values():
        operator._probe.reset()


def clear_window(window):
    pointer = window.as_pointer()
    _results.pop(pointer, None)
    operator = _operators.get(pointer)
    if operator is not None:
        operator._probe.query_key = None
        operator._probe.clear_lock()


def prune(live_windows):
    for pointer in tuple(_operators):
        if pointer not in live_windows:
            operator = _operators.pop(pointer)
            try:
                if operator._timer is not None:
                    bpy.context.window_manager.event_timer_remove(operator._timer)
            except (ReferenceError, RuntimeError):
                pass
            _results.pop(pointer, None)


def stop():
    global _generation
    _generation += 1
    for operator in tuple(_operators.values()):
        if operator._timer is not None:
            try:
                bpy.context.window_manager.event_timer_remove(operator._timer)
            except (ReferenceError, RuntimeError):
                pass
            operator._timer = None
        operator._probe.reset()
    _operators.clear()
    _results.clear()


def _visible_result():
    from . import ruler
    context = bpy.context
    if (not _registered or context.window is None or temporarily_suppressed(context.window)
            or ruler.is_running(context.window) or context.area is None
            or context.area.type != 'VIEW_3D'
            or not context.window_manager.final_dimensions_hover
            or not context.space_data.overlay.show_overlays):
        return None
    value = get(context.window, context.area)
    obj = context.view_layer.objects.active
    if (value is None or obj is None or value['object'] != obj.as_pointer()
            or value['epoch'] != _parent()._epoch
            or not obj.visible_get(view_layer=context.view_layer)):
        return None
    return value


def _lines(shader, points, color, width):
    if not points:
        return
    batch = batch_for_shader(shader, 'LINES', {'pos': points})
    shader.bind()
    shader.uniform_float('viewportSize', gpu.state.viewport_get()[2:])
    shader.uniform_float('lineWidth', width)
    shader.uniform_float('color', color)
    batch.draw(shader)


def _draw_view():
    value = _visible_result()
    if value is None or value['section'] is None:
        return
    section = value['section']
    points = [p for segment in section['segments'] for p in segment]
    shader = gpu.shader.from_builtin('POLYLINE_UNIFORM_COLOR')
    old_blend = gpu.state.blend_get()
    old_depth = gpu.state.depth_test_get()
    try:
        gpu.state.blend_set('ALPHA')
        gpu.state.depth_test_set('NONE')
        _lines(shader, points, (0.12, 0.85, 1.0, 0.3), 1.5)
        gpu.state.depth_test_set('LESS_EQUAL')
        _lines(shader, points, (0.2, 0.9, 1.0, 1.0), 2.5)
        gpu.state.depth_test_set('NONE')
        a, b = section['endpoints']
        _lines(shader, [a, b], (0.25, 0.9, 1.0, 1.0), 2.0)
        center = tuple((x + y) / 2 for x, y in zip(a, b))
        _lines(shader, [center, b], (1.0, 0.62, 0.16, 1.0), 2.5)
    finally:
        gpu.state.depth_test_set(old_depth)
        gpu.state.blend_set(old_blend)


def _draw_pixel():
    value = _visible_result()
    if value is None:
        return
    if value['section'] is None:
        # Instructions stay in the sidebar, not over the user's model.
        return
    context = bpy.context
    section = value['section']
    label = ('f: ' + format_length(context.scene, section['diameter'])
             + '   ½: ' + format_length(context.scene, section['radius']))
    if value['warnings']:
        label += ' *'
    import blf
    blf.size(0, overlay._SIZE)
    width, height = blf.dimensions(0, label)
    position = value['mouse']
    if value.get('maximum_snap'):
        a, b = map(Vector, section['endpoints'])
        projected = view3d_utils.location_3d_to_region_2d(
            context.region, context.region_data, (a+b)*.5)
        if projected is not None:
            position = projected
    x = min(max(8, position[0] + 18), max(8, context.region.width - width - 8))
    y = min(max(8, position[1] + 18), max(8, context.region.height - height - 8))
    overlay._text(x, y, label, (0.25, 0.88, 1.0, 1.0))


def _settings_changed(_wm, _context):
    invalidate()
    if not bpy.context.window_manager.final_dimensions_hover:
        stop()
    for window in bpy.context.window_manager.windows:
        _redraw(window)


def register():
    global _registered
    bpy.types.WindowManager.final_dimensions_hover = BoolProperty(
        name='Hover section', default=False, update=_settings_changed,
        description='Measure the section under the mouse; hold Ctrl to lock its maximum diameter')
    bpy.types.WindowManager.final_dimensions_section_axis = EnumProperty(
        name='Section orientation', default='EDGE', update=_settings_changed,
        items=[('EDGE', 'Edge + surface', 'Plane through the hit, spanning selected transverse edge and surface normal'),
               ('X', 'Local X', 'Plane perpendicular to local X, through the mouse hit'),
               ('Y', 'Local Y', 'Plane perpendicular to local Y, through the mouse hit'),
               ('Z', 'Local Z', 'Plane perpendicular to local Z, through the mouse hit')])
    bpy.utils.register_class(VIEW3D_OT_final_dimensions_hover)
    _registered = True
    _handles.append(bpy.types.SpaceView3D.draw_handler_add(_draw_view, (), 'WINDOW', 'POST_VIEW'))
    _handles.append(bpy.types.SpaceView3D.draw_handler_add(_draw_pixel, (), 'WINDOW', 'POST_PIXEL'))


def unregister():
    global _registered
    _registered = False
    stop()
    for handle in _handles:
        bpy.types.SpaceView3D.draw_handler_remove(handle, 'WINDOW')
    _handles.clear()
    bpy.utils.unregister_class(VIEW3D_OT_final_dimensions_hover)
    del bpy.types.WindowManager.final_dimensions_hover
    del bpy.types.WindowManager.final_dimensions_section_axis
