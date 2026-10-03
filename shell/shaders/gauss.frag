#version 440
// One separable Gaussian pass for the glass backdrop along `axis`, at the size of its source:
// DockBackdrop box-downsamples the capture first (down.frag), so taps one texel apart cover
// every texel. Taps go in pairs placed between two texels, where bilinear filtering sums both
// with their weights: ⌈3σ⌉ texels each side, at most 64.
// 27.09: this pass used to read the full-resolution capture with 25 taps σ/4 target texels
// apart (9.4 physical px for Regular at 1.25) — fine detail under the glass (terminal text,
// window edges) came through the plate as a lattice with that pitch ("stripes").
layout(location = 0) in vec2 qt_TexCoord0;
layout(location = 0) out vec4 fragColor;
layout(std140, binding = 0) uniform buf {
    mat4 qt_Matrix;
    float qt_Opacity;
    vec2 axis;   // one texel of the source: (1/width, 0) or (0, 1/height)
    float sigma; // in texels of the source
};
layout(binding = 1) uniform sampler2D source;
void main() {
    float s = max(sigma, 0.5);
    float reach = min(ceil(3.0 * s), 64.0);
    float k = -0.5 / (s * s);
    vec3 sum = texture(source, qt_TexCoord0).rgb;
    float total = 1.0;
    // A constant bound with a break: GLSL ES 1.00 (one of the qsb targets) allows no other loop.
    for (int n = 0; n < 32; n++) {
        float i = float(2 * n + 1);
        if (i > reach)
            break;
        float w1 = exp(k * i * i);
        float w2 = exp(k * (i + 1.0) * (i + 1.0));
        float w = w1 + w2;
        float off = i + w2 / w;
        sum += (texture(source, qt_TexCoord0 + axis * off).rgb + texture(source, qt_TexCoord0 - axis * off).rgb) * w;
        total += 2.0 * w;
    }
    fragColor = vec4(sum / total, 1.0) * qt_Opacity;
}
