"""Run with Blender --background --factory-startup --python tests/test_offset_cut.py."""
import math
import sys
from pathlib import Path

import bmesh
import bpy
from mathutils import Matrix, Vector

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from final_dimensions import offset_cut


offset_cut.register()


def cube(size, scale=1.):
    if bpy.context.object and bpy.context.object.mode == 'EDIT':
        bpy.ops.object.mode_set(mode='OBJECT')
    bpy.ops.object.select_all(action='SELECT')
    bpy.ops.object.delete(use_global=False)
    bpy.ops.mesh.primitive_cube_add(size=size)
    obj = bpy.context.object
    bpy.context.scene.unit_settings.system = 'METRIC'
    bpy.context.scene.unit_settings.scale_length = scale
    bpy.ops.object.mode_set(mode='EDIT')
    bm = bmesh.from_edit_mesh(obj.data)
    bm.faces.index_update()
    top = max(bm.faces, key=lambda face: face.calc_center_median().z)
    return obj, top.index


def data_signature(obj):
    bm = bmesh.from_edit_mesh(obj.data)
    return (len(bm.verts), len(bm.edges), len(bm.faces),
            sorted(tuple(round(float(c), 8) for c in vert.co) for vert in bm.verts))


def check_scale(size, scale, expected_z):
    obj, index = cube(size, scale)
    original = data_signature(obj)
    plan = offset_cut._plan(bpy.context, obj, index, '2 mm', False)
    assert math.isclose(plan['plane_co'].z, expected_z, abs_tol=1e-6), plan['plane_co']
    assert len(plan['segments']) == 4, len(plan['segments'])
    assert data_signature(obj) == original, 'Preview changed the mesh'
    offset_cut._apply(bpy.context, obj, plan)
    bm = bmesh.from_edit_mesh(obj.data)
    assert len(bm.verts) == original[0] + 4
    assert all(any((vert.co-Vector(co)).length < 1e-7 for vert in bm.verts)
               for co in original[3]), 'An original vertex moved'
    assert sum(abs(vert.co.z-expected_z) < 1e-6 for vert in bm.verts) == 4
    print('PASS exact 2 mm at scene scale', scale, flush=True)


check_scale(10, .001, 3.)
check_scale(.02, 1., .008)
assert math.isclose(offset_cut.parse_distance(bpy.context.scene, '0,2 cm'), .002)
assert math.isclose(offset_cut.parse_distance(bpy.context.scene, '0.002 m'), .002)
for invalid in ('2', '-2 mm', '0 mm', 'foo', '2 km'):
    try:
        offset_cut.parse_distance(bpy.context.scene, invalid)
    except ValueError:
        pass
    else:
        raise AssertionError(f'Invalid distance accepted: {invalid}')
print('PASS explicit units and invalid distance rejection', flush=True)

obj, index = cube(4, .001)
obj.matrix_world = Matrix.Translation((2, 3, 4)) @ Matrix.Rotation(.37, 4, 'Y') @ Matrix.Diagonal((2, 1.5, .5, 1))
plan = offset_cut._plan(bpy.context, obj, index, '2 mm', False)
bm = bmesh.from_edit_mesh(obj.data)
face = bm.faces[index]
top_world = obj.matrix_world @ face.calc_center_median()
normal_world = (obj.matrix_world.inverted().transposed().to_3x3() @ face.normal).normalized()
cut_world = obj.matrix_world @ plan['plane_co']
assert math.isclose((top_world-cut_world).dot(normal_world), 2., abs_tol=1e-6)
offset_cut._apply(bpy.context, obj, plan)
assert len(bm.verts) == 12
print('PASS rotated nonuniform scale maintains world perpendicular gap', flush=True)

obj, index = cube(10, .001)
obj.matrix_world = Matrix.Translation((-3, 2, 1)) @ Matrix.Rotation(.4, 4, 'X') @ Matrix.Diagonal((-2, 1, .5, 1))
plan = offset_cut._plan(bpy.context, obj, index, '2 mm', False)
bm = bmesh.from_edit_mesh(obj.data)
face = bm.faces[index]
top_world = obj.matrix_world @ face.calc_center_median()
normal_world = (obj.matrix_world.inverted().transposed().to_3x3() @ face.normal).normalized()
cut_world = obj.matrix_world @ plan['plane_co']
assert math.isclose((top_world-cut_world).dot(normal_world), 2., abs_tol=1e-6)
offset_cut._apply(bpy.context, obj, plan)
print('PASS mirrored nonuniform object scale keeps signed physical offset', flush=True)

