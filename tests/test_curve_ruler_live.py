"""Disposable UI process: actual mouse clicks on a curve without bevel, no OS input."""
import json
import math
import sys
import traceback
from pathlib import Path

import bpy
from bpy_extras import view3d_utils
from mathutils import Quaternion, Vector
from mathutils.geometry import intersect_point_line

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import final_dimensions as addon
from final_dimensions import ruler, overlay

report = {'blender': bpy.app.version_string, 'cases': []}
state = {'step': 0, 'labels': []}
original_text = overlay._text


def text(*args):
    original_text(*args)
    state['labels'].append(args[2])


overlay._text = text
bpy.context.preferences.view.show_splash = False
temporary = ROOT / 'artifacts' / 'runtime-temp' / ('curve-ruler-'+bpy.app.version_string.replace(' ','-'))
temporary.mkdir(parents=True, exist_ok=True)
bpy.context.preferences.filepaths.temporary_directory = str(temporary)
addon.register()
bpy.ops.object.select_all(action='SELECT')
bpy.ops.object.delete(use_global=False)
POINTS = [(-.04, 0, -.01), (0, 0, .02), (.04, 0, -.01)]
data = bpy.data.curves.new('Wire curve', 'CURVE')
data.dimensions = '3D'
spline = data.splines.new('POLY')
spline.points.add(len(POINTS)-1)
for point, co in zip(spline.points, POINTS):
    point.co = (*co, 1.)
curve = bpy.data.objects.new('Wire curve', data)
bpy.context.collection.objects.link(curve)
curve.select_set(True)
bpy.context.view_layer.objects.active = curve
scene = bpy.context.scene
scene.unit_settings.system = 'METRIC'
scene.unit_settings.length_unit = 'MILLIMETERS'
tools = scene.tool_settings
tools.use_snap = False
window = bpy.context.window
area = next(a for a in window.screen.areas if a.type == 'VIEW_3D')
region = next(r for r in area.regions if r.type == 'WINDOW')
space = area.spaces.active
space.show_region_ui = False
space.show_region_toolbar = False
space.region_3d.view_location = (0,0,0)
space.region_3d.view_distance = .3
space.region_3d.view_rotation = Quaternion((1,0,0), math.pi/2)
space.region_3d.view_perspective = 'ORTHO'
space.clip_start = .0001


def coordinates(point, offset=(0, 0)):
    p = view3d_utils.location_3d_to_region_2d(region, space.region_3d, Vector(point))
    return region.x+round(p.x)+offset[0], region.y+round(p.y)+offset[1]


def mouse(point, offset=(0, 0)):
    x,y = coordinates(point, offset)
    window.event_simulate(type='MOUSEMOVE', value='NOTHING', x=x, y=y)


def click(point, offset=(0, 0)):
    x,y = coordinates(point, offset)
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


def on_segment(point, a, b):
    nearest, factor = intersect_point_line(Vector(point), Vector(a), Vector(b))
    return 0 <= factor <= 1 and (nearest-Vector(point)).length < 1e-6


def midpoint(a, b):
    return tuple((x+y)/2 for x, y in zip(a, b))


def passed(name):
    report['cases'].append(name)


def finish(error=None):
    report.update(status='FAIL' if error else 'PASS')
    if error:
        report['error'] = error
    path = ROOT / 'artifacts' / ('curve-ruler-live-'+bpy.app.version_string.replace(' ','-')+'.json')
    path.write_text(json.dumps(report,indent=2), encoding='utf-8')
    print('CURVE RULER LIVE',report['status'],error or '',flush=True)
    bpy.ops.wm.quit_blender()


LEFT = midpoint(POINTS[0], POINTS[1])
RIGHT = midpoint(POINTS[1], POINTS[2])


