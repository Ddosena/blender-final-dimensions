"""Headless native Edit Mode lineage, fallback, and ruler-fork checks."""
import math
import sys
from pathlib import Path
from types import SimpleNamespace

import bmesh
import bpy
import numpy as np
from mathutils import Matrix

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from final_dimensions import ruler, vertex_tracking
from final_dimensions.snapping import SnapPicker, _projection


REGION = SimpleNamespace(width=1000, height=1000)
VIEW = SimpleNamespace(
    perspective_matrix=Matrix(((.2, 0, 0, 0), (0, .2, 0, 0),
                               (0, 0, -.05, 0), (0, 0, 0, 1))),
    view_matrix=Matrix.Translation((0, 0, -10)), is_perspective=False)


def reset(scale=(1., 1., 1.)):
    if bpy.context.object and bpy.context.object.mode != 'OBJECT':
        bpy.ops.object.mode_set(mode='OBJECT')
    bpy.ops.object.select_all(action='SELECT')
    bpy.ops.object.delete(use_global=False)
    bpy.ops.mesh.primitive_plane_add(size=2)
    obj = bpy.context.object
    obj.scale = scale
    bpy.context.view_layer.update()
    return obj


def hit(picker, point):
    projection = np.asarray(VIEW.perspective_matrix)
    xy = _projection(np.array((point,)), projection, REGION)[1][0]
    result = picker.snap(bpy.context, np.array((point[0], point[1], 20.)),
                         np.array((0., 0., -1.)), REGION, VIEW, xy,
                         {'VERTEX'}, 'ORIGINAL')
    assert result and result['snap_kind'] == 'VERTEX', result
    return result


def setup(scale=(1., 1., 1.), mixed_world=False, two_rulers=False):
    obj = reset(scale)
    manager = ruler.RulerCollection(bpy.context)
    manager.picker = SnapPicker(bpy.context, 0)
    coords = [tuple(obj.matrix_world @ vert.co) for vert in obj.data.vertices]
    first = hit(manager.picker, coords[0])
    second = ruler._world_hit((0., 0., 0.)) if mixed_world else hit(manager.picker, coords[1])
    item = ruler.RulerState(bpy.context)
    item.first, item.second = first, second
    item.label = 'Measured edge'
    item.precision = 5
    item.trim_zeros = False
    item.color = (.25, .5, .75, 1.)
    manager.items.append(item)
    if two_rulers:
        another = ruler.RulerState(bpy.context)
        another.first = dict(first, anchor=dict(first['anchor']))
        another.second = hit(manager.picker, coords[2])
        another.label = 'Shared corner'
        manager.items.append(another)
    manager.active = 0
    manager.refresh(bpy.context, 1)
    bpy.ops.object.mode_set(mode='EDIT')
    manager.refresh(bpy.context, 2)
    return obj, manager


def check_scalar(value):
    if isinstance(value, (str, int, float, bool, type(None))):
        return True
    if isinstance(value, (tuple, list)):
        return all(check_scalar(part) for part in value)
    if isinstance(value, dict):
        return all(isinstance(key, str) and check_scalar(part)
                   for key, part in value.items())
    return False


def test_inset():
    obj, manager = setup(scale=(2., .5, 1.))
    assert bpy.ops.mesh.inset(thickness=.25, depth=0) == {'FINISHED'}
    manager.refresh(bpy.context, 3)
    assert len(manager.items) == 2
    parent, child = manager.items
    assert manager.active == 1
    assert math.isclose(parent.distance, 4., abs_tol=1e-5)
    assert math.isclose(child.distance, 3., abs_tol=1e-5)
    assert child.label == 'Measured edge (new)'
    assert child.precision == parent.precision == 5
    assert child.trim_zeros is parent.trim_zeros is False
    assert child.color == parent.color
    assert all(check_scalar(hit['anchor']) for item in manager.items
               for hit in (item.first, item.second))
    manager.refresh(bpy.context, 3)
    manager.refresh(bpy.context, 4)
    assert len(manager.items) == 2, 'Unchanged topology forked repeatedly'
    # Move only the new inner corner: the child distance updates, old ruler stays.
    bm = bmesh.from_edit_mesh(obj.data)
    layer = bm.verts.layers.int.get(child.first['anchor']['vertex_layer'])
    moving = next(v for v in bm.verts if int(v[layer]) == child.first['anchor']['vertex_id'])
    moving.co.x -= .5
    bmesh.update_edit_mesh(obj.data)
    del moving, layer, bm
    manager.refresh(bpy.context, 5)
    assert math.isclose(parent.distance, 4., abs_tol=1e-5)
    assert math.isclose(child.distance, 4., abs_tol=1e-5)
    bpy.ops.object.mode_set(mode='OBJECT')
    manager.refresh(bpy.context, 6)
    assert len(manager.items) == 2
    assert math.isclose(child.distance, 4., abs_tol=1e-5)
    print('PASS Inset forks once; nonuniform world distance and child edits follow', flush=True)


