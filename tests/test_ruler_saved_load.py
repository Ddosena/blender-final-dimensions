"""Check that saved ruler sessions contain no native point helper IDs."""
import json
import sys
from pathlib import Path

import bpy

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from final_dimensions import point_edit

output = Path(sys.argv[sys.argv.index('--') + 1])
report = {'blender': bpy.app.version_string, 'status': 'FAIL', 'cases': []}
try:
    assert not any(obj.get('_final_dimensions_point_proxy') for obj in bpy.data.objects)
    report['cases'].append('saved_file_has_no_native_proxy')

    source = bpy.data.objects.get('Bound Vertex Source')
    assert source is not None and source.type == 'MESH'
    report['cases'].append('saved_file_preserves_source_mesh')

    keep = bpy.data.objects.new('Final Dimensions Point', None)
    bpy.context.scene.collection.objects.link(keep)
    discard = bpy.data.objects.new('Other Name', None)
    discard['_final_dimensions_point_proxy'] = True
    bpy.context.scene.collection.objects.link(discard)
    point_edit.cleanup_tagged()
    assert bpy.data.objects.get(keep.name) is keep
    assert bpy.data.objects.get('Other Name') is None
    report['cases'].append('load_cleanup_uses_ownership_tag_not_name')
    report['status'] = 'PASS'
except Exception as exc:
    report['error'] = repr(exc)
output.write_text(json.dumps(report, indent=2), encoding='utf-8')
print('RULER SAVED LOAD', report['status'], report.get('error', ''), flush=True)
if report['status'] != 'PASS':
    raise SystemExit(1)
