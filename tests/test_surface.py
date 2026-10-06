"""Run with blender -b --factory-startup --python tests/test_surface.py."""
import sys
from pathlib import Path

import bpy

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from final_dimensions import section
from final_dimensions import surface
from final_dimensions.surface import ScenePicker


for existing in tuple(bpy.data.objects):
    bpy.data.objects.remove(existing, do_unlink=True)


def close(value, expected, tolerance=1e-4):
    assert abs(value-expected) <= tolerance, (value, expected)


def update():
    bpy.context.view_layer.update()


def cube(name, location):
    bpy.ops.mesh.primitive_cube_add(location=location)
    obj = bpy.context.object
    obj.name = name
    return obj


def check_objects_and_anchors():
    back = cube("back", (0, 0, 0))
    front = cube("front", (0, 0, 4))
    front.modifiers.new("final subdivision", "SUBSURF").levels = 2
    off_ray = cube("off_ray", (100, 0, 10))
    update()
    picker = ScenePicker(bpy.context, 1)
    assert not picker._geometry_cache
    hit = picker.pick(bpy.context, (0, 0, 20), (0, 0, -1))
    assert hit and hit["object_name"] == "front", hit
    assert 4 < hit["point"][2] < 4.95, hit  # below the source cube's cage top (5)
    assert len(picker._geometry_cache) == 1, picker._geometry_cache.keys()
    cached = next(iter(picker._geometry_cache.values()))
    assert picker.pick(bpy.context, (0, 0, 20), (0, 0, -1))["object_name"] == "front"
    assert next(iter(picker._geometry_cache.values())) is cached
    assert not picker.refresh(bpy.context, 1)
    anchor = hit["anchor"]
    close(picker.resolve(bpy.context, anchor)[2], hit["point"][2])
    assert hasattr(cached, "_surface_topology")

    front.name = "renamed_front"
    update()
    picker.refresh(bpy.context, 2)
    close(picker.resolve(bpy.context, anchor)[2], hit["point"][2])
    assert picker.pick(bpy.context, (0, 0, 20), (0, 0, -1))["object_name"] == "renamed_front"

    # The point follows the same evaluated triangle under object transform.
    front.location.x = 3
    update()
    assert picker.refresh(bpy.context, 3)
    moved = picker.resolve(bpy.context, anchor)
    close(moved[0], hit["point"][0]+3)
    close(moved[2], hit["point"][2])
    assert picker.pick(bpy.context, (0, 0, 20), (0, 0, -1))["object_name"] == "back"

    # Vertex deformation preserves topology and the barycentric anchor.
    for vertex in front.data.vertices:
        vertex.co.z += .4
    update()
    picker.refresh(bpy.context, 4)
    deformed = picker.resolve(bpy.context, anchor)
    close(deformed[2], moved[2]+.4)

    front.modifiers[0].levels = 3
    update()
    picker.refresh(bpy.context, 5)
    try:
        picker.resolve(bpy.context, anchor)
    except ValueError as exc:
        assert "topology" in str(exc).lower()
    else:
        raise AssertionError("Topology change retained anchor")

    off_ray.hide_set(True)
    update()
    picker.refresh(bpy.context, 6)
    assert picker.pick(bpy.context, (100, 0, 20), (0, 0, -1)) is None
    front.hide_set(True)
    update()
    picker.refresh(bpy.context, 7)
    try:
        picker.resolve(bpy.context, anchor)
    except ValueError:
        pass
    else:
        raise AssertionError("Hidden object retained anchor")
    front.hide_set(False)
    bpy.data.objects.remove(front, do_unlink=True)
    update()
    picker.refresh(bpy.context, 8)
    try:
        picker.resolve(bpy.context, anchor)
    except ValueError:
        pass
    else:
        raise AssertionError("Deleted object retained anchor")
    bpy.data.objects.remove(back, do_unlink=True)
    bpy.data.objects.remove(off_ray, do_unlink=True)


def check_transformed_bounds():
    obj = cube("negative_scale", (20, 0, 0))
    obj.scale = (-2, .5, 3)
    obj.rotation_euler.z = .4
    update()
    picker = ScenePicker(bpy.context, 8)
    hit = picker.pick(bpy.context, (20, 0, 20), (0, 0, -1))
    assert hit and hit["object_name"] == "negative_scale"
    close(hit["point"][2], 3)
    bpy.data.objects.remove(obj, do_unlink=True)


