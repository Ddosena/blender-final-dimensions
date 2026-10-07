"""Physical offset of a loop made by Blender's ordinary Loop Cut tool.

This operator does not make a cut.  It moves only the selected cut vertices
along their existing cross-strip edges, after Blender has finished Ctrl+R.
"""

import math

import bmesh
import bpy
from bpy.app.handlers import persistent
from bpy.props import EnumProperty, FloatProperty
from bpy_extras import view3d_utils
from mathutils import Vector

from . import drawing


_preview_handles = set()


def _clear_preview(operator=None):
    handle = getattr(operator, '_preview_handle', None) if operator is not None else None
    if operator is not None and handle is None:
        return
    handles = (handle,) if handle is not None else tuple(_preview_handles)
    for current in handles:
        try:
            bpy.types.SpaceView3D.draw_handler_remove(current, 'WINDOW')
        except (ReferenceError, RuntimeError, ValueError):
            pass
        _preview_handles.discard(current)
    if operator is not None:
        operator._preview_handle = None


@persistent
def _on_load_pre(_dummy):
    _clear_preview()


def _preview_data(obj, bm, sides):
    """Copy coordinates only; draw callbacks never retain live BMesh elements."""
    verts = tuple(sides)
    lookup = {vert: index for index, vert in enumerate(verts)}
    matrix = obj.matrix_world
    rails = tuple((tuple(matrix @ side[0].co), tuple(matrix @ side[1].co))
                  for side in (sides[vert] for vert in verts))
    links = tuple((lookup[edge.verts[0]], lookup[edge.verts[1]])
                  for edge in bm.edges if edge.select)
    return rails, links


def _selection_signature(context, obj, bm, sides):
    """Plain scalars identify the exact edit selection present at invocation."""
    edges = tuple(sorted(tuple(sorted(vert.index for vert in edge.verts))
                         for edge in bm.edges if edge.select))
    rails = tuple(sorted((vert.index, tuple(vert.co),
                          a.index, tuple(a.co), b.index, tuple(b.co))
                         for vert, (a, b) in sides.items()))
    return (obj.as_pointer(), obj.data.as_pointer(),
            len(bm.verts), len(bm.edges), len(bm.faces), edges, rails,
            tuple(value for row in obj.matrix_world for value in row),
            context.scene.unit_settings.scale_length)


def _redraw_preview(_operator, context):
    if context.area is not None and context.area.type == 'VIEW_3D':
        context.area.tag_redraw()


def _draw_preview(operator, window_pointer, area_pointer, data):
    context = bpy.context
    if (context.window is None or context.area is None or context.region_data is None
            or context.window.as_pointer() != window_pointer
            or context.area.as_pointer() != area_pointer):
        return
    rails, links = data
    side = operator.side
    distance = float(operator.distance)
    if not math.isfinite(distance):
        return

    def project(point):
        return view3d_utils.location_3d_to_region_2d(
            context.region, context.region_data, Vector(point))

    projected = []
    for a, b in rails:
        rail = Vector(b)-Vector(a)
        target = (Vector(a if side == 'A' else b) +
                  rail * (distance / rail.length) * (1 if side == 'A' else -1))
        projected.append((project(a), project(b), project(target)))
    for first, second in links:
        for index, color in ((0, (1., .48, .08, 1.)),
                             (1, (.05, .82, 1., 1.)),
                             (2, (1., 1., 1., .95))):
            a, b = projected[first][index], projected[second][index]
            if a is not None and b is not None:
                width = 3.5 if (index == 0 and side == 'A' or
                                index == 1 and side == 'B') else 2.0
                drawing.line((a.x, a.y), (b.x, b.y), color, width)


def _source(context):
    obj = context.edit_object
    if obj is None or obj.type != 'MESH' or obj.mode != 'EDIT':
        raise ValueError('Finish a native Loop Cut on one mesh in Edit Mode first')
    if len(context.objects_in_mode_unique_data) != 1:
        raise ValueError('Edit only one mesh when offsetting a loop')
    if obj.data.users != 1:
        raise ValueError('Make this mesh single-user before offsetting the loop')
    if obj.data.shape_keys is not None:
        raise ValueError('Loop Offset does not support shape keys')
    if abs(obj.matrix_world.determinant()) <= 1e-12:
        raise ValueError('Object transform has a zero scale axis')
    scale = context.scene.unit_settings.scale_length
    if not math.isfinite(scale) or scale <= 0:
        raise ValueError('Scene unit scale must be positive')
    return obj, bmesh.from_edit_mesh(obj.data)


