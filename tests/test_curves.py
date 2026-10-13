"""Blender background curve picking tests, using an explicit synthetic view."""
import sys
from pathlib import Path
from types import SimpleNamespace

import bpy
import numpy as np
from mathutils import Matrix

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from final_dimensions.snapping import SnapPicker, _projection


REGION = SimpleNamespace(width=1000, height=1000)
# Ortho viewport spans [-5,5] in x/y (100 px per unit) and looks toward -Z.
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


def link(obj):
    bpy.context.collection.objects.link(obj)
    bpy.context.view_layer.update()
    return obj


def poly_curve(name, points):
    data = bpy.data.curves.new(name, 'CURVE')
    data.dimensions = '3D'
    spline = data.splines.new('POLY')
    spline.points.add(len(points)-1)
    for point, co in zip(spline.points, points):
        point.co = (*co, 1.)
    return link(bpy.data.objects.new(name, data))


def mesh(name, vertices, edges=(), faces=()):
    data = bpy.data.meshes.new(name)
    data.from_pydata(vertices, edges, faces)
    data.update()
    return link(bpy.data.objects.new(name, data))


def picker():
    global EPOCH
    EPOCH += 1
    bpy.context.view_layer.update()
    return SnapPicker(bpy.context, EPOCH)


def refresh(p):
    global EPOCH
    EPOCH += 1
    bpy.context.view_layer.update()
    p.refresh(bpy.context, EPOCH)


def pixel(point):
    return tuple(_projection(np.array((point,)), np.asarray(VIEW.perspective_matrix), REGION)[1][0])


def ray(point):
    h = np.asarray(VIEW.perspective_matrix) @ np.array((*point, 1.))
    inv = np.linalg.inv(np.asarray(VIEW.perspective_matrix))
    xy = h[:2]/h[3]
    a, b = inv @ np.array((*xy, -1., 1.)), inv @ np.array((*xy, 1., 1.))
    origin, end = a[:3]/a[3], b[:3]/b[3]
    return origin, end-origin


def free(p, point):
    """Magnet off: the ruler passes its view so thin lines can be found."""
    origin, direction = ray(point)
    return p.pick(bpy.context, origin, direction, (REGION, VIEW, pixel(point)))


def hit(p, point, elements, source='BOTH'):
    origin, direction = ray(point)
    return p.snap(bpy.context, origin, direction, REGION, VIEW, pixel(point), elements, source)


def close(actual, expected, tolerance=1e-5):
    assert np.allclose(actual, expected, atol=tolerance, rtol=0), (actual, expected)


def wire_curve_free_pick_follows_edits():
    clear()
    obj = poly_curve('wire curve', [(-2, 0, 0), (0, 0, 0), (2, 0, 0)])
    p = picker()
    assert any(record['name'] == obj.name for record in p.records), 'Curve objects must be pickable'
    origin, direction = ray((1, .02, 0))
    assert p.pick(bpy.context, origin, direction) is None, 'A wire has no faces for a bare ray'
    # 2 px off the line still lands on it; 50 px away does not.
    first = free(p, (1, .02, 0))
    assert first and first['snap_kind'] == 'WIRE' and first['object_name'] == obj.name
    assert first['warnings'] == (), first['warnings']
    close(first['point'], (1, 0, 0))
    assert 'vertex_id' not in first['anchor']
    assert free(p, (1, .5, 0)) is None
    second = free(p, (-1.5, -.03, 0))
    close(second['point'], (-1.5, 0, 0))
    close(np.linalg.norm(np.subtract(first['point'], second['point'])), 2.5)
    # The anchor rides on the curve when a control point moves.
    obj.data.splines[0].points[2].co = (2, 1, 0, 1)
    refresh(p)
    close(p.resolve(bpy.context, first['anchor']), (1, .5, 0))
    close(p.resolve(bpy.context, second['anchor']), (-1.5, 0, 0))


