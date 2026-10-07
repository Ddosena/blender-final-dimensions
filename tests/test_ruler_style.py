"""Display-only ruler style checks; run with Blender --background --python."""

import json
import sys
from pathlib import Path
from types import SimpleNamespace

import bpy


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from final_dimensions import ruler_style


VERSION = bpy.app.version_string.replace(' ', '-')
TEMP = ROOT / 'artifacts' / 'runtime-temp' / f'ruler-style-{VERSION}'
TEMP.mkdir(parents=True, exist_ok=True)
bpy.context.preferences.filepaths.temporary_directory = str(TEMP)
REPORT = ROOT / 'artifacts' / f'ruler-style-{VERSION}.json'


def run():
    ruler_style.register()
    try:
        scene = bpy.context.scene
        scene.unit_settings.system = 'METRIC'
        scene.unit_settings.length_unit = 'MILLIMETERS'
        scene.unit_settings.scale_length = 1.0
        settings = scene.final_dimensions_ruler_appearance
        settings.color = (.8, .4, .2, .9)
        settings.inactive_brightness = .5
        item = SimpleNamespace(label='Основная часть', precision=2,
                               trim_zeros=True, color=None)
        assert ruler_style.distance_text(scene, item, .002345) == '2.35 mm'
        assert ruler_style.distance_text(scene, item, .002) == '2 mm'
        assert ruler_style.label_text(scene, item, 0, .002) == 'Основная часть: 2 mm'
        assert ruler_style.label_text(scene, item, 0, None) == 'Основная часть: Pick points'
        item.label = ''
        assert ruler_style.label_text(scene, item, 1, .002) == '2 d: 2 mm'
        item.trim_zeros = False
        assert ruler_style.distance_text(scene, item, .002) == '2.00 mm'
        item.precision = 0
        assert ruler_style.distance_text(scene, item, .020) == '20 mm'
        item.trim_zeros = True
        assert ruler_style.distance_text(scene, item, .020) == '20 mm'
        assert ruler_style.distance_text(scene, item, 0) == '0 mm'
        scene.unit_settings.length_unit = 'ADAPTIVE'
        item.precision = 2
        assert ruler_style.distance_text(scene, item, .002345) == '2.35 mm'
        assert ruler_style.distance_text(scene, item, .002) == '2 mm'
        item.precision = 0
        assert ruler_style.distance_text(scene, item, .002) == '2 mm'
        item.precision = 6
        assert ruler_style.distance_text(scene, item, .002345678) == '2.345678 mm'
        scene.unit_settings.length_unit = 'MILLIMETERS'
        item.color = None
        assert all(abs(a - b) < 1e-6 for a, b in zip(
            ruler_style.line_color(scene, item, True), (.8, .4, .2, .9)))
        assert all(abs(a - b) < 1e-6 for a, b in zip(
            ruler_style.line_color(scene, item, False), (.4, .2, .1, .9)))
        item.color = (.2, .8, .6, .7)
        assert all(abs(a - b) < 1e-6 for a, b in zip(
            ruler_style.line_color(scene, item, False), (.1, .4, .3, .7)))
        REPORT.write_text(json.dumps({'status': 'PASS', 'blender': bpy.app.version_string,
                                      'cases': ['unit_rounding', 'adaptive_units', 'trailing_zeros',
                                                'named_and_unnamed_labels',
                                                'global_color', 'override_and_brightness']},
                                     ensure_ascii=False, indent=2), encoding='utf-8')
        print('RULER STYLE PASS', flush=True)
    finally:
        ruler_style.unregister()


run()
