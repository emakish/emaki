// Isolated Qt Quick/RHI image and render-work probe. Qt private headers are confined
// to this test executable; EGL uses Mesa llvmpipe, with no display or compositor.
#include <QtGui/QGuiApplication>
#include <QtGui/QOffscreenSurface>
#include <QtGui/QOpenGLContext>
#include <QtGui/QScreen>
#include <QtGui/QStyleHints>
#include <QtGui/private/qopenglcontext_p.h>
#include <QtGui/qpa/qplatformopenglcontext.h>
#include <QtQuick/QQuickWindow>
#include <QtQuick/QQuickItem>
#include <QtQuick/QQuickRenderControl>
#include <QtQuick/QQuickGraphicsDevice>
#include <QtQuick/QQuickRenderTarget>
#include <QtQuick/QSGTexture>
#include <QtQuick/QSGTextureProvider>
#include <QtQuick/private/qquickimagebase_p_p.h>
#include <QtQml/QQmlEngine>
#include <QtQml/QQmlComponent>
#include <QtQml/QQmlContext>
#include <QtCore/QCommandLineParser>
#include <QtCore/QElapsedTimer>
#include <QtCore/QFile>
#include <QtCore/QJsonArray>
#include <QtCore/QJsonDocument>
#include <QtCore/QJsonObject>
#include <QtCore/QSet>
#include <QtCore/QThread>
#include <QtCore/QDebug>
#include <rhi/qrhi.h>
#include <rhi/qrhi_platform.h>
#include <EGL/egl.h>
#include <EGL/eglext.h>
#include <GLES3/gl3.h>
#include <cstring>
#include <memory>

