"""Verify persistent handlers, fresh RNA after file load, and timer cleanup."""
import json
import sys
from pathlib import Path
import bpy

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import final_dimensions as addon
addon.register()
path = ROOT / 'artifacts' / 'load-fixture.blend'
bpy.ops.wm.save_as_mainfile(filepath=str(path))
addon._cache[123] = {'stale': True}
bpy.ops.wm.open_mainfile(filepath=str(path))
assert not addon._cache
assert bpy.app.timers.is_registered(addon._timer_tick)
assert bpy.app.handlers.depsgraph_update_post.count(addon._on_depsgraph_update) == 1
assert addon.measure_object(bpy.context, bpy.context.object)['final'] == (2, 2, 2)
addon.unregister()
assert not bpy.app.timers.is_registered(addon._timer_tick)
assert addon._on_state_reset not in bpy.app.handlers.load_post
report = {'blender': bpy.app.version_string, 'status': 'PASS',
          'checks': ['load_clears_cache', 'load_restarts_timer', 'no_duplicate_handler', 'postload_measurement', 'unregister_cleans_up']}
(ROOT / 'artifacts' / 'load.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
print('LOAD TEST PASS')
