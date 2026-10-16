#!/usr/bin/env python3
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Drive ProgramReviews.qml through the real reviews engine against recorded ODRS answers.

The component runs a stand-in `emaki-programs` that is programs_reviews.main with an opener
answering from update-manager/tests/odrs: no network. Submitted bodies land in a log file.
"""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

UI = Path(__file__).resolve().parents[1]
ROOT = UI.parents[1]
ENGINE = '''import io, json, os, sys
from urllib.error import HTTPError, URLError
sys.path.insert(0, os.environ['TEST_ENGINE'])
import programs_reviews
RECORDED = os.path.join(os.environ['TEST_ENGINE'], 'tests', 'odrs')

class Response(io.BytesIO):
    url = programs_reviews.SERVICE
    headers = {}
    def __enter__(self): return self
    def __exit__(self, *args): return False

def opener(request, timeout=None):
    path = request.full_url[len(programs_reviews.SERVICE):]
    body = json.loads(request.data) if request.data else None
    if path == '/ratings':
        return Response(open(os.path.join(RECORDED, 'ratings.json'), 'rb').read())
    if path == '/fetch':
        if body['app_id'] == 'org.example.Offline':
            raise URLError('offline')
        return Response(open(os.path.join(RECORDED, 'fetch-krita.json'), 'rb').read()
                        if body['app_id'] == 'org.kde.krita' else b'[]')
    if path == '/submit':
        with open(os.environ['TEST_SUBMITTED'], 'a') as log:
            log.write(json.dumps(body) + '\\n')
        if body['app_id'] == 'org.example.Refused':
            raise HTTPError(request.full_url, 400, 'Bad Request', {},
                            io.BytesIO(b'{"success": false, "msg": "already reviewed this app"}'))
        return Response(b'{"success": true, "review_id": 7}')
    raise AssertionError(path)

sys.exit(programs_reviews.main(sys.argv[1:], opener=opener))
'''
QML = '''import QtQuick
import QtQuick.Controls as C
import QtTest
import Quickshell
ShellRoot {
    id: test
    property bool failed: false
    property int step: 0
    property int waited: 0
    readonly property var krita: ({kind: "arch", id: "org.kde.krita", name: "Krita", version: "1:5.2.13-1",
        rating: {average: 3.7, count: 400},
        editions: [{kind: "arch", id: "org.kde.krita"}, {kind: "flatpak", id: "org.kde.krita"},
                   {kind: "aur", id: "org.kde.krita", package: "krita-git"}]})
    FloatingWindow {
        id: window
        implicitWidth: 940; implicitHeight: 900
        color: "#f4e9e4"
        Flickable {
            anchors.fill: parent; anchors.margins: 24
            contentHeight: reviews.implicitHeight
            ProgramReviews { id: reviews; width: parent.width; command: COMMAND; program: ({}) }
        }
        // A featured card 360 px wide has room for its stars, average and short count beside Install;
        // a narrower one drops the count (layout.py checks that nothing spills).
        Row {
            y: 700; x: 24; spacing: 16
            ProgramCard { id: plainCard; width: 260; program: ({name: "Obsidian", summary: "Notes", rating: {average: 4.62, count: 1234}}) }
            ProgramCard { id: featuredCard; featured: true; width: 360; program: ({name: "Obsidian", summary: "Notes", rating: {average: 4.62, count: 1234}}) }
            ProgramCard { id: unratedCard; width: 260; program: ({name: "Paint", summary: "Draw", rating: null}) }
        }
        // Below the window, out of the screenshots: two-line summaries and an unrated featured card.
        Row {
            y: 920; x: 24; spacing: 16
            readonly property var notes: ({name: "Obsidian", rating: {average: 4.62, count: 1234},
                summary: "A somewhat longer description of what this note taking program does for everyday notes and writing"})
            ProgramCard { id: longPlainCard; width: 260; program: parent.notes }
            ProgramCard { id: longFeaturedCard; featured: true; width: 360; program: parent.notes }
            ProgramCard { id: unratedFeaturedCard; featured: true; width: 360; program: ({name: "Paint", summary: "Draw", rating: null}) }
        }
        TestCase { id: input; when: false; name: "ProgramReviews" }
    }
    function check(ok, description) {
        if (!ok) { failed = true; console.error("ASSERTION_FAILED " + description); }
    }
    function find(item, predicate) {
        if (!item) return null;
        if (predicate(item)) return item;
        for (const child of item.children || []) { const found = find(child, predicate); if (found) return found; }
        return null;
    }
    function all(item, predicate, found = []) {
        if (!item) return found;
        if (predicate(item)) found.push(item);
        for (const child of item.children || []) all(child, predicate, found);
        return found;
    }
    function named(name) {
        // A Popup is no Item: it sits in its parent's data, its contents in the window overlay.
        for (const entry of reviews.data) if (entry.objectName === name) return entry;
        return find(window.contentItem, item => item.objectName === name)
            || find(reviews.C.Overlay.overlay, item => item.objectName === name);
    }
    function cards() { return all(reviews, item => item.objectName === "review-card" && item.visible); }
    // Items laid out since the last frame: a click at their coordinates hits what is on screen, not them.
    function settled() { return !input.isPolishScheduled(window.contentItem.Window.window); }
    function click(item) {
        check(!!item && item.visible, "clickable " + (item && item.objectName));
        check(settled(), "laid out before the click on " + (item && item.objectName));
        if (!item) return;
        input.mouseMove(item, item.width / 2, item.height / 2);
        input.mouseClick(item, item.width / 2, item.height / 2);
    }
    function shot(name) {
        if (!Quickshell.env("TEST_SHOTS")) return;
        input.grabImage(window.contentItem).save(Quickshell.env("TEST_SHOTS") + "/" + name + ".png");
    }
    function summaryText() { return find(named("reviews-summary"), item => item.objectName === "stars-text")?.text; }
    function checkRating(rating) {
        check(named("review-dialog").stars === rating, "the chosen rating is " + rating);
        for (let i = 1; i <= 5; i++) {
            const star = named("review-star-" + i);
            check(star.Accessible.name === "Rate " + i + " of 5", "each star names its rating");
            check(star.Accessible.role === Accessible.RadioButton, "each star is a radio button");
            check(star.checkable && star.Accessible.checkable, "each star exposes a checkable rating");
            check(star.checked === (i === rating), "only the chosen rating is checked");
            check(star.Accessible.checked === (i === rating), "rating " + rating + ": star " + i + " is announced as checked: " + star.Accessible.checked);
            check(star.Accessible.selected === (i === rating), "only the chosen rating is selected");
        }
    }
    // Each step waits for its condition and a laid-out window (polled, never a nested wait), then acts and checks.
    readonly property var steps: [
        [() => true, () => {
            const stars = card => find(card, item => item.objectName === "card-rating");
            const text = card => find(stars(card), item => item.objectName === "stars-text").text;
            check(stars(plainCard).visible && text(plainCard) === "4.6", "a card shows its average: " + text(plainCard));
            check(text(featuredCard) === "4.6 · 1.2k", "a featured card shows the short count: " + text(featuredCard));
            check(find(stars(plainCard), item => item.objectName === "stars-glyphs").text === "★★★★★", "4.62 draws five stars");
            check(!stars(unratedCard).visible, "an unrated card draws no stars");
            // The stars (and a plain card's size line) stay inside the padded card, even under two lines.
            const inside = (card, item, what) => {
                const top = item.mapToItem(card, 0, 0).y;
                check(top >= card.topPadding && top + item.height <= card.height - card.bottomPadding,
                      what + " inside the card: " + top + ".." + (top + item.height) + " of " + card.height);
            };
            for (const card of [plainCard, featuredCard, longPlainCard, longFeaturedCard])
                inside(card, stars(card), (card.featured ? "featured" : "plain") + " card stars");
            // The store window's cards keep the summary on one line, cut with an ellipsis.
            check(find(longPlainCard, item => item.text === longPlainCard.program.summary).truncated
                  && find(longFeaturedCard, item => item.text === longFeaturedCard.program.summary).truncated, "long summaries stay on one line");
            check(plainCard.height === unratedCard.height && featuredCard.height === unratedFeaturedCard.height, "rated and unrated cards of one kind are equally tall");
            check(plainCard.height === longPlainCard.height && featuredCard.height === longFeaturedCard.height, "summary length does not change a card's height");
            // A program with no catalog identity has no reviews block at all.
            reviews.program = {kind: "arch", id: "paint", packageOnly: true};
        }],
        [() => true, () => {
            check(!reviews.visible, "package rows show no reviews block");
            reviews.program = krita;
            check(reviews.visible && reviews.ids.join() === "org.kde.krita", "catalog editions give AppStream IDs, AUR editions none");
            check(reviews.rating.count === 400, "the row's cached stars show before the fetch answers");
        }],
        [() => reviews.loaded, () => {
            check(reviews.error === "", "no error: " + reviews.error);
            check(reviews.rating.count === 495 && Math.abs(reviews.rating.average - 3.78) < .01, "fresh rating from the index");
            check(summaryText() === "3.8 · 495 ratings", "summary reads " + summaryText());
            check(find(named("reviews-summary"), item => item.objectName === "stars-glyphs").text === "★★★★☆", "summary stars");
            check(cards().length === 3, "three review cards before Show all, got " + cards().length);
            check(find(cards()[0], item => item.text === "Slow to start") !== null, "first card is the service's first review");
            check(find(cards()[0], item => String(item.text).startsWith("Reviewer 1 · ")) !== null, "card names the reviewer and date");
            check(named("reviews-more").text === "Show all 4 reviews", "Show all names the count");
            click(named("reviews-more"));
        }],
        [() => cards().length === 4, () => {
            check(!named("reviews-more").visible, "Show all goes away");
            shot("program-reviews");
            check(named("reviews-write").visible && named("reviews-write").enabled, "Write a review is offered");
            click(named("reviews-write"));
        }],
        [() => named("review-dialog").opened, () => {
            check(!named("review-send").enabled, "Send waits for stars, a title and text");
            checkRating(0);
            click(named("review-star-4"));
            checkRating(4);
        }],
        [() => true, () => {
            named("review-star-2").forceActiveFocus();
            input.keyClick(Qt.Key_Space);
            checkRating(2);
        }],
        [() => true, () => {
            named("review-star-5").forceActiveFocus();
            input.keyClick(Qt.Key_Return);
            checkRating(5);
        }],
        [() => true, () => {
            click(named("review-star-4"));
            checkRating(4);
        }],
        [() => true, () => {
            click(named("review-star-4"));
            checkRating(4);
            const editor = named("review-text");
            editor.forceActiveFocus();
            input.keyClick(Qt.Key_Tab);
            check(named("review-name").activeFocus && editor.text === "", "Tab leaves review text without inserting a tab");
            editor.forceActiveFocus();
            input.keyClick(Qt.Key_Backtab);
            check(named("review-title").activeFocus, "Backtab leaves review text for title");
            named("review-title").text = "Good for drawing";
            named("review-text").text = "Works with my tablet.";
        }],
        [() => named("review-send").enabled, () => {
            shot("review-dialog");
            click(named("review-send"));
        }],
        [() => !named("review-dialog").visible, () => {
            check(reviews.notice.startsWith("Thank you."), "thanks after sending");
            // The service refuses a second review of one program: the dialog stays and says why.
            reviews.program = {kind: "flatpak", id: "org.example.Refused", name: "Refused"};
        }],
        [() => reviews.loaded, () => {
            check(named("reviews-state").text === "No ratings yet. Be the first to review it.", "an unrated program says so: " + named("reviews-state").text);
            click(named("reviews-write"));
        }],
        [() => named("review-dialog").opened, () => {
            check(named("review-dialog").stars === 0 && named("review-title").text === "", "a new dialog starts empty");
            checkRating(0);
            click(named("review-star-2"));
            named("review-title").text = "Again";
            named("review-text").text = "Second try.";
        }],
        [() => named("review-send").enabled, () => click(named("review-send"))],
        [() => named("review-dialog").problem !== "", () => {
            const dialog = named("review-dialog");
            check(dialog.visible && dialog.problem === "You have already reviewed this program.", "refusal text " + dialog.problem);
            input.keyClick(Qt.Key_Escape);
        }],
        [() => !named("review-dialog").visible, () => {
            // Stars and reviews may come from an ID the row provides; the review goes to the program's own ID.
            reviews.program = {kind: "flatpak", id: "org.mozilla.firefox", name: "Firefox",
                editions: [{kind: "flatpak", id: "org.mozilla.firefox", providedIds: ["firefox.desktop"]}]};
        }],
        [() => reviews.loaded, () => {
            check(reviews.ids.join() === "org.mozilla.firefox,firefox.desktop", "provided IDs are read: " + reviews.ids.join());
            check(reviews.appId === "firefox.desktop" && reviews.rating.count === 628, "the provided key holds the ratings");
            click(named("reviews-write"));
        }],
        [() => named("review-dialog").opened, () => {
            click(named("review-star-5"));
            named("review-title").text = "Fast";
            named("review-text").text = "Quick to start.";
        }],
        [() => named("review-send").enabled, () => click(named("review-send"))],
        [() => !named("review-dialog").visible, () => {
            // Offline: the row's stars stay, the problem is said once, the store goes on.
            reviews.program = {kind: "arch", id: "org.example.Offline", rating: {average: 4.2, count: 12}};
        }],
        [() => reviews.loaded, () => {
            check(named("reviews-state").text === "Ratings could not be loaded. Check your connection.", "offline text " + named("reviews-state").text);
            check(reviews.rating.count === 12 && named("reviews-summary").visible, "cached stars stay offline");
            check(cards().length === 0, "no cards offline");
            // AUR packages show their votes, no stars and no review form.
            reviews.program = {kind: "aur", id: "", package: "yay-bin", votes: 2610, popularity: 21.456};
        }],
        [() => true, () => {
            check(named("reviews-aur-votes").text === "2,610 votes on the AUR · popularity 21.46", "AUR votes: " + named("reviews-aur-votes").text);
            check(!named("reviews-write").parent.visible, "no review form for AUR");
            check(!named("reviews-summary").visible, "no stars for AUR");
            shot("aur-votes");
            // A missing command never starts: the block says so after a moment instead of loading forever.
            reviews.command = ["/nonexistent/emaki-programs"];
            reviews.program = {kind: "arch", id: "org.example.Missing"};
            check(named("reviews-state").text === "Loading ratings…", "loading first");
        }],
        [() => reviews.loaded, () => {
            check(named("reviews-state").text === "Ratings could not be loaded. Check your connection.", "a command that cannot start fails plainly");
        }]
    ]
    Timer {
        interval: 50; running: true; repeat: true
        onTriggered: {
            if (test.step >= test.steps.length || test.failed) {
                if (!test.failed) console.log("REVIEWS_OK");
                running = false;
                Qt.quit();
                return;
            }
            const [ready, act] = test.steps[test.step];
            let ok = false;
            try { ok = ready() && test.settled(); } catch (_) { ok = false; }
            if (!ok) {
                test.waited += interval;
                if (test.waited > 10000) test.check(false, "step " + test.step + " never became ready");
                return;
            }
            test.waited = 0;
            try { act(); } catch (error) { test.check(false, "step " + test.step + ": " + error + " " + error.stack); }
            test.step++;
        }
    }
}
'''


def main():
    with tempfile.TemporaryDirectory(prefix='reviews-ui-', dir=os.environ.get('TMPDIR')) as directory:
        root = Path(directory)
        for name in ('ProgramReviews.qml', 'Stars.qml', 'ManagerLabel.qml', 'ManagerButton.qml', 'ProgramCard.qml',
                     'ProgramIcon.qml', 'ManagerActions.qml'):
            (root / name).write_text((UI / name).read_text().replace('@EMAKI_DATADIR@', str(UI.parents[1])))
        (root / 'engine.py').write_text(ENGINE)
        (root / 'submitted.log').write_text('')
        (root / 'test.qml').write_text(QML.replace('COMMAND', json.dumps([sys.executable, str(root / 'engine.py')])))
        env = dict(os.environ, QT_QPA_PLATFORM='offscreen', QT_QUICK_BACKEND='software', QT_QUICK_CONTROLS_STYLE='Basic',
                   QML_DISABLE_DISK_CACHE='1', QS_DISABLE_CRASH_HANDLER='1', TEST_ENGINE=str(UI.parent),
                   TEST_SUBMITTED=str(root / 'submitted.log'), LANG='en_US.UTF-8', LC_ALL='')
        for name in ('cache', 'config', 'data', 'state'):
            (root / name).mkdir()
            env['XDG_' + name.upper() + '_HOME'] = str(root / name)
        for name in ('DISPLAY', 'WAYLAND_DISPLAY', 'DBUS_SESSION_BUS_ADDRESS', 'NIRI_SOCKET'):
            env.pop(name, None)
        shots = ROOT / '.cache/evidence/F8R'
        shots.mkdir(parents=True, exist_ok=True)
        env['TEST_SHOTS'] = str(shots)
        with tempfile.TemporaryDirectory(prefix='', dir=ROOT / '.cache') as runtime:
            env['XDG_RUNTIME_DIR'] = runtime
            with (root / 'runtime.log').open('w') as output:
                try:
                    result = subprocess.run(['qs', '-p', str(root / 'test.qml'), '--no-color'], env=env,
                                            stdout=output, stderr=subprocess.STDOUT, text=True, timeout=120)
                except subprocess.TimeoutExpired as error:
                    raise AssertionError((root / 'runtime.log').read_text()) from error
        log = (root / 'runtime.log').read_text()
        assert result.returncode == 0 and 'REVIEWS_OK' in log, log
        assert not any(word in log for word in ('ASSERTION_FAILED', 'ReferenceError', 'TypeError', 'Unable to assign',
                                                'Binding loop')), log
        bodies = [json.loads(line) for line in (root / 'submitted.log').read_text().splitlines()]
        # Never the key the reviews were read from (firefox.desktop): only the program's own ID.
        assert [body['app_id'] for body in bodies] == ['org.kde.krita', 'org.example.Refused',
                                                        'org.mozilla.firefox'], bodies
        first = dict(bodies[0])
        assert len(first.pop('user_hash')) == 40, bodies
        assert first == dict(app_id='org.kde.krita', locale='en_US', distro=first['distro'], version='5.2.13',
                             user_display='Anonymous', summary='Good for drawing',
                             description='Works with my tablet.', rating=80), first
        assert bodies[1]['rating'] == 40, bodies
        assert bodies[0]['user_hash'] == bodies[1]['user_hash'], 'one identity per account'
        assert (root / 'state/emaki-update-manager/odrs-user').read_text() == bodies[0]['user_hash']
        print('PASS reviews: stars, cards, Show all, write and send, refusal, offline, AUR votes; shots in', shots)


if __name__ == '__main__':
    main()
