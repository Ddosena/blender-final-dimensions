"""Run with Blender --background --factory-startup --python tests/test_blender.py."""
import json
import math
import sys
import time
import traceback
from pathlib import Path

import bpy
import bmesh
from mathutils import Matrix

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import final_dimensions
from final_dimensions.measure import measure_object, format_length

REPORT = {"blender": bpy.app.version_string, "cases": []}


def close(actual, expected, tolerance=1e-5):
    assert actual is not None, f"Missing dimensions, expected {expected}"
    assert len(actual) == len(expected)
    assert all(abs(a - b) <= tolerance * max(1.0, abs(b)) for a, b in zip(actual, expected)), (actual, expected)


def clean():
    if bpy.context.object and bpy.context.object.mode != 'OBJECT':
        bpy.ops.object.mode_set(mode='OBJECT')
    bpy.ops.object.select_all(action='SELECT')
    bpy.ops.object.delete(use_global=False)


def cube(size=2, location=(0, 0, 0)):
    bpy.ops.mesh.primitive_cube_add(size=size, location=location)
    return bpy.context.object


def measure(obj):
    bpy.context.view_layer.update()
    return measure_object(bpy.context, obj)


def applied_reference(obj):
    """Independently bake modifiers with Blender operators on a disposable copy."""
    assert obj.mode == 'OBJECT'
    bpy.ops.object.select_all(action='DESELECT')
    duplicate = obj.copy()
    duplicate.data = obj.data.copy()
    bpy.context.collection.objects.link(duplicate)
    duplicate.select_set(True)
    bpy.context.view_layer.objects.active = duplicate
    for modifier in list(duplicate.modifiers):
        if modifier.show_viewport:
            bpy.ops.object.modifier_apply(modifier=modifier.name)
        else:
            duplicate.modifiers.remove(modifier)
    points = [duplicate.matrix_world @ vertex.co for vertex in duplicate.data.vertices]
    result = tuple(max(p[axis] for p in points) - min(p[axis] for p in points) for axis in range(3)) if points else None
    mesh = duplicate.data
    bpy.data.objects.remove(duplicate, do_unlink=True)
    bpy.data.meshes.remove(mesh)
    obj.select_set(True)
    bpy.context.view_layer.objects.active = obj
    return result


def plain_cube():
    obj = cube(.04)
    m = measure(obj)
    close(m['cage'], (.04,) * 3)
    close(m['final'], (.04,) * 3)
    close(m['difference'], (0,) * 3)
    close(m['percent'], (0,) * 3)
    close(m['half'], (.02,) * 3)
    return m


def subdiv():
    obj = cube(.04)
    mod = obj.modifiers.new('Subdivision', 'SUBSURF')
    mod.levels = 3
    m = measure(obj)
    close(m['cage'], (.04,) * 3)
    assert all(0 < x < .04 for x in m['final']), m
    close(m['final'], applied_reference(obj))
    assert all(x < 0 for x in m['difference'])
    return m


def bevel():
    obj = cube()
    mod = obj.modifiers.new('Bevel', 'BEVEL')
    mod.width = .2
    mod.segments = 4
    m = measure(obj)
    close(m['final'], applied_reference(obj))
    return m


def stack():
    obj = cube()
    bevel = obj.modifiers.new('Bevel', 'BEVEL')
    bevel.width = .2
    bevel.segments = 3
    sub = obj.modifiers.new('Subdivision', 'SUBSURF')
    sub.levels = 2
    array = obj.modifiers.new('Array', 'ARRAY')
    array.count = 3
    solid = obj.modifiers.new('Solidify', 'SOLIDIFY')
    solid.thickness = .15
    obj.scale = (1, 2, .5)
    m = measure(obj)
    close(m['final'], applied_reference(obj))
    return m


def unapplied_scale():
    obj = cube()
    obj.scale = (-2, 3, .5)
    m = measure(obj)
    close(m['cage'], (4, 6, 1))
    close(m['final'], (4, 6, 1))
    return m


def rotated_world_bounds():
    obj = cube()
    obj.rotation_euler.z = math.pi / 4
    obj.scale = (2, 1, 1)
    m = measure(obj)
    close(m['final'], (3 * math.sqrt(2), 3 * math.sqrt(2), 2))
    return m


