"""Small anti-aliased screen-space strokes for the viewport ruler.

Coordinates and widths are in region pixels.  The fragment shader computes
coverage rather than relying on platform-dependent wide OpenGL lines.
"""

import math

import gpu
from gpu_extras.batch import batch_for_shader


_shader = None


def _get_shader():
    global _shader
    if _shader is None:
        interface = gpu.types.GPUStageInterfaceInfo('ruler_stroke_interface')
        interface.smooth('VEC2', 'pixelPos')
        info = gpu.types.GPUShaderCreateInfo()
        info.push_constant('MAT4', 'ModelViewProjectionMatrix')
        info.push_constant('VEC2', 'strokeA')
        info.push_constant('VEC2', 'strokeB')
        info.push_constant('FLOAT', 'halfWidth')
        info.push_constant('FLOAT', 'ringRadius')
        info.push_constant('VEC4', 'color')
        info.vertex_in(0, 'VEC2', 'pos')
        info.vertex_out(interface)
        info.fragment_out(0, 'VEC4', 'FragColor')
        info.vertex_source('''
            void main() {
                pixelPos = pos;
                gl_Position = ModelViewProjectionMatrix * vec4(pos, 0.0, 1.0);
            }
            ''')
        info.fragment_source('''
            void main() {
                vec2 delta = strokeB - strokeA;
                float lengthSquared = dot(delta, delta);
                float along = lengthSquared > 0.0
                    ? clamp(dot(pixelPos - strokeA, delta) / lengthSquared, 0.0, 1.0)
                    : 0.0;
                float distanceToCenter = length(pixelPos - strokeA - along * delta);
                float distanceToStroke = ringRadius > 0.0
                    ? abs(distanceToCenter - ringRadius) : distanceToCenter;
                float coverage = 1.0 - smoothstep(
                    halfWidth - 0.5, halfWidth + 0.5, distanceToStroke);
                FragColor = vec4(color.rgb, color.a * coverage);
            }
            ''')
        _shader = gpu.shader.create_from_info(info)
    return _shader


def _stroke(a, b, color, width, ring_radius=0.0):
    if a is None or b is None:
        return
    ax, ay = float(a[0]), float(a[1])
    bx, by = float(b[0]), float(b[1])
    if not all(math.isfinite(value) for value in (ax, ay, bx, by)):
        return
    half_width = max(float(width), 0.0) / 2.0
    if half_width <= 0.0:
        return
    padding = half_width + 1.0
    if ring_radius > 0.0:
        extent = ring_radius + padding
        x0, x1 = ax - extent, ax + extent
        y0, y1 = ay - extent, ay + extent
        corners = ((x0, y0), (x1, y0), (x1, y1), (x0, y1))
    else:
        dx, dy = bx - ax, by - ay
        length = math.hypot(dx, dy)
        if length > 0.0:
            ux, uy = dx / length, dy / length
        else:
            ux, uy = 1.0, 0.0
        nx, ny = -uy, ux
        start = (ax - ux * padding, ay - uy * padding)
        end = (bx + ux * padding, by + uy * padding)
        corners = (
            (start[0] - nx * padding, start[1] - ny * padding),
            (end[0] - nx * padding, end[1] - ny * padding),
            (end[0] + nx * padding, end[1] + ny * padding),
            (start[0] + nx * padding, start[1] + ny * padding),
        )
    positions = (corners[0], corners[1], corners[2],
                 corners[0], corners[2], corners[3])
    shader = _get_shader()
    batch = batch_for_shader(shader, 'TRIS', {'pos': positions})
    old_blend, old_depth = gpu.state.blend_get(), gpu.state.depth_test_get()
    try:
        gpu.state.blend_set('ALPHA')
        gpu.state.depth_test_set('NONE')
        shader.bind()
        shader.uniform_float('strokeA', (ax, ay))
        shader.uniform_float('strokeB', (bx, by))
        shader.uniform_float('halfWidth', half_width)
        shader.uniform_float('ringRadius', float(ring_radius))
        shader.uniform_float('color', color)
        batch.draw(shader)
    finally:
        gpu.state.depth_test_set(old_depth)
        gpu.state.blend_set(old_blend)


def line(a, b, color, width=2.0):
    """Draw a capsule with round ends between two region-pixel positions."""
    _stroke(a, b, color, width)


def ring(center, radius, color, width=2.0):
    """Draw a smooth circular endpoint outline in region pixels."""
    _stroke(center, center, color, width, float(radius))
