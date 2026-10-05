"""The ``$HERMES_BIN`` rung of child-launch resolution.

A gateway outlives the install it was started from: under a package-manager
environment the process keeps running from ``installs/<key>/environments/<gen>/
workspace``, which publishes no launcher beside its own tree. Every rung that
looks for ``<install>/.hermes/bin`` therefore misses, and a child launched
through the running interpreter or through PATH re-executes the *workspace* as
its root — a different install key than the one the gateway was started from
(#125537), whose environment need not carry the extras the gateway has.

``$HERMES_BIN`` is the one name that cannot be re-derived from a child's cwd,
so every lane that spawns a Hermes child consults it first: Nix publishes it
with ``--set-default`` and the updater exports it before each update
invocation. ``hermes_cli.kanban_db_dispatch._resolve_hermes_argv`` established
the order; the cron Bot Chat lane and the bot relay resolve their child without
it, which is why one manual launcher copy fixed ``message_agent`` for a turn
while cron kept failing until the gateway restarted.

Resolution rules, identical for every caller:

* a path-like value (``~``, absolute, holding a separator, backslash, or a
  Windows drive) is taken as an explicit path and made absolute;
* a bare name keeps PATH semantics but is searched without the current
  directory, so a file sitting next to a task workspace can never become the
  child's entry point;
* Windows ``.cmd``/``.bat`` is refused — ``cmd.exe`` reinterprets an otherwise
  literal argument vector (an ampersand in a query-file path) even with
  ``shell=False``;
* an empty, unresolvable or refused value yields ``None`` so the caller keeps
  its own fallbacks instead of dying on a bad override.

Deliberately stdlib-only and import-cheap: ``cron`` and ``tools`` both call
this on a delivery path.
"""

from __future__ import annotations

import os
import re
import shutil
from typing import Optional

_WINDOWS_DRIVE = re.compile(r"^[A-Za-z]:")


def _is_windows() -> bool:
    return os.name == "nt"


def _looks_like_path(value: str) -> bool:
    """True when the override names a file rather than a command on PATH."""
    expanded = os.path.expanduser(value)
    return (
        expanded.startswith("~")
        or os.path.isabs(expanded)
        or bool(os.path.dirname(expanded))
        or "\\" in expanded
        or bool(_WINDOWS_DRIVE.match(expanded))
    )


def _is_windows_batch_shim(path: str) -> bool:
    """True for the shell/batch shims that must never be a child's argv[0]."""
    return path.lower().endswith((".cmd", ".bat"))


def _path_search_names(command: str) -> list[str]:
    """Executable names to try for an unqualified command."""
    if not _is_windows() or os.path.splitext(command)[1]:
        return [command]
    raw = os.environ.get("PATHEXT") or ".COM;.EXE;.BAT;.CMD"
    return [command + ext for ext in raw.split(";") if ext]


def _which_no_cwd(command: str) -> Optional[str]:
    """Resolve a bare command from PATH without the current directory.

    ``shutil.which`` searches the working directory before PATH on Windows for
    a bare name. A child launch must never adopt a file that merely happens to
    sit where the scheduler was invoked from.
    """
    for raw_dir in os.environ.get("PATH", "").split(os.pathsep):
        if not raw_dir or raw_dir == ".":
            continue
        directory = os.path.expanduser(raw_dir)
        for name in _path_search_names(command):
            candidate = os.path.join(directory, name)
            if os.path.isfile(candidate) and (not _is_windows() or os.access(candidate, os.X_OK)):
                return candidate
    return None


def hermes_bin_from_env() -> Optional[str]:
    """The launcher ``$HERMES_BIN`` names, or ``None`` when it names nothing usable."""
    value = os.environ.get("HERMES_BIN", "").strip()
    if not value:
        return None
    if _looks_like_path(value):
        resolved = os.path.abspath(os.path.expanduser(value))
    else:
        resolved = _which_no_cwd(value) or ""
        if not resolved:
            return None
    if _is_windows() and _is_windows_batch_shim(resolved):
        return None
    return resolved
