"""Exercise anti-aliased ruler strokes in a real Blender GPU context."""

import json
import sys
import traceback
from pathlib import Path

import bpy
import gpu
from mathutils import Matrix


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from final_dimensions import drawing


SIZE = 256
VERSION = bpy.app.version_string.replace(' ', '-')
RESULT = ROOT / 'artifacts' / f'smooth-ruler-live-{VERSION}.json'
TEMP = ROOT / 'artifacts' / 'runtime-temp' / f'smooth-{VERSION}'
TEMP.mkdir(parents=True, exist_ok=True)
bpy.context.preferences.filepaths.temporary_directory = str(TEMP)


def render(draw):
    offscreen = gpu.types.GPUOffScreen(SIZE, SIZE)
    try:
        with offscreen.bind():
            framebuffer = gpu.state.active_framebuffer_get()
            framebuffer.clear(color=(0.0, 0.0, 0.0, 1.0))
            gpu.matrix.push()
            gpu.matrix.push_projection()
            try:
                gpu.matrix.load_matrix(Matrix.Identity(4))
                gpu.matrix.load_projection_matrix(Matrix((
                    (2 / SIZE, 0, 0, -1),
                    (0, 2 / SIZE, 0, -1),
                    (0, 0, 1, 0),
                    (0, 0, 0, 1))))
                old_blend = gpu.state.blend_get()
                old_depth = gpu.state.depth_test_get()
                try:
                    gpu.state.blend_set('NONE')
                    gpu.state.depth_test_set('LESS_EQUAL')
                    draw()
                    assert gpu.state.blend_get() == 'NONE'
                    assert gpu.state.depth_test_get() == 'LESS_EQUAL'
                finally:
                    gpu.state.depth_test_set(old_depth)
                    gpu.state.blend_set(old_blend)
                pixels = framebuffer.read_color(0, 0, SIZE, SIZE, 4, 0, 'UBYTE')
                if bpy.app.version < (5, 0, 0):
                    return bytes(channel for row in pixels for rgba in row for channel in rgba)
                return bytes(pixels)
            finally:
                gpu.matrix.pop_projection()
                gpu.matrix.pop()
    finally:
        offscreen.free()


def pixel(pixels, x, y):
    return pixels[4 * (y * SIZE + x):4 * (y * SIZE + x + 1)]


def run():
    cases = {}
    strokes = (
        ('diagonal', lambda: drawing.line((20.5, 30.5), (230.5, 190.5), (1, 1, 1, 1), 2)),
        ('horizontal', lambda: drawing.line((20.25, 120.25), (230.25, 120.25), (1, 1, 1, 1), 2)),
        ('vertical', lambda: drawing.line((120.25, 20.25), (120.25, 230.25), (1, 1, 1, 1), 2)),
        ('zero_length', lambda: drawing.line((120.25, 120.25), (120.25, 120.25), (1, 1, 1, 1), 2)),
        ('ring', lambda: drawing.ring((128.5, 128.5), 9, (1, 1, 1, 1), 2)),
    )
    for name, draw in strokes:
        pixels = render(draw)
        red = pixels[0::4]
        edge = sum(0 < value < 255 for value in red)
        solid = sum(value == 255 for value in red)
        assert edge > 0, (name, edge, solid)
        assert solid > 0, (name, edge, solid)
        assert pixel(pixels, 0, 0)[0] == 0
        cases[name] = {'partial_coverage_pixels': edge, 'solid_pixels': solid}
        if name == 'diagonal':
            image = bpy.data.images.new('Smooth ruler proof', width=SIZE, height=SIZE,
                                        alpha=True, float_buffer=False)
            image.pixels.foreach_set([channel / 255 for channel in pixels])
            image.filepath_raw = str(ROOT / 'artifacts' / f'smooth-ruler-{VERSION}.png')
            image.file_format = 'PNG'
            image.save()
            bpy.data.images.remove(image)
    RESULT.write_text(json.dumps({'status': 'PASS', 'blender': bpy.app.version_string,
                                  'cases': cases}, indent=2), encoding='utf-8')
    print('SMOOTH RULER PASS', cases, flush=True)
    bpy.ops.wm.quit_blender()
    return None


def main():
    try:
        return run()
    except Exception:
        error = traceback.format_exc()
        RESULT.write_text(json.dumps({'status': 'FAIL', 'error': error}, indent=2), encoding='utf-8')
        print('SMOOTH RULER FAIL', error, flush=True)
        bpy.ops.wm.quit_blender()
        return None


bpy.app.timers.register(main, first_interval=1.0)