namespace {
struct Counters {
    bool enabled = false;
    quint64 frames = 0, clears = 0, draws = 0, drawnFramebuffers = 0;
    QSet<GLuint> currentFramebuffers;
    QHash<GLuint, quint64> framebufferDraws, framebufferClears;
    QHash<GLuint, QSet<QString>> framebufferViewports;
    void clear() {
        if (!enabled) return;
        GLint framebuffer = 0;
        glGetIntegerv(GL_DRAW_FRAMEBUFFER_BINDING, &framebuffer);
        ++clears; ++framebufferClears[framebuffer];
    }
    void draw() {
        if (!enabled) return;
        GLint framebuffer = 0;
        glGetIntegerv(GL_DRAW_FRAMEBUFFER_BINDING, &framebuffer);
        GLint viewport[4] = {};
        glGetIntegerv(GL_VIEWPORT, viewport);
        framebufferViewports[framebuffer].insert(QString::number(viewport[2]) + "x" + QString::number(viewport[3]));
        ++draws; ++framebufferDraws[framebuffer];
        currentFramebuffers.insert(framebuffer);
    }
} counts;

void countedClear(GLbitfield mask) { counts.clear(); glClear(mask); }
void countedDrawArrays(GLenum mode, GLint first, GLsizei count) {
    counts.draw(); glDrawArrays(mode, first, count);
}
void countedDrawElements(GLenum mode, GLsizei count, GLenum type, const void *indices) {
    counts.draw(); glDrawElements(mode, count, type, indices);
}
void countedDrawArraysInstanced(GLenum mode, GLint first, GLsizei count, GLsizei instances) {
    counts.draw(); glDrawArraysInstanced(mode, first, count, instances);
}
void countedDrawElementsInstanced(GLenum mode, GLsizei count, GLenum type, const void *indices, GLsizei instances) {
    counts.draw(); glDrawElementsInstanced(mode, count, type, indices, instances);
}

class SurfacelessContext final : public QPlatformOpenGLContext {
    EGLDisplay display;
    EGLContext eglContext;
public:
    SurfacelessContext() {
        display = eglGetPlatformDisplay(EGL_PLATFORM_SURFACELESS_MESA, EGL_DEFAULT_DISPLAY, nullptr);
        EGLint major = 0, minor = 0;
        if (!eglInitialize(display, &major, &minor)) qFatal("eglInitialize failed: %x", eglGetError());
        if (!eglBindAPI(EGL_OPENGL_ES_API)) qFatal("eglBindAPI failed");
        const EGLint attributes[] = {EGL_SURFACE_TYPE, EGL_PBUFFER_BIT, EGL_RENDERABLE_TYPE, EGL_OPENGL_ES3_BIT, EGL_RED_SIZE, 8, EGL_GREEN_SIZE, 8, EGL_BLUE_SIZE, 8, EGL_ALPHA_SIZE, 8, EGL_NONE};
        EGLConfig config;
        EGLint count;
        if (!eglChooseConfig(display, attributes, &config, 1, &count) || !count) qFatal("eglChooseConfig failed");
        const EGLint contextAttributes[] = {EGL_CONTEXT_CLIENT_VERSION, 3, EGL_NONE};
        eglContext = eglCreateContext(display, config, EGL_NO_CONTEXT, contextAttributes);
        if (eglContext == EGL_NO_CONTEXT) qFatal("eglCreateContext failed: %x", eglGetError());
    }
    ~SurfacelessContext() override { eglDestroyContext(display, eglContext); eglTerminate(display); }
    QSurfaceFormat format() const override {
        QSurfaceFormat f; f.setRenderableType(QSurfaceFormat::OpenGLES); f.setVersion(3, 0);
        f.setDepthBufferSize(24); f.setStencilBufferSize(8); return f;
    }
    bool makeCurrent(QPlatformSurface *) override { return eglMakeCurrent(display, EGL_NO_SURFACE, EGL_NO_SURFACE, eglContext); }
    void doneCurrent() override { eglMakeCurrent(display, EGL_NO_SURFACE, EGL_NO_SURFACE, EGL_NO_CONTEXT); }
    void swapBuffers(QPlatformSurface *) override {}
    QFunctionPointer getProcAddress(const char *name) override {
        if (!std::strcmp(name, "glClear")) return reinterpret_cast<QFunctionPointer>(countedClear);
        if (!std::strcmp(name, "glDrawArrays")) return reinterpret_cast<QFunctionPointer>(countedDrawArrays);
        if (!std::strcmp(name, "glDrawElements")) return reinterpret_cast<QFunctionPointer>(countedDrawElements);
        if (!std::strcmp(name, "glDrawArraysInstanced")) return reinterpret_cast<QFunctionPointer>(countedDrawArraysInstanced);
        if (!std::strcmp(name, "glDrawElementsInstanced")) return reinterpret_cast<QFunctionPointer>(countedDrawElementsInstanced);
        return reinterpret_cast<QFunctionPointer>(eglGetProcAddress(name));
    }
};

QJsonObject imageStats(QObject *root) {
    QJsonArray images;
    QSet<QQuickTextureFactory *> factories;
    qint64 bytes = 0;
    auto objects = root->findChildren<QQuickImageBase *>();
    if (auto *image = qobject_cast<QQuickImageBase *>(root)) objects.prepend(image);
    for (auto *image : objects) {
        auto *data = QQuickImageBasePrivate::get(image);
        auto *factory = data->currentPix ? data->currentPix->textureFactory() : nullptr;
        if (!factory) continue;
        if (!factories.contains(factory)) { factories.insert(factory); bytes += factory->textureByteCount(); }
        const auto size = factory->textureSize();
        images.append(QJsonObject{{"source", image->source().toString()}, {"width", size.width()}, {"height", size.height()},
                                  {"factory", QString::number(reinterpret_cast<quintptr>(factory), 16)}, {"decoded_bytes", factory->textureByteCount()}});
    }
    return {{"images", images}, {"unique_factories", factories.size()}, {"unique_decoded_bytes", bytes}};
}

QJsonObject retainedLayerStats(QObject *root) {
    auto *item = qobject_cast<QQuickItem *>(root->property("retainedLayer").value<QObject *>());
    if (!item) return {};
    auto *provider = item->isTextureProvider() ? item->textureProvider() : nullptr;
    auto *texture = provider ? provider->texture() : nullptr;
    auto *rhiTexture = texture ? texture->rhiTexture() : nullptr;
    return {{"provider", QString::number(reinterpret_cast<quintptr>(provider), 16)},
            {"texture", QString::number(reinterpret_cast<quintptr>(texture), 16)},
            {"rhi_texture", QString::number(reinterpret_cast<quintptr>(rhiTexture), 16)},
            {"native_texture", rhiTexture ? QString::number(rhiTexture->nativeTexture().object) : QString()},
            {"fixture", QJsonObject::fromVariantMap(root->property("renderStats").toMap())}};
}

QJsonObject memoryStats() {
    QFile rollup("/proc/self/smaps_rollup");
    QJsonObject result;
    if (rollup.open(QIODevice::ReadOnly)) {
        const auto lines = rollup.readAll().split('\n');
        for (const QByteArray &line : lines) {
            const auto fields = line.simplified().split(' ');
            if (fields.size() >= 2 && (fields[0] == "Pss:" || fields[0] == "Rss:" || fields[0] == "Pss_Anon:" || fields[0] == "AnonHugePages:"))
                result[QString::fromLatin1(fields[0].chopped(1)) + "_kib"] = fields[1].toDouble();
        }
    }
    return result;
}
}

