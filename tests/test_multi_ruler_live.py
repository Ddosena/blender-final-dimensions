"""Multiple rulers and endpoint editing using actual Blender mouse events."""
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
test = {'step': 0, 'labels': []}
original_text = overlay._text

def text(*args):
    original_text(*args)
    test['labels'].append(args[2])

overlay._text = text
bpy.context.preferences.view.show_splash = False
temporary = ROOT / 'artifacts' / 'runtime-temp' / ('multi-ruler-'+bpy.app.version_string.replace(' ', '-'))
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
bpy.context.scene.unit_settings.system = 'METRIC'
bpy.context.scene.unit_settings.length_unit = 'MILLIMETERS'
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

def event(kind, value, point=None, offset=(0,0)):
    kwargs = {} if point is None else dict(zip(('x','y'), (c+d for c,d in zip(coordinates(point),offset))))
    window.event_simulate(type=kind, value=value, **kwargs)

def mouse(point):
    event('MOUSEMOVE','NOTHING',point)

def click(point, button='LEFTMOUSE'):
    event(button,'PRESS',point)
    event(button,'RELEASE',point)

def operator(name, **kwargs):
    with bpy.context.temp_override(window=window, area=area, region=region):
        return getattr(bpy.ops.view3d, name)(**kwargs)

def add():
    with bpy.context.temp_override(window=window, area=area, region=region):
        return bpy.ops.view3d.final_dimensions_ruler('INVOKE_DEFAULT')

def select(index):
    return operator('final_dimensions_ruler_select', index=index)

def manager():
    value = ruler.collection(window)
    assert value is not None
    return value

def active():
    value = ruler.get(window)
    assert value is not None
    return value

def endpoints(item):
    return tuple(tuple(hit['point']) if hit else None for hit in (item.first,item.second))

def same_endpoints(item, expected):
    actual = endpoints(item)
    assert all((a is None and b is None) or (a is not None and b is not None and math.dist(a,b)<1e-7)
               for a,b in zip(actual,expected)), (actual,expected)

def passed(name):
    report['cases'].append(name)

def capture(name):
    path = ROOT / 'artifacts' / (name+'-'+bpy.app.version_string.replace(' ','-')+'.png')
    bpy.ops.screen.screenshot(filepath=str(path))

def finish(error=None):
    report['status'] = 'FAIL' if error else 'PASS'
    if error:
        report['error'] = error
    path = ROOT / 'artifacts' / ('multi-ruler-live-'+bpy.app.version_string.replace(' ','-')+'.json')
    path.write_text(json.dumps(report,indent=2), encoding='utf-8')
    print('MULTI RULER LIVE', report['status'], error or '', flush=True)
    bpy.ops.wm.quit_blender()

