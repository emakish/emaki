#version 440
// Qt port of docs/mockups/liquid-glass/glass.frag (the dock recipe accepted
// 2026-09-24). Optics, constants and the order of operations are the mockup's; only the
// plumbing differs:
//   - uniforms live in a std140 block (Qt ShaderEffect), ints became floats;
//   - uDrops[8]/uDropParams[8] are eight named vec4s (ShaderEffect maps names, not arrays);
//   - vPosition = uOrigin + qt_TexCoord0 * uItemSize (logical scene pixels, top left);
//   - uIcons is a ShaderEffectSource, i.e. premultiplied: iconLayer composites
//     c*(1-a)+rgb, which equals the mockup's straight-alpha mix(c, rgb, a);
//   - uAverage/uAverageB (five samples of the blurred backdrop) are taken here, per
//     fragment, from uBlurred, instead of on the CPU from a read-back copy: the backdrop
//     can be a live capture, and reading it back every frame would stall the GPU;
//   - output additionally multiplied by qt_Opacity.
layout(location = 0) in vec2 qt_TexCoord0;
layout(location = 0) out vec4 fragColor;
layout(std140, binding = 0) uniform buf {
    mat4 qt_Matrix;
    float qt_Opacity;
    vec2 uOrigin;
    vec2 uItemSize;
    float uMaterial;
    float uRegularMix;
    float uRegularBlur;
    float uRegularLight;
    float uRegularDark;
    float uRegularContrast;
    float uRegularTint;
    float uRegularSaturation;
    float uRegularDock;
    float uIsDock;
    float uFlat;
    vec4 uFlatColor;
    vec2 uSceneSize;
    vec4 uCover;
    vec4 uRect;
    vec4 uRectB;
    float uRadius;
    float uRadiusB;
    float uUnion;
    float uHasB;
    float uEdge;
    float uThickness;
    float uRefraction;
    float uDispersion;
    float uLightAngle;
    float uSpecular;
    float uShininess;
    float uRim;
    float uRimWidth;
    float uInnerShadow;
    float uInnerWidth;
    float uShadow;
    float uShadowSoftness;
    float uShadowOffset;
    float uTint;
    vec3 uTintColor;
    float uAdaptation;
    float uContrast;
    float uSaturation;
    float uBlur;
    float uActivity;
    float uOpacity;
    vec4 uTrack;
    float uTrackValue;
    float uTrackEnabled;
    float uOnlyTrack;
    vec4 uRectC;
    float uHasC;
    float uRadiusC;
    float uUnionC;
    float uDropCount;
    vec4 uDrop0;
    vec4 uDrop1;
    vec4 uDrop2;
    vec4 uDrop3;
    vec4 uDrop4;
    vec4 uDrop5;
    vec4 uDrop6;
    vec4 uDrop7;
    vec4 uDropParam0;
    vec4 uDropParam1;
    vec4 uDropParam2;
    vec4 uDropParam3;
    vec4 uDropParam4;
    vec4 uDropParam5;
    vec4 uDropParam6;
    vec4 uDropParam7;
    vec4 uBulge;
    float uDematerialize;
    float uHasIcons;
    float uDropDome;
    float uRimLight;
    float uRimLightWidth;
    float uRegularBase; // mockup regular.baseMode: <0 automatic palette, else fixed
    float uLiveTop;     // above this y (the app bubble's top) glass sees windows; else wallpaper
    float uRegularBaseB; // the same for the popup (uRectB); <0 automatic
    vec4 uSelectedColor; // the selected drop's colour: rgb, a = share of its body
    vec4 uDropSelected0; // how selected drop 0..3 is, 0..1 (today, the chosen player, the open tab)
    vec4 uDropSelected1; // the same for drops 4..7
    // Opt-in lock sheet. Existing islands keep exactly the rectangular branch.
    float uWaveEnabled;
    vec4 uWaveEdge; // edge position, amplitude, side (+1 pour / -1 drain), time
    vec4 uSharpRect;
    vec4 uSharpBounds;
    vec4 uWallSharpRect;
    vec4 uWallSharpBounds;
    float uHasDynamicIcons;
    vec4 uDynamicRect;
};
layout(binding = 1) uniform sampler2D uSharp;
layout(binding = 2) uniform sampler2D uBlurred;
layout(binding = 3) uniform sampler2D uRegularLow;
layout(binding = 4) uniform sampler2D uRegularHigh;
layout(binding = 5) uniform sampler2D uIcons;
// The bubble does not see windows (2026-09-25): where it reaches past the plate it
// bends the wallpaper only, never the edge of a window above the dock. The plate and the
// menu keep the live backdrop (uSharp/uBlurred). Without a capture both pairs are the same.
layout(binding = 6) uniform sampler2D uWallSharp;
layout(binding = 7) uniform sampler2D uWallBlurred;
// The menu's text and marks (premultiplied, scene pixels): drawn on the menu pane only,
// never on the plate it slides out from under.
layout(binding = 8) uniform sampler2D uMenu;
layout(binding = 9) uniform sampler2D uDynamicIcons;

