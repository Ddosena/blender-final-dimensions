"""World-axis bounds of a mesh cage and its evaluated result.

All returned distances are in Blender scene units. The UI converts these to
physical metric lengths using scene.unit_settings.scale_length.
"""

from __future__ import annotations

import bmesh
import bpy
import numpy as np
from mathutils import Matrix, Vector


_CHUNK_VERTICES = 65_536


def _bounds_from_mesh(mesh, matrix):
    """Return world-space AABB as two tuples, or None for an empty mesh."""
    count = len(mesh.vertices)
    if not count:
        return None

    coordinates = np.empty(count * 3, dtype=np.float32)
    mesh.vertices.foreach_get("co", coordinates)
    coordinates = coordinates.reshape((count, 3))
    transform = np.asarray(matrix, dtype=np.float64)
    rotation = transform[:3, :3]
    offset = transform[:3, 3]
    lower = np.full(3, np.inf)
    upper = np.full(3, -np.inf)

    for start in range(0, count, _CHUNK_VERTICES):
        world = coordinates[start : start + _CHUNK_VERTICES] @ rotation.T + offset
        lower = np.minimum(lower, world.min(axis=0))
        upper = np.maximum(upper, world.max(axis=0))
    return tuple(lower.tolist()), tuple(upper.tolist())


def _bounds_from_edit_mesh(mesh, matrix):
    edit_mesh = bmesh.from_edit_mesh(mesh)
    if not edit_mesh.verts:
        return None
    lower = [float("inf")] * 3
    upper = [float("-inf")] * 3
    for vertex in edit_mesh.verts:
        point = matrix @ vertex.co
        for axis in range(3):
            lower[axis] = min(lower[axis], point[axis])
            upper[axis] = max(upper[axis], point[axis])
    return tuple(lower), tuple(upper)


def _union(left, right):
    if left is None:
        return right
    if right is None:
        return left
    return (
        tuple(min(a, b) for a, b in zip(left[0], right[0])),
        tuple(max(a, b) for a, b in zip(left[1], right[1])),
    )


def _size(bounds):
    if bounds is None:
        return None
    return tuple(max(0.0, hi - lo) for lo, hi in zip(*bounds))


def _original(item):
    try:
        return item.original
    except (AttributeError, ReferenceError):
        return item


def _mesh_bounds_for_object(evaluated_object, depsgraph, matrix):
    mesh = None
    try:
        mesh = evaluated_object.to_mesh(
            preserve_all_data_layers=False, depsgraph=depsgraph
        )
        if mesh is None:
            return None
        return _bounds_from_mesh(mesh, matrix)
    finally:
        if mesh is not None:
            evaluated_object.to_mesh_clear()


def _nonmesh_components(evaluated_object):
    """Return present non-mesh GeometrySet components, or None if unavailable."""
    if not hasattr(evaluated_object, "evaluated_geometry"):
        return None
    try:
        geometry = evaluated_object.evaluated_geometry()
        return {
            name
            for name, attribute in (
                ("curves", "curves"),
                ("points", "pointcloud"),
                ("volumes", "volume"),
                ("grease pencil", "grease_pencil"),
            )
            if getattr(geometry, attribute, None) is not None
        }
    except (RuntimeError, ReferenceError):
        return None


