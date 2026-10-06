"""Selected-edge viewport labels. Drawing uses cached primitive measurements only."""

from __future__ import annotations

import math

import bmesh
import blf
import bpy
from bpy_extras import view3d_utils

from .measure import format_length, measure_direction


_FONT = 0
_SIZE = 11
_GAP = 18.0
_HANDLE = None
_cache = {}  # window pointer -> primitive-only edge and directional result


def selected_edge(obj):
    """Return active or uniquely selected edit edge in world coordinates.

    The result contains ``a``, ``b``, ``length``, ``direction`` and a
    primitive ``signature``. None means no unambiguous valid edge.
    """
    if obj is None or obj.type != "MESH" or obj.mode != "EDIT":
        return None
    bm = bmesh.from_edit_mesh(obj.data)
    active = bm.select_history.active
    if isinstance(active, bmesh.types.BMEdge) and active.is_valid and active.select:
        edge = active
    else:
        edge = None
        for candidate in bm.edges:
            if candidate.select:
                if edge is not None:
                    return None
                edge = candidate
    if edge is None or not edge.is_valid:
        return None
    a = obj.matrix_world @ edge.verts[0].co
    b = obj.matrix_world @ edge.verts[1].co
    delta = b - a
    length = delta.length
    if not math.isfinite(length) or length <= 1e-10:
        return None
    start = tuple(float(value) for value in a)
    end = tuple(float(value) for value in b)
    direction = tuple(float(value / length) for value in delta)
    return {
        "a": start,
        "b": end,
        "length": float(length),
        "direction": direction,
        "signature": (edge.index, start, end),
    }


def update(context, window, obj, epoch):
    """Update one window's cached directional span; return True on change."""
    pointer = window.as_pointer()
    active = bool(
        context.window_manager.final_dimensions_show_overlay
        and obj is not None
        and obj.visible_get(view_layer=context.view_layer)
    )
    edge = selected_edge(obj) if active else None
    if edge is None:
        return _cache.pop(pointer, None) is not None
    key = (
        context.scene.as_pointer(),
        context.view_layer.as_pointer(),
        obj.as_pointer(),
        edge["signature"],
        epoch,
    )
    old = _cache.get(pointer)
    if old is not None and old["key"] == key:
        return False
    try:
        measured = measure_direction(context, obj, edge["direction"])
        span = measured.get("span")
        warnings = tuple(measured.get("warnings", ()))
    except (RuntimeError, ValueError, ReferenceError) as exc:
        span = None
        warnings = (f"Directional measurement failed: {exc}",)
    _cache[pointer] = {
        "key": key,
        "edge": edge,
        "span": span,
        "warnings": warnings,
    }
    return True


def get(window):
    """Primitive cache accessor for diagnostics and tests."""
    return _cache.get(window.as_pointer())


def clear():
    _cache.clear()


def clear_window(window):
    return _cache.pop(window.as_pointer(), None) is not None


def prune(live_pointers):
    for pointer in tuple(_cache):
        if pointer not in live_pointers:
            del _cache[pointer]


def _text(x, y, text, color):
    blf.size(_FONT, _SIZE)
    blf.enable(_FONT, blf.SHADOW)
    blf.shadow(_FONT, 3, 0.0, 0.0, 0.0, 0.95)
    blf.shadow_offset(_FONT, 1, -1)
    blf.color(_FONT, *color)
    blf.position(_FONT, x, y, 0)
    blf.draw(_FONT, text)
    blf.disable(_FONT, blf.SHADOW)


def _draw():
    context = bpy.context
    window = context.window
    area = context.area
    region = context.region
    if window is None or area is None or region is None:
        return
    if not context.window_manager.final_dimensions_show_overlay:
        return
    if area.type != "VIEW_3D" or not area.spaces.active.overlay.show_overlays:
        return
    obj = context.view_layer.objects.active
    if obj is None or obj.type != "MESH" or obj.mode != "EDIT":
        return
    if not obj.visible_get(view_layer=context.view_layer):
        return
    cached = _cache.get(window.as_pointer())
    if cached is None or cached["key"][2] != obj.as_pointer():
        return
    edge = cached["edge"]
    current = selected_edge(obj)
    if current is None or current["signature"] != edge["signature"]:
        return
    p0 = view3d_utils.location_3d_to_region_2d(region, context.region_data, edge["a"])
    p1 = view3d_utils.location_3d_to_region_2d(region, context.region_data, edge["b"])
    if p0 is None or p1 is None:
        return
    dx, dy = p1.x - p0.x, p1.y - p0.y
    screen_length = math.hypot(dx, dy)
    if screen_length < 4.0:
        return
    nx, ny = -dy / screen_length, dx / screen_length
    center_x, center_y = (p0.x + p1.x) / 2, (p0.y + p1.y) / 2
    scene = context.scene
    cage_text = "e: " + format_length(scene, edge["length"])
    span = cached["span"]
    final_text = "f: " + format_length(scene, span)
    if cached["warnings"]:
        final_text += " *"
    blf.size(_FONT, _SIZE)
    from . import hover
    local_section = hover.get(window, area)
    labels = (
        (1, cage_text, (1.0, 0.55, 0.18, 1.0)),
        (-1, final_text, (0.25, 0.83, 1.0, 1.0)),
    )
    if (context.window_manager.final_dimensions_hover and local_section is not None
            and local_section.get('section') is not None):
        labels = labels[:1]  # the hover label now supplies the local f
    for sign, text, color in labels:
        width, height = blf.dimensions(_FONT, text)
        # Text is horizontal. Move its whole rectangle past the edge normal.
        offset = _GAP + abs(nx) * width / 2 + abs(ny) * height / 2
        x = center_x + sign * nx * offset - width / 2
        y = center_y + sign * ny * offset - height / 2
        # Do not clamp across the edge onto the opposite side at screen borders.
        if x < 4 or y < 4 or x + width > region.width - 4 or y + height > region.height - 4:
            continue
        _text(x, y, text, color)


def register():
    global _HANDLE
    if _HANDLE is None:
        _HANDLE = bpy.types.SpaceView3D.draw_handler_add(
            _draw, (), "WINDOW", "POST_PIXEL"
        )


def unregister():
    global _HANDLE
    if _HANDLE is not None:
        bpy.types.SpaceView3D.draw_handler_remove(_HANDLE, "WINDOW")
        _HANDLE = None
    clear()