const vec3 ACCENT = vec3(0.886275, 0.450980, 0.247059); // tokens.toml #e2733f
float luminance(vec3 c) { return dot(c, vec3(0.2126, 0.7152, 0.0722)); }

vec4 dropRect(int i) {
    if (i == 0) return uDrop0;
    if (i == 1) return uDrop1;
    if (i == 2) return uDrop2;
    if (i == 3) return uDrop3;
    if (i == 4) return uDrop4;
    if (i == 5) return uDrop5;
    if (i == 6) return uDrop6;
    return uDrop7;
}
vec4 dropParams(int i) {
    if (i == 0) return uDropParam0;
    if (i == 1) return uDropParam1;
    if (i == 2) return uDropParam2;
    if (i == 3) return uDropParam3;
    if (i == 4) return uDropParam4;
    if (i == 5) return uDropParam5;
    if (i == 6) return uDropParam6;
    return uDropParam7;
}
int dropCount() { return int(uDropCount + 0.5); }

vec2 wallpaperUV(vec2 p) { return clamp(uCover.xy + p*uCover.zw, 0.0, 1.0); }
vec2 sharpUV(vec2 p) { return clamp(uSharpRect.xy + wallpaperUV(p)*uSharpRect.zw, uSharpBounds.xy, uSharpBounds.zw); }
vec2 wallSharpUV(vec2 p) { return clamp(uWallSharpRect.xy + wallpaperUV(p)*uWallSharpRect.zw, uWallSharpBounds.xy, uWallSharpBounds.zw); }

// Mockup average(rect): five samples of the blurred map, then regularPalette(avg).
vec3 averageOf(vec4 r) {
    vec3 s = texture(uBlurred, wallpaperUV(r.xy + r.zw*vec2(.5,.5))).rgb;
    s += texture(uBlurred, wallpaperUV(r.xy + r.zw*vec2(.2,.2))).rgb;
    s += texture(uBlurred, wallpaperUV(r.xy + r.zw*vec2(.8,.2))).rgb;
    s += texture(uBlurred, wallpaperUV(r.xy + r.zw*vec2(.2,.8))).rgb;
    s += texture(uBlurred, wallpaperUV(r.xy + r.zw*vec2(.8,.8))).rgb;
    return s / 5.0;
}
float paletteOf(vec3 avg, float base) {
    float lum = avg.r*.2126 + avg.g*.7152 + avg.b*.0722;
    return base < 0.0 ? (lum > .52 ? 1.0 : lum < .32 ? 0.0 : (lum-.32)/.20) : base;
}
vec3 uAverage;
float uLightMode;
vec3 uAverageB;
float uLightModeB;

