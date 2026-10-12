# Where Emaki is heading

Hi. I'm Artur, and I make Emaki.

Emaki is in alpha. On my computers and in virtual machines it already works the way I want: it
installs, updates, rolls back when something goes wrong, and it is a pleasure to use every day.

But there is a real problem: hardware. Emaki has only been installed on my own machines. I don't
know how it behaves on others, with a different graphics card, different Wi-Fi, a different
screen.

So the plan is this. First, late alpha. For me that means:

- a terminal that is properly set up, not a bare black window;
- one program for installing programs and updating the system, in a window;
- settings finished inside the shell;
- a polished shell and a polished boot menu;
- an installer that simply works, including next to Windows;
- animated wallpapers;
- a "Report a problem" button;
- keyboard shortcuts that make sense.

Then, before anyone else installs it, a full check of the whole system: not only the code, but
installing, updating, hardware, security, speed, the look, every text and every shortcut.
Everything it finds gets fixed or consciously left for later, and the rough edges get polished.

Only then will I quietly step into the light with one request: help. Install Emaki, use it, and
tell me what broke.

Emaki becomes a beta when other people use it on their own computers. That is when I will talk
about it out loud.

If everything goes well, there will be places to gather: Reddit, Discord and more.

Why Arch, and what about NixOS. Emaki is built on Arch Linux because Arch is what I use every day.
For a long time I wanted to move to NixOS, but when the idea of Emaki came, I understood that Emaki
cannot be a distribution built on NixOS. NixOS is declarative: the whole system is described in
configuration files. That is its strength, but Emaki promises a system you never have to configure
by hand. As a port, though, it fits well: you install ordinary NixOS, add one line to its
configuration and get Emaki. That is one of the plans for later, when Emaki on Arch is out of beta
and stable. Until then, everything new in Emaki is designed so it can be carried over. More:
[NIXOS.md](NIXOS.md).

AI assistants, deep and safe. More and more people let AI agents like Claude Code or Codex run
commands on their computers. On most systems that means one of two things: keep the agent away from
anything important, or let it do everything without asking. Emaki is being built for a third way: an
agent may work deep in the system, up to administrator rights, with a safety net at every step. Emaki
will describe itself to agents: where things are, which files belong to whom, how to check and change
them, how to roll back. One clear Emaki prompt will ask you before anything runs as administrator,
showing who is asking and exactly what will run; you answer with your password.
On btrfs, snapguard, my tool that takes a snapshot before risky actions, becomes part of Emaki. And
for those who want it, an optional sudo key lets an agent work without asking for a while — at your
own risk, with a plain warning. The first parts arrived in 0.5 and 0.6.

AUR in Programs & Updates. Programs & Updates is not a store of its own: it does what you would do in a terminal, only without the
terminal. That is why the AUR will be there too, with full install and update. But anyone can publish to the AUR and nobody checks it,
so it gets its own section that opens only after a plain warning, and you confirm that what you install from it is your decision and
your responsibility. The risks are the same as in a terminal; Emaki only makes them visible.

These are not promises, just my thoughts on where we are going. This text will change along with
the project.

— Artur