def _analyze(context):
    """Return fresh BMesh vertices and two consistently propagated boundaries."""
    obj, bm = _source(context)
    bm.verts.ensure_lookup_table()
    bm.verts.index_update()
    selected = tuple(edge for edge in bm.edges if edge.select)
    if not selected:
        raise ValueError('Select the new edge loop made by Ctrl+R')
    selected_set = set(selected)
    neighbors = {}
    for edge in selected:
        if edge.hide or len(edge.link_faces) != 2 or any(len(face.verts) != 4 or face.hide
                                                        for face in edge.link_faces):
            raise ValueError('The selected loop must run through manifold quads')
        for vert in edge.verts:
            if vert.hide:
                raise ValueError('Hidden cut vertices are unsupported')
            neighbors.setdefault(vert, []).append(edge)
    if any(len(edges) not in (1, 2) for edges in neighbors.values()):
        raise ValueError('Select one unbranched cut loop')
    endpoints = [vert for vert, edges in neighbors.items() if len(edges) == 1]
    if len(endpoints) not in (0, 2):
        raise ValueError('Selected cut edges must form one chain or one cycle')
    # A stale vertex selection could turn an unrelated edge network into an
    # apparent Ctrl+R result.  Reject it instead of silently moving a subset.
    if any(vert.select and vert not in neighbors for vert in bm.verts):
        raise ValueError('Deselect vertices outside the new cut loop')
    start = min(endpoints or neighbors.keys(), key=lambda vert: vert.index)
    seen = {start}
    stack = [start]
    while stack:
        current = stack.pop()
        for edge in neighbors[current]:
            other = edge.other_vert(current)
            if other not in seen:
                seen.add(other)
                stack.append(other)
    if len(seen) != len(neighbors):
        raise ValueError('Select only one connected cut loop')

    rails = {}
    for vert in neighbors:
        cross = [edge for edge in vert.link_edges if edge not in selected_set]
        if (len(cross) != 2 or any(edge.hide or not 1 <= len(edge.link_faces) <= 2
                                   or edge.other_vert(vert) in neighbors for edge in cross)):
            raise ValueError('The cut must have two unbranched neighboring rails at every vertex')
        rails[vert] = tuple(edge.other_vert(vert) for edge in cross)

    # Each selected edge has a quad on either side.  A face pairs the transverse
    # neighbor at its two selected endpoints, so it propagates A/B unambiguously.
    sides = {start: tuple(sorted(rails[start], key=lambda vert: vert.index))}
    stack = [start]
    while stack:
        current = stack.pop()
        a, b = sides[current]
        for edge in neighbors[current]:
            other = edge.other_vert(current)
            faces = tuple(edge.link_faces)
            face_a = next((face for face in faces if a in face.verts), None)
            face_b = next((face for face in faces if b in face.verts), None)
            if face_a is None or face_b is None or face_a is face_b:
                raise ValueError('Cannot identify two neighboring quad boundaries')
            a_other = next((vert for vert in rails[other] if vert in face_a.verts), None)
            b_other = next((vert for vert in rails[other] if vert in face_b.verts), None)
            if a_other is None or b_other is None or a_other is b_other:
                raise ValueError('The quad strip branches or twists at the cut')
            pair = (a_other, b_other)
            if other in sides:
                if sides[other] != pair:
                    raise ValueError('The selected loop has inconsistent neighboring sides')
            else:
                sides[other] = pair
                stack.append(other)

    matrix = obj.matrix_world
    rail_lengths = []
    for vert, (a, b) in sides.items():
        point, first, second = (matrix @ item.co for item in (vert, a, b))
        rail = second - first
        length = rail.length
        if length <= 1e-9 or not math.isfinite(length):
            raise ValueError('A transverse rail has zero length')
        fraction = (point - first).dot(rail) / (length * length)
        deviation = (point - (first + fraction * rail)).length
        if deviation > max(1e-6, length * 1e-5) or not 1e-6 < fraction < 1. - 1e-6:
            raise ValueError('Selected vertices are not inside their two neighboring loops')
        rail_lengths.append(length)
    return obj, bm, sides, min(rail_lengths)


