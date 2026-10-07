"""Preview and apply an exact planar offset cut to one Edit Mode mesh island."""
from __future__ import annotations

import math
import re

import bmesh
import bpy
import gpu
from bpy.app.handlers import persistent
from bpy.props import IntProperty, StringProperty
from bpy_extras import view3d_utils
from gpu_extras.batch import batch_for_shader
from mathutils import Vector
from mathutils.bvhtree import BVHTree


_DISTANCE = re.compile(r'^\s*(\d+(?:[.,]\d+)?|[.,]\d+)\s*(mm|cm|m)\s*$', re.I)
_MAX_ISLAND_ELEMENTS = 700_000
_sessions = {}  # area pointer -> modal operator
_draw_handle = None
_registered = False
_generation = 0


def _face_signature(face):
    return tuple(tuple(float(value) for value in vert.co) for vert in face.verts)


def parse_distance(scene, value):
    """Return Blender world units. Explicit mm/cm/m avoids unit ambiguity."""
    match = _DISTANCE.fullmatch(value)
    if match is None:
        raise ValueError('Enter a positive distance with mm, cm, or m (for example 2 mm)')
    number = float(match.group(1).replace(',', '.'))
    if not math.isfinite(number) or number <= 0:
        raise ValueError('Offset distance must be positive')
    meters = number * {'mm': .001, 'cm': .01, 'm': 1.}[match.group(2).lower()]
    scale = float(scene.unit_settings.scale_length)
    if not math.isfinite(scale) or scale <= 0:
        raise ValueError('Scene unit scale must be positive')
    distance = meters / scale
    if not math.isfinite(distance) or distance <= 0:
        raise ValueError('Offset distance is out of range')
    return distance


def _active_mesh(context):
    obj = context.edit_object
    if obj is None or obj.type != 'MESH' or obj.mode != 'EDIT':
        raise ValueError('Offset Cut needs one mesh in Edit Mode')
    if len(context.objects_in_mode) != 1:
        raise ValueError('Offset Cut needs only one mesh in Edit Mode')
    if obj.data.users != 1:
        raise ValueError('Make the mesh single-user before Offset Cut')
    if obj.data.shape_keys is not None:
        raise ValueError('Offset Cut does not edit meshes with shape keys')
    matrix = obj.matrix_world
    if abs(matrix.determinant()) < 1e-12:
        raise ValueError('Object transform is singular')
    return obj


def _face_and_island(bm, face_index):
    bm.faces.ensure_lookup_table()
    bm.faces.index_update()
    if not 0 <= face_index < len(bm.faces):
        raise ValueError('Reference face disappeared')
    face = bm.faces[face_index]
    if not face.is_valid or face.hide:
        raise ValueError('Reference face is hidden or unavailable')
    island = {face}
    todo = [face]
    edges = set()
    vertices = set()
    while todo:
        current = todo.pop()
        if current.hide:
            raise ValueError('The connected mesh island has hidden faces')
        for vert in current.verts:
            if vert.hide:
                raise ValueError('The connected mesh island has hidden vertices')
            vertices.add(vert)
        for edge in current.edges:
            if edge.hide:
                raise ValueError('The connected mesh island has hidden edges')
            if len(edge.link_faces) > 2:
                raise ValueError('The connected mesh island has non-manifold edges')
            edges.add(edge)
            for neighbor in edge.link_faces:
                if neighbor not in island:
                    island.add(neighbor)
                    todo.append(neighbor)
        if len(island) + len(edges) + len(vertices) > _MAX_ISLAND_ELEMENTS:
            raise ValueError('The connected mesh island is too large for Offset Cut')
    # A wire or another face fan touching only at a vertex lies outside the
    # face-edge traversal above. Reject the component instead of cutting only
    # part of what the user can reasonably perceive as one island.
    if any(edge not in edges for vert in vertices for edge in vert.link_edges):
        raise ValueError('Reference island contains loose or non-manifold connections')
    return face, island, edges, vertices


