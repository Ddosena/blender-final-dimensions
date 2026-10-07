"""Window-local surface rulers with one shared picker and passive editing listener."""

import sys
import textwrap
import time

import bpy
import gpu
from bpy.app.handlers import persistent
from bpy.props import BoolProperty, EnumProperty, IntProperty
from bpy_extras import view3d_utils
from mathutils import Vector

from . import drawing, overlay, hover, point_edit, ruler_style
from .snapping import SnapPicker, SUPPORTED_ELEMENTS
from .vertex_tracking import VertexResolutionDeferred


_states = {}
_sessions = {}
_handles = []
_registered = False
_generation = 0
_INTERVAL = 0.08
_HANDLE_RADIUS = 12.0
_LINE_HIT_RADIUS = 8.0
_DRAG_THRESHOLD = 5.0
_SLOTS = ('first', 'second')


def _world_hit(point):
    """A fixed world point, independent of the cursor and mesh topology."""
    point = tuple(float(value) for value in point)
    return {'point': point, 'normal': (0., 0., 0.),
            'object_name': '3D Cursor', 'anchor': {'kind': 'WORLD', 'point': point},
            'warnings': ()}


def _nearest_screen_ruler(coordinate, segments, active):
    """Pick a visible complete line; prefer the active ruler when overlapping."""
    mouse = Vector(coordinate)
    matches = []
    for index, first, second in segments:
        if first is None or second is None:
            continue
        a, b = Vector(first), Vector(second)
        delta = b - a
        factor = max(0., min(1., (mouse-a).dot(delta)/delta.length_squared)) if delta.length_squared else 0.
        distance = (mouse - (a + delta*factor)).length
        endpoint_distance = min((mouse-a).length, (mouse-b).length)
        if distance <= _LINE_HIT_RADIUS or endpoint_distance <= _HANDLE_RADIUS:
            matches.append((distance, index != active, index))
    return min(matches)[2] if matches else None


def _remove_ruler(context, index):
    """Delete one idle ruler and release its listener/proxy when necessary."""
    manager = collection(context.window)
    if manager is None or manager.mode != 'IDLE' or not 0 <= index < len(manager.items):
        return False
    point_edit.stop()
    manager.items.pop(index)
    if index < manager.active:
        manager.active -= 1
    manager.active = min(manager.active, len(manager.items)-1)
    manager.active_endpoint = None
    manager.recalculate()
    if not manager.items:
        pointer = context.window.as_pointer()
        operator = _sessions.get(pointer)
        if operator:
            operator.finish(context, keep=False)
        _states.pop(pointer, None)
    _changed(context)
    return True


def _native_point_operator_running(window):
    """Let Blender finish its own pie or transform before ending point selection."""
    try:
        return any(op.bl_rna.identifier not in {
            'VIEW3D_OT_final_dimensions_ruler', 'VIEW3D_OT_final_dimensions_hover'}
            for op in window.modal_operators)
    except (AttributeError, ReferenceError, RuntimeError):
        return False


def _ruler_snap_pie(menu, _context):
    pie = menu.layout.menu_pie()
    pie.operator('view3d.snap_cursor_to_grid', text='Cursor to Grid', icon='CURSOR')
    pie.operator(VIEW3D_OT_final_dimensions_ruler_to_cursor.bl_idname,
                 text='Point to 3D Cursor', icon='CURSOR')
    pie.operator(VIEW3D_OT_final_dimensions_cursor_to_ruler_point.bl_idname,
                 text='Cursor to Selected Point', icon='CURSOR')
    pie.operator('view3d.snap_cursor_to_center', text='Cursor to World Origin', icon='CURSOR')
    pie.operator(VIEW3D_OT_final_dimensions_cursor_to_ruler_point.bl_idname,
                 text='Cursor to Active Point', icon='CURSOR')


def _open_ruler_snap_pie(context, event, target):
    area, region = target
    with context.temp_override(window=context.window, area=area, region=region):
        bpy.context.window_manager.popup_menu_pie(
            event=event, draw_func=_ruler_snap_pie, title='Snap Ruler Point')


