// Copyright (C) 2026 Artur Yakymenko
// SPDX-License-Identifier: GPL-3.0-or-later
// An installed AUR program keeps its AUR identity on Installed and on its page: the real inventory
// formatter and AUR record parser feed the real window logic (ManagerLogic.js).
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';
import {spawnSync} from 'node:child_process';
import {fileURLToPath} from 'node:url';

const managerDir = fileURLToPath(new URL('../update-manager/', import.meta.url));
// QML JavaScript directives are not JavaScript; drop them in memory.
const source = fs.readFileSync(new URL('ui/ManagerLogic.js', `file://${managerDir}`), 'utf8')
    .split('\n').filter(line => !/^\s*\.(pragma|import)\b/.test(line)).join('\n');
const Logic = {};
vm.runInNewContext(`String.prototype.arg = function(value) { return this.replace(/%1/g, () => String(value)); };\n` + source + '\nObject.assign(Logic, {installedRows, merge, edition, identity, consolidate, removalStatus, updateProgress, progressEvent, aurBuilds, undoRefusal, downloadText});',
                   {Logic, Qt: {}, qsTranslate: (context, text, hint, count) => text.replace("need(s)", count === 1 ? "needs" : "need")});

const python = `
import json, sys
sys.path.insert(0, sys.argv[1])
import programs_catalog as catalog, programs_aur as aur
path = '/applications/paint.desktop'
owned = catalog.owners(path + ' is owned by paint-bin 3-1')
details = catalog.info('Name            : paint-bin\\nRequired By     : None\\nOptional For    : None\\n')
apps = [dict(id='paint.desktop', name='Paint', filename=path)]
installed = catalog.programs(apps, owned, details, foreign={'paint-bin'})
row = aur.records(dict(version=5, type='multiinfo', results=[dict(
    Name='paint-bin', PackageBase='paint-bin', Version='3-1', Description='Draw', NumVotes=12,
    Popularity=0.5, Maintainer='someone', LastModified=1700000000, FirstSubmitted=1600000000, OutOfDate=None)]),
    {'paint-bin': '3-1'})[0]
row['actions'] = row['actions'] + ['undo']
print(json.dumps(dict(installed=installed, aur=row)))
`;
const run = spawnSync('python3', ['-IB', '-c', python, managerDir], {encoding: 'utf8'});
assert.equal(run.status, 0, run.stderr);
const {installed: inventory, aur} = JSON.parse(run.stdout);
assert.equal(inventory.length, 1);
assert.equal(inventory[0].foreign, true, 'the inventory marks a package no repository has');

const facts = ['votes', 'popularity', 'lastModified', 'outOfDate', 'maintainer', 'orphan', 'firstSubmitted',
               'page', 'recipe'];

// (a) Foreign inventory alone does not prove an AUR source.
{
    const [entry] = Logic.installedRows([], inventory);
    assert.equal(entry.kind, 'arch');
    assert.equal(entry.sourceLabel, 'Installed outside the store');
    assert.equal(entry.key, 'legacy:paint-bin');
    assert.deepEqual(Array.from(entry.actions), ['remove']);
    assert.equal(entry.undoKey, '');
    for (const fact of facts) assert.equal(entry[fact], undefined, `${fact} stays unknown, not defaulted`);
}

// (b) The engine's AUR row for the installed package: its facts and its Undo.
const [entry] = Logic.installedRows([aur], inventory);
assert.equal(entry.kind, 'aur');
assert.equal(entry.sourceLabel, 'AUR');
assert.equal(entry.undoKey, 'aur:paint-bin');
assert.ok(entry.actions.includes('remove') && entry.actions.includes('undo'));
assert.equal(entry.votes, 12);
assert.equal(entry.popularity, 0.5);
assert.equal(entry.maintainer, 'someone');
assert.equal(entry.orphan, false);
assert.equal(entry.outOfDate, '');
assert.ok(entry.lastModified && entry.firstSubmitted && entry.page && entry.recipe);