// 1. Rounded rectangle SDF and polynomial smooth union: a single shared contour.
float roundedRect(vec2 p, vec4 r, float radius) {
    float rad = min(radius, min(r.z, r.w) * 0.5);
    vec2 q = abs(p - r.xy - r.zw * 0.5) - (r.zw * 0.5 - rad);
    return length(max(q, 0.0)) + min(max(q.x, q.y), 0.0) - rad;
}
float smoothUnion(float a, float b, float radius) {
    float k = max(radius, 0.001);
    float h = clamp(0.5 + 0.5 * (b-a) / k, 0.0, 1.0);
    return mix(b, a, h) - k * h * (1.0-h);
}
float bulge(vec2 p) {
    vec2 q = (p-uBulge.xy)/max(uBulge.z, 1.0);
    return uBulge.w * exp(-dot(q,q)*2.0);
}
// One signed field for the lock's mask, lens, rim, external shadow and the plate
// refracted through each drop. The three sines are lock-template.html waveAt().
float waveAt(float x) {
    float t=uWaveEdge.w;
    return uWaveEdge.y*(.55*sin(x*.0061+t*2.3)+.30*sin(x*.0137-t*3.1+1.3)+.15*sin(x*.029+t*4.7+2.1));
}
float waveSlope(float x) {
    float t=uWaveEdge.w;
    return uWaveEdge.y*(.55*.0061*cos(x*.0061+t*2.3)+.30*.0137*cos(x*.0137-t*3.1+1.3)+.15*.029*cos(x*.029+t*4.7+2.1));
}
float sheetDistance(vec2 p) {
    float slope=waveSlope(p.x);
    float edge=uWaveEdge.z*(p.y-uWaveEdge.x-waveAt(p.x))/sqrt(1.0+slope*slope);
    vec2 q=p-uRect.xy;
    float vessel=-min(min(q.x,q.y),min(uRect.z-q.x,uRect.w-q.y));
    return max(edge,vessel);
}
float plateDistance(vec2 p) {
    return uWaveEnabled>.5?sheetDistance(p):roundedRect(p,uRect,uRadius);
}
float shape(vec2 p) {
    float a = plateDistance(p);
    a -= bulge(p)*1.6;
    for (int i=0; i<8; i++) {
        if (i>=dropCount()) break;
        a = smoothUnion(a, roundedRect(p,dropRect(i),dropParams(i).x),dropParams(i).y);
    }
    if (uHasC > 0.5) a = smoothUnion(a,roundedRect(p,uRectC,uRadiusC),uUnionC);
    if (uHasB > 0.5) a = smoothUnion(a,roundedRect(p,uRectB,uRadiusB),uUnion);
    return a;
}

// 2. Lens height h=thickness*sqrt(1-(1-depth/edge)^2).
vec2 thicknessProfile(float depth) {
    float width = min(uEdge, min(uRect.z, uRect.w) * 0.47);
    float t = clamp(depth / max(width, 0.1), 0.0, 1.0);
    float x = 1.0-t;
    float height = sqrt(max(1.0-x*x, 0.0025));
    float slope = min(8.0, uThickness / max(width, 0.1) * x / height);
    return vec2(height * uThickness, slope);
}

// 3. SDF gradient and pseudo-3D surface normal, shared by refraction and lighting.
vec2 contourNormal(vec2 p) {
    const float e = 0.35;
    vec2 g = vec2(shape(p+vec2(e,0))-shape(p-vec2(e,0)),
                  shape(p+vec2(0,e))-shape(p-vec2(0,e)));
    return g / max(length(g), 0.0001);
}
vec3 surfaceNormal(vec2 n, float slope) { return normalize(vec3(n*slope, 1.0)); }

// 4. Screen-fixed refraction.
vec2 refractedPosition(vec2 p, vec2 n, float slope) {
    float bend = slope / sqrt(1.0+slope*slope);
    return p - n * uRefraction * bend * (1.0 + uActivity*0.12) * (1.0-uDematerialize);
}

vec3 localTrack(vec3 c, vec2 p) {
    if (uTrackEnabled < 0.5 || uOnlyTrack < 0.5) return c;
    vec4 tr = vec4(uTrack.x, uTrack.y-uTrack.w*0.5, uTrack.z, uTrack.w);
    float mask = 1.0-smoothstep(-0.6, 0.6, roundedRect(p,tr,uTrack.w*0.5));
    float fill = 1.0-smoothstep(-0.6,0.6,p.x-(uTrack.x+uTrack.z*uTrackValue));
    vec3 rest = mix(vec3(0.83,0.78,0.85),vec3(0.20,0.16,0.20),uLightMode);
    vec3 accent = uMaterial > 0.5 ? vec3(0.86,0.24,0.48) : ACCENT;
    return mix(c, mix(rest, accent, fill), mask*mix(0.42,0.94,fill));
}
vec3 sharpSample(vec2 p) { return localTrack(texture(uSharp,sharpUV(p)).rgb,p); }

// 5. Independent RGB separation, specified as total R-to-B distance in pixels.
vec3 chromaticRefraction(vec2 p, vec2 n, float slope) {
    float delta = uDispersion * 0.5 * slope/sqrt(1.0+slope*slope) * (1.0-uDematerialize);
    return vec3(sharpSample(p-n*delta).r, sharpSample(p).g, sharpSample(p+n*delta).b);
}