def _snap_settings(context, invert=False):
    settings = context.scene.tool_settings
    enabled = bool(settings.use_snap) != bool(invert)
    elements = frozenset(settings.snap_elements)
    source = context.window_manager.final_dimensions_snap_source
    space = context.space_data
    filters = tuple(bool(getattr(settings, name, False)) for name in (
        'use_snap_backface_culling', 'use_snap_selectable', 'use_snap_self',
        'use_snap_edit', 'use_snap_nonedit'))
    xray = (bool(space.shading.show_xray), bool(space.shading.show_xray_wireframe),
            space.shading.type, bool(space.shading.show_backface_culling))
    return enabled, elements, source, filters, xray, space.clip_start, space.clip_end


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
        self.label = ''
        self.precision = 3
        self.trim_zeros = True
        self.color = None

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
        self.active_endpoint = None
        self.mode = 'IDLE'
        self.slot = 0
        self.candidate = None
        self.original = None
        self.picker = None
        self.epoch = -1
        self._pending_vertex_resolution = False
        self._suppress_next_fork = False
        self.query_key = None

    @property
    def selected(self):
        return self.items[self.active] if 0 <= self.active < len(self.items) else None

    def recalculate(self):
        for index, item in enumerate(self.items):
            preview = index == self.active and self.mode != 'IDLE'
            item.recalculate(self.slot if preview else None,
                             self.candidate if preview else None)

    def resolve_hit(self, context, hit):
        anchor = hit['anchor']
        if anchor.get('kind') == 'WORLD':
            return tuple(anchor['point'])
        return tuple(self.picker.resolve(context, anchor))

    @staticmethod
    def _original_vertex_hit(hit):
        anchor = hit.get('anchor', {}) if hit else {}
        return (anchor.get('snap_source') == 'ORIGINAL' and
                anchor.get('snap_kind') == 'VERTEX' and 'vertex_id' in anchor)

    @staticmethod
    def _freeze_hit(hit):
        point = tuple(hit['point'])
        hit['anchor'] = {'kind': 'WORLD', 'point': point}
        hit['object_name'] = hit.get('object_name', 'Mesh') + ' (frozen)'

    def _fork_evidence(self, context, hits):
        evidence = []
        for hit in hits:
            if self._original_vertex_hit(hit):
                try:
                    evidence.append(self.picker.vertex_lineage(context, hit['anchor']))
                except VertexResolutionDeferred:
                    self._pending_vertex_resolution = True
                    evidence.append(None)
                except (ValueError, ReferenceError, RuntimeError):
                    evidence.append(None)
            else:
                evidence.append(None)
        descendants = [index for index, part in enumerate(evidence)
                       if part and part.get('descendant')]
        if not descendants:
            return evidence, None
        if len(descendants) == 2:
            a, b = hits
            if (a['anchor'].get('owner_uid') != b['anchor'].get('owner_uid') or
                    a['anchor'].get('mesh_uid') != b['anchor'].get('mesh_uid')):
                return evidence, None
            one, two = (part['descendant'] for part in evidence)
            if one['index'] == two['index']:
                return evidence, None
            same_face = set(one['face_tokens']).intersection(two['face_tokens'])
            same_edge = set(one['edge_tokens']).intersection(two['edge_tokens'])
            if not same_face and not same_edge:
                return evidence, None
        else:
            changed = descendants[0]
            other = 1 - changed
            other_hit = hits[other]
            if other_hit is None:
                return evidence, None
            if other_hit['anchor'].get('kind') != 'WORLD':
                other_proof = evidence[other]
                changed_anchor = hits[changed]['anchor']
                if (not other_proof or other_proof.get('original_index') is None or
                        changed_anchor.get('owner_uid') != other_hit['anchor'].get('owner_uid') or
                        changed_anchor.get('mesh_uid') != other_hit['anchor'].get('mesh_uid')):
                    return evidence, None
                try:
                    connected = self.picker.original_vertices_connected(
                        context, changed_anchor,
                        evidence[changed]['descendant']['index'],
                        other_proof['original_index'])
                except (ValueError, ReferenceError, RuntimeError):
                    return evidence, None
                if not connected:
                    return evidence, None
        # Keep source anchors separately: resolve() may rewrite the parents.
        return evidence, (evidence, tuple(dict(hit['anchor']) if hit else None for hit in hits))

    def _append_fork(self, context, source, source_index, proof):
        evidence, anchors = proof
        child_hits = []
        for slot, parent_hit in enumerate((source.first, source.second)):
            if parent_hit is None:
                return False
            hit = dict(parent_hit)
            hit['anchor'] = dict(parent_hit['anchor'])
            descendant = evidence[slot]['descendant'] if evidence[slot] else None
            if descendant is not None:
                if not anchors[slot] or not self._original_vertex_hit({'anchor': anchors[slot]}):
                    return False
                hit['anchor'] = dict(anchors[slot])
                identity = self.picker.bind_descendant(
                    context, anchors[slot], descendant['index'])
                hit['anchor'].update(identity)
                hit['anchor']['feature'] = descendant['index']
                hit['anchor']['indices'] = (descendant['index'],)
                hit['anchor']['weights'] = (1.,)
                hit['point'] = descendant['point']
                hit['object_name'] = self.picker.original_vertex_owner(context, anchors[slot]).name
            child_hits.append(hit)
        child = RulerState(context)
        child.first, child.second = child_hits
        child.area_pointer = source.area_pointer
        child.precision = source.precision
        child.trim_zeros = source.trim_zeros
        child.color = source.color
        child.label = (source.label if source.label.endswith(' (new)')
                       else source.label + ' (new)') if source.label else 'New topology (new)'
        child.message = f'New vertex branch of ruler {source_index+1}'
        child.recalculate()
        self.items.append(child)
        if (self.active == source_index and self.mode == 'IDLE' and
                self.active_endpoint is None):
            self.active = len(self.items) - 1
            self.active_endpoint = None
        return True

    def refresh(self, context, epoch):
        if self.context_key != (context.scene.as_pointer(), context.view_layer.as_pointer()):
            raise ValueError('Scene or view layer changed; start new rulers')
        if self.picker is None:
            self.picker = SnapPicker(context, epoch)
        else:
            self.picker.refresh(context, epoch)
        if self.epoch == epoch and not self._pending_vertex_resolution:
            return
        self.epoch = epoch
        self._pending_vertex_resolution = False
        self.query_key = None
        self.candidate = None
        fork_proofs = []
        freeze_slots = set()
        if not self._suppress_next_fork:
            for index, item in enumerate(tuple(self.items)):
                hits = (item.first, item.second)
                evidence, proof = self._fork_evidence(context, hits)
                if proof is not None:
                    for slot, part in enumerate(evidence):
                        if part and part.get('freeze_parent'):
                            freeze_slots.add((id(item), slot))
                    fork_proofs.append((index, item, proof))
        # An unavailable anchor affects only its own endpoint.
        for item in self.items:
            for slot, attr in enumerate(_SLOTS):
                hit = getattr(item, attr)
                if hit is None:
                    continue
                if (id(item), slot) in freeze_slots:
                    self._freeze_hit(hit)
                    item.errors[slot] = (
                        f'Point {slot+1} preserved at its prior position; '
                        'pick it again to rebind.')
                    continue
                try:
                    hit['point'] = self.resolve_hit(context, hit)
                except VertexResolutionDeferred:
                    self._pending_vertex_resolution = True
                except (ValueError, ReferenceError, RuntimeError) as exc:
                    self._freeze_hit(hit)
                    item.errors[slot] = f'Point {slot+1} frozen: {exc}. Pick it again to rebind.'
            item.message = item.error_message()
        # Cancel restores the current deformed anchor, never its old world point.
        if self.original is not None:
            try:
                self.original['point'] = self.resolve_hit(context, self.original)
            except VertexResolutionDeferred:
                self._pending_vertex_resolution = True
            except (ValueError, ReferenceError, RuntimeError):
                self._freeze_hit(self.original)
        for index, item, proof in fork_proofs:
            try:
                self._append_fork(context, item, index, proof)
            except (ValueError, ReferenceError, RuntimeError):
                # The old ruler remains visible even if descendant binding
                # becomes ambiguous after another edit in this same epoch.
                continue
        self.recalculate()
        if not self._pending_vertex_resolution:
            self._suppress_next_fork = False

    def pick(self, context, epoch, region, rv3d, coordinate, force=False, snap_invert=False):
        self.refresh(context, epoch)
        snap_settings = _snap_settings(context, snap_invert)
        key = (coordinate, tuple(v for row in rv3d.perspective_matrix for v in row),
               region.width, region.height, epoch, snap_settings)
        if not force and key == self.query_key:
            return
        self.query_key = key
        direction = view3d_utils.region_2d_to_vector_3d(region, rv3d, coordinate)
        origin = view3d_utils.region_2d_to_origin_3d(
            region, rv3d, coordinate, clamp=max(context.space_data.clip_end, 1.0))
        item = self.selected
        try:
            if snap_settings[0]:
                elements = snap_settings[1] & SUPPORTED_ELEMENTS
                if elements:
                    self.candidate = self.picker.snap(
                        context, origin, direction, region, rv3d, coordinate,
                        elements, snap_settings[2])
                    message = 'Move near a matching mesh element'
                else:
                    self.candidate = None
                    message = 'Choose Vertex, Edge or Face snapping'
            else:
                self.candidate = self.picker.pick(context, origin, direction)
                message = 'Move onto a visible mesh surface'
            item.message = item.error_message() if self.candidate else message
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
            self.active_endpoint = None
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
        self.active_endpoint = None
        self.mode, self.slot = 'ADD', 0

    def select(self, index):
        if not 0 <= index < len(self.items):
            return False
        if self.mode == 'ADD' and index == self.active:
            return True
        item = self.items[index]
        self.cancel_interaction()
        self.active_endpoint = None
        if item in self.items:
            self.active = self.items.index(item)
        # Selecting an incomplete surviving ruler offers its missing point.
        if self.selected:
            if self.selected.first is None:
                self.begin_endpoint(0, 'REPLACE')
            elif self.selected.second is None:
                self.begin_endpoint(1, 'REPLACE')
        return self.selected is not None

    def activate_endpoint(self, index, slot):
        if not 0 <= index < len(self.items) or slot not in (0, 1):
            return False
        if self.mode != 'IDLE':
            self.cancel_interaction()
            if not 0 <= index < len(self.items):
                return False
        self.active = index
        self.active_endpoint = slot
        self.recalculate()
        return True

    def begin_endpoint(self, slot, mode):
        item = self.selected
        if item is None or slot not in (0, 1):
            return False
        self.mode, self.slot = mode, slot
        self.active_endpoint = slot
        hit = getattr(item, _SLOTS[slot])
        self.original = dict(hit) if hit else None
        self.candidate = dict(hit) if hit and mode == 'DRAG' else None
        self.query_key = None
        if mode == 'REPLACE':
            setattr(item, _SLOTS[slot], None)
        self.recalculate()
        return True

    def release_drag_for_menu(self):
        """Retain the original while a held-button drag becomes click placement."""
        if self.mode == 'DRAG' and self.selected is not None:
            self.mode = 'REPLACE'
            setattr(self.selected, _SLOTS[self.slot], None)
            self.candidate = None
            self.query_key = None
            self.recalculate()

    def confirm(self):
        if self.selected is None or self.candidate is None:
            return False
        item = self.selected
        setattr(item, _SLOTS[self.slot], dict(self.candidate))
        item.errors.pop(self.slot, None)
        self.active_endpoint = self.slot
        if self.mode == 'ADD' and self.slot == 0:
            self.slot = 1
            self.active_endpoint = 1
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
        operator._pending_pie = False
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
            point_edit.stop()
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
        self._snap_invert = False
        self._last_tick = 0.0
        self._cursor_active = False
        self._pending_pie = False
        self._native_pie_open = False
        self._drag_start_mouse = None
        self._drag_started = False
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
                             area.spaces.active.region_3d, coordinate, force,
                             self._snap_invert)
        finally:
            parent._measuring = previous
        _redraw(context.window)

    def _near_endpoint(self, context):
        target = hover._viewport_at(context.window, self._mouse)
        if target is None:
            return None
        area, region = target
        if area.spaces.active.region_quadviews or not area.spaces.active.overlay.show_overlays:
            return None
        coordinate = Vector((self._mouse[0]-region.x, self._mouse[1]-region.y))
        closest, distance = None, _HANDLE_RADIUS
        for index, item in enumerate(self._state.items):
            if item.area_pointer != area.as_pointer():
                continue
            for slot, hit in enumerate((item.first, item.second)):
                if hit is None:
                    continue
                point = view3d_utils.location_3d_to_region_2d(
                    region, area.spaces.active.region_3d, Vector(hit['point']))
                if point is not None and (point-coordinate).length <= distance:
                    closest, distance = (index, slot), (point-coordinate).length
        return closest

    def _near_ruler(self, context):
        if (self._state.epoch != _parent()._epoch or
                self._state.context_key != (context.scene.as_pointer(), context.view_layer.as_pointer())):
            return None
        target = hover._viewport_at(context.window, self._mouse)
        if target is None:
            return None
        area, region = target
        space = area.spaces.active
        if space.region_quadviews or not space.overlay.show_overlays:
            return None
        coordinate = (self._mouse[0]-region.x, self._mouse[1]-region.y)
        segments = []
        # Rulers are drawn in every viewport of their window, even when their
        # endpoint interaction owner is another area.
        for index, item in enumerate(self._state.items):
            points = [view3d_utils.location_3d_to_region_2d(
                region, space.region_3d, Vector(hit['point'])) if hit else None
                for hit in (item.first, item.second)]
            segments.append((index, *points))
        return _nearest_screen_ruler(coordinate, segments, self._state.active)

    def _active_screen_distance(self, context):
        manager = self._state
        target = hover._viewport_at(context.window, self._mouse)
        if target is None or manager.selected is None or manager.active_endpoint is None:
            return float('inf')
        area, region = target
        hit = getattr(manager.selected, _SLOTS[manager.active_endpoint])
        if hit is None or area.as_pointer() != manager.selected.area_pointer:
            return float('inf')
        projected = view3d_utils.location_3d_to_region_2d(
            region, area.spaces.active.region_3d, Vector(hit['point']))
        if projected is None:
            return float('inf')
        return (projected-Vector((self._mouse[0]-region.x, self._mouse[1]-region.y))).length

    def _cancel_interaction(self, context):
        self._pending_pie = False
        self._drag_start_mouse = None
        self._drag_started = False
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
        if event.type in {'MOUSEMOVE', 'INBETWEEN_MOUSEMOVE', 'LEFTMOUSE', 'RIGHTMOUSE', 'MIDDLEMOUSE'}:
            self._mouse = (event.mouse_x, event.mouse_y)
            self._snap_invert = bool(getattr(event, 'ctrl', False))
        elif event.type in {'LEFT_CTRL', 'RIGHT_CTRL'}:
            self._snap_invert = bool(getattr(event, 'ctrl', False))
            if manager.mode != 'IDLE' and (manager.mode != 'DRAG' or self._drag_started):
                self._safe_update(context, force=True)
                return {'RUNNING_MODAL'}
        elif event.type == 'WINDOW_DEACTIVATE':
            self._mouse = None
            self._snap_invert = False
            if manager.mode == 'DRAG' or self._pending_pie:
                return self._cancel_interaction(context)
        if event.type == 'MIDDLEMOUSE':
            return {'PASS_THROUGH'}
        if event.type == 'LEFTMOUSE' and event.alt and manager.mode == 'IDLE':
            if (event.value != 'PRESS'
                    or event.shift or event.ctrl or event.oskey
                    or self._native_pie_open or _native_point_operator_running(context.window)):
                return {'PASS_THROUGH'}
            index = self._near_ruler(context)
            if index is None:
                return {'PASS_THROUGH'}
            if _remove_ruler(context, index):
                return {'RUNNING_MODAL'} if manager.items else {'FINISHED'}
            return {'PASS_THROUGH'}
        if manager.mode == 'IDLE' and point_edit.active(manager):
            if event.type == 'S' and event.value == 'PRESS' and event.shift and not event.ctrl:
                self._native_pie_open = True
                return {'PASS_THROUGH'}
            if event.type in {'G', 'R', 'S'} and event.value == 'PRESS' and not event.shift:
                return {'PASS_THROUGH'}
            if event.type == 'ESC' and event.value == 'PRESS':
                if self._native_pie_open or _native_point_operator_running(context.window):
                    self._native_pie_open = False
                    return {'PASS_THROUGH'}
                point_edit.stop()
                _redraw(context.window)
                return {'RUNNING_MODAL'}
            if self._native_pie_open and event.type == 'LEFTMOUSE':
                if event.value == 'RELEASE':
                    self._native_pie_open = False
                return {'PASS_THROUGH'}
            if _native_point_operator_running(context.window):
                return {'PASS_THROUGH'}
            if event.type == 'LEFTMOUSE' and event.value == 'PRESS':
                match = self._near_endpoint(context)
                if match is None and self._active_screen_distance(context) <= 120.:
                    return {'PASS_THROUGH'}
                point_edit.stop()
                if match is None:
                    _redraw(context.window)
                    return {'PASS_THROUGH'}
            if event.type == 'RIGHTMOUSE' and event.value == 'PRESS':
                if self._near_endpoint(context) is None:
                    return {'PASS_THROUGH'}
                point_edit.stop()
        if event.type == 'ESC' and event.value == 'PRESS' and manager.mode != 'IDLE':
            return self._cancel_interaction(context)
        if event.type == 'ESC' and event.value == 'PRESS' and manager.active_endpoint is not None:
            manager.active_endpoint = None
            _redraw(context.window)
            return {'RUNNING_MODAL'}
        pie_target = (hover._viewport_at(context.window, (event.mouse_x, event.mouse_y))
                      if (event.type == 'S' and event.value == 'PRESS' and event.shift
                          and not event.ctrl and not event.alt and not event.oskey) else None)
        if (pie_target is not None and not self._pending_pie
                and (manager.mode != 'IDLE' or
                     manager.active_endpoint is not None and not point_edit.active(manager))
                and manager.selected is not None
                and not pie_target[0].spaces.active.region_quadviews
                and pie_target[0].spaces.active.overlay.show_overlays
                and pie_target[0].as_pointer() == manager.selected.area_pointer):
            was_dragging = manager.mode == 'DRAG'
            manager.release_drag_for_menu()
            self._mouse = None
            self._snap_invert = False
            self._pending_pie = was_dragging
            _redraw(context.window)
            if not was_dragging:
                _open_ruler_snap_pie(context, event, pie_target)
            return {'RUNNING_MODAL'}
        if self._pending_pie:
            self._mouse = None
            if event.type == 'LEFTMOUSE' and event.value == 'RELEASE':
                self._pending_pie = False
                target = hover._viewport_at(context.window, (event.mouse_x, event.mouse_y))
                if (target is None or target[0].as_pointer() != manager.selected.area_pointer
                        or target[0].spaces.active.region_quadviews
                        or not target[0].spaces.active.overlay.show_overlays):
                    return self._cancel_interaction(context)
                _open_ruler_snap_pie(context, event, target)
            return {'RUNNING_MODAL'}
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
                if manager.mode != 'IDLE' and (manager.mode != 'DRAG' or self._drag_started):
                    self._safe_update(context)
            return {'PASS_THROUGH'}
        if manager.mode == 'DRAG':
            if event.type in {'MOUSEMOVE', 'INBETWEEN_MOUSEMOVE'}:
                if (self._drag_start_mouse is not None and
                        (Vector(self._mouse)-Vector(self._drag_start_mouse)).length >= _DRAG_THRESHOLD):
                    self._drag_started = True
                if self._drag_started:
                    self._safe_update(context)
                return {'RUNNING_MODAL'}
            if event.type == 'LEFTMOUSE' and event.value == 'RELEASE':
                if self._drag_started:
                    self._safe_update(context, force=True)
                    if manager.candidate is None:
                        manager.cancel_interaction()
                    else:
                        manager.confirm()
                else:
                    manager.idle()
                    if manager.selected and getattr(manager.selected, _SLOTS[manager.active_endpoint]) is not None:
                        if not point_edit.activate(context, manager, manager.active,
                                                   manager.active_endpoint, _world_hit):
                            manager.selected.message = 'Could not activate native point transform'
                self._drag_start_mouse = None
                self._drag_started = False
                self._sync_cursor(context)
                _redraw(context.window)
                return {'RUNNING_MODAL'}
        if event.type in {'MOUSEMOVE', 'INBETWEEN_MOUSEMOVE'} and manager.mode != 'IDLE':
            self._safe_update(context)
        if event.alt:
            return {'PASS_THROUGH'}
        if event.type in {'LEFTMOUSE', 'RIGHTMOUSE'} and event.value == 'PRESS':
            if manager.mode == 'IDLE':
                tick_window(context.window, _parent()._epoch)
                match = self._near_endpoint(context)
                if match is None:
                    if event.type == 'LEFTMOUSE' and manager.active_endpoint is not None:
                        manager.active_endpoint = None
                        _redraw(context.window)
                    return {'PASS_THROUGH'}
                index, slot = match
                manager.activate_endpoint(index, slot)
                manager.begin_endpoint(slot, 'DRAG' if event.type == 'LEFTMOUSE' else 'REPLACE')
                self._drag_start_mouse = self._mouse if event.type == 'LEFTMOUSE' else None
                self._drag_started = False
                self._sync_cursor(context)
                hover.clear_window(context.window)
                if event.type == 'RIGHTMOUSE':
                    self._safe_update(context, force=True)
                return {'RUNNING_MODAL'}
            if self._target(context) is None:
                return {'PASS_THROUGH'}
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
        self._pending_pie = False
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
                point_edit.stop()
                manager.cancel_interaction()
            else:
                manager.cancel_interaction()
                _remove_ruler(context, manager.active)
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
        point_edit.stop()
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
        point_edit.stop()
        manager.cancel_interaction()
        if not manager.begin_endpoint(self.endpoint, 'REPLACE'):
            return {'CANCELLED'}
        if context.area is not None and context.area.type == 'VIEW_3D':
            manager.selected.area_pointer = context.area.as_pointer()
        _ensure_listener(context)
        _changed(context)
        return {'FINISHED'}


