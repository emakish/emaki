# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Check complete refusal messages and exact localized snapshot lower bounds."""
import gettext
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'update-manager'))
import storage_arch as storage


class DisplayMessages(unittest.TestCase):
    def usage(self, amount):
        result = subprocess.CompletedProcess([], 0, 'fixture', '')
        with patch.object(storage, 'snapshot_paths', return_value=['snapshot']), \
                patch.object(storage, 'quota_status', return_value='generation'), \
                patch.object(storage, 'subvolume_id', side_effect=lambda path: '0/1' if path == '/' else '0/2'), \
                patch.object(storage, 'qgroup_inventory', return_value={'0/2': {'bytes': amount, 'path': 'snapshot'}}), \
                patch.object(storage, 'aggregate_usage', return_value=None), \
                patch.object(storage.subprocess, 'run', return_value=result):
            return storage.snapshot_usage()

    def test_snapshot_bound_uses_an_exact_formatted_size(self):
        with patch.object(storage, '_', side_effect=lambda text: text), \
                patch.dict(os.environ, EMAKI_LANGUAGE='en'):
            for amount, size in [(1, '1 B'), (5368709120, '5,368,709,120 B')]:
                with self.subTest(amount=amount):
                    answer = self.usage(amount)
                    self.assertIsNone(answer['bytes'])
                    self.assertEqual(answer['exclusive_bytes'], amount)
                    self.assertEqual(answer['reason'], 'Individual snapshots hold at least ' + size
                                     + ' of exclusive space; space shared between snapshots is unavailable.')

    def test_snapshot_size_uses_compiled_translations(self):
        expected = {'es': '5.368.709.120 B', 'pt_BR': '5.368.709.120 B',
                    'de': '5.368.709.120 B', 'fr': '5\u202f368\u202f709\u202f120\u00a0octets',
                    'ru': '5\u00a0368\u00a0709\u00a0120 Б'}
        with tempfile.TemporaryDirectory(prefix='emaki-storage-messages-') as temporary:
            compiled = Path(temporary) / 'programs.mo'
            for language, size in expected.items():
                with self.subTest(language=language):
                    subprocess.run(['msgfmt', '-o', str(compiled), str(ROOT / 'i18n/programs' / (language + '.po'))], check=True)
                    with compiled.open('rb') as stream:
                        translator = gettext.GNUTranslations(stream)
                    with patch.object(storage, '_', translator.gettext), \
                            patch.dict(os.environ, EMAKI_LANGUAGE=language):
                        answer = self.usage(5368709120)
                    self.assertIn(size, answer['reason'])
                    self.assertNotIn('Individual snapshots', answer['reason'])
                    self.assertEqual(answer['exclusive_bytes'], 5368709120)

    def test_undo_requests_complete_numerus_message(self):
        script = r'''
const fs = require('fs'); const vm = require('vm');
const source = fs.readFileSync(process.argv[1], 'utf8').split('\n').filter(line => !/^\s*\.(pragma|import)\b/.test(line)).join('\n');
const calls = [];
const context = {qsTranslate: (ctx, text, hint, count) => { calls.push({ctx, text, count}); return text; }};
vm.createContext(context);
vm.runInContext('String.prototype.arg = function(value) { return this.replace(/%1/g, () => value); };\n' + source, context);
const rows = [1, 2, 5, 21].map(count => {
    const packages = Array.from({length: count}, (_, i) => 'package' + i);
    const text = context.undoRefusal({code: 'cannot-undo', requiredBy: packages});
    return {count, text, source: calls[calls.length - 1], names: packages.join(', ')};
});
process.stdout.write(JSON.stringify(rows));
'''
        result = subprocess.run(['node', '-e', script, str(ROOT / 'update-manager/ui/ManagerLogic.js')],
                                capture_output=True, text=True, check=True)
        for row in json.loads(result.stdout):
            with self.subTest(count=row['count']):
                expected = 'This install can no longer be taken back as it was. %1 ' + ('needs' if row['count'] == 1 else 'need') + ' what it added.'
                self.assertEqual(row['source'], {
                    'ctx': 'ManagerLogic', 'count': row['count'],
                    'text': 'This install can no longer be taken back as it was. %1 need(s) what it added.'})
                self.assertEqual(row['text'], expected.replace('%1', row['names']))


if __name__ == '__main__':
    unittest.main()
