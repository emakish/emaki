// Copyright (C) 2026 Artur Yakymenko
// SPDX-License-Identifier: GPL-3.0-or-later

use emaki_core::settings::{Operation, run as settings};
use std::io::{self, BufRead, IsTerminal, Read, Write};
use std::path::Path;
use std::process::{Command, ExitCode, Stdio};
use std::time::Duration;

// A scoped terminal mode also restores the user's exact settings on I/O errors.
// Keep output processing enabled, and handle interrupt keys ourselves so they
// cannot terminate the process before the guard restores canonical input.
struct TerminalMode(String);

impl TerminalMode {
    fn enter() -> io::Result<Self> {
        let saved = Command::new("stty")
            .arg("-g")
            .stdin(Stdio::inherit())
            .output()?;
        if !saved.status.success() {
            return Err(io::Error::other("Cannot read terminal settings"));
        }
        let mode = Self(String::from_utf8_lossy(&saved.stdout).trim().to_owned());
        if !Command::new("stty")
            .args(["-icanon", "-echo", "-isig", "min", "1", "time", "0"])
            .status()?
            .success()
        {
            return Err(io::Error::other("Cannot set terminal input mode"));
        }
        Ok(mode)
    }
}

impl Drop for TerminalMode {
    fn drop(&mut self) {
        let _ = Command::new("stty").arg(&self.0).status();
    }
}

pub(super) fn run(root: Option<&Path>, timeout: Duration) -> ExitCode {
    let interactive = io::stdin().is_terminal() && io::stdout().is_terminal();
    let _terminal = if interactive {
        match TerminalMode::enter() {
            Ok(mode) => Some(mode),
            Err(_) => return ExitCode::from(2),
        }
    } else {
        None
    };
    match menu(
        &mut io::stdin().lock(),
        &mut io::stdout().lock(),
        root,
        timeout,
        interactive,
    ) {
        Ok(code) => ExitCode::from(code),
        Err(_) => ExitCode::from(2),
    }
}

#[derive(PartialEq)]
enum Key {
    Text(char),
    Enter,
    Escape,
    Quit,
    Next,
    Previous,
    Backspace,
    Left,
    Right,
    Home,
    End,
    Delete,
    Clear,
    Other,
}

fn byte(reader: &mut impl Read) -> io::Result<Option<u8>> {
    let mut buffer = [0];
    match reader.read(&mut buffer) {
        Ok(0) => Ok(None),
        Ok(_) => Ok(Some(buffer[0])),
        Err(error) if error.kind() == io::ErrorKind::Interrupted => byte(reader),
        Err(error) => Err(error),
    }
}

fn key(reader: &mut impl Read) -> io::Result<Key> {
    let Some(first) = byte(reader)? else {
        return Ok(Key::Quit);
    };
    Ok(match first {
        b'\r' | b'\n' => Key::Enter,
        3 | 4 => Key::Quit,
        b'\t' => Key::Next,
        8 | 127 => Key::Backspace,
        21 => Key::Clear,
        27 => escape_key(reader)?,
        0..=31 => Key::Other,
        _ => {
            let width = match first {
                0..=127 => 1,
                0xc2..=0xdf => 2,
                0xe0..=0xef => 3,
                0xf0..=0xf4 => 4,
                _ => return Ok(Key::Other),
            };
            let mut bytes = vec![first];
            for _ in 1..width {
                let Some(value) = byte(reader)? else {
                    return Ok(Key::Other);
                };
                bytes.push(value);
            }
            match std::str::from_utf8(&bytes)
                .ok()
                .and_then(|s| s.chars().next())
            {
                Some(value) => Key::Text(value),
                None => Key::Other,
            }
        }
    })
}

fn escape_key(reader: &mut impl Read) -> io::Result<Key> {
    // Only Escape needs a timed read. Normal reads block and preserve EOF.
    fn timing(minimum: &str, time: &str) -> io::Result<()> {
        if Command::new("stty")
            .args(["min", minimum, "time", time])
            .status()?
            .success()
        {
            Ok(())
        } else {
            Err(io::Error::other("Cannot set terminal input timing"))
        }
    }
    timing("0", "1")?;
    let result = (|| {
        Ok(match byte(reader)? {
            None => Key::Escape,
            Some(b'[' | b'O') => {
                let mut sequence = Vec::new();
                while let Some(value) = byte(reader)? {
                    sequence.push(value);
                    if (0x40..=0x7e).contains(&value) || sequence.len() == 16 {
                        break;
                    }
                }
                match sequence.as_slice() {
                    b"A" | b"Z" => Key::Previous,
                    b"B" => Key::Next,
                    b"C" => Key::Right,
                    b"D" => Key::Left,
                    b"H" | b"1~" | b"7~" => Key::Home,
                    b"F" | b"4~" | b"8~" => Key::End,
                    b"3~" => Key::Delete,
                    _ => Key::Other,
                }
            }
            Some(_) => Key::Other,
        })
    })();
    timing("1", "0")?;
    result
}