class VIEW3D_OT_final_dimensions_ruler_activate(bpy.types.Operator):
    bl_idname = 'view3d.final_dimensions_ruler_activate'
    bl_label = 'Select Ruler Point'
    bl_description = 'Keep this ruler point selected for snapping and transforms'
    bl_options = {'INTERNAL'}

    endpoint: IntProperty(default=0, min=0, max=1, options={'SKIP_SAVE'})

    def execute(self, context):
        manager = collection(context.window)
        if manager is None or manager.selected is None or manager.mode == 'ADD':
            return {'CANCELLED'}
        if not manager.activate_endpoint(manager.active, self.endpoint):
            return {'CANCELLED'}
        hit = getattr(manager.selected, _SLOTS[self.endpoint])
        if hit is not None and not point_edit.activate(context, manager, manager.active,
                                                   self.endpoint, _world_hit):
            manager.selected.message = 'Could not activate native point transform'
        _ensure_listener(context)
        _changed(context)
        return {'FINISHED'}


class VIEW3D_OT_final_dimensions_ruler_to_cursor(bpy.types.Operator):
    bl_idname = 'view3d.final_dimensions_ruler_to_cursor'
    bl_label = 'Point to 3D Cursor'
    bl_description = 'Place the active ruler point at the current 3D Cursor position'
    bl_options = {'INTERNAL'}

    @classmethod
    def poll(cls, context):
        manager = collection(context.window)
        return bool(manager and manager.selected and
                    (manager.mode != 'IDLE' or manager.active_endpoint is not None))

    def execute(self, context):
        manager = collection(context.window)
        if manager is None or manager.selected is None or (manager.mode == 'IDLE' and
                manager.active_endpoint is None):
            return {'CANCELLED'}
        if manager.mode == 'IDLE':
            slot = manager.active_endpoint
            setattr(manager.selected, _SLOTS[slot], _world_hit(context.scene.cursor.location))
            manager.selected.errors.pop(slot, None)
            manager.recalculate()
        else:
            manager.candidate = _world_hit(context.scene.cursor.location)
            manager.confirm()
        operator = _sessions.get(context.window.as_pointer())
        if operator:
            operator._mouse = None
            operator._pending_pie = False
            operator._sync_cursor(context)
        _redraw(context.window)
        return {'FINISHED'}