def _plan(context, obj, face_index, distance_text, flip):
    """Compute cut on a disposable BMesh copy; return world preview segments."""
    distance = parse_distance(context.scene, distance_text)
    bm = bmesh.from_edit_mesh(obj.data)
    face, island, edges, vertices = _face_and_island(bm, face_index)
    normal = face.normal.normalized()
    if normal.length < 1e-12:
        raise ValueError('Reference face has no reliable normal')
    points = [vert.co.copy() for vert in face.verts]
    center = sum(points, Vector()) / len(points)
    size = max((point-center).length for point in points)
    tolerance = max(size * 1e-6, 1e-8)
    if size <= 1e-10 or any(abs((point-center).dot(normal)) > tolerance for point in points):
        raise ValueError('Reference face must be planar')
    matrix = obj.matrix_world.copy()
    normal_world = (matrix.inverted().transposed().to_3x3() @ normal).normalized()
    if normal_world.length < 1e-12:
        raise ValueError('Object transform gives no reliable world normal')
    sign = 1 if flip else -1
    plane_world = matrix @ center + normal_world * (sign * distance)
    plane_co = matrix.inverted() @ plane_world
    plane_no = (matrix.to_3x3().transposed() @ normal_world).normalized()
    if plane_no.length < 1e-12:
        raise ValueError('Cut plane is degenerate')
    # Copy while indices still map one-for-one. The source BMesh is read only.
    bm.verts.index_update()
    bm.edges.index_update()
    bm.faces.index_update()
    face_indices = {part.index for part in island}
    edge_indices = {part.index for part in edges}
    vert_indices = {part.index for part in vertices}
    copy = bm.copy()
    try:
        copy.verts.ensure_lookup_table()
        copy.edges.ensure_lookup_table()
        copy.faces.ensure_lookup_table()
        geom = ([copy.verts[index] for index in vert_indices] +
                [copy.edges[index] for index in edge_indices] +
                [copy.faces[index] for index in face_indices])
        before = {vert: vert.co.copy() for vert in copy.verts}
        bisect_dist = max(tolerance * .1, 1e-7)
        result = bmesh.ops.bisect_plane(copy, geom=geom, dist=bisect_dist,
                               plane_co=plane_co, plane_no=plane_no,
                               clear_inner=False, clear_outer=False)
        if any((vert.co-position).length > 1e-8 for vert, position in before.items()):
            raise RuntimeError('Offset Cut would move an existing vertex')
        cut = set(result['geom_cut'])
        if not cut:
            raise ValueError('Offset plane does not cross this mesh island')
        fresh = {vert for vert in copy.verts if vert not in before}
        segments = []
        for edge in copy.edges:
            if (edge in cut or any(vert in cut or vert in fresh for vert in edge.verts)):
                if all(abs((vert.co-plane_co).dot(plane_no)) <= tolerance * 2
                       for vert in edge.verts):
                    segments.append(tuple(tuple(matrix @ vert.co) for vert in edge.verts))
        if not segments:
            raise ValueError('Offset plane does not cross this mesh island')
    finally:
        copy.free()
    outline = tuple(tuple(matrix @ vert.co) for vert in face.verts)
    return {'plane_co': plane_co, 'plane_no': plane_no, 'segments': tuple(segments),
            'outline': outline, 'face_index': face_index,
            'mesh_uid': int(obj.data.session_uid), 'object_uid': int(obj.session_uid),
            'matrix': matrix, 'distance': distance, 'bisect_dist': bisect_dist}


def _apply(context, obj, plan):
    if int(obj.data.session_uid) != plan['mesh_uid'] or int(obj.session_uid) != plan['object_uid']:
        raise ValueError('Reference mesh changed')
    if obj.matrix_world != plan['matrix']:
        raise ValueError('Object transform changed; preview again')
    bm = bmesh.from_edit_mesh(obj.data)
    _face, island, edges, vertices = _face_and_island(bm, plan['face_index'])
    geom = list(vertices) + list(edges) + list(island)
    bmesh.ops.bisect_plane(bm, geom=geom, dist=plan['bisect_dist'],
                                    plane_co=plan['plane_co'], plane_no=plan['plane_no'],
                                    clear_inner=False, clear_outer=False)
    bmesh.update_edit_mesh(obj.data, loop_triangles=True, destructive=True)


def _draw_segments(shader, segments, color, width):
    if not segments:
        return
    vertices = [point for segment in segments for point in segment]
    batch = batch_for_shader(shader, 'LINES', {'pos': vertices})
    shader.bind()
    shader.uniform_float('color', color)
    region = bpy.context.region
    shader.uniform_float('viewportSize', (region.width, region.height))
    shader.uniform_float('lineWidth', width)
    batch.draw(shader)