def test_extrude_and_duplicate():
    for operation in ('extrude', 'duplicate'):
        obj, manager = setup()
        if operation == 'extrude':
            assert bpy.ops.mesh.extrude_region() == {'FINISHED'}
        else:
            assert bpy.ops.mesh.duplicate() == {'FINISHED'}
        assert bpy.ops.transform.translate(value=(0, 0, 1)) == {'FINISHED'}
        manager.refresh(bpy.context, 3)
        assert len(manager.items) == 2, (operation, len(manager.items))
        parent, child = manager.items
        assert all(math.isclose(hit['point'][2], 0., abs_tol=1e-5)
                   for hit in (parent.first, parent.second))
        assert all(math.isclose(hit['point'][2], 1., abs_tol=1e-5)
                   for hit in (child.first, child.second))
        assert all(math.isclose(item.distance, 2., abs_tol=1e-5)
                   for item in (parent, child))
    print('PASS Extrude Region and Duplicate create coherent selected child', flush=True)


def test_repeated_inset():
    obj, manager = setup()
    assert bpy.ops.mesh.inset(thickness=.25, depth=0) == {'FINISHED'}
    manager.refresh(bpy.context, 3)
    assert len(manager.items) == 2
    assert bpy.ops.mesh.inset(thickness=.25, depth=0) == {'FINISHED'}
    manager.refresh(bpy.context, 4)
    assert len(manager.items) == 3
    assert [round(item.distance, 5) for item in manager.items] == [2., 1.5, 1.]
    assert all('(frozen)' not in hit['object_name'] for hit in
               (manager.items[-1].first, manager.items[-1].second))
    manager.refresh(bpy.context, 5)
    assert len(manager.items) == 3
    print('PASS repeated Inset forks only the newly selected branch', flush=True)


def test_subdivide_no_false_child():
    obj, manager = setup()
    assert bpy.ops.mesh.subdivide(number_cuts=1) == {'FINISHED'}
    manager.refresh(bpy.context, 3)
    assert len(manager.items) == 1
    assert manager.items[0].first and manager.items[0].second
    assert math.isclose(manager.items[0].distance, 2., abs_tol=1e-5)
    print('PASS Subdivide midpoint does not become a false descendant', flush=True)


def test_subdivide_then_translate_no_false_child():
    for mixed_world in (False, True):
        obj, manager = setup(mixed_world=mixed_world)
        assert bpy.ops.mesh.subdivide(number_cuts=1) == {'FINISHED'}
        assert bpy.ops.transform.translate(value=(.3, .1, 0)) == {'FINISHED'}
        manager.refresh(bpy.context, 3)
        assert len(manager.items) == 1, 'Moved old vertices falsely forked after Subdivide'
        item = manager.items[0]
        assert item.first['anchor'].get('kind') != 'WORLD', 'Original vertex stopped following'
        assert np.allclose(item.first['point'], (-.7, -.9, 0.), atol=1e-5)
        if mixed_world:
            assert item.second['anchor']['kind'] == 'WORLD'
        else:
            assert item.second['anchor'].get('kind') != 'WORLD'
            assert np.allclose(item.second['point'], (1.3, -.9, 0.), atol=1e-5)
    print('PASS Subdivide then translate keeps original live and makes no child', flush=True)