class VIEW3D_OT_final_dimensions_cursor_to_ruler_point(bpy.types.Operator):
    bl_idname = 'view3d.final_dimensions_cursor_to_ruler_point'
    bl_label = 'Cursor to Ruler Point'
    bl_description = 'Move the 3D Cursor to the selected ruler point'
    bl_options = {'INTERNAL'}

    @classmethod
    def poll(cls, context):
        manager = collection(context.window)
        return bool(manager and manager.selected and manager.active_endpoint is not None and
                    (getattr(manager.selected, _SLOTS[manager.active_endpoint]) is not None or
                     manager.original is not None))

    def execute(self, context):
        manager = collection(context.window)
        if manager is None or manager.selected is None or manager.active_endpoint is None:
            return {'CANCELLED'}
        hit = getattr(manager.selected, _SLOTS[manager.active_endpoint]) or manager.original
        if hit is None:
            return {'CANCELLED'}
        context.scene.cursor.location = hit['point']
        return {'FINISHED'}


def tick_window(window, epoch):
    manager = collection(window)
    if manager is None:
        return
    if manager.context_key != (window.scene.as_pointer(), window.view_layer.as_pointer()):
        point_edit.stop(restore=False)
    if point_edit.sync(window, manager, _world_hit):
        _redraw(window)
    if manager.context_key != (window.scene.as_pointer(), window.view_layer.as_pointer()):
        operator = _sessions.get(window.as_pointer())
        with bpy.context.temp_override(window=window):
            if operator:
                operator.finish(bpy.context, keep=False)
            else:
                _states.pop(window.as_pointer(), None)
        _redraw(window)
        return
    if window.as_pointer() not in _sessions and manager.items:
        area, region = _area_context(window, manager)
        if area and region:
            try:
                with bpy.context.temp_override(window=window, area=area, region=region):
                    _ensure_listener(bpy.context)
            except (ValueError, ReferenceError, RuntimeError):
                pass
    if manager.epoch == epoch and not manager._pending_vertex_resolution:
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
    point_edit.sync(window, manager, _world_hit)
    _redraw(window)


