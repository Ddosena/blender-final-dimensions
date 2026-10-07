"""Disposable GUI: real face pick, preview, cancel, confirm and Ctrl+Z."""
import json
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
from final_dimensions import offset_cut, ruler

report = {'blender': bpy.app.version_string, 'cases': []}
state = {'step': -4}
bpy.context.preferences.view.show_splash = False
temp = ROOT / 'artifacts/runtime-temp' / ('offset-cut-'+bpy.app.version_string.replace(' ', '-'))
temp.mkdir(parents=True, exist_ok=True)
bpy.context.preferences.filepaths.temporary_directory = str(temp)
addon.register()
bpy.ops.object.select_all(action='SELECT')
bpy.ops.object.delete(use_global=False)
bpy.ops.mesh.primitive_cube_add(size=10)
obj = bpy.context.object
bpy.context.scene.unit_settings.system = 'METRIC'
bpy.context.scene.unit_settings.scale_length = .001
bpy.context.window_manager.final_dimensions_offset_distance = '2 mm'
bpy.ops.object.mode_set(mode='EDIT')
bpy.context.tool_settings.mesh_select_mode = (False, False, True)

window = bpy.context.window
area = next(a for a in window.screen.areas if a.type == 'VIEW_3D')
region = next(r for r in area.regions if r.type == 'WINDOW')
space = area.spaces.active
space.show_region_ui = True
space.show_region_toolbar = False
space.region_3d.view_location = (0, 0, 0)
space.region_3d.view_distance = 25
space.region_3d.view_rotation = Quaternion((1, 0, 0, 0))
space.region_3d.view_perspective = 'ORTHO'


def event(kind, value='PRESS', point=None, ctrl=False, unicode=''):
    args = {'type': kind, 'value': value, 'ctrl': ctrl}
    if point is not None:
        p = view3d_utils.location_3d_to_region_2d(region, space.region_3d, Vector(point))
        assert p is not None
        args.update(x=region.x+round(p.x), y=region.y+round(p.y))
    if unicode:
        args['unicode'] = unicode
    window.event_simulate(**args)


def mesh_state():
    current = bpy.context.edit_object
    assert current is not None, 'Undo left Edit Mode or removed the test object'
    bm = bmesh.from_edit_mesh(current.data)
    return len(bm.verts), len(bm.edges), len(bm.faces), tuple(sorted(tuple(v.co) for v in bm.verts))


def session():
    return offset_cut._sessions.get(area.as_pointer())


def finish(error=None):
    report['status'] = 'FAIL' if error else 'PASS'
    if error:
        report['error'] = error
    path = ROOT / 'artifacts' / ('offset-cut-live-'+bpy.app.version_string.replace(' ', '-')+'.json')
    path.write_text(json.dumps(report, indent=2), encoding='utf-8')
    print('OFFSET CUT LIVE', report['status'], error or '', flush=True)
    addon.unregister()
    bpy.ops.wm.quit_blender()


