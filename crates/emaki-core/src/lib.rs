// Copyright (C) 2026 Artur Yakymenko
// SPDX-License-Identifier: GPL-3.0-or-later
//! Portable Emaki core, independent of the graphical shell and package manager.
//!
//! Discovery, explicit niri actions, and isolated-profile managed settings.

pub mod i18n;
pub mod map;
pub mod niri;
pub mod settings;
pub mod state;
mod transport;

/// Version shared by the core and command-line interface.
pub const VERSION: &str = env!("CARGO_PKG_VERSION");

/// Independently versioned public JSON contracts (additive fields are allowed).
pub const STATE_SCHEMA_VERSION: u32 = 1;
pub const MAP_SCHEMA_VERSION: u32 = 1;