int renderScene(int argc, char **argv, QQuickItem *externalRoot = nullptr) {
    counts = Counters{};
    qInstallMessageHandler([](QtMsgType, const QMessageLogContext &, const QString &text) { fprintf(stderr, "%s\n", qPrintable(text)); });
    qputenv("QT_QPA_PLATFORM", "offscreen"); qputenv("QT_QUICK_BACKEND", "rhi");
    qputenv("LIBGL_ALWAYS_SOFTWARE", "1"); qputenv("GALLIUM_DRIVER", "llvmpipe");
    qputenv("EGL_PLATFORM", "surfaceless"); qputenv("QML_DISABLE_DISK_CACHE", "1");
    qputenv("LP_NUM_THREADS", "1");
    qputenv("DBUS_SESSION_BUS_ADDRESS", "unix:path=/nonexistent/emaki-render-session-bus");
    qputenv("DBUS_SYSTEM_BUS_ADDRESS", "unix:path=/nonexistent/emaki-render-system-bus");
    qunsetenv("DISPLAY"); qunsetenv("WAYLAND_DISPLAY"); qunsetenv("QT_QPA_PLATFORMTHEME");
    std::unique_ptr<QGuiApplication> application;
    if (!QGuiApplication::instance()) application = std::make_unique<QGuiApplication>(argc, argv);
    auto &app = *QCoreApplication::instance();
    QCommandLineParser parser;
    parser.addHelpOption();
    parser.addOptions({{"qml", "Fixture with an ordinary Item root", "path"}, {"png", "Output image", "path"},
                       {"stats", "Output JSON counters", "path"}, {"width", "Logical width", "number", "640"},
                       {"height", "Logical height", "number", "480"}, {"scale", "Render target DPR", "number", "1"},
                       {"milliseconds", "Measured duration after warmup", "number", "1000"}, {"warmup", "Uncounted warmup duration", "number", "500"},
                       {"ready-property", "Wait for this boolean root property before warmup (15 s limit)", "name", ""},
                       {"properties", "JSON object of fixture initial properties", "json", "{}"}});
    QStringList arguments;
    for (int i = 0; i < argc; ++i) arguments.append(QString::fromLocal8Bit(argv[i]));
    parser.process(arguments);
    // A screenshot compares materials, not the random phase of a blinking caret.
    // Counter-only runs keep the normal style hint so their work stays observable.
    if (parser.isSet("png")) QGuiApplication::styleHints()->setCursorFlashTime(0);
    if (!externalRoot && !parser.isSet("qml")) parser.showHelp(2);
    const int width = parser.value("width").toInt(), height = parser.value("height").toInt();
    const qreal scale = parser.value("scale").toDouble();
    const int warmup = parser.value("warmup").toInt(), duration = parser.value("milliseconds").toInt();
    const QByteArray readyProperty = parser.value("ready-property").toUtf8();
    if (width <= 0 || height <= 0 || scale <= 0 || warmup < 0 || duration < 0) qFatal("Invalid size/scale/duration");
    QJsonParseError parseError;
    const auto properties = QJsonDocument::fromJson(parser.value("properties").toUtf8(), &parseError);
    if (parseError.error != QJsonParseError::NoError || !properties.isObject()) qFatal("--properties needs a JSON object");
    QQuickWindow::setGraphicsApi(QSGRendererInterface::OpenGL);
    QOpenGLContext context;
    QOpenGLContextPrivate::get(&context)->adopt(new SurfacelessContext());
    QOffscreenSurface surface;
    surface.setFormat(context.format()); surface.create();
    if (!context.makeCurrent(&surface)) qFatal("Qt makeCurrent failed");
    const QString renderer = QString::fromLatin1(reinterpret_cast<const char *>(glGetString(GL_RENDERER)));
    if (!renderer.contains("llvmpipe")) qFatal("Refusing non-llvmpipe renderer: %s", qPrintable(renderer));
    qInfo() << "GL_RENDERER" << renderer;
    QRhiGles2InitParams params;
    params.fallbackSurface = &surface; params.format = context.format();
    QRhiGles2NativeHandles native; native.context = &context;
    std::unique_ptr<QRhi> rhi(QRhi::create(QRhi::OpenGLES2, &params, {}, &native));
    if (!rhi) qFatal("QRhi create failed");
    QQuickRenderControl control;
    QQuickWindow window(&control);
    window.setGraphicsDevice(QQuickGraphicsDevice::fromRhi(rhi.get()));
    window.setGeometry(0, 0, width, height);
    if (!control.initialize()) qFatal("RenderControl initialize failed");
    const QSize pixelSize(qRound(width * scale), qRound(height * scale));
    GLuint texture;
    glGenTextures(1, &texture); glBindTexture(GL_TEXTURE_2D, texture);
    glTexImage2D(GL_TEXTURE_2D, 0, GL_RGBA8, pixelSize.width(), pixelSize.height(), 0, GL_RGBA, GL_UNSIGNED_BYTE, nullptr);
    auto target = QQuickRenderTarget::fromOpenGLTexture(texture, pixelSize);
    target.setDevicePixelRatio(scale); window.setRenderTarget(target);
    std::unique_ptr<QQmlEngine> engine;
    std::unique_ptr<QObject> object;
    QQuickItem *root = externalRoot;
    if (!root) {
        engine = std::make_unique<QQmlEngine>();
        engine->rootContext()->setContextProperty("RenderFixture", QVariantMap{{"width", width}, {"height", height}, {"scale", scale}});
        QQmlComponent component(engine.get(), QUrl::fromLocalFile(parser.value("qml")));
        if (component.isError()) qFatal("QML: %s", qPrintable(component.errorString()));
        object.reset(component.createWithInitialProperties(properties.object().toVariantMap()));
        root = qobject_cast<QQuickItem *>(object.get());
        if (!root) qFatal("Root must be Item: %s", qPrintable(component.errorString()));
    }
    root->setParentItem(window.contentItem());
    if (!readyProperty.isEmpty() && !root->property(readyProperty.constData()).isValid())
        qFatal("Readiness property does not exist: %s", readyProperty.constData());
    bool dirty = true;
    QObject::connect(&control, &QQuickRenderControl::renderRequested, [&] { dirty = true; });
    QObject::connect(&control, &QQuickRenderControl::sceneChanged, [&] { dirty = true; });
    QElapsedTimer elapsed; elapsed.start();
    QElapsedTimer warmupElapsed, measurement;
    QJsonObject fixtureStart;
    QJsonArray retainedLayers;
    QJsonObject lastRetainedLayer;
    bool renderedOnce = false;
    qint64 readinessElapsedMs = 0;
    while (true) {
        if (!measurement.isValid() && warmupElapsed.isValid() && warmupElapsed.elapsed() >= warmup) {
            counts.enabled = true;
            measurement.start();
            fixtureStart = QJsonObject::fromVariantMap(root->property("renderStats").toMap());
        }
        app.processEvents();
        if (dirty) {
            dirty = false;
            control.polishItems(); control.beginFrame(); control.sync(); control.render(); control.endFrame();
            const auto retained = retainedLayerStats(root);
            if (!retained.isEmpty() && retained != lastRetainedLayer) {
                retainedLayers.append(retained);
                lastRetainedLayer = retained;
            }
            renderedOnce = true;
            if (counts.enabled) { ++counts.frames; counts.drawnFramebuffers += counts.currentFramebuffers.size(); }
            counts.currentFramebuffers.clear();
        }
        // Shader compilation can block the first render long enough to expire a
        // wall-clock warmup before fixture timers have even configured a panel.
        // Start warmup only after a rendered frame and the explicit ready gate.
        if (!warmupElapsed.isValid()) {
            if (elapsed.elapsed() > 15000)
                qFatal("Fixture readiness timed out after 15000 ms (property: %s)", readyProperty.isEmpty() ? "first-render" : readyProperty.constData());
            if (renderedOnce && (readyProperty.isEmpty() || root->property(readyProperty.constData()).toBool())) {
                readinessElapsedMs = elapsed.elapsed();
                warmupElapsed.start();
            }
        }
        if (measurement.isValid() && measurement.elapsed() >= duration) break;
        QThread::msleep(1);
    }
    counts.enabled = false;
    if (parser.isSet("png")) {
        GLuint framebuffer;
        glGenFramebuffers(1, &framebuffer); glBindFramebuffer(GL_FRAMEBUFFER, framebuffer);
        glFramebufferTexture2D(GL_FRAMEBUFFER, GL_COLOR_ATTACHMENT0, GL_TEXTURE_2D, texture, 0);
        QImage image(pixelSize, QImage::Format_RGBA8888);
        glReadPixels(0, 0, pixelSize.width(), pixelSize.height(), GL_RGBA, GL_UNSIGNED_BYTE, image.bits());
        if (!image.flipped(Qt::Vertical).save(parser.value("png"))) qFatal("PNG save failed");
        glBindFramebuffer(GL_FRAMEBUFFER, 0); glDeleteFramebuffers(1, &framebuffer);
    }
    QJsonObject framebuffers;
    for (auto it = counts.framebufferDraws.cbegin(); it != counts.framebufferDraws.cend(); ++it) {
        auto viewports = counts.framebufferViewports.value(it.key()).values();
        viewports.sort();
        framebuffers[QString::number(it.key())] = QJsonObject{{"draws", qint64(it.value())}, {"clears", qint64(counts.framebufferClears.value(it.key()))},
                                                           {"draw_viewport_sizes", QJsonArray::fromStringList(viewports)}};
    }
    QJsonObject stats{{"method", "offscreen Qt Quick RHI; surfaceless EGL llvmpipe; frames only on renderRequested/sceneChanged"},
                      {"renderer", renderer}, {"qt", QT_VERSION_STR}, {"width", width}, {"height", height}, {"scale", scale},
                      {"screen_dpr", window.screen()->devicePixelRatio()}, {"window_dpr", window.QWindow::devicePixelRatio()},
                      {"effective_window_dpr", window.effectiveDevicePixelRatio()},
                      {"rounding_policy", int(QGuiApplication::highDpiScaleFactorRoundingPolicy())},
                      {"ready_property", QString::fromUtf8(readyProperty)}, {"readiness_reached", true}, {"readiness_elapsed_ms", readinessElapsedMs},
                      {"warmup_ms", warmup}, {"measurement_ms", duration}, {"measured_elapsed_ms", measurement.elapsed()}, {"elapsed_ms", elapsed.elapsed()},
                      {"frames", qint64(counts.frames)}, {"gl_clear_calls", qint64(counts.clears)}, {"gl_draw_calls", qint64(counts.draws)},
                      {"drawn_framebuffers_summed_per_frame", qint64(counts.drawnFramebuffers)}, {"framebuffer_work", framebuffers},
                      {"fixture_start", fixtureStart}, {"fixture_end", QJsonObject::fromVariantMap(root->property("renderStats").toMap())},
                      {"retained_layer_states", retainedLayers},
                      {"images", imageStats(root)}, {"memory", memoryStats()}};
    if (parser.isSet("stats")) {
        QFile file(parser.value("stats"));
        if (!file.open(QIODevice::WriteOnly) || file.write(QJsonDocument(stats).toJson()) < 0) qFatal("JSON save failed");
    }
    qInfo().noquote() << "RENDER_SHELL" << QJsonDocument(stats).toJson(QJsonDocument::Compact);
    root->setParentItem(nullptr);
    object.reset(); control.invalidate();
    glDeleteTextures(1, &texture);
    return 0;
}

#ifndef EMAKI_RENDER_PLUGIN
int main(int argc, char **argv) { return renderScene(argc, argv); }
#else
#include <QtQml/QQmlExtensionPlugin>
class RenderProbe : public QObject {
    Q_OBJECT
public:
    using QObject::QObject;
    Q_INVOKABLE void render(QQuickItem *item, const QVariantMap &options) {
        if (!item) qFatal("RenderProbe needs a source Item");
        QList<QByteArray> arguments{"render-shell"};
        for (auto it = options.cbegin(); it != options.cend(); ++it) {
            arguments.append("--" + it.key().toUtf8());
            arguments.append(it.value().toString().toUtf8());
        }
        QList<char *> argv;
        for (auto &argument : arguments) argv.append(argument.data());
        renderScene(argv.size(), argv.data(), item);
    }
};
class RenderPlugin : public QQmlExtensionPlugin {
    Q_OBJECT
    Q_PLUGIN_METADATA(IID "org.qt-project.Qt.QQmlExtensionInterface/1.0")
public:
    void registerTypes(const char *uri) override { qmlRegisterType<RenderProbe>(uri, 1, 0, "RenderProbe"); }
};
#include "render-shell.moc"
#endif
