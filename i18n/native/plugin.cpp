// Copyright (C) 2026 Artur Yakymenko
// SPDX-License-Identifier: GPL-3.0-or-later
#include <QCoreApplication>
#include <QDir>
#include <QLocale>
#include <QQmlEngine>
#include <QQmlExtensionPlugin>
#include <QTranslator>
#include <QPlatformSurfaceEvent>
#include <QPointer>
#include <QWindow>
#include <QtWaylandClient/private/qwaylandwindow_p.h>
#include <QtWaylandClient/private/qwaylandshellsurface_p.h>
#include <memory>
#include <vector>

// Each binding belongs to its window, so role recreation and engine teardown do
// not leave a callback referring to another window or to a former QML engine.
class WindowIdentityBinding final : public QObject {
    Q_OBJECT
public:
    explicit WindowIdentityBinding(QWindow *window) : QObject(window), window(window) {
        window->installEventFilter(this);
    }
    void identify(const QString &value) {
        appId = value;
        attach();
    }
    QString identity() const { return appId; }
protected:
    bool eventFilter(QObject *, QEvent *event) override {
        if (event->type() == QEvent::PlatformSurface) {
            const auto *surface = static_cast<QPlatformSurfaceEvent *>(event);
            if (surface->surfaceEventType() == QPlatformSurfaceEvent::SurfaceCreated) {
                attach();
            } else {
                QObject::disconnect(roleConnection);
                platform = nullptr;
            }
        }
        return false;
    }
private:
    void apply() {
        if (platform && platform->shellSurface()) platform->shellSurface()->setAppId(appId);
    }
    void attach() {
        auto *current = dynamic_cast<QtWaylandClient::QWaylandWindow *>(window->handle());
        if (platform != current) {
            QObject::disconnect(roleConnection);
            platform = current;
            if (platform) {
                // Qt sets the process identity before this signal and maps the
                // role afterwards. Restore the window's stable identity here.
                roleConnection = connect(platform, &QtWaylandClient::QWaylandWindow::surfaceRoleCreated,
                                         this, [this] { apply(); }, Qt::DirectConnection);
            }
        }
        apply();
    }
    QWindow *window;
    QPointer<QtWaylandClient::QWaylandWindow> platform;
    QMetaObject::Connection roleConnection;
    QString appId;
};

class WindowIdentity final : public QObject {
    Q_OBJECT
public:
    Q_INVOKABLE bool identify(QObject *object, const QString &appId) {
        auto *window = qobject_cast<QWindow *>(object);
        if (!window || appId.isEmpty()) return false;
        for (const auto character : appId) {
            if (!(character >= 'a' && character <= 'z') && !(character >= 'A' && character <= 'Z') &&
                !(character >= '0' && character <= '9') && character != '.' && character != '-' && character != '_')
                return false;
        }
        auto *binding = window->findChild<WindowIdentityBinding *>(QString(), Qt::FindDirectChildrenOnly);
        if (!binding) binding = new WindowIdentityBinding(window);
        binding->identify(appId);
        return true;
    }
    Q_INVOKABLE QString appId(QObject *object) const {
        auto *window = qobject_cast<QWindow *>(object);
        if (!window) return {};
        const auto *binding = window->findChild<WindowIdentityBinding *>(QString(), Qt::FindDirectChildrenOnly);
        return binding ? binding->identity() : QString();
    }
};

// Only the separate installer process opts into changing its translators. The
// engine stays alive, retaining entered values and the outstanding connection.
class InstallerLanguage final : public QObject {
    Q_OBJECT
public:
    InstallerLanguage(QQmlEngine *engine, std::vector<std::unique_ptr<QTranslator>> *translators)
        : engine(engine), translators(translators) {}
    Q_INVOKABLE bool setLanguage(const QString &locale) {
        if (qEnvironmentVariable("EMAKI_INSTALLER_UI") != QStringLiteral("1") || !engine)
            return false;
        QString language;
        if (locale == QStringLiteral("en_US.UTF-8")) language = QStringLiteral("en");
        else if (locale == QStringLiteral("es_MX.UTF-8")) language = QStringLiteral("es");
        else if (locale == QStringLiteral("pt_BR.UTF-8")) language = QStringLiteral("pt_BR");
        else return false;
        const auto directory = qEnvironmentVariable("EMAKI_LOCALE_DIR", QStringLiteral(EMAKI_LOCALE_DIR));
        std::vector<std::unique_ptr<QTranslator>> replacement;
        for (const auto *domain : {"shell", "programs", "installer", "notes"}) {
            const auto base = QString::fromLatin1(domain) + '_';
            auto english = std::make_unique<QTranslator>();
            if (english->load(QDir(directory).filePath(base + "en.qm")))
                replacement.push_back(std::move(english));
            if (language != QStringLiteral("en")) {
                auto selected = std::make_unique<QTranslator>();
                if (selected->load(QDir(directory).filePath(base + language + ".qm")))
                    replacement.push_back(std::move(selected));
                else if (QString::fromLatin1(domain) == QStringLiteral("installer"))
                    return false;
            }
        }
        for (const auto &translator : *translators)
            QCoreApplication::removeTranslator(translator.get());
        *translators = std::move(replacement);
        for (const auto &translator : *translators)
            QCoreApplication::installTranslator(translator.get());
        engine->setUiLanguage(language);
        engine->retranslate();
        return true;
    }
private:
    QPointer<QQmlEngine> engine;
    std::vector<std::unique_ptr<QTranslator>> *translators;
};

