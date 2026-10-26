// Copyright (C) 2026 Artur Yakymenko
// SPDX-License-Identifier: GPL-3.0-or-later
// Real Qt scene synchronization with the production capture/view methods. Protocol
// completion is queued by a private peer, so no display or compositor is contacted.
#include <QtQuick/QQuickItem>
#include <QtQuick/QQuickWindow>
#include <QtQuick/QSGSimpleRectNode>
#include <QtQuick/QSGTexture>
#include <QtQml/QQmlExtensionPlugin>
#include <QtCore/QProperty>
#include <QtCore/QTimer>
#include <QtGui/QRegion>
#include <functional>
#include <cmath>

struct ScreencopyDamage { QRegion region; bool known = false; };
struct Frontbuffer {
    struct Transform { bool flipSize() const { return false; } } transform;
    QSize size() const { return {400, 300}; }
};
struct Swapchain {
    Frontbuffer buffer;
    Frontbuffer* frontbuffer() { return &buffer; }
};
struct Context {
    Swapchain buffers;
    std::function<void()> request;
    void captureFrame() { request(); }
    Swapchain& swapchain() { return buffers; }
    ScreencopyDamage frameDamage() const { return {QRegion(0, 200, 20, 20), true}; }
};
namespace buffer {
class WlBufferQSGDisplayNode : public QSGSimpleRectNode {
public:
    explicit WlBufferQSGDisplayNode(QQuickWindow*) { setColor(QColor("#dd6337")); }
    void syncSwapchain(Swapchain&) {}
    void setFiltering(QSGTexture::Filtering) {}
};
}
class ScreencopyView : public QQuickItem {
    Q_OBJECT
    Q_PROPERTY(int frames READ frames NOTIFY frameCaptured)
public:
    explicit ScreencopyView(QQuickItem* parent = nullptr) : QQuickItem(parent) {
        context = &peer;
        peer.request = [this] {
            if (pending) return;
            pending = true;
            QTimer::singleShot(1, this, [this] {
                pending = false;
                ++completedFrames;
                onFrameCaptured();
            });
        };
    }
    void componentComplete() override {
        QQuickItem::componentComplete();
        context->captureFrame();
    }
    int frames() const { return completedFrames; }
    Q_INVOKABLE bool damageIntersects(const QRectF& sourceRect) const;
signals:
    void frameCaptured();
protected:
    QSGNode* updatePaintNode(QSGNode*, UpdatePaintNodeData*) override;
private:
    void onFrameCaptured();
    Context peer;
    Context* context;
    QProperty<bool> bHasContent;
    QProperty<QSize> bSourceSize;
    ScreencopyDamage completedDamage;
    bool mLive = true;
    bool pending = false;
    int completedFrames = 0;
};
#include "cadence-methods.inc"
class CadencePlugin : public QQmlExtensionPlugin {
    Q_OBJECT
    Q_PLUGIN_METADATA(IID "org.qt-project.Qt.QQmlExtensionInterface/1.0")
public:
    void registerTypes(const char* uri) override { qmlRegisterType<ScreencopyView>(uri, 1, 0, "CaptureView"); }
};
#include "damage-cadence.moc"
