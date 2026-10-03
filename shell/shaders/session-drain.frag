#version 440
layout(location = 0) in vec2 qt_TexCoord0;
layout(location = 0) out vec4 fragColor;
layout(std140, binding = 0) uniform buf {
    mat4 qt_Matrix;
    float qt_Opacity;
    vec2 size;
    float edge;
    float clock;
};
layout(binding = 1) uniform sampler2D source;
// C8's sheetDistance/plateField and dockRegular edge optics from dock.frag.
// The PNG already contains the Regular material, so only the moving edge is
// applied here. Regrading the image would change the held greeter's glass.
float sheetDistance(vec2 p) {
    float wave = 24.0 * (.55 * sin(p.x * .0061 + clock * 2.3)
                       + .30 * sin(p.x * .0137 - clock * 3.1 + 1.3)
                       + .15 * sin(p.x * .029 + clock * 4.7 + 2.1));
    float slope = 24.0 * (.55 * .0061 * cos(p.x * .0061 + clock * 2.3)
                        + .30 * .0137 * cos(p.x * .0137 - clock * 3.1 + 1.3)
                        + .15 * .029 * cos(p.x * .029 + clock * 4.7 + 2.1));
    return (edge + wave - p.y) / sqrt(1.0 + slope * slope);
}
void main() {
    vec2 p = qt_TexCoord0 * size;
    float distance = sheetDistance(p), depth = max(-distance, 0.0);
    const float e = .35;
    vec2 gradient = vec2(sheetDistance(p + vec2(e, 0)) - sheetDistance(p - vec2(e, 0)),
                         sheetDistance(p + vec2(0, e)) - sheetDistance(p - vec2(0, e)));
    vec2 normal = gradient / max(length(gradient), .0001);
    float x = 1.0 - clamp(depth / 6.0, 0.0, 1.0);
    float slope = min(8.0, (2.2 / 6.0) * x / sqrt(max(1.0 - x*x, .0025)));
    vec2 bent = p - normal * 4.5 * slope / sqrt(1.0 + slope*slope);
    vec3 color = texture(source, clamp(bent / size, vec2(0), vec2(1))).rgb;
    float side = max(0.0, -dot(normal, vec2(cos(3.97935), sin(3.97935))));
    color *= 1.0 - .055 * exp(-depth / 1.8) * (.25 + .75 * side);
    color += vec3(.18) * max(0.0, -normal.y) * (1.0 - smoothstep(0.0, 1.0, depth));
    float aa = max(fwidth(distance), .5);
    float coverage = 1.0 - smoothstep(-aa*.5, aa*.5, distance);
    float sd = max(sheetDistance(p - vec2(0, 5)), 0.0);
    float shadow = .16 * exp(-.5 * sd*sd / (14.0*14.0));
    fragColor = vec4(clamp(color, 0.0, 1.0) * coverage,
                     coverage + shadow * (1.0 - coverage)) * qt_Opacity;
}
