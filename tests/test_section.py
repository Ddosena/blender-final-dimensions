"""Run with blender -b --python tests/test_section.py."""
import math
import sys
from pathlib import Path

import bpy
import numpy as np
from mathutils import Vector

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from final_dimensions.section import Geometry, _diameter, _hull


def close(actual, expected, tolerance=1e-5):
    assert abs(actual - expected) <= tolerance, (actual, expected)


def make_mesh(name, vertices, faces):
    mesh = bpy.data.meshes.new(name)
    mesh.from_pydata(vertices, [], faces)
    mesh.update()
    obj = bpy.data.objects.new(name, mesh)
    bpy.context.collection.objects.link(obj)
    bpy.context.view_layer.objects.active = obj
    return obj


def update():
    bpy.context.view_layer.update()


def check_box():
    obj = make_mesh("box", [(-1,-1,-1), (1,-1,-1), (1,1,-1), (-1,1,-1),
                            (-1,-1,1), (1,-1,1), (1,1,1), (-1,1,1)],
                    [(0,3,2,1), (4,5,6,7), (0,1,5,4), (1,2,6,5), (2,3,7,6), (3,0,4,7)])
    obj.scale = (2, 3, .5)
    obj.rotation_euler.z = .4
    obj.location = (4, -2, 3)
    update()
    geometry = Geometry.from_object(bpy.context, obj)
    expected = math.hypot(4, 6)
    result = geometry.section(obj.location, (0, 0, 1))
    close(result["diameter"], expected)
    assert result["closed"] and len(result["segments"]) == 8
    close(math.dist(*result["endpoints"]), expected)
    top = geometry.section((4, -2, 3.5), (0, 0, 1))
    close(top["diameter"], expected)
    assert top["closed"]
    assert geometry.ray_cast((4, -2, 10), (0, 0, -1)) is not None
    assert geometry.ray_cast((100, 100, 10), (0, 0, -1)) is None
    # Evaluated modifier stack changes actual section without touching source.
    modifier = obj.modifiers.new("solid", "SOLIDIFY")
    modifier.thickness = .2
    update()
    modified = Geometry.from_object(bpy.context, obj)
    assert len(modified.triangles) > len(geometry.triangles)
    bpy.data.objects.remove(obj, do_unlink=True)


def check_calipers():
    rng = np.random.default_rng(42)
    for scale in (1, 1e-6):
        for size in (3, 17, 250):
            points = rng.normal(size=(size, 2)) * scale
            actual, _, _ = _diameter(_hull(points))
            brute = max(math.dist(a, b) for a in points for b in points)
            close(actual, brute, 1e-9 * scale)


def check_cylinder():
    bpy.ops.mesh.primitive_cylinder_add(vertices=128, radius=1, depth=2)
    obj = bpy.context.object
    obj.scale = (2, 1, 1)
    update()
    geometry = Geometry.from_object(bpy.context, obj)
    result = geometry.section((0,0,0), (0,0,1))
    close(result["diameter"], 4)
    close(result["radius"], 2)
    assert result["closed"]
    hit = geometry.ray_cast((3,0,0), (-1,0,0))
    assert hit is not None and hit["triangle"] >= 0
    assert np.linalg.norm(hit["normal"]) > .99
    targeted = geometry.section(hit["point"], (0,0,1), hit["triangle"])
    close(targeted["diameter"], 4)
    bpy.data.objects.remove(obj, do_unlink=True)


def check_modifier_stack():
    bpy.ops.mesh.primitive_cylinder_add(vertices=32, radius=1, depth=2)
    obj = bpy.context.object
    subdivision = obj.modifiers.new("subdivision", "SUBSURF")
    subdivision.levels = 2
    update()
    subdivided = Geometry.from_object(bpy.context, obj)
    original = subdivided.section((0, 0, 0), (0, 0, 1))["diameter"]
    taper = obj.modifiers.new("taper", "SIMPLE_DEFORM")
    taper.deform_method = "TAPER"
    taper.deform_axis = "Z"
    taper.factor = .7
    update()
    geometry = Geometry.from_object(bpy.context, obj)
    assert len(geometry.triangles) == len(subdivided.triangles)
    low = geometry.section((0, 0, -.5), (0, 0, 1))["diameter"]
    middle = geometry.section((0, 0, 0), (0, 0, 1))["diameter"]
    high = geometry.section((0, 0, .5), (0, 0, 1))["diameter"]
    assert low < middle < high, (low, middle, high)
    close(middle, original, .03)
    for z in (-.5, .5):
        hit = geometry.ray_cast((3, 0, z), (-1, 0, 0))
        assert hit is not None
        measured = geometry.section(hit["point"], (0, 0, 1), hit["triangle"])
        close(measured["diameter"], low if z < 0 else high, .03)
    bpy.data.objects.remove(obj, do_unlink=True)


