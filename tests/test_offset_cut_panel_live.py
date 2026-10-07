"""Click the actual Offset Cut button in the Final Dimensions sidebar."""
import json
import sys
import traceback
from pathlib import Path

import bpy

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import final_dimensions as addon
from final_dimensions import offset_cut

report = {'blender': bpy.app.version_string, 'cases': []}
state = {'step': 0}
bpy.context.preferences.view.show_splash = False
temp = ROOT / 'artifacts/runtime-temp' / ('offset-panel-'+bpy.app.version_string.replace(' ', '-'))
temp.mkdir(parents=True, exist_ok=True)
bpy.context.preferences.filepaths.temporary_directory = str(temp)
addon.register()
bpy.ops.object.select_all(action='SELECT')
bpy.ops.object.delete(use_global=False)
bpy.ops.mesh.primitive_cube_add(size=10)
bpy.ops.object.mode_set(mode='EDIT')
window = bpy.context.window
area = next(a for a in window.screen.areas if a.type == 'VIEW_3D')
area.spaces.active.show_region_ui = True
ui = next(r for r in area.regions if r.type == 'UI')
# Default collapsed Appearance adds one row above the ruler controls.
BUTTON_FROM_TOP = 260


def click(x, y):
    window.event_simulate(type='LEFTMOUSE', value='PRESS', x=x, y=y)
    window.event_simulate(type='LEFTMOUSE', value='RELEASE', x=x, y=y)


def finish(error=None):
    report['status'] = 'FAIL' if error else 'PASS'
    if error:
        report['error'] = error
    path = ROOT / 'artifacts' / ('offset-cut-panel-live-'+bpy.app.version_string.replace(' ', '-')+'.json')
    path.write_text(json.dumps(report, indent=2), encoding='utf-8')
    print('OFFSET CUT PANEL LIVE', report['status'], error or '', flush=True)
    addon.unregister()
    bpy.ops.wm.quit_blender()


def tick():
    try:
        step = state['step']
        if step == 0:
            print('PANEL BOUNDS', window.width, window.height, area.x, area.y, area.width,
                  area.height, ui.x, ui.y, ui.width, ui.height,
                  'category', getattr(ui, 'active_panel_category', None), flush=True)
            try:
                ui.active_panel_category = 'Final Dimensions'
                print('SET CATEGORY', ui.active_panel_category, flush=True)
            except Exception as exc:
                print('SET CATEGORY FAILED', repr(exc), flush=True)
        elif step == 1:
            bpy.ops.screen.screenshot(filepath=str(ROOT / 'artifacts' /
                ('offset-cut-panel-'+bpy.app.version_string.replace(' ', '-')+'.png')))
            print('PANEL CATEGORY', ui.active_panel_category, flush=True)
            window.event_simulate(type='MOUSEMOVE', value='NOTHING',
                                  x=ui.x + ui.width//2, y=ui.y + ui.height - BUTTON_FROM_TOP)
        elif step == 2:
            click(ui.x + ui.width//2, ui.y + ui.height - BUTTON_FROM_TOP)
        elif step == 3:
            assert offset_cut._sessions.get(area.as_pointer()) is not None, 'Sidebar button did not launch Offset Cut'
            report['cases'].append('actual Sidebar Pick Face & Preview button launches modal')
            window.event_simulate(type='ESC', value='PRESS')
        elif step == 4:
            assert offset_cut._sessions.get(area.as_pointer()) is None
            finish()
            return None
        state['step'] += 1
        area.tag_redraw()
        return .5
    except Exception:
        finish(traceback.format_exc())
        return None


bpy.app.timers.register(tick, first_interval=1.)