// 6. Sharp edge / preblurred centre.
vec3 centreBlur(vec3 sharp, vec2 p, float depth) {
    float width = min(uEdge,min(uRect.z,uRect.w)*0.47);
    float amount = smoothstep(0.0,width,depth) * uBlur;
    vec3 soft = localTrack(texture(uBlurred,wallpaperUV(p)).rgb,p);
    return mix(sharp,soft,amount);
}

// 7. Directional Blinn-Phong highlight. Angle uses screen axes.
vec3 directionalSpecular(vec3 c, vec3 n, float slope) {
    vec3 light = normalize(vec3(cos(uLightAngle),sin(uLightAngle),1.25));
    vec3 halfVector = normalize(light+vec3(0,0,1));
    float power = max(4.0,uShininess*(1.0-uActivity*0.3));
    float highlight = pow(max(dot(n,halfVector),0.0),power);
    highlight *= smoothstep(0.02,0.22,slope) * uSpecular * (1.0+uActivity*0.45);
    return c + vec3(1.0,0.965,0.92)*highlight;
}

// 8. Schlick Fresnel, confined to a thin silhouette rim.
vec3 fresnelRim(vec3 c, vec3 n, vec2 normal2, float depth) {
    float fresnel = 0.04+0.96*pow(1.0-max(n.z,0.0),5.0);
    float band = 1.0-smoothstep(0.0,max(0.1,uRimWidth),depth);
    float directional = 0.48+0.52*abs(dot(normal2,vec2(cos(uLightAngle),sin(uLightAngle))));
    return c + vec3(0.92,0.96,1.0) * band * (0.3+0.7*fresnel)*uRim*directional;
}

// 9. Narrow inner shadow, opposite the main light.
vec3 innerShadow(vec3 c, vec2 normal2, float depth) {
    float side = max(0.0,-dot(normal2,vec2(cos(uLightAngle),sin(uLightAngle))));
    float band = exp(-depth/max(0.1,uInnerWidth));
    return c * (1.0-uInnerShadow*band*(0.25+0.75*side));
}

// 10. External SDF shadow, premultiplied alpha.
float outerShadow(vec2 p) {
    float dist = max(shape(p-vec2(0,uShadowOffset)),0.0);
    float sigma = max(0.5,uShadowSoftness*0.5);
    return uShadow * exp(-0.5*dist*dist/(sigma*sigma));
}

// 11. Brightness/contrast adaptation.
vec3 brightnessAdaptation(vec3 c) {
    vec3 target = mix(uAverage*0.72,vec3(0.92,0.91,0.89),uLightMode);
    c = mix(c,mix(target,c,uContrast),uAdaptation*0.55);
    return c;
}
vec3 materialTint(vec3 c) { return mix(c,uTintColor,uTint); }

// 12. Saturation around luminance.
vec3 saturation(vec3 c) { return mix(vec3(luminance(c)),c,uSaturation); }

// Regular: strong cached blur across the whole plate.
vec3 regularMaterial(vec2 p, vec2 n, float slope, float depth) {
    vec2 bent = refractedPosition(p,n,slope);
    vec3 soft = mix(texture(uRegularLow,wallpaperUV(bent)).rgb,
                    texture(uRegularHigh,wallpaperUV(bent)).rgb,uRegularMix);
    vec3 c = mix(texture(uSharp,sharpUV(bent)).rgb,soft,uRegularBlur);
    float popup = uHasB > 0.5 ? 1.0-smoothstep(-5.0,6.0,roundedRect(p,uRectB,uRadiusB)) : 0.0;
    float lightMode = mix(uLightMode,uLightModeB,popup);
    c = mix(mix(uAverage,uAverageB,popup),c,uRegularContrast);
    c = mix(vec3(luminance(c)),c,uRegularTint*uRegularSaturation);
    float base = mix(uRegularDark,uRegularLight,lightMode);
    float dock = uIsDock*(1.0-popup);
    base = mix(base,uRegularDock,dock);
    c = mix(c,mix(vec3(0.055,0.050,0.065),vec3(1.0),lightMode),base);
    c = localTrack(c,p);
    c = innerShadow(c,n,depth);
    float top = max(0.0,-n.y);
    c += vec3(1.0)*uRim*top*(1.0-smoothstep(0.0,uRimWidth,depth));
    return c;
}

