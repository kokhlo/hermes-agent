"""The ``/queue`` verb grammar, shared by every queue-command surface.

The registry declares ``subcommands=("list","edit","rm","move","clear","add")`` for
completion, and each surface used to decide on its own what those words mean. Only the
classic CLI parsed them; the TUI/desktop and ACP handlers forwarded the whole argument
to the model as a literal prompt, so ``/queue list`` queued the word "list" and the
management actions were unreachable outside the CLI.

This module owns the grammar — which leading word is a management verb, when a verb
counts as management rather than as the start of a prompt, and which index a verb
addressed. A surface supplies its own queue (a snapshot/mutate pair plus how to read and
replace one item's text) and renders the returned output; nothing else re-derives which
invocations are commands.

Mirrors ``goal_command.py``: parse here, adapt there.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Generic, TypeVar

ItemT = TypeVar("ItemT")

# Usage lines, in the surface-facing "/queue ..." form.
USAGE = "Usage: /queue <prompt> | /queue list | /queue edit N <prompt> | /queue rm N | /queue move FROM TO | /queue clear"


@dataclass(frozen=True)
class QueueCommand:
    """One parsed ``/queue`` invocation.

    ``action`` is the surface-independent verb. ``enqueue`` carries the text to queue as
    a normal next-turn prompt; every other action addresses the queue itself and leaves
    ``payload`` empty.
    """

    action: str
    payload: str = ""
    index: int | None = None
    destination: int | None = None
    error: bool = False


def parse_queue_index(token: str) -> int | None:
    """1-based queue index from one whitespace-free token, or None when it isn't one.

    ``str.isdigit()`` alone is not enough: it accepts characters ``int()`` rejects
    (``²``, ``②``), and ``int()`` also refuses digit strings past Python's conversion
    limit. A ValueError here would escape the slash handler and take down the
    prompt_toolkit app, so every queue index goes through this.
    """
    if not token.isdigit():
        return None
    try:
        return int(token)
    except ValueError:
        return None


# Canonical verb -> (aliases, takes_an_index). A verb without an index is only management
# when it stands alone; an index verb when a number follows or when it stands alone (the
# surface then prints the usage line).
_VERB_ALIASES: dict[str, tuple[tuple[str, ...], bool]] = {
    "list": (("list", "ls", "show"), False),
    "clear": (("clear",), False),
    "edit": (("edit", "set"), True),
    "rm": (("rm", "remove", "delete", "del", "pop"), True),
    "move": (("move",), True),
}
_VERBS: dict[str, tuple[str, bool]] = {
    alias: (verb, takes_index)
    for verb, (aliases, takes_index) in _VERB_ALIASES.items()
    for alias in aliases
}

LIST, CLEAR, EDIT, RM, MOVE, ADD, ENQUEUE = "list", "clear", "edit", "rm", "move", "add", "enqueue"

# The classic CLI's own routing table: management verb -> (handler name, takes an index).
# Kept verbatim so its handler can keep dispatching by name while sharing the grammar.
VERB_TABLE: dict[str, tuple[str, bool]] = {
    "list": ("_queue_list", False), "ls": ("_queue_list", False), "show": ("_queue_list", False),
    "clear": ("_queue_clear", False),
    "edit": ("_queue_edit", True), "set": ("_queue_edit", True),
    "rm": ("_queue_remove", True), "remove": ("_queue_remove", True), "delete": ("_queue_remove", True),
    "del": ("_queue_remove", True), "pop": ("_queue_remove", True),
    "move": ("_queue_move", True),
}


def parse_queue_command(arg: str) -> QueueCommand:
    """Split a ``/queue`` argument into a verb and its operands.

    A leading management word only manages the queue when its arguments fit; ``clear the
    logs`` and ``edit the config`` are prompts, and ``/queue add <prompt>`` forces
    enqueueing.
    """
    payload = (arg or "").strip()
    if not payload:
        return QueueCommand(LIST)
    verb, _, rest = payload.partition(" ")
    verb, rest = verb.lower(), rest.strip()
    if verb == ADD:
        return QueueCommand(ENQUEUE, rest) if rest else QueueCommand(ADD, error=True)
    entry = _VERBS.get(verb)
    if entry is None:
        return QueueCommand(ENQUEUE, payload)
    canonical, takes_index = entry
    if not takes_index:
        # ``list``/``clear`` manage only when they stand alone; anything after them is a prompt.
        return QueueCommand(canonical) if not rest else QueueCommand(ENQUEUE, payload)
    if not rest:
        # A bare index verb is still management, but carries no index to address — the surface
        # answers with its usage line rather than queueing the word.
        return QueueCommand(canonical, error=True)
    # Routing is deliberately loose: a digit-shaped first token commits the verb to the queue,
    # and the hardened parse below decides whether it is usable. A token that is not even
    # digit-shaped (``rm soon``, ``edit the config``) starts a prompt instead.
    if not rest.split(None, 1)[0].isdigit():
        return QueueCommand(ENQUEUE, payload)
    if canonical == MOVE:
        bits = rest.split()
        source, destination = ((parse_queue_index(bit) for bit in bits) if len(bits) == 2 else (None, None))
        if source is None or destination is None:
            return QueueCommand(MOVE, error=True)
        return QueueCommand(MOVE, index=source, destination=destination)
    index = parse_queue_index(rest.split(None, 1)[0])
    if index is None:
        # Digit-shaped but not an int (``②``, a 5000-digit run): the verb is committed to the
        # queue, so the surface answers with this verb's usage line rather than queueing it.
        return QueueCommand(canonical, error=True)
    if canonical == RM:
        # ``rm 1 extra`` is not a removal — only a lone index reaches the queue.
        return QueueCommand(RM, index=index) if len(rest.split()) == 1 else QueueCommand(RM, error=True)
    bits = rest.split(None, 1)
    if len(bits) != 2:
        return QueueCommand(EDIT, error=True)
    return QueueCommand(EDIT, index=index, payload=bits[1])


@dataclass(frozen=True)
class QueueCommandResult:
    """What a surface should do after applying a :class:`QueueCommand`."""

    output: str
    enqueue: str | None = None
    error: bool = False


def preview(text: str, limit: int = 80) -> str:
    return f"{text[:limit]}{'...' if len(text) > limit else ''}"


def apply_queue_command(
    command: QueueCommand,
    *,
    snapshot: Callable[[], list],
    mutate: Callable[[Callable[[list], list]], tuple[list, list]],
    text_of: Callable[[ItemT], str],
    with_text: Callable[[ItemT, str], ItemT],
    format_item: Callable[[ItemT], str] | None = None,
    enqueue_usage: str = USAGE,
    render: Callable[[str, str], str] | None = None,
) -> QueueCommandResult:
    """Apply ``command`` to a surface's queue and return the text to show the user.

    ``snapshot``/``mutate`` are the surface's own queue access — the caller closes over its
    lock, so a voice ``put`` or a drain cannot slip between read and write-back.
    ``text_of``/``with_text`` read and replace one item's text, so an item carrying a
    marker (a voice sentinel, an envelope's attachments) keeps it. ``render(key,
    fallback)`` localizes the messages when a surface has locales; the English fallback
    is returned when it does not.
    """
    say = render or (lambda _key, fallback: fallback)
    describe = format_item or (lambda item: preview(str(text_of(item))))
    action = command.action

    if action == ENQUEUE:
        return QueueCommandResult("", enqueue=command.payload)
    if action == ADD:
        return QueueCommandResult(say("cli.queue.usage_add", "Usage: /queue add <prompt>"), error=True)
    if action == LIST:
        items = snapshot()
        if not items:
            return QueueCommandResult(
                say("cli.queue.empty", "Queue is empty.") + "  " + enqueue_usage)
        header = say("cli.queue.pending_header", "Queue ({count} pending):").format(count=len(items))
        rows = [f"  {i}. {describe(item)}" for i, item in enumerate(items, 1)]
        return QueueCommandResult("\n".join([header, *rows]))
    if action == CLEAR:
        before, _ = mutate(lambda items: [])
        if len(before) == 1:
            key, fallback = "cli.queue.cleared_one", "Cleared {count} queued prompt."
        else:
            key, fallback = "cli.queue.cleared_other", "Cleared {count} queued prompts."
        return QueueCommandResult(say(key, fallback).format(count=len(before)))
    if action == RM:
        if command.index is None:
            return QueueCommandResult(
                say("cli.queue.usage_remove", "Usage: /queue rm <number>"), error=True)
        index = command.index
        removed: list = []
        before, _ = mutate(
            lambda items: (removed.append(items.pop(index - 1)) or items)
            if 1 <= index <= len(items) else items)
        if not removed:
            return QueueCommandResult(
                say("cli.queue.item_not_found", "Queue item {index} not found. Current size: {size}")
                .format(index=index, size=len(before)))
        return QueueCommandResult(
            say("cli.queue.removed", "Removed queue item {index}: {preview}")
            .format(index=index, preview=describe(removed[0])))
    if action == EDIT:
        if command.index is None or not command.payload:
            return QueueCommandResult(
                say("cli.queue.usage_edit", "Usage: /queue edit <number> <new prompt>"), error=True)
        index = command.index
        applied = []
        before, _ = mutate(
            lambda items: (applied.append(with_text(items[index - 1], command.payload))
                           or items.__setitem__(index - 1, applied[0]) or items)
            if 1 <= index <= len(items) else items)
        # Membership, not ``before == after``: an in-place edit rewrites the very item the
        # snapshot holds, so the two lists compare equal even though the edit landed. The CLI's
        # queue holds immutable strings and never sees this; an envelope does.
        if not applied:
            return QueueCommandResult(
                say("cli.queue.item_not_found", "Queue item {index} not found. Current size: {size}")
                .format(index=index, size=len(before)))
        return QueueCommandResult(
            say("cli.queue.updated", "Updated queue item {index}: {preview}")
            .format(index=index, preview=describe(applied[0])))
    if action == MOVE:
        source, destination = command.index, command.destination
        if source is None or destination is None:
            return QueueCommandResult(
                say("cli.queue.usage_move", "Usage: /queue move <from> <to>"), error=True)

        def _move(items: list) -> list:
            if 1 <= source <= len(items) and 1 <= destination <= len(items):
                items.insert(destination - 1, items.pop(source - 1))
            return items

        # A no-op swap (source == destination) and an out-of-range move both leave the order
        # untouched, so the order itself is what says whether the move landed. Reordering never
        # rewrites an item, so comparing orders is exact here.
        before, after = mutate(_move)
        if [id(item) for item in before] == [id(item) for item in after] and source != destination:
            return QueueCommandResult(
                say("cli.queue.move_out_of_range", "Queue move out of range. Current size: {size}")
                .format(size=len(before)))
        return QueueCommandResult(
            say("cli.queue.moved", "Moved queue item {source} to {destination}.")
            .format(source=source, destination=destination))
    raise ValueError(f"unknown queue action: {action!r}")