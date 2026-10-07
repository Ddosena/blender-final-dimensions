"""Persistent endpoint selection and Shift+S after a completed click."""
import json
import math
import sys
import traceback
from pathlib import Path

import bpy
import bmesh
from bpy_extras import view3d_utils
from mathutils import Quaternion, Vector

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import final_dimensions as addon
from final_dimensions import ruler, point_edit

report = {'blender': bpy.app.version_string, 'cases': []}
state = {'step': 0}
bpy.context.preferences.view.show_splash = False
temporary = ROOT / 'artifacts/runtime-temp' / ('active-ruler-'+bpy.app.version_string.replace(' ', '-'))
temporary.mkdir(parents=True, exist_ok=True)
bpy.context.preferences.filepaths.temporary_directory = str(temporary)
addon.register()
bpy.ops.object.select_all(action='SELECT')
bpy.ops.object.delete(use_global=False)
bpy.ops.mesh.primitive_cube_add(size=.4, location=(-.5, 0, 0))
left = bpy.context.object
bpy.ops.mesh.primitive_cube_add(size=.4, location=(.5, 0, 0))
right = bpy.context.object
bpy.context.view_layer.update()
window = bpy.context.window
area = next(a for a in window.screen.areas if a.type == 'VIEW_3D')
region = next(r for r in area.regions if r.type == 'WINDOW')
space = area.spaces.active
space.show_region_ui = False
space.show_region_toolbar = False
space.region_3d.view_distance = 2.4
space.region_3d.view_location = (0, 0, 0)
space.region_3d.view_rotation = Quaternion((1, 0, 0), math.pi/2)
space.region_3d.view_perspective = 'ORTHO'
surface_left = (-.5, -.2, 0.)
surface_right = (.5, -.2, 0.)
inside_right = (.5, 0., .09)
inside_left = (-.5, 0., -.09)
other_point = (0., 0., .12)
menu_center = (0., 0., 0.)


def manager():
    result = ruler.collection(window)
    assert result is not None
    return result


def close(actual, expected, tolerance=1e-6):
    assert math.dist(actual, expected) < tolerance, (actual, expected)


def binding(hit):
    anchor = hit['anchor'] if 'anchor' in hit else hit
    return (anchor.get('snap_source'), anchor.get('mesh_uid'), anchor.get('vertex_id'))


def coordinates(point):
    projected = view3d_utils.location_3d_to_region_2d(region, space.region_3d, Vector(point))
    assert projected is not None
    return {'x': region.x+round(projected.x), 'y': region.y+round(projected.y)}


def event(kind, value, point=menu_center, **kwargs):
    window.event_simulate(type=kind, value=value, **coordinates(point), **kwargs)


def click(point):
    event('LEFTMOUSE', 'PRESS', point)
    event('LEFTMOUSE', 'RELEASE', point)


def pie_top(kind, value):
    xy = coordinates(menu_center)
    window.event_simulate(type=kind, value=value, x=xy['x'], y=xy['y']+115)


def pie_bottom(kind, value):
    xy = coordinates(menu_center)
    window.event_simulate(type=kind, value=value, x=xy['x'], y=xy['y']-115)


def add():
    with bpy.context.temp_override(window=window, area=area, region=region):
        bpy.ops.view3d.final_dimensions_ruler('INVOKE_DEFAULT')


def cursor_action():
    with bpy.context.temp_override(window=window, area=area, region=region):
        assert bpy.ops.view3d.final_dimensions_ruler_to_cursor() == {'FINISHED'}


def passed(name):
    report['cases'].append(name)


def finish(error=None):
    report['status'] = 'FAIL' if error else 'PASS'
    report['coverage_status'] = ('PARTIAL' if report.get('unverified') else 'COMPLETE')
    if error:
        report['error'] = error
    path = ROOT / 'artifacts' / ('active-ruler-live-'+bpy.app.version_string.replace(' ', '-')+'.json')
    path.write_text(json.dumps(report, indent=2), encoding='utf-8')
    print('ACTIVE RULER LIVE', report['status'], error or '', flush=True)
    bpy.ops.wm.quit_blender()


