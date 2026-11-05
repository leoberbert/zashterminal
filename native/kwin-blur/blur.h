/*
    SPDX-FileCopyrightText: 2010 Fredrik Höglund <fredrik@kde.org>
    SPDX-FileCopyrightText: 2018 Alex Nemeth <alex.nemeth329@gmail.com>
    SPDX-FileCopyrightText: 2026 Zashterminal contributors

    SPDX-License-Identifier: GPL-2.0-or-later

    Dual Kawase renderer adapted from KDE KWin v6.8.2:
    https://invent.kde.org/plasma/kwin/-/tree/v6.8.2/src/plugins/blur
    The global configuration, protocol advertisement, noise and automatic
    all-window selection are intentionally not part of this effect.
*/
#pragma once

#include "effect/effect.h"
#include "opengl/glutils.h"
#include "scene/item.h"

#include <QDBusContext>
#include <QDBusServiceWatcher>
#include <QList>

#include <map>
#include <optional>
#include <unordered_map>
#include <vector>

namespace KWin
{

class BackgroundEffectItem;
using BlurWindowKey = std::pair<uint, uint>;

struct ZashBlurRenderData
{
    // Keep the unblurred backdrop cached between partial scene repaints.
    std::vector<std::unique_ptr<GLTexture>> textures;
    std::vector<std::unique_ptr<GLFramebuffer>> framebuffers;
    // Reuse clipping storage instead of allocating it on every frame.
    QList<RectF> effectiveShape;
};

struct ZashBlurWindowData
{
    BlurWindowKey key;
    uint strength = 0;
    std::unordered_map<RenderView *, ZashBlurRenderData> render;
    std::unique_ptr<BackgroundEffectItem> blurItem;
};

class ZashterminalBlurEffect : public Effect, protected QDBusContext
{
    Q_OBJECT
    Q_CLASSINFO("D-Bus Interface", "org.leoberbert.ZashterminalBlur")

public:
    ZashterminalBlurEffect();
    ~ZashterminalBlurEffect() override;

    static bool supported();
    static bool enabledByDefault() { return false; }
    void prePaintScreen(ScreenPrePaintData &data) override;
    bool drawWindow(const RenderTarget &renderTarget, const RenderViewport &viewport, EffectWindow *w, int mask, const Region &deviceRegion, WindowPaintData &data) override;
    bool isActive() const override;
    bool blocksDirectScanout() const override;
    int requestedEffectChainPosition() const override { return 21; }

public Q_SLOTS:
    Q_SCRIPTABLE uint protocolVersion() const;
    Q_SCRIPTABLE bool setWindowBlur(uint pid, qulonglong surfaceId, uint strength);

private:
    void slotWindowAdded(EffectWindow *w);
    void slotWindowDeleted(EffectWindow *w);
    void slotViewRemoved(RenderView *view);
    void removeOwner(const QString &owner);
    void applyRequest(EffectWindow *w);
    void updateGeometry(EffectWindow *w);
    void removeWindow(EffectWindow *w);
    bool shouldBlur(const EffectWindow *w, int mask, const WindowPaintData &data) const;
    void blur(const RenderTarget &renderTarget, const RenderViewport &viewport, EffectWindow *w, int mask, const Region &deviceRegion, WindowPaintData &data);

    struct OnscreenPass
    {
        std::unique_ptr<GLShader> shader;
        int mvpMatrixLocation;
        int offsetLocation;
        int halfpixelLocation;
    } m_onscreenPass;
    struct
    {
        std::unique_ptr<GLShader> shader;
        int mvpMatrixLocation;
        int offsetLocation;
        int halfpixelLocation;
        int boxLocation;
        int cornerRadiusLocation;
        int opacityLocation;
    } m_roundedOnscreenPass;
    OnscreenPass m_downsamplePass;
    OnscreenPass m_upsamplePass;

    struct Request
    {
        uint strength;
        QString owner;
    };
    std::map<BlurWindowKey, Request> m_requests;
    std::unordered_map<EffectWindow *, ZashBlurWindowData> m_windows;
    std::unordered_map<EffectWindow *, QList<QMetaObject::Connection>> m_connections;
    std::unordered_map<EffectWindow *, BlurWindowKey> m_windowKeys;
    QDBusServiceWatcher m_ownerWatcher;
    RenderView *m_currentView = nullptr;
    bool m_valid = false;
    bool m_registered = false;
};

} // namespace KWin
