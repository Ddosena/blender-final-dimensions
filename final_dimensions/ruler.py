"""Window-local surface rulers with one shared picker and passive editing listener."""

import math
import sys
import textwrap
import time

import bpy
import gpu
from bpy.props import BoolProperty, IntProperty
from bpy_extras import view3d_utils
from mathutils import Vector

from . import overlay, hover
from .measure import format_length
from .surface import ScenePicker


_states = {}
_sessions = {}
_handles = []
_registered = False
_generation = 0
_INTERVAL = 0.08
_HANDLE_RADIUS = 12.0
_SLOTS = ('first', 'second')


def _parent():
    return sys.modules[__package__]


def collection(window):
    return None if window is None else _states.get(window.as_pointer())


def get(window):
    manager = collection(window)
    return manager.selected if manager else None


def is_running(window):
    manager = collection(window)
    return bool(manager and manager.mode != 'IDLE' and window.as_pointer() in _sessions)


class RulerState:
    """Only primitive anchors and measurements; no per-ruler geometry cache."""

    def __init__(self, context=None):
        self.first = None
        self.second = None
        self.candidate = None
        self.distance = None
        self.message = ''
        self.area_pointer = context.area.as_pointer() if context and context.area else 0
        self.errors = {}

    def endpoints(self, slot=None, candidate=None):
        ends = [self.first, self.second]
        if slot is not None:
            ends[slot] = candidate
        return ends

    def recalculate(self, slot=None, candidate=None):
        first, second = self.endpoints(slot, candidate)
        self.candidate = candidate
        self.distance = ((Vector(second['point']) - Vector(first['point'])).length
                         if first and second else None)

    def error_message(self):
        return ' '.join(self.errors.values())


class RulerCollection:
    def __init__(self, context):
        self.context_key = (context.scene.as_pointer(), context.view_layer.as_pointer())
        self.items = []
        self.active = -1
        self.mode = 'IDLE'
        self.slot = 0
        self.candidate = None
        self.original = None
        self.picker = None
        self.epoch = -1
        self.query_key = None

    @property
    def selected(self):
        return self.items[self.active] if 0 <= self.active < len(self.items) else None

    def recalculate(self):
        for index, item in enumerate(self.items):
            preview = index == self.active and self.mode != 'IDLE'
            item.recalculate(self.slot if preview else None,
                             self.candidate if preview else None)

    def refresh(self, context, epoch):
        if self.context_key != (context.scene.as_pointer(), context.view_layer.as_pointer()):
            raise ValueError('Scene or view layer changed; start new rulers')
        if self.picker is None:
            self.picker = ScenePicker(context, epoch)
        else:
            self.picker.refresh(context, epoch)
        if self.epoch == epoch:
            return
        self.epoch = epoch
        self.query_key = None
        self.candidate = None
        # An unavailable anchor affects only its own endpoint.
        for item in self.items:
            for slot, attr in enumerate(_SLOTS):
                hit = getattr(item, attr)
                if hit is None:
                    continue
                try:
                    hit['point'] = tuple(self.picker.resolve(context, hit['anchor']))
                except (ValueError, ReferenceError, RuntimeError) as exc:
                    setattr(item, attr, None)
                    item.errors[slot] = f'Point {slot+1}: {exc}. Pick it again.'
            item.message = item.error_message()
        # Cancel restores the current deformed anchor, never its old world point.
        if self.original is not None:
            try:
                self.original['point'] = tuple(self.picker.resolve(context, self.original['anchor']))
            except (ValueError, ReferenceError, RuntimeError):
                self.original = None
        self.recalculate()

    def pick(self, context, epoch, region, rv3d, coordinate, force=False):
        self.refresh(context, epoch)
        key = (coordinate, tuple(v for row in rv3d.perspective_matrix for v in row),
               region.width, region.height, epoch)
        if not force and key == self.query_key:
            return
        self.query_key = key
        direction = view3d_utils.region_2d_to_vector_3d(region, rv3d, coordinate)
        origin = view3d_utils.region_2d_to_origin_3d(
            region, rv3d, coordinate, clamp=max(context.space_data.clip_end, 1.0))
        item = self.selected
        try:
            self.candidate = self.picker.pick(context, origin, direction)
            item.message = item.error_message() if self.candidate else 'Move onto a visible mesh surface'
        except (ValueError, RuntimeError, ReferenceError) as exc:
            self.candidate = None
            item.message = str(exc)
        if self.mode == 'DRAG' and self.candidate is not None:
            setattr(item, _SLOTS[self.slot], dict(self.candidate))
        self.recalculate()

    def cancel_interaction(self):
        if self.mode == 'ADD' and self.selected is not None:
            self.items.pop(self.active)
            self.active = min(self.active, len(self.items)-1)
        elif self.mode in {'DRAG', 'REPLACE'} and self.selected is not None:
            setattr(self.selected, _SLOTS[self.slot], self.original)
        self.idle()

    def idle(self):
        self.mode = 'IDLE'
        self.candidate = self.original = None
        self.query_key = None
        self.recalculate()
        if self.selected:
            self.selected.message = self.selected.error_message()

    def add(self, context):
        self.cancel_interaction()
        self.items.append(RulerState(context))
        self.active = len(self.items)-1
        self.mode, self.slot = 'ADD', 0

    def select(self, index):
        if not 0 <= index < len(self.items):
            return False
        if self.mode == 'ADD' and index == self.active:
            return True
        item = self.items[index]
        self.cancel_interaction()
        if item in self.items:
            self.active = self.items.index(item)
        # Selecting an incomplete surviving ruler offers its missing point.
        if self.selected:
            if self.selected.first is None:
                self.begin_endpoint(0, 'REPLACE')
            elif self.selected.second is None:
                self.begin_endpoint(1, 'REPLACE')
        return self.selected is not None

    def begin_endpoint(self, slot, mode):
        item = self.selected
        if item is None or slot not in (0, 1):
            return False
        self.mode, self.slot = mode, slot
        hit = getattr(item, _SLOTS[slot])
        self.original = dict(hit) if hit else None
        self.candidate = dict(hit) if hit and mode == 'DRAG' else None
        self.query_key = None
        if mode == 'REPLACE':
            setattr(item, _SLOTS[slot], None)
        self.recalculate()
        return True

    def confirm(self):
        if self.selected is None or self.candidate is None:
            return False
        item = self.selected
        setattr(item, _SLOTS[self.slot], dict(self.candidate))
        item.errors.pop(self.slot, None)
        if self.mode == 'ADD' and self.slot == 0:
            self.slot = 1
            self.candidate = None
            self.query_key = None
            self.recalculate()
        else:
            self.idle()
        return True


