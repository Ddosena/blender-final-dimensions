"""Full add-on undo_post lifecycle for temporary rulers; headless API only."""
import sys
from pathlib import Path
from types import SimpleNamespace

import bpy
import numpy as np
from mathutils import Matrix

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import final_dimensions as addon
from final_dimensions import ruler
from final_dimensions.snapping import SnapPicker, _projection


REGION = SimpleNamespace(width=1000, height=1000)
VIEW = SimpleNamespace(
    perspective_matrix=Matrix(((.2, 0, 0, 0), (0, .2, 0, 0),
                               (0, 0, -.05, 0), (0, 0, 0, 1))),
    view_matrix=Matrix.Translation((0, 0, -10)), is_perspective=False)


def hit(picker, point):
    projection = np.asarray(VIEW.perspective_matrix)
    xy = _projection(np.array((point,)), projection, REGION)[1][0]
    result = picker.snap(bpy.context, np.array((point[0], point[1], 20.)),
                         np.array((0., 0., -1.)), REGION, VIEW, xy,
                         {'VERTEX'}, 'ORIGINAL')
    assert result and result['snap_kind'] == 'VERTEX', result
    return result


addon.register()
assert addon._on_history_reset in bpy.app.handlers.undo_post
assert addon._on_history_reset in bpy.app.handlers.redo_post
bpy.ops.object.select_all(action='SELECT')
bpy.ops.object.delete(use_global=False)
bpy.ops.mesh.primitive_plane_add(size=2)
obj = bpy.context.object
window = bpy.context.window
manager = ruler.RulerCollection(bpy.context)
manager.picker = SnapPicker(bpy.context, 0)
state = ruler.RulerState(bpy.context)
state.first = hit(manager.picker, (-1., -1., 0.))
state.second = hit(manager.picker, (1., -1., 0.))
manager.items.append(state)
manager.active = 0
ruler._states[window.as_pointer()] = manager
manager.refresh(bpy.context, 1)
bpy.ops.object.mode_set(mode='EDIT')
manager.refresh(bpy.context, 2)
bpy.ops.ed.undo_push(message='ruler history baseline')
assert bpy.ops.mesh.inset(thickness=.25, depth=0) == {'FINISHED'}

# Blender background Undo reports FINISHED but leaves EditMesh changed. The
# actual registered undo_post handler must still keep rulers and suppress a
# spurious new child from the history step.
assert bpy.ops.ed.undo() == {'FINISHED'}
assert ruler.collection(window) is manager
assert len(manager.items) == 1
assert manager.picker is None and manager._suppress_next_fork
manager.refresh(bpy.context, 3)
assert len(manager.items) == 1
assert state.first is not None and state.second is not None
assert not manager._suppress_next_fork
print('PASS registered undo_post preserves ruler and suppresses history fork', flush=True)

# The suppression is one-shot. Bind an inner ruler after history, then a new
# native Inset must still make a child of that newer ruler.
new_state = ruler.RulerState(bpy.context)
new_state.first = hit(manager.picker, (-.75, -.75, 0.))
new_state.second = hit(manager.picker, (.75, -.75, 0.))
manager.items.append(new_state)
manager.refresh(bpy.context, 4)
assert bpy.ops.mesh.inset(thickness=.15, depth=0) == {'FINISHED'}
manager.refresh(bpy.context, 5)
assert len(manager.items) == 3
assert manager.items[2].first and manager.items[2].second
print('PASS ordinary Inset can fork again after undo_post', flush=True)

# Run the registered redo handler directly because headless ed.redo.poll() is
# false. This checks the same add-on callback without asserting mesh replay.
addon._on_history_reset(None)
assert ruler.collection(window) is manager
assert len(manager.items) == 3
manager.refresh(bpy.context, 6)
assert len(manager.items) == 3
print('PASS redo_post callback preserves completed rulers', flush=True)
addon.unregister()
print('RULER HISTORY PASS', bpy.app.version_string, flush=True)
