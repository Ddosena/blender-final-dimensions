"""Own disposable GUI process: real modal mouse events and GPU drawing, no OS input."""
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
from final_dimensions import hover, overlay

report = {'blender': bpy.app.version_string, 'cases': []}
state = {'step': 0, 'builds': 0, 'draws': 0, 'labels': []}
original_build = hover.Geometry.from_object
original_draw = hover._draw_view
original_text = overlay._text


def build(*args):
    state['builds'] += 1
    return original_build(*args)


def draw():
    original_draw()
    if hover._visible_result() and hover._visible_result()['section']:
        state['draws'] += 1


def text(*args):
    original_text(*args)
    state['labels'].append(args[2])


hover.Geometry.from_object = build
hover._draw_view = draw
overlay._text = text
bpy.context.preferences.view.show_splash = False
test_temp = ROOT / 'artifacts' / 'runtime-temp'
test_temp.mkdir(exist_ok=True)
bpy.context.preferences.filepaths.temporary_directory = str(test_temp)
addon.register()
bpy.ops.object.select_all(action='SELECT')
bpy.ops.object.delete(use_global=False)
bpy.ops.mesh.primitive_cylinder_add(vertices=64, radius=.02, depth=.12)
obj = bpy.context.object
obj.name = 'Hover section QA'
taper = obj.modifiers.new('Taper', 'SIMPLE_DEFORM')
taper.deform_method = 'TAPER'
taper.deform_axis = 'Z'
taper.factor = .8
bpy.context.scene.unit_settings.system = 'METRIC'
bpy.context.scene.unit_settings.length_unit = 'MILLIMETERS'
window = bpy.context.window
area = next(a for a in window.screen.areas if a.type == 'VIEW_3D')
region = next(r for r in area.regions if r.type == 'WINDOW')
space = area.spaces.active
space.show_region_ui = False
space.show_region_toolbar = False
space.region_3d.view_distance = .25
space.region_3d.view_location = (0, 0, 0)
space.region_3d.view_rotation = Quaternion((1, 0, 0), math.pi / 2)
space.region_3d.view_perspective = 'ORTHO'
space.clip_start = .0001
bpy.context.window_manager.final_dimensions_section_axis = 'Z'


def mouse(z, x=0):
    point = view3d_utils.location_3d_to_region_2d(region, space.region_3d, Vector((x, 0, z)))
    window.event_simulate(type='MOUSEMOVE', value='NOTHING',
                          x=region.x + round(point.x), y=region.y + round(point.y))


def result():
    value = hover.get(window, area)
    assert value is not None, 'No hover result'
    assert value['section'] is not None, value
    anchor = Vector(value['section']['endpoints'][1])
    assert (anchor - Vector(value['hit'])).length < 1e-5, 'Radius detached from mouse hit'
    projected = view3d_utils.location_3d_to_region_2d(region, space.region_3d, anchor)
    assert (projected - Vector(value['mouse'])).length < 1.0, 'Anchor not under screen cursor'
    return value['section']


def pass_case(name):
    report['cases'].append(name)


def finish(error=None):
    report['status'] = 'FAIL' if error else 'PASS'
    if error:
        report['error'] = error
    report['builds'] = state['builds']
    report['draws'] = state['draws']
    (ROOT / 'artifacts' / ('hover-live-' + bpy.app.version_string.replace(' ', '-') + '.json')).write_text(
        json.dumps(report, indent=2), encoding='utf-8')
    print('HOVER LIVE', report['status'], error or '', flush=True)
    bpy.ops.wm.quit_blender()


