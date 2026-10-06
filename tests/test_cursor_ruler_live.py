"""Exercise ruler cursor placement with simulated viewport events in a real Blender UI."""
import json
import math
import sys
import traceback
from pathlib import Path
from types import SimpleNamespace

import bpy
from bpy_extras import view3d_utils
from mathutils import Quaternion, Vector

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import final_dimensions as addon
from final_dimensions import ruler

report = {'blender': bpy.app.version_string, 'cases': []}
stage = {'number': 0}
bpy.context.preferences.view.show_splash = False
temporary = ROOT / 'artifacts/runtime-temp' / ('cursor-ruler-'+bpy.app.version_string.replace(' ', '-'))
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
area = next(area for area in window.screen.areas if area.type == 'VIEW_3D')
region = next(region for region in area.regions if region.type == 'WINDOW')
space = area.spaces.active
space.show_region_ui = False
space.show_region_toolbar = False
space.region_3d.view_distance = 2.4
space.region_3d.view_location = (0, 0, 0)
space.region_3d.view_rotation = Quaternion((1, 0, 0), math.pi/2)
space.region_3d.view_perspective = 'ORTHO'
surface_left = (-.5, -.2, 0.)
surface_right = (.5, -.2, 0.)
cursor_a = (.5, 0., .09)  # Inside the mesh: no visible-surface hit can yield this.
cursor_b = (-.5, 0., -.09)


def manager():
    result = ruler.collection(window)
    assert result is not None
    return result


def close(actual, expected, tolerance=1e-6):
    assert math.dist(actual, expected) < tolerance, (actual, expected)


def event(kind, value, point=None, **kwargs):
    coordinates = {}
    if point is not None:
        projected = view3d_utils.location_3d_to_region_2d(
            region, space.region_3d, Vector(point))
        assert projected is not None
        coordinates = {'x': region.x+round(projected.x),
                       'y': region.y+round(projected.y)}
    window.event_simulate(type=kind, value=value, **coordinates, **kwargs)


def click(point, button='LEFTMOUSE'):
    event(button, 'PRESS', point)
    event(button, 'RELEASE', point)


def add():
    with bpy.context.temp_override(window=window, area=area, region=region):
        bpy.ops.view3d.final_dimensions_ruler('INVOKE_DEFAULT')


def edit(slot):
    with bpy.context.temp_override(window=window, area=area, region=region):
        assert bpy.ops.view3d.final_dimensions_ruler_endpoint(endpoint=slot) == {'FINISHED'}


def cursor_action():
    # The same operator used by the visible pie entry. Its execute must snapshot now.
    with bpy.context.temp_override(window=window, area=area, region=region):
        assert bpy.ops.view3d.final_dimensions_ruler_to_cursor() == {'FINISHED'}


def snap_pie():
    event('S', 'PRESS', surface_left, shift=True)
    event('S', 'RELEASE', surface_left, shift=True)


def pie_right(kind, value, center, **kwargs):
    projected = view3d_utils.location_3d_to_region_2d(region, space.region_3d, Vector(center))
    window.event_simulate(type=kind, value=value,
                          x=region.x+round(projected.x)+180,
                          y=region.y+round(projected.y), **kwargs)


def passed(name):
    report['cases'].append(name)


def finish(error=None):
    report['status'] = 'FAIL' if error else 'PASS'
    if error:
        report['error'] = error
    path = ROOT / 'artifacts' / ('cursor-ruler-live-'+bpy.app.version_string.replace(' ', '-')+'.json')
    path.parent.mkdir(exist_ok=True)
    path.write_text(json.dumps(report, indent=2), encoding='utf-8')
    print('CURSOR RULER LIVE', report['status'], error or '', flush=True)
    bpy.ops.wm.quit_blender()