def parent_shear():
    obj = cube()
    parent = bpy.data.objects.new('Parent', None)
    bpy.context.collection.objects.link(parent)
    parent.scale = (3, 1, .5)
    obj.parent = parent
    obj.rotation_euler.z = math.pi / 4
    m = measure(obj)
    close(m['final'], (6 * math.sqrt(2), 2 * math.sqrt(2), 1))
    return m


def flat_and_empty():
    bpy.ops.mesh.primitive_plane_add(size=2)
    obj = bpy.context.object
    m = measure(obj)
    close(m['final'], (2, 2, 0))
    assert m['percent'][2] is None
    mesh = bpy.data.meshes.new('Empty mesh')
    obj.data = mesh
    empty = measure(obj)
    assert empty['final'] is None, empty
    return {"plane": m, "empty": empty}


def edit_mode():
    obj = cube()
    obj.modifiers.new('Subdivision', 'SUBSURF').levels = 2
    baseline = measure(obj)
    bpy.ops.object.mode_set(mode='EDIT')
    bm = bmesh.from_edit_mesh(obj.data)
    for vertex in bm.verts:
        vertex.co.x *= 2
    bmesh.update_edit_mesh(obj.data, loop_triangles=False, destructive=False)
    edited = measure(obj)
    assert obj.mode == 'EDIT'
    close(edited['cage'], (4, 2, 2))
    close(edited['final'], (baseline['final'][0] * 2, baseline['final'][1], baseline['final'][2]))
    bpy.ops.object.mode_set(mode='OBJECT')
    close(edited['final'], applied_reference(obj))
    return edited


def edit_topology():
    obj = cube()
    bpy.ops.object.mode_set(mode='EDIT')
    bm = bmesh.from_edit_mesh(obj.data)
    bm.verts.new((5, 0, 0))
    bmesh.update_edit_mesh(obj.data, loop_triangles=True, destructive=True)
    m = measure(obj)
    close(m['cage'], (6, 2, 2))
    close(m['final'], (6, 2, 2))
    assert obj.mode == 'EDIT'
    return m


def modifier_changes():
    obj = cube()
    mod = obj.modifiers.new('Array', 'ARRAY')
    mod.count = 2
    close(measure(obj)['final'], (4, 2, 2))
    mod.count = 4
    close(measure(obj)['final'], (8, 2, 2))
    mod.show_viewport = False
    close(measure(obj)['final'], (2, 2, 2))
    obj.modifiers.remove(mod)
    close(measure(obj)['final'], (2, 2, 2))
    return {"add_parameter_disable_remove": "passed"}


def geometry_nodes(instances=False):
    obj = cube()
    group = bpy.data.node_groups.new('Test geometry', 'GeometryNodeTree')
    group.interface.new_socket(name='Geometry', in_out='INPUT', socket_type='NodeSocketGeometry')
    group.interface.new_socket(name='Geometry', in_out='OUTPUT', socket_type='NodeSocketGeometry')
    output = group.nodes.new('NodeGroupOutput')
    primitive = group.nodes.new('GeometryNodeMeshCube')
    primitive.inputs['Size'].default_value = (3, 4, 5)
    if instances:
        line = group.nodes.new('GeometryNodeMeshLine')
        line.inputs['Count'].default_value = 3
        line.inputs['Offset'].default_value = (10, 0, 0)
        instance = group.nodes.new('GeometryNodeInstanceOnPoints')
        group.links.new(line.outputs['Mesh'], instance.inputs['Points'])
        group.links.new(primitive.outputs['Mesh'], instance.inputs['Instance'])
        group.links.new(instance.outputs['Instances'], output.inputs['Geometry'])
    else:
        group.links.new(primitive.outputs['Mesh'], output.inputs['Geometry'])
    obj.modifiers.new('Geometry Nodes', 'NODES').node_group = group
    m = measure(obj)
    close(m['final'], (23, 4, 5) if instances else (3, 4, 5))
    return m


