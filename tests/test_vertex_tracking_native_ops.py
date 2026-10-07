"""Headless native EditMesh lifecycle regression; no UI event simulation."""
import sys
from pathlib import Path
from types import SimpleNamespace

import bpy
import numpy as np
from mathutils import Matrix

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from final_dimensions.snapping import SnapPicker, _projection
from final_dimensions import vertex_tracking


REGION = SimpleNamespace(width=1000, height=1000)
VIEW = SimpleNamespace(
    perspective_matrix=Matrix(((.2, 0, 0, 0), (0, .2, 0, 0),
                               (0, 0, -.05, 0), (0, 0, 0, 1))),
    view_matrix=Matrix.Translation((0, 0, -10)), is_perspective=False)


def pick(picker, point):
    projection = np.asarray(VIEW.perspective_matrix)
    xy = _projection(np.array((point,)), projection, REGION)[1][0]
    hit = picker.snap(bpy.context, np.array((point[0], point[1], 20.)),
                      np.array((0., 0., -1.)), REGION, VIEW, xy,
                      {'VERTEX'}, 'ORIGINAL')
    assert hit and hit['snap_kind'] == 'VERTEX', hit
    return hit['anchor']


def scalar_tree(value):
    if isinstance(value, (str, int, float, bool, type(None))):
        return True
    if isinstance(value, (tuple, list)):
        return all(scalar_tree(item) for item in value)
    if isinstance(value, dict):
        return all(isinstance(key, str) and scalar_tree(item)
                   for key, item in value.items())
    return False


bpy.ops.object.select_all(action='SELECT')
bpy.ops.object.delete(use_global=False)
bpy.ops.mesh.primitive_plane_add(size=2)
obj = bpy.context.object
pickers = [SnapPicker(bpy.context, 0), SnapPicker(bpy.context, 0)]
anchors = [pick(pickers[0], (-1., -1., 0.)),
           pick(pickers[1], (1., -1., 0.))]
assert all(scalar_tree(anchor) for anchor in anchors)
assert not vars(vertex_tracking.TRACKER), 'Tracker retained native references'
assert vertex_tracking._may_write_edit_identity([
    SimpleNamespace(bl_idname='view3d.final_dimensions_hover'),
    SimpleNamespace(bl_rna=SimpleNamespace(identifier='VIEW3D_OT_final_dimensions_ruler'))])
assert not vertex_tracking._may_write_edit_identity([
    SimpleNamespace(bl_idname='view3d.final_dimensions_hover'),
    SimpleNamespace(bl_rna=SimpleNamespace(identifier='MESH_OT_inset'))])

bpy.ops.object.mode_set(mode='EDIT')
for picker, anchor in zip(pickers, anchors):
    picker.refresh(bpy.context, 1)
    assert np.allclose(picker.resolve(bpy.context, anchor),
                       (-1., -1., 0.) if picker is pickers[0] else (1., -1., 0.))

# Native Inset duplicates POINT CustomData. Every iteration must release all
# local BMesh wrappers before the next operator; the two independent pickers
# must continue resolving the same original vertices.
for iteration in range(16):
    assert bpy.ops.mesh.inset(thickness=.01, depth=0) == {'FINISHED'}
    for picker, anchor, expected in zip(pickers, anchors,
                                        ((-1., -1., 0.), (1., -1., 0.))):
        picker.refresh(bpy.context, iteration + 2)
        assert np.allclose(picker.resolve(bpy.context, anchor), expected)
        assert scalar_tree(anchor)
    assert not vars(vertex_tracking.TRACKER)
print('PASS 16 native Insets, two pickers, scalar-only anchor state', flush=True)

# While a native modal operation owns EditMesh, resolution may read but must
# defer identity-layer repairs. Exercise the same branch without UI events.
saved_guard = vertex_tracking._may_write_edit_identity
before_modal_topologies = [anchor['verified_topology'] for anchor in anchors]
try:
    vertex_tracking._may_write_edit_identity = lambda: False
    bpy.ops.mesh.inset(thickness=.01, depth=0)
    for picker, anchor in zip(pickers, anchors):
        picker.refresh(bpy.context, 20)
        try:
            picker.resolve(bpy.context, anchor)
        except vertex_tracking.VertexResolutionDeferred:
            pass
        else:
            raise AssertionError('Native modal edit was resolved prematurely')
    assert [anchor['verified_topology'] for anchor in anchors] == before_modal_topologies
finally:
    vertex_tracking._may_write_edit_identity = saved_guard
for picker, anchor in zip(pickers, anchors):
    picker.refresh(bpy.context, 21)
    picker.resolve(bpy.context, anchor)
print('PASS native-modal resolution defers before EditMesh access', flush=True)

bpy.ops.object.mode_set(mode='OBJECT')
for picker, anchor, expected in zip(pickers, anchors,
                                    ((-1., -1., 0.), (1., -1., 0.))):
    picker.refresh(bpy.context, 22)
    assert np.allclose(picker.resolve(bpy.context, anchor), expected)
print('PASS repaired IDs survive Object mode conversion', flush=True)

bpy.ops.object.mode_set(mode='EDIT')
for picker, anchor in zip(pickers, anchors):
    picker.refresh(bpy.context, 23)
    picker.resolve(bpy.context, anchor)
bpy.ops.ed.undo_push(message='vertex tracking stress')
bpy.ops.mesh.inset(thickness=.01, depth=0)
for picker, anchor in zip(pickers, anchors):
    picker.refresh(bpy.context, 24)
    picker.resolve(bpy.context, anchor)
assert bpy.ops.ed.undo() == {'FINISHED'}
for picker, anchor in zip(pickers, anchors):
    picker.refresh(bpy.context, 25)
    try:
        picker.resolve(bpy.context, anchor)
    except ValueError:
        pass  # Conservative invalidation after an unverified EditMesh rebuild.
if bpy.ops.ed.redo.poll():
    assert bpy.ops.ed.redo() == {'FINISHED'}
    for picker, anchor in zip(pickers, anchors):
        picker.refresh(bpy.context, 26)
        try:
            picker.resolve(bpy.context, anchor)
        except ValueError:
            pass
    print('PASS native Undo/Redo lifecycle without retained BMesh wrappers', flush=True)
else:
    # Blender does not expose Redo in --background after bpy.ops.ed.undo().
    print('PASS native Undo; Redo unavailable in background context', flush=True)
print('VERTEX NATIVE OPS PASS', bpy.app.version_string, flush=True)