// (c) Its page: one AUR edition, the installed one, still able to undo and removed by its legacy key.
function page(row, rows) {
    // As ProgramsController.openProgram: Installed first, then the catalog rows; a row without
    // editions selects its own key through Logic.edition.
    const program = Logic.merge(rows).find(item => Logic.identity(item) === Logic.identity(row));
    const selected = row.editions ? Logic.edition(program) : Logic.edition(program, row.key);
    return {program, selected};
}
for (const opened of [entry, aur, Logic.merge([aur])[0]]) {
    const {program, selected} = page(opened, [entry, aur]);
    assert.equal(program.editions.filter(item => item.kind === 'aur').length, 1, 'one AUR edition');
    for (const edition of [selected, Logic.edition(program), Logic.edition(program, aur.key)]) {
        assert.equal(edition.kind, 'aur');
        assert.equal(edition.key, 'legacy:paint-bin');
        assert.equal(edition.installed, true);
        assert.equal(edition.undoKey, 'aur:paint-bin');
        assert.ok(edition.actions.includes('undo'));
        assert.equal(edition.votes, 12);
    }
}
// After an install from the AUR page the fresh lists arrive (as reconcileSelection: Installed first):
// the selected catalog key finds the installed entry, and the entry finds its program.
{
    const rows = [entry].concat(Logic.consolidate([aur]));
    const current = rows.find(row => row.key === aur.key || row.catalogKey === aur.key);
    assert.equal(current.undoKey, 'aur:paint-bin');
    assert.ok(Logic.merge(rows).find(row => (row.editions || []).some(item => item.key === current.key)));
}
// Without its installed entry the catalog edition stays whole.
assert.equal(Logic.merge([aur])[0].editions[0].key, 'aur:paint-bin');
console.log('programs logic: installed AUR identity, facts and Undo pass');
// A newer AUR version stays buildable from the installed entry; it is planned on the catalog key.
{
    const newer = Object.assign({}, aur, {version: '4-1', actions: ['remove', 'install']});
    const [updating] = Logic.installedRows([newer], inventory);
    assert.deepEqual(Array.from(updating.actions), ['remove', 'install']);
    assert.equal(updating.catalogKey, 'aur:paint-bin');
    assert.deepEqual(Array.from(Logic.installedRows([aur], inventory)[0].actions), ['remove', 'undo'], 'no update offered when none is newer');
}
// The installed entry keeps the program's own name; an update names the version it builds.
{
    const newer = Object.assign({}, aur, {version: '4-1', actions: ['remove', 'install']});
    const [updating] = Logic.installedRows([newer], inventory);
    assert.equal(updating.name, 'Paint');
    assert.equal(updating.version, '4-1');
    assert.equal(updating.installedVersion, '3-1');
}

// Installed development editions never borrow a stable desktop identity, even without RPC facts.
{
    const stable = {key: 'arch:foo', kind: 'arch', package: 'foo', id: 'org.example.Foo', name: 'Foo'};
    const inventory = [{package: 'foo-git', foreign: true, ids: ['org.example.Foo.desktop'], names: ['Foo']}];
    const [development] = Logic.installedRows([stable], inventory);
    const pages = Logic.merge([stable, development]);
    assert.equal(pages.length, 2, 'stable and -git must have separate pages');
    assert.notEqual(Logic.identity(stable), Logic.identity(development));
}

