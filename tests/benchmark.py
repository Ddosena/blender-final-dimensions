"""Bounded real-Blender measurements, not a general performance claim."""
import json
import sys
import time
from pathlib import Path
import bpy

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from final_dimensions.measure import measure_object

obj = bpy.context.object
modifier = obj.modifiers.new('Subdivision', 'SUBSURF')
modifier.levels = 8
t0 = time.perf_counter()
bpy.context.view_layer.update()
eval_ms = (time.perf_counter() - t0) * 1000
dg = bpy.context.evaluated_depsgraph_get()
evaluated = obj.evaluated_get(dg)
mesh = evaluated.to_mesh()
vertices = len(mesh.vertices)
evaluated.to_mesh_clear()
times = []
for _ in range(3):
    t0 = time.perf_counter()
    result = measure_object(bpy.context, obj)
    times.append((time.perf_counter() - t0) * 1000)
report = {'blender': bpy.app.version_string, 'vertices': vertices, 'subdivision_level': 8,
          'initial_blender_evaluation_ms': eval_ms, 'measurement_ms': times,
          'final': result['final'], 'note': 'Local CPU, evaluated graph already warm; not a latency guarantee'}
(ROOT / 'artifacts' / 'benchmark.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
print(json.dumps(report))
