"""Native ruler-style dialog apply and cancel in a disposable Blender UI."""

import json
import sys
import traceback
from pathlib import Path

import bpy


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from final_dimensions import ruler, ruler_style


VERSION = bpy.app.version_string.replace(' ', '-')
TEMP = ROOT / 'artifacts' / 'runtime-temp' / f'ruler-style-live-{VERSION}'
TEMP.mkdir(parents=True, exist_ok=True)
bpy.context.preferences.filepaths.temporary_directory = str(TEMP)
bpy.context.preferences.view.show_splash = False
REPORT = ROOT / 'artifacts' / f'ruler-style-live-{VERSION}.json'
state = {'step': 0, 'cases': []}

ruler_style.register()
window = bpy.context.window
area = next(a for a in window.screen.areas if a.type == 'VIEW_3D')
region = next(r for r in area.regions if r.type == 'WINDOW')
scene = bpy.context.scene
scene.unit_settings.system = 'METRIC'
scene.unit_settings.length_unit = 'MILLIMETERS'
scene.final_dimensions_ruler_appearance.color = (.2, .4, .8, 1.0)
manager = None
with bpy.context.temp_override(window=window, area=area, region=region):
    manager = ruler.RulerCollection(bpy.context)
    item = ruler.RulerState(bpy.context)
manager.items.append(item)
manager.active = 0
item.distance = .002345
item.label = ''
item.precision = 3
item.trim_zeros = True
item.color = None
ruler._states[window.as_pointer()] = manager


def dialog():
    with bpy.context.temp_override(window=window, area=area, region=region):
        result = bpy.ops.view3d.final_dimensions_ruler_style('INVOKE_DEFAULT')
    assert result == {'RUNNING_MODAL'}, result


def ui_event(kind, value, x, top_y, **options):
    window.event_simulate(type=kind, value=value, x=x,
                          y=window.height - top_y, **options)


def ui_click(x, top_y):
    ui_event('MOUSEMOVE', 'NOTHING', x, top_y)
    ui_event('LEFTMOUSE', 'PRESS', x, top_y)
    ui_event('LEFTMOUSE', 'RELEASE', x, top_y)


def ui_type(value, x, top_y):
    for char in value:
        ui_event('A', 'PRESS', x, top_y, unicode=char)
        ui_event('A', 'RELEASE', x, top_y)


def key(kind, x=100, top_y=670):
    ui_event(kind, 'PRESS', x, top_y)


def finish(error=None):
    result = {'status': 'FAIL' if error else 'PASS',
              'blender': bpy.app.version_string, 'cases': state['cases']}
    if error:
        result['error'] = error
    REPORT.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    print('RULER STYLE LIVE', result['status'], error or '', flush=True)
    ruler._states.pop(window.as_pointer(), None)
    ruler_style.unregister()
    bpy.ops.wm.quit_blender()


def tick():
    try:
        step = state['step']
        if step == 0:
            dialog()
        elif step == 1:
            ui_click(190, 568)
        elif step == 2:
            ui_type('Основная часть', 190, 568)
        elif step == 3:
            key('RET', 190, 568)
            ui_click(306, 593)
        elif step == 4:
            ui_event('A', 'PRESS', 306, 593, ctrl=True)
            ui_event('A', 'RELEASE', 306, 593)
            ui_type('2', 306, 593)
            key('RET', 306, 593)
            ui_click(24, 643)
        elif step == 5:
            bpy.ops.screen.screenshot(filepath=str(ROOT / 'artifacts' /
                                                   f'ruler-style-dialog-{VERSION}.png'))
            ui_click(90, 672)
        elif step == 6:
            assert item.label == 'Основная часть', item.label
            assert item.precision == 2
            assert item.trim_zeros
            assert tuple(round(x, 2) for x in item.color) == (.2, .4, .8, 1.0)
            assert ruler_style.label_text(scene, item, 0, item.distance) == 'Основная часть: 2.35 mm'
            state['cases'].append('dialog_apply_name_rounding_color')
            dialog()
        elif step == 7:
            ui_click(24, 618)
        elif step == 8:
            bpy.ops.screen.screenshot(filepath=str(ROOT / 'artifacts' /
                                                   f'ruler-style-cancel-{VERSION}.png'))
            key('ESC')
        elif step == 9:
            assert item.label == 'Основная часть'
            assert item.precision == 2
            assert tuple(round(x, 2) for x in item.color) == (.2, .4, .8, 1.0)
            state['cases'].append('dialog_cancel_preserves_item')
            finish()
            return None
        state['step'] += 1
        area.tag_redraw()
        return .5
    except Exception:
        finish(traceback.format_exc())
        return None


bpy.app.timers.register(tick, first_interval=1.0)
