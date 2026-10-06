"""World-space ray hits and exact polygonal plane sections of evaluated meshes.

The caller owns invalidation: construct a new Geometry after depsgraph changes.
Returned lengths are Blender units. No Blender mesh/RNA references are retained.
"""

from __future__ import annotations

import math
from collections import Counter, defaultdict, deque

import numpy as np
from mathutils import Vector
from mathutils.bvhtree import BVHTree


MAX_VERTICES = 350_000
MAX_TRIANGLES = 700_000
MAX_SEGMENTS = 150_000


def _original(item):
    try:
        return item.original
    except (AttributeError, ReferenceError):
        return item


def _append_mesh(evaluated, depsgraph, matrix, vertices, normals, triangles, smooth_flags, counts):
    mesh = None
    try:
        mesh = evaluated.to_mesh(preserve_all_data_layers=False, depsgraph=depsgraph)
        if mesh is None:
            return
        nv = len(mesh.vertices)
        if counts[0] + nv > MAX_VERTICES:
            raise ValueError("Evaluated mesh exceeds the section vertex limit")
        mesh.calc_loop_triangles()
        nt = len(mesh.loop_triangles)
        if counts[0] + nv > MAX_VERTICES or counts[1] + nt > MAX_TRIANGLES:
            raise ValueError("Evaluated mesh exceeds the section geometry limit")
        if not nv or not nt:
            return
        xyz = np.empty(nv * 3, dtype=np.float32)
        mesh.vertices.foreach_get("co", xyz)
        xyz = xyz.reshape((-1, 3)).astype(np.float64)
        local_normals = np.empty(nv * 3, dtype=np.float32)
        mesh.vertices.foreach_get("normal", local_normals)
        local_normals = local_normals.reshape((-1, 3)).astype(np.float64)
        transform = np.asarray(matrix, dtype=np.float64)
        if not np.all(np.isfinite(transform)):
            raise ValueError("Object transform contains non-finite values")
        linear = transform[:3, :3]
        xyz = xyz @ linear.T + transform[:3, 3]
        if not np.all(np.isfinite(xyz)):
            raise ValueError("Evaluated mesh contains non-finite coordinates")
        try:
            normal_matrix = np.linalg.inv(linear).T
            local_normals = local_normals @ normal_matrix.T
            lengths = np.linalg.norm(local_normals, axis=1)
            local_normals /= np.maximum(lengths[:, None], 1e-30)
        except np.linalg.LinAlgError:
            local_normals[:] = 0
        indices = np.empty(nt * 3, dtype=np.int32)
        mesh.loop_triangles.foreach_get("vertices", indices)
        indices = indices.reshape((-1, 3)) + counts[0]
        polygon_indices = np.empty(nt, dtype=np.int32)
        mesh.loop_triangles.foreach_get("polygon_index", polygon_indices)
        polygon_smooth = np.empty(len(mesh.polygons), dtype=np.bool_)
        mesh.polygons.foreach_get("use_smooth", polygon_smooth)
        vertices.append(xyz)
        normals.append(local_normals)
        triangles.append(indices)
        smooth_flags.append(polygon_smooth[polygon_indices])
        counts[0] += nv
        counts[1] += nt
    finally:
        if mesh is not None:
            evaluated.to_mesh_clear()


def _hull(points):
    """Monotone chain, retaining extreme points only."""
    order = sorted(set((float(x), float(y), i) for i, (x, y) in enumerate(points)))
    # Equal projected coordinates must have one representative.
    unique = []
    for x, y, i in order:
        if not unique or (x, y) != unique[-1][:2]:
            unique.append((x, y, i))
    if len(unique) <= 2:
        return unique

    def cross(a, b, c):
        return (b[0]-a[0])*(c[1]-a[1])-(b[1]-a[1])*(c[0]-a[0])

    lower = []
    for p in unique:
        while len(lower) > 1 and cross(lower[-2], lower[-1], p) <= 0:
            lower.pop()
        lower.append(p)
    upper = []
    for p in reversed(unique):
        while len(upper) > 1 and cross(upper[-2], upper[-1], p) <= 0:
            upper.pop()
        upper.append(p)
    return lower[:-1] + upper[:-1]


