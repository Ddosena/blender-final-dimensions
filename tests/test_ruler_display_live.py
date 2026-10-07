"""Integrated viewport and panel proof for ruler appearance in a private UI."""

import json
import math
import sys
import traceback
from pathlib import Path

import bpy
from mathutils import Quaternion


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import final_dimensions as addon
from final_dimensions import drawing, overlay, ruler, ruler_style


VERSION = bpy.app.version_string.replace(' ', '-')
TEMP = ROOT / 'artifacts' / 'runtime-temp' / f'ruler-display-{VERSION}'
TEMP.mkdir(parents=True, exist_ok=True)
bpy.context.preferences.filepaths.temporary_directory = str(TEMP)
bpy.context.preferences.view.show_splash = False
REPORT = ROOT / 'artifacts' / f'ruler-display-live-{VERSION}.json'
IMAGE = ROOT / 'artifacts' / f'ruler-display-{VERSION}.png'
DIALOG_IMAGE = ROOT / 'artifacts' / f'ruler-display-dialog-{VERSION}.png'
state = {'step': 0, 'lines': [], 'labels': [], 'cases': []}
BUTTON_TOP = 270 if bpy.app.version < (5, 0, 0) else 325

addon.register()
window = bpy.context.window
area = next(area for area in window.screen.areas if area.type == 'VIEW_3D')
region = next(region for region in area.regions if region.type == 'WINDOW')
space = area.spaces.active
space.show_region_toolbar = False
space.show_region_ui = True
space.region_3d.view_location = (0, 0, 0)
space.region_3d.view_distance = 7
space.region_3d.view_rotation = Quaternion((1, 0, 0, 0))
space.region_3d.view_perspective = 'ORTHO'
scene = bpy.context.scene
scene.unit_settings.system = 'METRIC'
scene.unit_settings.scale_length = .001
scene.unit_settings.length_unit = 'MILLIMETERS'
settings = scene.final_dimensions_ruler_appearance
settings.color = (1., .55, .12, 1.)
settings.inactive_brightness = .25
settings.show_appearance = True
with bpy.context.temp_override(window=window, area=area, region=region):
    manager = ruler.RulerCollection(bpy.context)
    first = ruler.RulerState(bpy.context)
    second = ruler.RulerState(bpy.context)
first.first = ruler._world_hit((-1.5, .6, 0))
first.second = ruler._world_hit((.5, .6, 0))
first.label = 'Основная часть'
first.precision = 2
first.trim_zeros = True
first.color = (.05, .95, 1., 1.)
second.first = ruler._world_hit((-.75, -.6, 0))
second.second = ruler._world_hit((.484, -.6, 0))
second.label = 'Вторая часть'
second.precision = 2
second.trim_zeros = True
manager.items[:] = [first, second]
manager.active = 0
manager.epoch = addon._epoch
manager.recalculate()
first_original = first.first
first_distance = first.distance
ruler._states[window.as_pointer()] = manager
original_line, original_text = drawing.line, overlay._text


def record_line(a, b, color, width=2):
    state['lines'].append((tuple(a), tuple(b), tuple(color), width))
    return original_line(a, b, color, width)


def record_text(x, y, value, color):
    state['labels'].append(value)
    return original_text(x, y, value, color)


drawing.line = record_line
overlay._text = record_text


def ui_event(kind, value, x, top_y):
    window.event_simulate(type=kind, value=value, x=x, y=window.height - top_y)


def click(x, top_y):
    ui_event('MOUSEMOVE', 'NOTHING', x, top_y)
    ui_event('LEFTMOUSE', 'PRESS', x, top_y)
    ui_event('LEFTMOUSE', 'RELEASE', x, top_y)


def finish(error=None):
    result = {'status': 'FAIL' if error else 'PASS', 'blender': bpy.app.version_string,
              'cases': state['cases'], 'draw_calls': len(state['lines'])}
    if error:
        result['error'] = error
    REPORT.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    print('RULER DISPLAY LIVE', result['status'], error or '', flush=True)
    drawing.line, overlay._text = original_line, original_text
    bpy.ops.wm.quit_blender()


def tick():
    try:
        step = state['step']
        if step == 0:
            assert math.isclose(first.distance, 2., abs_tol=1e-9)
            assert math.isclose(second.distance, 1.234, abs_tol=1e-6), second.distance
            assert ruler_style.label_text(scene, first, 0, first.distance) == 'Основная часть: 2 mm'
            assert ruler_style.label_text(scene, second, 1, second.distance) == 'Вторая часть: 1.23 mm'
            assert manager.epoch == addon._epoch
            assert any('Основная часть: 2 mm' == value for value in state['labels']), state['labels'][-10:]
            assert any('Вторая часть: 1.23 mm' == value for value in state['labels']), state['labels'][-10:]
            assert any(color[1] > .9 and color[2] > .9 for _, _, color, _ in state['lines'])
            assert any(color[0] > .2 and color[1] < .2 for _, _, color, _ in state['lines'])
            state['cases'].append('two_gpu_lines_labels_colors_and_exact_distances')
            with bpy.context.temp_override(window=window, area=area, region=region):
                bpy.ops.wm.call_panel(name='VIEW3D_PT_final_dimensions', keep_open=True)
        elif step == 1:
            bpy.ops.screen.screenshot(filepath=str(IMAGE))
            state['cases'].append('native_panel_screenshot')
        elif step == 2:
            click(110, BUTTON_TOP)
        elif step == 3:
            bpy.ops.screen.screenshot(filepath=str(DIALOG_IMAGE))
            ui_event('ESC', 'PRESS', 110, BUTTON_TOP)
        elif step == 4:
            assert first.label == 'Основная часть'
            assert first.precision == 2
            assert first.color == (.05, .95, 1., 1.)
            assert first.first is first_original
            assert math.isclose(first.distance, first_distance, abs_tol=1e-9)
            state['cases'].append('sidebar_button_dialog_cancel_preserves_style')
            ui_event('ESC', 'PRESS', 110, BUTTON_TOP)
        elif step == 5:
            finish()
            return None
        state['step'] += 1
        area.tag_redraw()
        return .7
    except Exception:
        finish(traceback.format_exc())
        return None


bpy.app.timers.register(tick, first_interval=1.0)