def control_points_and_final_line():
    clear()
    obj = poly_curve('snap curve', [(-2, 0, 0), (0, 1, 0), (2, 0, 0)])
    p = picker()
    control = hit(p, (0, 1, 0), {'VERTEX'}, 'ORIGINAL')
    assert control and control['snap_source'] == 'ORIGINAL' and control['snap_kind'] == 'VERTEX'
    close(control['point'], (0, 1, 0))
    assert 'vertex_id' not in control['anchor'], 'Curves have no mesh vertex identity'
    assert hit(p, (-1, .5, 0), {'EDGE'}, 'ORIGINAL') is None, 'Control points are not an edge cage'
    edge = hit(p, (-1, .5, 0), {'EDGE'}, 'FINAL')
    assert edge and edge['snap_source'] == 'FINAL'
    close(edge['point'], (-1, .5, 0))
    middle = hit(p, (1, .5, 0), {'EDGE_MIDPOINT'}, 'FINAL')
    close(middle['point'], (1, .5, 0))
    both = hit(p, (0, 1, 0), {'VERTEX'}, 'BOTH')
    assert both and both['snap_source'] == 'ORIGINAL'
    obj.location.x = 3
    obj.data.splines[0].points[1].co = (0, 2, 0, 1)
    refresh(p)
    close(p.resolve(bpy.context, control['anchor']), (3, 2, 0))
    close(p.resolve(bpy.context, edge['anchor']), (2, 1, 0))
    obj.data.splines[0].points.add(1)
    refresh(p)
    for anchor in (control['anchor'], edge['anchor']):
        try:
            p.resolve(bpy.context, anchor)
        except ValueError as exc:
            assert 'topology' in str(exc).lower()
        else:
            raise AssertionError('A changed spline layout must invalidate curve anchors')


def bezier_line_and_knots():
    clear()
    bpy.ops.curve.primitive_bezier_curve_add(location=(0, 0, 0))
    obj = bpy.context.object
    knot = tuple(obj.data.splines[0].bezier_points[0].co)
    p = picker()
    result = hit(p, knot, {'VERTEX'}, 'ORIGINAL')
    close(result['point'], knot)
    handle = tuple(obj.data.splines[0].bezier_points[0].handle_left)
    assert hit(p, handle, {'VERTEX'}, 'ORIGINAL') is None, 'Handles are not measured points'
    # A free pick near the drawn line lies on the evaluated polyline.
    evaluated = obj.evaluated_get(bpy.context.evaluated_depsgraph_get())
    line = evaluated.to_mesh()
    vertices = np.array([tuple(v.co) for v in line.vertices])
    edges = np.array([tuple(e.vertices) for e in line.edges])
    evaluated.to_mesh_clear()
    target = (vertices[edges[5, 0]] + vertices[edges[5, 1]]) / 2
    picked = free(p, target + (0, .03, 0))
    assert picked
    a, b = vertices[edges[:, 0]], vertices[edges[:, 1]]
    t = np.clip(np.einsum('ij,ij->i', picked['point']-a, b-a)/np.einsum('ij,ij->i', b-a, b-a), 0, 1)
    assert np.min(np.linalg.norm(a + t[:, None]*(b-a) - picked['point'], axis=1)) < 1e-5


def edit_mode_control_points():
    clear()
    obj = poly_curve('edit curve', [(-2, 0, 0), (0, 0, 0), (2, 0, 0)])
    bpy.context.view_layer.objects.active = obj
    obj.select_set(True)
    bpy.ops.object.mode_set(mode='EDIT')
    bpy.ops.curve.select_all(action='DESELECT')
    points = obj.data.splines[0].points
    points[1].co = (0, 1.5, 0, 1)
    p = picker()
    live = hit(p, (0, 1.5, 0), {'VERTEX'}, 'ORIGINAL')
    assert live, 'Edit Mode control points must be live'
    close(live['point'], (0, 1.5, 0))
    close(free(p, (-1, .75, 0))['point'], (-1, .75, 0))
    points[0].hide = True
    refresh(p)
    assert hit(p, (-2, 0, 0), {'VERTEX'}, 'ORIGINAL') is None
    points[0].hide = False
    points[2].select = True
    TOOLS.use_snap_self = False
    refresh(p)
    assert hit(p, (2, 0, 0), {'VERTEX'}, 'ORIGINAL') is None
    TOOLS.use_snap_self = True
    assert hit(p, (2, 0, 0), {'VERTEX'}, 'ORIGINAL')
    bpy.ops.object.mode_set(mode='OBJECT')
    refresh(p)
    close(p.resolve(bpy.context, live['anchor']), (0, 1.5, 0))


def beveled_curve_is_a_surface():
    clear()
    obj = poly_curve('tube', [(-2, 0, 0), (2, 0, 0)])
    obj.data.bevel_depth = .2
    p = picker()
    # Blender also lists the bevel mesh as a temporary instance of the curve.
    assert [record['key'][0] for record in p.records] == ['OBJECT'], p.records
    top = p.pick(bpy.context, (0, 0, 10), (0, 0, -1))
    assert top and 'snap_kind' not in top and top['warnings'] == ()
    close(top['point'], (0, 0, .2), 2e-3)
    assert 'snap_kind' not in free(p, (0, 0, 0)), 'A beveled curve has no loose wire'
    inside = hit(p, (-2, 0, 0), {'VERTEX'}, 'ORIGINAL')
    assert inside, 'Own bevel must not hide the control points'