def _measure_evaluated(context, obj, basis=None):
    """Evaluate once and reduce actual vertices in a chosen world-space basis."""
    warnings = []
    result = {
        "cage": None,
        "final": None,
        "minimum": None,
        "maximum": None,
        "difference": None,
        "percent": None,
        "half": None,
        "warnings": warnings,
    }
    if obj is None or obj.type != "MESH":
        warnings.append("Select a mesh object.")
        return result

    if obj.mode == "EDIT":
        if any(
            modifier.show_viewport and not modifier.show_in_editmode
            for modifier in obj.modifiers
        ):
            warnings.append(
                "Some enabled modifiers are hidden in Edit Mode evaluation."
            )
    depsgraph = context.evaluated_depsgraph_get()
    evaluated_object = obj.evaluated_get(depsgraph)
    transform = lambda matrix: basis @ matrix if basis is not None else matrix
    final_bounds = _mesh_bounds_for_object(
        evaluated_object, depsgraph, transform(evaluated_object.matrix_world)
    )

    # Object instances emitted by Geometry Nodes are separate depsgraph entries.
    # A mesh component alone would otherwise under-report the visible result.
    has_nodes = any(
        modifier.type == "NODES" and modifier.show_viewport
        for modifier in obj.modifiers
    )
    skipped_instances = False
    if has_nodes:
        omitted = _nonmesh_components(evaluated_object)
        inspection_unavailable = omitted is None
        omitted = set() if omitted is None else omitted
        inspected_objects = {evaluated_object.as_pointer()}
        owner_pointer = obj.as_pointer()
        for instance in depsgraph.object_instances:
            if not instance.is_instance or instance.parent is None:
                continue
            parent = _original(instance.parent)
            if parent.as_pointer() != owner_pointer:
                continue
            instance_object = instance.object
            if instance_object is None:
                skipped_instances = True
                continue
            if instance_object.type != "MESH":
                skipped_instances = True
                continue
            instance_pointer = instance_object.as_pointer()
            if instance_pointer not in inspected_objects:
                inspected_objects.add(instance_pointer)
                components = _nonmesh_components(instance_object)
                if components is None:
                    inspection_unavailable = True
                else:
                    omitted.update(components)
            try:
                bounds = _mesh_bounds_for_object(
                    instance_object, depsgraph, transform(instance.matrix_world)
                )
                final_bounds = _union(final_bounds, bounds)
            except (RuntimeError, ValueError, ReferenceError):
                skipped_instances = True
        if omitted:
            warnings.append(
                "Geometry Nodes non-mesh components omitted: "
                + ", ".join(sorted(omitted))
                + "."
            )
        if inspection_unavailable:
            warnings.append("Geometry Nodes non-mesh components could not be inspected.")
        if skipped_instances:
            warnings.append("Some non-mesh or unavailable instances were skipped.")

    if final_bounds is None:
        warnings.append("Evaluated result has no measurable mesh vertices.")
        return result

    result["minimum"], result["maximum"] = final_bounds
    final = _size(final_bounds)
    result["final"] = final
    result["half"] = tuple(span / 2.0 for span in final)
    return result


def measure_object(context, obj):
    """Measure control cage and evaluated geometry along world X/Y/Z."""
    result = _measure_evaluated(context, obj)
    if obj is None or obj.type != "MESH":
        return result
    cage_bounds = (
        _bounds_from_edit_mesh(obj.data, obj.matrix_world)
        if obj.mode == "EDIT"
        else _bounds_from_mesh(obj.data, obj.matrix_world)
    )
    result["cage"] = _size(cage_bounds)
    if cage_bounds is None:
        result["warnings"].append("Control cage has no vertices.")
    final = result["final"]
    if final is None:
        return result
    if result["cage"] is not None:
        cage = result["cage"]
        result["difference"] = tuple(f - c for f, c in zip(final, cage))
        result["percent"] = tuple(
            (f - c) / c * 100.0 if c != 0.0 else None
            for f, c in zip(final, cage)
        )
        if any(value is None for value in result["percent"]):
            result["warnings"].append("Percent is undefined on zero-size cage axes.")
    return result


def measure_direction(context, obj, direction):
    """Full evaluated mesh span projected onto a world-space edge direction.

    This is max(dot(vertex, unit_direction)) - min(dot(...)), not a world
    bounding-box diagonal and not the length of a deformed edge. Object scale,
    rotation, shear, and Geometry Nodes mesh instances use their world matrices.
    """
    axis = Vector(direction)
    if not all(np.isfinite(value) for value in axis) or axis.length <= 1e-12:
        return {"span": None, "warnings": ["Edge has no measurable direction."]}
    axis.normalize()
    # Only the first coordinate is needed; other rows keep a valid basis for
    # the shared exact vertex reducer. No Blender object is transformed.
    second = axis.orthogonal().normalized()
    third = axis.cross(second).normalized()
    basis = Matrix(((*axis, 0.0), (*second, 0.0), (*third, 0.0), (0, 0, 0, 1)))
    result = _measure_evaluated(context, obj, basis)
    return {
        "span": result["final"][0] if result["final"] is not None else None,
        "warnings": result["warnings"],
    }


def format_length(scene, value, precision=3):
    """Format a world-space Blender length using the scene's chosen unit."""
    if value is None:
        return "—"
    settings = scene.unit_settings
    system = settings.system
    if system == "NONE":
        return f"{float(value):.{precision}f} BU"

    scale = settings.scale_length
    meters = float(value) * (scale if scale > 0.0 else 1.0)
    length_unit = settings.length_unit
    fixed_units = {
        "METERS": (1.0, "m"),
        "CENTIMETERS": (0.01, "cm"),
        "MILLIMETERS": (0.001, "mm"),
        "KILOMETERS": (1000.0, "km"),
        "MICROMETERS": (0.000001, "µm"),
        "MILES": (1609.344, "mi"),
        "FEET": (0.3048, "ft"),
        "INCHES": (0.0254, "in"),
        "THOU": (0.0000254, "thou"),
    }
    if length_unit in fixed_units:
        divisor, suffix = fixed_units[length_unit]
        return f"{meters / divisor:.{precision}f} {suffix}"
    return bpy.utils.units.to_string(
        system, "LENGTH", meters, precision=precision
    )
