// Copyright (C) 2026 Artur Yakymenko
// SPDX-License-Identifier: GPL-3.0-or-later
#include <QtQml/QQmlExtensionPlugin>
#include <QtQuick/QQuickItem>
#include <QtQuick/QQuickWindow>
#include <QtCore/QCoreApplication>
#include <QtCore/QMimeData>
#include <QtGui/QDragEnterEvent>
#include <QtGui/QDragMoveEvent>
#include <QtGui/QDragLeaveEvent>
#include <QtGui/QDropEvent>
class DockDropProbe : public QObject {
    Q_OBJECT
    QMimeData mime;
public:
    Q_INVOKABLE void enter(QQuickItem *item, int x, int y, const QStringList &urls) {
        QList<QUrl> values;
        for (const auto &url : urls) values.append(QUrl(url));
        mime.clear();
        if (values.isEmpty()) mime.setText("plain text");
        else mime.setUrls(values);
        QDragEnterEvent event(QPoint(x,y), Qt::CopyAction, &mime, Qt::LeftButton, Qt::NoModifier);
        QCoreApplication::sendEvent(item->window(), &event);
    }
    Q_INVOKABLE void move(QQuickItem *item, int x, int y) {
        QDragMoveEvent event(QPoint(x,y), Qt::CopyAction, &mime, Qt::LeftButton, Qt::NoModifier);
        QCoreApplication::sendEvent(item->window(), &event);
    }
    Q_INVOKABLE bool drop(QQuickItem *item, int x, int y) {
        QDropEvent event(QPointF(x,y), Qt::CopyAction, &mime, Qt::LeftButton, Qt::NoModifier);
        QCoreApplication::sendEvent(item->window(), &event);
        return event.isAccepted();
    }
    Q_INVOKABLE void leave(QQuickItem *item) {
        QDragLeaveEvent event;
        QCoreApplication::sendEvent(item->window(), &event);
    }
};
class DockDropPlugin : public QQmlExtensionPlugin {
    Q_OBJECT
    Q_PLUGIN_METADATA(IID QQmlExtensionInterface_iid)
public:
    void registerTypes(const char *uri) override { qmlRegisterType<DockDropProbe>(uri,1,0,"DockDropProbe"); }
};
#include "dock-drop-probe.moc"
