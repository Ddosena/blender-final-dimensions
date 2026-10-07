"""Background API checks for temporary loop-tool overlay suppression."""
import sys
from pathlib import Path
from types import SimpleNamespace

import bpy

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import final_dimensions as addon
from final_dimensions import hover, loop_offset

addon.register()
window = SimpleNamespace(as_pointer=lambda: 101, modal_operators=[])
other = SimpleNamespace(as_pointer=lambda: 102, modal_operators=[])
wm = bpy.context.window_manager
assert not wm.final_dimensions_hover and not wm.final_dimensions_show_overlay
wm.final_dimensions_hover = True
try:
    for identifier in ('MESH_OT_loopcut_slide', 'mesh.loopcut',
                       'TRANSFORM_OT_edge_slide', 'transform.vert_slide'):
        window.modal_operators = [SimpleNamespace(bl_idname=identifier)]
        assert hover.temporarily_suppressed(window), identifier
    window.modal_operators = [SimpleNamespace(
        bl_rna=SimpleNamespace(identifier='MESH_OT_loopcut_slide'))]
    assert hover.temporarily_suppressed(window)
    window.modal_operators = [SimpleNamespace(bl_idname='transform.translate')]
    assert not hover.temporarily_suppressed(window)
    window.modal_operators = []

    value = {'area': 10, 'section': {'diameter': 2.}}
    hover._results[101] = value
    handle = bpy.types.SpaceView3D.draw_handler_add(lambda: None, (), 'WINDOW', 'POST_PIXEL')
    loop_offset._preview_handles.add(handle)
    loop_offset._preview_windows[handle] = 101
    owner = SimpleNamespace(_preview_handle=handle, _invocation_signature=('test',))
    assert hover.temporarily_suppressed(window)
    assert not hover.temporarily_suppressed(other)
    assert hover.get(window) is None
    assert wm.final_dimensions_hover  # Never change the user's checkbox.

    loop_offset.MESH_OT_final_dimensions_loop_offset.cancel(owner, bpy.context)
    assert not hover.temporarily_suppressed(window)
    assert hover.get(window) is value
    assert not loop_offset._preview_handles and not loop_offset._preview_windows
    assert wm.final_dimensions_hover

    wm.final_dimensions_hover = False
    loop_offset._clear_preview()
    assert not wm.final_dimensions_hover
    print('LOOP OVERLAY SUPPRESSION PASS', bpy.app.version_string, flush=True)
finally:
    addon.unregister()
    assert not loop_offset._preview_handles and not loop_offset._preview_windows
