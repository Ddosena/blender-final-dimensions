"""Lazy evaluated-surface picking across visible mesh objects in a view layer.

``refresh`` invalidates cached BVHs on a depsgraph epoch change. Records and
anchors contain only Python primitives; evaluated Blender RNA is resolved for
each build and never held across UI ticks.
"""

from __future__ import annotations

import hashlib
from collections import OrderedDict

import numpy as np

from .section import Geometry, _append_mesh, _original
from .measure import _nonmesh_components


MAX_RECORDS = 100_000
MAX_CACHED_RECORDS = 32
MAX_CACHED_VERTICES = 700_000
MAX_CACHED_TRIANGLES = 1_400_000


def _visible(obj, context):
    try:
        space = getattr(context, "space_data", None)
        viewport = space if space is not None and space.type == "VIEW_3D" else None
        return obj.visible_get(view_layer=context.view_layer, viewport=viewport)
    except (ReferenceError, RuntimeError):
        return False


def _bounds(obj, matrix):
    """World AABB of the evaluated mesh; None means unknown, not empty."""
    try:
        corners = np.asarray(tuple(tuple(corner) for corner in obj.bound_box), dtype=np.float64)
        transform = np.asarray(matrix, dtype=np.float64)
        if corners.shape != (8, 3) or not np.all(np.isfinite(corners)) or not np.all(np.isfinite(transform)):
            return None
        if np.all(corners == -1):
            return None
        world = corners @ transform[:3, :3].T + transform[:3, 3]
        return (tuple(np.min(world, axis=0)), tuple(np.max(world, axis=0)))
    except (AttributeError, ReferenceError, RuntimeError, TypeError):
        return None


def _ray_box(origin, direction, bounds):
    if bounds is None:
        return (0.0, float("inf"))
    lower, upper = (np.asarray(side, dtype=np.float64) for side in bounds)
    span = float(np.max(upper-lower))
    margin = max(span * 1e-6, 1e-7)
    lower -= margin
    upper += margin
    near, far = 0.0, float("inf")
    for axis in range(3):
        if abs(direction[axis]) <= 1e-15:
            if origin[axis] < lower[axis] or origin[axis] > upper[axis]:
                return None
            continue
        first = (lower[axis]-origin[axis]) / direction[axis]
        second = (upper[axis]-origin[axis]) / direction[axis]
        near = max(near, min(first, second))
        far = min(far, max(first, second))
        if far < near:
            return None
    return near, far


def _barycentric(point, vertices):
    a, b, c = vertices
    u, v, q = b-a, c-a, point-a
    d00, d01, d11 = u@u, u@v, v@v
    divisor = d00*d11-d01*d01
    if abs(divisor) <= 1e-30:
        raise ValueError("Hit triangle is degenerate")
    s = (d11*(q@u)-d01*(q@v))/divisor
    t = (d00*(q@v)-d01*(q@u))/divisor
    weights = np.array((1-s-t, s, t), dtype=np.float64)
    weights = np.clip(weights, 0, 1)
    weights /= weights.sum()
    return tuple(map(float, weights))


def _topology(geometry):
    cached = getattr(geometry, "_surface_topology", None)
    if cached is not None:
        return cached
    digest = hashlib.blake2b(digest_size=16)
    digest.update(np.asarray((len(geometry.vertices), len(geometry.triangles)), dtype=np.int64).tobytes())
    digest.update(geometry.triangles.tobytes())
    geometry._surface_topology = digest.hexdigest()
    return geometry._surface_topology


def _components_warning(evaluated):
    components = _nonmesh_components(evaluated)
    if components is None:
        return "Geometry Nodes non-mesh components could not be inspected."
    if components:
        return "Geometry Nodes non-mesh components omitted: " + ", ".join(sorted(components)) + "."
    return None


