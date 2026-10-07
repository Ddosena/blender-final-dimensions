"""Original vertex anchors with no retained BMesh or BMVert wrappers.

Native Edit operators and Undo can replace an EditMesh while Python still owns
its BMesh wrapper. Releasing that stale wrapper can crash Blender. Everything
kept between events here is a scalar, including same-session element tokens.
"""
from __future__ import annotations

import hashlib

import bmesh
import bpy


ATTRIBUTE = '.final_dimensions_vertex_id'
LAYER_KEY = '_final_dimensions_vertex_id_layer'
NEXT_KEY = '_final_dimensions_vertex_id_next'
MAX_ID = 2**31 - 1
_last_issued = 0
_issued_ids_by_mesh = {}


class VertexResolutionDeferred(RuntimeError):
    """A native modal edit is in progress; retain and retry this anchor."""


def _topology(mesh, bm=None):
    """A mode-independent connectivity check, unaffected by deformation."""
    if bm is not None:
        bm.verts.ensure_lookup_table()
        bm.verts.index_update()
        edges = [tuple(v.index for v in edge.verts) for edge in bm.edges]
        faces = [tuple(v.index for v in face.verts) for face in bm.faces]
        count = len(bm.verts)
    else:
        edges = [edge.vertices for edge in mesh.edges]
        faces = [tuple(mesh.loops[i].vertex_index for i in range(face.loop_start,
                  face.loop_start + face.loop_total)) for face in mesh.polygons]
        count = len(mesh.vertices)
    digest = hashlib.blake2b(digest_size=16)
    digest.update(f'{count}|'.encode('ascii'))
    # Element ordering and face winding may change on a mode conversion.
    edge_keys = sorted(tuple(sorted(pair)) for pair in edges)
    face_keys = sorted(tuple(sorted(loop)) for loop in faces)
    for kind, collection in ((b'E', edge_keys), (b'F', face_keys)):
        digest.update(kind)
        for entry in collection:
            digest.update(','.join(map(str, entry)).encode('ascii'))
            digest.update(b';')
    return digest.hexdigest()


def _may_write_edit_identity(operators=None):
    """Do not change EditMesh CustomData while a native modal op owns it."""
    if operators is None:
        window = bpy.context.window
        if window is None:
            return True
        operators = window.modal_operators
    for operator in operators:
        identifier = getattr(operator, 'bl_idname', '')
        if not identifier:
            identifier = getattr(getattr(operator, 'bl_rna', None), 'identifier', '')
        identifier = str(identifier).lower().replace('_ot_', '.')
        if identifier not in {'view3d.final_dimensions_ruler',
                              'view3d.final_dimensions_hover'}:
            return False
    return True


