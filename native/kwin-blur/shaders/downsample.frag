// SPDX-License-Identifier: GPL-2.0-or-later
// Adapted from KDE KWin v6.8.2 src/plugins/blur/shaders/downsample.frag
#version 140
uniform sampler2D texUnit;
uniform float offset;
uniform vec2 halfpixel;

in vec2 uv;

out vec4 fragColor;

void main(void)
{
    vec4 sum = texture2D(texUnit, uv) * 4.0;
    sum += texture2D(texUnit, uv - halfpixel.xy * offset);
    sum += texture2D(texUnit, uv + halfpixel.xy * offset);
    sum += texture2D(texUnit, uv + vec2(halfpixel.x, -halfpixel.y) * offset);
    sum += texture2D(texUnit, uv - vec2(halfpixel.x, -halfpixel.y) * offset);

    fragColor = sum / 8.0;
}
