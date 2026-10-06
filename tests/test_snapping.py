"""Blender background feature picking tests, using an explicit synthetic view."""
import sys
from pathlib import Path
from types import SimpleNamespace

import bpy
import bmesh
import numpy as np
from mathutils import Matrix

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from final_dimensions.snapping import SnapPicker, _projection
from final_dimensions import snapping, surface


REGION = SimpleNamespace(width=1000, height=1000)
# Ortho viewport spans [-5,5] in x/y and looks toward -Z from z=10.
VIEW = SimpleNamespace(perspective_matrix=Matrix(((.2, 0, 0, 0), (0, .2, 0, 0),
                                                (0, 0, -.05, 0), (0, 0, 0, 1))),
                       view_matrix=Matrix.Translation((0, 0, -10)), is_perspective=False)
TOOLS = bpy.context.scene.tool_settings
for attr in ('use_snap_edit', 'use_snap_nonedit', 'use_snap_self'):
    setattr(TOOLS, attr, True)
TOOLS.use_snap_selectable = False
TOOLS.use_snap_backface_culling = False
EPOCH = 0


def clear():
    if bpy.context.object and bpy.context.object.mode == 'EDIT':
        bpy.ops.object.mode_set(mode='OBJECT')
    for obj in tuple(bpy.data.objects):
        bpy.data.objects.remove(obj, do_unlink=True)


def mesh(name, vertices, edges=(), faces=()):
    data = bpy.data.meshes.new(name)
    data.from_pydata(vertices, edges, faces)
    data.update()
    obj = bpy.data.objects.new(name, data)
    bpy.context.collection.objects.link(obj)
    bpy.context.view_layer.update()
    return obj


def picker():
    global EPOCH
    EPOCH += 1
    bpy.context.view_layer.update()
    return SnapPicker(bpy.context, EPOCH)


def pixel(point, view=VIEW):
    return tuple(_projection(np.array((point,)), np.asarray(view.perspective_matrix), REGION)[1][0])


def hit(p, point, elements, source='BOTH', view=VIEW, context=None):
    # Pick ray travels through the supplied projected coordinate.
    h = np.asarray(view.perspective_matrix) @ np.array((*point, 1.))
    inv = np.linalg.inv(np.asarray(view.perspective_matrix))
    xy = h[:2]/h[3]
    a, b = inv @ np.array((*xy, -1., 1.)), inv @ np.array((*xy, 1., 1.))
    origin, end = a[:3]/a[3], b[:3]/b[3]
    direction = end-origin
    return p.snap(context or bpy.context, origin, direction, REGION, view, pixel(point, view), elements, source)


def close(actual, expected, tolerance=1e-5):
    assert np.allclose(actual, expected, atol=tolerance, rtol=0), (actual, expected)


def refresh(p):
    global EPOCH
    EPOCH += 1
    bpy.context.view_layer.update()
    p.refresh(bpy.context, EPOCH)


def original_final_and_anchors():
    clear()
    obj = mesh('deformed', [(-1,-1,0),(1,-1,0),(1,1,0),(-1,1,0)], faces=[(0,1,2,3)])
    modifier = obj.modifiers.new('final thickness', 'SOLIDIFY')
    modifier.thickness = 1
    modifier.offset = 1
    p = picker()
    orig = hit(p, (-1,-1,0), {'VERTEX'}, 'ORIGINAL')
    final = hit(p, (-1,-1,1), {'VERTEX'}, 'FINAL')
    assert orig and final
    close(orig['point'], (-1,-1,0))
    close(final['point'], (-1,-1,1))
    edge_orig = hit(p, (0,-1,0), {'EDGE'}, 'ORIGINAL')
    edge_final = hit(p, (0,-1,1), {'EDGE'}, 'FINAL')
    assert edge_orig and edge_final
    close(edge_orig['point'], (0,-1,0))
    close(edge_final['point'], (0,-1,1))
    face_orig = hit(p, (0,0,0), {'FACE'}, 'ORIGINAL')
    assert face_orig
    close(face_orig['point'], (0,0,0))
    face = hit(p, (0,0,1), {'FACE'}, 'FINAL')
    assert face
    close(face['point'], (0,0,1))
    both = hit(p, (-1,-1,1), {'VERTEX'}, 'BOTH')
    assert both['snap_source'] == 'FINAL'
    # Original source should remain usable on its own despite modifier envelope.
    modifier.thickness = 2
    refresh(p)
    close(p.resolve(bpy.context, orig['anchor']), orig['point'])
    close(p.resolve(bpy.context, final['anchor']), (-1,-1,2))
    for v in obj.data.vertices:
        v.co.x += .3
    obj.data.update()
    refresh(p)
    close(p.resolve(bpy.context, orig['anchor']), (-.7,-1,0))
    close(p.resolve(bpy.context, final['anchor']), (-.7,-1,2))
    modifier.use_rim = False
    refresh(p)
    close(p.resolve(bpy.context, orig['anchor']), (-.7,-1,0))
    try:
        p.resolve(bpy.context, final['anchor'])
    except ValueError as exc:
        assert 'topology' in str(exc).lower()
    else:
        raise AssertionError('Final topology must invalidate only Final anchor')
    assert obj.data.vertices[0].co.x == np.float32(-.7)


