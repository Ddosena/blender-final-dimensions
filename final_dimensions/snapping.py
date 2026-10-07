"""Viewport feature picking with bounded, source-specific primitive snapshots.

Projection arrays live for one query only. The inherited LRU is shared between
surface picking, original cages and evaluated geometry, including instances.
"""
from __future__ import annotations

import hashlib

import numpy as np

from .section import Geometry, MAX_VERTICES, MAX_TRIANGLES
from .surface import ScenePicker, _barycentric, _ray_box
from .vertex_tracking import TRACKER

SUPPORTED_ELEMENTS = frozenset({'VERTEX', 'EDGE', 'FACE', 'FACE_PROJECT', 'EDGE_MIDPOINT'})
SNAP_RADIUS = 14.0
PIXEL_TIE_PRECISION = .01
MAX_EDGES = 700_000


class _Features(Geometry):
    def __init__(self, vertices, normals, triangles, smooth, edges, face_topology, hidden, selected):
        if len(triangles):
            super().__init__(vertices, normals, triangles, smooth)
        else:
            self.vertices, self.normals, self.triangles = vertices, normals, triangles
            self.bvh = None
            self.scale = float(np.max(np.ptp(vertices, axis=0))) if len(vertices) else 0.
        self.edges = edges
        self.hidden, self.selected = hidden, selected
        # Include actual edges: a wire topology change must invalidate wire anchors.
        digest = hashlib.blake2b(digest_size=16)
        for array in (np.asarray((len(vertices), len(edges), len(triangles)), dtype=np.int64), edges, triangles):
            digest.update(array.tobytes())
        self.feature_topology = digest.hexdigest()
        # Triangulation can flip while a polygon deforms. Vertex indices remain
        # meaningful only while the actual mesh edge/face connectivity stays.
        digest = hashlib.blake2b(digest_size=16)
        for array in (np.asarray((len(vertices), len(edges)), dtype=np.int64), edges):
            digest.update(array.tobytes())
        digest.update(face_topology)
        self.vertex_topology = digest.hexdigest()

    def ray_cast(self, origin, direction):
        return super().ray_cast(origin, direction) if self.bvh is not None else None