fn input(
    reader: &mut impl BufRead,
    writer: &mut impl Write,
    prompt: &str,
    interactive: bool,
) -> io::Result<Option<String>> {
    write!(writer, "{prompt}")?;
    writer.flush()?;
    let mut line = String::new();
    if interactive {
        let mut cursor = 0;
        loop {
            match key(reader)? {
                Key::Escape => {
                    writeln!(writer)?;
                    return Ok(Some(":cancel".to_owned()));
                }
                Key::Quit => return Ok(None),
                Key::Enter => {
                    writeln!(writer)?;
                    return Ok(Some(line));
                }
                Key::Backspace => {
                    if cursor > 0 {
                        let previous = line[..cursor].char_indices().last().unwrap().0;
                        line.drain(previous..cursor);
                        cursor = previous;
                    }
                }
                Key::Delete if cursor < line.len() => {
                    line.remove(cursor);
                }
                Key::Left if cursor > 0 => {
                    cursor = line[..cursor].char_indices().last().unwrap().0;
                }
                Key::Right if cursor < line.len() => {
                    cursor += line[cursor..].chars().next().unwrap().len_utf8();
                }
                Key::Home => cursor = 0,
                Key::End => cursor = line.len(),
                Key::Clear => {
                    line.clear();
                    cursor = 0;
                }
                Key::Text(value) => {
                    line.insert(cursor, value);
                    cursor += value.len_utf8();
                }
                _ => continue,
            }
            write!(
                writer,
                "\r\x1b[2K{prompt}{line}\r{prompt}{}",
                &line[..cursor]
            )?;
            writer.flush()?;
        }
    }
    if reader.read_line(&mut line)? == 0 {
        return Ok(None);
    }
    Ok(Some(line.trim_end_matches(['\r', '\n']).to_owned()))
}

fn command(
    reader: &mut impl BufRead,
    writer: &mut impl Write,
    interactive: bool,
) -> io::Result<Option<String>> {
    if !interactive {
        return input(reader, writer, "settings> ", false);
    }
    write!(writer, "settings> ")?;
    writer.flush()?;
    let mut number = String::new();
    loop {
        let command = match key(reader)? {
            Key::Escape | Key::Quit => "q".to_owned(),
            Key::Enter if number.is_empty() => "e".to_owned(),
            Key::Enter => number,
            Key::Next => "j".to_owned(),
            Key::Previous => "k".to_owned(),
            Key::Text(value) if value.is_ascii_digit() => {
                number.push(value);
                write!(writer, "{value}")?;
                writer.flush()?;
                continue;
            }
            Key::Backspace => {
                if number.pop().is_some() {
                    write!(writer, "\x08 \x08")?;
                    writer.flush()?;
                }
                continue;
            }
            Key::Text(value) => value.to_string(),
            _ => continue,
        };
        writeln!(writer)?;
        return Ok(Some(command));
    }
}

fn menu(
    reader: &mut impl BufRead,
    writer: &mut impl Write,
    root: Option<&Path>,
    timeout: Duration,
    interactive: bool,
) -> io::Result<u8> {
    writeln!(writer, "Emaki settings — draft keyboard menu")?;
    let mut selected = 0;
    loop {
        let list = settings(Operation::List, root, timeout);
        if list.exit_code() != 0 {
            write!(writer, "{}", list.human())?;
            return Ok(list.exit_code());
        }
        for (index, row) in list.settings.iter().enumerate() {
            writeln!(
                writer,
                "{}{} {:2}. {} = {}{}",
                if interactive && index == selected {
                    "\x1b[7m"
                } else {
                    ""
                },
                if index == selected { ">" } else { " " },
                index + 1,
                row.key,
                row.value,
                if interactive && index == selected {
                    "\x1b[0m"
                } else {
                    ""
                }
            )?;
        }
        if interactive {
            writeln!(
                writer,
                "Arrows/Tab/j/k: select; Enter/e: edit; Esc/q: quit; r: reset; h: history; u: undo. Number + Enter: select."
            )?;
        } else {
            writeln!(
                writer,
                "Number: select; j/k: next/previous; e: edit; r: reset; h: history; u: undo; q: quit. Press Enter after each command."
            )?;
        }
        let Some(command) = command(reader, writer, interactive)? else {
            return Ok(0);
        };
        let reply = match command.trim() {
            "q" => return Ok(0),
            "j" => {
                selected = (selected + 1).min(list.settings.len().saturating_sub(1));
                continue;
            }
            "k" => {
                selected = selected.saturating_sub(1);
                continue;
            }
            "e" => {
                let Some(row) = list.settings.get(selected) else {
                    continue;
                };
                writeln!(
                    writer,
                    "{}: {}. Enter value; :cancel returns without changing it.",
                    row.key, row.allowed
                )?;
                if interactive {
                    writeln!(writer, "Esc: cancel; Enter: save.")?;
                }
                let Some(value) = input(reader, writer, "value> ", interactive)? else {
                    return Ok(0);
                };
                if value == ":cancel" {
                    continue;
                }
                settings(
                    Operation::Set {
                        key: row.key,
                        value: &value,
                    },
                    root,
                    timeout,
                )
            }
            "r" => {
                let Some(row) = list.settings.get(selected) else {
                    continue;
                };
                settings(Operation::Reset { key: row.key }, root, timeout)
            }
            "h" => settings(Operation::History, root, timeout),
            "u" => {
                let history = settings(Operation::History, root, timeout);
                write!(writer, "{}", history.human())?;
                if history.exit_code() != 0 {
                    continue;
                }
                let Some(id) = input(
                    reader,
                    writer,
                    "Change ID to undo (empty cancels)> ",
                    interactive,
                )?
                else {
                    return Ok(0);
                };
                if id.trim().is_empty() || id == ":cancel" {
                    continue;
                }
                settings(Operation::Undo { id: id.trim() }, root, timeout)
            }
            "" => continue,
            value => {
                if let Some(index) = value
                    .parse::<usize>()
                    .ok()
                    .filter(|n| *n > 0 && *n <= list.settings.len())
                {
                    selected = index - 1;
                } else {
                    writeln!(
                        writer,
                        "Unknown command. Choose a listed number or command."
                    )?;
                }
                continue;
            }
        };
        write!(writer, "{}", reply.human())?;
    }
}