def _draw_preview():
    area = bpy.context.area
    if area is None:
        return
    op = _sessions.get(area.as_pointer())
    if op is None:
        return
    old_blend = gpu.state.blend_get()
    old_depth = gpu.state.depth_test_get()
    gpu.state.blend_set('ALPHA')
    gpu.state.depth_test_set('ALWAYS')
    shader = gpu.shader.from_builtin('POLYLINE_UNIFORM_COLOR')
    try:
        outline = op._outline
        if len(outline) > 1:
            _draw_segments(shader, tuple(zip(outline, outline[1:] + outline[:1])),
                           (1., .48, .08, .9), 3.)
        if op._plan is not None:
            _draw_segments(shader, op._plan['segments'], (.08, .58, 1., .95), 3.)
    finally:
        gpu.state.depth_test_set(old_depth)
        gpu.state.blend_set(old_blend)


class VIEW3D_OT_final_dimensions_offset_cut(bpy.types.Operator):
    bl_idname = 'view3d.final_dimensions_offset_cut'
    bl_label = 'Offset Cut'
    bl_description = 'Pick a planar Original face and cut its connected island at an exact perpendicular distance'
    bl_options = {'REGISTER', 'UNDO'}

    face_index: IntProperty(default=-1, options={'HIDDEN'})
    flip: bpy.props.BoolProperty(default=False, options={'HIDDEN'})

    @classmethod
    def poll(cls, context):
        return context.edit_object is not None and context.edit_object.type == 'MESH'

    def _finish(self, context):
        if _sessions.get(self._area_pointer) is self:
            _sessions.pop(self._area_pointer, None)
            if context.area is not None:
                context.area.tag_redraw()
            context.workspace.status_text_set(None)

    def _set_status(self, context):
        if self._face_ref is None:
            status = ('Offset Cut ' + context.window_manager.final_dimensions_offset_distance +
                      ': hover a planar Original face · LMB pick · Esc cancel')
        else:
            status = ('Offset Cut ' + context.window_manager.final_dimensions_offset_distance +
                      ': blue preview · LMB/Enter confirm · F flip inward/outward · '
                      'type mm/cm/m · Backspace · Esc cancel')
        context.workspace.status_text_set(status)
        context.area.tag_redraw()

    def _pick_face(self, context, event):
        obj = _active_mesh(context)
        bm = bmesh.from_edit_mesh(obj.data)
        bm.faces.ensure_lookup_table()
        bm.faces.index_update()
        region, rv3d = self._window_region, context.space_data.region_3d
        xy = (event.mouse_x-region.x, event.mouse_y-region.y)
        if not self._over_viewport(event):
            self._outline = ()
            self._candidate = None
            context.area.tag_redraw()
            return
        ray_origin = view3d_utils.region_2d_to_origin_3d(region, rv3d, xy)
        ray_direction = view3d_utils.region_2d_to_vector_3d(region, rv3d, xy)
        inverse = obj.matrix_world.inverted()
        local_origin = inverse @ ray_origin
        local_direction = (inverse.to_3x3() @ ray_direction).normalized()
        bvh = BVHTree.FromBMesh(bm)
        point, _normal, index, _distance = bvh.ray_cast(local_origin, local_direction)
        if point is None or index is None or index < 0:
            self._outline = ()
            self._candidate = None
            return
        face = bm.faces[index]
        if face.hide:
            self._outline = ()
            self._candidate = None
            return
        self._candidate = face
        self._candidate_signature = _face_signature(face)
        self._outline = tuple(tuple(obj.matrix_world @ vert.co) for vert in face.verts)
        context.area.tag_redraw()

    def _over_viewport(self, event):
        region = self._window_region
        x, y = event.mouse_x, event.mouse_y
        if not region.x <= x < region.x+region.width or not region.y <= y < region.y+region.height:
            return False
        for other in self._area.regions:
            if other.as_pointer() == region.as_pointer():
                continue
            if (other.type in {'UI', 'TOOLS', 'TOOL_HEADER', 'HEADER'} and
                    other.x <= x < other.x+other.width and
                    other.y <= y < other.y+other.height):
                return False
        return True

    def _preview(self, context):
        obj = _active_mesh(context)
        bm = bmesh.from_edit_mesh(obj.data)
        if obj.as_pointer() != self._object_pointer or obj.data.as_pointer() != self._mesh_pointer:
            raise ValueError('Reference face changed; start Offset Cut again')
        # Blender can rebuild the Edit BMesh between modal events, invalidating
        # the BMFace wrapper without changing the user's geometry. Match the
        # exact local polygon; never adopt an index alone after that rebuild.
        matches = [face for face in bm.faces if _face_signature(face) == self._face_signature]
        if len(matches) != 1:
            raise ValueError('Reference face changed or became ambiguous; start Offset Cut again')
        face = matches[0]
        self._face_ref = face
        bm.faces.index_update()
        self.face_index = face.index
        text = context.window_manager.final_dimensions_offset_distance
        self._plan = None
        self._plan = _plan(context, obj, self.face_index, text, self.flip)
        self._outline = self._plan['outline']
        context.area.tag_redraw()

    def invoke(self, context, event):
        try:
            obj = _active_mesh(context)
        except ValueError as exc:
            self.report({'ERROR'}, str(exc))
            return {'CANCELLED'}
        if context.area is None or context.area.type != 'VIEW_3D':
            self.report({'ERROR'}, 'Offset Cut must start in a 3D View')
            return {'CANCELLED'}
        self._window_region = next((region for region in context.area.regions if region.type == 'WINDOW'), None)
        if self._window_region is None:
            self.report({'ERROR'}, '3D View window region is unavailable')
            return {'CANCELLED'}
        self._area_pointer = context.area.as_pointer()
        self._workspace = context.workspace
        self._area = context.area
        if self._area_pointer in _sessions:
            self.report({'ERROR'}, 'Offset Cut is already active in this view')
            return {'CANCELLED'}
        self._object_pointer = obj.as_pointer()
        self._mesh_pointer = obj.data.as_pointer()
        self._face_ref = None
        self._candidate = None
        self._face_signature = None
        self._outline = ()
        self._plan = None
        self._typing = False
        self._generation = _generation
        _sessions[self._area_pointer] = self
        self._set_status(context)
        context.window_manager.modal_handler_add(self)
        return {'RUNNING_MODAL'}

    def modal(self, context, event):
        try:
            if (not _registered or self._generation != _generation or
                    _sessions.get(self._area_pointer) is not self):
                self._finish(context)
                return {'CANCELLED'}
            if event.type in {'MIDDLEMOUSE', 'WHEELUPMOUSE', 'WHEELDOWNMOUSE',
                              'TRACKPADPAN', 'TRACKPADZOOM', 'MOUSEPAN', 'MOUSEZOOM'} or event.type.startswith('NUMPAD_') and event.type != 'NUMPAD_ENTER':
                return {'PASS_THROUGH'}
            if event.type in {'ESC', 'RIGHTMOUSE'} and event.value == 'PRESS':
                self._finish(context)
                return {'CANCELLED'}
            if event.type == 'MOUSEMOVE' and self._face_ref is None:
                self._pick_face(context, event)
                return {'RUNNING_MODAL'}
            if event.type == 'LEFTMOUSE' and event.value == 'PRESS':
                if (context.area is None or context.area.as_pointer() != self._area_pointer or
                        not self._over_viewport(event)):
                    return {'RUNNING_MODAL'}
                if self._face_ref is None:
                    self._pick_face(context, event)
                    if self._candidate is None:
                        return {'RUNNING_MODAL'}
                    self._face_ref = self._candidate
                    self._face_signature = self._candidate_signature
                    self._preview(context)
                    self._set_status(context)
                    return {'RUNNING_MODAL'}
                self._preview(context)
                result = self.execute(context)
                self._finish(context)
                return result
            if event.type in {'RET', 'NUMPAD_ENTER'} and event.value == 'PRESS' and self._face_ref:
                self._preview(context)
                result = self.execute(context)
                self._finish(context)
                return result
            if event.type == 'F' and event.value == 'PRESS' and self._face_ref:
                self.flip = not self.flip
                self._preview(context)
                return {'RUNNING_MODAL'}
            if self._face_ref and event.value == 'PRESS':
                value = context.window_manager.final_dimensions_offset_distance
                if event.type == 'BACK_SPACE':
                    context.window_manager.final_dimensions_offset_distance = value[:-1]
                    self._typing = True
                    self._plan = None
                    self._set_status(context)
                    return {'RUNNING_MODAL'}
                if event.unicode and event.unicode in '0123456789., mMcC':
                    context.window_manager.final_dimensions_offset_distance = (value if self._typing else '') + event.unicode
                    self._typing = True
                    try:
                        self._preview(context)
                    except ValueError:
                        self._plan = None
                    self._set_status(context)
                    return {'RUNNING_MODAL'}
            return {'RUNNING_MODAL'}
        except (ValueError, ReferenceError, RuntimeError) as exc:
            self.report({'ERROR'}, str(exc))
            # A bad distance can be corrected inside the modal operation.
            if self._face_ref is not None and isinstance(exc, ValueError) and event.type not in {'LEFTMOUSE', 'RET', 'NUMPAD_ENTER'}:
                self._plan = None
                context.area.tag_redraw()
                return {'RUNNING_MODAL'}
            self._finish(context)
            return {'CANCELLED'}

    def execute(self, context):
        try:
            obj = _active_mesh(context)
            plan = _plan(context, obj, self.face_index,
                         context.window_manager.final_dimensions_offset_distance, self.flip)
            _apply(context, obj, plan)
            self.report({'INFO'}, f'Offset Cut applied at {context.window_manager.final_dimensions_offset_distance}')
            return {'FINISHED'}
        except (ValueError, RuntimeError, ReferenceError) as exc:
            self.report({'ERROR'}, str(exc))
            return {'CANCELLED'}


