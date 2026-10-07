"""One temporary Blender object for native ruler-point transforms and snapping."""

import math

import bpy


_session = None
_TAG = '_final_dimensions_point_proxy'
_GIZMOS = ('show_gizmo_object_translate', 'show_gizmo_object_rotate',
           'show_gizmo_object_scale')


def _alive(obj):
    try:
        return bool(obj and obj.as_pointer() and bpy.data.objects.get(obj.name) == obj)
    except (AttributeError, ReferenceError, RuntimeError):
        return False


def _window_for(pointer):
    try:
        return next((w for w in bpy.context.window_manager.windows
                     if w.as_pointer() == pointer), None)
    except (AttributeError, ReferenceError):
        return None


def _view_region(window, area_pointer):
    if window is None:
        return None, None
    area = next((a for a in window.screen.areas
                 if a.type == 'VIEW_3D' and a.as_pointer() == area_pointer), None)
    region = next((r for r in area.regions if r.type == 'WINDOW'), None) if area else None
    return area, region


def active(manager=None):
    session = _session
    return bool(session and (manager is None or session['manager'] is manager)
                and _alive(session['proxy']))


def proxy(manager=None):
    return _session['proxy'] if active(manager) else None


def session_window():
    return _session['window_pointer'] if _session else None


def _restore_mode(session, window):
    mode = session['mode']
    if mode == 'OBJECT' or window is None:
        return
    area, region = _view_region(window, session['area_pointer'])
    if area is None or region is None:
        return
    active_obj = session['active_object']
    if not _alive(active_obj):
        return
    try:
        with bpy.context.temp_override(window=window, scene=session['scene'],
                                       view_layer=session['view_layer'],
                                       area=area, region=region):
            bpy.ops.object.mode_set(mode=mode)
    except (ValueError, RuntimeError, ReferenceError):
        pass


def _apply_position(session):
    """Commit only a real Empty displacement; retain a mesh anchor on cancel."""
    manager = session['manager']
    helper = session['proxy']
    index, slot = session['index'], session['slot']
    if not _alive(helper) or not 0 <= index < len(manager.items):
        return False
    item = manager.items[index]
    attr = 'first' if slot == 0 else 'second'
    hit = getattr(item, attr)
    if hit is None:
        return False
    position = tuple(float(v) for v in helper.matrix_world.translation)
    if math.dist(position, session['last_point']) <= 1e-7:
        return False
    if math.dist(position, session['baseline']) <= 1e-7:
        setattr(item, attr, dict(session['original_hit']))
    else:
        setattr(item, attr, session['world_hit'](position))
    item.errors.pop(slot, None)
    session['last_point'] = position
    manager.recalculate()
    return True


def stop(restore=True):
    """Remove only our owned ID and restore selection unless the user changed it."""
    global _session
    session = _session
    _session = None
    if session is None:
        return
    _apply_position(session)
    manager = session['manager']
    proxy_obj = session['proxy']
    window = _window_for(session['window_pointer'])
    for space, values in session['gizmos']:
        try:
            for prop, old in values.items():
                setattr(space, prop, old)
        except ReferenceError:
            pass
    try:
        if _alive(proxy_obj):
            bpy.data.objects.remove(proxy_obj, do_unlink=True)
    except (ReferenceError, RuntimeError):
        pass
    if restore:
        layer = session['view_layer']
        try:
            for obj in tuple(layer.objects):
                if obj is not None and obj.select_get(view_layer=layer):
                    obj.select_set(False, view_layer=layer)
            for obj in session['selected_objects']:
                if _alive(obj) and obj.name in layer.objects:
                    obj.select_set(True, view_layer=layer)
            original = session['active_object']
            layer.objects.active = original if _alive(original) and original.name in layer.objects else None
            _restore_mode(session, window)
        except (ReferenceError, RuntimeError):
            pass
    try:
        if manager.active_endpoint == session['slot'] and manager.active == session['index']:
            manager.active_endpoint = None
    except (AttributeError, ReferenceError):
        pass