def tick():
    try:
        step = stage['number']
        if step == 0:
            add()
            event('MOUSEMOVE', 'NOTHING', surface_left)
        elif step == 1:
            click(surface_left)
        elif step == 2:
            close(manager().selected.first['point'], surface_left, .002)
            bpy.context.scene.cursor.location = cursor_a
            snap_pie()
        elif step == 3:
            assert manager().mode == 'ADD' and manager().slot == 1
            cursor_action()
        elif step == 4:
            assert manager().mode == 'IDLE'
            close(manager().selected.second['point'], cursor_a)
            assert manager().selected.second['object_name'] == '3D Cursor'
            assert manager().selected.second['warnings'] == ()
            passed('shift_s_during_add_second_places_inside_mesh')
            bpy.context.scene.cursor.location = cursor_b
            edit(0)
            snap_pie()
        elif step == 5:
            cursor_action()
        elif step == 6:
            close(manager().selected.first['point'], cursor_b)
            close(manager().selected.second['point'], cursor_a)
            passed('replace_first_preserves_second_and_snapshots_cursor')
            edit(1)
            snap_pie()
        elif step == 7:
            bpy.context.scene.cursor.location = (0., 0., .11)
            cursor_action()
        elif step == 8:
            close(manager().selected.second['point'], (0., 0., .11))
            passed('replace_second_uses_cursor_at_command_execution')
            edit(1)
            snap_pie()
        elif step == 9:
            # Closing the pie does not throw away the replacement backup.
            event('ESC', 'PRESS')
        elif step == 10:
            if manager().mode == 'REPLACE':
                event('ESC', 'PRESS')
        elif step == 11:
            assert manager().mode == 'IDLE'
            close(manager().selected.second['point'], (0., 0., .11))
            passed('cancelled_pie_and_placement_restore_original')
            event('LEFTMOUSE', 'PRESS', cursor_b)
        elif step == 12:
            assert manager().mode == 'DRAG'
            bpy.context.scene.cursor.location = cursor_a
            snap_pie()
        elif step == 13:
            assert manager().mode == 'REPLACE'
            event('LEFTMOUSE', 'RELEASE', cursor_b)
        elif step == 14:
            assert manager().mode == 'REPLACE' and manager().selected.first is None
            close(bpy.context.scene.cursor.location, cursor_a)
            pie_right('MOUSEMOVE', 'NOTHING', cursor_b)
        elif step == 15:
            pie_right('S', 'RELEASE', cursor_b, shift=True)
        elif step == 16:
            pie_right('LEFTMOUSE', 'PRESS', cursor_b)
        elif step == 17:
            pie_right('LEFTMOUSE', 'RELEASE', cursor_b)
        elif step == 18:
            assert manager().mode == 'IDLE'
            close(manager().selected.first['point'], cursor_a)
            close(manager().selected.second['point'], (0., 0., .11))
            passed('held_button_release_then_real_pie_selection_places_cursor_point')
            bpy.context.scene.cursor.location = (3., 4., 5.)
            right.data.vertices[0].co.x += .1
            bpy.data.objects.remove(right, do_unlink=True)
            bpy.context.view_layer.update()
            addon._epoch += 1
            ruler.tick_window(window, addon._epoch)
        elif step == 19:
            close(manager().selected.first['point'], cursor_a)
            close(manager().selected.second['point'], (0., 0., .11))
            passed('world_anchors_survive_cursor_motion_and_mesh_deletion')
            add()
            snap_pie()
        elif step == 20:
            assert manager().mode == 'ADD' and manager().slot == 0
            cursor_action()
        elif step == 21:
            assert manager().mode == 'ADD' and manager().slot == 1
            close(manager().selected.first['point'], (3., 4., 5.))
            passed('shift_s_places_first_point_of_new_ruler')
            cursor_action()
        elif step == 22:
            assert manager().mode == 'IDLE' and len(manager().items) == 2
            close(manager().items[0].first['point'], cursor_a)
            close(manager().items[1].second['point'], (3., 4., 5.))
            passed('other_rulers_are_preserved')
            snap_pie()
        elif step == 23:
            assert manager().mode == 'IDLE'
            assert manager().selected.second is not None
            passed('idle_shift_s_keeps_native_behavior')
            edit(1)
            bpy.context.scene.cursor.location = (2., 3., 4.)
            event('MOUSEMOVE', 'NOTHING', surface_left)
            event('S', 'PRESS', surface_left, shift=True)
        elif step == 24:
            assert manager().mode == 'REPLACE'
            pie_right('MOUSEMOVE', 'NOTHING', surface_left)
        elif step == 25:
            pie_right('S', 'RELEASE', surface_left, shift=True)
        elif step == 26:
            pie_right('LEFTMOUSE', 'PRESS', surface_left)
        elif step == 27:
            pie_right('LEFTMOUSE', 'RELEASE', surface_left)
        elif step == 28:
            assert manager().selected.second is not None, (manager().mode, tuple(bpy.context.scene.cursor.location))
            close(manager().selected.second['point'], (2., 3., 4.))
            passed('pie_mouse_selection_invokes_point_to_cursor')
            with bpy.context.temp_override(window=window, area=area, region=region):
                bpy.ops.view3d.final_dimensions_ruler_select(index=0)
            event('LEFTMOUSE', 'PRESS', cursor_a)
        elif step == 29:
            assert manager().mode == 'DRAG'
            snap_pie()
        elif step == 30:
            assert ruler._sessions[window.as_pointer()]._pending_pie
            event('ESC', 'PRESS', cursor_a)
            event('LEFTMOUSE', 'RELEASE', cursor_a)
        elif step == 31:
            assert manager().mode == 'IDLE'
            assert not ruler._sessions[window.as_pointer()]._pending_pie
            close(manager().selected.first['point'], cursor_a)
            passed('esc_before_drag_button_release_restores_without_ghost_menu')
            addon.unregister()
            addon.register()
            space.show_region_ui = True
            area.tag_redraw()
        elif step == 32:
            ui = next(r for r in area.regions if r.type == 'UI')
            with bpy.context.temp_override(window=window, area=area, region=ui):
                bpy.ops.view3d.final_dimensions_ruler('INVOKE_DEFAULT')
            assert manager().mode == 'ADD'
            projected = view3d_utils.location_3d_to_region_2d(
                region, space.region_3d, Vector(surface_left))
            key = SimpleNamespace(type='S', value='PRESS', shift=True, ctrl=True,
                                  alt=False, oskey=False,
                                  mouse_x=region.x+round(projected.x),
                                  mouse_y=region.y+round(projected.y))
            with bpy.context.temp_override(window=window, area=area, region=region):
                result = ruler._sessions[window.as_pointer()].modal(bpy.context, key)
            assert result == {'PASS_THROUGH'} and manager().slot == 0
            passed('ctrl_shift_s_is_left_for_native_save_as')
            bpy.context.scene.cursor.location = (6., 7., 8.)
            event('MOUSEMOVE', 'NOTHING', surface_left)
            snap_pie()
        elif step == 33:
            assert manager().mode == 'ADD' and manager().slot == 0
            pie_right('MOUSEMOVE', 'NOTHING', surface_left)
        elif step == 34:
            pie_right('S', 'RELEASE', surface_left, shift=True)
        elif step == 35:
            pie_right('LEFTMOUSE', 'PRESS', surface_left)
        elif step == 36:
            pie_right('LEFTMOUSE', 'RELEASE', surface_left)
        elif step == 37:
            close(manager().selected.first['point'], (6., 7., 8.))
            assert manager().mode == 'ADD' and manager().slot == 1
            passed('sidebar_invocation_shift_s_uses_hovered_window_region')
            addon.unregister()
            finish()
            return None
        stage['number'] += 1
        area.tag_redraw()
        return .45
    except Exception:
        finish(traceback.format_exc())
        return None


bpy.app.timers.register(tick, first_interval=2.)
