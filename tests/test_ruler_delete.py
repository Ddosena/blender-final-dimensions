"""Headless hit-testing and deletion lifecycle; no mouse event injection."""
import sys
from pathlib import Path

import bpy

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import final_dimensions as addon
from final_dimensions import point_edit, ruler


def hit_tests():
    pick = ruler._nearest_screen_ruler
    lines = [(0, (10., 10.), (110., 10.)),
             (1, (10., 30.), (110., 30.))]
    assert pick((60., 13.), lines, 1) == 0  # Inactive line.
    assert pick((60., 35.), lines, 0) == 1
    assert pick((60., 20.), lines, 0) is None  # Empty space keeps orbit.
    assert pick((1., 10.), lines, 1) == 0  # Marker radius > line radius.
    assert pick((-3., 10.), lines, 0) is None
    assert pick((60., 10.), [(0, None, (110., 10.))], 0) is None
    assert pick((10., 10.), [(2, (10., 10.), (10., 10.))], 0) == 2
    overlap = [lines[0], (1, lines[0][1], lines[0][2])]
    assert pick((60., 10.), overlap, 0) == 0
    assert pick((60., 10.), overlap, 1) == 1
    diagonal = [(3, (10., 10.), (110., 110.))]
    assert pick((62., 60.), diagonal, 0) == 3
    print('PASS screen segments, endpoints, overlap priority and empty space', flush=True)


def lifecycle_tests():
    context = bpy.context
    manager = ruler.RulerCollection(context)
    pointer = context.window.as_pointer()
    ruler._states[pointer] = manager
    for number in range(3):
        item = ruler.RulerState(context)
        item.first = ruler._world_hit((number, 0., 0.))
        item.second = ruler._world_hit((number, 1., 0.))
        item.label = f'Ruler {number}'
        manager.items.append(item)
    manager.active = 2
    manager.recalculate()
    selected = manager.selected
    assert not ruler._remove_ruler(context, -1)
    assert not ruler._remove_ruler(context, 4)
    assert ruler._remove_ruler(context, 0)
    assert manager.selected is selected and manager.active == 1
    assert [item.label for item in manager.items] == ['Ruler 1', 'Ruler 2']
    # API activation creates the same native proxy as point selection, without
    # injecting input. Removing its ruler must restore the original selection.
    area = next(a for a in context.window.screen.areas if a.type == 'VIEW_3D')
    region = next(r for r in area.regions if r.type == 'WINDOW')
    original_active = context.view_layer.objects.active
    original_objects = set(obj.name for obj in bpy.data.objects)
    selected.area_pointer = area.as_pointer()
    with context.temp_override(area=area, region=region):
        assert point_edit.activate(bpy.context, manager, 1, 0, ruler._world_hit)
    assert point_edit.active(manager)
    assert ruler._remove_ruler(context, 1)
    assert not point_edit.active()
    assert set(obj.name for obj in bpy.data.objects) == original_objects
    assert context.view_layer.objects.active is original_active
    assert manager.active == 0 and manager.active_endpoint is None
    assert manager.selected.distance == 1.
    manager.begin_endpoint(0, 'REPLACE')
    assert not ruler._remove_ruler(context, 0), 'Do not delete during point placement'
    manager.cancel_interaction()
    assert ruler._remove_ruler(context, 0)
    assert ruler.collection(context.window) is None
    assert pointer not in ruler._sessions and not point_edit.active()
    print('PASS inactive/active/last deletion, selection indexes and interaction guard', flush=True)


addon.register()
try:
    hit_tests()
    lifecycle_tests()
    print('RULER DELETE PASS', bpy.app.version_string, flush=True)
finally:
    addon.unregister()
