#!/usr/bin/env python3
"""Honest status words (plans/eyes-acceptance.md 5.5): status documents and release gates report
a check only in the vocabulary of tests/vm/status-words.toml.

  test-status-words.py           check this tree, then test the checker on planted lines
  test-status-words.py FILE...   check FILE (a night journal, release notes) as a status document

Documents (STATUS.md, ROADMAP.md, acceptance sheets): in a checked line a claim word (verified,
works, OK, ...) stands only next to a positive phrase with its evidence, and a word that is never
alone (done, passed, ...) only with a phrase or evidence in its paragraph; "script passed", "seen
in VM" and "seen on hardware by" always carry their evidence. Blocks dated before the list's
`since` are history and are not checked. Gates: every printed RESULT line is an allowed result
line, every SHOT line says it was not judged. The public export has no status documents: they
are reported as SKIPPED there, and the gates are still checked.
"""
import datetime
import glob
import re
import shutil
import sys
import tempfile
import tomllib
import unittest
from pathlib import Path

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parent.parent
LIST = 'tests/vm/status-words.toml'
DATE = re.compile(r'(?<![\d.])(?:(\d{4})-(\d{2})-(\d{2})|(\d{1,2})\.(\d{2})(?:\.(\d{4}))?)(?![\d-])')
# Spans that claim nothing: `code`, "quoted words", link targets.
QUIET = re.compile(r'`[^`]*`|"[^"\n]*"|“[^”\n]*”|«[^»\n]*»|\]\([^)\s]*\)')
PRINTED = re.compile(r'(^|[\s;({&|])(echo|printf|log)\s|\b(print|out)\(')
RESULT = re.compile(r'RESULT:[^\'"\n]*')
SHOT = re.compile(r'SHOT:[^\'"\n]*')


def first_date(text):
    for match in DATE.finditer(text):
        try:
            if match.group(1):
                return datetime.date(*map(int, match.group(1, 2, 3)))
            return datetime.date(int(match.group(6) or 2026), int(match.group(5)), int(match.group(4)))
        except ValueError:
            continue
    return None


def blocks(lines):
    """(line number, line, date or None, paragraph text) for every line of a status document."""
    entry = para = None
    fence = False
    headings = []  # (level, date): a heading without a date takes the date of the heading above it
    rows, paragraph, paragraphs = [], [], []
    for number, line in enumerate(lines, 1):
        text = line.strip()
        if text.startswith('```'):
            fence = not fence
        heading = text.startswith('#') and not fence
        lead = re.match(r'(\*\*|\*)(?!\s)(.+?)(?:\1|$)', text)
        starts = not text or heading or re.match(r'([-*+]|\d+\.)\s', text) or text.startswith('**')
        if starts and paragraph:
            paragraphs.append(paragraph)
            paragraph = []
        if not text:
            para = None
            continue
        if heading:
            level = len(text) - len(text.lstrip('#'))
            while headings and headings[-1][0] >= level:
                headings.pop()
            entry, para = first_date(text) or (headings[-1][1] if headings else None), None
            headings.append((level, entry))
        elif lead and lead.group(1) == '**' and first_date(lead.group(2)):
            entry, para = first_date(lead.group(2)), None
        elif lead and lead.group(1) == '*' and first_date(lead.group(2)):
            para = first_date(lead.group(2))
        elif starts:
            para = None
        row = [number, line, para or entry, None]
        paragraph.append(row)
        rows.append(row)
    if paragraph:
        paragraphs.append(paragraph)
    for paragraph in paragraphs:
        joined = '\n'.join(row[1] for row in paragraph)
        for row in paragraph:
            row[3] = joined
    return rows


