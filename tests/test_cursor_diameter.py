"""Numerical acceptance for a section diameter attached to the cursor."""
import json
import math
import sys
from pathlib import Path

import bpy
import numpy as np
from mathutils import Matrix, Vector

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from final_dimensions.section import cursor_diameter, Geometry

cases = []


def contour(points):
    return {'segments': tuple(zip(points, points[1:] + points[:1])),
            'diameter': max(math.dist(a, b) for a in points for b in points),
            'radius': 0, 'endpoints': (points[0], points[1]), 'closed': True, 'warnings': ()}


def check(section, point, expected, normal=(0, 0, 1), tolerance=1e-7):
    measured = cursor_diameter(section, point, normal)
    assert abs(measured['diameter'] - expected) < tolerance, (measured['diameter'], expected)
    assert math.dist(measured['endpoints'][1], point) < tolerance
    assert abs(math.dist(*measured['endpoints']) - expected) < tolerance
    assert measured['radius'] == measured['diameter'] / 2
    ray = Vector(measured['endpoints'][0]) - Vector(measured['endpoints'][1])
    toward_center = Vector(measured['center']) - Vector(measured['endpoints'][1])
    assert ray.normalized().cross(toward_center.normalized()).length < 1e-5
    return measured


square = contour([(-2., -1., 0.), (2., -1., 0.), (2., 1., 0.), (-2., 1., 0.)])
check(square, (0, -1, 0), 2)
check(square, (2, 0, 0), 4)
check(square, (2, 1, 0), math.sqrt(20))
check(square, (1, -1, 0), math.sqrt(8))
assert square['diameter'] == math.sqrt(20)
cases.append('rectangle_short_long_diagonal_cursor_directions')

ellipse = contour([(3*math.cos(a), math.sin(a), 0.) for a in np.linspace(0, 2*math.pi, 128, endpoint=False)])
for index in (0, 5, 16, 32, 91):
    point = ellipse['segments'][index][0]
    check(ellipse, point, 2 * math.dist(point, (0,0,0)))
cases.append('ellipse_cursor_diameter_changes_with_angle')

# Same contour with uneven triangulation: center must be area-weighted.
subdivided = contour([(-2., -1., 0.), (-1.9, -1., 0.), (-1.8, -1., 0.),
                      (-1.7, -1., 0.), (2., -1., 0.), (2., 1., 0.), (-2., 1., 0.)])
check(subdivided, (0, -1, 0), 2)
cases.append('center_independent_of_boundary_tessellation')

rotation = Matrix.Rotation(.8, 3, Vector((1,2,3)).normalized())
translation = Vector((10, -7, 4))
rotated = contour([tuple(translation + rotation @ Vector(p)) for p, _ in square['segments']])
check(rotated, tuple(translation + rotation @ Vector((0,-1,0))), 2,
      rotation @ Vector((0,0,1)), tolerance=3e-6)
small = contour([tuple(v * 1e-6 for v in p) for p, _ in square['segments']])
check(small, (0, -1e-6, 0), 2e-6, tolerance=1e-12)
cases.append('world_transform_and_micro_scale')

# Concavity: ray should terminate at its first exit, not span the outside gap.
concave = contour([(0.,0.,0.), (4.,0.,0.), (4.,1.,0.), (1.,1.,0.),
                   (1.,3.,0.), (4.,3.,0.), (4.,4.,0.), (0.,4.,0.)])
value = cursor_diameter(concave, (0., 2., 0.), (0,0,1))
assert abs(value['diameter'] - 1) < 1e-9
cases.append('concave_first_exit_no_exterior_gap')
for bad in ({**square, 'closed': False}, {**square, 'segments': ()}):
    try:
        cursor_diameter(bad, (0,-1,0), (0,0,1))
    except ValueError:
        pass
    else:
        raise AssertionError('Invalid contour accepted')
cases.append('open_and_empty_contours_rejected')

# Real evaluated Blender geometry + exact ray hit, including modifier stack.
bpy.ops.object.select_all(action='SELECT')
bpy.ops.object.delete(use_global=False)
bpy.ops.mesh.primitive_cylinder_add(vertices=64, radius=1, depth=4)
obj = bpy.context.object
obj.scale.x = 2
taper = obj.modifiers.new('Taper', 'SIMPLE_DEFORM')
taper.deform_method = 'TAPER'
taper.deform_axis = 'Z'
taper.factor = .5
bpy.context.view_layer.update()
geometry = Geometry.from_object(bpy.context, obj)
for z in (-1., 0., 1.):
    results = []
    for origin, ray in (((0,-10,z), (0,1,0)), ((10,0,z), (-1,0,0))):
        hit = geometry.ray_cast(origin, ray)
        assert hit is not None
        section = geometry.section(hit['point'], (0,0,1), hit['triangle'])
        measured = cursor_diameter(section, hit['point'], (0,0,1))
        assert math.dist(measured['anchor'], hit['point']) < 1e-5
        results.append(measured['diameter'])
    assert abs(results[1] / results[0] - 2) < 1e-5, results
cases.append('evaluated_taper_ellipse_actual_rays')

obj.scale = (.02, .01, .01)
bpy.context.view_layer.update()
geometry = Geometry.from_object(bpy.context, obj)
hit = geometry.ray_cast((0, -1000, -.01), (0, 1, 0))
section = geometry.section(hit['point'], (0,0,1), hit['triangle'])
value = cursor_diameter(section, hit['point'], (0,0,1))
assert math.dist(value['anchor'], hit['point']) < 1e-7
cases.append('distant_orthographic_ray_on_small_part')

report = {'blender': bpy.app.version_string, 'status': 'PASS', 'cases': cases}
(ROOT / 'artifacts' / ('cursor-diameter-' + bpy.app.version_string.replace(' ', '-') + '.json')).write_text(
    json.dumps(report, indent=2), encoding='utf-8')
print('CURSOR DIAMETER PASS', len(cases))