def after_history_change():
    """Keep completed measurements while releasing pre-Undo UI and caches."""
    global _generation
    _generation += 1
    # undo_pre/redo_pre already stop the proxy. Undo may recreate its helper ID.
    point_edit.cleanup_tagged()
    windows = {window.as_pointer(): window
               for window in tuple(bpy.context.window_manager.windows)}
    for pointer, operator in tuple(_sessions.items()):
        window = windows.get(pointer)
        if window is not None:
            try:
                with bpy.context.temp_override(window=window):
                    operator.finish(bpy.context, keep=True)
            except (ValueError, ReferenceError, RuntimeError):
                _sessions.pop(pointer, None)
        else:
            _sessions.pop(pointer, None)
    for pointer, manager in tuple(_states.items()):
        window = windows.get(pointer)
        if window is None:
            _states.pop(pointer, None)
            continue
        if manager.mode != 'IDLE':
            manager.cancel_interaction()
        manager.context_key = (window.scene.as_pointer(), window.view_layer.as_pointer())
        manager.active_endpoint = None
        manager.candidate = None
        manager.original = None
        manager.query_key = None
        manager.picker = None
        manager.epoch = -1
        manager._pending_vertex_resolution = False
        manager._suppress_next_fork = True
        manager.recalculate()
        _redraw(window)