def loose_geometry_and_real_edges():
    clear()
    mesh('wire', [(-1,0,0),(1,0,0),(3,0,0)], edges=[(0,1)])
    p = picker()
    assert hit(p, (3,0,0), {'VERTEX'}, 'FINAL')['snap_kind'] == 'VERTEX'
    close(hit(p, (0,0,0), {'EDGE'}, 'FINAL')['point'], (0,0,0))
    close(hit(p, (0,0,0), {'EDGE_MIDPOINT'}, 'FINAL')['point'], (0,0,0))
    # Vertex can be beyond the silhouette by 10 px and needs no ray hit.
    close(hit(p, (3.1,0,0), {'VERTEX'}, 'FINAL')['point'], (3,0,0))
    assert hit(p, (2,0,0), {'VERTEX', 'EDGE'}, 'FINAL') is None
    clear()
    mesh('quad', [(-1,-1,0),(1,-1,0),(1,1,0),(-1,1,0)], faces=[(0,1,2,3)])
    p = picker()
    assert hit(p, (0,0,0), {'EDGE'}, 'FINAL') is None, 'Triangulation diagonal became snap edge'
    assert hit(p, (0,0,0), {'FACE_PROJECT'}, 'FINAL')['snap_kind'] == 'FACE'
    assert hit(p, (-1,-1,0), {'VERTEX','FACE'}, 'FINAL')['snap_kind'] == 'VERTEX'
    old = p.pick(bpy.context, (0,0,10), (0,0,-1))
    close(p.resolve(bpy.context, old['anchor']), old['point'])
    bpy.context.view_layer.objects.active = bpy.data.objects['quad']
    bpy.data.objects['quad'].select_set(True)
    subdiv = bpy.data.objects['quad'].modifiers.new('generated', 'SUBSURF')
    subdiv.subdivision_type = 'SIMPLE'
    subdiv.levels = 1
    refresh(p)
    generated = hit(p, (0,0,0), {'VERTEX'}, 'FINAL')
    assert generated and hit(p, (0,0,0), {'VERTEX'}, 'ORIGINAL') is None
    # Middle subdivision edges are real evaluated mesh edges.
    assert hit(p, (0,-.5,0), {'EDGE'}, 'FINAL')


def perspective_edges_and_clipping():
    clear()
    # Standard GL perspective with near=1, far=20; camera at origin, looking -Z.
    view = SimpleNamespace(perspective_matrix=Matrix(((1,0,0,0),(0,1,0,0),(0,0,-21/19,-40/19),(0,0,-1,0))),
                           view_matrix=Matrix.Identity(4), is_perspective=True)
    mesh('slanted', [(-1,0,-2),(2,0,-8)], edges=[(0,1)])
    p = picker()
    # Screen midpoint corresponds to world t=.2, rather than .5.
    expected = (-.4,0,-3.2)
    close(hit(p, expected, {'EDGE'}, 'FINAL', view)['point'], expected)
    clear()
    mesh('clipped', [(0,0,-.5),(0,0,-21),(1,0,-2)], edges=[(0,2)])
    p = picker()
    assert hit(p, (0,0,-.5), {'VERTEX'}, 'FINAL', view) is None
    assert hit(p, (0,0,-21), {'VERTEX'}, 'FINAL', view) is None
    assert hit(p, (.5,0,-1.25), {'EDGE'}, 'FINAL', view)


