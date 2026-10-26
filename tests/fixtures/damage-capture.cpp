// Copyright (C) 2026 Artur Yakymenko
// SPDX-License-Identifier: GPL-3.0-or-later
#include <QDebug>
#include <QRectF>
#include <QRegion>
#include <QSize>
#include <cmath>
#include <cstdint>
#include <functional>
#include <iostream>
#include <limits>
#include <stdexcept>
#include <string>

// Only the peers are substitutes. Method bodies below come from the selected tree.
#include "damage-type.inc"
struct Transform {
    int value = 0;
    Transform& operator=(int v) { value = v; return *this; }
    bool flipSize() const { return value % 2; }
};
struct Buffer {
    Transform transform;
    QSize size() const { return {200, 160}; }
    void* buffer() { return this; }
};
struct Request { int resets = 0; void reset() { ++resets; } };
struct Swapchain {
    Buffer a, b;
    Buffer* front = &a;
    Buffer* back = &b;
    Buffer* createBackbuffer(const Request&) { return back; }
    Buffer* frontbuffer() { return front; }
    Buffer* backbuffer() { return back; }
    void swapBuffers() { std::swap(front, back); }
};
namespace QtWaylandClient {
struct QWaylandScreen { void* output() { return this; } };
}
using Screen = QtWaylandClient::QWaylandScreen;
struct WlrScreencopyManager {
    #include "manager-state.inc"
    void captureStarted();
    void captureStopped();
    bool damageExclusive(quint64&) const;
    int full = 0, regional = 0;
    void* capture_output(int, void*) { ++full; return this; }
    void* capture_output_region(int, void*, int, int, int, int) { ++regional; return this; }
};
struct QSGNode { virtual ~QSGNode() = default; };
struct QSGTexture { enum { Linear }; };
namespace buffer {
struct WlBufferTransform { enum { Normal0 = 0, Flipped180 = 6 }; };
struct WlBufferQSGDisplayNode: QSGNode {
    int syncs = 0;
    explicit WlBufferQSGDisplayNode(void*) {}
    void syncSwapchain(Swapchain&) { ++syncs; }
    void setRect(QRectF) {}
    void setFiltering(int) {}
};
}
#define qCWarning(category) qWarning()
constexpr int ZWLR_SCREENCOPY_FRAME_V1_FLAGS_Y_INVERT = 1;
struct WlrScreencopyContext {
    WlrScreencopyManager managerStorage;
    WlrScreencopyManager* manager = &managerStorage;
    Screen screenStorage;
    Screen* screen = &screenStorage;
    quint64 damageEpoch = 0;
    explicit WlrScreencopyContext(WlrScreencopyManager* shared = nullptr, Screen* output = nullptr) {
        if (shared) manager = shared;
        if (output) screen = output;
        manager->captureStarted();
    }
    ~WlrScreencopyContext() { if (object()) destroy(); manager->captureStopped(); }
    Request request;
    Swapchain mSwapchain;
    bool copiedFirstFrame = false, requestedDamage = false, sawDamage = false;
    bool invalidDamage = false, yInvert = false, paintCursors = false;
    QRect region;
    QRegion pendingDamage;
    ScreencopyDamage completedDamage;
    struct { int transform = 0; } transform;
    void* active = nullptr;
    int plainCopies = 0, damageCopies = 0, emissions = 0;
    std::function<void()> callback;
    void* object() { return active; }
    void init(void* value) { active = value; }
    void destroy() { active = nullptr; }
    void copy(void*) { ++plainCopies; }
    void copy_with_damage(void*) { ++damageCopies; }
    Swapchain& swapchain() { return mSwapchain; }
    #include "damage-accessor.inc"
    void frameCaptured() { ++emissions; if (callback) callback(); }
    void captureFrame();
    void zwlr_screencopy_frame_v1_damage(uint32_t, uint32_t, uint32_t, uint32_t);
    void zwlr_screencopy_frame_v1_buffer_done();
    void zwlr_screencopy_frame_v1_flags(uint32_t);
    void zwlr_screencopy_frame_v1_ready(uint32_t, uint32_t, uint32_t);
    void submitFrame();
};
template<typename T> struct Property {
    T data{};
    std::function<void()> changed;
    Property& operator=(T v) { data = v; if (changed) changed(); return *this; }
    T value() const { return data; }
    operator T() const { return data; }
};
struct QQuickItem { enum { ItemHasContents }; };
struct UpdatePaintNodeData {};
struct ScreencopyView {
    WlrScreencopyContext* context = nullptr;
    ScreencopyDamage completedDamage;
    Property<bool> bHasContent;
    Property<QSize> bSourceSize;
    bool mLive = true;
    double w = 200, h = 160;
    int updates = 0, emissions = 0;
    void setFlag(int, bool = true) {}
    void update() { ++updates; }
    double width() const { return w; }
    double height() const { return h; }
    QRectF boundingRect() const { return {0, 0, w, h}; }
    void* window() { return nullptr; }
    void frameCaptured() { ++emissions; }
    void onFrameCaptured();
    bool damageIntersects(const QRectF&) const;
    void destroyContext(bool);
    QSGNode* updatePaintNode(QSGNode*, UpdatePaintNodeData*);
};
#include "extracted.inc"