def _diameter(hull):
    """Rotating calipers, O(h), returning original point indices."""
    count = len(hull)
    if count == 1:
        return 0.0, hull[0][2], hull[0][2]
    if count == 2:
        a, b = hull
        return math.hypot(a[0]-b[0], a[1]-b[1]), a[2], b[2]

    def area(i, j, k):
        a, b, c = hull[i], hull[j], hull[k]
        return abs((b[0]-a[0])*(c[1]-a[1])-(b[1]-a[1])*(c[0]-a[0]))

    def distance(i, j):
        a, b = hull[i], hull[j]
        return (a[0]-b[0])**2+(a[1]-b[1])**2

    opposite = 1
    best = (-1.0, 0, 1)
    for i in range(count):
        nxt = (i+1) % count
        while area(i, nxt, (opposite+1) % count) > area(i, nxt, opposite):
            opposite = (opposite+1) % count
        for a, b in ((i, opposite), (nxt, opposite)):
            d = distance(a, b)
            if d > best[0]:
                best = (d, hull[a][2], hull[b][2])
    return math.sqrt(best[0]), best[1], best[2]


def cursor_diameter(section, point, normal):
    """Chord from the cursor boundary point toward the section's area centroid.

    The nearest opposite crossing bounds the chord, so a concave contour does
    not bridge an exterior gap. The last endpoint is always the cursor anchor.
    Half remains D/2; it is not the centroid-to-boundary distance for an
    asymmetric section. Original maximum-diameter results are left untouched.
    """
    if not section['closed']:
        raise ValueError("Cursor diameter needs a closed, manifold section")
    segments = np.asarray(section['segments'], dtype=np.float64)
    if len(segments) < 3:
        raise ValueError("Section has no enclosed area")
    normal = np.asarray(normal, dtype=np.float64)
    normal_length = np.linalg.norm(normal)
    point = np.asarray(point, dtype=np.float64)
    if (point.shape != (3,) or normal.shape != (3,)
            or not np.all(np.isfinite(point)) or not np.all(np.isfinite(normal))
            or normal_length <= 1e-15):
        raise ValueError("Cursor point and plane normal must be valid 3D vectors")
    normal = normal / normal_length
    scale = max(float(np.max(np.ptp(segments.reshape(-1, 3), axis=0))), 1e-20)
    eps = max(scale * 1e-8, 1e-12)

    # Recover a consistently oriented ring. Segment endpoints from section()
    # share exact stored tuples, independent of triangulation density/order.
    adjacency = defaultdict(list)
    for a, b in section['segments']:
        adjacency[tuple(a)].append(tuple(b))
        adjacency[tuple(b)].append(tuple(a))
    if any(len(neighbors) != 2 for neighbors in adjacency.values()):
        raise ValueError("Cursor diameter needs one closed contour")
    start = next(iter(adjacency))
    ring = [start]
    previous, current = start, adjacency[start][0]
    while current != start and len(ring) <= len(adjacency):
        ring.append(current)
        neighbors = adjacency[current]
        previous, current = current, (neighbors[0] if neighbors[0] != previous else neighbors[1])
    if len(ring) != len(adjacency) or current != start:
        raise ValueError("Section contour could not be ordered")
    origin = np.asarray(start)
    axis = np.eye(3)[int(np.argmin(np.abs(normal)))]
    u = np.cross(normal, axis)
    u /= np.linalg.norm(u)
    v = np.cross(normal, u)
    relative = np.asarray(ring) - origin
    planar = np.column_stack((relative @ u, relative @ v))
    following = np.roll(planar, -1, axis=0)
    crosses = planar[:, 0] * following[:, 1] - following[:, 0] * planar[:, 1]
    twice_area = float(crosses.sum())
    if abs(twice_area) <= scale * scale * 1e-12:
        raise ValueError("Section has no stable area center")
    center_2d = ((planar + following) * crosses[:, None]).sum(axis=0) / (3 * twice_area)
    center = origin + u * center_2d[0] + v * center_2d[1]

    # Snap only numerical ray-cast error to the actual selected contour.
    delta = segments[:, 1] - segments[:, 0]
    denominators = np.einsum('ij,ij->i', delta, delta)
    t = np.clip(np.einsum('ij,ij->i', point - segments[:, 0], delta)
                / np.maximum(denominators, 1e-30), 0, 1)
    nearest = segments[:, 0] + t[:, None] * delta
    distances = np.linalg.norm(nearest - point, axis=1)
    anchor = nearest[int(np.argmin(distances))]
    if float(distances.min()) > max(scale * 1e-4, 1e-10):
        raise ValueError("Mouse hit is not on the selected section contour")
    direction = center - anchor
    direction -= normal * (direction @ normal)
    length = np.linalg.norm(direction)
    if length <= eps:
        raise ValueError("Cursor is too close to the section center")
    direction /= length
    perpendicular = np.cross(normal, direction)
    # In this basis the cursor ray is (x > 0, y = 0).
    local = segments - anchor
    x = local @ direction
    y = local @ perpendicular
    crossings = []
    for xs, ys in zip(x, y):
        if abs(ys[0]) <= eps and abs(ys[1]) <= eps:
            crossings.extend(float(value) for value in xs if value > eps)
        elif (ys[0] <= eps and ys[1] >= -eps) or (ys[1] <= eps and ys[0] >= -eps):
            divisor = ys[1] - ys[0]
            if abs(divisor) > eps:
                s = -ys[0] / divisor
                if -1e-8 <= s <= 1 + 1e-8:
                    distance = float(xs[0] + np.clip(s, 0, 1) * (xs[1] - xs[0]))
                    if distance > eps:
                        crossings.append(distance)
    if not crossings:
        raise ValueError("No opposite contour intersection in the cursor direction")
    diameter = min(crossings)
    midpoint = anchor + direction * (diameter / 2)
    q = np.array(((midpoint-origin) @ u, (midpoint-origin) @ v))
    # A centroid can lie outside a concave polygon; reject outward rays.
    inside = False
    for a, b in zip(planar, following):
        if (a[1] > q[1]) != (b[1] > q[1]):
            crossing_x = a[0] + (q[1]-a[1]) * (b[0]-a[0]) / (b[1]-a[1])
            if q[0] < crossing_x:
                inside = not inside
    if not inside:
        raise ValueError("Cursor direction leaves the contour; hover another point")
    result = dict(section)
    result.update(diameter=diameter, radius=diameter / 2,
                  endpoints=(tuple(anchor + direction * diameter), tuple(anchor)),
                  anchor=tuple(anchor), center=tuple(center),
                  maximum_diameter=section['diameter'])
    return result


