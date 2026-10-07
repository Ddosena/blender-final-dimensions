"""Install the distribution ZIP through Blender's extension operator in a private profile."""
import importlib
import json
import tomllib
from pathlib import Path
import bpy

ROOT = Path(__file__).resolve().parents[1]
version = bpy.app.version_string.replace(' ', '-')
repository = ROOT / 'artifacts' / 'profiles' / version / 'extensions'
repository.mkdir(parents=True, exist_ok=True)
addon_version = tomllib.loads((ROOT / 'final_dimensions' / 'blender_manifest.toml').read_text(encoding='utf-8'))['version']
package = ROOT / 'dist' / f'final_dimensions-{addon_version}.zip'
result = bpy.ops.preferences.extension_repo_add(
    name='Final Dimensions QA', type='LOCAL',
    use_custom_directory=True, custom_directory=str(repository),
)
assert result == {'FINISHED'}, result
repo = next(r for r in bpy.context.preferences.extensions.repos if Path(r.directory) == repository)
result = bpy.ops.extensions.package_install_files(
    filepath=str(package),
    repo=repo.module, enable_on_install=True,
)
assert result == {'FINISHED'}, result
module_name = f'bl_ext.{repo.module}.final_dimensions'
assert module_name in bpy.context.preferences.addons
module = importlib.import_module(module_name)
assert str(repository) in module.__file__, module.__file__
dimensions = module.measure_object(bpy.context, bpy.context.object)
assert dimensions['final'] == (2.0, 2.0, 2.0), dimensions
assert bpy.app.timers.is_registered(module._timer_tick)
assert module.ruler._registered and len(module.ruler._handles) == 1
assert hasattr(bpy.types.Scene, 'final_dimensions_ruler_appearance')
bpy.ops.view3d.final_dimensions_ruler_style.get_rna_type()
bpy.ops.mesh.final_dimensions_loop_offset.get_rna_type()
bpy.ops.view3d.final_dimensions_ruler_activate.get_rna_type()
bpy.ops.preferences.addon_disable(module=module_name)
assert not bpy.app.timers.is_registered(module._timer_tick)
assert module._on_depsgraph_update not in bpy.app.handlers.depsgraph_update_post
assert not module._cache
assert not module.ruler._registered and not module.ruler._handles
assert not module.ruler._sessions and not module.ruler._states
assert not module.loop_offset._preview_handles and not module.loop_offset._preview_windows
assert module.loop_offset._on_load_pre not in bpy.app.handlers.load_pre
assert not hasattr(bpy.types.Scene, 'final_dimensions_ruler_appearance')
bpy.ops.preferences.addon_enable(module=module_name)
assert bpy.app.handlers.depsgraph_update_post.count(module._on_depsgraph_update) == 1
assert bpy.app.timers.is_registered(module._timer_tick)
assert module.ruler._registered and len(module.ruler._handles) == 1
bpy.ops.preferences.addon_disable(module=module_name)
report = {'blender': bpy.app.version_string, 'status': 'PASS', 'installed_module': module_name,
          'package': str(package),
          'checks': ['install_zip', 'automatic_enable', 'installed_code_measurement', 'ruler_registration', 'disable_cleanup', 're_enable_no_duplicate_handlers']}
(ROOT / 'artifacts' / f'install-{version}.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
print('INSTALL TEST PASS', module_name)