def activate(context, manager, index, slot, world_hit):
    """Select a tiny Empty so native G/R/S, gizmos and Shift+S target the point."""
    global _session
    if not 0 <= index < len(manager.items) or slot not in (0, 1):
        return False
    item = manager.items[index]
    hit = item.first if slot == 0 else item.second
    if hit is None:
        return False
    if active(manager) and _session['window_pointer'] == context.window.as_pointer():
        session = _session
        _apply_position(session)
        session['index'], session['slot'] = index, slot
        session['original_hit'] = dict(hit)
        session['baseline'] = tuple(hit['point'])
        session['last_point'] = tuple(hit['point'])
        session['proxy'].location = hit['point']
        manager.active, manager.active_endpoint = index, slot
        return True
    stop()
    window = context.window
    scene, layer = context.scene, context.view_layer
    original_active = layer.objects.active
    selected = tuple(obj for obj in layer.objects if obj.select_get(view_layer=layer))
    original_mode = original_active.mode if original_active else 'OBJECT'
    area = context.area if context.area and context.area.type == 'VIEW_3D' else None
    if area is None or not any(a.as_pointer() == area.as_pointer() for a in window.screen.areas):
        area, _ = _view_region(window, item.area_pointer)
    region = next((r for r in area.regions if r.type == 'WINDOW'), None) if area else None
    if area is None or region is None:
        return False
    if original_mode != 'OBJECT':
        try:
            with context.temp_override(window=window, scene=scene, view_layer=layer,
                                       area=area, region=region):
                bpy.ops.object.mode_set(mode='OBJECT')
        except (ValueError, RuntimeError, ReferenceError):
            return False
    helper = bpy.data.objects.new('Final Dimensions Point', None)
    helper[_TAG] = True
    helper.empty_display_type = 'PLAIN_AXES'
    helper.empty_display_size = .015
    helper.hide_render = True
    helper.show_in_front = True
    scene.collection.objects.link(helper)
    helper.location = hit['point']
    for obj in tuple(layer.objects):
        if obj.select_get(view_layer=layer):
            obj.select_set(False, view_layer=layer)
    helper.select_set(True, view_layer=layer)
    layer.objects.active = helper
    gizmos = []
    space = area.spaces.active
    values = {prop: getattr(space, prop) for prop in _GIZMOS if hasattr(space, prop)}
    for prop in values:
        setattr(space, prop, True)
    gizmos.append((space, values))
    _session = {'manager': manager, 'window_pointer': window.as_pointer(),
                'scene': scene, 'view_layer': layer, 'area_pointer': area.as_pointer(),
                'proxy': helper, 'index': index, 'slot': slot,
                'original_hit': dict(hit), 'baseline': tuple(hit['point']),
                'last_point': tuple(hit['point']), 'selected_objects': selected,
                'active_object': original_active, 'mode': original_mode,
                'gizmos': gizmos, 'world_hit': world_hit}
    manager.active, manager.active_endpoint = index, slot
    return True


def sync(window, manager, world_hit=None):
    """Copy only actual proxy movement to a fixed world anchor; undo a canceled move."""
    session = _session
    if session is None or session['manager'] is not manager:
        return False
    if window.as_pointer() != session['window_pointer']:
        return False
    helper = session['proxy']
    if not _alive(helper):
        stop(restore=True)
        return False
    layer = session['view_layer']
    if layer.objects.active is helper and not helper.select_get(view_layer=layer):
        # Blender's pie click can leave the active object temporarily unselected.
        helper.select_set(True, view_layer=layer)
    if layer.objects.active is not helper:
        stop(restore=False)
        return False
    index, slot = session['index'], session['slot']
    if (manager.active != index or manager.active_endpoint != slot or
            not 0 <= index < len(manager.items)):
        stop(restore=True)
        return False
    item = manager.items[index]
    attr = 'first' if slot == 0 else 'second'
    hit = getattr(item, attr)
    if hit is None:
        stop(restore=True)
        return False
    changed = _apply_position(session)
    if not changed and math.dist(tuple(hit['point']), session['last_point']) > 1e-7:
        # An attached mesh endpoint moved while no native transform was running.
        # Follow it without replacing its anchor with a fixed world point.
        session['original_hit'] = dict(hit)
        session['baseline'] = tuple(hit['point'])
        session['last_point'] = tuple(hit['point'])
        helper.location = hit['point']
    return changed


def cleanup_tagged():
    """Discard only helper IDs owned by this add-on after undo or file load."""
    for obj in tuple(bpy.data.objects):
        if obj.get(_TAG) is True:
            bpy.data.objects.remove(obj, do_unlink=True)
