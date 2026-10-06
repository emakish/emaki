// SPDX-License-Identifier: GPL-3.0-or-later
#include <QApplication>
#include <QTreeView>
#include <KOpenWithDialog>
#include <KApplicationTrader>
#include <KColorScheme>
#include <KServiceGroup>
#include <iostream>

int main(int argc, char **argv) {
    QApplication app(argc, argv);
    const auto root = KServiceGroup::root();
    if (!root || root->entries().isEmpty()) return 1;
    const auto folder = KApplicationTrader::preferredService(QStringLiteral("inode/directory"));
    const auto pdf = KApplicationTrader::preferredService(QStringLiteral("application/pdf"));
    if (!folder || folder->storageId() != QStringLiteral("org.kde.dolphin.desktop")) return 2;
    if (!pdf || pdf->storageId() != QString::fromLocal8Bit(argv[1])) return 3;
    const auto png = KApplicationTrader::preferredService(QStringLiteral("image/png"));
    if (!png || png->storageId() != QString::fromLocal8Bit(argv[4])) return 7;
    KOpenWithDialog dialog(QStringLiteral("application/pdf"), QString());
    bool populated = false;
    for (auto *tree : dialog.findChildren<QTreeView *>()) {
        if (tree->model() && tree->model()->rowCount() > 0) populated = true;
    }
    if (!populated) return 6;
    KColorScheme scheme;
    if (scheme.background().color().name() != QString::fromLocal8Bit(argv[2])) return 4;
    if (scheme.foreground().color().name() != QString::fromLocal8Bit(argv[3])) return 5;
    std::cout << "PASS: KDE Open With dialog, Dolphin association, PDF/PNG choices and token palette\n";
}