def test_shared_and_mixed():
    obj, manager = setup(two_rulers=True)
    assert bpy.ops.mesh.inset(thickness=.25, depth=0) == {'FINISHED'}
    manager.refresh(bpy.context, 3)
    assert len(manager.items) == 4, 'Shared source ID was repaired before all rulers forked'
    assert [item.label for item in manager.items] == [
        'Measured edge', 'Shared corner', 'Measured edge (new)', 'Shared corner (new)']
    obj, manager = setup(mixed_world=True)
    assert bpy.ops.mesh.inset(thickness=.25, depth=0) == {'FINISHED'}
    manager.refresh(bpy.context, 3)
    assert len(manager.items) == 2
    assert manager.items[1].second['anchor']['kind'] == 'WORLD'
    print('PASS two shared-vertex rulers and mixed WORLD endpoint fork', flush=True)


def test_unrelated_islands_do_not_pair():
    if bpy.context.object and bpy.context.object.mode != 'OBJECT':
        bpy.ops.object.mode_set(mode='OBJECT')
    bpy.ops.object.select_all(action='SELECT')
    bpy.ops.object.delete(use_global=False)
    mesh = bpy.data.meshes.new('two disconnected planes')
    mesh.from_pydata([(-3,-1,0), (-1,-1,0), (-1,1,0), (-3,1,0),
                      (1,-1,0), (3,-1,0), (3,1,0), (1,1,0)], [],
                     [(0,1,2,3), (4,5,6,7)])
    mesh.update()
    obj = bpy.data.objects.new('two disconnected planes', mesh)
    bpy.context.collection.objects.link(obj)
    bpy.context.view_layer.objects.active = obj
    obj.select_set(True)
    manager = ruler.RulerCollection(bpy.context)
    manager.picker = SnapPicker(bpy.context, 0)
    item = ruler.RulerState(bpy.context)
    item.first = hit(manager.picker, (-3., -1., 0.))
    item.second = hit(manager.picker, (3., -1., 0.))
    manager.items.append(item)
    manager.active = 0
    manager.refresh(bpy.context, 1)
    bpy.ops.object.mode_set(mode='EDIT')
    manager.refresh(bpy.context, 2)
    assert bpy.ops.mesh.inset(thickness=.2, depth=0) == {'FINISHED'}
    manager.refresh(bpy.context, 3)
    assert len(manager.items) == 1
    assert item.first and item.second and math.isclose(item.distance, 6., abs_tol=1e-5)
    print('PASS descendants on unrelated islands never form a child pair', flush=True)


def test_cancel_and_fallback():
    obj, manager = setup()
    # A canceled native modal operator returns the same topology. Background
    # bpy.ops.ed.undo() does not actually revert EditMesh, so model that final
    # state without pretending a headless Undo exercised native cancel.
    saved_guard = vertex_tracking._may_write_edit_identity
    try:
        vertex_tracking._may_write_edit_identity = lambda: False
        manager.refresh(bpy.context, 3)
        assert manager._pending_vertex_resolution
        assert len(manager.items) == 1
    finally:
        vertex_tracking._may_write_edit_identity = saved_guard
    manager.refresh(bpy.context, 3)
    assert len(manager.items) == 1
    assert not manager._pending_vertex_resolution
    # An unsupported/deleted vertex freezes at its last verified world point.
    bm = bmesh.from_edit_mesh(obj.data)
    layer = bm.verts.layers.int.get(manager.items[0].first['anchor']['vertex_layer'])
    target = next(v for v in bm.verts
                  if int(v[layer]) == manager.items[0].first['anchor']['vertex_id'])
    bmesh.ops.delete(bm, geom=[target], context='VERTS')
    bmesh.update_edit_mesh(obj.data, destructive=True)
    del target, layer, bm
    manager.refresh(bpy.context, 4)
    assert len(manager.items) == 1
    item = manager.items[0]
    assert item.first['anchor']['kind'] == 'WORLD'
    assert math.isclose(item.distance, 2., abs_tol=1e-5)
    assert 'frozen' in item.message.lower()
    print('PASS deferred no-change cancel makes no fork; deleted anchor freezes visibly', flush=True)


test_inset()
test_extrude_and_duplicate()
test_repeated_inset()
test_subdivide_no_false_child()
test_subdivide_then_translate_no_false_child()
test_shared_and_mixed()
test_unrelated_islands_do_not_pair()
test_cancel_and_fallback()
print('RULER LINEAGE PASS', bpy.app.version_string, flush=True)