def tick():
    try:
        step = state['step']
        if step == 0:
            assert len(hover._operators) == 1
            mouse(-.025)
        elif step == 1:
            section = result()
            assert section['closed']
            assert .02 < section['diameter'] < .04, section['diameter']
            assert section['radius'] == section['diameter'] / 2
            state['lower_diameter'] = section['diameter']
            state['build_count'] = state['builds']
            pass_case('real_mouse_event_starts_section_on_taper')
        elif step == 2:
            assert state['build_count'] == state['builds'], 'idle geometry rebuild'
            pass_case('idle_cache')
            mouse(.025)
        elif step == 3:
            section = result()
            assert section['diameter'] > .04
            assert state['build_count'] == state['builds'], 'mouse motion rebuilt BVH'
            assert state['draws'] > 0
            assert any(t.startswith('f: ') and '½:' in t for t in state['labels'])
            pass_case('motion_changes_diameter_without_geometry_rebuild')
            pass_case('real_gpu_contour_and_small_label')
            state['upper_diameter'] = section['diameter']
            taper.factor = 1.4
        elif step == 4:
            assert result()['diameter'] > state['upper_diameter']
            assert state['builds'] > state['build_count']
            pass_case('stationary_mouse_modifier_change')
            bpy.ops.object.mode_set(mode='EDIT')
            bm = bmesh.from_edit_mesh(obj.data)
            for f in bm.faces:
                f.select_set(False)
            for e in bm.edges:
                e.select_set(False)
            for v in bm.verts:
                v.select_set(False)
            edge = min((e for e in bm.edges if all(v.co.z > 0 for v in e.verts)),
                       key=lambda e: sum(v.co.y for v in e.verts))
            edge.select_set(True)
            bm.select_history.add(edge)
            bmesh.update_edit_mesh(obj.data)
            bpy.context.window_manager.final_dimensions_section_axis = 'EDGE'
            mouse(.025)
        elif step == 5:
            assert result()['diameter'] > .04
            assert any(t.startswith('e: ') for t in state['labels'])
            pass_case('selected_edge_surface_plane_in_edit_mode')
            bpy.ops.screen.screenshot(filepath=str(ROOT / 'artifacts' / 'hover-section-preview.png'))
            bm = bmesh.from_edit_mesh(obj.data)
            for v in bm.verts:
                v.co.x *= 1.4
            bmesh.update_edit_mesh(obj.data)
            state['before_edit'] = result()['diameter']
        elif step == 6:
            assert result()['diameter'] > state['before_edit']
            pass_case('live_edit_mesh')
            # Passive listener must let standard keyboard events pass through.
            listener = hover._operators[window.as_pointer()]
            class Event:
                type = 'ESC'
            assert listener.modal(bpy.context, Event()) == {'PASS_THROUGH'}
            pass_case('editing_keys_pass_through')
            bpy.context.window_manager.final_dimensions_hover = False
        elif step == 7:
            assert not hover._operators and not hover._results
            pass_case('disable_removes_timers_and_cache')
            bpy.context.window_manager.final_dimensions_hover = True
        elif step == 8:
            assert len(hover._operators) == 1
            mouse(0)
        elif step == 9:
            assert result()
            pass_case('re_enable_one_listener')
            space.overlay.show_overlays = False
        elif step == 10:
            assert hover.get(window) is None
            space.overlay.show_overlays = True
        elif step == 11:
            assert result()
            pass_case('standard_overlays_toggle_restores_without_mouse_motion')
            # Cursor leaving the viewport clears the result, with no click.
            window.event_simulate(type='MOUSEMOVE', value='NOTHING', x=0, y=0)
        elif step == 12:
            assert hover.get(window) is None
            pass_case('leaving_viewport_clears_section')
            addon._on_load_pre(None)
            assert not hover._operators
            addon._on_state_reset(None)
        elif step == 13:
            assert len(hover._operators) == 1
            mouse(0)
        elif step == 14:
            assert result()
            pass_case('load_lifecycle_restarts_listener')
            bpy.context.window_manager.final_dimensions_section_axis = 'Z'
            mouse(0)
        elif step == 15:
            section = result()
            state['cursor_diameter'] = section['diameter']
            state['cursor_vector'] = (Vector(section['endpoints'][1]) - Vector(section['endpoints'][0])).normalized()
            state['cursor_builds'] = state['builds']
            mouse(0, x=.02)
        elif step == 16:
            section = result()
            direction = (Vector(section['endpoints'][1]) - Vector(section['endpoints'][0])).normalized()
            assert direction.angle(state['cursor_vector']) > .25
            assert section['diameter'] > state['cursor_diameter'] + .002
            assert state['cursor_builds'] == state['builds']
            pass_case('cursor_rotates_diameter_and_changes_ellipse_value')
            pass_case('diameter_endpoint_projects_to_mouse_within_one_pixel')
            bpy.ops.screen.screenshot(filepath=str(ROOT / 'artifacts' / 'cursor-diameter-preview.png'))
            addon.unregister()
            assert not hover._operators and not hover._handles and not hover._results
            assert not overlay._HANDLE
            pass_case('unregister_cleanup')
            finish()
            return None
        state['step'] += 1
        area.tag_redraw()
        return .65
    except Exception:
        finish(traceback.format_exc())
        return None


bpy.app.timers.register(tick, first_interval=2.0)
