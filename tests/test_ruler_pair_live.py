"""Both endpoints of one ruler selected together and moved like an edge."""
import json
import math
import sys
import traceback
from pathlib import Path

import bpy
from bpy_extras import view3d_utils
from mathutils import Quaternion, Vector

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import final_dimensions as addon
from final_dimensions import ruler, point_edit

report = {'blender': bpy.app.version_string, 'cases': []}
state = {'step': 0}
bpy.context.preferences.view.show_splash = False
temporary = ROOT / 'artifacts/runtime-temp' / ('ruler-pair-'+bpy.app.version_string.replace(' ', '-'))
temporary.mkdir(parents=True, exist_ok=True)
bpy.context.preferences.filepaths.temporary_directory = str(temporary)
addon.register()
bpy.ops.object.select_all(action='SELECT')
bpy.ops.object.delete(use_global=False)
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
bpy.context.scene.tool_settings.transform_pivot_point = 'MEDIAN_POINT'
with bpy.context.temp_override(window=window, area=area, region=region):
    collection = ruler.RulerCollection(bpy.context)
    item = ruler.RulerState(bpy.context)
item.first, item.second = ruler._world_hit((-.5, 0, 0)), ruler._world_hit((.5, 0, 0))
collection.items.append(item)
collection.active = 0
collection.recalculate()
ruler._states[window.as_pointer()] = collection
with bpy.context.temp_override(window=window, area=area, region=region):
    bpy.ops.view3d.final_dimensions_ruler('INVOKE_DEFAULT', listen_only=True)


def close(actual, expected, tolerance=2e-3):
    assert math.dist(actual, expected) < tolerance, (actual, expected)


def coordinates(point):
    projected = view3d_utils.location_3d_to_region_2d(region, space.region_3d, Vector(point))
    assert projected is not None
    return {'x': region.x+round(projected.x), 'y': region.y+round(projected.y)}


def event(kind, value, point=(0, 0, 0), **kwargs):
    window.event_simulate(type=kind, value=value, **coordinates(point), **kwargs)


def end(slot):
    return tuple((item.first, item.second)[slot]['point'])


def click(slot, **kwargs):
    event('LEFTMOUSE', 'PRESS', end(slot), **kwargs)
    event('LEFTMOUSE', 'RELEASE', end(slot), **kwargs)


def helpers():
    return [obj for obj in bpy.data.objects if obj.get('_final_dimensions_point_proxy')]


def passed(name):
    report['cases'].append(name)


def finish(error=None):
    report['status'] = 'FAIL' if error else 'PASS'
    if error:
        report['error'] = error
    path = ROOT / 'artifacts' / ('ruler-pair-live-'+bpy.app.version_string.replace(' ', '-')+'.json')
    path.write_text(json.dumps(report, indent=2), encoding='utf-8')
    print('RULER PAIR LIVE', report['status'], error or '', flush=True)
    bpy.ops.wm.quit_blender()


def tick():
    try:
        step = state['step']
        if step == 0:
            event('MOUSEMOVE', 'NOTHING', end(0))
        elif step == 1:
            click(0)
        elif step == 2:
            assert point_edit.selected_slots(collection) == (0,), point_edit.selected_slots(collection)
            event('MOUSEMOVE', 'NOTHING', end(1))
        elif step == 3:
            click(1, shift=True)
        elif step == 4:
            assert point_edit.selected_slots(collection) == (0, 1), point_edit.selected_slots(collection)
            assert collection.active_endpoint == 1
            layer = bpy.context.view_layer
            assert len(helpers()) == 2 and all(obj.select_get(view_layer=layer) for obj in helpers())
            assert layer.objects.active is point_edit.proxy(collection)
            close(point_edit.proxy(collection).location, (.5, 0, 0))
            passed('shift_click_selects_both_points')
            with bpy.context.temp_override(window=window, area=area, region=region):
                bpy.ops.wm.tool_set_by_id(name='builtin.move')
                bpy.ops.screen.screenshot(filepath=str(
                    ROOT / 'artifacts' / ('ruler-pair-'+bpy.app.version_string.replace(' ', '-')+'.png')))
            event('G', 'PRESS')
        elif step == 5:
            event('X', 'PRESS')
        elif step == 6:
            event('ONE', 'PRESS', unicode='1')
        elif step == 7:
            event('RET', 'PRESS')
        elif step == 8:
            close(end(0), (.5, 0, 0))
            close(end(1), (1.5, 0, 0))
            assert abs(item.distance-1.) < 2e-3
            assert point_edit.selected_slots(collection) == (0, 1)
            passed('g_x_moves_whole_ruler')
            event('R', 'PRESS')
        elif step == 9:
            event('Y', 'PRESS')
        elif step == 10:
            event('NINE', 'PRESS', unicode='9')
        elif step == 11:
            event('ZERO', 'PRESS', unicode='0')
        elif step == 12:
            event('RET', 'PRESS')
        elif step == 13:
            # Median pivot: the ruler turns about its middle (1, 0, 0).
            assert abs(end(0)[0]-1.) < 2e-3 and abs(end(1)[0]-1.) < 2e-3, (end(0), end(1))
            assert abs(abs(end(0)[2])-.5) < 2e-3 and abs(end(0)[2]+end(1)[2]) < 2e-3
            assert abs(item.distance-1.) < 2e-3
            passed('r_rotates_ruler_about_its_middle')
            event('S', 'PRESS')
        elif step == 14:
            event('TWO', 'PRESS', unicode='2')
        elif step == 15:
            event('RET', 'PRESS')
        elif step == 16:
            assert abs(item.distance-2.) < 4e-3, item.distance
            close(tuple((a+b)/2 for a, b in zip(end(0), end(1))), (1, 0, 0))
            assert item.first['anchor']['kind'] == item.second['anchor']['kind'] == 'WORLD'
            passed('s_scales_ruler_length_about_middle')
            event('ESC', 'PRESS')
        elif step == 17:
            assert not point_edit.active(collection) and collection.active_endpoint is None
            assert not helpers()
            assert abs(item.distance-2.) < 4e-3
            passed('escape_releases_both_points_and_keeps_result')
            with bpy.context.temp_override(window=window, area=area, region=region):
                assert bpy.ops.view3d.final_dimensions_ruler_activate(endpoint=0, both=True) == {'FINISHED'}
        elif step == 18:
            assert point_edit.selected_slots(collection) == (0, 1)
            assert collection.active_endpoint == 0
            passed('panel_both_button_selects_pair')
            event('MOUSEMOVE', 'NOTHING', end(0))
        elif step == 19:
            click(0, shift=True)
        elif step == 20:
            assert point_edit.selected_slots(collection) == (1,), point_edit.selected_slots(collection)
            assert collection.active_endpoint == 1 and len(helpers()) == 1
            passed('shift_click_active_point_leaves_the_other')
            event('MOUSEMOVE', 'NOTHING', end(1))
        elif step == 21:
            click(1, shift=True)
        elif step == 22:
            assert not point_edit.active(collection) and collection.active_endpoint is None
            assert not helpers()
            passed('shift_click_last_point_deselects')
            addon.unregister()
            finish()
            return None
        state['step'] += 1
        area.tag_redraw()
        return .5
    except Exception:
        finish(traceback.format_exc())
        return None


bpy.app.timers.register(tick, first_interval=2.)
