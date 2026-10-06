"""Directional dimensions: independent brute-force reference and edge selection."""
import json
import math
import sys
from pathlib import Path
import bpy
from mathutils import Vector

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from final_dimensions.measure import measure_direction

obj = bpy.context.object
cases = []


def close(a, b):
    assert a is not None and abs(a-b) < 2e-5, (a, b)


def reference(direction):
    u = Vector(direction).normalized()
    bpy.context.view_layer.update()
    ev = obj.evaluated_get(bpy.context.evaluated_depsgraph_get())
    mesh = ev.to_mesh()
    try:
        projections = [(ev.matrix_world @ v.co).dot(u) for v in mesh.vertices]
        return max(projections)-min(projections)
    finally:
        ev.to_mesh_clear()


def check(name, direction, expected=None):
    bpy.context.view_layer.update()
    result = measure_direction(bpy.context, obj, direction)
    close(result['span'], reference(direction) if expected is None else expected)
    cases.append({'name': name, 'status': 'PASS', 'span': result['span']})


check('axis', (1, 0, 0), 2)
check('diagonal', (1, 1, 0), 2*math.sqrt(2))
check('reverse_direction', (-1, -1, 0), 2*math.sqrt(2))
obj.scale = (-2, 3, .5)
obj.rotation_euler = (.3, .5, .7)
obj.location = (7, -3, 20)
check('arbitrary_rotation_scale_translation', (1, 2, 3))
mod = obj.modifiers.new('Subdivision', 'SUBSURF')
mod.levels = 3
check('subdivision_oblique_projection', (1, 2, 3))
# A transformed object's local edge direction should follow world matrix,
# not the nearest global coordinate axis.
direction = obj.matrix_world.to_3x3() @ Vector((1, 0, 0))
check('transformed_edge_orientation', direction)
bpy.ops.object.mode_set(mode='EDIT')
check('edit_mode_evaluated', direction)
assert measure_direction(bpy.context, obj, (0, 0, 0))['span'] is None
report = {'blender': bpy.app.version_string, 'status': 'PASS', 'cases': cases}
(ROOT/'artifacts'/f'direction-{bpy.app.version_string.replace(" ", "-")}.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
print('DIRECTION PASS', len(cases))
