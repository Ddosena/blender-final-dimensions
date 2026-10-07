"""Run with Blender --background --factory-startup --python tests/test_vertex_tracking.py."""
import math
import sys
from pathlib import Path
from types import SimpleNamespace

import bmesh
import bpy
import numpy as np
from mathutils import Matrix

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from final_dimensions.snapping import SnapPicker, _projection
from final_dimensions.vertex_tracking import ATTRIBUTE


REGION = SimpleNamespace(width=1000, height=1000)
VIEW = SimpleNamespace(
    perspective_matrix=Matrix(((.2, 0, 0, 0), (0, .2, 0, 0),
                               (0, 0, -.05, 0), (0, 0, 0, 1))),
    view_matrix=Matrix.Translation((0, 0, -10)), is_perspective=False)
EPOCH = 0


def close(actual, expected, tolerance=1e-5):
    assert np.allclose(actual, expected, atol=tolerance, rtol=0), (actual, expected)


def refresh(picker):
    global EPOCH
    EPOCH += 1
    bpy.context.view_layer.update()
    picker.refresh(bpy.context, EPOCH)


def hit(picker, point, source='ORIGINAL'):
    matrix = np.asarray(VIEW.perspective_matrix)
    clip = matrix @ np.array((*point, 1.))
    inverse = np.linalg.inv(matrix)
    xy = clip[:2] / clip[3]
    near = inverse @ np.array((*xy, -1, 1.))
    far = inverse @ np.array((*xy, 1, 1.))
    origin, end = near[:3]/near[3], far[:3]/far[3]
    pixel = _projection(np.array((point,)), matrix, REGION)[1][0]
    result = picker.snap(bpy.context, origin, end-origin, REGION, VIEW, pixel,
                         {'VERTEX'}, source)
    assert result and result['snap_kind'] == 'VERTEX', (point, result)
    return result


def test_inset_and_mode_cycles():
    bpy.ops.object.select_all(action='SELECT')
    bpy.ops.object.delete(use_global=False)
    bpy.ops.mesh.primitive_plane_add(size=2)
    obj = bpy.context.object
    original = [tuple(v.co) for v in obj.data.vertices]
    p = SnapPicker(bpy.context, EPOCH)
    a = hit(p, original[0])['anchor']
    b = hit(p, original[1])['anchor']
    assert a['vertex_id'] != b['vertex_id']
    assert ATTRIBUTE in obj.data.attributes
    assert [tuple(v.co) for v in obj.data.vertices] == original
    bpy.ops.object.mode_set(mode='EDIT')
    refresh(p)  # A real ruler receives the mode-change depsgraph update.
    bm = bmesh.from_edit_mesh(obj.data)
    bm.verts.ensure_lookup_table()
    outer = bm.verts[0]
    other = bm.verts[1]
    bpy.ops.mesh.inset(thickness=.25, depth=0)
    refresh(p)
    assert outer.is_valid and other.is_valid
    close(p.resolve(bpy.context, a), original[0])
    close(p.resolve(bpy.context, b), original[1])
    inner = [v for v in bm.verts if v not in {outer, other}
             and abs(v.co.x) < 1 and abs(v.co.y) < 1]
    assert len(inner) == 4, len(inner)
    inner_vert = inner[0]
    inner_point = tuple(inner_vert.co)
    inner_anchor = hit(p, inner_point)['anchor']
    assert inner_anchor['vertex_id'] not in {a['vertex_id'], b['vertex_id']}
    outer.co.x -= .5
    inner_vert.co.x += .1
    bmesh.update_edit_mesh(obj.data)
    refresh(p)
    close(p.resolve(bpy.context, a), tuple(outer.co))
    close(p.resolve(bpy.context, inner_anchor), tuple(inner_vert.co))
    distance = math.dist(p.resolve(bpy.context, a), p.resolve(bpy.context, b))
    assert not math.isclose(distance, math.dist(original[0], original[1]))
    print('PASS inset surviving and new vertices follow independently', flush=True)

    # A second Inset copies POINT attributes again. The original BMesh
    # references identify exactly which copies retain each ruler ID.
    for face in bm.faces:
        face.select_set(False)
    selected = next(face for face in bm.faces if inner_vert in face.verts and
                    all(abs(v.co.x) < 1.1 and abs(v.co.y) < 1.1 for v in face.verts))
    selected.select_set(True)
    bmesh.update_edit_mesh(obj.data)
    bpy.ops.mesh.inset(thickness=.08, depth=0)
    refresh(p)
    close(p.resolve(bpy.context, a), tuple(outer.co))
    if inner_vert.is_valid:
        close(p.resolve(bpy.context, inner_anchor), tuple(inner_vert.co))
    expected_outer, expected_other = tuple(outer.co), tuple(other.co)
    bpy.ops.object.mode_set(mode='OBJECT')
    refresh(p)
    close(p.resolve(bpy.context, a), expected_outer)
    close(p.resolve(bpy.context, b), expected_other)
    print('PASS repeated inset and Edit/Object transition', flush=True)

    bpy.ops.object.mode_set(mode='EDIT')
    refresh(p)
    bm = bmesh.from_edit_mesh(obj.data)
    # Deleting an unrelated vertex changes later BMesh indices. IDs prevent
    # silently following the vertex now occupying an old index.
    layer = bm.verts.layers.int.get(a['vertex_layer'])
    bm.verts.ensure_lookup_table()
    unrelated = next(v for v in bm.verts if int(v[layer]) not in
                     {a['vertex_id'], b['vertex_id'], inner_anchor['vertex_id']})
    bmesh.ops.delete(bm, geom=[unrelated], context='VERTS')
    bmesh.update_edit_mesh(obj.data, destructive=True)
    refresh(p)
    assert p.resolve(bpy.context, a) is not None
    assert p.resolve(bpy.context, b) is not None
    print('PASS unrelated deletion does not rebind anchors', flush=True)

    target = next(v for v in bm.verts if int(v[layer]) == a['vertex_id'])
    bmesh.ops.delete(bm, geom=[target], context='VERTS')
    bmesh.update_edit_mesh(obj.data, destructive=True)
    refresh(p)
    try:
        p.resolve(bpy.context, a)
    except ValueError as exc:
        assert 'deleted' in str(exc)
    else:
        raise AssertionError('Deleted vertex remained anchored')
    assert p.resolve(bpy.context, b) is not None
    print('PASS deleted vertex invalidates only its own endpoint', flush=True)