class VertexTracker:
    @staticmethod
    def _layer_name(mesh, create):
        name = mesh.get(LAYER_KEY)
        if name is not None:
            name = str(name)
            attribute = mesh.attributes.get(name)
            if attribute is not None and (attribute.domain != 'POINT' or attribute.data_type != 'INT'):
                raise ValueError('Ruler vertex identity attribute has changed')
            return name
        if not create:
            return None
        name = ATTRIBUTE
        if mesh.attributes.get(name) is not None:
            suffix = 1
            while mesh.attributes.get(f'{ATTRIBUTE}.{suffix:03d}') is not None:
                suffix += 1
            name = f'{ATTRIBUTE}.{suffix:03d}'
        mesh[LAYER_KEY] = name
        return name

    @staticmethod
    def _next_id(mesh, used):
        global _last_issued
        number = max(int(mesh.get(NEXT_KEY, 1)), _last_issued + 1,
                     max(used, default=0) + 1)
        if number > MAX_ID:
            raise ValueError('Ruler vertex identity limit exceeded')
        mesh[NEXT_KEY] = number + 1
        _last_issued = number
        return number

    @staticmethod
    def _edit_members(mesh, name, create):
        bm = bmesh.from_edit_mesh(mesh)
        layer = bm.verts.layers.int.get(name)
        if layer is None and create:
            layer = bm.verts.layers.int.new(name)
        bm.verts.ensure_lookup_table()
        bm.verts.index_update()
        return bm, layer

    @staticmethod
    def _object_members(mesh, name, create):
        attribute = mesh.attributes.get(name)
        if attribute is None and create:
            attribute = mesh.attributes.new(name=name, type='INT', domain='POINT')
        if attribute is not None and (attribute.domain != 'POINT' or attribute.data_type != 'INT'):
            raise ValueError('Ruler vertex identity attribute has changed')
        return attribute

    @staticmethod
    def _remember_edit(anchor, bm, vertex, mesh):
        anchor['edit_mesh_token'] = hash(bm)
        anchor['edit_vertex_token'] = hash(vertex)
        anchor['edit_neighbor_tokens'] = tuple(sorted(
            hash(edge.other_vert(vertex)) for edge in vertex.link_edges))
        anchor['verified_topology'] = _topology(mesh, bm)

    def bind(self, owner, index):
        mesh = owner.data
        issued = _issued_ids_by_mesh.setdefault(int(mesh.session_uid), set())
        if owner.mode == 'EDIT' and not _may_write_edit_identity():
            raise ValueError('Finish the active mesh operation before binding a vertex')
        name = self._layer_name(mesh, True)
        identity = {'mesh_uid': int(mesh.session_uid),
                    'mesh_pointer': mesh.as_pointer(), 'vertex_layer': name}
        if owner.mode == 'EDIT':
            bm, layer = self._edit_members(mesh, name, True)
            if not 0 <= index < len(bm.verts):
                raise ValueError('Picked vertex disappeared')
            vertex = bm.verts[index]
            value = int(vertex[layer])
            if value > 0 and sum(int(part[layer]) == value for part in bm.verts) != 1:
                if value in issued:
                    raise ValueError('Picked vertex identity was copied and is ambiguous')
                value = 0  # Interpolated CustomData is not an issued identity.
            if value <= 0:
                used = {int(part[layer]) for part in bm.verts if part[layer] > 0}
                value = self._next_id(mesh, used)
                vertex[layer] = value
                bmesh.update_edit_mesh(mesh, loop_triangles=False, destructive=False)
            self._remember_edit(identity, bm, vertex, mesh)
        else:
            attribute = self._object_members(mesh, name, True)
            if not 0 <= index < len(attribute.data):
                raise ValueError('Picked vertex disappeared')
            value = int(attribute.data[index].value)
            if value > 0 and sum(int(part.value) == value for part in attribute.data) != 1:
                if value in issued:
                    raise ValueError('Picked vertex identity was copied and is ambiguous')
                value = 0
            if value <= 0:
                used = {int(part.value) for part in attribute.data if part.value > 0}
                value = self._next_id(mesh, used)
                attribute.data[index].value = value
            identity['verified_topology'] = _topology(mesh)
        identity['vertex_id'] = value
        issued.add(value)
        return identity

    def resolve(self, owner, anchor):
        mesh = owner.data
        if (int(mesh.session_uid) != anchor.get('mesh_uid') or
                mesh.as_pointer() != anchor.get('mesh_pointer')):
            raise ValueError('Anchor mesh identity changed')
        name = self._layer_name(mesh, False)
        if name != anchor.get('vertex_layer'):
            raise ValueError('Anchor vertex identity layer changed')
        value = int(anchor['vertex_id'])
        if value <= 0:
            raise ValueError('Anchor vertex identity is invalid')
        if owner.mode == 'EDIT':
            # The native operator may own a provisional or replaced EditMesh.
            # Even index_update() is too early until it has finished or canceled.
            if not _may_write_edit_identity():
                raise VertexResolutionDeferred('Vertex resolution awaits the mesh operation')
            bm, layer = self._edit_members(mesh, name, False)
            if layer is None:
                raise ValueError('Anchor vertex identity layer is missing')
            matches = [part for part in bm.verts if int(part[layer]) == value]
            token = anchor.get('edit_vertex_token')
            same_session = anchor.get('edit_mesh_token') == hash(bm) and token is not None
            if same_session:
                selected = [part for part in matches if hash(part) == token]
                if len(selected) != 1:
                    raise ValueError('Anchor vertex was deleted')
                vertex = selected[0]
                old_neighbors = set(anchor.get('edit_neighbor_tokens', ()))
                if old_neighbors:
                    if not old_neighbors.intersection(
                            hash(edge.other_vert(vertex)) for edge in vertex.link_edges):
                        raise ValueError('Anchor vertex was deleted or replaced')
                elif _topology(mesh, bm) != anchor.get('verified_topology'):
                    raise ValueError('Anchor topology changed around an isolated vertex')
                if len(matches) != 1:
                    if not old_neighbors:
                        raise ValueError('Anchor vertex identity was copied and is ambiguous')
                    used = {int(part[layer]) for part in bm.verts if part[layer] > 0}
                    for part in matches:
                        if part is vertex:
                            continue
                        fresh = self._next_id(mesh, used)
                        part[layer] = fresh
                        used.add(fresh)
                    bmesh.update_edit_mesh(mesh, loop_triangles=False, destructive=False)
            else:
                if _topology(mesh, bm) != anchor.get('verified_topology'):
                    raise ValueError('Anchor topology changed before vertex identity could be verified')
                if len(matches) != 1:
                    raise ValueError('Anchor vertex was deleted or its identity was copied')
                vertex = matches[0]
            self._remember_edit(anchor, bm, vertex, mesh)
            return tuple(owner.matrix_world @ vertex.co)
        attribute = self._object_members(mesh, name, False)
        if attribute is None:
            raise ValueError('Anchor vertex identity layer is missing')
        if _topology(mesh) != anchor.get('verified_topology'):
            raise ValueError('Anchor topology changed before vertex identity could be verified')
        matches = [i for i, part in enumerate(attribute.data) if int(part.value) == value]
        if len(matches) != 1:
            raise ValueError('Anchor vertex was deleted or its identity was copied')
        anchor.pop('edit_mesh_token', None)
        anchor.pop('edit_vertex_token', None)
        anchor.pop('edit_neighbor_tokens', None)
        return tuple(owner.matrix_world @ mesh.vertices[matches[0]].co)


TRACKER = VertexTracker()
