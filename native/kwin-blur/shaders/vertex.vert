// SPDX-License-Identifier: GPL-2.0-or-later
// Adapted from KDE KWin v6.8.2 src/plugins/blur/shaders/vertex.vert
#version 140

uniform mat4 modelViewProjectionMatrix;

in vec2 position;
in vec2 texcoord;

out vec2 uv;

void main(void)
{
    gl_Position = modelViewProjectionMatrix * vec4(position, 0.0, 1.0);
    uv = texcoord;
}
