"""Token-aware colourisation for the CLI's reasoning/thinking text.

Thinking text used to be painted with one uniform ``_DIM`` sequence, so an order
mark, a log stamp, a PR number and ordinary prose were indistinguishable in the
scrollback. This module splits a line of thinking text into spans and gives each
class its own foreground: prose stays in the terminal's Homebrew green, order
marks go orange (as does a ``-`` list bullet right before a shell command), and
log stamps / PR numbers / URLs go white.

Every rule is deliberately narrow so the lookalikes stay prose: the order-mark
rule needs trailing whitespace, at most two digits and no quantity prefix
(``3.14``, ``v1.0.1``, ``2.5 hours`` and ``~570.`` do not match), the PR rule
needs six digits and a word boundary (``#12345`` and ``#123456x`` do not
match), the command-bullet rule needs
the word after the dash to be a known shell command (``- git``, ``- hermes`` —
not ``- checkout`` or a mid-line hyphen), and a URL span ends before the
sentence punctuation that hugs it.

The palette is configurable per class via ``display.thinking_colors`` (hex
``#RRGGBB``, ``hermes config set display.thinking_colors.main '#FF0000'``);
each key falls back to its spec default independently, so a bad or missing
value never breaks rendering.

:class:`split_safe_stream` is the streaming half: a token that has only partly
arrived renders as plain prose rather than guessing, and the live box cuts its
buffer before the oldest such fragment so no token is ever painted twice under
two different colours.
"""

from __future__ import annotations

import re

# Homebrew Terminal profile TextColor (NSRGB 0.1569 / 0.996 / 0.0784).
SHOWCASE_GREEN = "#28FE14"
SHOWCASE_ORANGE = "#FF9F0A"
SHOWCASE_WHITE = "#FFFFFF"

# Per-class defaults (the showcase palette). ``display.thinking_colors`` in the
# live CLI config overrides these per key; an invalid or missing value falls
# back to that key's default only, so rendering never breaks on a bad value.
_THINKING_HEX_DEFAULTS = {
    "main": SHOWCASE_GREEN,  # prose
    "order": SHOWCASE_ORANGE,  # 1. 2. 3. order marks
    "cmd": SHOWCASE_ORANGE,  # "-" list bullets before shell commands
    "log": SHOWCASE_WHITE,  # §[2026-10-02] log marks
    "pr": SHOWCASE_WHITE,  # #123456 PR numbers (6+ digits)
    "url": SHOWCASE_WHITE,  # https://… (matched through the #fragment)
}
_HEX_COLOR_RE = re.compile(r"^\s*#[0-9a-fA-F]{6}\s*$")


def _sgr(hex_color: str) -> str:
    """True-colour SGR sequence for '#RRGGBB'."""
    red, green, blue = (int(hex_color[i:i + 2], 16) for i in (1, 3, 5))
    return f"\x1b[38;2;{red};{green};{blue}m"


_GREEN = _sgr(SHOWCASE_GREEN)
_ORANGE = _sgr(SHOWCASE_ORANGE)
_WHITE = _sgr(SHOWCASE_WHITE)
_RST = "\x1b[0m"


def _thinking_config_overrides() -> dict:
    """``display.thinking_colors`` from the live CLI config; {} when unset/unreachable."""
    try:
        from cli import CLI_CONFIG

        overrides = (CLI_CONFIG or {}).get("display", {}).get("thinking_colors", {})
        if isinstance(overrides, dict):
            return overrides
    except Exception:
        pass
    return {}


def resolve_showcase_colors() -> dict[str, str]:
    """The showcase ANSI codes: config overrides over the spec defaults."""
    try:
        overrides = _thinking_config_overrides()
    except Exception:
        overrides = {}
    colors: dict[str, str] = {}
    for key, default in _THINKING_HEX_DEFAULTS.items():
        value = overrides.get(key)
        if isinstance(value, str) and _HEX_COLOR_RE.match(value):
            colors[key] = _sgr(value)
        else:
            colors[key] = _sgr(default)
    return colors


# Shell commands whose "-" list bullets claim the command colour. Curated on
# purpose: prose lookalikes ("- check", "- test", "- more …") stay out, so a
# bullet only claims when it really prefixes a command line.
_COMMAND_WORDS = frozenset(
    """apk apt apt-get awk aws base64 biber black brew bundle bun caffeinate
    cargo cat cd chgrp chmod chown clang claude cmake code codex composer
    conda coverage cp crontab curl cut defaults deno df dmesg diff diskutil
    ditto docker docker-compose dotnet du dnf emacs fd ffmpeg ffprobe flake8
    gcc gcloud gem gh git git-lfs glab go gofmt gradle grep gunzip gzip hatch
    helm hermes hostname htop id iostat ipython irb isort java javac journalctl
    jq jupyter kill killall kubectl latex latexmk launchctl ln ls lsof lua
    lualatex make mamba micromamba mdfind md5 md5sum mdls mdutil mkdir mvn mv
    mypy mysql nano nc nvim nox node npm npx openssl open osascript otool
    pacman pandoc pbcopy pbpaste pdflatex pdm perl php pgrep ping pip pip3 pipx
    pkill plutil podman poetry pre-commit ps psql pwd py pyright pytest python
    python3 rake redis-cli rg rmdir rm rsync ruby ruff rustc rustup rye scp
    security sed shasum sha256sum sips sort sqlite3 ssh ssh-add ssh-copy-id
    ssh-keygen swift sw_vers sysctl scutil systemctl service tar tee tmux
    touch tox tr tsc typst uname uniq unzip uptime uv venv vim vi virtualenv
    vm_stat wc wget whoami xargs xcodebuild xcrun xelatex yarn yq yt-dlp
    youtube-dl yum zip zed""".split()
)

