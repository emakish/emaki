// Copyright (C) 2026 Artur Yakymenko
// SPDX-License-Identifier: GPL-3.0-or-later
pragma ComponentBehavior: Bound
import QtQuick

QtObject {
    id: registry
    property var catalog: null
    property SystemService service: null
    property NiriService niri: null
    property var notificationStore: null
    property var displays: null
    property SettingsNetworkService networkSettings: null
    property string page: "panel"
    readonly property var availablePages: Array.from(definitions).map(entry => entry.pageId)
    readonly property var coreKeys: Array.from(definitions).reduce((keys, entry) => keys.concat(Array.from(entry.keys)), [])
    readonly property var searchAnchors: Array.from(definitions).reduce((anchors, entry) => Object.assign(anchors, entry.searchAnchors), ({}))
    readonly property Component fallbackComponent: Component {
        SystemSettingsPage {
            service: registry.service
            niri: registry.niri
            page: registry.page
        }
    }
    signal navigateRequested(string page, string key)
    signal revealRequested(string key)
    function definition(pageId: string): SettingsPageDefinition {
        return Array.from(definitions).find(entry => entry.pageId === pageId) || null;
    }
    property list<SettingsPageDefinition> definitions: [
        SettingsPageDefinition {
            pageId: "panel"
            keys: ["bar.autohide", "bar.overview_workspaces", "dock.on", "dock.auto_hide"]
            description: "Choose when the panel and dock appear."
            component: Component {
                SettingsCorePage {
                    catalog: registry.catalog
                    section: "panel"
                }
            }
        },
        SettingsPageDefinition {
            pageId: "windows"
            keys: ["appearance.gaps", "windows.default_column_width", "windows.focus_follows_mouse"]
            description: "Choose window spacing, initial width and focus behavior."
            component: Component {
                SettingsCorePage {
                    catalog: registry.catalog
                    section: "windows"
                }
            }
        },
        SettingsPageDefinition {
            pageId: "wifi"
            description: "Join networks, see saved ones, forget them, show a password."
            searchAnchors: ({
                    "wifi-hidden-network": "settings-wifi-networks",
                    "wifi-radio": "settings-wifi-power",
                    "wifi-networks": "settings-wifi-networks",
                    "wifi-saved-networks": "settings-wifi-saved",
                    "wifi-forget-a-network": "settings-wifi-saved",
                    "wifi-show-password": "settings-wifi-saved",
                    "wifi-restart-wi-fi": "settings-wifi-restart"
                })
            component: Component {
                SettingsWifiPage {
                    service: registry.service
                    networkSettings: registry.networkSettings
                }
            }
        },
        SettingsPageDefinition {
            pageId: "bluetooth"
            description: "Pair headphones, mice and other devices and see their battery."
            searchAnchors: ({
                    "bluetooth-radio": "settings-bluetooth-power",
                    "bluetooth-pairing": "settings-bluetooth-search",
                    "bluetooth-devices": "settings-bluetooth-devices",
                    "bluetooth-battery-level-of-headphones-and-mice": "settings-bluetooth-devices"
                })
            component: Component {
                SettingsBluetoothPage {
                    service: registry.service
                }
            }
        },
        SettingsPageDefinition {
            pageId: "sound"
            description: "Choose speakers and microphones, and adjust volume for each app."
            searchAnchors: ({
                    "sound-mute": "settings-sound-mute",
                    "sound-volume": "settings-sound-volume",
                    "sound-output": "settings-sound-output",
                    "sound-input": "settings-sound-input",
                    "sound-input-volume": "settings-sound-input-volume",
                    "sound-input-mute": "settings-sound-input-mute",
                    "sound-input-level": "settings-sound-input-level",
                    "sound-volume-per-app": "settings-sound-streams"
                })
            component: Component {
                SoundSettingsPage {
                    backend: registry.service.backend
                }
            }
        },
        SettingsPageDefinition {
            pageId: "displays"
            description: "Arrange your screens and set their resolution, scale and refresh rate."
            searchAnchors: ({
                    "displays-night-light-end": "settings-night-schedule",
                    "displays-night-light-start": "settings-night-schedule",
                    "displays-enabled": "settings-display-enabled",
                    "displays-brightness": "settings-display-brightness",
                    "displays-resolution": "settings-display-resolution",
                    "displays-scale": "settings-display-scale",
                    "displays-refresh-rate": "settings-display-refresh-rate",
                    "displays-arrangement": "settings-display-arrangement",
                    "displays-main-display": "settings-display-main",
                    "displays-rotation": "settings-display-rotation",
                    "displays-night-light-schedule": "settings-night-schedule",
                    "displays-night-light": "settings-night-light",
                    "displays-night-light-temperature": "settings-night-warmth"
                })
            component: Component {
                DisplaysSettingsPage {
                    service: registry.service
                    displays: registry.displays
                    onConfirmationNeeded: registry.revealRequested("settings-display-confirmation")
                }
            }
        },
        SettingsPageDefinition {
            pageId: "battery"
            description: "See battery status and choose a power mode."
            searchAnchors: ({
                    "battery-power-mode": "settings-power-profile",
                    "battery-blank-on-battery": "settings-blank-battery",
                    "battery-blank-on-power": "settings-blank-ac",
                    "battery-when-the-lid-closes": "settings-lid",
                    "battery-charge-limit": "settings-charge-limit",
                    "battery-battery-health": "settings-battery-health"
                })
            component: Component {
                SettingsPowerPage {
                    service: registry.service
                }
            }
        },
        SettingsPageDefinition {
            pageId: "keyboard"
            keys: ["keyboard.layouts", "keyboard.switch_key", "keyboard.repeat_delay", "keyboard.repeat_rate", "keybindings.toggle_window_floating"]
            description: "Choose your layouts, key repeat and shortcuts."
            searchAnchors: ({
                    "keyboard-keyboard-shortcuts": "settings-keyboard-shortcuts"
                })
            resetKeys: Array.from(keys).concat(Array.from(registry.catalog?.values || []).filter(row => row.key.startsWith("shortcuts.")).map(row => row.key)).filter(key => registry.catalog?.row(key)?.default !== null && registry.catalog?.row(key)?.editable !== false)
            component: Component {
                KeyboardSettingsPage {
                    catalog: registry.catalog
                }
            }
        },
        SettingsPageDefinition {
            pageId: "mouse"
            keys: ["mouse.speed", "mouse.natural_scroll", "touchpad.speed", "touchpad.natural_scroll", "touchpad.tap", "touchpad.two_finger_right_click", "touchpad.disable_while_typing", "gestures.dnd_edge_view_scroll", "gestures.dnd_edge_workspace_switch"]
            description: "Choose pointer speed, scrolling and trackpad behavior."
            component: Component {
                MouseSettingsPage {
                    catalog: registry.catalog
                }
            }
        },
        SettingsPageDefinition {
            pageId: "network"
            searchAnchors: ({
                    "network-vpn-type": "settings-network-vpn",
                    "network-connection": "settings-network-connection",
                    "network-wired": "settings-network-wired",
                    "network-vpn-import-from-a-file": "settings-network-vpn",
                    "network-dns": "settings-network-dns"
                })
            component: Component {
                SettingsNetworkPage {
                    networkSettings: registry.networkSettings
                }
            }
        },
        SettingsPageDefinition {
            pageId: "notifications"
            searchAnchors: ({
                    "notifications-end": "settings-notifications-end",
                    "notifications-start": "settings-notifications-start"
                })
            keys: ["notifications.dnd", "notifications.until", "notifications.schedule", "notifications.rules"]
            description: "Choose which apps may notify you and when to stay quiet."
            component: Component {
                NotificationsSettingsPage {
                    catalog: registry.catalog
                    store: registry.notificationStore
                }
            }
        },
        SettingsPageDefinition {
            pageId: "wallpaper"
            keys: ["appearance.wallpaper"]
            description: "Choose an Emaki wallpaper or your own picture."
            searchAnchors: ({
                    "wallpaper-emaki-wallpapers": "appearance.wallpaper"
                })
            component: Component {
                SettingsWallpaperPage {
                    catalog: registry.catalog
                }
            }
        },
        SettingsPageDefinition {
            pageId: "region"
            keys: ["bar.clock_24_hour"]
            description: "Choose your language, regional formats and clock settings."
            searchAnchors: ({
                    "region-language": "settings-language",
                    "region-24-hour-clock": "settings-24-hour-clock",
                    "region-formats": "settings-formats",
                    "region-time-zone": "settings-time-zone",
                    "region-automatic-time": "settings-automatic-time"
                })
            component: Component {
                SettingsRegionPage {
                    catalog: registry.catalog
                }
            }
        },
        SettingsPageDefinition {
            pageId: "apps"
            keys: ["defaults.browser", "defaults.mail", "defaults.files", "defaults.terminal", "defaults.editor"]
            description: "Choose default apps and what starts when you sign in."
            searchAnchors: ({
                    "apps-startup-apps": "settings-startup-apps"
                })
            component: Component {
                SettingsAppsPage {
                    catalog: registry.catalog
                }
            }
        },
        SettingsPageDefinition {
            pageId: "about"
            description: "Your Emaki version and this computer."
            searchAnchors: ({
                    "about-hardware": "settings-about-hardware",
                    "about-emaki-version": "settings-about-version",
                    "about-report-a-problem": "settings-report-problem"
                })
            component: Component {
                SettingsAboutPage {
                    onUpdatesRequested: registry.navigateRequested("updates", "")
                }
            }
        },
        SettingsPageDefinition {
            pageId: "updates"
            description: "Choose the release channel for future updates."
            searchAnchors: ({
                    "updates-update-channel": "settings-update-channel"
                })
            component: Component {
                SettingsMaintenancePage {
                    page: "updates"
                    service: registry.service
                }
            }
        },
        SettingsPageDefinition {
            pageId: "lock"
            description: "Change your password and choose when to lock."
            searchAnchors: ({
                    "lock-password": "settings-password",
                    "lock-when-to-lock": "settings-lock-delay"
                })
            component: Component {
                SettingsMaintenancePage {
                    page: "lock"
                    service: registry.service
                }
            }
        }
    ]
}
