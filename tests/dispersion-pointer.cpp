// Copyright (C) 2026 Artur Yakymenko
// SPDX-License-Identifier: GPL-3.0-or-later
#include <QCoreApplication>
#include <QEvent>
#include <QMouseEvent>
#include <QQmlExtensionPlugin>
#include <QQuickItem>
#include <QQuickWindow>
#include <QtQml>

// Native window events reach the same HoverHandler and MouseArea as a desktop pointer.
// The leave event reproduces the compositor transferring focus to the modal surface.
class DispersionPointer : public QObject {
    Q_OBJECT
public:
    using QObject::QObject;
    Q_INVOKABLE void move(QQuickItem *item) {
        send(item, QEvent::MouseMove, Qt::NoButton, Qt::NoButton);
    }
    Q_INVOKABLE void click(QQuickItem *item) {
        send(item, QEvent::MouseButtonPress, Qt::LeftButton, Qt::LeftButton);
        send(item, QEvent::MouseButtonRelease, Qt::LeftButton, Qt::NoButton);
    }
    Q_INVOKABLE void leave(QQuickItem *item) {
        QEvent event(QEvent::Leave);
        QCoreApplication::sendEvent(item->window(), &event);
    }
private:
    void send(QQuickItem *item, QEvent::Type type, Qt::MouseButton button, Qt::MouseButtons buttons) {
        const auto position = item->mapToScene(QPointF(item->width() / 2, item->height() / 2));
        QMouseEvent event(type, position, position, position, button, buttons, Qt::NoModifier);
        QCoreApplication::sendEvent(item->window(), &event);
    }
};
class DispersionPointerPlugin : public QQmlExtensionPlugin {
    Q_OBJECT
    Q_PLUGIN_METADATA(IID "org.qt-project.Qt.QQmlExtensionInterface/1.0")
public:
    void registerTypes(const char *uri) override {
        qmlRegisterType<DispersionPointer>(uri, 1, 0, "DispersionPointer");
    }
};
#include "dispersion-pointer.moc"