def stop_all(clear=True):
    global _generation
    _generation += 1
    point_edit.stop()
    point_edit.cleanup_tagged()
    for window in tuple(bpy.context.window_manager.windows):
        operator = _sessions.get(window.as_pointer())
        if operator:
            with bpy.context.temp_override(window=window):
                operator.finish(bpy.context, keep=not clear)
    _sessions.clear()
    if clear:
        _states.clear()


def prune(live_windows):
    if point_edit.session_window() is not None and point_edit.session_window() not in live_windows:
        point_edit.stop(restore=False)
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


@persistent
def _before_save(_dummy):
    # A native proxy is transient UI state and must never enter a saved blend.
    point_edit.stop()
    point_edit.cleanup_tagged()
    for manager in _states.values():
        manager.active_endpoint = None


@persistent
def _before_history_step(_dummy):
    point_edit.stop()
    point_edit.cleanup_tagged()
    for manager in _states.values():
        manager.active_endpoint = None


@persistent
def _after_load(_dummy):
    point_edit.cleanup_tagged()


def _project(hit):
    if hit is None:
        return None
    return view3d_utils.location_3d_to_region_2d(
        bpy.context.region, bpy.context.region_data, Vector(hit['point']))


def _marker(projected, color, radius=5.):
    if projected is None:
        return
    drawing.ring((projected.x, projected.y), radius, color, 2.)