def _snapshot(mesh, matrix, edit=False):
    """Copy RNA/BMesh completely before its lifetime ends, keeping real edges."""
    if edit:
        mesh.verts.ensure_lookup_table()
        mesh.edges.ensure_lookup_table()
        mesh.faces.ensure_lookup_table()
        mesh.verts.index_update()
        mesh.edges.index_update()
        mesh.faces.index_update()
        nv = len(mesh.verts)
        if nv > MAX_VERTICES:
            raise ValueError('Mesh exceeds the snap vertex limit')
        if len(mesh.edges) > MAX_EDGES:
            raise ValueError('Mesh exceeds the snap edge limit')
        # Faces tessellate only for ray hits; edge snapping uses mesh.edges.
        tessellation = mesh.calc_loop_triangles()
        if len(tessellation) > MAX_TRIANGLES:
            raise ValueError('Mesh exceeds the snap triangle limit')
        xyz = np.asarray([tuple(v.co) for v in mesh.verts], dtype=np.float64).reshape(-1, 3)
        normals = np.asarray([tuple(v.normal) for v in mesh.verts], dtype=np.float64).reshape(-1, 3)
        edges = np.asarray([[v.index for v in e.verts] for e in mesh.edges], dtype=np.int32).reshape(-1, 2)
        triangles = np.asarray([[l.vert.index for l in tri] for tri in tessellation], dtype=np.int32).reshape(-1, 3)
        face_digest = hashlib.blake2b(digest_size=16)
        for face in mesh.faces:
            face_digest.update(np.asarray((len(face.verts), *(vert.index for vert in face.verts)),
                                          dtype=np.int32).tobytes())
        smooth = np.asarray([tri[0].face.smooth for tri in tessellation], dtype=np.bool_)
        hidden = (np.asarray([v.hide for v in mesh.verts]), np.asarray([e.hide for e in mesh.edges]),
                  np.asarray([tri[0].face.hide for tri in tessellation]))
        selected = (np.asarray([v.select for v in mesh.verts]), np.asarray([e.select for e in mesh.edges]),
                    np.asarray([tri[0].face.select for tri in tessellation]))
    else:
        nv = len(mesh.vertices)
        if nv > MAX_VERTICES:
            raise ValueError('Mesh exceeds the snap vertex limit')
        if len(mesh.edges) > MAX_EDGES:
            raise ValueError('Mesh exceeds the snap edge limit')
        mesh.calc_loop_triangles()
        nt = len(mesh.loop_triangles)
        if nt > MAX_TRIANGLES:
            raise ValueError('Mesh exceeds the snap triangle limit')
        xyz = np.empty(nv * 3, dtype=np.float32)
        normals = np.empty(nv * 3, dtype=np.float32)
        edges = np.empty(len(mesh.edges) * 2, dtype=np.int32)
        triangles = np.empty(nt * 3, dtype=np.int32)
        mesh.vertices.foreach_get('co', xyz)
        mesh.vertices.foreach_get('normal', normals)
        mesh.edges.foreach_get('vertices', edges)
        mesh.loop_triangles.foreach_get('vertices', triangles)
        xyz, normals = xyz.reshape(-1, 3).astype(np.float64), normals.reshape(-1, 3).astype(np.float64)
        edges, triangles = edges.reshape(-1, 2), triangles.reshape(-1, 3)
        totals = np.empty(len(mesh.polygons), dtype=np.int32)
        loops = np.empty(len(mesh.loops), dtype=np.int32)
        mesh.polygons.foreach_get('loop_total', totals)
        mesh.loops.foreach_get('vertex_index', loops)
        face_digest = hashlib.blake2b(digest_size=16)
        face_digest.update(totals.tobytes())
        face_digest.update(loops.tobytes())
        polygon = np.empty(nt, dtype=np.int32)
        mesh.loop_triangles.foreach_get('polygon_index', polygon)
        flags = []
        for attribute in ('use_smooth', 'hide', 'select'):
            values = np.empty(len(mesh.polygons), dtype=np.bool_)
            mesh.polygons.foreach_get(attribute, values)
            flags.append(values[polygon])
        smooth = flags[0]
        hidden, selected = [], []
        for collection in (mesh.vertices, mesh.edges):
            for attribute, destination in (('hide', hidden), ('select', selected)):
                values = np.empty(len(collection), dtype=np.bool_)
                collection.foreach_get(attribute, values)
                destination.append(values)
        hidden, selected = (*hidden, flags[1]), (*selected, flags[2])
    matrix = np.asarray(matrix, dtype=np.float64)
    if not np.all(np.isfinite(matrix)):
        raise ValueError('Object transform contains non-finite values')
    xyz = xyz @ matrix[:3, :3].T + matrix[:3, 3]
    if not np.all(np.isfinite(xyz)):
        raise ValueError('Mesh contains non-finite coordinates')
    try:
        normals = normals @ np.linalg.inv(matrix[:3, :3])
        normals /= np.maximum(np.linalg.norm(normals, axis=1)[:, None], 1e-30)
    except np.linalg.LinAlgError:
        normals[:] = 0
    return _Features(xyz, normals, triangles, smooth, edges, face_digest.digest(),
                     tuple(np.asarray(v, dtype=np.bool_) for v in hidden),
                     tuple(np.asarray(v, dtype=np.bool_) for v in selected))


def _projection(vertices, matrix, region):
    homogeneous = np.column_stack((vertices, np.ones(len(vertices)))) @ matrix.T
    w = homogeneous[:, 3]
    ndc = homogeneous[:, :3] / np.where(abs(w) > 1e-30, w, 1e-30)[:, None]
    pixel = (ndc[:, :2] + 1) * np.array((region.width, region.height)) / 2
    valid = (w > 1e-12) & (ndc[:, 2] >= -1) & (ndc[:, 2] <= 1)
    return homogeneous, pixel, valid


def _bounds_candidate(bounds, matrix, region, coordinate):
    if bounds is None:
        return True
    low, high = bounds
    corners = np.array([(x,y,z) for x in (low[0], high[0])
                        for y in (low[1], high[1]) for z in (low[2], high[2])])
    homogeneous, pixel, _valid = _projection(corners, matrix, region)
    # A box crossing the camera/near plane can enclose a projected region
    # larger than its corner rectangle. Keep it conservatively in that case.
    if np.any(homogeneous[:, 3] <= 1e-12):
        return True
    near = homogeneous[:, 2]+homogeneous[:, 3]
    far = homogeneous[:, 3]-homogeneous[:, 2]
    if np.all(near < 0) or np.all(far < 0):
        return False
    if np.any(near < 0):
        return True
    return bool(np.all(coordinate >= pixel.min(axis=0)-SNAP_RADIUS)
                and np.all(coordinate <= pixel.max(axis=0)+SNAP_RADIUS))