def test_unobserved_duplicate_fails_safely():
    bpy.ops.object.mode_set(mode='OBJECT')
    bpy.ops.object.select_all(action='SELECT')
    bpy.ops.object.delete(use_global=False)
    bpy.ops.mesh.primitive_plane_add(size=2)
    obj = bpy.context.object
    p = SnapPicker(bpy.context, EPOCH)
    anchor = hit(p, tuple(obj.data.vertices[0].co))['anchor']
    bpy.ops.object.mode_set(mode='EDIT')
    bm = bmesh.from_edit_mesh(obj.data)
    bm.verts.ensure_lookup_table()
    copy = bm.verts.new(bm.verts[0].co)
    layer = bm.verts.layers.int.get(anchor['vertex_layer'])
    copy[layer] = anchor['vertex_id']
    bmesh.update_edit_mesh(obj.data, destructive=True)
    bpy.ops.object.mode_set(mode='OBJECT')  # No edit refresh could record identity.
    refresh(p)
    try:
        p.resolve(bpy.context, anchor)
    except ValueError as exc:
        assert 'ambiguous' in str(exc)
    else:
        raise AssertionError('Duplicate vertex ID silently rebound')
    print('PASS unobserved duplicate invalidates instead of guessing', flush=True)


def test_deleted_original_with_surviving_copy():
    bpy.ops.object.select_all(action='SELECT')
    bpy.ops.object.delete(use_global=False)
    bpy.ops.mesh.primitive_plane_add(size=2)
    obj = bpy.context.object
    p = SnapPicker(bpy.context, EPOCH)
    anchor = hit(p, tuple(obj.data.vertices[0].co))['anchor']
    bpy.ops.object.mode_set(mode='EDIT')
    refresh(p)
    bm = bmesh.from_edit_mesh(obj.data)
    bm.verts.ensure_lookup_table()
    original = next(v for v in bm.verts if v.index == anchor['feature'])
    layer = bm.verts.layers.int.get(anchor['vertex_layer'])
    copy = bm.verts.new(original.co)
    copy[layer] = anchor['vertex_id']
    bmesh.ops.delete(bm, geom=[original], context='VERTS')
    bmesh.update_edit_mesh(obj.data, destructive=True)
    refresh(p)
    try:
        p.resolve(bpy.context, anchor)
    except ValueError as exc:
        assert 'deleted' in str(exc)
    else:
        raise AssertionError('Anchor jumped from deleted original to its copy')
    bm.verts.ensure_lookup_table()
    copied_index = next(i for i, v in enumerate(bm.verts) if v is copy)
    new_anchor = hit(p, tuple(copy.co))['anchor']
    assert new_anchor['feature'] == copied_index
    assert new_anchor['vertex_id'] != anchor['vertex_id']
    print('PASS deletion and copied ID never rebind old anchor', flush=True)