def target_filters_and_occlusion():
    clear()
    back = mesh('back point', [(0,0,0)])
    front = mesh('blocker', [(-1,-1,1),(1,-1,1),(1,1,1),(-1,1,1)], faces=[(0,1,2,3)])
    p = picker()
    assert hit(p, (0,0,0), {'VERTEX'}, 'FINAL') is None
    proxy = SimpleNamespace(scene=bpy.context.scene, view_layer=bpy.context.view_layer,
                            evaluated_depsgraph_get=bpy.context.evaluated_depsgraph_get,
                            space_data=SimpleNamespace(type='NONE', shading=SimpleNamespace(type='SOLID', show_xray=True)))
    assert hit(p, (0,0,0), {'VERTEX'}, 'FINAL', context=proxy)['object_name'] == back.name
    back.hide_select = True
    TOOLS.use_snap_selectable = True
    assert hit(p, (0,0,0), {'VERTEX'}, 'FINAL', context=proxy) is None
    TOOLS.use_snap_selectable = False
    front.hide_set(True)
    refresh(p)
    assert hit(p, (0,0,0), {'VERTEX'}, 'FINAL')
    clear()
    # Reverse winding: looking toward -Z sees the back of this face.
    mesh('backface', [(-1,-1,0),(1,-1,0),(1,1,0),(-1,1,0)], faces=[(3,2,1,0)])
    p = picker()
    TOOLS.use_snap_backface_culling = True
    assert hit(p, (-1,-1,0), {'VERTEX','EDGE','FACE'}, 'FINAL') is None
    TOOLS.use_snap_backface_culling = False
    assert hit(p, (-1,-1,0), {'VERTEX'}, 'FINAL')


def live_edit_and_shared_budget():
    clear()
    obj = mesh('edit wire', [(-1,0,0),(1,0,0)], edges=[(0,1)])
    bpy.context.view_layer.objects.active = obj
    obj.select_set(True)
    bpy.ops.object.mode_set(mode='EDIT')
    bm = bmesh.from_edit_mesh(obj.data)
    bm.verts.ensure_lookup_table()
    bm.verts[0].co.y = 1
    bm.verts[0].hide_set(True)
    bmesh.update_edit_mesh(obj.data)
    p = picker()
    assert hit(p, (-1,1,0), {'VERTEX'}, 'ORIGINAL') is None
    bm.verts[0].hide_set(False)
    bmesh.update_edit_mesh(obj.data)
    refresh(p)
    assert hit(p, (-1,1,0), {'VERTEX'}, 'ORIGINAL')
    TOOLS.use_snap_self = False
    bm.verts[0].select_set(True)
    bmesh.update_edit_mesh(obj.data)
    refresh(p)
    assert hit(p, (-1,1,0), {'VERTEX'}, 'ORIGINAL') is None
    TOOLS.use_snap_self = True
    TOOLS.use_snap_edit = False
    assert hit(p, (1,0,0), {'VERTEX'}, 'ORIGINAL'), 'Active edit owner ignores other-edit filter'
    TOOLS.use_snap_edit = True
    bpy.ops.object.mode_set(mode='OBJECT')
    refresh(p)
    limits = surface.MAX_CACHED_RECORDS, surface.MAX_CACHED_VERTICES
    try:
        surface.MAX_CACHED_RECORDS = 1
        surface.MAX_CACHED_VERTICES = 2
        p._feature_geometry(bpy.context, p.records[0], 'ORIGINAL')
        p._feature_geometry(bpy.context, p.records[0], 'FINAL')
        assert len(p._geometry_cache) == 1 and p._cache_vertices == 2
    finally:
        surface.MAX_CACHED_RECORDS, surface.MAX_CACHED_VERTICES = limits
    old_limit = snapping.MAX_VERTICES
    try:
        snapping.MAX_VERTICES = 1
        refresh(p)
        try:
            hit(p, (1,0,0), {'VERTEX'}, 'FINAL')
        except ValueError as exc:
            assert 'vertex limit' in str(exc)
        else:
            raise AssertionError('Vertex limit ignored')
    finally:
        snapping.MAX_VERTICES = old_limit
    assert hit(p, (1,0,0), {'VERTEX'}, 'FINAL'), 'to_mesh cleanup after rejected snapshot failed'


