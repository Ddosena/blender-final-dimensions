"""Background geometry checks for Loop Offset after an already completed cut."""
import json
import math
import sys
import traceback
from pathlib import Path
from types import SimpleNamespace

import bmesh
import bpy
from mathutils import Euler, Matrix, Vector

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from final_dimensions import loop_offset

report = {'blender': bpy.app.version_string, 'cases': []}
loop_offset.register()


def near(actual, expected, tolerance=1e-6):
    assert abs(actual - expected) <= tolerance, (actual, expected)


def make_mesh(name, corners, faces, selected_pairs):
    if bpy.context.object and bpy.context.object.mode != 'OBJECT':
        bpy.ops.object.mode_set(mode='OBJECT')
    bpy.ops.object.select_all(action='SELECT')
    bpy.ops.object.delete(use_global=False)
    bm = bmesh.new()
    verts = [bm.verts.new(co) for co in corners]
    bm.verts.ensure_lookup_table()
    for indices in faces:
        bm.faces.new(tuple(verts[index] for index in indices))
    bm.normal_update()
    mesh = bpy.data.meshes.new(name)
    bm.to_mesh(mesh)
    bm.free()
    obj = bpy.data.objects.new(name, mesh)
    bpy.context.scene.collection.objects.link(obj)
    obj.select_set(True)
    bpy.context.view_layer.objects.active = obj
    bpy.context.tool_settings.mesh_select_mode = (False, True, False)
    bpy.ops.object.mode_set(mode='EDIT')
    edit = bmesh.from_edit_mesh(mesh)
    edit.verts.ensure_lookup_table()
    for edge in edit.edges:
        edge.select_set(False)
    for a, b in selected_pairs:
        edge = edit.edges.get((edit.verts[a], edit.verts[b]))
        assert edge is not None, (a, b)
        edge.select_set(True)
    edit.select_flush_mode()
    bmesh.update_edit_mesh(mesh)
    return obj


def cube_strip(top_heights=(1., 1., 1., 1.)):
    xy = ((-1., -1.), (1., -1.), (1., 1.), (-1., 1.))
    bottom = [(x, y, -1.) for x, y in xy]
    middle = [(x, y, (top_heights[i]-1.)/2.) for i, (x, y) in enumerate(xy)]
    top = [(x, y, top_heights[i]) for i, (x, y) in enumerate(xy)]
    faces = []
    for i in range(4):
        j = (i+1) % 4
        faces.extend(((i, j, 4+j, 4+i), (4+i, 4+j, 8+j, 8+i)))
    faces.extend(((8, 9, 10, 11), (3, 2, 1, 0)))
    selected = [(4+i, 4+(i+1)%4) for i in range(4)]
    return make_mesh('Loop Cut Cube', bottom+middle+top, faces, selected)


def plane_strip():
    points = ((-1., -1., 0.), (-1., 1., 0.), (0., -1., 0.),
              (0., 1., 0.), (1., -1., 0.), (1., 1., 0.))
    return make_mesh('Loop Cut Plane', points,
                     ((0, 2, 3, 1), (2, 4, 5, 3)), ((2, 3),))


def native_cube_cut():
    """Use Blender's own Loop Cut macro through a real View3D API context."""
    if bpy.context.object and bpy.context.object.mode != 'OBJECT':
        bpy.ops.object.mode_set(mode='OBJECT')
    bpy.ops.object.select_all(action='SELECT')
    bpy.ops.object.delete(use_global=False)
    bpy.ops.mesh.primitive_cube_add(size=2)
    obj = bpy.context.object
    bpy.context.tool_settings.mesh_select_mode = (False, True, False)
    bpy.ops.object.mode_set(mode='EDIT')
    bpy.ops.mesh.select_all(action='DESELECT')
    views = [(window, area, region)
             for window in bpy.context.window_manager.windows
             for area in window.screen.areas if area.type == 'VIEW_3D'
             for region in area.regions if region.type == 'WINDOW']
    assert views, 'No 3D View API context is available'
    window, area, region = views[0]
    with bpy.context.temp_override(window=window, area=area, region=region):
        assert bpy.ops.mesh.loopcut_slide.poll()
        result = bpy.ops.mesh.loopcut_slide(
            'EXEC_DEFAULT',
            MESH_OT_loopcut={'number_cuts': 1, 'edge_index': 0, 'object_index': 0},
            TRANSFORM_OT_edge_slide={'value': 0.0},
        )
    assert result == {'FINISHED'}, result
    edit = bmesh.from_edit_mesh(obj.data)
    assert len(edit.verts) == 12 and sum(edge.select for edge in edit.edges) == 4
    return obj


