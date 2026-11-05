/*
    SPDX-FileCopyrightText: 2010 Fredrik Höglund <fredrik@kde.org>
    SPDX-FileCopyrightText: 2011 Philipp Knechtges <philipp-dev@knechtges.com>
    SPDX-FileCopyrightText: 2018 Alex Nemeth <alex.nemeth329@gmail.com>

    SPDX-FileCopyrightText: 2026 Zashterminal contributors

    SPDX-License-Identifier: GPL-2.0-or-later
*/

// Renderer source: KDE KWin v6.8.2, src/plugins/blur/blur.cpp.
// https://invent.kde.org/plasma/kwin/-/tree/v6.8.2/src/plugins/blur
// Keep the scene damage, cached backdrop and dual-Kawase GPU passes in sync
// with that version; only explicitly authorized native surfaces are selected.

#include "blur.h"
#include "core/rendertarget.h"
#include "core/renderviewport.h"
#include "effect/effecthandler.h"
#include "scene/backgroundeffectitem.h"
#include "scene/windowitem.h"
#include "wayland/clientconnection.h"
#include "wayland/surface.h"
#include "window.h"

#include <QDBusConnection>
#include <QDBusConnectionInterface>
#include <QDBusMessage>
#include <QDBusReply>
#include <QLoggingCategory>
#include <QMatrix4x4>
#include <algorithm>
#include <array>
#include <limits>

Q_LOGGING_CATEGORY(ZASHTERMINAL_BLUR, "kwin_effect_zashterminalblur", QtWarningMsg)

static void ensureResources()
{
    Q_INIT_RESOURCE(zashterminalblur);
}

