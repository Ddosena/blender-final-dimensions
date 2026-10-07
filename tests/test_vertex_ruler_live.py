"""Disposable GUI check: click two plane vertices, inset, and move the cage."""
import json
import math
import sys
import traceback
from pathlib import Path

import bmesh
import bpy
from bpy_extras import view3d_utils
from mathutils import Quaternion, Vector

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import final_dimensions as addon
from final_dimensions import ruler

report = {'blender': bpy.app.version_string, 'cases': []}
state = {'step': 0}
bpy.context.preferences.view.show_splash = False
temp = ROOT / 'artifacts/runtime-temp' / ('vertex-ruler-'+bpy.app.version_string.replace(' ', '-'))
temp.mkdir(parents=True, exist_ok=True)
bpy.context.preferences.filepaths.temporary_directory = str(temp)
addon.register()
bpy.ops.object.select_all(action='SELECT')
bpy.ops.object.delete(use_global=False)
mesh = bpy.data.meshes.new('ruler inset plane')
mesh.from_pydata([(-1,-1,0), (1,-1,0), (1,1,0), (-1,1,0)], [], [(0,1,2,3)])
mesh.update()
obj = bpy.data.objects.new('ruler inset plane', mesh)
bpy.context.collection.objects.link(obj)
bpy.context.view_layer.objects.active = obj
obj.select_set(True)

window = bpy.context.window
area = next(a for a in window.screen.areas if a.type == 'VIEW_3D')
region = next(r for r in area.regions if r.type == 'WINDOW')
space = area.spaces.active
space.show_region_ui = False
space.show_region_toolbar = False
space.region_3d.view_location = (0, 0, 0)
space.region_3d.view_distance = 5
space.region_3d.view_rotation = Quaternion((1, 0, 0, 0))
space.region_3d.view_perspective = 'ORTHO'
bpy.context.scene.tool_settings.use_snap = True
bpy.context.scene.tool_settings.snap_elements = {'VERTEX'}
bpy.context.window_manager.final_dimensions_snap_source = 'BOTH'


def event(kind, value, point):
    p = view3d_utils.location_3d_to_region_2d(region, space.region_3d, Vector(point))
    assert p is not None, point
    window.event_simulate(type=kind, value=value, x=region.x+round(p.x), y=region.y+round(p.y))


def mouse(point):
    event('MOUSEMOVE', 'NOTHING', point)


def click(point):
    event('LEFTMOUSE', 'PRESS', point)
    event('LEFTMOUSE', 'RELEASE', point)


def manager():
    result = ruler.collection(window)
    assert result is not None
    return result


def refresh():
    bpy.context.view_layer.update()
    with bpy.context.temp_override(window=window, area=area, region=region):
        manager().refresh(bpy.context, manager().epoch+1)


def finish(error=None):
    report['status'] = 'FAIL' if error else 'PASS'
    if error:
        report['error'] = error
    path = ROOT / 'artifacts' / ('vertex-ruler-live-'+bpy.app.version_string.replace(' ', '-')+'.json')
    path.write_text(json.dumps(report, indent=2), encoding='utf-8')
    print('VERTEX RULER LIVE', report['status'], error or '', flush=True)
    bpy.ops.wm.quit_blender()


def tick():
    try:
        step = state['step']
        if step == 0:
            with bpy.context.temp_override(window=window, area=area, region=region):
                bpy.ops.view3d.final_dimensions_ruler('INVOKE_DEFAULT')
            mouse((-1,-1,0))
        elif step == 1:
            hit = manager().candidate
            assert hit and hit['snap_source'] == 'ORIGINAL' and hit['snap_kind'] == 'VERTEX', hit
            click((-1,-1,0))
        elif step == 2:
            mouse((1,-1,0))
        elif step == 3:
            hit = manager().candidate
            assert hit and hit['snap_source'] == 'ORIGINAL' and hit['snap_kind'] == 'VERTEX', hit
            click((1,-1,0))
        elif step == 4:
            item = manager().selected
            assert item and item.first and item.second and math.isclose(item.distance, 2, abs_tol=1e-5)
            assert item.first['anchor']['vertex_id'] != item.second['anchor']['vertex_id']
            report['cases'].append('mouse clicks bind distinct original vertices')
            bpy.ops.object.mode_set(mode='EDIT')
            refresh()
            bpy.ops.mesh.inset(thickness=.25, depth=0)
            refresh()
        elif step == 5:
            item = manager().selected
            assert item and math.isclose(item.distance, 2, abs_tol=1e-5), item
            bm = bmesh.from_edit_mesh(obj.data)
            layer = bm.verts.layers.int.get(item.first['anchor']['vertex_layer'])
            moving = next(v for v in bm.verts if int(v[layer]) == item.first['anchor']['vertex_id'])
            moving.co.x -= .5
            bmesh.update_edit_mesh(obj.data)
            refresh()
        elif step == 6:
            item = manager().selected
            assert item and item.first and item.second
            assert math.isclose(item.distance, 2.5, abs_tol=1e-5), item.distance
            assert math.isclose(item.first['point'][0], -1.5, abs_tol=1e-5)
            report['cases'].append('inset and edit move update live ruler distance')
            bpy.ops.object.mode_set(mode='OBJECT')
            refresh()
        elif step == 7:
            item = manager().selected
            assert item and math.isclose(item.distance, 2.5, abs_tol=1e-5)
            report['cases'].append('object mode retains vertex identity')
            addon.unregister()
            finish()
            return None
        state['step'] += 1
        area.tag_redraw()
        return .5
    except Exception:
        finish(traceback.format_exc())
        return None


bpy.app.timers.register(tick, first_interval=1.)
