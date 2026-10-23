// Copyright (C) 2026 Artur Yakymenko
// SPDX-License-Identifier: GPL-3.0-or-later

function numberLocale() {
    const language = Qt.uiLanguage || "en";
    return language.startsWith("es") ? Qt.locale("es_ES")
        : (language === "en" || language === "en_XA" || language === "C") ? Qt.locale("en_US")
        : Qt.locale(language);
}
