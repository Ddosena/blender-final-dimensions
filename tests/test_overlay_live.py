"""Exercise real viewport drawing, selection changes and directional cache."""
import json
import sys
import traceback
from pathlib import Path
import bpy
import bmesh

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import final_dimensions as addon
from final_dimensions import overlay

bpy.context.preferences.view.show_splash = False
addon.register()
bpy.context.window_manager.final_dimensions_show_overlay = True
bpy.ops.object.select_all(action='SELECT')
bpy.ops.object.delete(use_global=False)
bpy.ops.mesh.primitive_cube_add(size=.04)
obj = bpy.context.object
obj.name = 'Selected edge: 40 mm / Final span'
obj.modifiers.new('Subdivision', 'SUBSURF').levels = 3
scene = bpy.context.scene
scene.unit_settings.system = 'METRIC'
scene.unit_settings.length_unit = 'MILLIMETERS'
window = bpy.context.window
area = next(a for a in window.screen.areas if a.type == 'VIEW_3D')
space = area.spaces.active
space.show_region_ui = False
space.region_3d.view_distance = .16
space.region_3d.view_location = (0, 0, 0)
space.clip_start = .0001
bpy.context.tool_settings.mesh_select_mode = (False, True, False)
bpy.ops.object.mode_set(mode='EDIT')
bm = bmesh.from_edit_mesh(obj.data)
for face in bm.faces:
    face.select_set(False)
for edge in bm.edges:
    edge.select_set(False)
for vertex in bm.verts:
    vertex.select_set(False)
edge = next(e for e in bm.edges if all(v.co.y < 0 and v.co.z > 0 for v in e.verts))
edge.select_set(True)
bm.select_history.clear()
bm.select_history.add(edge)
bmesh.update_edit_mesh(obj.data, loop_triangles=False, destructive=False)
count = [0]
original = overlay.measure_direction


def counted(*args):
    count[0] += 1
    return original(*args)


overlay.measure_direction = counted
draw_count = [0]
original_text = overlay._text


def drawn(*args):
    original_text(*args)
    draw_count[0] += 1


overlay._text = drawn
report = {'blender': bpy.app.version_string, 'cases': []}
state = {'step': 0}


def close(a, b):
    assert a is not None and abs(a-b) < 1e-6, (a, b)


def tick():
    try:
        stage = state['step']
        if stage == 0:
            assert draw_count[0] >= 2, 'The viewport did not successfully draw both labels'
            cached = overlay.get(window)
            assert cached is not None
            close(cached['edge']['length'], .04)
            assert .03 < cached['span'] < .04, cached
            report['cases'].append('closed_sidebar_selected_edge')
            state['count'] = count[0]
            state['span'] = cached['span']
            bpy.ops.screen.screenshot(filepath=str(ROOT/'artifacts'/'edge-overlay-preview.png'))
        elif stage == 1:
            assert count[0] == state['count'], 'idle recomputation'
            report['cases'].append('idle_no_geometry_recompute')
            for vertex in bm.verts:
                vertex.co.x *= 1.5
            bmesh.update_edit_mesh(obj.data, loop_triangles=False, destructive=False)
        elif stage == 2:
            cached = overlay.get(window)
            close(cached['edge']['length'], .06)
            close(cached['span'], state['span']*1.5)
            report['cases'].append('edge_and_final_update_without_leaving_edit_mode')
            edge.select_set(False)
            bm.select_history.clear()
            bmesh.update_edit_mesh(obj.data, loop_triangles=False, destructive=False)
        elif stage == 3:
            assert overlay.get(window) is None
            report['cases'].append('deselection_hides_labels')
            edge.select_set(True)
            bm.select_history.add(edge)
            bmesh.update_edit_mesh(obj.data, loop_triangles=False, destructive=False)
        elif stage == 4:
            assert overlay.get(window) is not None
            report['cases'].append('reselection_restores_labels')
            bpy.context.window_manager.final_dimensions_show_overlay = False
        elif stage == 5:
            assert overlay.get(window) is None
            report['cases'].append('toggle_disables_labels')
            addon.unregister()
            assert overlay._HANDLE is None
            assert not overlay._cache
            report['cases'].append('unregister_removes_draw_handler')
            report['status'] = 'PASS'
            report['measurement_calls'] = count[0]
            report['text_draw_calls'] = draw_count[0]
            (ROOT/'artifacts'/'overlay-live.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
            print('OVERLAY LIVE PASS', flush=True)
            bpy.ops.wm.quit_blender()
            return None
        state['step'] += 1
        bpy.context.view_layer.update()
        area.tag_redraw()
        return 1.0
    except Exception:
        report['status'] = 'FAIL'
        report['error'] = traceback.format_exc()
        (ROOT/'artifacts'/'overlay-live.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
        print(report['error'], flush=True)
        bpy.ops.wm.quit_blender()
        return None


bpy.app.timers.register(tick, first_interval=3.0)
