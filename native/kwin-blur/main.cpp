/*
    SPDX-FileCopyrightText: 2021 Vlad Zahorodnii <vlad.zahorodnii@kde.org>
    SPDX-FileCopyrightText: 2026 Zashterminal contributors

    SPDX-License-Identifier: GPL-2.0-or-later
*/

#include "blur.h"

namespace KWin
{

KWIN_EFFECT_FACTORY_SUPPORTED_ENABLED(ZashterminalBlurEffect,
                                      "metadata.json",
                                      return ZashterminalBlurEffect::supported();
                                      ,
                                      return ZashterminalBlurEffect::enabledByDefault();)

} // namespace KWin

#include "main.moc"
