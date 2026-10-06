"""Additional edge cases for Geometry Nodes and handler invalidation."""
import json
import sys
from pathlib import Path
import bpy

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import final_dimensions as addon

addon.register()
obj = bpy.context.object
group = bpy.data.node_groups.new('Nested mesh instances', 'GeometryNodeTree')
group.interface.new_socket(name='Geometry', in_out='OUTPUT', socket_type='NodeSocketGeometry')
nodes, links = group.nodes, group.links
cube = nodes.new('GeometryNodeMeshCube')
cube.inputs['Size'].default_value = (2, 4, 6)
line = nodes.new('GeometryNodeMeshLine')
line.inputs['Count'].default_value = 2
line.inputs['Offset'].default_value = (10, 0, 0)
first = nodes.new('GeometryNodeInstanceOnPoints')
links.new(line.outputs['Mesh'], first.inputs['Points'])
links.new(cube.outputs['Mesh'], first.inputs['Instance'])
line2 = nodes.new('GeometryNodeMeshLine')
line2.inputs['Count'].default_value = 2
line2.inputs['Offset'].default_value = (0, 20, 0)
second = nodes.new('GeometryNodeInstanceOnPoints')
links.new(line2.outputs['Mesh'], second.inputs['Points'])
links.new(first.outputs['Instances'], second.inputs['Instance'])
output = nodes.new('NodeGroupOutput')
links.new(second.outputs['Instances'], output.inputs['Geometry'])
obj.modifiers.new('GN', 'NODES').node_group = group
bpy.context.view_layer.update()
measurement = addon.measure_object(bpy.context, obj)
assert measurement['final'] == (12, 24, 6), measurement

join = nodes.new('GeometryNodeJoinGeometry')
curve = nodes.new('GeometryNodeCurvePrimitiveLine')
curve.inputs['End'].default_value = (100, 100, 100)
links.new(cube.outputs['Mesh'], join.inputs['Geometry'])
links.new(curve.outputs['Curve'], join.inputs['Geometry'])
links.new(join.outputs['Geometry'], output.inputs['Geometry'])
bpy.context.view_layer.update()
mixed = addon.measure_object(bpy.context, obj)
assert mixed['warnings'], 'Mixed curve and mesh must not be silently labelled a complete final bound'

before = addon._epoch
cube.inputs['Size'].default_value = (3, 5, 7)
bpy.context.view_layer.update()
after = addon._epoch
assert after > before, f'Depsgraph invalidation failed: {before} -> {after}'
updated = addon.measure_object(bpy.context, obj)
assert updated['final'] == (3, 5, 7), updated

addon.unregister()
report = {'blender': bpy.app.version_string, 'status': 'PASS', 'nested_instances': measurement,
          'mixed_nonmesh_warning': mixed, 'depsgraph_epoch_before': before, 'depsgraph_epoch_after': after}
(ROOT / 'artifacts' / ('extra-' + bpy.app.version_string.replace(' ', '-') + '.json')).write_text(json.dumps(report, indent=2), encoding='utf-8')
print('EXTRA TESTS PASS')
