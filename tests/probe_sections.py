"""Isolated feasibility probe; does not install or modify the addon.

Ray hits locate the section. Its normal is explicitly supplied, not inferred
from a mouse ray or assumed to follow arbitrary bent mesh topology.
"""
import json
import time
from pathlib import Path

import bpy
import numpy as np
from mathutils import Vector
from mathutils.bvhtree import BVHTree

ROOT = Path(__file__).resolve().parents[1]
obj = bpy.context.object
obj.scale = (.02, .02, .02)
obj.modifiers.new('Subdivision probe', 'SUBSURF').levels = 4
bpy.context.view_layer.update()
evaluated = obj.evaluated_get(bpy.context.evaluated_depsgraph_get())
mesh = evaluated.to_mesh()
try:
    mesh.calc_loop_triangles()
    vertices = [tuple(evaluated.matrix_world @ v.co) for v in mesh.vertices]
    triangles = [tuple(triangle.vertices) for triangle in mesh.loop_triangles]
finally:
    evaluated.to_mesh_clear()
tree = BVHTree.FromPolygons(vertices, triangles, all_triangles=True)
tri_points = np.asarray(vertices, dtype=np.float64)[np.asarray(triangles)]


def section(point, normal):
    normal = np.asarray(normal, dtype=np.float64)
    normal /= np.linalg.norm(normal)
    distances = (tri_points - np.asarray(point)) @ normal
    candidates = tri_points[(distances.min(axis=1) <= 1e-10) & (distances.max(axis=1) >= -1e-10)]
    points = []
    for triangle in candidates:
        d = (triangle - point) @ normal
        for a, b in ((0, 1), (1, 2), (2, 0)):
            if abs(d[a]) < 1e-10:
                points.append(triangle[a])
            if d[a] * d[b] < 0:
                points.append(triangle[a] + (triangle[b]-triangle[a]) * d[a]/(d[a]-d[b]))
    points = np.unique(np.round(np.asarray(points), 10), axis=0)
    # Tiny bounded prototype: exact diameter of a polygonal section is attained
    # at vertices. Production should use a convex hull for large contours.
    deltas = points[:, None, :] - points[None, :, :]
    squared = np.einsum('ijk,ijk->ij', deltas, deltas)
    i, j = np.unravel_index(np.argmax(squared), squared.shape)
    diameter = float(np.sqrt(squared[i, j]))
    return diameter, points[i].tolist(), points[j].tolist(), len(points)


samples = []
for x in (0, .008, .014):
    start = time.perf_counter()
    hit, normal, face, distance = tree.ray_cast(Vector((x, -.1, 0)), Vector((0, 1, 0)))
    assert hit is not None
    diameter, a, b, count = section(tuple(hit), (1, 0, 0))
    assert diameter > 0 and abs(a[0]-x) < 1e-7 and abs(b[0]-x) < 1e-7
    samples.append({'hit': list(hit), 'diameter_mm': diameter*1000,
                    'half_diameter_mm': diameter*500, 'endpoints': [a, b],
                    'section_points': count, 'query_ms': (time.perf_counter()-start)*1000})
assert samples[0]['diameter_mm'] > samples[1]['diameter_mm'] > samples[2]['diameter_mm']
report = {'status': 'PASS', 'blender': bpy.app.version_string, 'triangles': len(triangles),
          'plane_normal': [1, 0, 0], 'samples': samples,
          'limits': 'Synthetic closed mesh; explicit section orientation; no interactive hover implementation or arbitrary-bend inference'}
(ROOT/'artifacts'/'section-feasibility.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
print(json.dumps(report), flush=True)
