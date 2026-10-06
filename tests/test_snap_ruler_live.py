"""Native magnet settings and original/final targets through real ruler mouse events."""
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
from final_dimensions import ruler, overlay

report = {'blender': bpy.app.version_string, 'cases': []}
state = {'step': 0, 'labels': []}
original_text = overlay._text


def text(*args):
    original_text(*args)
    state['labels'].append(args[2])


overlay._text = text
bpy.context.preferences.view.show_splash = False
temporary = ROOT / 'artifacts/runtime-temp' / ('snap-ruler-'+bpy.app.version_string.replace(' ', '-'))
temporary.mkdir(parents=True, exist_ok=True)
bpy.context.preferences.filepaths.temporary_directory = str(temporary)
addon.register()
bpy.ops.object.select_all(action='SELECT')
bpy.ops.object.delete(use_global=False)
bpy.ops.mesh.primitive_cube_add(size=.04, location=(-.05, 0, 0))
left = bpy.context.object
left.name = 'Original cage and subdivision'
subdivision = left.modifiers.new('Subdivision', 'SUBSURF')
subdivision.levels = 2
bpy.ops.mesh.primitive_cube_add(size=.04, location=(.05, 0, 0))
right = bpy.context.object
right.name = 'Final beveled mesh'
bevel = right.modifiers.new('Bevel', 'BEVEL')
bevel.width, bevel.segments = .004, 3
bpy.context.view_layer.objects.active = left
bpy.context.view_layer.update()
settings = bpy.context.scene.tool_settings
wm = bpy.context.window_manager
window = bpy.context.window
area = next(a for a in window.screen.areas if a.type == 'VIEW_3D')
region = next(r for r in area.regions if r.type == 'WINDOW')
space = area.spaces.active
space.show_region_ui = False
space.show_region_toolbar = False
space.region_3d.view_distance = .3
space.region_3d.view_location = (0, 0, 0)
space.region_3d.view_rotation = Quaternion((1, 0, 0), math.pi/2)
space.region_3d.view_perspective = 'ORTHO'
space.clip_start = .0001
source_meshes = len(bpy.data.meshes)
source_vertices = {obj.name: tuple(tuple(v.co) for v in obj.data.vertices) for obj in (left, right)}
original_vertex = (-.07, -.02, .02)
original_edge_middle = (-.05, -.02, .02)


def evaluated_vertex():
    evaluated = left.evaluated_get(bpy.context.evaluated_depsgraph_get())
    mesh = evaluated.to_mesh()
    try:
        points = [evaluated.matrix_world @ v.co for v in mesh.vertices]
        # Frontmost generated vertex on the positive-X side; no base cube vertex here.
        return tuple(min(points, key=lambda p: (round(p.y, 7), -p.x)))
    finally:
        evaluated.to_mesh_clear()


final_vertex = evaluated_vertex()
assert math.dist(final_vertex, original_vertex) > .005


def manager():
    value = ruler.collection(window)
    assert value is not None
    return value


def event(kind, value, point=None, offset=(0, 0), ctrl=False):
    coordinates = {}
    if point is not None:
        p = view3d_utils.location_3d_to_region_2d(region, space.region_3d, Vector(point))
        coordinates = {'x': region.x+round(p.x)+offset[0], 'y': region.y+round(p.y)+offset[1]}
    window.event_simulate(type=kind, value=value, ctrl=ctrl, **coordinates)


def mouse(point, offset=(0, 0), ctrl=False):
    event('MOUSEMOVE', 'NOTHING', point, offset, ctrl)


def click(point, button='LEFTMOUSE', offset=(0, 0), ctrl=False):
    event(button, 'PRESS', point, offset, ctrl)
    event(button, 'RELEASE', point, offset, ctrl)


def add():
    with bpy.context.temp_override(window=window, area=area, region=region):
        bpy.ops.view3d.final_dimensions_ruler('INVOKE_DEFAULT')


def close(point, expected, tolerance=1e-7):
    assert math.dist(point, expected) < tolerance, (point, expected)


def candidate(kind, source):
    hit = manager().candidate
    assert hit and hit.get('snap_kind') == kind and hit.get('snap_source') == source, hit
    return hit


def passed(name):
    report['cases'].append(name)


def finish(error=None):
    report['status'] = 'FAIL' if error else 'PASS'
    if error:
        report['error'] = error
    path = ROOT / 'artifacts' / ('snap-ruler-live-'+bpy.app.version_string.replace(' ', '-')+'.json')
    path.write_text(json.dumps(report, indent=2), encoding='utf-8')
    print('SNAP RULER LIVE', report['status'], error or '', flush=True)
    bpy.ops.wm.quit_blender()