def _redraw(window):
    for area in window.screen.areas:
        if area.type == 'VIEW_3D':
            area.tag_redraw()


def _area_context(window, manager):
    item = manager.selected
    area = next((a for a in window.screen.areas if a.type == 'VIEW_3D'
                 and item and a.as_pointer() == item.area_pointer), None)
    if area is None:
        area = next((a for a in window.screen.areas if a.type == 'VIEW_3D'), None)
    region = next((r for r in area.regions if r.type == 'WINDOW'), None) if area else None
    return area, region


def _ensure_listener(context):
    if context.window.as_pointer() not in _sessions:
        bpy.ops.view3d.final_dimensions_ruler('INVOKE_DEFAULT', listen_only=True)


def _changed(context):
    operator = _sessions.get(context.window.as_pointer())
    if operator:
        operator._mouse = None
        operator._sync_cursor(context)
    if is_running(context.window):
        hover.clear_window(context.window)
    _redraw(context.window)


class VIEW3D_OT_final_dimensions_ruler(bpy.types.Operator):
    bl_idname = 'view3d.final_dimensions_ruler'
    bl_label = 'Add Surface Ruler'
    bl_description = 'Add a ruler by clicking two evaluated mesh surfaces'
    bl_options = {'INTERNAL'}

    listen_only: BoolProperty(default=False, options={'HIDDEN', 'SKIP_SAVE'})

    def invoke(self, context, event):
        if context.area is None or context.area.type != 'VIEW_3D':
            return {'CANCELLED'}
        if context.space_data.region_quadviews:
            self.report({'INFO'}, 'Surface Rulers require a single view')
            return {'CANCELLED'}
        pointer = context.window.as_pointer()
        manager = collection(context.window)
        if manager is None:
            manager = _states[pointer] = RulerCollection(context)
        if not self.listen_only:
            manager.add(context)
        elif not manager.items:
            _states.pop(pointer, None)
            return {'CANCELLED'}
        if pointer in _sessions:
            _changed(context)
            return {'FINISHED'}
        self._window_pointer = pointer
        self._generation = _generation
        self._mouse = None
        self._last_tick = 0.0
        self._cursor_active = False
        self._state = manager
        self._timer = context.window_manager.event_timer_add(_INTERVAL, window=context.window)
        _sessions[pointer] = self
        self._sync_cursor(context)
        context.window_manager.modal_handler_add(self)
        _changed(context)
        return {'RUNNING_MODAL'}

    def _sync_cursor(self, context):
        active = self._state.mode != 'IDLE'
        if active != self._cursor_active:
            if active:
                context.window.cursor_modal_set('CROSSHAIR')
            else:
                context.window.cursor_modal_restore()
            self._cursor_active = active

    def _target(self, context):
        target = hover._viewport_at(context.window, self._mouse)
        item = self._state.selected
        if target is None or item is None or target[0].as_pointer() != item.area_pointer:
            return None
        area, region = target
        space = area.spaces.active
        if space.region_quadviews or not space.overlay.show_overlays:
            return None
        return area, region

    def _update(self, context, force=False):
        target = self._target(context)
        manager = self._state
        if target is None:
            manager.query_key = None
            manager.candidate = None
            manager.recalculate()
            _redraw(context.window)
            return
        area, region = target
        parent = _parent()
        previous = parent._measuring
        parent._measuring = True
        try:
            with context.temp_override(window=context.window, area=area, region=region):
                coordinate = (self._mouse[0]-region.x, self._mouse[1]-region.y)
                manager.pick(bpy.context, parent._epoch, region,
                             area.spaces.active.region_3d, coordinate, force)
        finally:
            parent._measuring = previous
        _redraw(context.window)

    def _near_endpoint(self, context):
        target = self._target(context)
        if target is None:
            return None
        area, region = target
        coordinate = Vector((self._mouse[0]-region.x, self._mouse[1]-region.y))
        closest, distance = None, _HANDLE_RADIUS
        for slot, hit in enumerate((self._state.selected.first, self._state.selected.second)):
            if hit is None:
                continue
            point = view3d_utils.location_3d_to_region_2d(
                region, area.spaces.active.region_3d, Vector(hit['point']))
            if point is not None and (point-coordinate).length <= distance:
                closest, distance = slot, (point-coordinate).length
        return closest

    def _cancel_interaction(self, context):
        self._state.cancel_interaction()
        self._sync_cursor(context)
        if not self._state.items:
            self.finish(context, keep=False)
            return {'CANCELLED'}
        _redraw(context.window)
        return {'RUNNING_MODAL'}

    def modal(self, context, event):
        if (not _registered or self._generation != _generation
                or _sessions.get(self._window_pointer) is not self):
            self.finish(context, keep=False)
            return {'CANCELLED'}
        manager = self._state
        if not manager.items:
            self.finish(context, keep=False)
            return {'CANCELLED'}
        if event.type in {'MOUSEMOVE', 'INBETWEEN_MOUSEMOVE', 'LEFTMOUSE', 'RIGHTMOUSE'}:
            self._mouse = (event.mouse_x, event.mouse_y)
        elif event.type == 'WINDOW_DEACTIVATE':
            self._mouse = None
            if manager.mode == 'DRAG':
                return self._cancel_interaction(context)
        if event.type == 'ESC' and event.value == 'PRESS' and manager.mode != 'IDLE':
            return self._cancel_interaction(context)
        if event.type == 'BACK_SPACE' and event.value == 'PRESS' and manager.mode == 'ADD':
            manager.selected.first = manager.selected.second = None
            manager.selected.errors.clear()
            manager.slot = 0
            manager.candidate = None
            manager.query_key = None
            manager.recalculate()
            _redraw(context.window)
            return {'RUNNING_MODAL'}
        if event.type == 'TIMER':
            now = time.monotonic()
            if now-self._last_tick >= _INTERVAL*.9:
                self._last_tick = now
                tick_window(context.window, _parent()._epoch)
                if manager.mode != 'IDLE':
                    self._safe_update(context)
            return {'PASS_THROUGH'}
        if manager.mode == 'DRAG':
            if event.type in {'MOUSEMOVE', 'INBETWEEN_MOUSEMOVE'}:
                self._safe_update(context)
                return {'RUNNING_MODAL'}
            if event.type == 'LEFTMOUSE' and event.value == 'RELEASE':
                self._safe_update(context, force=True)
                if manager.candidate is None:
                    manager.cancel_interaction()
                else:
                    manager.confirm()
                self._sync_cursor(context)
                _redraw(context.window)
                return {'RUNNING_MODAL'}
        if event.type in {'MOUSEMOVE', 'INBETWEEN_MOUSEMOVE'} and manager.mode != 'IDLE':
            self._safe_update(context)
        if event.alt:
            return {'PASS_THROUGH'}
        if event.type in {'LEFTMOUSE', 'RIGHTMOUSE'} and event.value == 'PRESS':
            if self._target(context) is None:
                return {'PASS_THROUGH'}
            if manager.mode == 'IDLE':
                tick_window(context.window, _parent()._epoch)
                slot = self._near_endpoint(context)
                if slot is None:
                    return {'PASS_THROUGH'}
                manager.begin_endpoint(slot, 'DRAG' if event.type == 'LEFTMOUSE' else 'REPLACE')
                self._sync_cursor(context)
                hover.clear_window(context.window)
                self._safe_update(context, force=True)
                return {'RUNNING_MODAL'}
            if event.type == 'LEFTMOUSE' and manager.mode in {'ADD', 'REPLACE'}:
                self._safe_update(context, force=True)
                manager.confirm()
                self._sync_cursor(context)
                _redraw(context.window)
                return {'RUNNING_MODAL'}
        return {'PASS_THROUGH'}

    def _safe_update(self, context, force=False):
        try:
            self._update(context, force)
        except (ReferenceError, RuntimeError, ValueError) as exc:
            if self._state.selected:
                self._state.selected.message = str(exc)
            self._state.candidate = None
            self._state.recalculate()

    def finish(self, context, keep=False):
        if getattr(self, '_timer', None) is not None:
            try:
                context.window_manager.event_timer_remove(self._timer)
            except (ReferenceError, RuntimeError):
                pass
            self._timer = None
        if _sessions.get(self._window_pointer) is self:
            _sessions.pop(self._window_pointer, None)
            if keep:
                self._state.cancel_interaction()
            else:
                _states.pop(self._window_pointer, None)
            if self._cursor_active:
                context.window.cursor_modal_restore()
                self._cursor_active = False
        _redraw(context.window)

    def cancel(self, context):
        self.finish(context, keep=False)