class MESH_OT_final_dimensions_loop_offset(bpy.types.Operator):
    bl_idname = 'mesh.final_dimensions_loop_offset'
    bl_label = 'Offset Selected Loop'
    bl_description = 'Place a native Ctrl+R cut at a physical distance along its neighboring edges'
    bl_options = {'REGISTER', 'UNDO'}

    side: EnumProperty(
        name='Boundary',
        description='Choose which of the two neighboring loops the distance starts from',
        items=(('A', 'Boundary A', 'Distance from neighboring loop A'),
               ('B', 'Boundary B', 'Distance from neighboring loop B')),
        default='A',
        update=_redraw_preview,
    )
    distance: FloatProperty(
        name='Distance',
        description='Physical distance measured along each transverse slide edge',
        unit='LENGTH', min=0.0, precision=6, default=0.002,
        update=_redraw_preview,
    )

    @classmethod
    def poll(cls, context):
        return context.edit_object is not None and context.edit_object.type == 'MESH'

    def invoke(self, context, _event):
        try:
            obj, bm, sides, _minimum = _analyze(context)
        except (ValueError, RuntimeError) as exc:
            self.report({'ERROR'}, str(exc))
            return {'CANCELLED'}
        # RNA LENGTH values are Blender units.  The UI displays them according
        # to scene.scale_length; this default is physically 2 mm in any scene.
        if not self.properties.is_property_set('distance'):
            self.distance = 0.002 / context.scene.unit_settings.scale_length
        self._invocation_signature = _selection_signature(context, obj, bm, sides)
        self._preview_handle = None
        if context.window is not None and context.area is not None and context.area.type == 'VIEW_3D':
            data = _preview_data(obj, bm, sides)
            handle = bpy.types.SpaceView3D.draw_handler_add(
                _draw_preview,
                (self, context.window.as_pointer(), context.area.as_pointer(), data),
                'WINDOW', 'POST_PIXEL',
            )
            self._preview_handle = handle
            _preview_handles.add(handle)
            context.area.tag_redraw()
        try:
            result = context.window_manager.invoke_props_dialog(self, width=340)
        except Exception:
            _clear_preview(self)
            raise
        if 'RUNNING_MODAL' not in result:
            _clear_preview(self)
        return result

    def draw(self, _context):
        layout = self.layout
        layout.prop(self, 'side', expand=True)
        layout.prop(self, 'distance')
        layout.label(text='A: orange  B: cyan  target: white')
        layout.label(text='Distance follows the cross-strip edges, not face normals.')

    def execute(self, context):
        invoked_selection = getattr(self, '_invocation_signature', None)
        if invoked_selection is not None:
            self._invocation_signature = None
        _clear_preview(self)
        try:
            obj, bm, sides, minimum = _analyze(context)
            if (invoked_selection is not None and
                    _selection_signature(context, obj, bm, sides) != invoked_selection):
                raise ValueError('The mesh or selected loop changed while the dialog was open')
            distance = float(self.distance)
            if not math.isfinite(distance) or distance <= 0.0:
                raise ValueError('Enter a positive distance')
            if distance >= minimum - max(1e-8, minimum * 1e-6):
                raise ValueError('Distance must be shorter than every transverse rail')
            matrix = obj.matrix_world
            inverse = matrix.inverted()
            targets = {}
            for vert, (a, b) in sides.items():
                boundary, opposite = (a, b) if self.side == 'A' else (b, a)
                origin = matrix @ boundary.co
                rail = matrix @ opposite.co - origin
                target_world = origin + rail * (distance / rail.length)
                targets[vert] = inverse @ target_world
            # Validate all targets before the first write.  Only the selected
            # native cut vertices move; both neighboring loops remain intact.
            if not all(all(math.isfinite(value) for value in point) for point in targets.values()):
                raise ValueError('Offset produced a non-finite coordinate')
            previous = {vert: vert.co.copy() for vert in targets}
            try:
                for vert, point in targets.items():
                    vert.co = point
                bm.normal_update()
                bmesh.update_edit_mesh(obj.data, loop_triangles=False, destructive=False)
            except Exception:
                for vert, point in previous.items():
                    vert.co = point
                bm.normal_update()
                bmesh.update_edit_mesh(obj.data, loop_triangles=False, destructive=False)
                raise
            return {'FINISHED'}
        except (ValueError, RuntimeError, ReferenceError, ZeroDivisionError) as exc:
            self.report({'ERROR'}, str(exc))
            return {'CANCELLED'}

    def cancel(self, _context):
        _clear_preview(self)
        self._invocation_signature = None


def draw_panel(layout, context):
    box = layout.box()
    box.label(text='Loop Offset', icon='MOD_BEVEL')
    box.label(text='Ctrl+R: finish the native cut first')
    row = box.row()
    row.enabled = (context.edit_object is not None and context.edit_object.type == 'MESH')
    row.operator(MESH_OT_final_dimensions_loop_offset.bl_idname,
                 text='Offset Selected Loop')
    box.label(text='Distance is measured along slide edges')


def register():
    bpy.utils.register_class(MESH_OT_final_dimensions_loop_offset)
    if _on_load_pre not in bpy.app.handlers.load_pre:
        bpy.app.handlers.load_pre.append(_on_load_pre)


def unregister():
    _clear_preview()
    if _on_load_pre in bpy.app.handlers.load_pre:
        bpy.app.handlers.load_pre.remove(_on_load_pre)
    bpy.utils.unregister_class(MESH_OT_final_dimensions_loop_offset)
