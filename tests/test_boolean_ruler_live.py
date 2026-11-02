"""Disposable UI process: rulers and hover section through a live Boolean cutter."""
import json
import math
import sys
import time
import traceback
from pathlib import Path

import bpy
from bpy_extras import view3d_utils
from mathutils import Quaternion, Vector

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import final_dimensions as addon
from final_dimensions import hover, ruler

report = {'blender': bpy.app.version_string, 'cases': []}
state = {'step': 0}

bpy.context.preferences.view.show_splash = False
temporary = ROOT / 'artifacts' / 'runtime-temp' / ('boolean-ruler-' + bpy.app.version_string.replace(' ', '-'))
temporary.mkdir(parents=True, exist_ok=True)
bpy.context.preferences.filepaths.temporary_directory = str(temporary)
addon.register()
bpy.ops.object.select_all(action='SELECT')
bpy.ops.object.delete(use_global=False)
bpy.ops.mesh.primitive_cube_add(size=2.0, location=(0, 0, 0))
canvas = bpy.context.object
canvas.name = 'Boolean canvas'
bpy.ops.mesh.primitive_cube_add(size=1.0, location=(0, 0, 1.0))
cutter = bpy.context.object
cutter.name = 'Boolean cutter'
cutter.display_type = 'BOUNDS'
modifier = canvas.modifiers.new('Boolean difference', 'BOOLEAN')
modifier.operation = 'DIFFERENCE'
modifier.object = cutter
modifier.solver = 'EXACT'
bpy.ops.object.select_all(action='DESELECT')
canvas.select_set(True)
bpy.context.view_layer.objects.active = canvas
bpy.context.scene.unit_settings.system = 'METRIC'
bpy.context.scene.unit_settings.length_unit = 'MILLIMETERS'
window = bpy.context.window
area = next(a for a in window.screen.areas if a.type == 'VIEW_3D')
region = next(r for r in area.regions if r.type == 'WINDOW')
space = area.spaces.active
space.show_region_ui = False
space.show_region_toolbar = False
space.region_3d.view_location = (0, 0, 0)
space.region_3d.view_distance = 6
space.region_3d.view_rotation = Quaternion()
space.region_3d.view_perspective = 'ORTHO'
space.clip_start = .01
space.clip_end = 100
bpy.context.window_manager.final_dimensions_section_axis = 'X'
bpy.context.window_manager.final_dimensions_hover = True


def coordinates(point):
    projected = view3d_utils.location_3d_to_region_2d(region, space.region_3d, Vector(point))
    assert projected is not None, point
    return region.x + round(projected.x), region.y + round(projected.y)


def mouse(point):
    x, y = coordinates(point)
    window.event_simulate(type='MOUSEMOVE', value='NOTHING', x=x, y=y)


def click(point):
    x, y = coordinates(point)
    window.event_simulate(type='LEFTMOUSE', value='PRESS', x=x, y=y)
    window.event_simulate(type='LEFTMOUSE', value='RELEASE', x=x, y=y)


def start():
    with bpy.context.temp_override(window=window, area=area, region=region):
        outcome = bpy.ops.view3d.final_dimensions_ruler('INVOKE_DEFAULT')
    assert outcome in ({'RUNNING_MODAL'}, {'FINISHED'}), outcome


def value():
    measured = ruler.get(window)
    assert measured is not None
    return measured


def passed(name):
    report['cases'].append(name)


def finish(error=None):
    report.update(status='FAIL' if error else 'PASS', cases=report['cases'])
    if error:
        report['error'] = error
        report['diagnostics'] = {
            'step': state['step'],
            'hover': hover.get(window, area),
            'ruler': None if ruler.get(window) is None else {
                'mode': ruler.collection(window).mode if ruler.collection(window) else None,
                'first': ruler.get(window).first,
                'second': ruler.get(window).second,
                'candidate': ruler.get(window).candidate,
                'message': ruler.get(window).message,
            },
        }
        bpy.ops.screen.screenshot(filepath=str(ROOT / 'artifacts' / 'boolean-ruler-failure.png'))
    (ROOT / 'artifacts' / ('boolean-ruler-live-' + bpy.app.version_string.replace(' ', '-') + '.json')).write_text(
        json.dumps(report, indent=2), encoding='utf-8')
    print('BOOLEAN RULER LIVE', report['status'], error or '', flush=True)
    bpy.ops.wm.quit_blender()


def wait_for_hover():
    measured = hover.get(window, area)
    if measured is not None and measured['section'] is not None:
        return measured
    deadline = state.setdefault('hover_deadline', time.monotonic() + 8.0)
    assert time.monotonic() < deadline, f'hover section missing: {measured!r}'
    mouse((.25, .25, 1.0))
    return None


def wait_for_candidate():
    candidate = value().candidate
    if candidate is not None:
        return candidate
    deadline = state.setdefault('candidate_deadline', time.monotonic() + 8.0)
    assert time.monotonic() < deadline, f'candidate missing: {value().message!r}'
    mouse((.25, .25, 1.0))
    return None


def tick():
    try:
        step = state['step']
        if step == 0:
            assert hover._operators, 'hover listener missing'
            mouse((.25, .25, 1.0))
        elif step == 1:
            measured = wait_for_hover()
            if measured is None:
                return .35
            assert measured['object'] == canvas.as_pointer(), measured
            assert measured['section']['segments'], measured
            passed('hover_section_ignores_boolean_cutter')
            bpy.context.window_manager.final_dimensions_hover = False
            start()
            mouse((.25, .25, 1.0))
        elif step == 2:
            assert ruler.is_running(window)
            candidate = wait_for_candidate()
            if candidate is None:
                return .35
            assert candidate['object_name'] == canvas.name, candidate
            assert abs(candidate['point'][2] - .5) < 1e-4, candidate['point']
            passed('ruler_candidate_is_boolean_result')
            click((.25, .25, 1.0))
        elif step == 3:
            assert value().first is not None and value().second is None, value().message
            passed('first_click_on_cut_floor')
            mouse((-.8, -.8, 1.0))
        elif step == 4:
            candidate = value().candidate
            assert candidate and candidate['object_name'] == canvas.name, candidate
            assert abs(candidate['point'][2] - 1.0) < 1e-4, candidate['point']
            click((-.8, -.8, 1.0))
        elif step == 5:
            measured = value()
            assert measured.first and measured.second, measured.message
            assert not ruler.is_running(window)
            for hit in (measured.first, measured.second):
                assert hit['object_name'] == canvas.name, hit
            expected = math.dist(measured.first['point'], measured.second['point'])
            assert abs(measured.distance - expected) < 1e-7
            passed('ruler_measures_canvas_not_cutter')
            passed('two_clicks_finish_measurement')
            state['distance'] = measured.distance
            state['first'] = tuple(measured.first['point'])
            cutter.location.x += .3
            bpy.context.view_layer.update()
        elif step == 6:
            measured = value()
            for hit in (measured.first, measured.second):
                assert 'cutter' not in hit['object_name'].lower(), hit
                assert hit['object_name'].startswith(canvas.name), hit
            passed('moving_cutter_does_not_attach_ruler_to_it')
            addon.unregister()
            passed('unregister_cleanup')
            finish()
            return None
        state['step'] += 1
        area.tag_redraw()
        return .65
    except Exception:
        finish(traceback.format_exc())
        return None


bpy.app.timers.register(tick, first_interval=3.0)