class ScenePicker:
    """Pick the frontmost visible evaluated mesh and resolve stable anchors."""

    def __init__(self, context, epoch):
        self.epoch = object()
        self._context_key = None
        self.records = ()
        self._by_key = {}
        self._geometry_cache = OrderedDict()
        self._cache_vertices = 0
        self._cache_triangles = 0
        self.warnings = ()
        self.refresh(context, epoch)

    def refresh(self, context, epoch):
        space = getattr(context, "space_data", None)
        viewport_pointer = space.as_pointer() if space is not None and space.type == "VIEW_3D" else 0
        context_key = (context.scene.as_pointer(), context.view_layer.as_pointer(), viewport_pointer)
        if epoch == self.epoch and context_key == self._context_key:
            return False
        depsgraph = context.evaluated_depsgraph_get()
        visible = {}
        records = []
        keys = set()
        warnings_by_owner = {}
        component_cache = {}
        gn_owners = set()
        for obj in context.view_layer.objects:
            if obj.type != "MESH" or not _visible(obj, context):
                continue
            pointer = obj.as_pointer()
            visible[pointer] = obj
            evaluated = obj.evaluated_get(depsgraph)
            object_warnings = []
            if obj.mode == "EDIT" and any(
                    modifier.show_viewport and not modifier.show_in_editmode
                    for modifier in obj.modifiers):
                object_warnings.append("Some enabled modifiers are hidden in Edit Mode evaluation.")
            if any(modifier.type == "NODES" and modifier.show_viewport for modifier in obj.modifiers):
                gn_owners.add(pointer)
                warning = _components_warning(evaluated)
                if warning:
                    object_warnings.append(warning)
            warnings_by_owner[pointer] = object_warnings
            key = ("OBJECT", pointer)
            keys.add(key)
            records.append({"key": key, "owner": pointer,
                            "owner_uid": int(obj.session_uid), "source": pointer,
                            "name": obj.name, "bounds": _bounds(evaluated, evaluated.matrix_world)})
            if len(records) > MAX_RECORDS:
                raise ValueError("Too many visible mesh objects for surface picking")
        for instance in depsgraph.object_instances:
            if not instance.is_instance or instance.parent is None:
                continue
            parent = _original(instance.parent)
            owner = parent.as_pointer()
            if owner not in visible:
                continue
            source = instance.object
            if source is None or source.type != "MESH":
                warnings_by_owner[owner].append("Some non-mesh or unavailable instances were skipped.")
                continue
            source_pointer = _original(source).as_pointer()
            if owner in gn_owners:
                if source_pointer not in component_cache:
                    component_cache[source_pointer] = _components_warning(source)
                if component_cache[source_pointer]:
                    warnings_by_owner[owner].append(component_cache[source_pointer])
            persistent_id = tuple(int(value) for value in instance.persistent_id)
            key = ("INSTANCE", owner, source_pointer, persistent_id)
            if key in keys:
                raise ValueError("Ambiguous evaluated instance identity")
            keys.add(key)
            records.append({"key": key, "owner": owner,
                            "owner_uid": int(parent.session_uid), "source": source_pointer,
                            "name": parent.name, "bounds": _bounds(source, instance.matrix_world)})
            if len(records) > MAX_RECORDS:
                raise ValueError("Too many evaluated mesh instances for surface picking")
        for record in records:
            record["warnings"] = tuple(dict.fromkeys(warnings_by_owner[record["owner"]]))
        self.records = tuple(records)
        self._by_key = {record["key"]: record for record in records}
        self._geometry_cache.clear()
        self._cache_vertices = 0
        self._cache_triangles = 0
        self.warnings = ()
        self.epoch = epoch
        self._context_key = context_key
        return True

    def _find_source(self, context, record):
        depsgraph = context.evaluated_depsgraph_get()
        owner = self._owner(context, record)
        if record["key"][0] == "OBJECT":
            evaluated = owner.evaluated_get(depsgraph)
            return evaluated, evaluated.matrix_world, depsgraph
        for instance in depsgraph.object_instances:
            if not instance.is_instance or instance.parent is None or instance.object is None:
                continue
            if _original(instance.parent).as_pointer() != record["owner"]:
                continue
            if instance.object.type != "MESH":
                continue
            identity = ("INSTANCE", record["owner"], _original(instance.object).as_pointer(),
                        tuple(int(value) for value in instance.persistent_id))
            if identity == record["key"]:
                return instance.object, instance.matrix_world, depsgraph
        raise ValueError("Anchor instance no longer exists")

    @staticmethod
    def _owner(context, record):
        owner = context.view_layer.objects.get(record["name"])
        if owner is None or owner.as_pointer() != record["owner"] or int(owner.session_uid) != record["owner_uid"]:
            owner = next((obj for obj in context.view_layer.objects
                          if obj.as_pointer() == record["owner"]
                          and int(obj.session_uid) == record["owner_uid"]), None)
        if owner is None or owner.type != "MESH" or not _visible(owner, context):
            raise ValueError("Anchor object was deleted or hidden")
        record["name"] = owner.name
        return owner

    def _geometry(self, context, record):
        key = record["key"]
        if key in self._geometry_cache:
            self._owner(context, record)
            self._geometry_cache.move_to_end(key)
            return self._geometry_cache[key]
        source, matrix, depsgraph = self._find_source(context, record)
        chunks = ([], [], [], [])
        counts = [0, 0]
        try:
            _append_mesh(source, depsgraph, matrix, *chunks, counts)
        except ValueError as exc:
            raise ValueError(f"Cannot pick {record['name']}: {exc}") from exc
        if not counts[1]:
            self._retain(key, None)
            return None
        geometry = Geometry(*(np.concatenate(part, axis=0) for part in chunks))
        self._retain(key, geometry)
        return geometry

    def _retain(self, key, geometry):
        vertices = len(geometry.vertices) if geometry is not None else 0
        triangles = len(geometry.triangles) if geometry is not None else 0
        if vertices > MAX_CACHED_VERTICES or triangles > MAX_CACHED_TRIANGLES:
            return
        while self._geometry_cache and (
                len(self._geometry_cache) >= MAX_CACHED_RECORDS
                or self._cache_vertices + vertices > MAX_CACHED_VERTICES
                or self._cache_triangles + triangles > MAX_CACHED_TRIANGLES):
            _old_key, old = self._geometry_cache.popitem(last=False)
            if old is not None:
                self._cache_vertices -= len(old.vertices)
                self._cache_triangles -= len(old.triangles)
        self._geometry_cache[key] = geometry
        self._cache_vertices += vertices
        self._cache_triangles += triangles

    def pick(self, context, origin, direction):
        origin = np.array(origin, dtype=np.float64, copy=True)
        direction = np.array(direction, dtype=np.float64, copy=True)
        if (origin.shape != (3,) or direction.shape != (3,)
                or not np.all(np.isfinite(origin)) or not np.all(np.isfinite(direction))
                or np.linalg.norm(direction) <= 1e-15):
            raise ValueError("Ray origin and direction must be finite, with nonzero direction")
        direction /= np.linalg.norm(direction)
        candidates = []
        for record in self.records:
            interval = _ray_box(origin, direction, record["bounds"])
            if interval is not None:
                candidates.append((interval[0], record))
        candidates.sort(key=lambda pair: pair[0])
        best = None
        best_distance = float("inf")
        warnings = []
        for near, record in candidates:
            if near > best_distance:
                break
            geometry = self._geometry(context, record)
            if geometry is None:
                warnings.append(f"{record['name']}: evaluated result has no mesh triangles.")
                continue
            hit = geometry.ray_cast(origin, direction)
            if hit is None:
                continue
            point = np.asarray(hit["point"], dtype=np.float64)
            distance = float((point-origin) @ direction)
            if distance < -1e-6 or distance >= best_distance:
                continue
            triangle = hit["triangle"]
            weights = _barycentric(point, geometry.vertices[geometry.triangles[triangle]])
            anchor = {"key": record["key"], "owner_uid": record["owner_uid"],
                      "triangle": triangle, "barycentric": weights,
                      "topology": _topology(geometry)}
            best = {"point": tuple(point), "normal": hit["normal"],
                    "object_name": record["name"], "anchor": anchor,
                    "warnings": tuple(warnings) + record["warnings"]}
            best_distance = distance
        self.warnings = tuple(warnings)
        if best is not None:
            best["warnings"] = self.warnings + self._by_key[best["anchor"]["key"]]["warnings"]
            self.warnings = best["warnings"]
        return best

    def resolve(self, context, anchor):
        try:
            key = tuple(anchor["key"])
            record = self._by_key[key]
            if record["owner_uid"] != anchor["owner_uid"]:
                raise ValueError("Anchor object identity changed")
            triangle = int(anchor["triangle"])
            weights = np.asarray(anchor["barycentric"], dtype=np.float64)
            topology = anchor["topology"]
        except (KeyError, TypeError, IndexError) as exc:
            raise ValueError("Anchor is invalid or its object is unavailable") from exc
        if weights.shape != (3,) or not np.all(np.isfinite(weights)) or abs(weights.sum()-1) > 1e-5:
            raise ValueError("Anchor barycentric coordinates are invalid")
        geometry = self._geometry(context, record)
        if geometry is None or topology != _topology(geometry) or not 0 <= triangle < len(geometry.triangles):
            raise ValueError("Anchor topology changed")
        vertices = geometry.vertices[geometry.triangles[triangle]]
        return tuple(weights @ vertices)
