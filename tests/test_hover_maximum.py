"""Headless Ctrl-lock state and real evaluated section geometry; no input injection."""
import math
import sys
from pathlib import Path
from unittest.mock import patch

import bpy
from mathutils import Vector

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import final_dimensions as addon
from final_dimensions import hover


addon.register()
wm = bpy.context.window_manager
assert not wm.final_dimensions_hover
assert not wm.final_dimensions_show_overlay
assert not wm.bl_rna.properties['final_dimensions_hover'].default
assert not wm.bl_rna.properties['final_dimensions_show_overlay'].default
print('PASS both overlays are off by default', flush=True)

bpy.ops.object.select_all(action='SELECT')
bpy.ops.object.delete(use_global=False)
bpy.ops.mesh.primitive_cube_add(size=2)
obj = bpy.context.object
# Use evaluated geometry with a real modifier.
mod = obj.modifiers.new('Evaluation', 'SUBSURF')
mod.subdivision_type = 'SIMPLE'
mod.levels = 1
bpy.context.view_layer.update()
wm.final_dimensions_hover = True
wm.final_dimensions_section_axis = 'Z'
window = bpy.context.window
area = next(a for a in window.screen.areas if a.type == 'VIEW_3D')
region = next(r for r in area.regions if r.type == 'WINDOW')
probe = hover.Probe()
probe.mouse = (region.x+40, region.y+40)
ray_origin = [Vector((3., .5, .25))]

# Projection is supplied as numerical rays: no mouse or keyboard events are
# generated, and all ray hits/sections use the actual evaluated Blender mesh.
with patch.object(hover, '_viewport_at', return_value=(area, region)), \
     patch.object(hover.view3d_utils, 'region_2d_to_origin_3d',
                  side_effect=lambda *a, **k: ray_origin[0]), \
     patch.object(hover.view3d_utils, 'region_2d_to_vector_3d',
                  return_value=Vector((-1., 0., 0.))):
    with bpy.context.temp_override(area=area, region=region):
        context = bpy.context
        probe.update(context, 1)
        normal = hover.get(window)
        assert normal['section'] and not normal['maximum_snap'], normal
        assert math.isclose(normal['section']['diameter'], math.sqrt(5), abs_tol=1e-5)
        assert probe.set_maximum_snap(True)
        probe.update(context, 1)
        locked = hover.get(window)
        assert locked['maximum_snap']
        assert math.isclose(locked['section']['diameter'], math.sqrt(8), abs_tol=1e-5)
        assert math.isclose(math.dist(*locked['section']['endpoints']), math.sqrt(8), abs_tol=1e-5)
        assert locked['section']['radius'] == locked['section']['diameter']/2
        # Moving to a ray outside the mesh cannot move a held measurement.
        ray_origin[0] = Vector((3., 10., .8))
        probe.mouse = (region.x+80, region.y+80)
        probe.update(context, 1)
        assert hover.get(window) is locked
        assert not probe.set_maximum_snap(True)
        probe.update(context, 1)
        assert hover.get(window) is locked
        print('PASS Ctrl uses extremal endpoints and holds section despite cursor motion', flush=True)

        probe.set_maximum_snap(False)
        probe.update(context, 1)
        assert hover.get(window) is None, 'Release must resume ray selection'
        ray_origin[0] = Vector((3., 0., .8))
        probe.mouse = (region.x+90, region.y+90)
        probe.update(context, 1)
        assert math.isclose(hover.get(window)['section']['diameter'], 2., abs_tol=1e-5)
        probe.set_maximum_snap(True)
        probe.update(context, 1)
        assert all(abs(p[2]-.8) < 1e-5 for p in hover.get(window)['section']['endpoints'])
        print('PASS release and re-press select a fresh section', flush=True)

        # Changes to geometry invalidate the lock and cannot retain old numbers.
        obj.scale.x = 2.
        bpy.context.view_layer.update()
        probe.update(context, 2)
        assert math.isclose(hover.get(window)['section']['diameter'], math.sqrt(20), abs_tol=1e-5)
        space = area.spaces.active
        space.overlay.show_overlays = False
        probe.update(context, 2)
        assert hover.get(window) is None and probe.locked_value is None
        space.overlay.show_overlays = True
        probe.update(context, 2)
        assert probe.locked_value is not None
        with patch.object(hover, 'temporarily_suppressed', return_value=True):
            probe.update(context, 2)
            assert probe.locked_value is None
        wm.final_dimensions_hover = False
        probe.update(context, 2)
        assert hover.get(window) is None and probe.locked_value is None
        print('PASS geometry, visibility, loop-tool and toggle invalidation', flush=True)

addon.unregister()
print('HOVER MAXIMUM PASS', bpy.app.version_string, flush=True)