def check_gn_instances():
    obj = make_mesh("gn_source", [(-1,-1,-1), (1,-1,-1), (1,1,-1), (-1,1,-1),
                                  (-1,-1,1), (1,-1,1), (1,1,1), (-1,1,1)],
                    [(0,3,2,1), (4,5,6,7), (0,1,5,4), (1,2,6,5), (2,3,7,6), (3,0,4,7)])
    group = bpy.data.node_groups.new("section_instances", "GeometryNodeTree")
    group.interface.new_socket(name="Geometry", in_out="OUTPUT", socket_type="NodeSocketGeometry")
    nodes, links = group.nodes, group.links
    cube = nodes.new("GeometryNodeMeshCube")
    cube.inputs["Size"].default_value = (2, 4, 6)
    line = nodes.new("GeometryNodeMeshLine")
    line.inputs["Count"].default_value = 2
    line.inputs["Offset"].default_value = (10, 0, 0)
    instance = nodes.new("GeometryNodeInstanceOnPoints")
    links.new(line.outputs["Mesh"], instance.inputs["Points"])
    links.new(cube.outputs["Mesh"], instance.inputs["Instance"])
    output = nodes.new("NodeGroupOutput")
    links.new(instance.outputs["Instances"], output.inputs["Geometry"])
    obj.modifiers.new("GN", "NODES").node_group = group
    update()
    geometry = Geometry.from_object(bpy.context, obj)
    hit = geometry.ray_cast((10, 0, 10), (0, 0, -1))
    assert hit is not None and hit["point"][0] == 10
    section = geometry.section((10, 0, 0), (0, 0, 1), hit["triangle"])
    close(section["diameter"], math.sqrt(20))
    assert section["closed"]
    bpy.data.objects.remove(obj, do_unlink=True)


def check_components_and_hole():
    # Two disconnected square tubes, each section has outer and inner loops.
    verts = []
    faces = []
    for cx, size in ((0, 4), (20, 8)):
        base = len(verts)
        for z in (-1, 1):
            for radius in (size/2, size/4):
                verts.extend((cx+x*radius, y*radius, z) for x,y in ((-1,-1),(1,-1),(1,1),(-1,1)))
        for ring in (0, 4):
            other = ring + 8
            for i in range(4):
                j = (i+1)%4
                faces.append((base+ring+i, base+ring+j, base+other+j, base+other+i))
        for i in range(4):
            j = (i+1)%4
            faces.append((base+i,base+4+i,base+4+j,base+j))
            faces.append((base+8+i,base+8+j,base+12+j,base+12+i))
    obj = make_mesh("tubes", verts, faces)
    update()
    geometry = Geometry.from_object(bpy.context, obj)
    outer = geometry.section((2,0,0), (0,0,1))
    inner = geometry.section((1,0,0), (0,0,1))
    far = geometry.section((24,0,0), (0,0,1))
    close(outer["diameter"], 4*math.sqrt(2))
    close(inner["diameter"], 2*math.sqrt(2))
    close(far["diameter"], 8*math.sqrt(2))
    assert outer["closed"] and inner["closed"] and far["closed"]
    bpy.data.objects.remove(obj, do_unlink=True)


def check_open_and_degenerate():
    obj = make_mesh("open", [(-1,-1,-1), (1,-1,-1), (1,-1,1), (-1,-1,1)], [(0,1,2,3)])
    update()
    geometry = Geometry.from_object(bpy.context, obj)
    result = geometry.section((0,0,0), (0,0,1))
    close(result["diameter"], 2)
    assert not result["closed"] and result["warnings"]
    empty = geometry.section((0,0,5), (0,0,1))
    assert empty["diameter"] == 0
    try:
        geometry.section((0,0,0), (0,0,0))
    except ValueError:
        pass
    else:
        raise AssertionError("Zero normal accepted")
    bpy.data.objects.remove(obj, do_unlink=True)


check_box()
check_cylinder()
check_modifier_stack()
check_gn_instances()
check_components_and_hole()
check_open_and_degenerate()
check_calipers()
print("SECTION PASS", bpy.app.version_string, flush=True)