def draw_panel(layout, context):
    box = layout.box()
    box.label(text='Offset Cut', icon='MOD_BOOLEAN')
    box.prop(context.window_manager, 'final_dimensions_offset_distance', text='Distance')
    row = box.row()
    row.enabled = VIEW3D_OT_final_dimensions_offset_cut.poll(context)
    row.operator(VIEW3D_OT_final_dimensions_offset_cut.bl_idname, text='Pick Face & Preview')
    box.label(text='Original face · connected island')
    box.label(text='Inward by default · F flips side')
    box.label(text='Enter confirms · Esc cancels')


def _cancel_sessions():
    global _generation
    _generation += 1
    owned = tuple(_sessions.values())
    _sessions.clear()
    for op in owned:
        try:
            op._workspace.status_text_set(None)
            op._area.tag_redraw()
        except (AttributeError, ReferenceError, RuntimeError):
            pass


@persistent
def _on_load_pre(_unused):
    _cancel_sessions()


@persistent
def _on_save_pre(_unused):
    _cancel_sessions()


@persistent
def _on_undo_pre(_unused):
    _cancel_sessions()


def register():
    global _draw_handle, _registered
    bpy.types.WindowManager.final_dimensions_offset_distance = StringProperty(
        name='Offset distance', default='2 mm',
        description='Positive physical offset with mm, cm, or m suffix')
    bpy.utils.register_class(VIEW3D_OT_final_dimensions_offset_cut)
    _draw_handle = bpy.types.SpaceView3D.draw_handler_add(_draw_preview, (), 'WINDOW', 'POST_VIEW')
    bpy.app.handlers.load_pre.append(_on_load_pre)
    bpy.app.handlers.save_pre.append(_on_save_pre)
    bpy.app.handlers.undo_pre.append(_on_undo_pre)
    _registered = True


def unregister():
    global _draw_handle, _registered
    _registered = False
    _cancel_sessions()
    for handlers, callback in ((bpy.app.handlers.load_pre, _on_load_pre),
                               (bpy.app.handlers.save_pre, _on_save_pre),
                               (bpy.app.handlers.undo_pre, _on_undo_pre)):
        if callback in handlers:
            handlers.remove(callback)
    if _draw_handle is not None:
        bpy.types.SpaceView3D.draw_handler_remove(_draw_handle, 'WINDOW')
        _draw_handle = None
    bpy.utils.unregister_class(VIEW3D_OT_final_dimensions_offset_cut)
    del bpy.types.WindowManager.final_dimensions_offset_distance
