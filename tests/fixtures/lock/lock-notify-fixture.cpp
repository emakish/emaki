// Test-only QObject semantics of Quickshell 0.3.1 WlSessionLock. Never installed.
// A QML-declared property cannot reproduce a native setter which omits NOTIFY.
#include <QObject>
#include <QQmlExtensionPlugin>
#include <qqml.h>

class SessionLock : public QObject {
    Q_OBJECT
    Q_PROPERTY(bool locked READ locked WRITE setLocked NOTIFY lockStateChanged)
    Q_PROPERTY(bool secure READ secure NOTIFY secureStateChanged)
public:
    bool locked() const { return m_locked; }
    bool secure() const { return m_secure; }
    void setLocked(bool value) {
        if (m_locked == value) return;
        m_locked = value;
        // With a manager present, setLocked(true) realizes the target without
        // lockStateChanged. The later compositor callback notifies secure only.
        if (!value) {
            if (m_secure) {
                m_secure = false;
                emit secureStateChanged();
            }
            emit lockStateChanged();
        }
    }
    Q_INVOKABLE void confirm() {
        if (m_locked && !m_secure) {
            m_secure = true;
            emit secureStateChanged();
        }
    }
signals:
    void lockStateChanged();
    void secureStateChanged();
private:
    bool m_locked = false;
    bool m_secure = false;
};

class LockNotifyPlugin : public QQmlExtensionPlugin {
    Q_OBJECT
    Q_PLUGIN_METADATA(IID QQmlExtensionInterface_iid)
public:
    void registerTypes(const char *uri) override {
        qmlRegisterType<SessionLock>(uri, 1, 0, "SessionLock");
    }
};

#include "lock-notify-fixture.moc"