def _draw_pixel():
    manager = _visible_state()
    if manager is None:
        return
    context = bpy.context
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
            color = ruler_style.line_color(context.scene, item, selected)
            if a is not None and b is not None:
                drawing.line((a.x, a.y), (b.x, b.y), color,
                             2. if selected else 1.5)
                distance = (Vector(second['point'])-Vector(first['point'])).length
                label = ruler_style.label_text(context.scene, item, index, distance)
                if first.get('warnings') or second.get('warnings'):
                    label += ' *'
                overlay._text((a.x+b.x)/2+10, (a.y+b.y)/2+10, label, color)
            if selected:
                _marker(a, color)
                _marker(b, color)
                if manager.active_endpoint is not None:
                    active_point = (a, b)[manager.active_endpoint]
                    _marker(active_point, (1., 1., 1., 1.), 9.)
                    if active_point is not None:
                        overlay._text(active_point.x+12, active_point.y+10,
                                      f'Active point {manager.active_endpoint+1}',
                                      (1., 1., 1., 1.))
        item = manager.selected
        if (manager.mode != 'IDLE' and item
                and context.area.as_pointer() == item.area_pointer):
            candidate = manager.candidate
            projected = _project(candidate)
            if projected is not None and candidate.get('snap_kind'):
                kind = candidate['snap_kind'].replace('_', ' ').title()
                source = candidate['snap_source'].title()
                overlay._text(projected.x+12, projected.y-18, source+' · '+kind,
                              (1., .85, .3, 1.))
            operator = _sessions.get(context.window.as_pointer())
            instruction = ('Release LMB for Shift+S menu' if operator and operator._pending_pie
                           else 'Release to place point' if manager.mode == 'DRAG'
                           else f'Click surface for point {manager.slot+1}')
            message = instruction if operator and operator._pending_pie else item.message or instruction
            overlay._text(18, 24, 'Surface ruler: '+message
                          +' · Shift+S: 3D Cursor · Esc cancel',
                          (1., .8, .25, 1.))
    finally:
        gpu.state.blend_set(old_blend)
        gpu.state.depth_test_set(old_depth)