// Installed vital programs stay visible without catalog actions restoring Remove or Undo.
for (const name of ['emaki-config', 'kitty']) {
    const [vital] = Logic.installedRows(
        [{key: 'arch:extra:' + name, package: name, kind: 'arch', actions: ['remove', 'undo']}],
        [{package: name, names: [name], ids: [name + '.desktop'], protected: true, removable: false}]);
    assert.equal(vital.protected, true);
    assert.deepEqual(Array.from(vital.actions), []);
}
for (const name of ['firefox', 'dolphin', 'mpv']) {
    const [app] = Logic.installedRows([], [{package: name, names: [name], ids: [name + '.desktop'],
        protected: false, removable: true}]);
    assert.deepEqual(Array.from(app.actions), ['remove']);
    assert.equal(Logic.removalStatus(app), '');
}
// Required programs keep their reason and cannot regain Remove from catalog metadata.
for (const [name, blockers, protectedRow, reason] of [
    ['cups', ['emaki-apps'], true, 'Part of Emaki'],
    ['mpv', ['mpvqt'], false, 'Needed by other software']
]) {
    const [app] = Logic.installedRows(
        [{key: 'arch:extra/' + name, id: name, package: name, kind: 'arch', actions: ['remove']}],
        [{package: name, names: [name], ids: [name + '.desktop'],
          protected: protectedRow, requiredBy: blockers, removable: false}]);
    assert.deepEqual(Array.from(app.actions), []);
    assert.equal(Logic.removalStatus(app), reason);
    const page = Logic.merge([app])[0];
    assert.equal(Logic.removalStatus(Logic.edition(page)), reason);
    assert.deepEqual(Array.from(Logic.edition(page).actions), []);
}
// A private catalog identifies repository apps before the first system sync.
{
    const [app] = Logic.installedRows([{key: 'arch:extra/firefox', package: 'firefox', kind: 'arch'}],
        [{package: 'firefox', names: ['Firefox'], ids: ['firefox.desktop'], foreign: true}]);
    assert.equal(app.sourceLabel, 'Arch');
}
// A merged program's card shows its best-counted edition's stars, never a sum: the Flathub
// edition may be the only one whose metadata names the key that holds the ratings.
{
    const arch = {key: 'arch:extra/firefox:org.mozilla.firefox', kind: 'arch', package: 'firefox', id: 'org.mozilla.firefox', name: 'Firefox', rating: null};
    const flatpak = {key: 'flatpak:org.mozilla.firefox', kind: 'flatpak', id: 'org.mozilla.firefox', name: 'Firefox', rating: {average: 3.3, count: 628}};
    for (const rows of [[arch, flatpak], [flatpak, arch]]) {
        const [page] = Logic.merge(rows);
        assert.equal(page.editions[0].kind, 'arch');
        assert.deepEqual(page.rating, {average: 3.3, count: 628});
    }
    const [both] = Logic.merge([Object.assign({}, arch, {rating: {average: 4, count: 12}}), flatpak]);
    assert.deepEqual(both.rating, {average: 3.3, count: 628});
    assert.equal(Logic.merge([arch])[0].rating, null);
}
// The update service pipes pacman, so pacman prints no progress bar: plain "upgrading foo..."
// lines, counted against the "Packages (N)" summary of the same run. A terminal's "(n/m)" lines
// still count, and without a summary the total stays unknown rather than "N of N".
{
    const head = ['Packages (3) linux-6.1-1  mesa-1-1  vim-9-1', '', 'Total Download Size:  10.00 MiB',
        ':: Proceed with installation? [Y/n] ', ':: Retrieving packages...', ':: Running pre-transaction hooks...',
        '(1/2) Performing snapper pre snapshots for the following configurations...', '==> root: 512',
        ':: Processing package changes...'];
    const plain = Logic.updateProgress(head.concat(['upgrading linux...', 'installing mesa...']).join('\n'));
    assert.deepEqual([plain.step, plain.done, plain.total, plain.restorePoint], [3, 2, 3, true]);
    const verbose = Logic.updateProgress(['Package (2)  Old Version  New Version  Net Change  Download Size', '',
        ':: Processing package changes...', 'downgrading a...', 'removing b...', 'reinstalling c...'].join('\n'));
    assert.deepEqual([verbose.step, verbose.done, verbose.total], [3, 3, 3], 'the count never runs below what was seen');
    const unknown = Logic.updateProgress(':: Processing package changes...\nupgrading linux...\nupgrading mesa...');
    assert.deepEqual([unknown.step, unknown.done, unknown.total], [3, 2, 0]);
    // The keyring's own pacman run comes first; the full update's summary starts a new count.
    const second = Logic.updateProgress(['Packages (1) archlinux-keyring-1-1', ':: Processing package changes...',
        'upgrading archlinux-keyring...', ':: Running post-transaction hooks...', 'Packages (4) a-1 b-1 c-1 d-1',
        ':: Retrieving packages...', ':: Processing package changes...', 'upgrading a...'].join('\n'));
    assert.deepEqual([second.step, second.done, second.total], [3, 1, 4]);
    const counted = Logic.updateProgress(':: Processing package changes...\n(2/5) upgrading mesa\n');
    assert.deepEqual([counted.step, counted.done, counted.total], [3, 2, 5]);
    // Warnings that merely start with a verb are not package steps.
    const warning = Logic.updateProgress(':: Processing package changes...\nremoving foo breaks dependency \'foo\' required by bar');
    assert.equal(warning.done, 0);
}
{
    // Update rows: a release marker package of a few kilobytes (emaki 0.10.0-1 downloads 7.70 KiB)
    // never reads as zero; 0 is pacman's "already in the cache"; no size stays unknown.
    assert.equal(Logic.downloadText(7885), '< 0.1 MB');
    assert.equal(Logic.downloadText(49999), '< 0.1 MB');
    assert.equal(Logic.downloadText(50000), '0.1 MB');
    assert.equal(Logic.downloadText(0), '0 MB');
    assert.equal(Logic.downloadText(null), '—');
    assert.equal(Logic.downloadText(150000000), '150.0 MB');
    assert.equal(Logic.downloadText(1500000000), '1.5 GB');
}

// Progress and warning identity stay independent of translated display strings.
assert.equal(Logic.progressEvent(Logic.updateProgress(''), {phase: 'flatpak', message: '[Translated progress]'}).step, 4);
assert.equal(Logic.updateProgress('Updating Flatpak applications').step, 0);
assert.equal(Logic.aurBuilds({transaction: {kind: 'aur', builds: [{orphan: true}]}})[0].orphan, true);
assert.equal(Logic.undoRefusal({code: 'cannot-undo', requiredBy: ['one']}), ' one needs what it added.');
assert.equal(Logic.undoRefusal({code: 'cannot-undo', requiredBy: ['one', 'two']}), ' one, two need what it added.');