class Vocabulary:
    def __init__(self, data):
        self.since = data['since']
        flags = re.IGNORECASE
        word = lambda w: re.compile(r'(?<![\w-])' + re.escape(w) + r'(?![\w-])', flags)
        self.claims = [(w, word(w)) for w in data['claim_words']]
        self.alone = [(w, word(w)) for w in data['never_alone']]
        self.negation = re.compile(r'\b(' + '|'.join(map(re.escape, data['negations'])) + r')\W+(\w+\W+){0,2}$', flags)
        self.alone_evidence = [re.compile(e) for e in data['alone_evidence']]
        self.idioms = [re.compile(i, flags) for i in data['idioms']]
        self.phrases = [(p['text'], re.compile(r'(?<![\w-])' + re.escape(p['text']) + r'(?![\w-])', flags),
                         [re.compile(e) for e in p.get('evidence', [])]) for p in data['phrase']]
        self.documents = data['documents']
        self.gate = data['gate']
        self.results = [re.compile(r) for r in self.gate['result_lines']]
        self.count = dict.fromkeys(('documents', 'checked lines', 'gate files', 'result lines', 'shot lines'), 0)

    def line(self, raw, paragraph):
        """Messages of one checked line; PARAGRAPH (the line's paragraph) is searched for evidence."""
        quiet = lambda s: QUIET.sub(lambda m: ' ' * len(m.group(0)), s)
        text = quiet(raw)
        found = []
        positive = False  # a positive phrase with its evidence on this line
        for name, pattern, evidence in self.phrases:
            for match in pattern.finditer(text):
                missing = [e.pattern for e in evidence if not e.search(paragraph)]
                if missing:
                    found.append(f'"{name}" without its evidence ({", ".join(missing)})')
                elif evidence:
                    positive = True
        backed = (any(p.search(quiet(paragraph)) for _, p, _ in self.phrases)
                  or any(e.search(paragraph) for e in self.alone_evidence))
        idioms = [m.span() for i in self.idioms for m in i.finditer(text)]
        use = 'write script passed / seen in VM / seen on hardware by / failed / not tested / not applicable'
        for words, alone in ((self.claims, False), (self.alone, True)):
            for _, pattern in words:
                for match in pattern.finditer(text):
                    if self.negation.search(text[:match.start()]):
                        continue
                    if not alone and not positive:
                        found.append(f'"{match.group(0)}" is a claim word: {use} with its evidence')
                    elif alone and not backed and not any(a <= match.start() and match.end() <= b for a, b in idioms):
                        found.append(f'"{match.group(0)}" stands alone: {use}, or name the command, log, '
                                     'commit or frame in the same paragraph')
        return found

    def document(self, path, label, undated):
        """Findings (label, line number, line, message) of one status document."""
        findings = []
        self.count['documents'] += 1
        for number, line, date, paragraph in blocks(path.read_text(encoding='utf-8').splitlines()):
            checked = undated == 'check' if date is None else date >= self.since
            if checked:
                self.count['checked lines'] += 1
                findings += [(label, number, line, message) for message in self.line(line, paragraph)]
        return findings

    def gate_file(self, path, label):
        findings = []
        self.count['gate files'] += 1
        for number, line in enumerate(path.read_text(encoding='utf-8').splitlines(), 1):
            if line.lstrip().startswith('#'):
                continue
            if PRINTED.search(line):
                for match in RESULT.finditer(line):
                    self.count['result lines'] += 1
                    text = match.group(0).rstrip()
                    if not any(r.search(text) for r in self.results):
                        findings.append((label, number, line, f'result line {text!r} is not one of the allowed result lines'))
            for match in SHOT.finditer(line):
                self.count['shot lines'] += 1
                if self.gate['shot_suffix'] not in match.group(0):
                    findings.append((label, number, line, f'SHOT line without "{self.gate["shot_suffix"]}"'))
        return findings


def check_tree(root, out=print):
    """All findings of the tree at ROOT; SKIPPED lines and the counts go to OUT."""
    vocabulary = Vocabulary(tomllib.loads((root / LIST).read_text(encoding='utf-8')))
    private = (root / 'scripts/make-public.sh').is_file()
    findings = []
    for pattern, undated in vocabulary.documents.items():
        paths = sorted(Path(p) for p in glob.glob(str(root / pattern)))
        if not paths and not glob.has_magic(pattern):
            if private:
                findings.append((pattern, 0, '', 'status document missing'))
            else:
                out(f'SKIPPED: {pattern}: a private status document, not in the public export')
        for path in paths:
            findings += vocabulary.document(path, str(path.relative_to(root)), undated)
    for name in vocabulary.gate['required']:
        if not (root / name).is_file():
            findings.append((name, 0, '', 'release gate missing'))
    gates = sorted({Path(p) for g in vocabulary.gate['files'] for p in glob.glob(str(root / g))})
    for path in gates:
        findings += vocabulary.gate_file(path, str(path.relative_to(root)))
    out('checked: ' + ', '.join(f'{n} {k}' for k, n in vocabulary.count.items()))
    return findings


