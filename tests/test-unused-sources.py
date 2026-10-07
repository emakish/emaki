#!/usr/bin/env python3
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Offline checks for the exact unused wiki exception."""
import copy
from pathlib import Path
import subprocess
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'packaging/mirror'))
import unused_sources as unused

RECIPE = b'# Maintainer: Levente Polyak <anthraxx[at]archlinux[dot]org>\n# Contributor: Dave Reisner <dreisner@archlinux.org>\n# Contributor: Thomas Baechler <thomas@archlinux.org>\n\npkgname=vpnc\n_vpnc_commit=95f08ac4434d59752f2447a2c568b325df4c5903\n_vpncscripts_commit=ce9e961bd0f6b867e1c7c35f78f6fb973f6ff101\npkgver=0.5.3.r557.r241\npkgrel=1\nepoch=1\npkgdesc=\'VPN client for cisco3000 VPN Concentrators\'\nurl=\'https://github.com/streambinder/vpnc\'\narch=(x86_64)\nlicense=(GPL-2.0-only)\ndepends=(\n  bash\n  glibc\n  iproute2\n  libgcrypt\n  openssl\n  perl\n  which\n)\nmakedepends=(\n  git\n)\noptdepends=(\n  \'openresolv: Let vpnc manage resolv.conf\'\n)\nbackup=(\n  etc/vpnc/default.conf\n)\nsource=(\n  "vpnc::git+https://github.com/streambinder/vpnc#commit=${_vpnc_commit}"\n  "vpnc-scripts::git+https://gitlab.com/openconnect/vpnc-scripts.git#commit=${_vpncscripts_commit}"\n  git+https://github.com/streambinder/vpnc.wiki.git\n  vpnc.conf\n  vpnc@.service\n)\nsha512sums=(\'SKIP\'\n            \'SKIP\'\n            \'SKIP\'\n            \'ac70712192c01ff638a9badc5cff7105bee5c4fed5d3a3b728e9597661952d156041c82fe1e544e2bab602d193d4105d3689c79c46d964623f6ce38dd89f0ea7\'\n            \'cafcab676986c1a2e49441f01d61997f1c6b54bbb68661b9af007d4816f8e76eee6b7ac2dfab55b55965fa407e8331c663cf11aa79384c30b0c9049c1477b791\')\n\npkgver() {\n  cd ${pkgname}\n  printf "%s.r%s.r%s" "$(grep \'^VERSION\' Makefile|sed \'s|VERSION := ||\')" \\\n    "$(git -C ../vpnc rev-list --count HEAD)" \\\n    "$(git -C ../vpnc-scripts rev-list --count HEAD)"\n}\n\nprepare() {\n  cd ${pkgname}\n\n  git submodule init\n  git config submodule."src/doc".url "${srcdir}/vpnc.wiki"\n  git -c protocol.file.allow=always submodule update --recursive\n\n  # Build hybrid support\n  sed \'s|^#OPENSSL|OPENSSL|g\' -i Makefile\n  # fix resolvconf location for community/openresolv\n  sed \'s|/sbin/resolvconf|/usr/bin/resolvconf|g\' -i ../vpnc-scripts/vpnc-script\n  ln -sf ../../vpnc-scripts/vpnc-script src\n  ln -sf ../../vpnc.conf src\n}\n\nbuild() {\n  make -C ${pkgname}\n}\n\ncheck() {\n  make -C ${pkgname} test\n}\n\npackage() {\n  cd ${pkgname}\n  install -d "${pkgdir}/usr/share/doc/${pkgname}"\n  make DESTDIR="${pkgdir}" PREFIX=/usr SBINDIR=/usr/bin install\n  install -Dm 755 ../vpnc-scripts/vpnc-script -t "${pkgdir}/etc/vpnc"\n  install -Dm 644 ../vpnc@.service -t "${pkgdir}/usr/lib/systemd/system"\n  install -Dm 644 .github/README.md -t "${pkgdir}/usr/share/doc/${pkgname}"\n  install -Dm 644 LICENSE -t "${pkgdir}/usr/share/licenses/${pkgname}"\n}\n\n# vim: ts=2 sw=2 et:\n'


class UnusedSources(unittest.TestCase):
    def setUp(self):
        self.record = dict(base='vpnc', name='vpnc', version='1:0.5.3.r557.r241-1',
                           recipe_sha256=unused.RECIPE_SHA256,
                           binary_sha256=unused.BINARY_SHA256)
        self.info = 'pkgbase = vpnc\n'
        sources = [unused.EVIDENCE['primary_source'],
                   'vpnc-scripts::git+https://gitlab.com/openconnect/vpnc-scripts.git#commit=ce9e961bd0f6b867e1c7c35f78f6fb973f6ff101',
                   unused.WIKI, 'vpnc.conf', 'vpnc@.service']
        self.info += ''.join('\tsource = ' + value + '\n' for value in sources)
        self.info += ''.join('\tsha512sums = ' + value + '\n'
                             for value in ['SKIP', 'SKIP', 'SKIP', 'abc', 'def'])

    def test_only_wiki_and_its_checksum_removed(self):
        effective, info, evidence = unused.effective_recipe(self.record, RECIPE, self.info)
        self.assertTrue(effective.startswith(RECIPE))
        self.assertNotIn(unused.WIKI, info)
        self.assertEqual(info.count('sha512sums = SKIP'), 2)
        self.assertEqual(info.count('source = '), 4)
        self.assertIn('sha512sums = abc', info)
        unused.validate_evidence(self.record, evidence)
        result = subprocess.run(['bash', '-c', effective.decode() +
                                 '\nprintf "%s\n" "${source[@]}"; '
                                 'printf "%s\n" "${sha512sums[@]}"'],
                                check=True, text=True, capture_output=True)
        self.assertEqual(len(result.stdout.splitlines()), 8)
        self.assertNotIn(unused.WIKI, result.stdout)

    def test_changed_recipe_bytes_refused(self):
        with self.assertRaisesRegex(ValueError, 'recipe bytes'):
            unused.effective_recipe(self.record, RECIPE + b'\n', self.info)

    def test_other_recipe_has_no_exception(self):
        self.record['recipe_sha256'] = '0' * 64
        self.assertEqual(unused.effective_recipe(self.record, RECIPE, self.info),
                         (RECIPE, self.info, []))

    def test_changed_binary_refused(self):
        for field in ['name', 'base', 'version', 'binary_sha256']:
            record = dict(self.record, **{field: 'changed'})
            with self.assertRaisesRegex(ValueError, 'binary'):
                unused.filtered_srcinfo(record, self.info)

    def test_changed_metadata_refused(self):
        for info in [self.info.replace(unused.WIKI, unused.WIKI + '#branch=main'),
                     self.info.replace('sha512sums = SKIP', 'sha512sums = 123', 1),
                     self.info + '\tsource_x86_64 = surprise\n']:
            with self.assertRaises(ValueError):
                unused.filtered_srcinfo(self.record, info)

    def test_missing_or_tampered_evidence_refused(self):
        bad = copy.deepcopy([unused.EVIDENCE])
        bad[0]['primary_commit'] = '0' * 40
        for evidence in [[], bad]:
            with self.assertRaises(ValueError):
                unused.validate_evidence(self.record, evidence)


if __name__ == '__main__':
    unittest.main()
