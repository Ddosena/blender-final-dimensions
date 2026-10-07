"""Headless manager test for native modal EditMesh deferral and retry."""
import sys
from pathlib import Path
from types import SimpleNamespace

import bpy
import numpy as np
from mathutils import Matrix

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from final_dimensions import ruler, vertex_tracking
from final_dimensions.snapping import SnapPicker, _projection


region = SimpleNamespace(width=1000, height=1000)
view = SimpleNamespace(
    perspective_matrix=Matrix(((.2, 0, 0, 0), (0, .2, 0, 0),
                               (0, 0, -.05, 0), (0, 0, 0, 1))),
    view_matrix=Matrix.Translation((0, 0, -10)), is_perspective=False)


def pick(picker, point):
    projection = np.asarray(view.perspective_matrix)
    xy = _projection(np.array((point,)), projection, region)[1][0]
    result = picker.snap(bpy.context, np.array((point[0], point[1], 20.)),
                         np.array((0., 0., -1.)), region, view, xy,
                         {'VERTEX'}, 'ORIGINAL')
    assert result and result['snap_kind'] == 'VERTEX'
    return result


bpy.ops.object.select_all(action='SELECT')
bpy.ops.object.delete(use_global=False)
bpy.ops.mesh.primitive_plane_add(size=2)
obj = bpy.context.object
manager = ruler.RulerCollection(bpy.context)
manager.picker = SnapPicker(bpy.context, 0)
item = ruler.RulerState(bpy.context)
item.first = pick(manager.picker, (-1., -1., 0.))
item.second = pick(manager.picker, (1., -1., 0.))
manager.items.append(item)
manager.active = 0
manager.original = dict(item.first)
first, second, original = item.first, item.second, manager.original
manager.refresh(bpy.context, 1)
assert abs(item.distance - 2.) < 1e-5
bpy.ops.object.mode_set(mode='EDIT')
manager.refresh(bpy.context, 2)

# A canceled native Inset may expose provisional topology during its modal
# lifetime. Retain the hits and retry at the same epoch after Undo restores it.
bpy.ops.ed.undo_push(message='before deferred inset')
assert bpy.ops.mesh.inset(thickness=.15, depth=0) == {'FINISHED'}
saved_guard = vertex_tracking._may_write_edit_identity
try:
    vertex_tracking._may_write_edit_identity = lambda: False
    manager.refresh(bpy.context, 3)
    assert manager._pending_vertex_resolution
    assert item.first is first and item.second is second
    assert manager.original is original
    assert abs(item.distance - 2.) < 1e-5
finally:
    vertex_tracking._may_write_edit_identity = saved_guard
assert bpy.ops.ed.undo() == {'FINISHED'}
manager.refresh(bpy.context, 3)  # No depsgraph epoch increment required.
assert not manager._pending_vertex_resolution
assert item.first is first and item.second is second
assert manager.original is original
assert abs(item.distance - 2.) < 1e-5
print('PASS deferred native edit cancel retains both endpoints and original', flush=True)

# A confirmed Inset must also retry at the same epoch and repair duplicated
# POINT IDs before the next Object-mode conversion.
assert bpy.ops.mesh.inset(thickness=.15, depth=0) == {'FINISHED'}
try:
    vertex_tracking._may_write_edit_identity = lambda: False
    manager.refresh(bpy.context, 4)
    assert manager._pending_vertex_resolution
finally:
    vertex_tracking._may_write_edit_identity = saved_guard
manager.refresh(bpy.context, 4)
assert not manager._pending_vertex_resolution
assert item.first is first and item.second is second
assert manager.original is original
assert abs(item.distance - 2.) < 1e-5
bpy.ops.object.mode_set(mode='OBJECT')
manager.refresh(bpy.context, 5)
assert item.first is first and item.second is second
assert abs(item.distance - 2.) < 1e-5
print('PASS deferred native edit confirm retries and survives mode conversion', flush=True)
print('VERTEX RULER DEFERRED PASS', bpy.app.version_string, flush=True)