def tick():
    try:
        step = state['step']
        if step == 0:
            start()
            mouse(LEFT, (0, 3))
        elif step == 1:
            hit = value().candidate
            assert hit and hit['object_name'] == curve.name, hit
            assert hit['snap_kind'] == 'WIRE' and on_segment(hit['point'], POINTS[0], POINTS[1]), hit
            assert math.dist(hit['point'], LEFT) < 5e-4, hit['point']
            assert any(label == 'Final · Wire' for label in state['labels']), state['labels'][-5:]
            passed('free_pick_finds_curve_line_without_magnet')
            click(LEFT, (0, 3))
        elif step == 2:
            assert value().first and not value().second
            mouse(RIGHT, (2, -3))
        elif step == 3:
            measured = value()
            assert measured.candidate and on_segment(measured.candidate['point'], POINTS[1], POINTS[2])
            click(RIGHT, (2, -3))
        elif step == 4:
            measured = value()
            assert measured.first and measured.second and not ruler.is_running(window)
            assert abs(measured.distance-math.dist(measured.first['point'], measured.second['point'])) < 1e-7
            assert abs(measured.distance-math.dist(LEFT, RIGHT)) < 1e-3, measured.distance
            assert not measured.message, measured.message
            passed('two_clicks_measure_between_curve_points')
            state['first'] = measured.first['point']
            # Native editing of the curve moves its line; the ruler follows.
            curve.data.splines[0].points[0].co = (-.06, 0, -.01, 1)
            curve.data.update_tag()
        elif step == 5:
            measured = value()
            assert measured.first['anchor'].get('kind') != 'WORLD', measured.message
            moved = midpoint((-.06, 0, -.01), POINTS[1])
            assert math.dist(measured.first['point'], state['first']) > .005
            assert on_segment(measured.first['point'], (-.06, 0, -.01), POINTS[1])
            assert math.dist(measured.first['point'], moved) < 1e-3, (measured.first['point'], moved)
            passed('ruler_follows_curve_edit')
            tools.use_snap = True
            tools.snap_elements = {'VERTEX'}
            bpy.context.window_manager.final_dimensions_snap_source = 'ORIGINAL'
            start()
            mouse(POINTS[1], (4, 4))
        elif step == 6:
            hit = value().candidate
            assert hit and hit['snap_source'] == 'ORIGINAL' and hit['snap_kind'] == 'VERTEX', hit
            assert math.dist(hit['point'], POINTS[1]) < 1e-7, hit['point']
            passed('magnet_snaps_to_curve_control_point')
            click(POINTS[1], (4, 4))
            mouse(POINTS[2], (-3, 2))
        elif step == 7:
            click(POINTS[2], (-3, 2))
        elif step == 8:
            measured = value()
            assert measured.first and measured.second and not ruler.is_running(window)
            assert abs(measured.distance-math.dist(POINTS[1], POINTS[2])) < 1e-7, measured.distance
            assert len(ruler.collection(window).items) == 2
            passed('control_point_to_control_point_distance')
            # The last placed point is active; only its ring marks it, no caption
            # covers the distance label.
            assert ruler.collection(window).active_endpoint is not None
            assert not any(label.startswith('Active point') for label in state['labels'])
            passed('active_point_has_no_viewport_caption')
            with bpy.context.temp_override(window=window, area=area, region=region):
                bpy.ops.wm.call_panel(name='VIEW3D_PT_final_dimensions', keep_open=True)
        elif step == 9:
            bpy.ops.screen.screenshot(filepath=str(ROOT / 'artifacts' / ('curve-ruler-panel-'+bpy.app.version_string.replace(' ','-')+'.png')))
            window.event_simulate(type='ESC', value='PRESS')
        elif step == 10:
            addon.unregister()
            assert not ruler._handles and not ruler._sessions and not ruler._states
            passed('unregister_cleanup')
            finish()
            return None
        state['step'] += 1
        area.tag_redraw()
        return .7
    except Exception:
        finish(traceback.format_exc())
        return None


bpy.app.timers.register(tick, first_interval=2.)