def instances_filters_and_cage():
    clear()
    owner = mesh('instance owner', [(0,0,0)])
    group = bpy.data.node_groups.new('snap instances', 'GeometryNodeTree')
    group.interface.new_socket(name='Geometry', in_out='OUTPUT', socket_type='NodeSocketGeometry')
    nodes, links = group.nodes, group.links
    shape = nodes.new('GeometryNodeMeshCube')
    shape.inputs['Size'].default_value = (2,2,2)
    line = nodes.new('GeometryNodeMeshLine')
    line.inputs['Count'].default_value = 2
    line.inputs['Offset'].default_value = (3,0,0)
    instance = nodes.new('GeometryNodeInstanceOnPoints')
    links.new(line.outputs['Mesh'], instance.inputs['Points'])
    links.new(shape.outputs['Mesh'], instance.inputs['Instance'])
    output = nodes.new('NodeGroupOutput')
    links.new(instance.outputs['Instances'], output.inputs['Geometry'])
    owner.modifiers.new('GN instances', 'NODES').node_group = group
    p = picker()
    result = hit(p, (4,1,1), {'VERTEX'}, 'FINAL')
    assert result and result['anchor']['key'][0] == 'INSTANCE'
    assert hit(p, (4,1,1), {'VERTEX'}, 'ORIGINAL') is None
    assert hit(p, (4,0,1), {'EDGE'}, 'FINAL')
    owner.location.y = .5
    refresh(p)
    close(p.resolve(bpy.context, result['anchor']), (4,1.5,1))
    # The Original point lies inside Final mesh instances of the same owner.
    # BOTH exposes the cage, while unrelated object occlusion remains active.
    result = hit(p, (0,.5,0), {'VERTEX'}, 'BOTH')
    assert result and result['snap_source'] == 'ORIGINAL'
    unrelated = mesh('unrelated blocker', [(-.5,0,2),(.5,0,2),(.5,1,2),(-.5,1,2)], faces=[(0,1,2,3)])
    refresh(p)
    assert hit(p, (0,.5,0), {'VERTEX'}, 'BOTH') is None
    unrelated.hide_set(True)
    refresh(p)
    assert hit(p, (0,.5,0), {'VERTEX'}, 'BOTH')
    # Object-mode snapping ignores target flags intended for edit-mode objects.
    TOOLS.use_snap_nonedit = False
    assert hit(p, (4,1.5,1), {'VERTEX'}, 'FINAL')
    bpy.context.view_layer.objects.active = owner
    owner.select_set(True)
    bpy.ops.object.mode_set(mode='EDIT')
    target = mesh('nonedit target', [(2,-2,2)])
    refresh(p)
    assert hit(p, (2,-2,2), {'VERTEX'}, 'FINAL') is None
    TOOLS.use_snap_nonedit = True
    assert hit(p, (2,-2,2), {'VERTEX'}, 'FINAL')
    bpy.ops.object.mode_set(mode='OBJECT')
    # Off-ray heavy records must not be built for occlusion.
    clear()
    mesh('target', [(0,0,0)])
    mesh('offray', [(99,99,0),(100,99,0),(100,100,0)], faces=[(0,1,2)])
    p = picker()
    assert hit(p, (0,0,0), {'VERTEX'}, 'FINAL')
    # Snap target projection itself visits meshes, but an occlusion query must
    # leave the unrelated record untouched.
    p._geometry_cache.clear()
    p._cache_vertices = p._cache_triangles = 0
    record = next(record for record in p.records if record['name'] == 'target')
    assert not p._occluded(bpy.context, record, np.zeros(3), np.asarray(VIEW.perspective_matrix), np.linalg.inv(np.asarray(VIEW.perspective_matrix)), False)
    assert all(p._by_key[key]['name'] != 'offray' for key in p._geometry_cache)


def silhouette_pixel_ties_prefer_front():
    clear()
    bpy.ops.mesh.primitive_cube_add(size=2)
    p = picker()
    # A nearly axis-aligned view makes the rear corner fractionally closer to
    # the cursor while its ray grazes outside the front silhouette. This must
    # resolve by forward depth rather than invisible subpixel differences.
    view = SimpleNamespace(perspective_matrix=Matrix(((.2,0,-1e-8,0),(0,.2,0,0),
                                                      (0,0,-.05,0),(0,0,0,1))),
                           view_matrix=Matrix.Translation((0,0,-10)), is_perspective=False)
    result = hit(p, (1,1,-1), {'VERTEX'}, 'ORIGINAL', view)
    assert result
    close(result['point'], (1,1,1))
    # A visible pixel separation continues to prefer the nearer screen target.
    view.perspective_matrix[0][2] = -.003
    result = hit(p, (1,1,-1), {'VERTEX'}, 'ORIGINAL', view)
    assert result
    close(result['point'], (1,1,-1))


for check in (original_final_and_anchors, loose_geometry_and_real_edges,
              perspective_edges_and_clipping, target_filters_and_occlusion,
              live_edit_and_shared_budget, instances_filters_and_cage,
              silhouette_pixel_ties_prefer_front):
    check()
    print('SNAP CHECK', check.__name__, flush=True)
clear()
print('SNAPPING PASS', bpy.app.version_string, flush=True)