# The reference may be the broad planar face of an applied bevel. Its own
# coordinates and outer silhouette stay fixed while the crossing is inserted.
obj, _index = cube(.02, 1.)
bpy.ops.object.mode_set(mode='OBJECT')
bevel = obj.modifiers.new('bevel', 'BEVEL')
bevel.width, bevel.segments = .001, 3
bpy.ops.object.modifier_apply(modifier=bevel.name)
bpy.ops.object.mode_set(mode='EDIT')
bm = bmesh.from_edit_mesh(obj.data)
bm.faces.index_update()
top = max((face for face in bm.faces if face.normal.z > .999),
          key=lambda face: face.calc_center_median().z)
before = data_signature(obj)
plan = offset_cut._plan(bpy.context, obj, top.index, '2 mm', False)
assert len(plan['segments']) >= 4
assert data_signature(obj) == before
offset_cut._apply(bpy.context, obj, plan)
assert all(any((vert.co-Vector(co)).length < 1e-7 for vert in bm.verts)
           for co in before[3])
print('PASS applied bevel top reference and exterior preserved', flush=True)

# Test island scope using two disconnected cubes in one datablock.
obj, _index = cube(4, 1.)
bm = bmesh.from_edit_mesh(obj.data)
second = bmesh.ops.create_cube(bm, size=4)
bmesh.ops.translate(bm, verts=second['verts'], vec=(10,0,0))
bmesh.update_edit_mesh(obj.data, destructive=True)
bm.faces.index_update()
first_top = max((face for face in bm.faces if face.calc_center_median().x < 5),
                key=lambda face: face.calc_center_median().z)
second_before = sorted(tuple(vert.co) for vert in second['verts'])
plan = offset_cut._plan(bpy.context, obj, first_top.index, '2 m', False)
offset_cut._apply(bpy.context, obj, plan)
assert sorted(tuple(vert.co) for vert in second['verts']) == second_before
assert len(bm.verts) == 20
print('PASS disconnected island remains untouched', flush=True)

# A cut plane can pass through an existing vertex on one cube edge. Its preview
# must include the two on-plane segments ending at that old vertex.
obj, _index = cube(10, .001)
bm = bmesh.from_edit_mesh(obj.data)
vertical = next(edge for edge in bm.edges
                if abs(edge.verts[0].co.z-edge.verts[1].co.z) > 9
                and all(abs(vert.co.x-5) < 1e-5 and abs(vert.co.y-5) < 1e-5
                        for vert in edge.verts))
bmesh.ops.subdivide_edges(bm, edges=[vertical], cuts=1, use_grid_fill=False)
bmesh.ops.triangulate(bm, faces=[face for face in bm.faces if abs(face.normal.z) < .5])
bmesh.update_edit_mesh(obj.data, destructive=True)
bm.faces.index_update()
top = max(bm.faces, key=lambda face: face.calc_center_median().z)
before = data_signature(obj)
plan = offset_cut._plan(bpy.context, obj, top.index, '5 mm', False)
assert len(plan['segments']) >= 4, len(plan['segments'])
assert any(any(abs(point[0]-5) < 1e-5 and abs(point[1]-5) < 1e-5 and abs(point[2]) < 1e-5
                   for point in segment) for segment in plan['segments'])
assert data_signature(obj) == before
offset_cut._apply(bpy.context, obj, plan)
print('PASS preview and cut pass through an existing edge vertex and side triangles', flush=True)

obj, index = cube(10, .001)
bm = bmesh.from_edit_mesh(obj.data)
top_corner = next(vert for vert in bm.verts if all(abs(c-5) < 1e-5 for c in vert.co))
wire_end = bm.verts.new((5, 5, 0))
bm.edges.new((top_corner, wire_end))
bmesh.update_edit_mesh(obj.data, destructive=True)
before = data_signature(obj)
try:
    offset_cut._plan(bpy.context, obj, index, '2 mm', False)
except ValueError as exc:
    assert 'loose or non-manifold' in str(exc)
else:
    raise AssertionError('Attached wire was silently excluded from the island')
assert data_signature(obj) == before
print('PASS attached wire is rejected before mutation', flush=True)

obj, index = cube(4, 1.)
bm = bmesh.from_edit_mesh(obj.data)
bm.faces.ensure_lookup_table()
face = bm.faces[index]
face.verts[0].co.z += .3
bmesh.update_edit_mesh(obj.data)
try:
    offset_cut._plan(bpy.context, obj, index, '2 mm', False)
except ValueError as exc:
    assert 'planar' in str(exc)
else:
    raise AssertionError('Nonplanar reference accepted')
print('PASS nonplanar reference rejected', flush=True)

obj, index = cube(10, .001)
before = data_signature(obj)
bpy.context.window_manager.final_dimensions_offset_distance = '2 mm'
assert bpy.ops.view3d.final_dimensions_offset_cut(face_index=index) == {'FINISHED'}
assert data_signature(obj) != before
print('PASS direct operator commits cut', flush=True)

offset_cut.unregister()
print('OFFSET CUT PASS', bpy.app.version_string, flush=True)