namespace KWin
{
namespace
{
constexpr auto objectPath = "/org/leoberbert/ZashterminalBlur";
struct Kernel
{
    size_t iterations;
    float offset;
    uint32_t expand;
};
// KDE's 15-step distribution, retaining fractional offsets (not truncating
// them to integers). Each step changes the real sampling kernel.
constexpr std::array<Kernel, 15> kernels = {{
    {1, 1.5f, 10}, {1, 2.0f, 10},
    {2, 2.5f, 20}, {2, 3.0f, 20},
    {3, 2.6f, 50}, {3, 3.2f, 50}, {3, 3.8f, 50}, {3, 4.4f, 50}, {3, 5.0f, 50},
    {4, 23.0f / 6.0f, 150}, {4, 14.0f / 3.0f, 150}, {4, 5.5f, 150},
    {4, 19.0f / 3.0f, 150}, {4, 43.0f / 6.0f, 150}, {4, 8.0f, 150},
}};

std::optional<BlurWindowKey> windowKey(EffectWindow *w)
{
    if (!w->isWaylandClient() || w->isDeleted()) {
        return std::nullopt;
    }
    const auto surface = w->surface();
    if (!surface || surface->client()->processId() <= 0) {
        return std::nullopt;
    }
    return BlurWindowKey{uint(surface->client()->processId()), surface->id()};
}
}

ZashterminalBlurEffect::ZashterminalBlurEffect()
{
    ensureResources();
    m_onscreenPass.shader = ShaderManager::instance()->generateShaderFromFile(ShaderTrait::MapTexture,
                                                                              QStringLiteral(":/effects/zashterminalblur/shaders/vertex.vert"),
                                                                              QStringLiteral(":/effects/zashterminalblur/shaders/onscreen.frag"));
    if (!m_onscreenPass.shader) {
        qCWarning(ZASHTERMINAL_BLUR) << "Failed to load onscreen pass shader";
        return;
    } else {
        m_onscreenPass.mvpMatrixLocation = m_onscreenPass.shader->uniformLocation("modelViewProjectionMatrix");
        m_onscreenPass.offsetLocation = m_onscreenPass.shader->uniformLocation("offset");
        m_onscreenPass.halfpixelLocation = m_onscreenPass.shader->uniformLocation("halfpixel");
    }

    m_roundedOnscreenPass.shader = ShaderManager::instance()->generateShaderFromFile(ShaderTrait::MapTexture,
                                                                                     QStringLiteral(":/effects/zashterminalblur/shaders/onscreen_rounded.vert"),
                                                                                     QStringLiteral(":/effects/zashterminalblur/shaders/onscreen_rounded.frag"));
    if (!m_roundedOnscreenPass.shader) {
        qCWarning(ZASHTERMINAL_BLUR) << "Failed to load onscreen pass shader";
        return;
    } else {
        m_roundedOnscreenPass.mvpMatrixLocation = m_roundedOnscreenPass.shader->uniformLocation("modelViewProjectionMatrix");
        m_roundedOnscreenPass.offsetLocation = m_roundedOnscreenPass.shader->uniformLocation("offset");
        m_roundedOnscreenPass.halfpixelLocation = m_roundedOnscreenPass.shader->uniformLocation("halfpixel");
        m_roundedOnscreenPass.boxLocation = m_roundedOnscreenPass.shader->uniformLocation("box");
        m_roundedOnscreenPass.cornerRadiusLocation = m_roundedOnscreenPass.shader->uniformLocation("cornerRadius");
        m_roundedOnscreenPass.opacityLocation = m_roundedOnscreenPass.shader->uniformLocation("opacity");
    }

    m_downsamplePass.shader = ShaderManager::instance()->generateShaderFromFile(ShaderTrait::MapTexture,
                                                                                QStringLiteral(":/effects/zashterminalblur/shaders/vertex.vert"),
                                                                                QStringLiteral(":/effects/zashterminalblur/shaders/downsample.frag"));
    if (!m_downsamplePass.shader) {
        qCWarning(ZASHTERMINAL_BLUR) << "Failed to load downsampling pass shader";
        return;
    } else {
        m_downsamplePass.mvpMatrixLocation = m_downsamplePass.shader->uniformLocation("modelViewProjectionMatrix");
        m_downsamplePass.offsetLocation = m_downsamplePass.shader->uniformLocation("offset");
        m_downsamplePass.halfpixelLocation = m_downsamplePass.shader->uniformLocation("halfpixel");
    }

    m_upsamplePass.shader = ShaderManager::instance()->generateShaderFromFile(ShaderTrait::MapTexture,
                                                                              QStringLiteral(":/effects/zashterminalblur/shaders/vertex.vert"),
                                                                              QStringLiteral(":/effects/zashterminalblur/shaders/upsample.frag"));
    if (!m_upsamplePass.shader) {
        qCWarning(ZASHTERMINAL_BLUR) << "Failed to load upsampling pass shader";
        return;
    } else {
        m_upsamplePass.mvpMatrixLocation = m_upsamplePass.shader->uniformLocation("modelViewProjectionMatrix");
        m_upsamplePass.offsetLocation = m_upsamplePass.shader->uniformLocation("offset");
        m_upsamplePass.halfpixelLocation = m_upsamplePass.shader->uniformLocation("halfpixel");
    }

    connect(effects, &EffectsHandler::windowAdded, this, &ZashterminalBlurEffect::slotWindowAdded);
    connect(effects, &EffectsHandler::windowClosed, this, &ZashterminalBlurEffect::slotWindowDeleted);
    connect(effects, &EffectsHandler::windowDeleted, this, &ZashterminalBlurEffect::slotWindowDeleted);
    connect(effects, &EffectsHandler::viewRemoved, this, &ZashterminalBlurEffect::slotViewRemoved);
    m_ownerWatcher.setConnection(QDBusConnection::sessionBus());
    m_ownerWatcher.setWatchMode(QDBusServiceWatcher::WatchForUnregistration);
    connect(&m_ownerWatcher, &QDBusServiceWatcher::serviceUnregistered, this, &ZashterminalBlurEffect::removeOwner);
    for (EffectWindow *w : effects->stackingOrder()) {
        slotWindowAdded(w);
    }
    m_registered = QDBusConnection::sessionBus().registerObject(QString::fromLatin1(objectPath), this, QDBusConnection::ExportScriptableSlots);
    m_valid = m_registered;
    if (!m_registered) {
        qCWarning(ZASHTERMINAL_BLUR) << "Could not register per-window blur D-Bus object";
    }
}

ZashterminalBlurEffect::~ZashterminalBlurEffect()
{
    if (m_registered) {
        QDBusConnection::sessionBus().unregisterObject(QString::fromLatin1(objectPath));
    }
    // GL objects must be destroyed while the compositor context is current.
    effects->makeOpenGLContextCurrent();
    m_windows.clear();
    effects->addRepaintFull();
}

uint ZashterminalBlurEffect::protocolVersion() const
{
    return 1;
}

bool ZashterminalBlurEffect::setWindowBlur(uint pid, qulonglong surfaceId, uint strength)
{
    if (!m_valid || !calledFromDBus() || pid == 0 || surfaceId == 0
        || surfaceId > std::numeric_limits<uint32_t>::max() || strength > kernels.size()) {
        return false;
    }
    const QString owner = message().service();
    const QDBusReply<uint> callerPid = connection().interface()->servicePid(owner);
    if (!callerPid.isValid() || callerPid.value() != pid) {
        return false;
    }
    const BlurWindowKey key{pid, uint(surfaceId)};
    if (strength == 0) {
        m_requests.erase(key);
        for (EffectWindow *w : effects->stackingOrder()) {
            if (windowKey(w) == key) {
                removeWindow(w);
            }
        }
        return true;
    }
    EffectWindow *matched = nullptr;
    for (EffectWindow *w : effects->stackingOrder()) {
        if (windowKey(w) == key) {
            // The resource ID is local to a Wayland connection. If a process
            // opened two connections with the same ID, never pick arbitrarily.
            if (matched) {
                return false;
            }
            matched = w;
        }
    }
    // Never stack this renderer on top of standard Wayland content blur.
    // The GTK backend must destroy its standard blur request first.
    if (matched && !matched->surface()->blurRegion().isEmpty()) {
        return false;
    }
    m_requests.insert_or_assign(key, Request{strength, owner});
    m_ownerWatcher.addWatchedService(owner);
    if (matched) {
        applyRequest(matched);
    }
    return true;
}

void ZashterminalBlurEffect::slotWindowAdded(EffectWindow *w)
{
    if (!w->isWaylandClient() || m_connections.contains(w)) {
        return;
    }
    if (const auto key = windowKey(w)) {
        m_windowKeys.insert_or_assign(w, *key);
    }
    auto &connections = m_connections[w];
    connections.append(connect(w, &EffectWindow::windowFrameGeometryChanged, this, [this, w]() {
        updateGeometry(w);
    }));
    if (auto surface = w->surface()) {
        connections.append(connect(surface, &SurfaceInterface::blurChanged, this, [this, w]() {
            // A client switching back to standard blur cannot be double-blurred.
            applyRequest(w);
        }));
    }
    applyRequest(w);
}

void ZashterminalBlurEffect::applyRequest(EffectWindow *w)
{
    const auto key = windowKey(w);
    if (!key) {
        return;
    }
    const auto request = m_requests.find(*key);
    if (request == m_requests.end() || !w->surface()->blurRegion().isEmpty()) {
        removeWindow(w);
        return;
    }
    // PID + resource ID must identify exactly one current native surface.
    for (EffectWindow *other : effects->stackingOrder()) {
        if (other != w && windowKey(other) == key) {
            removeWindow(other);
            removeWindow(w);
            return;
        }
    }
    auto &data = m_windows[w];
    data.key = *key;
    data.strength = request->second.strength;
    if (!data.blurItem) {
        data.blurItem = std::make_unique<BackgroundEffectItem>(w->windowItem());
    }
    data.blurItem->setPixelsToExpandRepaintsBelowOpaqueRegions(kernels[data.strength - 1].expand);
    updateGeometry(w);
    effects->addRepaint(w->expandedGeometry());
}

void ZashterminalBlurEffect::updateGeometry(EffectWindow *w)
{
    if (auto it = m_windows.find(w); it != m_windows.end()) {
        // BackgroundEffectItem integrates damage from moving/resizing windows
        // and changing content *below* the client into KWin's scene damage.
        it->second.blurItem->setEffectBoundingRect(w->contentsRect());
    }
}

void ZashterminalBlurEffect::removeWindow(EffectWindow *w)
{
    if (auto it = m_windows.find(w); it != m_windows.end()) {
        effects->addRepaint(w->expandedGeometry());
        effects->makeOpenGLContextCurrent();
        m_windows.erase(it);
    }
}

void ZashterminalBlurEffect::slotWindowDeleted(EffectWindow *w)
{
    if (auto it = m_windowKeys.find(w); it != m_windowKeys.end()) {
        m_requests.erase(it->second);
        m_windowKeys.erase(it);
    }
    removeWindow(w);
    if (auto it = m_connections.find(w); it != m_connections.end()) {
        for (const auto &connection : it->second) {
            disconnect(connection);
        }
        m_connections.erase(it);
    }
}

void ZashterminalBlurEffect::removeOwner(const QString &owner)
{
    m_ownerWatcher.removeWatchedService(owner);
    for (auto it = m_requests.begin(); it != m_requests.end();) {
        if (it->second.owner == owner) {
            const auto key = it->first;
            it = m_requests.erase(it);
            for (auto window = m_windows.begin(); window != m_windows.end();) {
                if (window->second.key == key) {
                    EffectWindow *w = window->first;
                    ++window;
                    removeWindow(w);
                } else {
                    ++window;
                }
            }
        } else {
            ++it;
        }
    }
}

void ZashterminalBlurEffect::slotViewRemoved(RenderView *view)
{
    effects->makeOpenGLContextCurrent();
    for (auto &[window, data] : m_windows) {
        data.render.erase(view);
    }
}

bool ZashterminalBlurEffect::supported()
{
    return effects->isOpenGLCompositing();
}

void ZashterminalBlurEffect::prePaintScreen(ScreenPrePaintData &data)
{
    m_currentView = data.view;

    effects->prePaintScreen(data);
}

bool ZashterminalBlurEffect::shouldBlur(const EffectWindow *w, int mask, const WindowPaintData &data) const
{
    if (effects->activeFullScreenEffect() && !w->data(WindowForceBlurRole).toBool()) {
        return false;
    }

    if (w->isDesktop() || !w->surface() || !w->surface()->blurRegion().isEmpty()) {
        return false;
    }

    bool scaled = !qFuzzyCompare(data.xScale(), 1.0) || !qFuzzyCompare(data.yScale(), 1.0);
    bool translated = data.xTranslation() || data.yTranslation();

    if ((scaled || (translated || (mask & PAINT_WINDOW_TRANSFORMED))) && !w->data(WindowForceBlurRole).toBool()) {
        return false;
    }

    return true;
}

bool ZashterminalBlurEffect::drawWindow(const RenderTarget &renderTarget, const RenderViewport &viewport, EffectWindow *w, int mask, const Region &deviceRegion, WindowPaintData &data)
{
    blur(renderTarget, viewport, w, mask, deviceRegion, data);

    // Draw the window over the blurred area
    return effects->drawWindow(renderTarget, viewport, w, mask, deviceRegion, data);
}

void ZashterminalBlurEffect::blur(const RenderTarget &renderTarget, const RenderViewport &viewport, EffectWindow *w, int mask, const Region &deviceRegion, WindowPaintData &data)
{
    auto it = m_windows.find(w);
    if (it == m_windows.end()) {
        return;
    }

    ZashBlurWindowData &blurInfo = it->second;
    if (!shouldBlur(w, mask, data)) {
        return;
    }

    const auto &kernel = kernels[blurInfo.strength - 1];
    const size_t m_iterationCount = kernel.iterations;
    const float m_offset = kernel.offset;
    ZashBlurRenderData &renderInfo = blurInfo.render[m_currentView];

    // Compute the effective blur shape. Note that if the window is transformed, so will be the blur shape.
    RectF blurShape = w->contentsRect();
    if (data.xScale() != 1 || data.yScale() != 1) {
        blurShape = RectF(blurShape.x() * data.xScale(), blurShape.y() * data.yScale(), blurShape.width() * data.xScale(), blurShape.height() * data.yScale());
    }
    if (data.xTranslation() || data.yTranslation()) {
        blurShape.translate(data.xTranslation(), data.yTranslation());
    }

    blurShape.translate(w->pos());

    const Rect backgroundRect = blurShape.rounded();
    if (backgroundRect.isEmpty()) {
        return;
    }
    const Rect scaledBackgroundRect = backgroundRect.scaled(viewport.scale()).rounded();
    const Rect deviceBackgroundRect = viewport.mapToDeviceCoordinates(backgroundRect).rounded();
    const auto opacity = w->opacity() * data.opacity();

    // Get the effective shape that will be actually blurred. It's possible that all of it will be clipped.
    auto &effectiveShape = renderInfo.effectiveShape;
    effectiveShape.clear();
    const RectF deviceShapeRect = blurShape.translated(-backgroundRect.topLeft()).scaled(viewport.scale()).rounded();
    if (deviceRegion != Region::infinite()) {
        effectiveShape.reserve(deviceRegion.rects().size());
        for (const Rect &clipRect : deviceRegion.rects()) {
            const RectF deviceClipRect = clipRect.translated(-deviceBackgroundRect.topLeft());
            if (const RectF intersected = deviceClipRect.intersected(deviceShapeRect); !intersected.isEmpty()) {
                effectiveShape.append(intersected);
            }
        }
    } else {
        effectiveShape.append(deviceShapeRect);
    }
    if (effectiveShape.isEmpty()) {
        return;
    }

    // Maybe reallocate offscreen render targets. Keep in mind that the first one contains
    // original background behind the window, it's not blurred.
    GLenum textureFormat = GL_RGBA8;
    if (renderTarget.texture()) {
        textureFormat = renderTarget.texture()->internalFormat();
    }

    if (renderInfo.framebuffers.size() != (m_iterationCount + 1) || renderInfo.textures[0]->size() != backgroundRect.size() || renderInfo.textures[0]->internalFormat() != textureFormat) {
        renderInfo.framebuffers.clear();
        renderInfo.textures.clear();
        renderInfo.framebuffers.reserve(m_iterationCount + 1);
        renderInfo.textures.reserve(m_iterationCount + 1);

        glClearColor(0, 0, 0, 0);
        for (size_t i = 0; i <= m_iterationCount; ++i) {
            auto texture = GLTexture::allocate(textureFormat, QSize(std::max(1, backgroundRect.width() / (1 << i)), std::max(1, backgroundRect.height() / (1 << i))));
            if (!texture) {
                qCWarning(ZASHTERMINAL_BLUR) << "Failed to allocate an offscreen texture";
                return;
            }
            texture->setFilter(GL_LINEAR);
            texture->setWrapMode(GL_CLAMP_TO_EDGE);

            auto framebuffer = std::make_unique<GLFramebuffer>(texture.get());
            if (!framebuffer->valid()) {
                qCWarning(ZASHTERMINAL_BLUR) << "Failed to create an offscreen framebuffer";
                return;
            }
            EglContext::currentContext()->pushFramebuffer(framebuffer.get());
            glClear(GL_COLOR_BUFFER_BIT);
            EglContext::currentContext()->popFramebuffer();
            renderInfo.textures.push_back(std::move(texture));
            renderInfo.framebuffers.push_back(std::move(framebuffer));
        }
    }

    // Fetch the pixels behind the shape that is going to be blurred.
    const Region dirtyRegion = viewport.mapFromDeviceCoordinatesContained(deviceRegion) & backgroundRect;
    for (const Rect &dirtyRect : dirtyRegion.rects()) {
        renderInfo.framebuffers[0]->blitFromRenderTarget(renderTarget, viewport, dirtyRect, dirtyRect.translated(-backgroundRect.topLeft()));
    }

    // Upload the geometry: the first 6 vertices are used when downsampling and upsampling offscreen,
    // the remaining vertices are used when rendering on the screen.
    GLVertexBuffer *vbo = GLVertexBuffer::streamingBuffer();
    vbo->reset();
    vbo->setAttribLayout(std::span(GLVertexBuffer::GLVertex2DLayout), sizeof(GLVertex2D));

    const int vertexCount = effectiveShape.size() * 6;
    if (auto result = vbo->map<GLVertex2D>(6 + vertexCount)) {
        auto map = *result;

        size_t vboIndex = 0;

        // The geometry that will be blurred offscreen, in logical pixels.
        {
            const RectF localRect = RectF(0, 0, backgroundRect.width(), backgroundRect.height());

            const float x0 = localRect.left();
            const float y0 = localRect.top();
            const float x1 = localRect.right();
            const float y1 = localRect.bottom();

            const float u0 = x0 / backgroundRect.width();
            const float v0 = 1.0f - y0 / backgroundRect.height();
            const float u1 = x1 / backgroundRect.width();
            const float v1 = 1.0f - y1 / backgroundRect.height();

            // first triangle
            map[vboIndex++] = GLVertex2D{
                .position = QVector2D(x0, y0),
                .texcoord = QVector2D(u0, v0),
            };
            map[vboIndex++] = GLVertex2D{
                .position = QVector2D(x1, y1),
                .texcoord = QVector2D(u1, v1),
            };
            map[vboIndex++] = GLVertex2D{
                .position = QVector2D(x0, y1),
                .texcoord = QVector2D(u0, v1),
            };

            // second triangle
            map[vboIndex++] = GLVertex2D{
                .position = QVector2D(x0, y0),
                .texcoord = QVector2D(u0, v0),
            };
            map[vboIndex++] = GLVertex2D{
                .position = QVector2D(x1, y0),
                .texcoord = QVector2D(u1, v0),
            };
            map[vboIndex++] = GLVertex2D{
                .position = QVector2D(x1, y1),
                .texcoord = QVector2D(u1, v1),
            };
        }

        // The geometry that will be painted on screen, in device pixels.
        for (const RectF &rect : effectiveShape) {
            const float x0 = rect.left();
            const float y0 = rect.top();
            const float x1 = rect.right();
            const float y1 = rect.bottom();

            const float u0 = x0 / scaledBackgroundRect.width();
            const float v0 = 1.0f - y0 / scaledBackgroundRect.height();
            const float u1 = x1 / scaledBackgroundRect.width();
            const float v1 = 1.0f - y1 / scaledBackgroundRect.height();

            // first triangle
            map[vboIndex++] = GLVertex2D{
                .position = QVector2D(x0, y0),
                .texcoord = QVector2D(u0, v0),
            };
            map[vboIndex++] = GLVertex2D{
                .position = QVector2D(x1, y1),
                .texcoord = QVector2D(u1, v1),
            };
            map[vboIndex++] = GLVertex2D{
                .position = QVector2D(x0, y1),
                .texcoord = QVector2D(u0, v1),
            };

            // second triangle
            map[vboIndex++] = GLVertex2D{
                .position = QVector2D(x0, y0),
                .texcoord = QVector2D(u0, v0),
            };
            map[vboIndex++] = GLVertex2D{
                .position = QVector2D(x1, y0),
                .texcoord = QVector2D(u1, v0),
            };
            map[vboIndex++] = GLVertex2D{
                .position = QVector2D(x1, y1),
                .texcoord = QVector2D(u1, v1),
            };
        }

        vbo->unmap();
    } else {
        qCWarning(ZASHTERMINAL_BLUR) << "Failed to map vertex buffer";
        return;
    }

    vbo->bindArrays();

    // The downsample pass of the dual Kawase algorithm: the background will be scaled down 50% every iteration.
    {
        ShaderManager::instance()->pushShader(m_downsamplePass.shader.get());

        QMatrix4x4 projectionMatrix;
        projectionMatrix.ortho(QRectF(0.0, 0.0, backgroundRect.width(), backgroundRect.height()));

        m_downsamplePass.shader->setUniform(m_downsamplePass.mvpMatrixLocation, projectionMatrix);
        m_downsamplePass.shader->setUniform(m_downsamplePass.offsetLocation, float(m_offset));

        for (size_t i = 1; i < renderInfo.framebuffers.size(); ++i) {
            const auto &read = renderInfo.framebuffers[i - 1];
            const auto &draw = renderInfo.framebuffers[i];

            const QVector2D halfpixel(0.5 / read->colorAttachment()->width(),
                                      0.5 / read->colorAttachment()->height());
            m_downsamplePass.shader->setUniform(m_downsamplePass.halfpixelLocation, halfpixel);

            read->colorAttachment()->bind();

            GLFramebuffer::pushFramebuffer(draw.get());
            vbo->draw(GL_TRIANGLES, 0, 6);
        }

        ShaderManager::instance()->popShader();
    }

    // The upsample pass of the dual Kawase algorithm: the background will be scaled up 200% every iteration.
    {
        ShaderManager::instance()->pushShader(m_upsamplePass.shader.get());

        QMatrix4x4 projectionMatrix;
        projectionMatrix.ortho(QRectF(0.0, 0.0, backgroundRect.width(), backgroundRect.height()));

        m_upsamplePass.shader->setUniform(m_upsamplePass.mvpMatrixLocation, projectionMatrix);
        m_upsamplePass.shader->setUniform(m_upsamplePass.offsetLocation, float(m_offset));

        for (size_t i = renderInfo.framebuffers.size() - 1; i > 1; --i) {
            GLFramebuffer::popFramebuffer();
            const auto &read = renderInfo.framebuffers[i];

            const QVector2D halfpixel(0.5 / read->colorAttachment()->width(),
                                      0.5 / read->colorAttachment()->height());
            m_upsamplePass.shader->setUniform(m_upsamplePass.halfpixelLocation, halfpixel);

            read->colorAttachment()->bind();

            vbo->draw(GL_TRIANGLES, 0, 6);
        }

        ShaderManager::instance()->popShader();
    }

    const float modulation = opacity * opacity;

    if (const BorderRadius cornerRadius = w->window()->borderRadius(); !cornerRadius.isNull()) {
        ShaderManager::instance()->pushShader(m_roundedOnscreenPass.shader.get());

        QMatrix4x4 projectionMatrix = viewport.projectionMatrix();
        projectionMatrix.translate(scaledBackgroundRect.x(), scaledBackgroundRect.y());

        GLFramebuffer::popFramebuffer();
        const auto &read = renderInfo.framebuffers[1];

        const QVector2D halfpixel(0.5 / read->colorAttachment()->width(),
                                  0.5 / read->colorAttachment()->height());

        const RectF transformedRect = RectF{
            w->frameGeometry().x() + data.xTranslation(),
            w->frameGeometry().y() + data.yTranslation(),
            w->frameGeometry().width() * data.xScale(),
            w->frameGeometry().height() * data.yScale(),
        };
        const RectF nativeBox = transformedRect
                                    .scaled(viewport.scale())
                                    .rounded()
                                    .translated(-scaledBackgroundRect.topLeft());
        const BorderRadius nativeCornerRadius = cornerRadius.scaled(viewport.scale()).rounded();

        m_roundedOnscreenPass.shader->setUniform(m_roundedOnscreenPass.mvpMatrixLocation, projectionMatrix);
        m_roundedOnscreenPass.shader->setUniform(m_roundedOnscreenPass.halfpixelLocation, halfpixel);
        m_roundedOnscreenPass.shader->setUniform(m_roundedOnscreenPass.offsetLocation, float(m_offset));
        m_roundedOnscreenPass.shader->setUniform(m_roundedOnscreenPass.boxLocation, QVector4D(nativeBox.horizontalCenter(), nativeBox.verticalCenter(), nativeBox.width() * 0.5, nativeBox.height() * 0.5));
        m_roundedOnscreenPass.shader->setUniform(m_roundedOnscreenPass.cornerRadiusLocation, nativeCornerRadius.toVector());
        m_roundedOnscreenPass.shader->setUniform(m_roundedOnscreenPass.opacityLocation, modulation);

        read->colorAttachment()->bind();

        glEnable(GL_BLEND);
        glBlendFunc(GL_ONE, GL_ONE_MINUS_SRC_ALPHA);

        vbo->draw(GL_TRIANGLES, 6, vertexCount);

        glDisable(GL_BLEND);

        ShaderManager::instance()->popShader();
    } else {
        ShaderManager::instance()->pushShader(m_onscreenPass.shader.get());

        QMatrix4x4 projectionMatrix = viewport.projectionMatrix();
        projectionMatrix.translate(scaledBackgroundRect.x(), scaledBackgroundRect.y());

        GLFramebuffer::popFramebuffer();
        const auto &read = renderInfo.framebuffers[1];

        const QVector2D halfpixel(0.5 / read->colorAttachment()->width(),
                                  0.5 / read->colorAttachment()->height());

        m_onscreenPass.shader->setUniform(m_onscreenPass.mvpMatrixLocation, projectionMatrix);
        m_onscreenPass.shader->setUniform(m_onscreenPass.halfpixelLocation, halfpixel);
        m_onscreenPass.shader->setUniform(m_onscreenPass.offsetLocation, float(m_offset));

        read->colorAttachment()->bind();

        if (modulation < 1.0) {
            glEnable(GL_BLEND);
            glBlendColor(0, 0, 0, modulation);
            glBlendFunc(GL_CONSTANT_ALPHA, GL_ONE_MINUS_CONSTANT_ALPHA);
        }

        vbo->draw(GL_TRIANGLES, 6, vertexCount);

        if (modulation < 1.0) {
            glDisable(GL_BLEND);
        }

        ShaderManager::instance()->popShader();
    }

    vbo->unbindArrays();
}

bool ZashterminalBlurEffect::isActive() const
{
    return m_valid && !m_windows.empty() && !effects->isScreenLocked();
}

bool ZashterminalBlurEffect::blocksDirectScanout() const
{
    return false;
}

} // namespace KWin

#include "moc_blur.cpp"