class VIEW3D_OT_final_dimensions_ruler_clear(bpy.types.Operator):
    bl_idname = 'view3d.final_dimensions_ruler_clear'
    bl_label = 'Remove Selected Ruler'
    bl_description = 'Remove only the selected surface ruler'
    bl_options = {'INTERNAL'}

    def execute(self, context):
        manager = collection(context.window)
        if manager and manager.selected:
            # A selected unfinished addition is itself the item being removed.
            if manager.mode == 'ADD':
                manager.cancel_interaction()
            else:
                manager.cancel_interaction()
                manager.items.pop(manager.active)
                manager.active = min(manager.active, len(manager.items)-1)
                manager.recalculate()
            if not manager.items:
                operator = _sessions.get(context.window.as_pointer())
                if operator:
                    operator.finish(context, keep=False)
                _states.pop(context.window.as_pointer(), None)
        _changed(context)
        return {'FINISHED'}


class VIEW3D_OT_final_dimensions_ruler_select(bpy.types.Operator):
    bl_idname = 'view3d.final_dimensions_ruler_select'
    bl_label = 'Select Ruler'
    bl_description = 'Select a ruler to drag its endpoints or pick a missing point'
    bl_options = {'INTERNAL'}

    index: IntProperty(default=0, min=0, options={'SKIP_SAVE'})

    def execute(self, context):
        manager = collection(context.window)
        if manager is None or not manager.select(self.index):
            return {'CANCELLED'}
        if context.area is not None and context.area.type == 'VIEW_3D':
            manager.selected.area_pointer = context.area.as_pointer()
        _ensure_listener(context)
        _changed(context)
        return {'FINISHED'}