def tick():
    try:
        step = state['step']
        if step == 0:
            settings.use_snap = False
            add()
            mouse(left.location)
        elif step == 1:
            hit = manager().candidate
            assert hit and not hit.get('snap_kind'), hit
            assert -.019 < hit['point'][1] < -.015
            passed('magnet_off_preserves_final_surface_picking')
            settings.use_snap = True
            settings.snap_elements = {'VERTEX'}
            wm.final_dimensions_snap_source = 'ORIGINAL'
            mouse(original_vertex, offset=(4, 3))
        elif step == 2:
            close(candidate('VERTEX', 'ORIGINAL')['point'], original_vertex)
            passed('original_vertex_snaps_outside_evaluated_silhouette')
            assert any('Original' in label and 'Vertex' in label for label in state['labels'])
            passed('candidate_source_and_kind_are_drawn')
            click(original_vertex, offset=(4, 3))
        elif step == 3:
            close(manager().selected.first['point'], original_vertex)
            wm.final_dimensions_snap_source = 'FINAL'
            mouse(final_vertex, offset=(3, 2))
        elif step == 4:
            close(candidate('VERTEX', 'FINAL')['point'], final_vertex)
            passed('generated_subdivision_vertex_is_exact_target')
            click(final_vertex, offset=(3, 2))
        elif step == 5:
            assert manager().mode == 'IDLE'
            close(manager().selected.first['point'], original_vertex)
            close(manager().selected.second['point'], final_vertex)
            assert manager().selected.distance > .005
            passed('one_ruler_can_mix_original_and_final_anchors')
            wm.final_dimensions_snap_source = 'ORIGINAL'
            settings.snap_elements = {'EDGE'}
            event('LEFTMOUSE', 'PRESS', original_vertex)
        elif step == 6:
            assert manager().mode == 'DRAG'
            mouse(original_edge_middle)
        elif step == 7:
            candidate('EDGE', 'ORIGINAL')
            close(manager().candidate['point'], original_edge_middle, 2e-4)
            event('LEFTMOUSE', 'RELEASE', original_edge_middle)
        elif step == 8:
            assert manager().mode == 'IDLE'
            close(manager().selected.first['point'], original_edge_middle, 2e-4)
            close(manager().selected.second['point'], final_vertex)
            passed('drag_uses_native_edge_mode_and_keeps_other_end')
            settings.snap_elements = {'FACE'}
            wm.final_dimensions_snap_source = 'FINAL'
            click(final_vertex, button='RIGHTMOUSE')
        elif step == 9:
            assert manager().mode == 'REPLACE'
            mouse(right.location)
        elif step == 10:
            hit = candidate('FACE', 'FINAL')
            assert hit['object_name'] == right.name
            assert abs(hit['point'][1]+.02) < 1e-5
            passed('right_click_replacement_uses_final_face')
            click(right.location)
        elif step == 11:
            assert manager().mode == 'IDLE'
            state['original_end'] = tuple(manager().selected.first['point'])
            subdivision.levels = 3
        elif step == 12:
            close(manager().selected.first['point'], state['original_end'])
            assert manager().selected.second is not None
            passed('original_anchor_survives_modifier_topology_change')
            bevel.segments = 4
        elif step == 13:
            close(manager().selected.first['point'], state['original_end'])
            assert manager().selected.second is None
            passed('final_topology_invalidates_only_final_endpoint')
            with bpy.context.temp_override(window=window, area=area, region=region):
                bpy.ops.view3d.final_dimensions_ruler_select(index=0)
            wm.final_dimensions_snap_source = 'ORIGINAL'
            mouse(left.location)
        elif step == 14:
            hit = candidate('FACE', 'ORIGINAL')
            assert abs(hit['point'][1]+.02) < 1e-5
            passed('original_face_selected_by_native_face_setting')
            # No mouse motion: changing source still updates the candidate.
            wm.final_dimensions_snap_source = 'FINAL'
        elif step == 15:
            hit = candidate('FACE', 'FINAL')
            assert -.019 < hit['point'][1] < -.015
            passed('stationary_cursor_source_change_refreshes_candidate')
            settings.snap_elements = {'VERTEX'}
            # The bevel's central quad has no center vertex; Subdivision does.
            mouse(right.location)
        elif step == 16:
            assert manager().candidate is None, manager().candidate
            passed('vertex_mode_does_not_fall_back_to_face')
            event('LEFT_CTRL', 'PRESS', ctrl=True)
        elif step == 17:
            hit = manager().candidate
            assert hit and not hit.get('snap_kind'), hit
            passed('ctrl_temporarily_inverts_magnet_without_mouse_motion')
            event('LEFT_CTRL', 'RELEASE')
        elif step == 18:
            assert manager().candidate is None, manager().candidate
            passed('releasing_ctrl_restores_native_vertex_mode')
            settings.use_snap = False
        elif step == 19:
            assert manager().candidate and not manager().candidate.get('snap_kind')
            passed('stationary_cursor_magnet_change_refreshes_candidate')
            assert len(bpy.data.meshes) == source_meshes
            assert all(tuple(tuple(v.co) for v in obj.data.vertices) == source_vertices[obj.name]
                       for obj in (left, right))
            passed('snapping_preserves_source_meshes_and_cleans_temporary_meshes')
            bpy.ops.screen.screenshot(filepath=str(ROOT / 'artifacts' / 'snap-ruler-preview.png'))
            addon.unregister()
            assert not hasattr(bpy.types.WindowManager, 'final_dimensions_snap_source')
            assert not ruler._handles and not ruler._sessions and not ruler._states
            passed('unregister_cleans_snap_property_and_rulers')
            finish()
            return None
        state['step'] += 1
        area.tag_redraw()
        return .8
    except Exception:
        finish(traceback.format_exc())
        return None


bpy.app.timers.register(tick, first_interval=2.)
