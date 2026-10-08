// Copyright (C) 2026 Artur Yakymenko
// SPDX-License-Identifier: GPL-3.0-or-later
#include <QtQml/QQmlExtensionPlugin>
#include <QtQuick/QQuickItem>
#include <QtQuick/QQuickWindow>
#include <QtCore/QCoreApplication>
#include <QtGui/QMouseEvent>

class InputMaskProbe : public QObject {
    Q_OBJECT
public:
    Q_INVOKABLE bool contains(QQuickItem *item, int x, int y) const {
        return item && item->window() && item->window()->mask().contains(QPoint(x, y));
    }
    Q_INVOKABLE void move(QQuickItem *item, qreal x, qreal y) const {
        if (!item || !item->window())
            return;
        const QPointF local(x, y);
        const QPointF global = item->window()->mapToGlobal(local);
        QMouseEvent event(QEvent::MouseMove, local, global, Qt::NoButton,
                         Qt::NoButton, Qt::NoModifier);
        QCoreApplication::sendEvent(item->window(), &event);
    }
};

class InputMaskPlugin : public QQmlExtensionPlugin {
    Q_OBJECT
    Q_PLUGIN_METADATA(IID QQmlExtensionInterface_iid)
public:
    void registerTypes(const char *uri) override {
        qmlRegisterType<InputMaskProbe>(uri, 1, 0, "InputMaskProbe");
    }
};

#include "input-mask-probe.moc"