def wire_mesh_and_occlusion():
    clear()
    mesh('loose edge', [(-1, -2, 0), (1, -2, 0)], edges=[(0, 1)])
    mesh('quad', [(-1, -1, 0), (1, -1, 0), (1, 1, 0), (-1, 1, 0)], faces=[(0, 1, 2, 3)])
    p = picker()
    close(free(p, (0, -1.98, 0))['point'], (0, -2, 0))
    border = free(p, (0, -.98, 0))
    assert border and 'snap_kind' not in border, 'Face border edges are surface, not wire'
    clear()
    poly_curve('hidden wire', [(-2, 0, 0), (2, 0, 0)])
    blocker = mesh('blocker', [(-3, -1, 1), (3, -1, 1), (3, 1, 1), (-3, 1, 1)], faces=[(0, 1, 2, 3)])
    p = picker()
    covered = free(p, (0, .02, 0))
    assert covered and covered['object_name'] == blocker.name, 'A covered wire must not win'
    blocker.hide_set(True)
    refresh(p)
    assert free(p, (0, .02, 0))['object_name'] == 'hidden wire'


def text_and_nurbs_surface():
    clear()
    bpy.ops.surface.primitive_nurbs_surface_surface_add(location=(0, 0, 0))
    surface_obj = bpy.context.object
    bpy.ops.object.text_add(location=(10, 0, 0))
    text = bpy.context.object
    p = picker()
    names = [record['name'] for record in p.records]
    assert sorted(names) == sorted((surface_obj.name, text.name)), names
    top = p.pick(bpy.context, (0, 0, 10), (0, 0, -1))
    assert top and top['object_name'] == surface_obj.name
    evaluated = text.evaluated_get(bpy.context.evaluated_depsgraph_get())
    glyphs = evaluated.to_mesh()
    corner = tuple(text.matrix_world @ glyphs.vertices[0].co)
    evaluated.to_mesh_clear()
    p = picker()
    result = hit(p, corner, {'VERTEX'}, 'FINAL')
    assert result and result['object_name'] == text.name
    assert hit(p, corner, {'VERTEX'}, 'ORIGINAL') is None, 'Text has no control points'


def geometry_nodes_on_curve():
    clear()
    obj = poly_curve('gn curve', [(-2, 0, 0), (2, 0, 0)])
    group = bpy.data.node_groups.new('curve instances', 'GeometryNodeTree')
    group.interface.new_socket(name='Geometry', in_out='INPUT', socket_type='NodeSocketGeometry')
    group.interface.new_socket(name='Geometry', in_out='OUTPUT', socket_type='NodeSocketGeometry')
    nodes, links = group.nodes, group.links
    source, output = nodes.new('NodeGroupInput'), nodes.new('NodeGroupOutput')
    modifier = obj.modifiers.new('GN', 'NODES')
    modifier.node_group = group
    links.new(source.outputs[0], output.inputs[0])
    p = picker()
    wire = free(p, (0, .02, 0))
    assert wire and wire['warnings'] == (), 'Curves drawn as wire are not omitted'
    shape = nodes.new('GeometryNodeMeshCube')
    instances = nodes.new('GeometryNodeInstanceOnPoints')
    join = nodes.new('GeometryNodeJoinGeometry')
    links.new(source.outputs[0], instances.inputs['Points'])
    links.new(shape.outputs['Mesh'], instances.inputs['Instance'])
    links.new(instances.outputs['Instances'], join.inputs[0])
    links.new(source.outputs[0], join.inputs[0])
    links.new(join.outputs[0], output.inputs[0])
    p = picker()
    assert [record['key'][0] for record in p.records] == ['OBJECT'], p.records
    wire = free(p, (0, .02, 0))
    assert wire and any('instances' in warning for warning in wire['warnings']), wire['warnings']


for check in (wire_curve_free_pick_follows_edits, control_points_and_final_line,
              bezier_line_and_knots, edit_mode_control_points, beveled_curve_is_a_surface,
              wire_mesh_and_occlusion, text_and_nurbs_surface, geometry_nodes_on_curve):
    check()
    print('CURVE CHECK', check.__name__, flush=True)
clear()
print('CURVES PASS', bpy.app.version_string, flush=True)
