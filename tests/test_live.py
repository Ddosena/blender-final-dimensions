"""Live UI/timer acceptance in a factory-startup Blender process."""
import json
import sys
import time
import traceback
from pathlib import Path
import bpy
import bmesh

bpy.context.preferences.view.show_splash = False

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import final_dimensions as addon

addon.register()
bpy.ops.object.select_all(action='SELECT')
bpy.ops.object.delete(use_global=False)
bpy.ops.mesh.primitive_cube_add(size=.04)
obj = bpy.context.object
obj.name = 'Cube 40 mm - Subdivision'
sub = obj.modifiers.new('Subdivision', 'SUBSURF')
sub.levels = 3
scene = bpy.context.scene
scene.unit_settings.system = 'METRIC'
scene.unit_settings.length_unit = 'MILLIMETERS'
window = bpy.context.window
area = next(a for a in window.screen.areas if a.type == 'VIEW_3D')
space = area.spaces.active
space.show_region_ui = True
space.region_3d.view_distance = .16
space.region_3d.view_location = (0, 0, 0)
space.clip_start = .0001
for other_area in window.screen.areas:
    if other_area.type == 'PROPERTIES':
        other_area.spaces.active.context = 'MODIFIER'

report = {"blender": bpy.app.version_string, "cases": [], "started": time.time()}
counter = [0]
original_measure = addon.measure_object


def counted(*args, **kwargs):
    counter[0] += 1
    return original_measure(*args, **kwargs)


addon.measure_object = counted
state = {'step': 0, 'count': 0, 'base': None}


def check(condition, message):
    if not condition:
        raise AssertionError(message)


def close(a, b):
    check(all(abs(x-y) < 1e-6 for x, y in zip(a, b)), f'{a} != {b}')


def cached():
    cache = addon._cache.get(window.as_pointer())
    check(cache is not None and cache['result'] is not None, 'Missing live cached result')
    return cache['result']


def save_report():
    report['measurement_calls'] = counter[0]
    (ROOT / 'artifacts' / 'live-ui.json').write_text(json.dumps(report, indent=2), encoding='utf-8')


def tick():
    try:
        stage = state['step']
        if stage == 0:
            region = next(r for r in area.regions if r.type == 'UI')
            region.active_panel_category = 'Final Dimensions'
            area.tag_redraw()
        elif stage == 1:
            result = cached()
            close(result['cage'], (.04, .04, .04))
            state['base'] = result['final']
            check(all(0 < x < .04 for x in result['final']), 'Subdivision should shrink')
            report['cases'].append({'name': 'initial_visible_panel', 'status': 'PASS', 'result': result})
            state['count'] = counter[0]
        elif stage == 2:
            check(counter[0] == state['count'], 'Idle panel recomputed geometry')
            report['cases'].append({'name': 'idle_no_recompute', 'status': 'PASS'})
            with bpy.context.temp_override(window=window, area=area):
                bpy.ops.object.mode_set(mode='EDIT')
                bm = bmesh.from_edit_mesh(obj.data)
                for vertex in bm.verts:
                    vertex.co.x *= 2
                bmesh.update_edit_mesh(obj.data, loop_triangles=False, destructive=False)
        elif stage == 3:
            result = cached()
            close(result['cage'], (.08, .04, .04))
            close(result['final'], (state['base'][0]*2, state['base'][1], state['base'][2]))
            report['cases'].append({'name': 'edit_mode_automatic_refresh', 'status': 'PASS'})
            with bpy.context.temp_override(window=window, area=area):
                bpy.ops.object.mode_set(mode='OBJECT')
            obj.scale.x = 1.5
        elif stage == 4:
            result = cached()
            close(result['cage'], (.12, .04, .04))
            close(result['final'], (state['base'][0]*3, state['base'][1], state['base'][2]))
            report['cases'].append({'name': 'scale_automatic_refresh', 'status': 'PASS'})
            sub.levels = 1
            sub.use_limit_surface = False
        elif stage == 5:
            result = cached()
            close(result['final'], (.12, .04, .04))
            report['cases'].append({'name': 'modifier_parameter_automatic_refresh', 'status': 'PASS'})
            space.show_region_ui = False
        elif stage == 6:
            state['count'] = counter[0]
            obj.scale.y = 2
        elif stage == 7:
            check(counter[0] == state['count'], 'Hidden panel recomputed geometry')
            report['cases'].append({'name': 'hidden_panel_no_recompute', 'status': 'PASS'})
            space.show_region_ui = True
            area.tag_redraw()
        elif stage == 8:
            result = cached()
            close(result['cage'], (.12, .08, .04))
            report['cases'].append({'name': 'reopen_refreshes_stale_cache', 'status': 'PASS'})
            # Restore a simple 40 mm demo for screenshot review.
            obj.scale = (1, 1, 1)
            for vertex in obj.data.vertices:
                vertex.co.x *= .5
            obj.data.update()
            sub.levels = 3
            sub.use_limit_surface = True
        elif stage == 9:
            close(cached()['cage'], (.04, .04, .04))
            state['count'] = counter[0]
        elif stage == 10:
            check(counter[0] == state['count'], 'Stable scene did not stop recomputing')
            with bpy.context.temp_override(window=window, area=area):
                bpy.ops.object.mode_set(mode='EDIT')
        elif stage == 11:
            close(cached()['cage'], (.04, .04, .04))
            bm = bmesh.from_edit_mesh(obj.data)
            for vertex in bm.verts:
                vertex.co.z *= 1.5
            bmesh.update_edit_mesh(obj.data, loop_triangles=False, destructive=False)
        elif stage == 12:
            close(cached()['cage'], (.04, .04, .06))
            close(cached()['final'], (state['base'][0], state['base'][1], state['base'][2]*1.5))
            check(obj.mode == 'EDIT', 'Edit Mode changed unexpectedly')
            report['cases'].append({'name': 'vertices_refresh_without_leaving_edit_mode', 'status': 'PASS'})
            bm = bmesh.from_edit_mesh(obj.data)
            for vertex in bm.verts:
                vertex.co.z /= 1.5
            bmesh.update_edit_mesh(obj.data, loop_triangles=False, destructive=False)
            with bpy.context.temp_override(window=window, area=area):
                bpy.ops.object.mode_set(mode='OBJECT')
        elif stage == 13:
            close(cached()['cage'], (.04, .04, .04))
            bpy.ops.wm.save_as_mainfile(filepath=str(ROOT / 'artifacts' / 'Final-Dimensions-Demo.blend'))
            bpy.ops.screen.screenshot(filepath=str(ROOT / 'artifacts' / 'panel-preview.png'))
            report['status'] = 'PASS'
            save_report()
            print('LIVE TESTS PASS', flush=True)
            return None
        state['step'] += 1
        # Python timer edits need explicit evaluation in an occluded test
        # window; UI property editing normally schedules this in the event loop.
        bpy.context.view_layer.update()
        area.tag_redraw()
        return 1.5
    except Exception:
        report['status'] = 'FAIL'
        report['error'] = traceback.format_exc()
        report['failed_stage'] = state['step']
        save_report()
        bpy.ops.screen.screenshot(filepath=str(ROOT / 'artifacts' / 'panel-failure.png'))
        print(report['error'], flush=True)
        return None


bpy.app.timers.register(tick, first_interval=2.0)