void require(bool value, const char* description) {
    if (!value) throw std::runtime_error(description);
}
void next(WlrScreencopyContext& c) {
    c.captureFrame();
    c.zwlr_screencopy_frame_v1_buffer_done();
}
void captureChecks() {
    WlrScreencopyContext c;
    c.pendingDamage = QRect(1, 2, 3, 4);
    c.sawDamage = c.invalidDamage = c.requestedDamage = true;
    c.captureFrame();
    require(c.pendingDamage.isEmpty() && !c.sawDamage && !c.invalidDamage && !c.requestedDamage,
            "new request clears pending metadata");
    c.captureFrame();
    require(c.manager->full == 1 && c.request.resets == 1, "one outstanding request only");
    c.zwlr_screencopy_frame_v1_buffer_done();
    require(c.plainCopies == 1 && c.damageCopies == 0, "first copy is plain");
    c.zwlr_screencopy_frame_v1_damage(1, 2, 3, 4);
    c.zwlr_screencopy_frame_v1_ready(0, 0, 0);
    require(!c.frameDamage().known, "first frame always conservative");
    next(c);
    require(c.damageCopies == 1, "later copy waits for damage");
    c.zwlr_screencopy_frame_v1_damage(10, 20, 5, 6);
    c.zwlr_screencopy_frame_v1_damage(195, 155, 20, 20);
    const QRegion expected = QRegion(QRect(10, 20, 5, 6)) + QRect(195, 155, 5, 5);
    ScreencopyDamage snapshot;
    c.callback = [&] {
        snapshot = c.frameDamage();
        require(snapshot.known && snapshot.region == expected, "snapshot precedes signal");
        c.captureFrame();
        require(c.pendingDamage.isEmpty(), "signal consumer starts next request");
        require(c.frameDamage().region == expected, "pending reset preserves completed snapshot");
    };
    c.submitFrame();
    c.callback = {};
    require(snapshot.region == expected, "retained snapshot remains immutable");
    c.zwlr_screencopy_frame_v1_buffer_done();
    c.submitFrame();
    require(!c.frameDamage().known, "missing metadata is unknown");
    next(c);
    c.zwlr_screencopy_frame_v1_damage(UINT32_MAX, 0, 1, 1);
    c.submitFrame();
    require(!c.frameDamage().known, "overflow metadata is unknown");
    for (int transform = 0; transform < 8; ++transform) {
        next(c);
        c.transform.transform = transform;
        c.zwlr_screencopy_frame_v1_damage(1, 2, 3, 4);
        c.submitFrame();
        require(c.frameDamage().known == (transform == 0), "transforms stay conservative");
    }
    c.transform.transform = 0;
    next(c);
    c.zwlr_screencopy_frame_v1_flags(1);
    c.zwlr_screencopy_frame_v1_damage(1, 2, 3, 4);
    c.submitFrame();
    require(!c.frameDamage().known, "inverted buffers stay conservative");
    c.zwlr_screencopy_frame_v1_flags(0);
    int beforeEmissions = c.emissions;
    for (int n = 0; n < 2; ++n) {
        next(c);
        c.zwlr_screencopy_frame_v1_damage(7, 8, 2, 3);
        c.submitFrame();
        require(c.frameDamage().known, "consecutive equal damage is known");
    }
    require(c.emissions == beforeEmissions + 2, "identical regions still notify twice");
    next(c);
    c.zwlr_screencopy_frame_v1_damage(10, 20, 0, 0);
    c.submitFrame();
    require(c.frameDamage().known && c.frameDamage().region.isEmpty(),
            "explicit empty damage is distinct from missing metadata");
    next(c);
    c.zwlr_screencopy_frame_v1_damage(2147483640U, 0, 20, 1);
    c.submitFrame();
    require(!c.frameDamage().known, "rectangle endpoint overflow is unknown");
    c.region = QRect(2, 3, 40, 50);
    c.captureFrame();
    require(c.manager->regional == 1, "regional request retained");
}
void viewChecks() {
    ScreencopyView v;
    v.context = new WlrScreencopyContext;
    const QRegion region(QRect(20, 30, 2, 3));
    v.context->completedDamage = {region, true};
    int propertySignals = 0;
    auto checkNotification = [&] {
        ++propertySignals;
        require(v.completedDamage.known && v.completedDamage.region == region,
                "damage snapshot precedes property notifications");
    };
    v.bSourceSize.changed = checkNotification;
    v.bHasContent.changed = checkNotification;
    v.onFrameCaptured();
    require(propertySignals == 2 && v.emissions == 1, "view notifies frame and properties");
    v.onFrameCaptured();
    require(v.emissions == 2, "view repeats identical damage notification");
    v.bSourceSize.changed = v.bHasContent.changed = {};
    QSGNode* node = nullptr;
    for (int i = 0; i < 3; ++i) {
        v.context->destroy();
        node = v.updatePaintNode(node, nullptr);
    }
    require(v.context->manager->full == 3, "each live sync requests another capture");
    v.mLive = false;
    v.context->destroy();
    node = v.updatePaintNode(node, nullptr);
    require(v.context->manager->full == 3, "paused view does not request capture");
    delete node;
    for (double scale: {1.0, 1.25, 1.3}) {
        v.w = 200 / scale;
        v.h = 160 / scale;
        QRectF crop(20 / scale, 30 / scale, 10 / scale, 10 / scale);
        v.completedDamage = {QRegion(QRect(19, 35, 1, 1)), true};
        require(v.damageIntersects(crop), "one physical texel outside crop intersects");
        v.completedDamage.region = QRect(17, 35, 1, 1);
        require(!v.damageIntersects(crop), "distant damage misses crop");
        require(v.damageIntersects({}), "empty crop means whole output");
        v.completedDamage.region = QRect(29, 39, 1, 1);
        require(v.damageIntersects(crop), "crop interior intersects at fractional scale");
        v.completedDamage.region = QRect(150, 140, 1, 1);
        require(!v.damageIntersects(crop), "far corner misses crop");
    }
    require(v.damageIntersects(QRectF(1000, 1000, 10, 10)), "outside output conservative");
    v.completedDamage.region = QRegion();
    require(!v.damageIntersects({}), "known empty damage misses whole output");
    require(v.damageIntersects(QRectF(std::numeric_limits<double>::infinity(), 0, 10, 10)),
            "nonfinite input conservative");
    v.completedDamage.known = false;
    require(v.damageIntersects(QRectF(0, 0, 1, 1)), "unknown damage conservative");
    v.completedDamage.known = true;
    v.w = 0;
    require(v.damageIntersects(QRectF(0, 0, 1, 1)), "zero geometry conservative");
    v.destroyContext(true);
    require(!v.context && !v.completedDamage.known && v.completedDamage.region.isEmpty()
            && !v.bHasContent && v.bSourceSize.value().isEmpty(), "destroy clears all frame state");
    require(v.damageIntersects(QRectF(0, 0, 1, 1)), "absent content conservative");
}
void concurrencyChecks() {
    WlrScreencopyManager manager;
    Screen output, otherOutput;
    WlrScreencopyContext a(&manager, &output);
    auto finish = [](WlrScreencopyContext& c) {
        c.zwlr_screencopy_frame_v1_damage(150, 120, 5, 5);
        c.submitFrame();
    };
    next(a);
    finish(a);
    next(a);
    finish(a);
    require(a.frameDamage().known, "exclusive context learns a stable manager interval");
    {
        WlrScreencopyContext b(&manager, &output);
        next(a);
        finish(a);
        require(!a.frameDamage().known, "simultaneous same-output context disables damage gating");
        next(b);
        finish(b);
        next(b);
        finish(b);
        require(!b.frameDamage().known, "second same-output context remains conservative");
    }
    next(a);
    finish(a);
    require(!a.frameDamage().known, "departure invalidates first exclusive frame");
    next(a);
    finish(a);
    require(a.frameDamage().known, "next stable exclusive frame restores known damage");
    next(a);
    {
        WlrScreencopyContext regional(&manager, &output);
        regional.region = QRect(0, 0, 20, 20);
        next(regional);
        finish(regional);
        require(manager.regional == 1, "regional peer uses original capture request");
    }
    finish(a);
    require(!a.frameDamage().known, "peer join-copy-leave before ready invalidates snapshot");
    next(a);
    finish(a);
    require(a.frameDamage().known, "exclusive interval recovers after transient regional peer");
    next(a);
    {
        WlrScreencopyContext canceled(&manager, &output);
        next(canceled);
        require(canceled.object() != nullptr && canceled.plainCopies == 1
                && canceled.emissions == 0, "peer has pending copy without ready before cancellation");
    }
    finish(a);
    require(!a.frameDamage().known, "peer canceled before ready invalidates pending frame");
    next(a);
    finish(a);
    require(a.frameDamage().known, "next exclusive frame recovers after canceled peer");
    {
        WlrScreencopyContext other(&manager, &otherOutput);
        next(other);
        finish(other);
        next(a);
        finish(a);
        require(!a.frameDamage().known, "another output sharing manager also disables damage");
    }
    next(a);
    finish(a);
    require(!a.frameDamage().known, "another output departure invalidates first exclusive frame");
    next(a);
    finish(a);
    require(a.frameDamage().known, "next exclusive frame recovers after other-output departure");
    std::cout << "PASS manager membership: concurrent/transient/regional/other-output peers and recovery\n";
}
// Also runs on old source with only missing APIs represented as conservative peers.
// Existing capture/swap/view methods are identical to the selected production source.
void outsideCropRegression() {
    ScreencopyView v;
    v.context = new WlrScreencopyContext;
    auto& c = *v.context;
    c.callback = [&] { v.onFrameCaptured(); };
    next(c);
    c.submitFrame();
    require(c.plainCopies == 1 && c.damageCopies == 0 && v.bHasContent,
            "baseline first-frame delivery preserved");
    c.captureFrame();
    c.captureFrame();
    require(c.manager->full == 2, "baseline duplicate-request guard preserved");
    c.zwlr_screencopy_frame_v1_buffer_done();
    c.zwlr_screencopy_frame_v1_damage(150, 120, 5, 5);
    c.submitFrame();
    require(c.damageCopies == 1 && c.emissions == 2,
            "baseline damage-copy protocol and frame delivery preserved");
    std::cout << "PASS preserved plain-first/damage-next delivery and request guard" << std::endl;
    const bool outsideRefresh = v.damageIntersects(QRectF(0, 0, 40, 40));
    std::cout << "outside-crop refreshes=" << (outsideRefresh ? 1 : 0)
              << " expected=0" << std::endl;
    v.destroyContext(false);
    require(!outsideRefresh, "damage outside the sampled band must skip refresh");
}
int main(int argc, char**) {
    try {
        outsideCropRegression();
        if (argc > 1) return 0;
        captureChecks();
        viewChecks();
        concurrencyChecks();
        std::cout << "PASS damage capture: production callbacks, snapshots, crop mapping and sync cadence\n";
    } catch (const std::exception& error) {
        std::cerr << "FAIL: " << error.what() << '\n';
        return 1;
    }
}