def tick():
    try:
        step = state['step']
        if step == -4:
            with bpy.context.temp_override(window=window, area=area, region=region):
                assert bpy.ops.view3d.final_dimensions_ruler('INVOKE_DEFAULT') in ({'RUNNING_MODAL'}, {'FINISHED'})
            event('MOUSEMOVE', 'NOTHING', (0,0,5))
        elif step == -3:
            event('LEFTMOUSE', 'PRESS', (0,0,5))
            event('LEFTMOUSE', 'RELEASE', (0,0,5))
        elif step == -2:
            event('MOUSEMOVE', 'NOTHING', (3,0,5))
        elif step == -1:
            event('LEFTMOUSE', 'PRESS', (3,0,5))
            event('LEFTMOUSE', 'RELEASE', (3,0,5))
        elif step == 0:
            manager = ruler.collection(window)
            assert manager and manager.selected and manager.selected.distance is not None
            report['cases'].append('completed ruler remains active beside Offset Cut')
            bpy.ops.ed.undo_push(message='before Offset Cut')
            state['before'] = mesh_state()
            ui_region = next(r for r in area.regions if r.type == 'UI')
            bpy.ops.screen.screenshot(filepath=str(ROOT / 'artifacts/runtime-temp/offset-sidebar.png'))
            with bpy.context.temp_override(window=window, area=area, region=ui_region):
                assert bpy.context.region.type == 'UI'
                assert bpy.ops.view3d.final_dimensions_offset_cut('INVOKE_DEFAULT') == {'RUNNING_MODAL'}
            report['cases'].append('Sidebar UI region launches face picker')
            event('MOUSEMOVE', 'NOTHING', (0,0,5))
        elif step == 1:
            assert session() and session()._candidate is not None
            event('LEFTMOUSE', 'PRESS', (0,0,5))
        elif step == 2:
            op = session()
            assert op and op._plan and len(op._plan['segments']) == 4, (op, getattr(op, '_face_ref', None), getattr(op, '_candidate', None), getattr(op, '_plan', None))
            assert mesh_state() == state['before']
            report['cases'].append('face picked and 2 mm preview makes no mesh edit')
            event('F')
        elif step == 3:
            assert session() and session()._plan is None
            assert mesh_state() == state['before']
            report['cases'].append('F reverses direction and nonintersecting side has no preview')
            event('F')
        elif step == 4:
            assert session() and session()._plan
            event('ONE', unicode='1')
            event('SPACE', unicode=' ')
            event('M', unicode='m')
            event('M', unicode='m')
        elif step == 5:
            assert bpy.context.window_manager.final_dimensions_offset_distance == '1 mm'
            assert session() and session()._plan
            assert abs(session()._plan['plane_co'].z-4) < 1e-5
            assert mesh_state() == state['before']
            report['cases'].append('typed units update exact preview before commit')
            ui = next(r for r in area.regions if r.type == 'UI')
            x, y = ui.x+ui.width//2, ui.y+ui.height-225
            window.event_simulate(type='LEFTMOUSE', value='PRESS', x=x, y=y)
            window.event_simulate(type='LEFTMOUSE', value='RELEASE', x=x, y=y)
        elif step == 6:
            assert session() is not None and mesh_state() == state['before']
            report['cases'].append('Sidebar click during preview cannot confirm cut')
            event('ESC')
        elif step == 7:
            assert session() is None and mesh_state() == state['before']
            report['cases'].append('Esc cancels without any mesh change')
            bpy.context.window_manager.final_dimensions_offset_distance = '2 mm'
            with bpy.context.temp_override(window=window, area=area, region=region):
                assert bpy.ops.view3d.final_dimensions_offset_cut('INVOKE_DEFAULT') == {'RUNNING_MODAL'}
            event('MOUSEMOVE', 'NOTHING', (0,0,5))
        elif step == 8:
            event('LEFTMOUSE', 'PRESS', (0,0,5))
        elif step == 9:
            assert session() and session()._plan
            event('LEFTMOUSE', 'PRESS', (0,0,5))
        elif step == 10:
            assert session() is None
            after = mesh_state()
            assert after[0] == state['before'][0] + 4, after[:3]
            bm = bmesh.from_edit_mesh(obj.data)
            assert sum(abs(v.co.z-3) < 1e-5 for v in bm.verts) == 4
            report['cases'].append('viewport click commits exact cut without moving old vertices')
            event('LEFT_CTRL', 'PRESS', ctrl=True)
            event('Z', 'PRESS', ctrl=True)
            event('Z', 'RELEASE', ctrl=True)
            event('LEFT_CTRL', 'RELEASE')
        elif step == 11:
            assert mesh_state() == state['before'], mesh_state()[:3]
            report['cases'].append('Ctrl+Z restores the mesh')
            with bpy.context.temp_override(window=window, area=area, region=region):
                assert bpy.ops.view3d.final_dimensions_offset_cut('INVOKE_DEFAULT') == {'RUNNING_MODAL'}
            event('MOUSEMOVE', 'NOTHING', (0,0,5))
        elif step == 12:
            assert session() is not None
            offset_cut._on_save_pre(None)
            assert session() is None
            with bpy.context.temp_override(window=window, area=area, region=region):
                assert bpy.ops.view3d.final_dimensions_offset_cut('INVOKE_DEFAULT') == {'RUNNING_MODAL'}
            state['new_session'] = session()
            assert state['new_session'] is not None
            event('MOUSEMOVE', 'NOTHING', (0,0,5))
        elif step == 13:
            assert session() is state['new_session'] and mesh_state() == state['before']
            report['cases'].append('save cancellation does not erase a newly started session')
            event('ESC')
        elif step == 14:
            assert session() is None and mesh_state() == state['before']
            finish()
            return None
        state['step'] += 1
        area.tag_redraw()
        return .5
    except Exception:
        finish(traceback.format_exc())
        return None


bpy.app.timers.register(tick, first_interval=1.)