class VIEW3D_OT_final_dimensions_ruler_endpoint(bpy.types.Operator):
    bl_idname = 'view3d.final_dimensions_ruler_endpoint'
    bl_label = 'Pick Ruler Point'
    bl_description = 'Replace this endpoint by clicking an evaluated mesh surface'
    bl_options = {'INTERNAL'}

    endpoint: IntProperty(default=0, min=0, max=1, options={'SKIP_SAVE'})

    def execute(self, context):
        manager = collection(context.window)
        if manager is None or manager.selected is None or manager.mode == 'ADD':
            return {'CANCELLED'}
        manager.cancel_interaction()
        if not manager.begin_endpoint(self.endpoint, 'REPLACE'):
            return {'CANCELLED'}
        if context.area is not None and context.area.type == 'VIEW_3D':
            manager.selected.area_pointer = context.area.as_pointer()
        _ensure_listener(context)
        _changed(context)
        return {'FINISHED'}


def tick_window(window, epoch):
    manager = collection(window)
    if manager is None:
        return
    if manager.context_key != (window.scene.as_pointer(), window.view_layer.as_pointer()):
        operator = _sessions.get(window.as_pointer())
        with bpy.context.temp_override(window=window):
            if operator:
                operator.finish(bpy.context, keep=False)
            else:
                _states.pop(window.as_pointer(), None)
        _redraw(window)
        return
    if manager.epoch == epoch:
        return
    parent = _parent()
    previous = parent._measuring
    parent._measuring = True
    try:
        area, region = _area_context(window, manager)
        overrides = dict(window=window, scene=window.scene, view_layer=window.view_layer)
        if area and region:
            overrides.update(area=area, region=region)
        with bpy.context.temp_override(**overrides):
            manager.refresh(bpy.context, epoch)
    except (ValueError, RuntimeError, ReferenceError) as exc:
        # A global evaluation failure must not erase otherwise recoverable anchors.
        for item in manager.items:
            item.message = str(exc)
        manager.candidate = None
        manager.recalculate()
    finally:
        parent._measuring = previous
    _redraw(window)