// Mixed dock (uIsDock=2): Regular foundation, one Clear body, Regular popup.
vec3 rectField(vec2 p, vec4 r, float radius) {
    float rad=min(radius,min(r.z,r.w)*0.5);
    vec2 offset=p-r.xy-r.zw*0.5;
    vec2 q=abs(offset)-(r.zw*0.5-rad),outside=max(q,0.0);
    float len=length(outside);
    vec2 n=len>0.0001 ? outside/len : (q.x>q.y?vec2(1,0):vec2(0,1));
    return vec3(n*sign(offset),len+min(max(q.x,q.y),0.0)-rad);
}
vec3 plateField(vec2 p) {
    if(uWaveEnabled<.5) return rectField(p,uRect,uRadius);
    const float e=.35;
    vec2 g=vec2(sheetDistance(p+vec2(e,0))-sheetDistance(p-vec2(e,0)),
                sheetDistance(p+vec2(0,e))-sheetDistance(p-vec2(0,e)));
    return vec3(g/max(length(g),.0001),sheetDistance(p));
}
vec3 unionField(vec3 a, vec3 b, float k) {
    k=max(k,.001);
    float h=clamp(.5+.5*(b.z-a.z)/k,0.0,1.0);
    return vec3(mix(b.xy,a.xy,h),mix(b.z,a.z,h)-k*h*(1.0-h));
}
void dockFields(vec2 p, out vec3 whole, out vec3 liquid, out float strength) {
    whole=plateField(p);liquid=vec3(0,0,10000);
    vec3 resting=vec3(0,0,10000);strength=uBulge.y>.5?mix(.32,1.0,uBulge.z):uBulge.z;
    for(int i=0;i<8;i++) {
        if(i>=dropCount()) break;
        vec4 dp=dropParams(i);
        vec3 cap=rectField(p,dropRect(i),dp.x);
        if(dp.z>.5) {
            if(uBulge.y<.5&&uWaveEnabled<.5)cap.z+=(1.0-dp.w)*26.0;
            liquid=unionField(liquid,cap,dp.y);
        } else {whole=unionField(whole,cap,uBulge.x);if(cap.z<resting.z)resting=cap;}
    }
    if(uHasC>.5) liquid=unionField(liquid,rectField(p,uRectC,uRadiusC),uUnionC);
    if(uWaveEnabled<.5) whole=unionField(whole,liquid,uBulge.x*(uBulge.y>.5?1.0:uBulge.z));
    if(resting.z<liquid.z){liquid=resting;strength=.32;}
}
float dockSlope(float depth,float edge,float thickness) {
    float x=1.0-clamp(depth/max(edge,.1),0.0,1.0);
    return min(8.0,thickness/max(edge,.1)*x/sqrt(max(1.0-x*x,.0025)));
}
vec2 fieldNormal(vec3 field) {return field.xy/max(length(field.xy),.0001);}
float popupMask(vec2 p) {
    return uHasB>.5?1.0-smoothstep(-4.0,5.0,roundedRect(p,uRectB,uRadiusB)):0.0;
}
vec3 dockRegular(vec2 p,vec2 normal,float depth,float popup) {
    float slope=dockSlope(depth,6.0,2.2);
    vec2 bent=p-normal*4.5*slope/sqrt(1.0+slope*slope)*(1.0-uDematerialize);
    vec3 soft=mix(texture(uRegularLow,wallpaperUV(bent)).rgb,
                  texture(uRegularHigh,wallpaperUV(bent)).rgb,uRegularMix);
    // The menu is fully frosted: over a window a .1 sharp share let its thin lines show
    // through as stripes (2026-09-25). The plate keeps the mockup's .9.
    vec3 c=mix(texture(uSharp,sharpUV(bent)).rgb,soft,mix(uRegularBlur,1.0,popup));
    c=mix(mix(uAverage,uAverageB,popup),c,uRegularContrast);
    c=mix(vec3(luminance(c)),c,uRegularTint*uRegularSaturation);
    float light=mix(uLightMode,uLightModeB,popup);
    c=mix(c,mix(vec3(.055,.05,.065),vec3(1),light),uRegularDock);
    float side=max(0.0,-dot(normal,vec2(cos(uLightAngle),sin(uLightAngle))));
    c*=1.0-.055*exp(-depth/1.8)*(.25+.75*side);
    c+=vec3(.18)*max(0.0,-normal.y)*(1.0-smoothstep(0.0,1.0,depth));
    return c;
}
// Icons live under the glass (premultiplied ShaderEffectSource, see header).
vec3 iconLayer(vec3 c, vec2 p) {
    if (uHasIcons < .5) return c;
    vec4 icon=texture(uIcons,p/uSceneSize);
    if (uHasDynamicIcons > .5) {
        vec2 uv=(p-uDynamicRect.xy)/uDynamicRect.zw;
        if (all(greaterThanEqual(uv,vec2(0))) && all(lessThanEqual(uv,vec2(1)))) {
            vec4 dynamicIcon=texture(uDynamicIcons,uv);
            icon=icon*(1.0-dynamicIcon.a)+dynamicIcon;
        }
    }
    return c*(1.0-icon.a)+icon.rgb;
}
// The menu (uRectB) is a calm Regular pane that slides out from under the plate
// (2026-09-25, after trying a menu grown out of the bubble): not part of the liquid
// body, drawn behind the plate. Its text lives in uIcons, so the hover drop bends it.
vec3 menuField(vec2 p) {
    if (uHasB < .5) return vec3(0,0,10000);
    vec3 f=rectField(p,uRectB,uRadiusB);
    // Below the plate's bottom edge the menu does not exist yet: it is still under the plate.
    f.z=max(f.z,p.y-(uRect.y+uRect.w));
    return f;
}
vec3 menuPane(vec2 p, vec3 menu) {
    vec3 c=dockRegular(p,fieldNormal(menu),max(-menu.z,0.0),1.0);
    if (uHasB < .5) return c;
    vec4 ink=texture(uMenu,p/uSceneSize);
    return c*(1.0-ink.a)+ink.rgb;
}
// What lies under a liquid drop at p: plate over menu over the backdrop, plus the content
// layer. live: 0 = the bubble at the plate (sees the wallpaper only), 1 = above the app
// bubble, over windows (sees them, 2026-09-25).
vec3 dockBackdrop(vec2 p, float live) {
    vec3 foundation=plateField(p);
    float mask=1.0-smoothstep(-.6,.6,foundation.z);
    vec3 behind=mix(texture(uWallSharp,wallSharpUV(p)).rgb,texture(uSharp,sharpUV(p)).rgb,live);
    vec3 menu=menuField(p);
    behind=mix(behind,menuPane(p,menu),1.0-smoothstep(-.6,.6,menu.z));
    return iconLayer(mix(behind,dockRegular(p,fieldNormal(foundation),max(-foundation.z,0.0),0.0),mask),p);
}
// The liquid member p belongs to: its rectangle, corner radius and life (params.w, 0..1).
vec4 nearestDrop(vec2 p, out float radius, out float life) {
    vec4 best=vec4(0);float bestZ=1e9;radius=0.0;life=1.0;
    for(int i=0;i<8;i++) {
        if(i>=dropCount()) break;
        vec4 dp=dropParams(i);
        if(dp.z<.5) continue;
        vec4 r=dropRect(i);
        float z=rectField(p,r,dp.x).z;
        if(z<bestZ){bestZ=z;best=r;radius=dp.x;life=dp.w;}
    }
    return best;
}
float dropSelected(int i) {
    if (i == 0) return uDropSelected0.x;
    if (i == 1) return uDropSelected0.y;
    if (i == 2) return uDropSelected0.z;
    if (i == 3) return uDropSelected0.w;
    if (i == 4) return uDropSelected1.x;
    if (i == 5) return uDropSelected1.y;
    if (i == 6) return uDropSelected1.z;
    return uDropSelected1.w;
}
// How much of the selected colour the liquid takes at p (2026-09-27: the selected /
// current drop is orange, hover the plain one). A selected drop colours its own body, also
// where a hover drop lies on the same spot; the others stay glass. Melts with its drop.
float selectedShare(vec2 p) {
    if (uSelectedColor.a <= 0.0 || dot(uDropSelected0+uDropSelected1,vec4(1.0)) <= 0.0) return 0.0;
    float share=0.0;
    for(int i=0;i<8;i++) {
        if(i>=dropCount()) break;
        vec4 dp=dropParams(i);
        float mark=dropSelected(i);
        if(dp.z<.5||mark<=0.0) continue;
        float z=rectField(p,dropRect(i),dp.x).z;
        if(uBulge.y<.5) z+=(1.0-dp.w)*26.0;
        share=max(share,(1.0-smoothstep(-1.2,1.2,z))*mark);
    }
    return share*uSelectedColor.a;
}
vec3 rimLight(vec3 c, vec2 n, float depth) {
    vec2 L=vec2(cos(uLightAngle),sin(uLightAngle));
    float f=dot(n,L),w=max(uRimLightWidth,.5);
    float band=1.0-smoothstep(0.0,w,depth),glow=1.0-smoothstep(0.0,w*4.5,depth);
    c+=vec3(1.0,.985,.97)*uRimLight*(band*.9*max(f,0.0)+glow*.14*max(f,0.0));
    c*=1.0-uRimLight*.5*band*max(-f,0.0);
    return c;
}
vec4 mixedDock(vec2 p) {
    vec3 whole,liquid;float strength;dockFields(p,whole,liquid,strength);
    vec3 menu=menuField(p);
    float aa=max(fwidth(whole.z),.5);
    float body=1.0-smoothstep(-aa*.5,aa*.5,whole.z);
    float pane=1.0-smoothstep(-aa*.5,aa*.5,menu.z);
    float mask=max(body,pane);
    vec3 shadowField,unused;float unusedStrength;dockFields(p-vec2(0,uShadowOffset),shadowField,unused,unusedStrength);
    float sd=max(min(shadowField.z,menuField(p-vec2(0,uShadowOffset)).z),0.0),sigma=max(.5,uShadowSoftness*.5);
    float shadow=uShadow*exp(-.5*sd*sd/(sigma*sigma));
    if(mask<.001)return vec4(0,0,0,shadow*uOpacity);
    float depth=max(-whole.z,0.0),popup=popupMask(p);
    // The plate (and the bubble on it) lies over the menu: the menu comes out from under it.
    vec3 c=iconLayer(mix(menuPane(p,menu),dockRegular(p,fieldNormal(whole),depth,popup),body),p);
    float inside=strength*(1.0-smoothstep(-1.2,1.2,liquid.z)),amount=inside;
    if(amount>.001) {
        float ndRadius,ndLife;vec4 nd=nearestDrop(p,ndRadius,ndLife);
        // The islands' drops (uDropDome) melt as the dock's one bubble does through uBulge.z:
        // each one's lens and look follow its own life while it sinks. At full power to the end
        // a melting drop was a shard of split colour over the icon (27.09).
        float own=uDropDome>.5?ndLife:1.0;
        amount*=own;
        float liquidDepth=max(-liquid.z,0.0),edge=min(min(uEdge,23.5),max(min(nd.z,nd.w)*.34,4.0));
        float live=1.0-smoothstep(uLiveTop-6.0,uLiveTop+2.0,p.y);
        float slope=dockSlope(liquidDepth,edge,uThickness);
        vec2 normal=fieldNormal(liquid);
        float life=strength*own*(1.0-uDematerialize);
        float bend=slope/sqrt(1.0+slope*slope)*life;
        // A drop standing on the plate is fused with it below (meniscus): no lens there.
        if(nd.y<uRect.y&&nd.y+nd.w>uRect.y) bend*=1.0-clamp(normal.y*1.6,0.0,1.0);
        // The hover drop in the menu bends nothing (2026-09-25: "no refraction at all
        // for the menu's bubble"): same glass, no lens. Only drops that reach the plate bend.
        if(nd.y+nd.w<uRect.y) bend=0.0;
        vec2 bent=p-normal*uRefraction*bend;
        float dispersion=uDispersion*.5*bend;
        vec3 centre=dockBackdrop(bent,live);
        vec3 sharp=vec3(dockBackdrop(bent-normal*dispersion,live).r,
                         centre.g,dockBackdrop(bent+normal*dispersion,live).b);
        // On the plate or the menu the drop's middle keeps what is drawn there (it is
        // already frosted); only over the bare backdrop does it use the Clear blur.
        float plateMask=1.0-smoothstep(-1.0,1.0,min(plateDistance(bent),menuField(bent).z));
        vec3 behind=mix(texture(uWallBlurred,wallpaperUV(bent)).rgb,texture(uBlurred,wallpaperUV(bent)).rgb,live);
        vec3 soft=mix(behind,centre,plateMask);
        vec3 clear=mix(sharp,soft,smoothstep(0.0,edge,liquidDepth)*uBlur);
        if(uDropDome<.5){clear=brightnessAdaptation(clear);clear=materialTint(clear);clear=saturation(clear);}
        clear=mix(clear,uSelectedColor.rgb,selectedShare(p));
        clear=innerShadow(clear,normal,liquidDepth);
        clear=directionalSpecular(clear,surfaceNormal(normal,slope),slope);
        clear=fresnelRim(clear,surfaceNormal(normal,slope),normal,liquidDepth);
        if(uRimLight>0.0) clear=rimLight(clear,normal,liquidDepth);
        c=mix(c,clear,amount);
    }
    if(uDropDome>.5&&dropCount()>0) {
        // The drop's shadow sinks and fades with it (dockFields): a full-size grey disc stayed
        // on the plate after the drop itself had gone.
        float r,life;vec4 d=nearestDrop(p,r,life);
        float sd=max(roundedRect(p-vec2(0,3.0),d,r)+(uBulge.y<.5?(1.0-life)*26.0:0.0),0.0);
        c*=1.0-.18*life*exp(-sd/6.0)*(1.0-inside)*(1.0-popup);
    }
    float alpha=mask+shadow*(1.0-mask);
    return vec4(clamp(c,0.0,1.0)*mask,alpha)*uOpacity;
}

