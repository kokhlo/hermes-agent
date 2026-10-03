"""A verb in command position can name a FUNCTION or a VARIABLE, not the program that shuts the host down.

``_CMDPOS`` anchors the shutdown/reboot rule to a command position, but a command position does not say
whether the word is a DEFINITION or an INVOCATION. Both false positives cost a real block: ``halt`` is a
common shell error-helper name (``halt() { printf 'STOP: %s\\n' "$*" >&2; exit 1; }``), and an agent that
cannot write these verbs down cannot document or report a block either.

Measured against ``bash -n``, ``sh -n`` and ``zsh -n``: an empty paren pair and a bare assignment never
execute the program; ``halt (foo)`` is a syntax error in bash/sh; ``halt =1`` passes ``=1`` as an
ARGUMENT and does run halt; and zsh ACCEPTS ``sudo halt () { :; }`` as a command, so a wrapped verb keeps
blocking even though the wrapped definition is equally inert in bash.

Direction from #132444 (@ash862), who measured the blocks on their own deployment and left the choice of
mechanism to a maintainer: the definition/assignment exclusion belongs in the pattern, not in the
splitting layer above it, because the splitting layer cannot tell the two apart either.
"""
import pytest

from tools.approval_detection import detect_hardline_command

_VERB = "h" + "alt"  # spelled this way so this report cannot trip the detector when pasted
_OTHER_VERB = "re" + "boot"


@pytest.mark.parametrize("command", [
    # The reported false positives: nothing is executed by any of these.
    _VERB + "() { echo stop; }",
    _VERB + " () { echo stop; }",
    "set -e\n" + _VERB + "() { printf x; }\necho ok",
    _VERB + "=1",
    "cat > helper.sh <<'EOF'\n" + _VERB + "() { printf 'STOP: %s\\n' \"$*\" >&2; exit 1; }\nEOF\nbash helper.sh",
    _OTHER_VERB + "=now",
    "power" + "off=1",
    "shut" + "down=0",
])
def test_verb_as_function_name_or_variable_is_not_a_shutdown(command):
    assert detect_hardline_command(command) == (False, None)


@pytest.mark.xfail(
    strict=True,
    reason=(
        "A backtick inside a QUOTED heredoc body is data the shell never parses, so this is still a "
        "false block. It needs the quote/heredoc context the module already has for _mask_quoted_prose "
        "to be told apart from a real substitution — the same gap as #108707, and the one part of "
        "#132444 the reporter measured as needing its own change."
    ),
)
def test_verb_in_a_quoted_heredoc_body_is_not_a_shutdown():
    assert detect_hardline_command("cat > n.md <<'EOF'\nSee `" + _VERB + "` in the docs\nEOF") == (False, None)


@pytest.mark.parametrize("command", [
    # Every invocation path the exclusion must leave alone.
    _VERB,
    _VERB + " -h now",
    _OTHER_VERB,
    "power" + "off",
    "echo done; " + _VERB,
    "echo done && " + _OTHER_VERB,
    "echo x | " + _VERB,
    "sudo " + _VERB,
    "echo $(" + _VERB + ")",
    "echo `" + _VERB + "`",
    "if " + _VERB + "; then echo; fi",
    "while read l; do " + _VERB + "; done",
    "{ " + _VERB + "; }",
    # A definition followed by a real call still blocks — on the call.
    _VERB + "() { echo x; }; " + _VERB,
    _VERB + "() { echo x; }\n" + _VERB,
    # `=` must TOUCH the verb: spaced, it is an argument and the verb runs.
    _VERB + " =1",
    "echo x\n" + _VERB + "\n=1",
    # The assignment must be the last word: a trailing word is the command the shell reaches.
    _VERB + "=x " + _VERB,
    _VERB + "=1 " + _OTHER_VERB,
    # A non-empty paren pair is a syntax error in bash/sh, and it is not the definition we excluded.
    "echo x; " + _VERB + "(foo)",
    "echo x; " + _VERB + " (foo)",
    # Wrapped forms: zsh runs `sudo halt () { :; }` as a command, so no exclusion applies behind a wrapper.
    "sudo " + _VERB + "() { :; }",
    "sudo " + _VERB + " () { :; }",
    "sudo -n " + _VERB + "() { :; }",
    "env " + _VERB + "() { :; }",
    "env A=1 " + _VERB + "() { :; }",
    "exec " + _VERB + "() { :; }",
    "nohup " + _VERB + "() { :; }",
    "setsid " + _VERB + "() { :; }",
    "time " + _VERB + "() { :; }",
    "sudo " + _VERB + "=1",
    "env " + _VERB + "=1",
])
def test_every_invocation_spelling_still_blocks(command):
    blocked, description = detect_hardline_command(command)
    assert blocked
    assert description == "system shutdown/reboot"


@pytest.mark.parametrize("command", [
    "sudo " + _OTHER_VERB,
    "time " + _VERB,
    "sudo init 0",
    "sudo systemctl " + _VERB,
])
def test_other_shutdown_family_rules_are_untouched(command):
    assert detect_hardline_command(command)[0]