// Quickshell creates QQmlEngine, not QQmlApplicationEngine. Loading on import
// installs translators before any component bindings evaluate their qsTr calls.
class TranslationPlugin final : public QQmlExtensionPlugin {
    Q_OBJECT
    Q_PLUGIN_METADATA(IID QQmlExtensionInterface_iid)
public:
    void registerTypes(const char *uri) override {
        qmlRegisterSingletonType<WindowIdentity>(uri, 1, 0, "WindowIdentity",
            [](QQmlEngine *, QJSEngine *) -> QObject * { return new WindowIdentity; });
        qmlRegisterSingletonType<InstallerLanguage>(uri, 1, 0, "InstallerLanguage",
            [this](QQmlEngine *engine, QJSEngine *) -> QObject * {
                return new InstallerLanguage(engine, &translators);
            });
        qmlRegisterModule(uri, 1, 0);
    }
    void initializeEngine(QQmlEngine *engine, const char *) override {
        const auto directory = qEnvironmentVariable("EMAKI_LOCALE_DIR", QStringLiteral(EMAKI_LOCALE_DIR));
        const auto explicitLanguage = qEnvironmentVariable("EMAKI_LANGUAGE");
        auto locale = qEnvironmentVariable("LC_ALL");
        if (locale.isEmpty()) locale = qEnvironmentVariable("LC_MESSAGES");
        if (locale.isEmpty()) locale = qEnvironmentVariable("LANG", "C");
        const auto normalize = [](QString name) {
            return name.section('.', 0, 0).section('@', 0, 0).replace('-', '_');
        };
        QStringList languages;
        if (!explicitLanguage.isEmpty()) {
            languages = explicitLanguage.split(':', Qt::SkipEmptyParts);
        } else if (normalize(locale) != "C" && normalize(locale) != "POSIX") {
            languages = qEnvironmentVariable("LANGUAGE").split(':', Qt::SkipEmptyParts);
            languages.append(locale);
        }
        QStringList preferences;
        for (const auto &name : languages) {
            const auto normalized = normalize(name);
            if (normalized == "C" || normalized == "POSIX") break;
            if (!normalized.isEmpty()) preferences.append(normalized);
        }
        const auto language = preferences.isEmpty() ? QStringLiteral("en") : preferences.first();
        const bool pseudo = language == QStringLiteral("en_XA");
        if (!explicitLanguage.isEmpty())
            QLocale::setDefault(QLocale(pseudo ? QStringLiteral("en_US") : language));
        // Qt has no built-in pseudo territory. Keep real numeric/date formatting.
        engine->setUiLanguage(pseudo ? QStringLiteral("en_US") : language);
        if (translators.empty()) {
            for (const auto *domain : {"shell", "programs", "installer", "notes"}) {
                const auto base = QString::fromLatin1(domain) + '_';
                auto english = std::make_unique<QTranslator>();
                if (english->load(QDir(directory).filePath(base + "en.qm"))) {
                    QCoreApplication::installTranslator(english.get());
                    translators.push_back(std::move(english));
                }
                // Later installed translators win; retain each lower preference as
                // a fallback for messages missing from a partial catalogue.
                for (auto preference = preferences.crbegin(); preference != preferences.crend(); ++preference) {
                    auto translator = std::make_unique<QTranslator>();
                    const bool loaded = *preference == QStringLiteral("en_XA")
                        ? translator->load(QDir(directory).filePath(base + *preference + ".qm"))
                        : translator->load(QLocale(*preference), QString::fromLatin1(domain), QStringLiteral("_"), directory);
                    if (loaded) {
                        QCoreApplication::installTranslator(translator.get());
                        translators.push_back(std::move(translator));
                    }
                }
            }
        }
        engine->retranslate();
    }
private:
    std::vector<std::unique_ptr<QTranslator>> translators;
};

#include "plugin.moc"
