# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
#
# Emaki's interactive zsh defaults, installed by emaki-config as /etc/zsh/emaki.zsh.
# They apply only to a person without their own ~/.zshrc (or $ZDOTDIR/.zshrc): whoever
# writes one gets exactly that file and nothing from here. To start your own from these
# defaults: cp /etc/zsh/emaki.zsh ~/.zshrc, then delete the first `return` line below.
[[ -e ${ZDOTDIR:-$HOME}/.zshrc || -L ${ZDOTDIR:-$HOME}/.zshrc ]] && return

# History: shared between open terminals, a repeated command is kept once in a row.
HISTFILE=${HISTFILE:-$HOME/.zsh_history}
HISTSIZE=10000
SAVEHIST=10000
setopt SHARE_HISTORY HIST_IGNORE_DUPS APPEND_HISTORY

# Completion: Tab opens a menu walked with the arrow keys; lower and upper case match.
# The completion cache lives in the cache directory, not in the home directory itself.
autoload -Uz compinit
() {
    local cache=${XDG_CACHE_HOME:-$HOME/.cache}/zsh
    if [[ -d $cache ]] || mkdir -p -m 700 -- $cache 2>/dev/null; then
        compinit -d $cache/zcompdump-$ZSH_VERSION
    else
        compinit -D
    fi
}
zstyle ':completion:*' menu select
zstyle ':completion:*' matcher-list 'm:{a-zA-Z}={A-Za-z}'
zstyle ':completion:*' list-colors "${(s.:.)LS_COLORS}"

# Emacs keys; Shift+Tab walks the completion menu backwards.
bindkey -e
bindkey '^[[Z' reverse-menu-complete

# A plugin is found in the data directories, in XDG_DATA_DIRS order; a missing one is skipped.
_emaki_zsh_plugin() {
    reply=(${^${(M)${(s.:.)${XDG_DATA_DIRS:-/usr/local/share:/usr/share}}:#/*}}/zsh/plugins/$1/$1.zsh(N-.r))
    (( $#reply ))
}

# Suggestions from history in grey as you type: Ctrl+Space takes the whole suggestion, and so
# does the Right arrow at the end of the line (the plugin's default for forward-char).
if _emaki_zsh_plugin zsh-autosuggestions; then
    # Use kitty's muted foreground; low-colour terminals need bold black for dark grey.
    zmodload zsh/terminfo
    if [[ $TERM == xterm-kitty ]]; then
        ZSH_AUTOSUGGEST_HIGHLIGHT_STYLE='fg=7'
    elif (( ${terminfo[colors]:-0} < 16 )); then
        ZSH_AUTOSUGGEST_HIGHLIGHT_STYLE='fg=black,bold'
    fi
    source $reply[1]
    bindkey '^[[C' forward-char
    bindkey '^ ' autosuggest-accept
fi

# Up and Down search the history for lines starting with what is already typed.
autoload -Uz up-line-or-beginning-search down-line-or-beginning-search colors
zle -N up-line-or-beginning-search
zle -N down-line-or-beginning-search
bindkey '^[[A' up-line-or-beginning-search
bindkey '^[OA' up-line-or-beginning-search
bindkey '^[[B' down-line-or-beginning-search
bindkey '^[OB' down-line-or-beginning-search

colors
PROMPT='%F{cyan}%~%f %# '

# kitty leaves its shell integration out of a zsh whose home has no startup files (to let zsh's
# first-run questionnaire run), and without it kitty cannot tell the prompt from a running
# program: it would ask before closing every window. Loaded here the way kitty's manual
# integration does it; kitty's loader skips a second load.
if [[ -n ${KITTY_INSTALLATION_DIR-} && -n ${KITTY_SHELL_INTEGRATION-} &&
      -r $KITTY_INSTALLATION_DIR/shell-integration/zsh/kitty-integration ]]; then
    autoload -Uz -- $KITTY_INSTALLATION_DIR/shell-integration/zsh/kitty-integration
    kitty-integration
    unfunction kitty-integration
fi

# Command colouring goes last: it wraps the line editor's widgets defined above.
if _emaki_zsh_plugin zsh-syntax-highlighting; then
    source $reply[1]
fi
unfunction _emaki_zsh_plugin
unset reply