def _front_flags(geometry, camera, perspective, direction):
    """A silhouette vertex/edge is front-facing if any incident face is."""
    tri = geometry.triangles
    vertex = np.ones(len(geometry.vertices), dtype=np.bool_)
    edge = np.ones(len(geometry.edges), dtype=np.bool_)
    if not len(tri):
        return vertex, edge, np.empty(0, dtype=np.bool_)
    points = geometry.vertices[tri]
    normal = np.cross(points[:, 1]-points[:, 0], points[:, 2]-points[:, 0])
    ray = points.mean(axis=1)-camera if perspective else np.broadcast_to(direction, normal.shape)
    front = (np.einsum('ij,ij->i', normal, ray) <= 0) & ~geometry.hidden[2]
    vertex[tri.ravel()] = False
    np.logical_or.at(vertex, tri.ravel(), np.repeat(front, 3))
    # Match triangle sides against actual mesh edges; diagonals are absent.
    n = max(len(vertex), 1)
    keys = np.min(geometry.edges, axis=1).astype(np.int64)*n + np.max(geometry.edges, axis=1)
    order = np.argsort(keys)
    sides = np.stack((tri[:, [0, 1]], tri[:, [1, 2]], tri[:, [2, 0]]), axis=1).reshape(-1, 2)
    side_keys = np.min(sides, axis=1).astype(np.int64)*n + np.max(sides, axis=1)
    if len(keys):
        match = np.searchsorted(keys[order], side_keys)
        valid = match < len(keys)
        valid[valid] &= keys[order[match[valid]]] == side_keys[valid]
        indices = order[match[valid]]
        edge[indices] = False
        np.logical_or.at(edge, indices, np.repeat(front, 3)[valid])
    return vertex, edge, front