def stop_all(clear=True):
    global _generation
    _generation += 1
    for window in tuple(bpy.context.window_manager.windows):
        operator = _sessions.get(window.as_pointer())
        if operator:
            with bpy.context.temp_override(window=window):
                operator.finish(bpy.context, keep=not clear)
    _sessions.clear()
    if clear:
        _states.clear()


def prune(live_windows):
    for pointer in tuple(_states):
        if pointer not in live_windows:
            _states.pop(pointer, None)
    for pointer in tuple(_sessions):
        if pointer not in live_windows:
            operator = _sessions.pop(pointer)
            try:
                if operator._timer is not None:
                    bpy.context.window_manager.event_timer_remove(operator._timer)
            except (ReferenceError, RuntimeError):
                pass
            operator._timer = None


def _visible_state():
    context = bpy.context
    if (not _registered or context.window is None or context.area is None
            or context.area.type != 'VIEW_3D' or context.region_data is None
            or context.space_data.region_quadviews
            or not context.space_data.overlay.show_overlays):
        return None
    manager = collection(context.window)
    if (manager is None or manager.epoch != _parent()._epoch
            or manager.context_key != (context.scene.as_pointer(), context.view_layer.as_pointer())):
        return None
    return manager


def _project(hit):
    if hit is None:
        return None
    return view3d_utils.location_3d_to_region_2d(
        bpy.context.region, bpy.context.region_data, Vector(hit['point']))


def _marker(shader, projected, color):
    if projected is None:
        return
    points = []
    for i in range(16):
        a, b = i*math.tau/16, (i+1)*math.tau/16
        points.extend(((projected.x+5*math.cos(a), projected.y+5*math.sin(a), 0.),
                       (projected.x+5*math.cos(b), projected.y+5*math.sin(b), 0.)))
    hover._lines(shader, points, color, 2.)