def check_gn_instance():
    owner = cube("instance_owner", (0, 0, 0))
    group = bpy.data.node_groups.new("picker instances", "GeometryNodeTree")
    group.interface.new_socket(name="Geometry", in_out="OUTPUT", socket_type="NodeSocketGeometry")
    nodes, links = group.nodes, group.links
    shape = nodes.new("GeometryNodeMeshCube")
    shape.inputs["Size"].default_value = (2, 4, 6)
    line = nodes.new("GeometryNodeMeshLine")
    line.inputs["Count"].default_value = 2
    line.inputs["Offset"].default_value = (10, 0, 0)
    instance = nodes.new("GeometryNodeInstanceOnPoints")
    links.new(line.outputs["Mesh"], instance.inputs["Points"])
    links.new(shape.outputs["Mesh"], instance.inputs["Instance"])
    outer_line = nodes.new("GeometryNodeMeshLine")
    outer_line.inputs["Count"].default_value = 2
    outer_line.inputs["Offset"].default_value = (0, 20, 0)
    outer_instance = nodes.new("GeometryNodeInstanceOnPoints")
    links.new(outer_line.outputs["Mesh"], outer_instance.inputs["Points"])
    links.new(instance.outputs["Instances"], outer_instance.inputs["Instance"])
    output = nodes.new("NodeGroupOutput")
    links.new(outer_instance.outputs["Instances"], output.inputs["Geometry"])
    owner.modifiers.new("GN", "NODES").node_group = group
    update()
    picker = ScenePicker(bpy.context, 10)
    hit = picker.pick(bpy.context, (10, 0, 20), (0, 0, -1))
    assert hit and hit["object_name"] == "instance_owner", hit
    close(hit["point"][2], 3)
    anchor = hit["anchor"]
    assert anchor["key"][0] == "INSTANCE", anchor
    nested_hit = picker.pick(bpy.context, (10, 20, 20), (0, 0, -1))
    assert nested_hit and nested_hit["anchor"]["key"] != anchor["key"]
    owner.location.x = 5
    update()
    picker.refresh(bpy.context, 11)
    close(picker.resolve(bpy.context, anchor)[0], 15)
    join = nodes.new("GeometryNodeJoinGeometry")
    curve = nodes.new("GeometryNodeCurvePrimitiveLine")
    links.new(outer_instance.outputs["Instances"], join.inputs["Geometry"])
    links.new(curve.outputs["Curve"], join.inputs["Geometry"])
    links.new(join.outputs["Geometry"], output.inputs["Geometry"])
    update()
    picker.refresh(bpy.context, 12)
    mixed = picker.pick(bpy.context, (15, 0, 20), (0, 0, -1))
    assert mixed and any("non-mesh" in warning for warning in mixed["warnings"]), mixed
    bpy.data.objects.remove(owner, do_unlink=True)


def check_edit_warning():
    obj = cube("edit_warning", (0, 0, 0))
    modifier = obj.modifiers.new("hidden in edit", "BEVEL")
    modifier.width = .2
    modifier.show_in_editmode = False
    bpy.ops.object.mode_set(mode="EDIT")
    update()
    picker = ScenePicker(bpy.context, 15)
    hit = picker.pick(bpy.context, (0, 0, 10), (0, 0, -1))
    assert hit and any("Edit Mode" in warning for warning in hit["warnings"]), hit
    bpy.ops.object.mode_set(mode="OBJECT")
    bpy.data.objects.remove(obj, do_unlink=True)


def check_limit_and_cleanup():
    obj = cube("limited", (0, 0, 0))
    update()
    picker = ScenePicker(bpy.context, 20)
    original = section.MAX_VERTICES
    try:
        section.MAX_VERTICES = 4
        try:
            picker.pick(bpy.context, (0, 0, 10), (0, 0, -1))
        except ValueError as exc:
            assert "vertex limit" in str(exc)
        else:
            raise AssertionError("Oversized front mesh was ignored")
    finally:
        section.MAX_VERTICES = original
    assert picker.pick(bpy.context, (0, 0, 10), (0, 0, -1)) is not None
    bpy.data.objects.remove(obj, do_unlink=True)


def check_cache_budget():
    left = cube("cache_left", (-10, 0, 0))
    right = cube("cache_right", (10, 0, 0))
    update()
    picker = ScenePicker(bpy.context, 30)
    limits = (surface.MAX_CACHED_VERTICES, surface.MAX_CACHED_TRIANGLES,
              surface.MAX_CACHED_RECORDS)
    try:
        surface.MAX_CACHED_VERTICES = 8
        surface.MAX_CACHED_TRIANGLES = 12
        surface.MAX_CACHED_RECORDS = 2
        assert picker.pick(bpy.context, (-10, 0, 10), (0, 0, -1))["object_name"] == "cache_left"
        left_key = next(iter(picker._geometry_cache))
        assert picker.pick(bpy.context, (10, 0, 10), (0, 0, -1))["object_name"] == "cache_right"
        assert len(picker._geometry_cache) == 1 and left_key not in picker._geometry_cache
        assert picker._cache_vertices == 8 and picker._cache_triangles == 12
    finally:
        (surface.MAX_CACHED_VERTICES, surface.MAX_CACHED_TRIANGLES,
         surface.MAX_CACHED_RECORDS) = limits
    bpy.data.objects.remove(left, do_unlink=True)
    bpy.data.objects.remove(right, do_unlink=True)


check_objects_and_anchors()
check_transformed_bounds()
check_gn_instance()
check_edit_warning()
check_limit_and_cleanup()
check_cache_budget()
print("SURFACE PASS", bpy.app.version_string, flush=True)