vec4 glass(vec2 p) {
    if (uIsDock > 1.5) return mixedDock(p);
    float d = shape(p);
    float aa = max(fwidth(d),0.5);
    float mask = 1.0-smoothstep(-aa*0.5,aa*0.5,d);
    if (uFlat > 0.5) {
        float alpha = mask*uFlatColor.a*uOpacity;
        return vec4(uFlatColor.rgb*alpha,alpha);
    }
    float shadow = outerShadow(p);
    if (mask < 0.001) return vec4(0,0,0,shadow*uOpacity);
    float depth = max(-d,0.0);
    vec2 profile = thicknessProfile(depth);
    vec2 n2 = contourNormal(p);
    if (uMaterial > 0.5) {
        vec3 c = regularMaterial(p,n2,profile.y,depth);
        float alpha = mask+shadow*(1.0-mask);
        return vec4(clamp(c,0.0,1.0)*mask,alpha)*uOpacity;
    }
    vec2 gradient = n2 * profile.y * (1.0+bulge(p)*0.55);
    for (int i=0; i<8; i++) {
        if (i>=dropCount()) break;
        vec4 r=dropRect(i); vec4 dp=dropParams(i);
        float dDrop=roundedRect(p,r,dp.x);
        if (dDrop < 0.0) {
            float edge=min(uEdge*0.43,6.0);
            float t=clamp(-dDrop/max(edge,0.1),0.0,1.0);
            float x=1.0-t;
            float slope=min(8.0,uThickness*(1.0+dp.w*0.5)/max(edge,0.1)*x/sqrt(max(1.0-x*x,0.0025)));
            vec2 g=vec2(roundedRect(p+vec2(.35,0),r,dp.x)-roundedRect(p-vec2(.35,0),r,dp.x),
                        roundedRect(p+vec2(0,.35),r,dp.x)-roundedRect(p-vec2(0,.35),r,dp.x));
            gradient += g/max(length(g),.0001)*slope*dp.z*smoothstep(0.0,2.5,-dDrop);
        }
    }
    float slope=length(gradient);
    vec2 opticalNormal=slope>.00001?gradient/slope:n2;
    vec3 n3 = surfaceNormal(opticalNormal,slope);
    vec2 bent = refractedPosition(p,opticalNormal,slope);
    vec3 c = chromaticRefraction(bent,opticalNormal,slope);
    c = centreBlur(c,bent,depth);
    c = brightnessAdaptation(c);
    c = materialTint(c);
    c = saturation(c);
    c = innerShadow(c,n2,depth);
    c = directionalSpecular(c,n3,slope);
    c = fresnelRim(c,n3,n2,depth);
    c += uActivity*0.035;
    float alpha = mask+shadow*(1.0-mask);
    return vec4(clamp(c,0.0,1.0)*mask,alpha)*uOpacity;
}

void main() {
    vec2 p = uOrigin + qt_TexCoord0 * uItemSize;
    uAverage = averageOf(uRect);
    uLightMode = paletteOf(uAverage, uRegularBase);
    uAverageB = uHasB > 0.5 ? averageOf(uRectB) : uAverage;
    uLightModeB = paletteOf(uAverageB, uRegularBaseB);
    fragColor = glass(p) * qt_Opacity;
}