def positions(obj):
    bm = bmesh.from_edit_mesh(obj.data)
    bm.verts.ensure_lookup_table()
    bm.verts.index_update()
    return {vert.index: tuple(vert.co) for vert in bm.verts}


def apply(distance_bu, side='A'):
    try:
        return bpy.ops.mesh.final_dimensions_loop_offset(distance=distance_bu, side=side)
    except RuntimeError as exc:
        if 'Error:' not in str(exc):
            raise
        return {'CANCELLED'}


def check_absolute(obj, side, distance_bu, before):
    _, edit, sides, _ = loop_offset._analyze(bpy.context)
    matrix = obj.matrix_world
    selected = {vert.index for vert in sides}
    current = positions(obj)
    for index, original in before.items():
        if index not in selected:
            assert math.dist(current[index], original) <= 1e-7, (index, original, current[index])
    for vert, (a, b) in sides.items():
        boundary = a if side == 'A' else b
        opposite = b if side == 'A' else a
        point, start, end = (matrix @ element.co for element in (vert, boundary, opposite))
        near((point-start).length, distance_bu)
        rail = end-start
        near((point-start).cross(rail).length, 0., 1e-5)


try:
    scene = bpy.context.scene
    scene.unit_settings.system = 'METRIC'
    scene.unit_settings.scale_length = 1.

    if bpy.app.version >= (5, 2, 0):
        obj = native_cube_cut()
        before = positions(obj)
        assert apply(.002, 'A') == {'FINISHED'}
        check_absolute(obj, 'A', .002, before)
        assert apply(.003, 'B') == {'FINISHED'}
        check_absolute(obj, 'B', .003, before)
        report['cases'].append('native_loopcut_slide_api_both_boundaries_only_cut_moves')
    else:
        # Blender 4.5.14 crashes the background process at EXEC_DEFAULT even
        # with poll=True and a valid View3D override (probe exit code 11).
        report['native_loopcut_slide'] = 'UNVERIFIED: Blender 4.5.14 background API crash'

    obj = cube_strip()
    before = positions(obj)
    _, edit, sides, _ = loop_offset._analyze(bpy.context)
    signature = loop_offset._selection_signature(bpy.context, obj, edit, sides)
    obj.location.x += 1.
    bpy.context.view_layer.update()
    assert loop_offset._selection_signature(bpy.context, obj, edit, sides) != signature
    obj.location.x -= 1.
    bpy.context.view_layer.update()
    sentinel = object()
    loop_offset._preview_handles.add(sentinel)
    loop_offset._clear_preview(SimpleNamespace(_preview_handle=None))
    assert sentinel in loop_offset._preview_handles
    loop_offset._preview_handles.remove(sentinel)
    report['cases'].append('dialog_target_signature_and_unrelated_preview_preserved')

    assert apply(.002, 'A') == {'FINISHED'}
    check_absolute(obj, 'A', .002, before)
    report['cases'].append('closed_cube_loop_2mm_only_new_vertices_move')

    # Reapplying from either side is absolute, even without a redo restore.
    assert apply(.005, 'B') == {'FINISHED'}
    check_absolute(obj, 'B', .005, before)
    report['cases'].append('switch_side_and_distance_has_no_accumulated_offset')

    obj = cube_strip(top_heights=(1., 2., 1.5, 1.2))
    obj.matrix_world = (Matrix.Translation((3., -2., .5)) @
                        Euler((.31, -.42, .71)).to_matrix().to_4x4() @
                        Matrix.Diagonal((-2., .5, 3., 1.)))
    scene.unit_settings.scale_length = .1
    before = positions(obj)
    # Two physical millimeters are 0.02 world Blender units at scale 0.1.
    assert apply(.002 / scene.unit_settings.scale_length, 'A') == {'FINISHED'}
    check_absolute(obj, 'A', .02, before)
    report['cases'].append('nonuniform_mirrored_rotated_scale_and_scene_units')

    obj = plane_strip()
    scene.unit_settings.scale_length = 1.
    before = positions(obj)
    assert apply(.002, 'B') == {'FINISHED'}
    check_absolute(obj, 'B', .002, before)
    report['cases'].append('open_quad_strip')

    snapshot = positions(obj)
    assert apply(3., 'A') == {'CANCELLED'}
    assert positions(obj) == snapshot
    report['cases'].append('out_of_range_distance_does_not_mutate')

    edit = bmesh.from_edit_mesh(obj.data)
    for edge in edit.edges:
        edge.select_set(False)
    edit.select_flush_mode()
    bmesh.update_edit_mesh(obj.data)
    snapshot = positions(obj)
    assert apply(.002) == {'CANCELLED'}
    assert positions(obj) == snapshot
    report['cases'].append('missing_cut_selection_does_not_mutate')

    # A branch and an unrelated selected vertex must fail before any write.
    obj = cube_strip()
    edit = bmesh.from_edit_mesh(obj.data)
    middle = next(vert for vert in edit.verts if vert.select)
    extra = next(edge for edge in middle.link_edges if not edge.select)
    extra.select_set(True)
    edit.select_flush_mode()
    bmesh.update_edit_mesh(obj.data)
    snapshot = positions(obj)
    assert apply(.002) == {'CANCELLED'}
    assert positions(obj) == snapshot
    report['cases'].append('branched_selection_does_not_mutate')

    obj = make_mesh(
        'Triangle Beside Cut',
        ((-1., -1., 0.), (-1., 1., 0.), (0., -1., 0.),
         (0., 1., 0.), (1., -1., 0.), (1., 1., 0.)),
        ((0, 2, 3), (2, 4, 5, 3)), ((2, 3),),
    )
    snapshot = positions(obj)
    assert apply(.002) == {'CANCELLED'}
    assert positions(obj) == snapshot
    report['cases'].append('nonquad_neighbor_does_not_mutate')

    obj = cube_strip()
    duplicate = bpy.data.objects.new('Shared Mesh', obj.data)
    bpy.context.scene.collection.objects.link(duplicate)
    snapshot = positions(obj)
    assert apply(.002) == {'CANCELLED'}
    assert positions(obj) == snapshot
    report['cases'].append('shared_mesh_does_not_mutate')

    obj = cube_strip()
    bpy.ops.object.mode_set(mode='OBJECT')
    obj.shape_key_add(name='Basis')
    bpy.ops.object.mode_set(mode='EDIT')
    snapshot = positions(obj)
    assert apply(.002) == {'CANCELLED'}
    assert positions(obj) == snapshot
    report['cases'].append('shape_keys_do_not_mutate')

    obj = cube_strip()
    obj.scale.x = 0.
    bpy.context.view_layer.update()
    snapshot = positions(obj)
    assert apply(.002) == {'CANCELLED'}
    assert positions(obj) == snapshot
    report['cases'].append('zero_scale_does_not_mutate')

    report['status'] = 'PASS'
except Exception:
    report['status'] = 'FAIL'
    report['error'] = traceback.format_exc()
finally:
    path = ROOT / 'artifacts' / ('loop-offset-'+bpy.app.version_string.replace(' ', '-')+'.json')
    path.write_text(json.dumps(report, indent=2), encoding='utf-8')
    print('LOOP OFFSET', report['status'], report.get('error', ''), flush=True)
    loop_offset.unregister()
    if report['status'] != 'PASS':
        raise SystemExit(1)