# Alternation order is the precedence order: a log stamp is claimed whole (so
# ``§[#123456]`` is one white span, not white-inside-white), and a URL is claimed
# before the PR rule so a ``#fragment`` never splits off on its own. The command
# bullet comes last: it matches only the ``-`` itself (the word after it is a
# lookahead group), and the span is painted only when that word is a known
# shell command.
_TOKENS = re.compile(
    r"(?m)"
    r"(?P<log>§\s*\[[^\]\n]*\])"
    r"|(?P<url>https?://[^\s]+)"
    r"|(?P<pr>\#\d{6,}\b)"
    r"|(?P<mark>(?<![\w.#~≈±])\d{1,2}\.(?=\s))"
    r"|(?P<cmd>^[ \t]*-(?=[ \t]+(?P<cmdword>[a-z][a-z0-9_-]*)(?:[ \t]|$)))"
)

_URL_TRAILING = ".,;:!?'\""
_URL_BRACKETS = (("(", ")"), ("[", "]"), ("{", "}"))
_URL_CLOSER_TO_OPENER = {closer: opener for opener, closer in _URL_BRACKETS}


def _split_url(url: str) -> tuple[str, str]:
    """Peel sentence punctuation off a URL span so it renders as prose.

    Sentence punctuation peels one char at a time; a trailing bracket closer
    peels only when the span does not balance it, so the ``)`` of
    ``…/C_(programming_language)`` stays with the URL while ``…/a)`` loses it.
    """
    end = len(url)
    while end:
        char = url[end - 1]
        if char in _URL_CLOSER_TO_OPENER:
            head = url[:end]
            if head.count(_URL_CLOSER_TO_OPENER[char]) < head.count(char):
                end -= 1
                continue
            break
        if char in _URL_TRAILING:
            end -= 1
            continue
        break
    return url[:end], url[end:]


def thinking_spans(text: str) -> list[tuple[str, str | None]]:
    """Split thinking text into ``(chunk, colour)`` pairs.

    ``colour`` is ``None`` for ordinary prose, which the renderer paints in the
    showcase green. Adjacent same-coloured chunks are merged so the output
    carries one escape per run rather than one per token.
    """
    spans: list[tuple[str, str | None]] = []
    position = 0
    for match in _TOKENS.finditer(text or ""):
        if match.start() > position:
            spans.append((text[position:match.start()], None))
        kind = match.lastgroup
        raw = match.group()
        if kind == "url":
            body, tail = _split_url(raw)
            spans.append((body, "url"))
            if tail:
                spans.append((tail, None))
        elif kind == "mark":
            spans.append((raw, "order"))
        elif kind == "cmd":
            # ``lastgroup`` names the outer ``cmd`` group — groups inside a
            # lookahead do not count — so read the captured word explicitly.
            if len(raw) > 1:
                spans.append((raw[:-1], None))  # indentation before the bullet
            word = match.group("cmdword")
            spans.append(("-", "cmd") if word in _COMMAND_WORDS else ("-", None))
        else:  # log, pr
            spans.append((raw, kind))
        position = match.end()
    if position < len(text or ""):
        spans.append((text[position:], None))

    merged: list[tuple[str, str | None]] = []
    for chunk, color in spans:
        if merged and merged[-1][1] == color:
            merged[-1] = (merged[-1][0] + chunk, color)
        else:
            merged.append((chunk, color))
    return merged


def render_thinking_text(text: str, colors: dict[str, str] | None = None) -> str:
    """Colourise a finished piece of thinking text.

    ``colors`` is the resolved palette (see :func:`resolve_showcase_colors`); when
    omitted it is resolved per call, so a ``display.thinking_colors`` change applies
    without a restart.
    """
    palette = colors or resolve_showcase_colors()
    return "".join(
        f"{palette[color or 'main']}{chunk}{_RST}"
        for chunk, color in thinking_spans(text)
        if chunk
    )


# Fragments that could still grow into a token: holding them back keeps a
# half-arrived ``§[2026-10-0``, ``#12345`` or ``https://x.co`` plain prose
# instead of committing to a colour that the rest of the token may contradict.
# A line-leading ``-`` is held until the word after it is terminated (space or
# newline) so a bullet is never painted orange before we know that word really
# is a shell command.
_PENDING = (
    re.compile(r"§\s*\[[^\]\n]*$"),
    re.compile(r"§\s*$"),
    re.compile(r"\#\d{0,5}$"),
    re.compile(r"\#\d{6,}$"),
    re.compile(r"\bhttps?://[^\s]*$"),
    re.compile(r"\b(?:h|ht|htt|http|https|https:|https:/)$"),
    re.compile(r"(?<![\w.#~≈±])\d{1,2}\.$"),
    re.compile(r"(?m)^[ \t]*-[ \t]*$"),
    re.compile(r"(?m)^[ \t]*-[ \t]+[a-z][a-z0-9_-]*$"),
)


def split_safe_stream(buffered: str) -> tuple[str, str]:
    """Split ``buffered`` into (safe to paint now, keep buffering).

    The cut lands before the oldest still-arriving token, so a token is painted
    exactly once, by the call that saw it complete.
    """
    cut = len(buffered)
    for pattern in _PENDING:
        match = pattern.search(buffered)
        if match is not None and match.start() < cut:
            cut = match.start()
    return buffered[:cut], buffered[cut:]
