"""Isolated addon disable/re-enable while ruler appearance popup is visible."""

import json
import sys
import traceback
from pathlib import Path

import bpy


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import final_dimensions as addon


VERSION = bpy.app.version_string.replace(' ', '-')
TEMP = ROOT / 'artifacts' / 'runtime-temp' / f'ruler-style-disable-{VERSION}'
TEMP.mkdir(parents=True, exist_ok=True)
bpy.context.preferences.filepaths.temporary_directory = str(TEMP)
bpy.context.preferences.view.show_splash = False
REPORT = ROOT / 'artifacts' / f'ruler-style-disable-live-{VERSION}.json'
state = {'step': 0, 'cases': []}
BEFORE = ROOT / 'artifacts' / f'ruler-style-popup-open-{VERSION}.png'
CLOSED = ROOT / 'artifacts' / f'ruler-style-popup-closed-{VERSION}.png'

addon.register()
window = bpy.context.window
area = next(a for a in window.screen.areas if a.type == 'VIEW_3D')
region = next(r for r in area.regions if r.type == 'WINDOW')
scene = bpy.context.scene
scene.final_dimensions_ruler_appearance.show_appearance = True


def finish(error=None):
    report = {'status': 'FAIL' if error else 'PASS',
              'blender': bpy.app.version_string, 'cases': state['cases']}
    if error:
        report['error'] = error
    REPORT.write_text(json.dumps(report, indent=2), encoding='utf-8')
    print('RULER STYLE DISABLE', report['status'], error or '', flush=True)
    bpy.ops.wm.quit_blender()


def red_pixel(path, x, bottom_y):
    image = bpy.data.images.load(str(path), check_existing=False)
    try:
        width, _height = image.size
        return image.pixels[4 * (bottom_y * width + x)]
    finally:
        bpy.data.images.remove(image)


def tick():
    try:
        step = state['step']
        if step == 0:
            with bpy.context.temp_override(window=window, area=area, region=region):
                bpy.ops.wm.call_panel(name='VIEW3D_PT_final_dimensions', keep_open=True)
        elif step == 1:
            bpy.ops.screen.screenshot(filepath=str(BEFORE))
            assert scene.final_dimensions_ruler_appearance.show_appearance
            state['cases'].append('visible_color_control_in_popup')
        elif step == 2:
            # Close the test-owned popup before removing the RNA it displays.
            window.event_simulate(type='ESC', value='PRESS', x=100,
                                  y=window.height - 100)
        elif step == 3:
            bpy.ops.screen.screenshot(filepath=str(CLOSED))
            samples = [(bottom_y, red_pixel(BEFORE, 150, bottom_y),
                        red_pixel(CLOSED, 150, bottom_y))
                       for bottom_y in (150, 200, 250, 300, 350, 400)]
            assert sum(after > before * 1.5 for _, before, after in samples) >= 3, (
                'Popup still covers the viewport', samples)
            assert scene.final_dimensions_ruler_appearance.show_appearance
        elif step == 4:
            addon.unregister()
            assert not hasattr(bpy.types.Scene, 'final_dimensions_ruler_appearance')
            state['cases'].append('closed_popup_before_complete_unregister')
        elif step == 5:
            addon.register()
            assert hasattr(bpy.types.Scene, 'final_dimensions_ruler_appearance')
            state['cases'].append('reregister_without_leaked_rna_classes')
        elif step == 6:
            finish()
            return None
        state['step'] += 1
        area.tag_redraw()
        return .6
    except Exception:
        finish(traceback.format_exc())
        return None


bpy.app.timers.register(tick, first_interval=1.0)