def boolean():
    obj = cube()
    cutter = cube(location=(1, 0, 0))
    bpy.context.view_layer.objects.active = obj
    mod = obj.modifiers.new('Boolean', 'BOOLEAN')
    mod.operation = 'DIFFERENCE'
    mod.object = cutter
    m = measure(obj)
    close(m['final'], (1, 2, 2))
    close(m['final'], applied_reference(obj))
    cutter.location.x = 1.5
    close(measure(obj)['final'], (1.5, 2, 2))
    return m


def mirror_displace():
    obj = cube(location=(3, 0, 0))
    for vertex in obj.data.vertices:
        vertex.co.x += 2
    obj.modifiers.new('Mirror', 'MIRROR')
    disp = obj.modifiers.new('Displace', 'DISPLACE')
    disp.strength = .25
    disp.direction = 'Z'
    m = measure(obj)
    close(m['final'], applied_reference(obj))
    return m


def units():
    scene = bpy.context.scene
    scene.unit_settings.system = 'METRIC'
    scene.unit_settings.scale_length = 1
    formatted = {}
    for unit, expected in [('MILLIMETERS', '40.000'), ('CENTIMETERS', '4.000'), ('METERS', '0.040')]:
        scene.unit_settings.length_unit = unit
        value = format_length(scene, .04)
        assert expected in value, (unit, value)
        formatted[unit] = value
    scene.unit_settings.scale_length = .001
    scene.unit_settings.length_unit = 'MILLIMETERS'
    value = format_length(scene, 40)
    assert '40.000' in value, value
    formatted['scale_0.001'] = value
    return formatted


def non_destructive():
    obj = cube()
    obj.modifiers.new('Subdivision', 'SUBSURF').levels = 3
    obj.scale = (2, 3, 4)
    bpy.context.view_layer.update()
    before = (len(bpy.data.objects), len(bpy.data.meshes), tuple(v.co[:] for v in obj.data.vertices), obj.matrix_world.copy(), len(obj.modifiers))
    for _ in range(8):
        measure(obj)
    after = (len(bpy.data.objects), len(bpy.data.meshes), tuple(v.co[:] for v in obj.data.vertices), obj.matrix_world.copy(), len(obj.modifiers))
    assert before == after
    return {"objects_meshes_vertices_transforms_modifiers_unchanged": True}


def register_cycle():
    final_dimensions.unregister()
    final_dimensions.register()
    final_dimensions.unregister()
    final_dimensions.register()
    return {"enable_disable_enable": "passed"}


final_dimensions.register()
for name, case in [
    ('cube_40mm', plain_cube), ('subdivision', subdiv), ('bevel', bevel),
    ('modifier_stack', stack), ('unapplied_negative_scale', unapplied_scale),
    ('rotation_world_axes', rotated_world_bounds), ('parent_nonuniform_scale_shear', parent_shear),
    ('zero_span_empty', flat_and_empty), ('edit_mode_live_vertices', edit_mode),
    ('edit_mode_topology', edit_topology), ('modifier_add_change_disable_remove', modifier_changes),
    ('geometry_nodes_mesh', geometry_nodes), ('geometry_nodes_instances', lambda: geometry_nodes(True)),
    ('boolean_operand_change', boolean), ('mirror_displace', mirror_displace),
    ('units', units), ('non_destructive_repeated_measurements', non_destructive),
    ('register_cleanup', register_cycle),
]:
    start = time.perf_counter()
    try:
        clean()
        details = case()
        REPORT['cases'].append({"name": name, "status": "PASS", "seconds": time.perf_counter() - start, "details": details})
        print('PASS', name, flush=True)
    except Exception:
        error = traceback.format_exc()
        REPORT['cases'].append({"name": name, "status": "FAIL", "error": error})
        print('FAIL', name, error, flush=True)

clean()
final_dimensions.unregister()
REPORT['passed'] = sum(c['status'] == 'PASS' for c in REPORT['cases'])
REPORT['failed'] = sum(c['status'] == 'FAIL' for c in REPORT['cases'])
output = ROOT / 'artifacts' / f'tests-{bpy.app.version_string.replace(" ", "-")}.json'
output.write_text(json.dumps(REPORT, ensure_ascii=False, indent=2), encoding='utf-8')
print('REPORT', output, REPORT['passed'], 'passed,', REPORT['failed'], 'failed', flush=True)
if REPORT['failed']:
    raise RuntimeError('Blender acceptance tests failed')