def tick():
    try:
        step = state['step']
        if step == 0:
            add()
            event('MOUSEMOVE', 'NOTHING', surface_left)
        elif step == 1:
            click(surface_left)
        elif step == 2:
            assert manager().slot == 1
            bpy.context.scene.cursor.location = inside_right
            cursor_action()
        elif step == 3:
            assert manager().mode == 'IDLE'
            assert manager().active_endpoint == 1
            close(manager().selected.second['point'], inside_right)
            add()
            event('MOUSEMOVE', 'NOTHING', surface_right)
        elif step == 4:
            click(surface_right)
        elif step == 5:
            bpy.context.scene.cursor.location = other_point
            cursor_action()
        elif step == 6:
            assert len(manager().items) == 2 and manager().active == 1
            click(inside_right)
        elif step == 7:
            assert manager().mode == 'IDLE' and manager().active == 0
            assert manager().active_endpoint == 1
            assert point_edit.active(manager()), (bpy.context.view_layer.objects.active,
                                                  point_edit.session_window())
            close(manager().selected.second['point'], inside_right)
            assert manager().selected.second['anchor']['kind'] == 'WORLD'
            passed('simple_click_selects_world_endpoint_without_surface_jump')
            event('MOUSEMOVE', 'NOTHING', menu_center)
        elif step == 8:
            close(manager().selected.second['point'], inside_right)
            bpy.context.scene.cursor.location = inside_left
            event('S', 'PRESS', menu_center, shift=True)
        elif step == 9:
            assert point_edit.active(manager()), (manager().mode, manager().active_endpoint,
                                                  bpy.context.view_layer.objects.active)
            if manager().mode != 'IDLE' or manager().active_endpoint != 1:
                raise AssertionError((manager().mode, manager().active_endpoint,
                                      manager().active, tuple(bpy.context.scene.cursor.location)))
            pie_top('MOUSEMOVE', 'NOTHING')
        elif step == 10:
            pie_top('S', 'RELEASE')
        elif step == 11:
            pie_top('LEFTMOUSE', 'PRESS')
        elif step == 12:
            state['before_release'] = (manager().active_endpoint, point_edit.session_window(),
                                       bpy.context.view_layer.objects.active.name if bpy.context.view_layer.objects.active else None)
            pie_top('LEFTMOUSE', 'RELEASE')
        elif step == 13:
            if manager().mode != 'IDLE' or manager().active_endpoint != 1:
                raise AssertionError((manager().mode, manager().active_endpoint,
                                      manager().active, tuple(bpy.context.scene.cursor.location),
                                      manager().items[0].second, point_edit.session_window(),
                                      bpy.context.view_layer.objects.active, state.get('before_release')))
            close(manager().items[0].second['point'], inside_left)
            close(manager().items[1].second['point'], other_point)
            assert manager().items[0].first is not None
            passed('idle_selected_endpoint_shift_s_pie_changes_only_that_point')
            bpy.context.scene.cursor.location = (4., 5., 6.)
            addon._epoch += 1
            ruler.tick_window(window, addon._epoch)
        elif step == 14:
            close(manager().items[0].second['point'], inside_left)
            passed('selected_world_point_remains_fixed_after_cursor_moves')
            with bpy.context.temp_override(window=window, area=area, region=region):
                bpy.ops.view3d.final_dimensions_cursor_to_ruler_point()
        elif step == 15:
            close(bpy.context.scene.cursor.location, inside_left)
            close(right.location, (.5, 0., 0.))
            passed('cursor_to_selected_point_does_not_move_mesh')
            event('ESC', 'PRESS', menu_center)
        elif step == 16:
            assert manager().active_endpoint is None and manager().mode == 'IDLE'
            passed('esc_deselects_endpoint')
            click(inside_left)
        elif step == 17:
            assert manager().active_endpoint == 1 and manager().active == 0
            event('LEFTMOUSE', 'PRESS', menu_center)
            event('LEFTMOUSE', 'RELEASE', menu_center)
        elif step == 18:
            assert manager().active_endpoint is None
            passed('empty_viewport_click_deselects_endpoint')
            with bpy.context.temp_override(window=window, area=area, region=region):
                bpy.ops.view3d.final_dimensions_ruler_activate(endpoint=0)
            assert manager().active_endpoint == 0
            state['surface_anchor'] = dict(manager().items[0].first['anchor'])
            close(point_edit.proxy(manager()).location, surface_left, .002)
            event('G', 'PRESS', surface_left)
        elif step == 19:
            event('X', 'PRESS', surface_left)
            event('ONE', 'PRESS', surface_left)
            event('ESC', 'PRESS', surface_left)
        elif step == 20:
            close(manager().items[0].first['point'], surface_left, .002)
            assert manager().items[0].first['anchor'] == state['surface_anchor']
            assert point_edit.active(manager())
            passed('native_g_cancel_preserves_surface_anchor')
            event('ESC', 'PRESS', surface_left)
        elif step == 21:
            assert not point_edit.active(manager())
            assert manager().active_endpoint is None
            passed('next_escape_exits_native_point_selection')
            click(surface_left)
        elif step == 22:
            assert point_edit.active(manager())
            bpy.context.scene.cursor.location = (3., 4., 5.)
            event('S', 'PRESS', menu_center, shift=True)
        elif step == 23:
            pie_bottom('MOUSEMOVE', 'NOTHING')
            pie_bottom('S', 'RELEASE')
        elif step == 24:
            pie_bottom('LEFTMOUSE', 'PRESS')
        elif step == 25:
            pie_bottom('LEFTMOUSE', 'RELEASE')
        elif step == 26:
            close(bpy.context.scene.cursor.location, surface_left, .002)
            assert point_edit.active(manager())
            passed('native_shift_s_cursor_to_selected_first_endpoint')
            bpy.context.scene.cursor.location = inside_left
            event('S', 'PRESS', menu_center, shift=True)
        elif step == 27:
            pie_top('MOUSEMOVE', 'NOTHING')
            pie_top('S', 'RELEASE')
        elif step == 28:
            pie_top('LEFTMOUSE', 'PRESS')
        elif step == 29:
            pie_top('LEFTMOUSE', 'RELEASE')
        elif step == 30:
            close(manager().items[0].first['point'], inside_left)
            assert manager().items[0].first['anchor']['kind'] == 'WORLD'
            close(manager().items[0].second['point'], inside_left)
            passed('native_shift_s_selection_to_cursor_first_endpoint')
            event('G', 'PRESS', inside_left)
        elif step == 31:
            event('X', 'PRESS', inside_left)
        elif step == 32:
            event('ONE', 'PRESS', inside_left, unicode='1')
        elif step == 33:
            event('RET', 'PRESS', inside_left)
        elif step == 34:
            close(manager().items[0].first['point'], (inside_left[0]+1., inside_left[1], inside_left[2]), .002)
            assert point_edit.active(manager())
            passed('native_g_x_numeric_confirm')
            event('ESC', 'PRESS', menu_center)
        elif step == 35:
            assert not point_edit.active(manager())
            assert manager().active_endpoint is None
            close(manager().items[0].first['point'], (inside_left[0]+1., inside_left[1], inside_left[2]), .002)
            passed('native_transform_confirm_then_escape_exits_selection')
            with bpy.context.temp_override(window=window, area=area, region=region):
                bpy.ops.view3d.final_dimensions_ruler_activate(endpoint=0)
            bpy.context.scene.tool_settings.transform_pivot_point = 'CURSOR'
            bpy.context.scene.cursor.location = (0., 0., 0.)
            event('R', 'PRESS', menu_center)
        elif step == 36:
            event('Z', 'PRESS', menu_center)
        elif step == 37:
            event('NINE', 'PRESS', menu_center, unicode='9')
        elif step == 38:
            event('ZERO', 'PRESS', menu_center, unicode='0')
        elif step == 39:
            event('RET', 'PRESS', menu_center)
        elif step == 40:
            close(manager().items[0].first['point'], (0., .5, inside_left[2]), .003)
            passed('native_r_z_numeric_cursor_pivot')
            event('S', 'PRESS', menu_center)
        elif step == 41:
            event('TWO', 'PRESS', menu_center, unicode='2')
        elif step == 42:
            event('RET', 'PRESS', menu_center)
        elif step == 43:
            close(manager().items[0].first['point'], (0., 1., 2*inside_left[2]), .003)
            passed('native_s_numeric_cursor_pivot')
            event('ESC', 'PRESS', menu_center)
        elif step == 44:
            assert not point_edit.active(manager())
            with bpy.context.temp_override(window=window, area=area, region=region):
                bpy.ops.view3d.final_dimensions_ruler_activate(endpoint=0)
                bpy.ops.wm.tool_set_by_id(name='builtin.move')
            bpy.context.scene.tool_settings.transform_pivot_point = 'MEDIAN_POINT'
        elif step == 45:
            with bpy.context.temp_override(window=window, area=area, region=region):
                bpy.ops.screen.screenshot(filepath=str(temporary / 'point-gizmo.png'))
        elif step == 46:
            xy = coordinates((0., 1., 2*inside_left[2]))
            state['gizmo_xy'] = xy
            state['before_gizmo'] = tuple(manager().items[0].first['point'])
            window.event_simulate(type='MOUSEMOVE', value='NOTHING', x=xy['x']+86, y=xy['y'])
        elif step == 47:
            xy = state['gizmo_xy']
            window.event_simulate(type='LEFTMOUSE', value='PRESS', x=xy['x']+86, y=xy['y'])
        elif step == 48:
            xy = state['gizmo_xy']
            window.event_simulate(type='MOUSEMOVE', value='NOTHING', x=xy['x']+180, y=xy['y'])
        elif step == 49:
            xy = state['gizmo_xy']
            window.event_simulate(type='LEFTMOUSE', value='RELEASE', x=xy['x']+180, y=xy['y'])
        elif step == 50:
            moved = tuple(manager().items[0].first['point'])
            if moved[0] > state['before_gizmo'][0]+.05:
                assert manager().items[0].first['anchor']['kind'] == 'WORLD'
                passed('native_translate_gizmo_mouse_drag')
            else:
                report.setdefault('unverified', []).append('native_translate_gizmo_mouse_drag')
            event('ESC', 'PRESS', menu_center)
        elif step == 51:
            assert not point_edit.active(manager())
            with bpy.context.temp_override(window=window, area=area, region=region):
                bpy.ops.view3d.final_dimensions_ruler_activate(endpoint=0)
            bpy.data.objects.remove(left, do_unlink=True)
            bpy.context.view_layer.update()
            addon._epoch += 1
            ruler.tick_window(window, addon._epoch)
        elif step == 52:
            assert manager().active_endpoint == 0
            assert manager().items[0].first is not None
            passed('world_anchor_survives_source_mesh_deletion')
            with bpy.context.temp_override(window=window, area=area, region=region):
                bpy.ops.view3d.final_dimensions_ruler_activate(endpoint=1)
            bpy.data.objects.remove(right, do_unlink=True)
            bpy.context.view_layer.update()
            addon._epoch += 1
            ruler.tick_window(window, addon._epoch)
        elif step == 53:
            assert manager().active_endpoint == 1
            close(manager().items[0].second['point'], inside_left)
            passed('world_anchor_survives_source_mesh_deletion_second')
            point_edit.stop()
            ruler.stop_all()
            bpy.ops.mesh.primitive_cube_add(size=.4, location=(0., 0., 0.))
            source = state['source'] = bpy.context.object
            source.name = 'Bound Vertex Source'
            bpy.ops.object.mode_set(mode='EDIT')
            mesh = bmesh.from_edit_mesh(source.data)
            for vertex in mesh.verts:
                vertex.select_set(False)
            vertex = min(mesh.verts, key=lambda v: math.dist(v.co, (-.2, -.2, -.2)))
            vertex.select_set(True)
            mesh.select_flush_mode()
            bmesh.update_edit_mesh(source.data)
            state['vertex_point'] = tuple(vertex.co)
            settings = bpy.context.scene.tool_settings
            settings.use_snap = True
            settings.snap_elements = {'VERTEX'}
            bpy.context.window_manager.final_dimensions_snap_source = 'ORIGINAL'
            add()
            event('MOUSEMOVE', 'NOTHING', state['vertex_point'])
        elif step == 54:
            click(state['vertex_point'])
        elif step == 55:
            hit = manager().selected.first
            assert hit is not None and hit['anchor'].get('kind') != 'WORLD', hit
            assert hit['anchor']['snap_source'] == 'ORIGINAL'
            assert hit['anchor']['snap_kind'] == 'VERTEX'
            assert hit['anchor'].get('vertex_id') is not None
            state['vertex_anchor'] = dict(hit['anchor'])
            bpy.context.scene.cursor.location = (0., 0., .05)
            cursor_action()
        elif step == 56:
            assert manager().mode == 'IDLE'
            click(state['vertex_point'])
        elif step == 57:
            assert point_edit.active(manager())
            assert state['source'].mode == 'OBJECT'
            assert binding(manager().items[0].first) == binding(state['vertex_anchor'])
            passed('edit_mode_vertex_click_preserves_binding')
            event('ESC', 'PRESS', state['vertex_point'])
        elif step == 58:
            assert state['source'].mode == 'EDIT'
            assert manager().active_endpoint is None
            vertex = next(v for v in bmesh.from_edit_mesh(state['source'].data).verts if v.select)
            close(vertex.co, state['vertex_point'])
            vertex.co.x += .1
            bmesh.update_edit_mesh(state['source'].data)
            addon._epoch += 1
            ruler.tick_window(window, addon._epoch)
        elif step == 59:
            moved = (state['vertex_point'][0]+.1, *state['vertex_point'][1:])
            close(manager().items[0].first['point'], moved, .002)
            assert binding(manager().items[0].first) == binding(state['vertex_anchor'])
            passed('edit_mode_vertex_moves_after_click_escape')
            state['vertex_point'] = moved
            click(moved)
        elif step == 60:
            assert point_edit.active(manager())
            event('G', 'PRESS', state['vertex_point'])
        elif step == 61:
            event('X', 'PRESS', state['vertex_point'])
            event('ONE', 'PRESS', state['vertex_point'], unicode='1')
            event('ESC', 'PRESS', state['vertex_point'])
        elif step == 62:
            close(manager().items[0].first['point'], state['vertex_point'], .002)
            assert binding(manager().items[0].first) == binding(state['vertex_anchor'])
            passed('native_g_cancel_keeps_original_vertex_binding')
            event('ESC', 'PRESS', state['vertex_point'])
        elif step == 63:
            assert state['source'].mode == 'EDIT'
            vertex = next(v for v in bmesh.from_edit_mesh(state['source'].data).verts if v.select)
            vertex.co.x += .1
            bmesh.update_edit_mesh(state['source'].data)
            addon._epoch += 1
            ruler.tick_window(window, addon._epoch)
        elif step == 64:
            moved = (state['vertex_point'][0]+.1, *state['vertex_point'][1:])
            close(manager().items[0].first['point'], moved, .002)
            assert binding(manager().items[0].first) == binding(state['vertex_anchor'])
            passed('original_vertex_binding_survives_g_cancel_and_edit_restore')
            state['vertex_point'] = moved
            with bpy.context.temp_override(window=window, area=area, region=region):
                bpy.ops.view3d.final_dimensions_ruler_activate(endpoint=0)
            assert point_edit.active(manager())
        elif step == 65:
            event('LEFTMOUSE', 'PRESS', state['vertex_point'])
        elif step == 66:
            assert manager().mode == 'DRAG' and not point_edit.active(manager())
            assert state['source'].mode == 'EDIT'
            event('MOUSEMOVE', 'NOTHING', (.2, -.2, -.2))
        elif step == 67:
            event('LEFTMOUSE', 'RELEASE', (.2, -.2, -.2))
        elif step == 68:
            close(manager().items[0].first['point'], (.2, -.2, -.2), .002)
            assert manager().mode == 'IDLE'
            passed('selected_marker_drag_uses_surface_snap')
            with bpy.context.temp_override(window=window, area=area, region=region):
                bpy.ops.view3d.final_dimensions_ruler_activate(endpoint=0)
        elif step == 69:
            assert point_edit.active(manager())
            event('RIGHTMOUSE', 'PRESS', (.2, -.2, -.2))
        elif step == 70:
            assert manager().mode == 'REPLACE' and not point_edit.active(manager())
            event('MOUSEMOVE', 'NOTHING', (-.2, -.2, .2))
        elif step == 71:
            click((-.2, -.2, .2))
        elif step == 72:
            close(manager().items[0].first['point'], (-.2, -.2, .2), .002)
            assert manager().mode == 'IDLE'
            passed('active_proxy_rmb_marker_starts_replace')
            with bpy.context.temp_override(window=window, area=area, region=region):
                bpy.ops.view3d.final_dimensions_ruler_activate(endpoint=0)
            assert point_edit.active(manager())
            bpy.ops.wm.save_as_mainfile(filepath=str(temporary / 'native-clean.blend'))
        elif step == 73:
            assert not point_edit.active(manager())
            assert state['source'].mode == 'EDIT'
            assert not any(obj.get('_final_dimensions_point_proxy') for obj in bpy.data.objects)
            passed('save_pre_removes_proxy_and_restores_edit_mode')
            with bpy.context.temp_override(window=window, area=area, region=region):
                bpy.ops.view3d.final_dimensions_ruler_activate(endpoint=0)
            assert point_edit.active(manager())
            assert bpy.ops.ed.undo() == {'FINISHED'}
        elif step == 74:
            assert not any(obj.get('_final_dimensions_point_proxy') for obj in bpy.data.objects)
            passed('undo_pre_removes_proxy')
            assert bpy.ops.ed.redo() == {'FINISHED'}
        elif step == 75:
            assert not any(obj.get('_final_dimensions_point_proxy') for obj in bpy.data.objects)
            passed('redo_pre_keeps_proxy_out_of_history')
            addon.unregister()
            finish()
            return None
        state['step'] += 1
        area.tag_redraw()
        return .45
    except Exception:
        finish(traceback.format_exc())
        return None


bpy.app.timers.register(tick, first_interval=2.)
