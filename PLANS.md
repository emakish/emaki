# Where Emaki is heading

Hi. I'm Artur, and I make Emaki.

Emaki is in alpha. On my computers and in virtual machines it already works the way I want: it
installs, updates, rolls back when something goes wrong, and it is a pleasure to use every day.

But there is a real problem: hardware. Emaki has only been installed on my own machines. I don't
know how it behaves on others, with a different graphics card, different Wi-Fi, a different
screen.

So the plan is this. I take Emaki to late alpha, when it has everything 1.0 needs. Then I will
quietly step into the light with one request: help. Install Emaki on a spare machine and tell me
what broke.

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

And my goal for the future: our own Wayland compositor that turns the way we work with windows
upside down. That comes later; Emaki comes first.

These are not promises, just my thoughts on where we are going. This text will change along with
the project.

— Artur