def test_native_edit_transforms():
    bpy.ops.object.mode_set(mode='OBJECT')
    bpy.ops.object.select_all(action='SELECT')
    bpy.ops.object.delete(use_global=False)
    bpy.ops.mesh.primitive_plane_add(size=2)
    obj = bpy.context.object
    p = SnapPicker(bpy.context, EPOCH)
    original = hit(p, tuple(obj.data.vertices[0].co))['anchor']
    bpy.ops.object.mode_set(mode='EDIT')
    refresh(p)
    bpy.ops.mesh.inset(thickness=.25, depth=0)
    refresh(p)
    bm = bmesh.from_edit_mesh(obj.data)
    inner = next(v for v in bm.verts if abs(v.co.x) < 1 and abs(v.co.y) < 1)
    inner_anchor = hit(p, tuple(inner.co))['anchor']
    before_inner = p.resolve(bpy.context, inner_anchor)
    before_outer = p.resolve(bpy.context, original)
    for face in bm.faces:
        face.select_set(False)
    for edge in bm.edges:
        edge.select_set(False)
    for vert in bm.verts:
        vert.select_set(False)
    inner.select_set(True)
    bmesh.update_edit_mesh(obj.data)
    assert bpy.ops.transform.translate(value=(.2, 0, 0)) == {'FINISHED'}
    refresh(p)
    moved = p.resolve(bpy.context, inner_anchor)
    close(moved, (before_inner[0]+.2, before_inner[1], before_inner[2]))
    close(p.resolve(bpy.context, original), before_outer)
    for vert in bm.verts:
        vert.select_set(abs(vert.co.x) < 1 and abs(vert.co.y) < 1)
    bmesh.update_edit_mesh(obj.data)
    assert bpy.ops.transform.resize(value=(1.25, 1.25, 1.25)) == {'FINISHED'}
    refresh(p)
    close(p.resolve(bpy.context, original), before_outer)
    assert not np.allclose(p.resolve(bpy.context, inner_anchor), moved)
    print('PASS native translate and scale preserve vertex anchors', flush=True)


def test_inset_undo():
    bpy.ops.object.mode_set(mode='OBJECT')
    bpy.ops.object.select_all(action='SELECT')
    bpy.ops.object.delete(use_global=False)
    bpy.ops.mesh.primitive_plane_add(size=2)
    obj = bpy.context.object
    p = SnapPicker(bpy.context, EPOCH)
    initial = tuple(obj.data.vertices[0].co)
    anchor = hit(p, initial)['anchor']
    bpy.ops.object.mode_set(mode='EDIT')
    refresh(p)
    bpy.ops.ed.undo_push(message='before inset')
    assert bpy.ops.mesh.inset(thickness=.2, depth=0) == {'FINISHED'}
    refresh(p)
    close(p.resolve(bpy.context, anchor), initial)
    assert bpy.ops.ed.undo() == {'FINISHED'}
    refresh(p)
    close(p.resolve(bpy.context, anchor), initial)
    print('PASS undo of inset keeps surviving anchor', flush=True)


def test_final_vertex_survives_tessellation_flip():
    bpy.ops.object.mode_set(mode='OBJECT')
    bpy.ops.object.select_all(action='SELECT')
    bpy.ops.object.delete(use_global=False)
    data = bpy.data.meshes.new('deforming quad')
    data.from_pydata([(0,0,0), (2,0,0), (2,2,0), (0,2,0)], [], [(0,1,2,3)])
    data.update()
    obj = bpy.data.objects.new('deforming quad', data)
    bpy.context.collection.objects.link(obj)
    bpy.context.view_layer.update()
    p = SnapPicker(bpy.context, EPOCH)
    anchor = hit(p, (2,2,0), 'FINAL')['anchor']
    before = p._feature_geometry(bpy.context, p.records[0], 'FINAL')
    data.vertices[2].co = (-1,2,0)
    data.update()
    refresh(p)
    after = p._feature_geometry(bpy.context, p.records[0], 'FINAL')
    assert before.feature_topology != after.feature_topology, 'Quad tessellation did not flip'
    assert before.vertex_topology == after.vertex_topology
    close(p.resolve(bpy.context, anchor), (-1,2,0))
    print('PASS Final vertex follows deformation through triangulation flip', flush=True)


test_inset_and_mode_cycles()
test_unobserved_duplicate_fails_safely()
test_deleted_original_with_surviving_copy()
test_native_edit_transforms()
test_inset_undo()
test_final_vertex_survives_tessellation_flip()
print('VERTEX TRACKING PASS', bpy.app.version_string, flush=True)
