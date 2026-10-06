"""Disposable UI process: actual mouse clicks on modified surfaces, no OS input."""
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
from final_dimensions import ruler, hover, overlay

report = {'blender': bpy.app.version_string, 'cases': []}
state = {'step': 0, 'draws': 0, 'labels': []}
original_draw, original_text = ruler._draw_pixel, overlay._text


def draw():
    original_draw()
    value = ruler._visible_state()
    if value is not None and value.selected is not None and value.selected.distance is not None:
        state['draws'] += 1


def text(*args):
    original_text(*args)
    state['labels'].append(args[2])


ruler._draw_pixel = draw
overlay._text = text
bpy.context.preferences.view.show_splash = False
temporary = ROOT / 'artifacts' / 'runtime-temp' / ('ruler-'+bpy.app.version_string.replace(' ','-'))
temporary.mkdir(parents=True, exist_ok=True)
bpy.context.preferences.filepaths.temporary_directory = str(temporary)
addon.register()
bpy.ops.object.select_all(action='SELECT')
bpy.ops.object.delete(use_global=False)
bpy.ops.mesh.primitive_cube_add(size=.04, location=(-.05,0,0))
left = bpy.context.object
left.name = 'Subdivided surface'
left.modifiers.new('Subdivision', 'SUBSURF').levels = 2
bpy.ops.mesh.primitive_cube_add(size=.04, location=(.05,.02,0))
right = bpy.context.object
right.name = 'Beveled surface'
bevel = right.modifiers.new('Bevel', 'BEVEL')
bevel.width, bevel.segments = .006, 3
bpy.ops.object.select_all(action='DESELECT')
left.select_set(True)
bpy.context.view_layer.objects.active = left
scene = bpy.context.scene
scene.unit_settings.system = 'METRIC'
scene.unit_settings.length_unit = 'MILLIMETERS'
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
bpy.context.window_manager.final_dimensions_section_axis = 'X'
source_meshes = len(bpy.data.meshes)
source_vertices = {obj.name: tuple(tuple(v.co) for v in obj.data.vertices) for obj in (left,right)}


def coordinates(point):
    p = view3d_utils.location_3d_to_region_2d(region, space.region_3d, Vector(point))
    return region.x+round(p.x), region.y+round(p.y)


def mouse(point):
    x,y = coordinates(point)
    window.event_simulate(type='MOUSEMOVE', value='NOTHING', x=x, y=y)


def click(point):
    x,y = coordinates(point)
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
    report.update(status='FAIL' if error else 'PASS', draws=state['draws'])
    if error:
        report['error'] = error
    path = ROOT / 'artifacts' / ('ruler-live-'+bpy.app.version_string.replace(' ','-')+'.json')
    path.write_text(json.dumps(report,indent=2), encoding='utf-8')
    print('RULER LIVE',report['status'],error or '',flush=True)
    bpy.ops.wm.quit_blender()


def tick():
    try:
        step = state['step']
        if step == 0:
            mouse(left.location)
        elif step == 1:
            assert hover.get(window)['section'] is not None
            start()
            mouse(left.location)
        elif step == 2:
            assert ruler.is_running(window)
            assert hover.get(window) is None
            hit = value().candidate
            assert hit and hit['object_name'] == left.name, hit
            assert -.019 < hit['point'][1] < -.015, hit['point']
            passed('snap_to_subdivision_surface_not_cage')
            passed('ruler_suspends_hover_section')
            click(left.location)
        elif step == 3:
            assert value().first and not value().second
            passed('first_actual_mouse_click')
            mouse((0,0,0))
            click((0,0,0))
        elif step == 4:
            assert value().first and not value().second and value().candidate is None
            passed('empty_click_does_not_create_point')
            mouse(right.location)
        elif step == 5:
            measured = value()
            assert measured.candidate and measured.candidate['object_name'] == right.name
            assert measured.distance > .1
            assert any('d: ' in t and 'mm' in t for t in state['labels'])
            assert state['draws'] > 0
            state['preview_distance'] = measured.distance
            passed('live_distance_preview_to_another_unselected_object')
            passed('actual_gpu_line_and_unit_label')
            click(right.location)
        elif step == 6:
            measured = value()
            assert measured.first and measured.second and not ruler.is_running(window)
            assert abs(measured.distance-state['preview_distance']) < 1e-8
            assert abs(measured.distance-math.dist(measured.first['point'],measured.second['point'])) < 1e-7
            assert left.select_get() and not right.select_get()
            assert len(bpy.data.meshes) == source_meshes
            for obj in (left,right):
                assert tuple(tuple(v.co) for v in obj.data.vertices) == source_vertices[obj.name]
            passed('second_click_finishes_and_preserves_selection_and_mesh')
            bpy.ops.screen.screenshot(filepath=str(ROOT / 'artifacts' / ('surface-ruler-preview-'+bpy.app.version_string.replace(' ','-')+'.png')))
            # Render the registered panel as a native popup. Sidebar category
            # is read-only in these Blender builds, so don't assign its RNA.
            with bpy.context.temp_override(window=window, area=area, region=region):
                bpy.ops.wm.call_panel(name='VIEW3D_PT_final_dimensions', keep_open=True)
            state['second_x'] = measured.second['point'][0]
            state['old_distance'] = measured.distance
        elif step == 7:
            bpy.ops.screen.screenshot(filepath=str(ROOT / 'artifacts' / ('surface-ruler-panel-'+bpy.app.version_string.replace(' ','-')+'.png')))
            window.event_simulate(type='ESC', value='PRESS')
            right.location.x += .01
        elif step == 8:
            measured = value()
            assert abs(measured.second['point'][0]-state['second_x']-.01) < 1e-6
            assert measured.distance > state['old_distance']
            passed('completed_ruler_follows_object_transform')
            left.modifiers[0].levels = 3
        elif step == 9:
            assert value().first is None and value().distance is None
            assert 'topology' in value().message.lower(), value().message
            passed('topology_change_invalidates_anchors')
            start()
            mouse(left.location)
            click(left.location)
        elif step == 10:
            assert value().first
            window.event_simulate(type='BACK_SPACE',value='PRESS')
        elif step == 11:
            assert value().first is None and ruler.is_running(window)
            passed('backspace_resets_first_point')
            window.event_simulate(type='ESC',value='PRESS')
        elif step == 12:
            assert not ruler.is_running(window)
            assert len(ruler.collection(window).items) == 1
            assert ruler.get(window).first is None and ruler.get(window).second
            assert hover.get(window) and hover.get(window)['section']
            passed('escape_cancels_only_unfinished_addition')
            passed('hover_resumes_after_ruler')
            start()
            mouse(left.location)
            click(left.location)
        elif step == 13:
            assert value().first
            bpy.ops.view3d.final_dimensions_ruler_clear()
            assert not ruler.is_running(window)
            assert len(ruler.collection(window).items) == 1
            passed('clear_removes_only_active_ruler')
            start()
            mouse(left.location)
        elif step == 14:
            assert ruler.is_running(window)
            addon._on_load_pre(None)
            addon._on_state_reset(None)
            assert not ruler._sessions and not ruler._states
            passed('file_lifecycle_clears_ruler')
            addon.unregister()
            assert not ruler._handles and not ruler._sessions and not ruler._states
            assert not hover._operators
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
