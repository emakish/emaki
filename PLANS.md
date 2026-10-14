# Where Emaki is heading

Hi. I'm Artur, and I make Emaki.

Emaki is in alpha, and it already works well. It installs, also next to Windows. It updates and
rolls back when something goes wrong. Programs & Updates installs programs from Arch, Flathub and
the AUR. The shell has a light and a dark theme, accent colours, and works with the keyboard. It is
good-looking, easy to use, and a pleasure every day.

There are still small rough edges. But if I polished every one of them, Emaki would never come out.
So what is left before it goes to people is short: Secure Boot, a few small things, and a website
where you can download the installation image and see what is new in each update.

Then a full check of the whole system: installing, updating, hardware, security, speed, the look,
every text and every shortcut. What it finds gets fixed or consciously left for later.

There is one real problem: hardware. Emaki has only been installed on my own machines. I don't
know how it behaves on others, with a different graphics card, different Wi-Fi, a different
screen. So after the check Emaki goes to people, still as an alpha, with one request: help.
Install Emaki, use it, and tell me what broke.

Emaki becomes a beta when it has been tested on enough different hardware.

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
them, how to roll back. One clear Emaki prompt asks you before anything runs as administrator,
showing who is asking and exactly what will run; you answer with your password. Snapguard, my tool
that takes a snapshot before risky actions, is on the From Emaki shelf in Programs & Updates, and an
optional sudo key lets an agent work without asking for a while — at your own risk, with a plain
warning.

These are not promises, just my thoughts on where we are going. This text will change along with
the project.

— Artur
