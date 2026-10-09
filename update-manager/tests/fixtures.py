# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""C-locale pacman and AUR RPC v5 wire-format fixtures."""
INSTALLED = '''bash 5.3.8-1
linux 6.16.8.arch3-1
emaki-config 0.3.1-2
ffmpeg 2:8.0-1
paru 2.0.4-1
example-local 1-1
'''
AVAILABLE = '''linux 6.16.8.arch3-1 -> 6.17.1.arch1-1
emaki-config 0.3.1-2 -> 0.3.1-3
ffmpeg 2:8.0-1 -> 2:8.0-2 [ignored]
'''
TRANSACTION = '''core|linux|6.17.1.arch1-1|145827301
emaki|emaki-config|0.3.1-3|82153
extra|new-dependency|1:2.0-1|0
'''
AUR = {'version': 5, 'type': 'multiinfo', 'resultcount': 1, 'results': [
    {'ID': 1853887, 'Name': 'paru', 'PackageBaseID': 157722, 'PackageBase': 'paru',
     'Version': '2.1.0-1', 'Description': 'Feature packed AUR helper',
     'URL': 'https://github.com/Morganamilo/paru', 'NumVotes': 698, 'Popularity': 8.1,
     'OutOfDate': None, 'Maintainer': 'Morganamilo', 'FirstSubmitted': 1600174800,
     'LastModified': 1756334308, 'URLPath': '/cgit/aur.git/snapshot/paru.tar.gz',
     'Depends': ['git', 'pacman'], 'License': ['GPL-3.0-or-later']}]}
NEWS = b'''<?xml version="1.0"?><rss version="2.0"><channel><title>Arch Linux: Recent news updates</title>
<item><title>Older manual intervention</title><link>https://archlinux.org/news/older/</link><description>Read this.</description><pubDate>Tue, 01 Sep 2026 10:00:00 +0000</pubDate></item>
<item><title>Package update requires manual intervention</title><link>https://archlinux.org/news/package-update/</link><description>&lt;p&gt;Read this before updating.&lt;/p&gt;</description><pubDate>Tue, 06 Oct 2026 10:00:00 +0000</pubDate></item>
<item><title>Package migration</title><link>https://archlinux.org/news/package-migration/</link><description>&lt;p&gt;This requires &lt;strong&gt;manual intervention&lt;/strong&gt;.&lt;/p&gt;</description><pubDate>Tue, 06 Oct 2026 11:00:00 +0000</pubDate></item>
<item><title>Mirror news</title><link>https://archlinux.org/news/mirrors/</link><description>New mirrors.</description><pubDate>Tue, 06 Oct 2026 11:00:00 +0000</pubDate></item>
</channel></rss>'''
LOG = '''[2026-09-01T12:00:00+0000] [PACMAN] Running 'pacman -Syu'
[2026-09-01T12:01:00+0000] [ALPM] transaction completed
[2026-10-01T12:00:00+0000] [PACMAN] Running '/usr/bin/pacman -Syu --noconfirm'
[2026-10-01T12:02:00+0000] [ALPM] transaction completed
[2026-10-02T12:00:00+0000] [PACMAN] Running 'pacman -S bash'
[2026-10-02T12:01:00+0000] [ALPM] transaction completed
[2026-10-03T12:00:00+0000] [PACMAN] Running 'pacman -Syu'
'''
