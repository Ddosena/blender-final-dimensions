"""Persistent identities for Original mesh vertices used by ruler anchors.

The mesh attribute stores identity, never geometry. Live BMesh references resolve
copies of that attribute made by edit operations such as Inset. An ambiguous ID
without a surviving reference is deliberately treated as a missing endpoint.
"""
from __future__ import annotations

import bmesh


ATTRIBUTE = '.final_dimensions_vertex_id'
LAYER_KEY = '_final_dimensions_vertex_id_layer'
NEXT_KEY = '_final_dimensions_vertex_id_next'
MAX_ID = 2**31 - 1


def _mesh_key(mesh):
    return (mesh.as_pointer(), int(mesh.session_uid))


class VertexTracker:
    def __init__(self):
        # Mesh references are deliberately absent: a removed datablock can be
        # collected. Invalid BMVerts are discarded on the next access.
        self._edit_refs = {}
        self._edit_bms = {}
        self._known_meshes = set()

    def _refs_for(self, mesh, bm):
        key = _mesh_key(mesh)
        old_bm = self._edit_bms.get(key)
        try:
            same_session = old_bm is bm and bm.is_valid
        except (ReferenceError, RuntimeError):
            same_session = False
        if not same_session:
            self._edit_bms[key] = bm
            self._edit_refs[key] = {}
        return self._edit_refs[key]

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
        number = max(int(mesh.get(NEXT_KEY, 1)), max(used, default=0) + 1)
        if number > MAX_ID:
            raise ValueError('Ruler vertex identity limit exceeded')
        mesh[NEXT_KEY] = number + 1
        used.add(number)
        return number

    @staticmethod
    def _edit_members(mesh, name, create):
        bm = bmesh.from_edit_mesh(mesh)
        layer = bm.verts.layers.int.get(name)
        if layer is None and create:
            layer = bm.verts.layers.int.new(name)
        return bm, layer

    @staticmethod
    def _object_members(mesh, name, create):
        attribute = mesh.attributes.get(name)
        if attribute is None and create:
            attribute = mesh.attributes.new(name=name, type='INT', domain='POINT')
        if attribute is not None and (attribute.domain != 'POINT' or attribute.data_type != 'INT'):
            raise ValueError('Ruler vertex identity attribute has changed')
        return attribute

    def _edit_ids(self, mesh, name, create=False):
        bm, layer = self._edit_members(mesh, name, create)
        if layer is None:
            return bm, None, (), {}
        bm.verts.ensure_lookup_table()
        bm.verts.index_update()
        verts = tuple(bm.verts)
        groups = {}
        for vert in verts:
            value = int(vert[layer])
            if value > 0:
                groups.setdefault(value, []).append(vert)
        return bm, layer, verts, groups

    def observe_edit(self, owner):
        """Record unique IDs before a later edit can copy them to new vertices."""
        if owner.mode != 'EDIT':
            return
        mesh = owner.data
        if _mesh_key(mesh) not in self._known_meshes:
            return
        name = self._layer_name(mesh, False)
        if name is None:
            return
        bm, _layer, _verts, groups = self._edit_ids(mesh, name)
        refs = self._refs_for(mesh, bm)
        for value, members in groups.items():
            if len(members) == 1 and value not in refs:
                refs[value] = members[0]

    @staticmethod
    def _live_ref(ref, verts):
        try:
            return ref if ref is not None and ref.is_valid and ref in verts else None
        except (ReferenceError, RuntimeError):
            return None

    def _repair_edit_duplicates(self, mesh, bm, layer, verts, groups, protected=None):
        """Keep a known surviving BMVert's ID; remove only copied IDs."""
        refs = self._refs_for(mesh, bm)
        used = set(groups)
        changed = False
        for value, members in groups.items():
            if len(members) == 1:
                old_ref = refs.get(value)
                if old_ref is not None and old_ref is not members[0]:
                    # A deleted vertex's copied ID must not adopt its anchor.
                    # Do this even when the copy is now the only vertex with
                    # that ID; the old BMesh session still proves identity.
                    new_id = self._next_id(mesh, used)
                    members[0][layer] = new_id
                    refs[new_id] = members[0]
                    changed = True
                else:
                    refs[value] = members[0]
                continue
            keeper = self._live_ref(refs.get(value), verts)
            if protected is not None and protected[0] == value:
                target = self._live_ref(protected[1], verts)
                if target in members:
                    keeper = target
            # Without a known original, all copies lose this ID. Existing
            # anchors fail safely instead of silently following a new vertex.
            for member in members:
                if member is keeper:
                    continue
                new_id = self._next_id(mesh, used)
                member[layer] = new_id
                refs[new_id] = member
                changed = True
            if keeper is None:
                refs.pop(value, None)
            else:
                refs[value] = keeper
        if changed:
            bmesh.update_edit_mesh(mesh, loop_triangles=False, destructive=False)

    def bind(self, owner, index):
        """Return identity fields for a picked Original vertex."""
        mesh = owner.data
        self._known_meshes.add(_mesh_key(mesh))
        name = self._layer_name(mesh, True)
        identity = {'mesh_uid': int(mesh.session_uid), 'mesh_pointer': mesh.as_pointer(),
                    'vertex_layer': name}
        if owner.mode == 'EDIT':
            bm, layer, verts, groups = self._edit_ids(mesh, name, True)
            if not 0 <= index < len(verts):
                raise ValueError('Picked vertex disappeared')
            self._repair_edit_duplicates(mesh, bm, layer, verts, groups)
            vertex = verts[index]
            value = int(vertex[layer])
            if value <= 0:
                value = self._next_id(mesh, set(groups))
                vertex[layer] = value
                bmesh.update_edit_mesh(mesh, loop_triangles=False, destructive=False)
            self._refs_for(mesh, bm)[value] = vertex
            identity['vertex_ref'] = vertex
            identity['edit_bmesh'] = bm
        else:
            attribute = self._object_members(mesh, name, True)
            if not 0 <= index < len(attribute.data):
                raise ValueError('Picked vertex disappeared')
            value = int(attribute.data[index].value)
            if value > 0 and sum(int(entry.value) == value for entry in attribute.data) != 1:
                raise ValueError('Picked vertex identity is ambiguous')
            if value <= 0:
                used = {int(entry.value) for entry in attribute.data if entry.value > 0}
                value = self._next_id(mesh, used)
                attribute.data[index].value = value
        identity['vertex_id'] = value
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
            bm, layer, verts, groups = self._edit_ids(mesh, name)
            if layer is None:
                raise ValueError('Anchor vertex identity layer is missing')
            prior_bm = anchor.get('edit_bmesh')
            try:
                if prior_bm is bm and prior_bm.is_valid and self._live_ref(anchor.get('vertex_ref'), verts) is None:
                    raise ValueError('Anchor vertex was deleted')
            except (ReferenceError, RuntimeError):
                pass
            ref = self._live_ref(anchor.get('vertex_ref'), verts)
            if ref is not None and int(ref[layer]) != value:
                raise ValueError('Anchor vertex identity changed')
            self._repair_edit_duplicates(mesh, bm, layer, verts, groups, (value, ref))
            members = [v for v in verts if int(v[layer]) == value]
            if len(members) != 1:
                raise ValueError('Anchor vertex was deleted or became ambiguous')
            vertex = members[0]
            anchor['vertex_ref'] = vertex
            anchor['edit_bmesh'] = bm
            self._refs_for(mesh, bm)[value] = vertex
            return tuple(owner.matrix_world @ vertex.co)
        attribute = self._object_members(mesh, name, False)
        if attribute is None:
            raise ValueError('Anchor vertex identity layer is missing')
        matches = [i for i, entry in enumerate(attribute.data) if int(entry.value) == value]
        if len(matches) != 1:
            raise ValueError('Anchor vertex was deleted or became ambiguous')
        return tuple(owner.matrix_world @ mesh.vertices[matches[0]].co)


# Windows can hold independent ruler collections for the same mesh. They must
# share edit references while repairing copied IDs, or one window could replace
# an ID still owned by an anchor in another window.
TRACKER = VertexTracker()
