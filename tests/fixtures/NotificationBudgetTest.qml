// Copyright (C) 2026 Artur Yakymenko
// SPDX-License-Identifier: GPL-3.0-or-later
pragma ComponentBehavior: Bound
import QtQuick
import QtTest
import Quickshell

ShellRoot {
    readonly property bool translationsReady: Translation.initialize()
    NotificationStore {
        id: store
    }
    Component {
        id: mediaFixture
        MediaBlock {
            players: []
        }
    }
    Window {
        id: window
        width: 1280
        height: 800
        visible: true
        ClockPanel {
            id: panel
            viewportWidth: window.width
            viewportHeight: window.height
            opened: true
            expansion: 1
            head: 1
            store: store
            serverState: "active"
            mediaEnabled: true
        }
        TestCase {
            name: "NotificationBudget"
            when: window.visible
            readonly property var body: panel.body
            onCompletedChanged: if (completed)
                console.log("NOTIFICATION_BUDGET_RESULT", qtest_results.passCount, qtest_results.failCount)
            function check(ok, message) {
                if (!ok)
                    console.error("NOTIFICATION_BUDGET_FAIL", message || "check", new Error().stack);
                verify(ok, message || "");
            }
            function equal(actual, expected, message) {
                if (actual !== expected)
                    console.error("NOTIFICATION_BUDGET_FAIL", actual, "expected", expected, message || "", new Error().stack);
                compare(actual, expected, message || "");
            }
            function initTestCase() {
                try {
                    NotesService.active = false;
                    NotesService.ready = true;
                    body.mediaSource = mediaFixture;
                } catch (error) {
                    console.error("NOTIFICATION_BUDGET_FAIL", "initTestCase", String(error), error.stack);
                    throw error;
                }
            }
            function setup(height, weeks, eventCount, mediaCount, disabled, unavailable) {
                panel.opened = false;
                window.height = height;
                // Monday-first February 2021, October 2026, and March 2026.
                panel.today = weeks === 4 ? new Date(2021, 1, 15) : weeks === 5 ? new Date(2026, 9, 19) : new Date(2026, 2, 16);
                panel.serverState = disabled ? "disabled" : "active";
                store.scheduleSuppressedUntil = 0;
                store.entries = [];
                store.actionState = unavailable ? "action_unavailable" : "idle";
                for (let i = 0; i < 9; ++i)
                    store.local("Application " + i, "Notice " + i, "Short message");
                store.entries = store.entries.map((note, index) => Object.assign({}, note, {
                        time: Date.now() - index * 1000
                    }));
                NotesService.notes = [
                    {
                        id: "note-1",
                        title: "A note",
                        body: "Remember",
                        updated: 1
                    }
                ];
                NotesService.events = [];
                panel.opened = true;
                body.keyboardMode = false;
                body.historyExpanded = false;
                body.calendar.showToday();
                wait(50);
                let events = [];
                for (let i = 0; i < eventCount; ++i)
                    events.push({
                        id: "event-" + i,
                        title: "Appointment " + i,
                        date: body.calendar.selectedDate,
                        start: "09:00",
                        duration: 30,
                        reminder: null,
                        revision: "v1"
                    });
                NotesService.events = events;
                let players = [];
                for (let i = 0; i < mediaCount; ++i)
                    players.push({
                        dbusName: "player-" + i,
                        identity: "Player " + i,
                        trackTitle: "Track",
                        trackArtist: "Artist",
                        isPlaying: false,
                        canTogglePlaying: false,
                        canGoNext: false,
                        canGoPrevious: false
                    });
                body.player.players = players;
                body.scroller.contentY = 0;
                wait(350);
                equal(body.calendar.cellCount / 7, weeks);
                equal(body.player.playerCount, mediaCount);
                check(body.findTarget(body, "shade-notes") !== null);
            }
            function textItem(item, text) {
                if (item.text === text && item.visible)
                    return item;
                for (const child of item.children) {
                    const found = textItem(child, text);
                    if (found)
                        return found;
                }
                return null;
            }
            function mixedNotices(count) {
                store.entries = [];
                for (let i = 0; i < count; ++i)
                    store.addLocal(i < 2 ? "Grouped application" : "Application " + i, "Notice " + i, "A detailed notification about a device whose name needs enough room to wrap across several lines in the drawer.", false, false, i % 2 ? [
                        {
                            id: "review",
                            text: "Review"
                        }
                    ] : [], {
                        time: Date.now() - i * 1000
                    });
                for (const group of store.groups())
                    store.expand(group.key, true);
                wait(100);
            }
            function targetBottom(target) {
                return target.mapToItem(body.scroller, 0, target.height).y;
            }
            function verifyBudget() {
                const clear = body.findTarget(body, "clear-all");
                check(clear !== null);
                // Use the rendered footer's height, not the plan's preferred trailing space.
                let expected = 1;
                for (let cap = 6; cap >= 1; --cap) {
                    const candidate = body.notePlan(cap);
                    const footer = candidate.items.find(item => item.kind === "clear");
                    const occupied = body.actionNote ? candidate.height + 18 : footer.y + clear.height;
                    if (body.listTop + occupied <= body.maxHeight) {
                        expected = cap;
                        break;
                    }
                }
                const count = body.plan.items.filter(item => item.kind === "note").length;
                equal(count, expected, "largest compact cap that fits");
                const first = body.notePlan(1);
                const firstFooter = first.items.find(item => item.kind === "clear");
                const firstBottom = body.listTop + (body.actionNote ? first.height + 18 : firstFooter.y + clear.height);
                if (firstBottom <= body.maxHeight) {
                    const keys = ["clear-all", "history-more"].concat(body.plan.items.filter(item => item.kind === "note").map(item => "note-" + item.note.id));
                    for (const key of keys) {
                        const target = body.findTarget(body, key);
                        check(target !== null, key);
                        check(target.mapToItem(body.scroller, 0, 0).y >= 0, key + " top");
                        check(targetBottom(target) <= body.scroller.height, key + " bottom");
                    }
                    if (body.actionNote) {
                        const action = textItem(body, body.actionNote);
                        check(action !== null, "action status text exists");
                        check(action.mapToItem(body.scroller, 0, 0).y >= 0);
                        check(targetBottom(action) <= body.scroller.height, "action status fits");
                    }
                } else {
                    equal(count, 1, "one card remains reachable by scrolling");
                    check(body.scroller.contentHeight > body.scroller.height);
                    body.historyExpanded = true;
                    wait(50);
                    body.scroller.contentY = body.scroller.contentHeight - body.scroller.height;
                    wait(50);
                    const lastClear = body.findTarget(body, "clear-all");
                    check(targetBottom(lastClear) <= body.scroller.height);
                    check(lastClear.mapToItem(body.scroller, 0, 0).y >= 0);
                }
                if (body.fullHeight <= body.maxHeight)
                    check(body.scroller.contentHeight <= body.scroller.height, "no phantom scroll");
            }
            function test_viewports_data() {
                return [
                    {
                        tag: "800-six-weeks-disabled",
                        height: 800,
                        weeks: 6,
                        events: 0,
                        media: 0,
                        disabled: true
                    },
                    {
                        tag: "900-mixed-expanded",
                        height: 900,
                        weeks: 5,
                        events: 0,
                        media: 0,
                        mixed: true
                    },
                    {
                        tag: "850-schedule-resume",
                        height: 850,
                        weeks: 5,
                        events: 0,
                        media: 0,
                        resume: true
                    },
                    {
                        tag: "620-four-weeks",
                        height: 620,
                        weeks: 4,
                        events: 0,
                        media: 0
                    },
                    {
                        tag: "720-five-weeks-disabled",
                        height: 720,
                        weeks: 5,
                        events: 0,
                        media: 0,
                        disabled: true
                    },
                    {
                        tag: "800-five-weeks",
                        height: 800,
                        weeks: 5,
                        events: 0,
                        media: 0
                    },
                    {
                        tag: "850-six-weeks",
                        height: 850,
                        weeks: 6,
                        events: 0,
                        media: 0
                    },
                    {
                        tag: "900-events-player",
                        height: 900,
                        weeks: 5,
                        events: 2,
                        media: 1
                    },
                    {
                        tag: "800-action-two-players",
                        height: 800,
                        weeks: 4,
                        events: 0,
                        media: 2,
                        unavailable: true
                    },
                    {
                        tag: "620-tall-events-media",
                        height: 620,
                        weeks: 6,
                        events: 8,
                        media: 2,
                        disabled: true
                    }
                ];
            }
            function test_viewports(data) {
                try {
                    setup(data.height, data.weeks, data.events, data.media, !!data.disabled, !!data.unavailable);
                    if (data.mixed)
                        mixedNotices(9);
                    if (data.resume) {
                        store.suppressedSchedule = JSON.stringify(store.schedule);
                        store.scheduleSuppressedUntil = Date.now() + 3600000;
                        wait(100);
                        check(store.scheduleResumeNote.length > 0);
                    }
                    verifyBudget();
                } catch (error) {
                    console.error("NOTIFICATION_BUDGET_FAIL", "test_viewports", String(error), error.stack);
                    throw error;
                }
            }
            function test_four_wrapped_cards_need_footer_space() {
                try {
                    setup(800, 6, 0, 0, false, false);
                    mixedNotices(4);
                    body.historyExpanded = true;
                    wait(100);
                    const clear = body.findTarget(body, "clear-all");
                    const footerBottom = clear.mapToItem(body, 0, clear.height).y;
                    let cardsBottom = 0;
                    for (const note of store.entries) {
                        const row = body.findTarget(body, "note-" + note.id);
                        cardsBottom = Math.max(cardsBottom, row.mapToItem(body, 0, row.height).y);
                    }
                    check(cardsBottom < footerBottom);
                    // Retain all four card rectangles, but leave the footer short by one pixel.
                    window.height = footerBottom - 1 + 2 * Metrics.top;
                    body.historyExpanded = false;
                    body.scroller.contentY = 0;
                    wait(350);
                    check(cardsBottom <= body.maxHeight);
                    equal(body.plan.items.filter(item => item.kind === "note").length, 3);
                    check(targetBottom(body.findTarget(body, "clear-all")) <= body.scroller.height);
                    body.historyExpanded = true;
                    wait(100);
                    equal(body.plan.items.filter(item => item.kind === "note").length, 4);
                    body.scroller.contentY = body.scroller.contentHeight - body.scroller.height;
                    wait(100);
                    check(targetBottom(body.findTarget(body, "clear-all")) <= body.scroller.height);
                } catch (error) {
                    console.error("NOTIFICATION_BUDGET_FAIL", "test_four_wrapped_cards_need_footer_space", String(error), error.stack);
                    throw error;
                }
            }
            function test_exact_footer_boundary() {
                try {
                    setup(900, 5, 0, 0, false, false);
                    const candidate = body.notePlan(4);
                    const clear = body.findTarget(body, "clear-all");
                    const footer = candidate.items.find(item => item.kind === "clear");
                    const boundary = body.listTop + footer.y + clear.height;
                    window.height = boundary + 2 * Metrics.top;
                    wait(350);
                    equal(body.plan.items.filter(item => item.kind === "note").length, 4);
                    equal(targetBottom(body.findTarget(body, "clear-all")), body.scroller.height);
                    verifyBudget();
                    window.height -= 1;
                    wait(350);
                    equal(body.plan.items.filter(item => item.kind === "note").length, 3);
                    verifyBudget();
                } catch (error) {
                    console.error("NOTIFICATION_BUDGET_FAIL", "test_exact_footer_boundary", String(error), error.stack);
                    throw error;
                }
            }
            function test_wheel_without_extra_scroll_range() {
                try {
                    setup(900, 4, 0, 0, false, false);
                    check(body.fullHeight <= body.maxHeight);
                    check(body.scroller.contentHeight <= body.scroller.height);
                    mouseWheel(body.scroller, 250, 80, 0, -240);
                    wait(150);
                    check(body.historyExpanded);
                    equal(body.plan.items.filter(item => item.kind === "note").length, 9);
                    check(body.scroller.contentY > 0);
                } catch (error) {
                    console.error("NOTIFICATION_BUDGET_FAIL", "test_wheel_without_extra_scroll_range", String(error), error.stack);
                    throw error;
                }
            }
            // A drawer that fits the screen has no scroll range; dragging it up still opens history.
            function test_drag_without_extra_scroll_range() {
                try {
                    setup(900, 4, 0, 0, false, false);
                    check(body.fullHeight <= body.maxHeight);
                    check(body.scroller.contentHeight <= body.scroller.height);
                    check(!body.historyExpanded);
                    mouseDrag(body.scroller, 250, 300, 0, -200);
                    wait(150);
                    check(body.historyExpanded);
                    equal(body.plan.items.filter(item => item.kind === "note").length, 9);
                } catch (error) {
                    console.error("NOTIFICATION_BUDGET_FAIL", "test_drag_without_extra_scroll_range", String(error), error.stack);
                    throw error;
                }
            }
            function test_overflow_pointer() {
                try {
                    setup(850, 5, 0, 0, false, false);
                    verifyBudget();
                    const more = body.findTarget(body, "history-more");
                    mouseClick(more, more.width / 2, more.height / 2);
                    wait(150);
                    check(body.historyExpanded);
                    equal(body.plan.items.filter(item => item.kind === "note").length, 9);
                    check(body.scroller.contentY > 0);
                } catch (error) {
                    console.error("NOTIFICATION_BUDGET_FAIL", "test_overflow_pointer", String(error), error.stack);
                    throw error;
                }
            }
        }
    }
}