class Geometry:
    def __init__(self, vertices, normals, triangles, smooth_flags=None, warnings=()):
        self.vertices = np.asarray(vertices, dtype=np.float64)
        self.normals = np.asarray(normals, dtype=np.float64)
        self.triangles = np.asarray(triangles, dtype=np.int32)
        self.smooth_flags = (np.asarray(smooth_flags, dtype=np.bool_) if smooth_flags is not None
                             else np.ones(len(self.triangles), dtype=np.bool_))
        self.warnings = tuple(warnings)
        if not len(self.triangles):
            raise ValueError("Evaluated result has no mesh triangles")
        self.bvh = BVHTree.FromPolygons(
            [Vector(v) for v in self.vertices],
            [tuple(map(int, t)) for t in self.triangles],
            all_triangles=True,
        )
        self.scale = float(np.max(np.ptp(self.vertices, axis=0)))
        lower, upper = self.vertices.min(axis=0), self.vertices.max(axis=0)
        self.bounds_center = (lower + upper) / 2
        self.bounds_radius = float(np.linalg.norm(upper - lower) / 2)

    @classmethod
    def from_object(cls, context, obj):
        if obj is None or obj.type != "MESH":
            raise ValueError("Select a mesh object")
        depsgraph = context.evaluated_depsgraph_get()
        evaluated = obj.evaluated_get(depsgraph)
        chunks = ([], [], [], [])
        counts = [0, 0]
        warnings = []
        if obj.mode == "EDIT" and any(m.show_viewport and not m.show_in_editmode for m in obj.modifiers):
            warnings.append("Some enabled modifiers are hidden in Edit Mode evaluation.")
        _append_mesh(evaluated, depsgraph, evaluated.matrix_world, *chunks, counts)
        has_nodes = any(m.type == "NODES" and m.show_viewport for m in obj.modifiers)
        if has_nodes:
            owner = obj.as_pointer()
            skipped = False
            inspected = [evaluated]
            inspected_pointers = {evaluated.as_pointer()}
            for instance in depsgraph.object_instances:
                if not instance.is_instance or instance.parent is None:
                    continue
                if _original(instance.parent).as_pointer() != owner:
                    continue
                source = instance.object
                if source is None or source.type != "MESH":
                    skipped = True
                    continue
                pointer = source.as_pointer()
                if pointer not in inspected_pointers:
                    inspected.append(source)
                    inspected_pointers.add(pointer)
                try:
                    _append_mesh(source, depsgraph, instance.matrix_world, *chunks, counts)
                except (RuntimeError, ReferenceError):
                    skipped = True
            if skipped:
                warnings.append("Some non-mesh or unavailable instances were skipped.")
            inspection_unavailable = False
            omitted = set()
            for inspected_object in inspected:
                if not hasattr(inspected_object, "evaluated_geometry"):
                    inspection_unavailable = True
                    continue
                try:
                    result = inspected_object.evaluated_geometry()
                    omitted.update(name for name, attr in (("curves", "curves"), ("points", "pointcloud"), ("volumes", "volume"), ("grease pencil", "grease_pencil")) if getattr(result, attr, None) is not None)
                except (RuntimeError, ReferenceError):
                    inspection_unavailable = True
            if omitted:
                warnings.append("Geometry Nodes non-mesh components omitted: " + ", ".join(sorted(omitted)) + ".")
            if inspection_unavailable:
                warnings.append("Geometry Nodes non-mesh components could not be inspected.")
        if not counts[1]:
            raise ValueError("Evaluated result has no mesh triangles")
        return cls(*(np.concatenate(part, axis=0) for part in chunks), warnings=warnings)

    def ray_cast(self, origin, direction):
        origin = Vector(origin)
        direction = Vector(direction)
        if not all(math.isfinite(x) for x in (*origin, *direction)) or direction.length <= 1e-15:
            raise ValueError("Ray origin and direction must be finite and direction nonzero")
        direction.normalize()
        # Orthographic view rays may begin thousands of units from a mm-sized
        # part. Rebase along the same ray in float64 before the float32 BVH,
        # otherwise hit coordinates can drift visibly off the actual surface.
        ray_direction = np.asarray(direction, dtype=np.float64)
        ray_direction /= np.linalg.norm(ray_direction)
        ray_origin = np.asarray(origin, dtype=np.float64)
        near = float((self.bounds_center - ray_origin) @ ray_direction
                     - self.bounds_radius * 1.05)
        if near > 0:
            origin = Vector(ray_origin + ray_direction * near)
        location, face_normal, triangle, _distance = self.bvh.ray_cast(origin, direction)
        if location is None:
            return None
        tri = self.triangles[triangle]
        pts = self.vertices[tri]
        a, b, c = pts
        v0, v1, v2 = b-a, c-a, np.asarray(location)-a
        d00, d01, d11 = v0@v0, v0@v1, v1@v1
        denominator = d00*d11-d01*d01
        smooth = np.asarray(face_normal, dtype=np.float64)
        if self.smooth_flags[triangle] and abs(denominator) > 1e-30:
            u = (d11*(v2@v0)-d01*(v2@v1))/denominator
            v = (d00*(v2@v1)-d01*(v2@v0))/denominator
            candidate = (1-u-v)*self.normals[tri[0]] + u*self.normals[tri[1]] + v*self.normals[tri[2]]
            norm = np.linalg.norm(candidate)
            if norm > 1e-12:
                smooth = candidate/norm
                if smooth @ np.asarray(face_normal) < 0:
                    smooth = -smooth
        return {"point": tuple(location), "normal": tuple(smooth), "triangle": int(triangle)}

    def section(self, point, normal, hit_triangle=None):
        """Slice evaluated triangles and measure the nearest connected contour.

        A hole is an independent contour: the selected contour is the one
        nearest the hit point (preferably on ``hit_triangle``). ``diameter`` is
        its maximum planar Euclidean point separation, not curvature radius.
        """
        point = np.asarray(point, dtype=np.float64)
        normal = np.asarray(normal, dtype=np.float64)
        if point.shape != (3,) or normal.shape != (3,) or not np.all(np.isfinite(point)) or not np.all(np.isfinite(normal)):
            raise ValueError("Section point and normal must be finite 3D vectors")
        length = np.linalg.norm(normal)
        if length <= 1e-15:
            raise ValueError("Section normal must be nonzero")
        normal /= length
        if hit_triangle is not None and not 0 <= hit_triangle < len(self.triangles):
            raise ValueError("hit_triangle is outside the evaluated triangle range")
        eps = max(self.scale * 1e-9, 1e-10)
        distances = (self.vertices-point) @ normal
        triangle_distances = distances[self.triangles]
        candidates = np.flatnonzero((triangle_distances.min(axis=1) <= eps) & (triangle_distances.max(axis=1) >= -eps))
        if len(candidates) > MAX_SEGMENTS * 2:
            raise ValueError("Section intersects too many triangles")
        regular = []
        coplanar = Counter()
        coordinates = {}
        triangle_edges = defaultdict(list)
        def key(v):
            k = tuple(np.rint(v / eps).astype(np.int64))
            coordinates.setdefault(k, tuple(map(float, v)))
            return k
        for ti in candidates:
            ids = self.triangles[ti]
            ds = distances[ids]
            vs = self.vertices[ids]
            on = np.abs(ds) <= eps
            if np.all(on):
                for a, b in ((0, 1), (1, 2), (2, 0)):
                    ka, kb = key(vs[a]), key(vs[b])
                    if ka != kb:
                        pair = tuple(sorted((ka, kb)))
                        coplanar[pair] += 1
                        triangle_edges[int(ti)].append(pair)
                continue
            intersections = []
            for a, b in ((0, 1), (1, 2), (2, 0)):
                if on[a]:
                    intersections.append(key(vs[a]))
                if ds[a] * ds[b] < -eps*eps and not on[a] and not on[b]:
                    t = ds[a]/(ds[a]-ds[b])
                    intersections.append(key(vs[a] + t*(vs[b]-vs[a])))
                if on[a] and on[b]:
                    intersections.append(key(vs[b]))
            unique = list(dict.fromkeys(intersections))
            if len(unique) == 2 and unique[0] != unique[1]:
                pair = tuple(sorted(unique))
                regular.append(pair)
                triangle_edges[int(ti)].append(pair)
        edges = set(regular)
        edges.update(pair for pair, count in coplanar.items() if count == 1)
        if len(edges) > MAX_SEGMENTS:
            raise ValueError("Section has too many segments")
        if not edges:
            return {"segments": (), "diameter": 0.0, "radius": 0.0, "endpoints": (tuple(point), tuple(point)), "closed": False, "warnings": ("Plane does not form a measurable contour.",)}
        adjacency = defaultdict(set)
        for a, b in edges:
            adjacency[a].add(b)
            adjacency[b].add(a)
        components = []
        seen = set()
        for start in adjacency:
            if start in seen:
                continue
            queue = deque([start]); seen.add(start); nodes = set()
            while queue:
                current = queue.popleft(); nodes.add(current)
                for neighbor in adjacency[current]:
                    if neighbor not in seen:
                        seen.add(neighbor); queue.append(neighbor)
            components.append(nodes)
        preferred = set()
        if hit_triangle is not None:
            for a, b in triangle_edges.get(int(hit_triangle), ()):
                preferred.update((a, b))
        component_by_node = {node: index for index, nodes in enumerate(components) for node in nodes}
        scores = [float("inf")] * len(components)
        # One scan of all edges; point-to-segment distance keeps sparse contours
        # selectable even when the hit lies midway along a long section edge.
        for a, b in edges:
            x, y = np.asarray(coordinates[a]), np.asarray(coordinates[b])
            delta = y-x
            t = np.clip(((point-x)@delta)/max(delta@delta, 1e-30), 0, 1)
            distance = float(np.linalg.norm(point-(x+t*delta)))
            index = component_by_node[a]
            scores[index] = min(scores[index], distance)
        preferred_indices = {component_by_node[node] for node in preferred if node in component_by_node}
        pool = preferred_indices or range(len(components))
        chosen = components[min(pool, key=lambda index: scores[index])]
        selected = [(a, b) for a, b in edges if a in chosen]
        warnings = []
        closed = all(len(adjacency[node]) == 2 for node in chosen)
        if not closed:
            warnings.append("Section contour is open or non-manifold.")
        axis = np.eye(3)[int(np.argmin(np.abs(normal)))]
        u = np.cross(normal, axis); u /= np.linalg.norm(u)
        v = np.cross(normal, u)
        keys = list(chosen)
        planar = np.asarray([((np.asarray(coordinates[k])-point)@u, (np.asarray(coordinates[k])-point)@v) for k in keys])
        diameter, i, j = _diameter(_hull(planar))
        return {"segments": tuple((coordinates[a], coordinates[b]) for a, b in selected),
                "diameter": diameter, "radius": diameter/2,
                "endpoints": (coordinates[keys[i]], coordinates[keys[j]]),
                "closed": closed, "warnings": tuple(warnings)}
