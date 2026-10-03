#!/usr/bin/env python3
"""VM ONLY, root: real uinput keyboard. Text comes from stdin, never password argv.

  printf arch | sudo python3 tests/vm/guest-keys.py text key:Return
  sudo python3 tests/vm/guest-keys.py key:Caps_Lock key:Escape
  printf арх | sudo python3 tests/vm/guest-keys.py layout:ru text
`layout:us|ru` selects the text-to-key mapping, not the desktop layout. Use the
session shortcut `chord:Super_L+space` to switch layouts; niri must have us,ru.
Requires python-evdev, already used by guest-pointer.py.
"""
import sys
import time
from evdev import UInput, ecodes as e

ALIASES = {'Return':'ENTER', 'BackSpace':'BACKSPACE', 'Escape':'ESC', 'Caps_Lock':'CAPSLOCK',
           'Alt_L':'LEFTALT', 'Shift_L':'LEFTSHIFT', 'Control_L':'LEFTCTRL', 'Super_L':'LEFTMETA',
           'Tab':'TAB', 'space':'SPACE', 'Right':'RIGHT', 'Left':'LEFT', 'Delete':'DELETE'}
US = dict(zip('abcdefghijklmnopqrstuvwxyz', 'ABCDEFGHIJKLMNOPQRSTUVWXYZ'))
US.update(dict(zip('0123456789', '0123456789')))
US.update({' ':'SPACE', '-':'MINUS', '=':'EQUAL', '[':'LEFTBRACE', ']':'RIGHTBRACE',
           ';':'SEMICOLON', "'":'APOSTROPHE', ',':'COMMA', '.':'DOT', '/':'SLASH', '\\':'BACKSLASH', '`':'GRAVE'})
SHIFT = dict(zip(')!@#$%^&*(', '0123456789'))
SHIFT.update(dict(zip('_+{}:"<>?|~', '-=[];\',./\\`')))
RU = dict(zip('йцукенгшщзхъфывапролджэячсмитьбюё',
              ['Q','W','E','R','T','Y','U','I','O','P','LEFTBRACE','RIGHTBRACE','A','S','D','F','G','H','J','K','L','SEMICOLON','APOSTROPHE','Z','X','C','V','B','N','M','COMMA','DOT','GRAVE']))


def keycode(name):
    return getattr(e, 'KEY_' + ALIASES.get(name, name.upper()))


def emit(ui, codes):
    for code in codes: ui.write(e.EV_KEY, code, 1)
    ui.syn(); time.sleep(.035)
    for code in reversed(codes): ui.write(e.EV_KEY, code, 0)
    ui.syn(); time.sleep(.035)


def main():
    steps = sys.argv[1:]
    text = sys.stdin.read() if 'text' in steps else ''
    mapping = 'us'
    with UInput({e.EV_KEY: list(range(e.KEY_ESC, e.KEY_MICMUTE + 1))}, name='emaki-test-keyboard', bustype=e.BUS_USB) as ui:
        time.sleep(1.5)
        for step in steps:
            kind, _, value = step.partition(':')
            if kind == 'wait': time.sleep(float(value))
            elif kind == 'layout':
                if value not in ('us', 'ru'): raise ValueError('layout must be us or ru')
                mapping = value
            elif kind in ('key', 'chord'): emit(ui, [keycode(k) for k in value.split('+')])
            elif kind == 'text':
                for char in text:
                    if char == '\n': emit(ui, [e.KEY_ENTER]); continue
                    source = RU if mapping == 'ru' and char.lower() in RU else US
                    shifted = char.isupper() or char in SHIFT
                    base = SHIFT.get(char, char.lower())
                    if base not in source: raise ValueError('unsupported character for selected layout')
                    emit(ui, ([e.KEY_LEFTSHIFT] if shifted else []) + [keycode(source[base])])
            else: raise ValueError('unknown keyboard operation')
        time.sleep(.2)


if __name__ == '__main__': main()
