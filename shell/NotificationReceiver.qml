pragma ComponentBehavior: Bound
import QtQuick
import Quickshell.Services.Notifications

NotificationServer {
    required property NotificationStore store
    keepOnReload: false
    actionsSupported: true
    bodySupported: true
    persistenceSupported: true
    bodyMarkupSupported: false
    bodyHyperlinksSupported: false
    bodyImagesSupported: false
    imageSupported: false
    inlineReplySupported: false
    onNotification: notification => store.accept(notification)
}