def _draw_pixel():
    manager = _visible_state()
    if manager is None:
        return
    context = bpy.context
    shader = gpu.shader.from_builtin('POLYLINE_UNIFORM_COLOR')
    old_blend, old_depth = gpu.state.blend_get(), gpu.state.depth_test_get()
    try:
        gpu.state.blend_set('ALPHA')
        gpu.state.depth_test_set('NONE')
        for index, item in enumerate(manager.items):
            selected = index == manager.active
            preview = (selected and manager.mode != 'IDLE'
                       and context.area.as_pointer() == item.area_pointer)
            first, second = item.endpoints(manager.slot if preview else None,
                                           manager.candidate if preview else None)
            a, b = _project(first), _project(second)
            color = (1., .75, .16, 1.) if selected else (.65, .72, .8, .7)
            if a is not None and b is not None:
                hover._lines(shader, [(a.x, a.y, 0.), (b.x, b.y, 0.)],
                             color, 2. if selected else 1.5)
                distance = (Vector(second['point'])-Vector(first['point'])).length
                label = f'{index+1} d: ' + format_length(context.scene, distance)
                if first.get('warnings') or second.get('warnings'):
                    label += ' *'
                overlay._text((a.x+b.x)/2+10, (a.y+b.y)/2+10, label, color)
            if selected:
                _marker(shader, a, (1., .7, .15, 1.))
                _marker(shader, b, (.35, 1., .55, 1.))
        item = manager.selected
        if (manager.mode != 'IDLE' and item
                and context.area.as_pointer() == item.area_pointer):
            instruction = ('Release to place point' if manager.mode == 'DRAG'
                           else f'Click surface for point {manager.slot+1}')
            overlay._text(18, 24, 'Surface ruler: '+(item.message or instruction)+' · Esc cancel',
                          (1., .8, .25, 1.))
    finally:
        gpu.state.blend_set(old_blend)
        gpu.state.depth_test_set(old_depth)


def draw_panel(layout, context):
    box = layout.box()
    row = box.row(align=True)
    row.operator(VIEW3D_OT_final_dimensions_ruler.bl_idname, text='Add Ruler', icon='ADD')
    remove = row.row(align=True)
    remove.enabled = get(context.window) is not None
    remove.operator(VIEW3D_OT_final_dimensions_ruler_clear.bl_idname, text='', icon='X')
    manager = collection(context.window)
    if manager is None or not manager.items:
        box.label(text='Two clicks on final surfaces')
        return
    # Ordinary panel rows use the sidebar's native scrolling, with no RNA collection.
    for index, item in enumerate(manager.items):
        value = format_length(context.scene, item.distance) if item.distance is not None else 'Pick points'
        op = box.operator(VIEW3D_OT_final_dimensions_ruler_select.bl_idname,
                          text=f'{index+1}: {value}', depress=index == manager.active,
                          icon='DRIVER_DISTANCE')
        op.index = index
    state = manager.selected
    if state is None:
        return
    row = box.row(align=True)
    row.enabled = manager.mode != 'ADD'
    for slot, hit in enumerate((state.first, state.second)):
        op = row.operator(VIEW3D_OT_final_dimensions_ruler_endpoint.bl_idname,
                          text=f'Pick Point {slot+1}' if hit is None else f'Replace {slot+1}')
        op.endpoint = slot
    if state.distance is not None:
        box.label(text='d: '+format_length(context.scene, state.distance))
    if manager.mode != 'IDLE':
        box.label(text='Release to place point' if manager.mode == 'DRAG'
                  else f'Pick point {manager.slot+1} · Esc cancel')
    else:
        box.label(text='Drag point · Right click to replace')
    for hit in (state.first, state.second):
        if hit:
            box.label(text=hit['object_name'])
    warnings = dict.fromkeys(warning for hit in (state.first, state.second, state.candidate)
                             if hit for warning in hit.get('warnings', ()))
    for message in (state.message, *warnings):
        for line in textwrap.wrap(message, width=28, break_long_words=False):
            box.label(text=line, icon='INFO')


_CLASSES = (VIEW3D_OT_final_dimensions_ruler, VIEW3D_OT_final_dimensions_ruler_clear,
            VIEW3D_OT_final_dimensions_ruler_select, VIEW3D_OT_final_dimensions_ruler_endpoint)


def register():
    global _registered
    for cls in _CLASSES:
        bpy.utils.register_class(cls)
    _registered = True
    _handles.append(bpy.types.SpaceView3D.draw_handler_add(_draw_pixel, (), 'WINDOW', 'POST_PIXEL'))


def unregister():
    global _registered
    _registered = False
    stop_all()
    for handle in _handles:
        bpy.types.SpaceView3D.draw_handler_remove(handle, 'WINDOW')
    _handles.clear()
    for cls in reversed(_CLASSES):
        bpy.utils.unregister_class(cls)