def tick():
    global area, region, space
    try:
        step = test['step']
        if step == 0:
            add()
            mouse(left.location)
        elif step == 1:
            assert manager().mode == 'ADD' and ruler.is_running(window)
            assert manager().candidate and -.019 < manager().candidate['point'][1] < -.015
            click(left.location)
        elif step == 2:
            assert active().first and not active().second
            mouse(right.location)
        elif step == 3:
            assert manager().candidate and active().distance > .1
            click(right.location)
        elif step == 4:
            assert len(manager().items) == 1 and manager().mode == 'IDLE'
            assert active().first and active().second and not ruler.is_running(window)
            test['first_item'] = active()
            test['first_original'] = endpoints(active())
            passed('two_click_ruler_with_passive_edit_listener')
            add()
            mouse((-.055,0,.009))
        elif step == 5:
            assert len(manager().items) == 2
            assert endpoints(test['first_item']) == test['first_original']
            click((-.055,0,.009))
        elif step == 6:
            mouse((.057,.02,-.012))
        elif step == 7:
            click((.057,.02,-.012))
        elif step == 8:
            assert len(manager().items) == 2 and manager().mode == 'IDLE'
            assert all(item.distance is not None for item in manager().items)
            assert endpoints(test['first_item']) == test['first_original']
            assert any('d:' in label and 'mm' in label for label in test['labels'])
            test['second_item'] = active()
            test['second_original'] = endpoints(active())
            passed('add_preserves_all_completed_rulers')
            select(0)
            assert manager().active == 0 and active() is test['first_item']
            mouse(active().first['point'])
            event('LEFTMOUSE','PRESS',active().first['point'],offset=(8,-5))
        elif step == 9:
            assert manager().mode == 'DRAG' and manager().slot == 0
            mouse((-.056,0,-.008))
        elif step == 10:
            assert manager().mode == 'DRAG' and ruler.is_running(window)
            assert hover.get(window) is None
            assert endpoints(test['second_item']) == test['second_original']
            event('LEFTMOUSE','RELEASE',(-.056,0,-.008))
        elif step == 11:
            assert manager().mode == 'IDLE'
            first_now = endpoints(active())
            assert math.dist(first_now[0],test['first_original'][0]) > .003
            assert first_now[1] == test['first_original'][1]
            test['first_after_drag'] = first_now
            passed('list_select_and_drag_first_endpoint_only')
            select(1)
            assert manager().active == 1
            mouse(active().second['point'])
            event('LEFTMOUSE','PRESS',active().second['point'])
        elif step == 12:
            assert manager().mode == 'DRAG' and manager().slot == 1
            mouse((.052,.02,.009))
        elif step == 13:
            event('LEFTMOUSE','RELEASE',(.052,.02,.009))
        elif step == 14:
            assert manager().mode == 'IDLE'
            assert active().first['point'] == test['second_original'][0]
            assert math.dist(active().second['point'],test['second_original'][1]) > .003
            assert endpoints(test['first_item']) == test['first_after_drag']
            test['fixed_second'] = tuple(active().second['point'])
            passed('switch_and_drag_second_endpoint_only')
            click(active().first['point'], 'RIGHTMOUSE')
        elif step == 15:
            assert manager().mode == 'REPLACE' and manager().slot == 0
            assert active().first is None and tuple(active().second['point']) == test['fixed_second']
            mouse((.042,.02,.010))
        elif step == 16:
            assert manager().candidate and active().distance is not None
            assert active().first is None and tuple(active().second['point']) == test['fixed_second']
            assert manager().candidate['object_name'] == right.name
            click((.042,.02,.010))
        elif step == 17:
            assert manager().mode == 'IDLE' and active().first and active().second
            assert active().first['object_name'] == right.name
            assert tuple(active().second['point']) == test['fixed_second']
            test['fixed_first'] = tuple(active().first['point'])
            passed('right_click_first_replaces_across_objects_keeps_second')
            click(active().second['point'], 'RIGHTMOUSE')
        elif step == 18:
            assert manager().mode == 'REPLACE' and manager().slot == 1
            assert active().second is None and tuple(active().first['point']) == test['fixed_first']
            mouse((.060,.02,-.010))
        elif step == 19:
            click((.060,.02,-.010))
        elif step == 20:
            assert manager().mode == 'IDLE' and active().second
            assert tuple(active().first['point']) == test['fixed_first']
            test['second_after_replace'] = endpoints(active())
            passed('right_click_second_replaces_keeps_first')
            mouse(active().first['point'])
            event('LEFTMOUSE','PRESS',active().first['point'])
        elif step == 21:
            assert manager().mode == 'DRAG'
            mouse((0,0,.05))
        elif step == 22:
            assert manager().candidate is None
            event('LEFTMOUSE','RELEASE',(0,0,.05))
        elif step == 23:
            assert manager().mode == 'IDLE'
            assert endpoints(active()) == test['second_after_replace']
            passed('drag_release_in_empty_space_restores_original_anchor')
            click(active().second['point'], 'RIGHTMOUSE')
        elif step == 24:
            assert active().second is None and manager().mode == 'REPLACE'
            event('ESC','PRESS')
        elif step == 25:
            assert manager().mode == 'IDLE'
            assert endpoints(active()) == test['second_after_replace']
            passed('escape_restores_replaced_endpoint')
            add()
            mouse(left.location)
            click(left.location)
        elif step == 26:
            assert len(manager().items) == 3 and manager().mode == 'ADD'
            event('ESC','PRESS')
        elif step == 27:
            assert len(manager().items) == 2 and manager().mode == 'IDLE'
            assert all(item.first and item.second for item in manager().items)
            passed('escape_cancels_only_new_unfinished_ruler')
            capture('multi-ruler-preview')
            with bpy.context.temp_override(window=window, area=area, region=region):
                bpy.ops.wm.call_panel(name='VIEW3D_PT_final_dimensions',keep_open=True)
        elif step == 28:
            capture('multi-ruler-panel')
            event('ESC','PRESS')
        elif step == 29:
            space.overlay.show_overlays = False
            mouse(active().first['point'])
            click(active().first['point'])
        elif step == 30:
            assert manager().mode == 'IDLE'
            same_endpoints(active(), test['second_after_replace'])
            passed('overlays_off_does_not_capture_invisible_handles')
            space.overlay.show_overlays = True
            left.modifiers[0].levels = 3
        elif step == 31:
            assert test['first_item'].first is None and test['first_item'].second
            assert test['first_item'].distance is None
            same_endpoints(test['second_item'], test['second_after_replace'])
            passed('topology_invalidates_only_affected_endpoint_and_ruler')
            select(0)
            operator('final_dimensions_ruler_endpoint', endpoint=0)
            mouse(left.location)
            click(left.location)
        elif step == 32:
            assert active().first and active().second and manager().mode == 'IDLE'
            passed('missing_endpoint_can_be_picked_again')
            test['before_transform'] = [endpoints(item) for item in manager().items]
            right.location.x += .01
        elif step == 33:
            for index, item in enumerate(manager().items):
                old = test['before_transform'][index]
                assert abs(item.second['point'][0]-old[1][0]-.01) < 1e-6
            assert abs(test['second_item'].first['point'][0]-test['before_transform'][1][0][0]-.01) < 1e-6
            passed('all_rulers_follow_surface_object_transform')
            for obj in (left,right):
                assert tuple(tuple(v.co) for v in obj.data.vertices) == source_vertices[obj.name]
            assert len(bpy.data.meshes) == source_meshes
            passed('editing_preserves_source_geometry_and_temporary_mesh_count')
            test['before_other_view'] = endpoints(active())
            area = next(a for a in window.screen.areas if a.type == 'PROPERTIES')
            area.type = 'VIEW_3D'
            space = area.spaces.active
            space.show_region_ui = space.show_region_toolbar = False
            space.region_3d.view_location = (0,0,0)
            space.region_3d.view_distance = .3
            space.region_3d.view_rotation = Quaternion((1,0,0), math.pi/2)
            space.region_3d.view_perspective = 'ORTHO'
            space.clip_start = .0001
            region = next(r for r in area.regions if r.type == 'WINDOW')
            select(0)
        elif step == 34:
            assert active().area_pointer == area.as_pointer()
            mouse(active().first['point'])
            event('LEFTMOUSE','PRESS',active().first['point'])
        elif step == 35:
            assert manager().mode == 'DRAG'
            mouse((-.053,0,.010))
        elif step == 36:
            event('LEFTMOUSE','RELEASE',(-.053,0,.010))
        elif step == 37:
            assert manager().mode == 'IDLE'
            assert math.dist(active().first['point'],test['before_other_view'][0]) > .003
            assert math.dist(active().second['point'],test['before_other_view'][1]) < 1e-7
            passed('list_selection_rebinds_editing_to_another_viewport')
            operator('final_dimensions_ruler_clear')
            assert len(manager().items) == 1 and active() is test['second_item']
            passed('delete_selected_preserves_other_ruler')
            operator('final_dimensions_ruler_clear')
            assert ruler.collection(window) is None and window.as_pointer() not in ruler._sessions
            passed('last_delete_cleans_passive_listener')
            add()
            mouse(left.location)
        elif step == 38:
            assert ruler.is_running(window)
            addon._on_load_pre(None)
            addon._on_state_reset(None)
            assert not ruler._sessions and not ruler._states
            passed('load_undo_lifecycle_clears_all_rulers')
            addon.unregister()
            assert not ruler._handles and not ruler._sessions and not ruler._states
            assert not hover._operators
            passed('unregister_cleanup')
            finish()
            return None
        test['step'] += 1
        area.tag_redraw()
        return .65
    except Exception:
        finish(traceback.format_exc())
        return None

bpy.app.timers.register(tick, first_interval=2.)