class SnapPicker(ScenePicker):
    def __init__(self, context, epoch):
        self._vertex_tracker = TRACKER
        super().__init__(context, epoch)

    def refresh(self, context, epoch):
        changed = super().refresh(context, epoch)
        if changed:
            self._original_bounds = {}
            for record in self.records:
                if record['key'][0] == 'OBJECT':
                    self._vertex_tracker.observe_edit(self._owner(context, record))
        return changed

    def _feature_geometry(self, context, record, source):
        key = record['key'] if source == 'FINAL' else ('ORIGINAL', *record['key'])
        if key in self._geometry_cache:
            self._owner(context, record)
            self._geometry_cache.move_to_end(key)
            return self._geometry_cache[key]
        if source == 'ORIGINAL':
            import bmesh
            owner = self._owner(context, record)
            mesh = bmesh.from_edit_mesh(owner.data) if owner.mode == 'EDIT' else owner.data
            geometry = _snapshot(mesh, owner.matrix_world, owner.mode == 'EDIT')
            if len(geometry.vertices):
                self._original_bounds[record['key']] = (tuple(geometry.vertices.min(axis=0)),
                                                       tuple(geometry.vertices.max(axis=0)))
        else:
            evaluated, matrix, depsgraph = self._find_source(context, record)
            mesh = None
            try:
                mesh = evaluated.to_mesh(preserve_all_data_layers=False, depsgraph=depsgraph)
                geometry = _snapshot(mesh, matrix) if mesh is not None else None
            finally:
                if mesh is not None:
                    evaluated.to_mesh_clear()
        self._retain(key, geometry)
        return geometry

    def _geometry(self, context, record):
        geometry = self._feature_geometry(context, record, 'FINAL')
        return geometry if geometry is not None and len(geometry.triangles) else None

    def _eligible(self, context, record):
        owner = self._owner(context, record)
        tool = context.scene.tool_settings
        if getattr(tool, 'use_snap_selectable', False) and owner.hide_select:
            return False
        active = getattr(context, 'active_object', None)
        if active is None or active.mode != 'EDIT':
            return True
        if owner == active:
            return True
        return getattr(tool, 'use_snap_edit' if owner.mode == 'EDIT' else 'use_snap_nonedit', True)

    def _occluded(self, context, record, point, projection, inverse, original_self=False):
        clip = projection @ np.append(point, 1.)
        xy = clip[:2]/clip[3]
        near = inverse @ np.array((*xy, -1., 1.))
        far = inverse @ np.array((*xy, 1., 1.))
        near, far = near[:3]/near[3], far[:3]/far[3]
        ray = far-near
        ray /= np.linalg.norm(ray)
        distance = float((point-near) @ ray)
        tolerance = max(distance*1e-6, 1e-6)
        for blocker in self.records:
            if original_self and blocker['owner'] == record['owner']:
                if blocker['key'][0] != 'OBJECT':
                    continue
                geometry = self._feature_geometry(context, blocker, 'ORIGINAL')
            else:
                interval = _ray_box(near, ray, blocker['bounds'])
                if interval is None or interval[0] >= distance-tolerance:
                    continue
                geometry = self._geometry(context, blocker)
            if geometry is None:
                continue
            start = near
            for _ in range(len(geometry.triangles)+1):
                hit = geometry.ray_cast(start, ray)
                if hit is None or float((np.asarray(hit['point'])-near) @ ray) >= distance-tolerance:
                    break
                if not geometry.hidden[2][hit['triangle']]:
                    return True
                start = np.asarray(hit['point']) + ray*max(geometry.scale*1e-6, 1e-7)
        return False

    @staticmethod
    def _hit(record, geometry, source, kind, index, indices, weights, point, normal):
        anchor = {'key': record['key'], 'owner_uid': record['owner_uid'],
                  'snap_source': source, 'snap_kind': kind, 'feature': int(index),
                  'indices': tuple(map(int, indices)), 'weights': tuple(map(float, weights)),
                  'topology': geometry.feature_topology,
                  'vertex_topology': geometry.vertex_topology}
        return {'point': tuple(map(float, point)), 'normal': tuple(map(float, normal)),
                'object_name': record['name'], 'anchor': anchor, 'warnings': record['warnings'],
                'snap_kind': kind, 'snap_source': source}

    def snap(self, context, origin, direction, region, rv3d, coordinate, elements, source='BOTH'):
        if source not in {'ORIGINAL', 'FINAL', 'BOTH'}:
            raise ValueError('Unknown snap geometry source')
        origin, direction = np.asarray(origin, dtype=np.float64), np.asarray(direction, dtype=np.float64)
        if origin.shape != (3,) or direction.shape != (3,) or not np.all(np.isfinite((origin, direction))) or np.linalg.norm(direction) <= 1e-15:
            raise ValueError('Ray origin and direction must be finite, with nonzero direction')
        direction = direction/np.linalg.norm(direction)
        projection = np.asarray(rv3d.perspective_matrix, dtype=np.float64)
        inverse = np.linalg.inv(projection)
        camera = np.linalg.inv(np.asarray(rv3d.view_matrix, dtype=np.float64))[:3, 3]
        coordinate = np.asarray(coordinate, dtype=np.float64)
        elements = set(elements) & SUPPORTED_ELEMENTS
        space = getattr(context, 'space_data', None)
        shading = getattr(space, 'shading', None)
        xray = bool(shading and ((shading.type == 'WIREFRAME' and shading.show_xray_wireframe) or (shading.type == 'SOLID' and shading.show_xray)))
        culling = getattr(context.scene.tool_settings, 'use_snap_backface_culling', False) or bool(shading and getattr(shading, 'show_backface_culling', False))
        best, best_key = None, None
        for record in self.records:
            if not self._eligible(context, record):
                continue
            sources = ('ORIGINAL', 'FINAL') if source == 'BOTH' and record['key'][0] == 'OBJECT' else (source,)
            if record['key'][0] != 'OBJECT':
                sources = ('FINAL',) if source != 'ORIGINAL' else ()
            for layer in sources:
                bounds = self._original_bounds.get(record['key']) if layer == 'ORIGINAL' else record['bounds']
                if not _bounds_candidate(bounds, projection, region, coordinate):
                    continue
                geometry = self._feature_geometry(context, record, layer)
                if geometry is None or not len(geometry.vertices):
                    continue
                homogeneous, pixels, valid = _projection(geometry.vertices, projection, region)
                front_v, front_e, front_f = _front_flags(geometry, camera, bool(getattr(rv3d, 'is_perspective', True)), direction) if culling else (None, None, None)
                owner = self._owner(context, record)
                exclude_selected = owner.mode == 'EDIT' and owner == getattr(context, 'active_object', None) and not getattr(context.scene.tool_settings, 'use_snap_self', True)
                for kind in ('VERTEX', 'EDGE', 'EDGE_MIDPOINT'):
                    if kind not in elements:
                        continue
                    group = 0 if kind == 'VERTEX' else 1
                    allowed = ~geometry.hidden[group]
                    if exclude_selected:
                        allowed &= ~geometry.selected[group]
                    if culling:
                        allowed &= front_v if group == 0 else front_e
                    if kind == 'VERTEX':
                        points, screen, clip_valid = geometry.vertices, pixels, valid
                        weights = None
                    else:
                        edges = geometry.edges
                        h0, h1 = homogeneous[edges[:, 0]], homogeneous[edges[:, 1]]
                        # Clip segments in homogeneous space against near/far and w.
                        lower, upper = np.zeros(len(edges)), np.ones(len(edges))
                        for f0, f1 in ((h0[:, 3]-1e-12, h1[:, 3]-1e-12), (h0[:, 2]+h0[:, 3], h1[:, 2]+h1[:, 3]), (h0[:, 3]-h0[:, 2], h1[:, 3]-h1[:, 2])):
                            delta = f1-f0
                            crossing = -f0/np.where(abs(delta) > 1e-30, delta, 1.)
                            lower = np.where((f0 < 0) & (delta > 0), np.maximum(lower, crossing), lower)
                            upper = np.where((f1 < 0) & (delta < 0), np.minimum(upper, crossing), upper)
                            allowed &= ~((f0 < 0) & (f1 < 0))
                        clip_valid = lower <= upper
                        a = h0 + lower[:, None]*(h1-h0)
                        b = h0 + upper[:, None]*(h1-h0)
                        screen_a = (a[:, :2]/np.maximum(a[:, 3, None], 1e-30)+1)*np.array((region.width, region.height))/2
                        screen_b = (b[:, :2]/np.maximum(b[:, 3, None], 1e-30)+1)*np.array((region.width, region.height))/2
                        if kind == 'EDGE_MIDPOINT':
                            weights = np.full(len(edges), .5)
                            points = geometry.vertices[edges].mean(axis=1)
                            _h, screen, clip_valid = _projection(points, projection, region)
                        else:
                            delta = screen_b-screen_a
                            s = np.clip(np.einsum('ij,ij->i', coordinate-screen_a, delta)/np.maximum(np.einsum('ij,ij->i', delta, delta), 1e-30), 0, 1)
                            # Screen-linear s is not world-linear under perspective.
                            q = (s/np.maximum(b[:, 3], 1e-30))/np.maximum((1-s)/np.maximum(a[:, 3], 1e-30)+s/np.maximum(b[:, 3], 1e-30), 1e-30)
                            weights = lower + q*(upper-lower)
                            points = geometry.vertices[edges[:, 0]]*(1-weights[:, None])+geometry.vertices[edges[:, 1]]*weights[:, None]
                            screen = screen_a+s[:, None]*delta
                    distance = np.linalg.norm(screen-coordinate, axis=1)
                    # View rotation and projected endpoints use floating-point
                    # coordinates. Below one hundredth of a pixel, treat a
                    # silhouette front/rear pair as coincident and prefer depth.
                    pixel_rank = np.rint(distance/PIXEL_TIE_PRECISION)
                    depth = (points-origin) @ direction
                    candidate_indices = np.flatnonzero(allowed & clip_valid & (distance <= SNAP_RADIUS) & (depth >= -1e-6))
                    order = np.lexsort((candidate_indices, depth[candidate_indices], pixel_rank[candidate_indices]))
                    # Only integer indices are sorted; no Python object per
                    # candidate and no projection retained after this record.
                    for index in candidate_indices[order]:
                        index = int(index)
                        sort_key = (0, float(pixel_rank[index]), float(depth[index]), 0 if layer == 'ORIGINAL' else 1,
                                    {'VERTEX': 0, 'EDGE': 1, 'EDGE_MIDPOINT': 2}[kind], record['key'], index)
                        if best_key is not None and sort_key >= best_key:
                            break
                        point = points[index]
                        if not xray and self._occluded(context, record, point, projection, inverse, layer == 'ORIGINAL'):
                            continue
                        indices = (index,) if kind == 'VERTEX' else geometry.edges[index]
                        feature_weights = (1.,) if kind == 'VERTEX' else (1-weights[index], weights[index])
                        normal = np.asarray(feature_weights) @ geometry.normals[list(indices)]
                        norm = np.linalg.norm(normal)
                        normal = normal/norm if norm > 1e-15 else -direction
                        vertex_identity = None
                        if layer == 'ORIGINAL' and kind == 'VERTEX':
                            try:
                                vertex_identity = self._vertex_tracker.bind(owner, index)
                            except ValueError:
                                continue
                        best = self._hit(record, geometry, layer, kind, index, indices, feature_weights, point, normal)
                        if vertex_identity is not None:
                            best['anchor'].update(vertex_identity)
                        best_key = sort_key
                        break
                if elements & {'FACE', 'FACE_PROJECT'} and (best_key is None or best_key[0] > 0):
                    ray_origin = origin.copy()
                    for _ in range(len(geometry.triangles)+1):
                        hit = geometry.ray_cast(ray_origin, direction)
                        if hit is None:
                            break
                        triangle, point = hit['triangle'], np.asarray(hit['point'])
                        permitted = not geometry.hidden[2][triangle] and (not exclude_selected or not geometry.selected[2][triangle]) and (not culling or front_f[triangle])
                        _h, _p, clipped = _projection(point[None, :], projection, region)
                        if permitted and clipped[0]:
                            depth = float((point-origin) @ direction)
                            sort_key = (1, 0., depth, 0 if layer == 'ORIGINAL' else 1, 3, record['key'], triangle)
                            if depth >= -1e-6 and (best_key is None or sort_key < best_key) and (xray or not self._occluded(context, record, point, projection, inverse, layer == 'ORIGINAL')):
                                indices = geometry.triangles[triangle]
                                weights = _barycentric(point, geometry.vertices[indices])
                                best = self._hit(record, geometry, layer, 'FACE', triangle, indices, weights, point, hit['normal'])
                                best_key = sort_key
                            break
                        ray_origin = point + direction*max(geometry.scale*1e-6, 1e-7)
        self.warnings = best['warnings'] if best else ()
        return best

    def resolve(self, context, anchor):
        if 'snap_source' not in anchor:
            return super().resolve(context, anchor)
        try:
            record = self._by_key[tuple(anchor['key'])]
            if record['owner_uid'] != anchor['owner_uid']:
                raise ValueError('Anchor object identity changed')
            source = anchor['snap_source']
            if source not in {'ORIGINAL', 'FINAL'} or (source == 'ORIGINAL' and record['key'][0] != 'OBJECT'):
                raise ValueError('Anchor source is invalid')
            if source == 'ORIGINAL' and anchor['snap_kind'] == 'VERTEX' and 'vertex_id' in anchor:
                return self._vertex_tracker.resolve(self._owner(context, record), anchor)
            geometry = self._feature_geometry(context, record, source)
            indices = np.asarray(anchor['indices'], dtype=np.int64)
            weights = np.asarray(anchor['weights'], dtype=np.float64)
            kind, index = anchor['snap_kind'], int(anchor['feature'])
            topology = (geometry.vertex_topology if kind == 'VERTEX' and 'vertex_topology' in anchor
                        else geometry.feature_topology) if geometry is not None else None
            expected_topology = (anchor.get('vertex_topology', anchor['topology']) if kind == 'VERTEX'
                                 else anchor['topology'])
            if geometry is None or topology != expected_topology:
                raise ValueError('Anchor topology changed')
            count = len(geometry.vertices) if kind == 'VERTEX' else len(geometry.edges) if kind in {'EDGE', 'EDGE_MIDPOINT'} else len(geometry.triangles) if kind == 'FACE' else 0
            if not 0 <= index < count:
                raise ValueError('Anchor feature index is invalid')
            expected = (index,) if kind == 'VERTEX' else geometry.edges[index] if kind in {'EDGE', 'EDGE_MIDPOINT'} else geometry.triangles[index] if kind == 'FACE' else ()
            if (indices.ndim != 1 or weights.shape != indices.shape or not len(indices) or not np.array_equal(indices, expected)
                    or np.any(indices < 0) or np.any(indices >= len(geometry.vertices)) or not np.all(np.isfinite(weights))
                    or np.any(weights < -1e-6) or abs(weights.sum()-1) > 1e-5):
                raise ValueError('Anchor feature weights are invalid')
            return tuple(map(float, weights @ geometry.vertices[indices]))
        except (KeyError, TypeError, IndexError, OverflowError) as exc:
            raise ValueError('Anchor is invalid or its object is unavailable') from exc
