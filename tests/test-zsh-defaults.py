#!/usr/bin/env python3
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""The packaged zsh defaults (zsh/emaki.zsh, zsh/zshenv) in a real interactive zsh.

Each shell runs on a pseudo-terminal with a temporary home. Where unprivileged namespaces
work, the repository's files are bind-mounted over /etc/zsh in a private mount namespace (the
host's files are never touched), so zsh reads them exactly as installed and its newuser module
runs as it would for a new account. Elsewhere the files are sourced into `zsh -f` and the
first-run part is skipped with a message. Nothing is written outside the temporary directory.
"""
import os
from pathlib import Path
import pty
import select
import shutil
import subprocess
import tempfile
import time
import unittest

ROOT = Path(__file__).resolve().parent.parent
ZSH = shutil.which('zsh')
QUESTIONNAIRE = 'This is the Z Shell configuration function for new users'
REPORT = (
    'print -r -- "prompt=$PROMPT"; '
    'for key in "^ " "^[[C" "^[[A" "^[OA" "^[[B" "^[OB" "^[[Z"; do bindkey "$key"; done; '
    'zstyle -L ":completion:*"; '
    'print -r -- "functions=${+functions[_zsh_autosuggest_start]}${+functions[_zsh_highlight]}'
    '${+functions[zsh-newuser-install]}${+functions[_emaki_zsh_plugin]}"; '
    'print -r -- "options=${options[sharehistory]} ${options[histignoredups]} ${options[appendhistory]}"; '
    'print -r -- "history=$HISTFILE $HISTSIZE $SAVEHIST"; '
    'print -r -- "suggestion=$ZSH_AUTOSUGGEST_HIGHLIGHT_STYLE"; '
    'print -r -- "keymap=${$(bindkey -lL main)}"'
)


def plugins_on_host():
    dirs = [d for d in os.environ.get('XDG_DATA_DIRS', '/usr/local/share:/usr/share').split(':') if d.startswith('/')]
    return all(any(Path(d, 'zsh/plugins', name, name + '.zsh').is_file() for d in dirs)
               for name in ('zsh-autosuggestions', 'zsh-syntax-highlighting'))


def namespaces_work():
    try:
        with tempfile.TemporaryDirectory(prefix='zsh-ns-') as directory:
            result = subprocess.run(
                ['unshare', '-rm', 'sh', '-c', 'mount --bind "$1" "$1" && exec unshare --map-user=%d '
                 '--map-group=%d true' % (os.getuid(), os.getgid()), '_', directory],
                capture_output=True, timeout=10)
        return result.returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


NAMESPACES = namespaces_work() and not os.environ.get('EMAKI_TEST_ZSH_NO_NAMESPACE')


class ZshDefaults(unittest.TestCase):
    def setUp(self):
        if ZSH is None:
            self.skipTest('zsh is not installed')
        self.base = Path(tempfile.mkdtemp(prefix='zsh-defaults-'))
        self.addCleanup(shutil.rmtree, self.base)
        self.home = self.base / 'home'
        self.home.mkdir()
        self.etc = self.base / 'etc-zsh'
        self.etc.mkdir()
        # The host's own zprofile (Arch's sources /etc/profile), so login order stays real.
        if Path('/etc/zsh/zprofile').is_file():
            shutil.copyfile('/etc/zsh/zprofile', self.etc / 'zprofile')

    def install(self, zshenv=True, zshrc=True):
        for name, wanted in (('zshenv', zshenv), ('emaki.zsh', zshrc)):
            if wanted:
                shutil.copyfile(ROOT / 'zsh' / name, self.etc / name)
                if name == 'zshenv' and not NAMESPACES:
                    path = self.etc / name
                    path.write_text(path.read_text().replace('/etc/zsh/', str(self.etc) + '/'))

    def environment(self, **extra):
        env = {'HOME': str(self.home), 'PATH': os.environ.get('PATH', '/usr/bin:/bin'),
               'TERM': 'xterm-256color', 'LANG': 'C.UTF-8', 'TMPDIR': str(self.base)}
        if 'XDG_DATA_DIRS' in os.environ:
            env['XDG_DATA_DIRS'] = os.environ['XDG_DATA_DIRS']
        env.update(extra)
        return {key: value for key, value in env.items() if value is not None}

    def command(self, interactive=True, script=None):
        flags = ['-i'] if interactive else []
        if script is not None:
            flags += ['-c', script]
        if NAMESPACES:
            inner = 'mount --bind "$1" /etc/zsh && shift && exec unshare --map-user=%d --map-group=%d "$@"' % (
                os.getuid(), os.getgid())
            return ['unshare', '-rm', 'sh', '-c', inner, '_', str(self.etc), ZSH, *flags]
        # Without namespaces: no global files at all; the test sources the repository's copies.
        return [ZSH, '-f', *flags]

    def interactive(self, lines, env=None, wait_for_questionnaire=False):
        """Run an interactive zsh on a 24x80 terminal; return everything it printed."""
        env = env or self.environment()
        if not NAMESPACES:
            # Keep synthetic startup in one command, so precmd cannot run between
            # startup files. Restore RCS only after -f has bypassed host startup.
            startup = ['setopt rcs']
            if (self.etc / 'zshenv').exists():
                startup += [f'source {self.etc}/zshenv',
                            '(( ${+functions[zsh-newuser-install]} )) && zsh-newuser-install']
            startup.append('[[ -e ~/.zshenv ]] && source ~/.zshenv')
            if (self.etc / 'zshrc').exists():
                startup.append(f'source {self.etc}/zshrc')
            startup.append('[[ -e ${ZDOTDIR:-$HOME}/.zshrc ]] && source ${ZDOTDIR:-$HOME}/.zshrc')
            lines = ['; '.join(startup)] + lines
        pid, fd = pty.fork()
        if pid == 0:
            os.chdir(self.home)
            os.execvpe(self.command()[0], self.command(), env)
        try:
            import fcntl
            import struct
            import termios
            fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack('HHHH', 24, 80, 0, 0))
            output = b''
            deadline = time.monotonic() + 15
            # The shell is ready at its first prompt (or the questionnaire's key prompt).
            while time.monotonic() < deadline:
                text = output.decode(errors='replace')
                if QUESTIONNAIRE in text and 'Type one of the keys' in text:
                    break
                if '%' in text and time.monotonic() > deadline - 13:
                    break
                ready, _, _ = select.select([fd], [], [], 0.2)
                if ready:
                    try:
                        output += os.read(fd, 65536)
                    except OSError:
                        break
            if QUESTIONNAIRE in output.decode(errors='replace'):
                os.write(fd, b'q')
                time.sleep(0.5)
            for line in lines + ['exit']:
                os.write(fd, line.encode() + b'\r')
            deadline = time.monotonic() + 15
            while time.monotonic() < deadline:
                ready, _, _ = select.select([fd], [], [], 0.2)
                if ready:
                    try:
                        chunk = os.read(fd, 65536)
                    except OSError:
                        break
                    if not chunk:
                        break
                    output += chunk
            return output.decode(errors='replace')
        finally:
            os.close(fd)
            for _ in range(50):
                done, _ = os.waitpid(pid, os.WNOHANG)
                if done:
                    break
                time.sleep(0.1)
            else:
                os.kill(pid, 9)
                os.waitpid(pid, 0)

    def report(self, env=None):
        out = self.base / 'report.txt'
        self.interactive([f'{{ {REPORT} }} > {out} 2>&1'], env=env)
        self.assertTrue(out.is_file(), 'the shell never ran the report command')
        return dict(line.split('=', 1) if '=' in line and not line.startswith('"') else (line, '')
                    for line in out.read_text().splitlines()), out.read_text()

    def written_outside_home(self):
        return sorted(str(p.relative_to(self.base)) for p in self.base.rglob('*')
                      if not p.is_relative_to(self.home) and not p.is_relative_to(self.etc)
                      and p.name != 'report.txt')

    def test_package_coexists_with_other_global_zshrc(self):
        metadata = (ROOT / 'packaging/emaki-config/PKGBUILD').read_text()
        conflicts = next(line for line in metadata.splitlines() if line.startswith('conflicts='))
        self.assertNotIn('grml-zsh-config', conflicts)
        inventory = (ROOT / 'packaging/emaki-config/expected-files.list').read_text().splitlines()
        self.assertNotIn('/etc/zsh/zshrc', inventory)
        self.assertIn('/etc/zsh/emaki.zsh', inventory)

    def test_install_preserves_another_packages_zshrc(self):
        # A caller can supply the actual grml file for the integration check; the
        # ordinary regression still proves that staging never writes that pathname.
        fixture = os.environ.get('EMAKI_TEST_GRML_ZSHRC')
        original = Path(fixture).read_bytes() if fixture else b"# Existing global configuration\n"
        stage = self.base / 'stage'
        target = stage / 'etc/zsh/zshrc'
        target.parent.mkdir(parents=True)
        target.write_bytes(original)
        plan = subprocess.run(['make', '-n', 'install', f'DESTDIR={stage}'], cwd=ROOT,
                              capture_output=True, text=True, check=True).stdout
        commands = [line for line in plan.splitlines() if line.startswith('install ') and ' zsh/' in line]
        self.assertGreaterEqual(len(commands), 2)
        for command in commands:
            subprocess.run(command, shell=True, cwd=ROOT, check=True)
        self.assertEqual(target.read_bytes(), original)
        self.assertTrue((stage / 'etc/zsh/emaki.zsh').is_file())
        if fixture:
            shutil.copyfile(target, self.etc / 'zshrc')
            own = "PROMPT='personal> '\n"
            (self.home / '.zshrc').write_text(own)
            baseline, _ = self.report()
            self.install()
            values, _ = self.report()
            self.assertEqual(values, baseline)
            self.assertEqual((self.home / '.zshrc').read_text(), own)
            self.assertEqual((self.etc / 'zshrc').read_bytes(), original)

    def test_personal_zshenv_can_disable_global_defaults(self):
        self.install()
        for option in ('rcs', 'globalrcs'):
            with self.subTest(option=option):
                (self.home / '.zshenv').write_text(f'unsetopt {option}\n')
                values, text = self.report()
                self.assertNotEqual(values['prompt'], '%F{cyan}%~%f %# ')
                self.assertEqual(values['functions'], '0000')
                self.assertNotIn('menu select', text)
                self.assertFalse((self.home / '.cache').exists())

    def test_personal_zdotdir_selected_in_zshenv_is_respected(self):
        self.install()
        custom = self.home / 'settings'
        custom.mkdir()
        own = "PROMPT='custom> '\n"
        (custom / '.zshrc').write_text(own)
        (self.home / '.zshenv').write_text('ZDOTDIR=$HOME/settings\n')
        values, text = self.report()
        self.assertEqual(values['prompt'], 'custom> ')
        self.assertEqual(values['functions'], '0000')
        self.assertNotIn('menu select', text)
        self.assertEqual((custom / '.zshrc').read_text(), own)
        self.assertFalse((self.home / '.cache').exists())

    def test_new_account_gets_the_defaults(self):
        self.install()
        values, text = self.report(self.environment(TERM='xterm-kitty'))
        self.assertEqual(values['prompt'], '%F{cyan}%~%f %# ')
        self.assertEqual(values['options'], 'on on on')
        self.assertEqual(values['history'], f'{self.home}/.zsh_history 10000 10000')
        self.assertIn('emacs', values['keymap'], text)  # Emacs keys
        for binding in ('"^[[A" up-line-or-beginning-search', '"^[OA" up-line-or-beginning-search',
                        '"^[[B" down-line-or-beginning-search', '"^[OB" down-line-or-beginning-search',
                        '"^[[Z" reverse-menu-complete'):
            self.assertIn(binding, text)
        self.assertIn("zstyle ':completion:*' menu select", text)
        self.assertIn("zstyle ':completion:*' matcher-list 'm:{a-zA-Z}={A-Za-z}'", text)
        # The completion cache goes to the cache directory; nothing else lands in the home.
        self.assertTrue(list((self.home / '.cache/zsh').glob('zcompdump-*')))
        self.assertFalse(list(self.home.glob('.zcompdump*')))
        self.assertEqual(self.written_outside_home(), [])
        self.assertEqual(values['functions'][2:], '00', 'helpers or the first-run stub stayed defined')
        if plugins_on_host():
            self.assertEqual(values['functions'][:2], '11', 'a plugin was not loaded')
            self.assertEqual(values['suggestion'], 'fg=7')
            self.assertIn('"^@" autosuggest-accept', text)  # Ctrl+Space
            self.assertIn('"^[[C" forward-char', text)
        else:
            print('SKIP plugin checks: zsh-autosuggestions or zsh-syntax-highlighting not installed')

    def test_suggestion_style_follows_terminal(self):
        if not plugins_on_host():
            self.skipTest('zsh plugins are not installed')
        self.install()
        for terminal, style in (('xterm-kitty', 'fg=7'), ('linux', 'fg=black,bold'),
                                ('xterm', 'fg=black,bold'), ('dumb', 'fg=black,bold'),
                                (None, 'fg=black,bold'), ('', 'fg=black,bold'),
                                ('xterm-256color', 'fg=8')):
            with self.subTest(terminal=terminal):
                values, _ = self.report(self.environment(TERM=terminal))
                self.assertEqual(values['functions'][:2], '11', 'a plugin was not loaded')
                self.assertEqual(values['suggestion'], style)

    def test_suggestion_drawn_bytes_follow_terminal(self):
        if not plugins_on_host():
            self.skipTest('zsh plugins are not installed')
        # Load the defaults at startup, before zsh reads history and draws its first prompt.
        (self.home / '.zshenv').write_text(
            f'source {ROOT}/zsh/emaki.zsh\nPROMPT="suggestion-ready> "\n')
        # dumb and absent TERM have no bold capability; zsh still emits black foreground.
        for terminal, colour in (('linux', b'\x1b[1m\x1b[30m'),
                                 ('xterm', b'\x1b[1m\x1b[30m'),
                                 ('dumb', b'\x1b[30m'),
                                 (None, b'\x1b[30m'),
                                 ('', b'\x1b[30m'),
                                 ('xterm-kitty', b'\x1b[37m'),
                                 ('xterm-256color', b'\x1b[90m')):
            with self.subTest(terminal=terminal):
                (self.home / '.zsh_history').write_text('echo previous-session\n')
                pid, fd = pty.fork()
                if pid == 0:
                    os.chdir(self.home)
                    os.execve(ZSH, [ZSH, '-d', '-i'], self.environment(
                        TERM=terminal, ZDOTDIR=str(self.home)))
                try:
                    import fcntl
                    import struct
                    import termios
                    fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack('HHHH', 24, 80, 0, 0))

                    def read_until(marker):
                        output = b''
                        deadline = time.monotonic() + 10
                        while marker not in output and time.monotonic() < deadline:
                            ready, _, _ = select.select([fd], [], [], 0.1)
                            if ready:
                                try:
                                    chunk = os.read(fd, 65536)
                                except OSError:
                                    break
                                if not chunk:
                                    break
                                output += chunk
                        self.assertIn(marker, output, 'shell did not draw the expected text')
                        return output

                    read_until(b'suggestion-ready> ')
                    os.write(fd, b'ech')
                    output = read_until(b'o previous-session')
                    self.assertIn(colour + b'o previous-session', output)
                finally:
                    os.close(fd)
                    for _ in range(20):
                        done, _ = os.waitpid(pid, os.WNOHANG)
                        if done:
                            break
                        time.sleep(0.05)
                    else:
                        os.kill(pid, 9)
                        os.waitpid(pid, 0)

    def test_existing_history_is_available_at_first_prompt(self):
        self.install()
        (self.home / '.zsh_history').write_text('echo previous-session\n')
        out = self.base / 'history.txt'
        self.interactive([f'fc -l 1 > {out}'])
        self.assertIn('echo previous-session', out.read_text())

    def test_cache_directory_follows_xdg(self):
        self.install()
        cache = self.base / 'cache'
        self.report(self.environment(XDG_CACHE_HOME=str(cache)))
        self.assertTrue(list((cache / 'zsh').glob('zcompdump-*')))
        self.assertFalse((self.home / '.cache').exists())

    def test_missing_plugins_are_skipped_quietly(self):
        self.install()
        empty = self.base / 'data'
        empty.mkdir()
        values, text = self.report(self.environment(XDG_DATA_DIRS=str(empty)))
        self.assertEqual(values['functions'], '0000')
        self.assertNotIn('autosuggest-accept', text)
        self.assertNotIn('no such file', text.lower())
        self.assertEqual(values['prompt'], '%F{cyan}%~%f %# ')

    def test_own_zshrc_gets_nothing_from_the_defaults(self):
        self.install()
        (self.home / '.zshrc').write_text("PROMPT='own> '\n")
        values, text = self.report()
        self.assertEqual(values['prompt'], 'own> ')
        self.assertEqual(values['functions'], '0000')
        self.assertNotIn('up-line-or-beginning-search', text)
        self.assertNotIn('menu select', text)
        self.assertFalse((self.home / '.cache').exists())

    def test_dangling_personal_zshrc_is_not_replaced_by_defaults(self):
        self.install()
        (self.home / '.zshrc').symlink_to('unavailable-config')
        values, text = self.report()
        self.assertNotEqual(values['prompt'], '%F{cyan}%~%f %# ')
        self.assertEqual(values['functions'], '0000')
        self.assertNotIn('menu select', text)
        self.assertFalse((self.home / '.cache').exists())
        self.assertEqual(os.readlink(self.home / '.zshrc'), 'unavailable-config')

    def test_no_first_run_questionnaire(self):
        if not NAMESPACES:
            self.skipTest('unprivileged namespaces are unavailable: the newuser module cannot be reached')
        # Without Emaki's zshenv a new account meets zsh's questionnaire (the proof that it runs here).
        self.install(zshenv=False)
        self.assertIn(QUESTIONNAIRE, self.interactive(['true']))
        self.home.joinpath('.zshrc').unlink(missing_ok=True)
        self.install()
        output = self.interactive(['true'])
        self.assertNotIn(QUESTIONNAIRE, output)
        self.assertEqual(sorted(p.name for p in self.home.iterdir()), ['.cache', '.zsh_history'])

    def test_kitty_shell_integration_is_loaded(self):
        # kitty injects its integration only into a zsh whose home has startup files; without it
        # kitty asks before closing every window (it cannot see the prompt).
        kitty = Path('/usr/lib/kitty')
        if not (kitty / 'shell-integration/zsh/kitty-integration').is_file():
            self.skipTest("kitty's shell integration is not installed here")
        self.install()
        out = self.base / 'ksi.txt'
        probe = [f'print -r -- ${{+_ksi_state}} > {out}']
        for env, expected in ((self.environment(KITTY_INSTALLATION_DIR=str(kitty), KITTY_SHELL_INTEGRATION='enabled'), '1'),
                              (self.environment(KITTY_INSTALLATION_DIR=str(kitty)), '0'),   # turned off in kitty.conf
                              (self.environment(), '0')):                                   # not in kitty
            with self.subTest(expected=expected, kitty='KITTY_INSTALLATION_DIR' in env):
                self.interactive(probe, env=env)
                self.assertEqual(out.read_text(), expected + '\n')
        self.assertEqual(sorted(p.name for p in self.base.iterdir() if p.name not in ('home', 'etc-zsh')), ['ksi.txt'])

    def test_kitty_marks_the_first_prompt(self):
        kitty = Path('/usr/lib/kitty')
        if not (kitty / 'shell-integration/zsh/kitty-integration').is_file():
            self.skipTest("kitty's shell integration is not installed here")
        self.install()
        # Exit as the first command: a second prompt must not hide delayed initialization.
        output = self.interactive([], env=self.environment(
            KITTY_INSTALLATION_DIR=str(kitty), KITTY_SHELL_INTEGRATION='enabled'))
        self.assertIn('\x1b]133;A', output)

    def test_zshenv_is_silent_for_scripts(self):
        if not NAMESPACES:
            self.skipTest('unprivileged namespaces are unavailable')
        self.install()
        result = subprocess.run(self.command(interactive=False, script='print -r -- ${+functions[zsh-newuser-install]}'),
                                env=self.environment(), capture_output=True, text=True, timeout=20, cwd=self.home)
        self.assertEqual((result.returncode, result.stdout, result.stderr), (0, '0\n', ''))
        self.assertEqual(list(self.home.iterdir()), [])


if __name__ == '__main__':
    unittest.main()