def draw_panel(layout, context):
    ruler_style.draw_defaults(layout, context)
    box = layout.box()
    row = box.row(align=True)
    row.operator(VIEW3D_OT_final_dimensions_ruler.bl_idname, text='Add Ruler', icon='ADD')
    remove = row.row(align=True)
    remove.enabled = get(context.window) is not None
    remove.operator(VIEW3D_OT_final_dimensions_ruler_clear.bl_idname, text='', icon='X')
    box.prop(context.window_manager, 'final_dimensions_snap_source', text='Snap mesh')
    settings = context.scene.tool_settings
    box.label(text='Blender snapping: '+('On' if settings.use_snap else 'Off'),
              icon='SNAP_ON' if settings.use_snap else 'SNAP_OFF')
    manager = collection(context.window)
    if manager is None or not manager.items:
        box.label(text='Two clicks on final surfaces')
        return
    # Ordinary panel rows use the sidebar's native scrolling, with no RNA collection.
    for index, item in enumerate(manager.items):
        op = box.operator(VIEW3D_OT_final_dimensions_ruler_select.bl_idname,
                          text=ruler_style.label_text(context.scene, item, index, item.distance),
                          depress=index == manager.active,
                          icon='DRIVER_DISTANCE')
        op.index = index
    state = manager.selected
    if state is None:
        return
    ruler_style.draw_selected(box, context)
    row = box.row(align=True)
    row.enabled = manager.mode != 'ADD'
    for slot in (0, 1):
        op = row.operator(VIEW3D_OT_final_dimensions_ruler_activate.bl_idname,
                          text=f'Select Point {slot+1}',
                          depress=manager.active_endpoint == slot,
                          icon='RADIOBUT_ON' if manager.active_endpoint == slot else 'RADIOBUT_OFF')
        op.endpoint = slot
    row = box.row(align=True)
    row.enabled = manager.mode != 'ADD'
    for slot, hit in enumerate((state.first, state.second)):
        op = row.operator(VIEW3D_OT_final_dimensions_ruler_endpoint.bl_idname,
                          text=f'Pick Point {slot+1}' if hit is None else f'Replace {slot+1}')
        op.endpoint = slot
    if state.distance is not None:
        box.label(text=ruler_style.label_text(context.scene, state, manager.active, state.distance))
    if manager.mode != 'IDLE':
        operator = _sessions.get(context.window.as_pointer())
        box.label(text='Release LMB for Shift+S menu' if operator and operator._pending_pie
                  else 'Release to place point' if manager.mode == 'DRAG'
                  else f'Pick point {manager.slot+1} · Esc cancel')
        box.label(text='Shift+S: 3D Cursor')
        box.operator(VIEW3D_OT_final_dimensions_ruler_to_cursor.bl_idname,
                     text='Point to 3D Cursor', icon='CURSOR')
    elif manager.active_endpoint is not None:
        box.label(text=f'Active point {manager.active_endpoint+1} · Shift+S · Esc deselect')
        box.operator(VIEW3D_OT_final_dimensions_ruler_to_cursor.bl_idname,
                     text='Point to 3D Cursor', icon='CURSOR')
    else:
        box.label(text='Drag point · Right click to replace')
        box.label(text='Alt + left click line to remove')
    for hit in (state.first, state.second):
        if hit:
            box.label(text=hit['object_name'])
    warnings = dict.fromkeys(warning for hit in (state.first, state.second, state.candidate)
                             if hit for warning in hit.get('warnings', ()))
    for message in (state.message, *warnings):
        for line in textwrap.wrap(message, width=28, break_long_words=False):
            box.label(text=line, icon='INFO')


_CLASSES = (VIEW3D_OT_final_dimensions_ruler, VIEW3D_OT_final_dimensions_ruler_clear,
            VIEW3D_OT_final_dimensions_ruler_select, VIEW3D_OT_final_dimensions_ruler_endpoint,
            VIEW3D_OT_final_dimensions_ruler_activate,
            VIEW3D_OT_final_dimensions_ruler_to_cursor,
            VIEW3D_OT_final_dimensions_cursor_to_ruler_point)


def _cleanup_after_registration():
    # Extension enable runs register() with bpy.data restricted. Scene IDs are
    # available only after Blender returns to its normal event loop.
    if _registered and not point_edit.active():
        point_edit.cleanup_tagged()
    return None


def register():
    global _registered
    bpy.types.WindowManager.final_dimensions_snap_source = EnumProperty(
        name='Snap mesh',
        description='Geometry used for ruler snapping when the Blender magnet is enabled',
        items=(('ORIGINAL', 'Original', 'Base mesh before modifiers'),
               ('FINAL', 'Final', 'Viewport mesh after modifiers'),
               ('BOTH', 'Both', 'Base mesh and viewport result')),
        default='BOTH',
    )
    for cls in _CLASSES:
        bpy.utils.register_class(cls)
    _registered = True
    if not bpy.app.timers.is_registered(_cleanup_after_registration):
        bpy.app.timers.register(_cleanup_after_registration, first_interval=0.0)
    if _before_save not in bpy.app.handlers.save_pre:
        bpy.app.handlers.save_pre.append(_before_save)
    for handlers, callback in ((bpy.app.handlers.undo_pre, _before_history_step),
                               (bpy.app.handlers.redo_pre, _before_history_step),
                               (bpy.app.handlers.load_post, _after_load)):
        if callback not in handlers:
            handlers.append(callback)
    _handles.append(bpy.types.SpaceView3D.draw_handler_add(_draw_pixel, (), 'WINDOW', 'POST_PIXEL'))


def unregister():
    global _registered
    _registered = False
    if bpy.app.timers.is_registered(_cleanup_after_registration):
        bpy.app.timers.unregister(_cleanup_after_registration)
    if _before_save in bpy.app.handlers.save_pre:
        bpy.app.handlers.save_pre.remove(_before_save)
    for handlers, callback in ((bpy.app.handlers.undo_pre, _before_history_step),
                               (bpy.app.handlers.redo_pre, _before_history_step),
                               (bpy.app.handlers.load_post, _after_load)):
        if callback in handlers:
            handlers.remove(callback)
    stop_all()
    for handle in _handles:
        bpy.types.SpaceView3D.draw_handler_remove(handle, 'WINDOW')
    _handles.clear()
    for cls in reversed(_CLASSES):
        bpy.utils.unregister_class(cls)
    del bpy.types.WindowManager.final_dimensions_snap_source