def report(findings, out=print):
    for label, number, line, message in findings:
        out(f'{label}:{number}: {message}' + (f'\n    {line.strip()}' if line else ''))


# ---- tests of the checker on planted lines ----------------------------------------------------
STATUS = '''# Where we are now

**2026-10-04 — the night before the list.**
- encrypted install: verified on the test laptop, everything OK.

**2026-10-06 — after the list.**
- erase-btrfs: script passed (`tests/vm/iso-install.sh erase-btrfs`, log `gate/accept.log`).
- installer pages: seen in VM, sheet `walk/walk.toml`, ISO 0123456789ab, 2026-10-06.
- hibernation: not tested (hardware line H-M11); the update is not verified yet.
- lid close: seen on hardware by the owner, test laptop, 2026-10-06, ISO 0123456789ab.

## In short

The "it's fine" note is his words. Closure: **Done when:** the image boots.
'''

GATE = '''#!/bin/bash
# RESULT: OK in a comment is not printed
echo "RESULT: FAILED at $job"
echo 'RESULT: SCRIPTS PASSED'
print(f'SHOT: {path} (not judged)')
'''


class Checker(unittest.TestCase):
    def tree(self, files, gate=GATE, private=True):
        tmp = tempfile.TemporaryDirectory(prefix='status-words-test.')
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        (root / 'tests/vm/eyes').mkdir(parents=True)
        shutil.copy2(ROOT / LIST, root / LIST)
        for name in ('tests/vm/release-gate.sh', 'tests/vm/eyes/eyes-gate.py'):
            (root / name).write_text(gate)
        if private:
            (root / 'scripts').mkdir()
            (root / 'scripts/make-public.sh').write_text('')
            files = {'ROADMAP.md': '# Plan\n', **files}
        for name, text in files.items():
            (root / name).parent.mkdir(parents=True, exist_ok=True)
            (root / name).write_text(text)
        lines = []
        return check_tree(root, out=lines.append), lines

    def messages(self, findings):
        return [f'{f[0]}:{f[1]}: {f[3]}' for f in findings]

    def test_allowed_forms_and_history_pass(self):
        findings, _ = self.tree({'STATUS.md': STATUS})
        self.assertEqual(self.messages(findings), [])

    def test_a_planted_claim_fails(self):
        for line in ('- encrypted install: verified on the test laptop.', '- the lock works.', '- everything OK.',
                     '- проверено.', '- the walk is done.', '- install passed.', '- a clean run.',
                     '- accepted by the owner.', '- erase-btrfs verified (`tests/vm/iso-install.sh`, log `a.log`).'):
            with self.subTest(line=line):
                findings, _ = self.tree({'STATUS.md': STATUS + line + '\n'})
                self.assertEqual(len(findings), 1, self.messages(findings))
                self.assertEqual(findings[0][1], STATUS.count('\n') + 1)

    def test_words_with_their_evidence_pass(self):
        # A claim word only on a line with a positive phrase and its evidence (plan step 11); a word
        # that is never alone with a command, commit or log in its paragraph (plan 5.5).
        for line in ('- erase-btrfs: script passed (`tests/vm/iso-install.sh`, log `a.log`); the install is verified.',
                     '- the first two items are done: patch 0001 of `packaging/quickshell-emaki`.',
                     '- script failed: iso-encrypt-check rc=1 at resume; the unlock assertions passed before it.'):
            with self.subTest(line=line):
                findings, _ = self.tree({'STATUS.md': STATUS + line + '\n'})
                self.assertEqual(self.messages(findings), [])

    def test_an_update_paragraph_is_checked_inside_an_old_entry(self):
        old = '**2026-10-04 — old.**\nverified then.\n*Update 2026-10-05:* verified now.\n\nverified then.\n'
        findings, _ = self.tree({'STATUS.md': old})
        self.assertEqual([f[1] for f in findings], [3])

    def test_a_phrase_without_its_evidence_fails(self):
        for line in ('- erase-btrfs: script passed.', '- installer: seen in VM.',
                     '- lid: seen on hardware by the owner, test laptop.'):
            with self.subTest(line=line):
                findings, _ = self.tree({'STATUS.md': STATUS + '\n' + line + '\n'})
                self.assertEqual(len(findings), 1, self.messages(findings))
                self.assertIn('without its evidence', findings[0][3])

    def test_negations_quotes_and_code_claim_nothing(self):
        text = STATUS + 'Not verified yet; nobody knows whether it works; `clean`; "OK" on the button.\n'
        findings, _ = self.tree({'STATUS.md': text})
        self.assertEqual(self.messages(findings), [])

    def test_roadmap_checks_only_its_dated_status_lines(self):
        roadmap = ('**C4. Theme.** It works in Firefox.\n**Done when:** Firefox opens in the palette.\n'
                   '*Closed 2026-09-17:* accepted.\n\n*Update 2026-10-06:* the theme works.\n')
        findings, _ = self.tree({'STATUS.md': STATUS, 'ROADMAP.md': roadmap})
        self.assertEqual([(f[0], f[1]) for f in findings], [('ROADMAP.md', 5)])

    def test_a_journal_section_takes_the_date_of_its_night(self):
        tmp = tempfile.TemporaryDirectory(prefix='status-words-test.')
        self.addCleanup(tmp.cleanup)
        journal = Path(tmp.name) / 'JOURNAL.md'
        journal.write_text('# Night 2026-10-02 → 03\n## State\nall OK.\n# Night 2026-10-05 → 06\n## State\nall OK.\n')
        vocabulary = Vocabulary(tomllib.loads((ROOT / LIST).read_text(encoding='utf-8')))
        self.assertEqual([f[1] for f in vocabulary.document(journal, 'JOURNAL.md', 'check')], [6])

    def test_acceptance_sheets_are_checked(self):
        findings, _ = self.tree({'STATUS.md': STATUS, 'docs/history/acceptance-0.2.0.md': 'Emaki 0.2.0: verified.\n'})
        self.assertEqual([f[0] for f in findings], ['docs/history/acceptance-0.2.0.md'])

    def test_gate_result_and_shot_lines(self):
        for line, bad in (('echo "RESULT: PASSED"', True), ('echo "RESULT: OK"', True),
                          ("print(f'SHOT: {p}')", True), ('log "RESULT: SCRIPTS PASSED: $OUT"', False),
                          ("out(f'RESULT: PASSED (walk record of {sha[:12]})')", False),
                          ('echo "RESULT: NOT TESTED at release-walk"', False)):
            with self.subTest(line=line):
                findings, _ = self.tree({'STATUS.md': STATUS}, gate=GATE + line + '\n')
                self.assertEqual(len(findings), 2 if bad else 0, self.messages(findings))

    def test_the_public_export_skips_the_status_documents_visibly(self):
        findings, lines = self.tree({}, private=False)
        self.assertEqual(self.messages(findings), [])
        self.assertIn('SKIPPED: STATUS.md: a private status document, not in the public export', lines)

    def test_a_private_tree_without_status_fails(self):
        findings, _ = self.tree({})
        self.assertIn('status document missing', [f[3] for f in findings])


def main(argv):
    if argv:
        vocabulary = Vocabulary(tomllib.loads((ROOT / LIST).read_text(encoding='utf-8')))
        findings = [f for name in argv for f in vocabulary.document(Path(name), name, 'check')]
        report(findings)
        print(f'{len(findings)} findings in {len(argv)} files (vocabulary since {vocabulary.since})')
        return 1 if findings else 0
    findings = check_tree(ROOT)
    report(findings)
    if findings:
        print(f'FAILED: {len(findings)} findings against the status vocabulary of {LIST}')
        return 1
    print(f'status words: the tree follows {LIST}', flush=True)
    result = unittest.main(argv=[sys.argv[0]], exit=False, verbosity=1).result
    return 0 if result.wasSuccessful() else 1


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
