#version 440
// Half the size of the source in one tap: the centre of a target texel falls on the corner of
// four source texels, so bilinear filtering returns their average (a 2×2 box). DockBackdrop
// chains it (capture → half → quarter) before the Gaussian passes (gauss.frag).
layout(location = 0) in vec2 qt_TexCoord0;
layout(location = 0) out vec4 fragColor;
layout(std140, binding = 0) uniform buf {
    mat4 qt_Matrix;
    float qt_Opacity;
    vec4 uvRect;
};
layout(binding = 1) uniform sampler2D source;
void main() {
    fragColor = vec4(texture(source, uvRect.xy + qt_TexCoord0 * uvRect.zw).rgb, 1.0) * qt_Opacity;
}
