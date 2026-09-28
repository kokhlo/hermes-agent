"""``--diff <ref>`` receipts: a failed range must not read as a clean scan.

``get_diff_files`` used to collapse every git failure — unknown ref, shallow
clone with no merge base, missing git binary — into an empty file list, so
``main()`` printed its success line and exited 0. The receipt was
byte-identical to a legitimately empty range, and ``--diff`` receipts get
quoted in PR bodies, so the false "clean" was actively misleading.
"""
from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
LINTER_PATH = REPO_ROOT / "scripts" / "check-windows-footguns.py"


@pytest.fixture(scope="module")
def linter():
    spec = importlib.util.spec_from_file_location("check_windows_footguns_diff", LINTER_PATH)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


def _git(cwd, *args):
    subprocess.run(
        ["git", *args],
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )


@pytest.fixture()
def repo(tmp_path, monkeypatch, linter):
    """A two-commit repo the linter resolves as its root."""
    (tmp_path / "pkg.py").write_text("x = 1\n", encoding="utf-8")
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "add", "-A")
    _git(
        tmp_path, "-c", "user.email=t@example.com", "-c", "user.name=t",
        "commit", "-qm", "base",
    )
    (tmp_path / "pkg.py").write_text("x = 2\n", encoding="utf-8")
    _git(tmp_path, "add", "-A")
    _git(
        tmp_path, "-c", "user.email=t@example.com", "-c", "user.name=t",
        "commit", "-qm", "change",
    )
    monkeypatch.setattr(linter, "REPO_ROOT", tmp_path)
    return tmp_path


def test_valid_ref_lists_changed_files(linter, repo):
    files = linter.get_diff_files("HEAD~1")
    assert [f.name for f in files] == ["pkg.py"]


def test_empty_range_is_an_empty_list_not_an_error(linter, repo):
    assert linter.get_diff_files("HEAD") == []


def test_unknown_ref_surfaces_gits_fatal_line(linter, repo):
    with pytest.raises(linter.DiffRangeError) as excinfo:
        linter.get_diff_files("no-such-ref")
    assert "fatal" in str(excinfo.value).lower()
    assert "no-such-ref...HEAD" in str(excinfo.value)


def test_no_merge_base_names_the_shallow_clone_remedy(linter, repo, monkeypatch):
    def fake_run(cmd, **kwargs):
        return subprocess.CompletedProcess(
            cmd, 128, stdout="",
            stderr="fatal: base...HEAD: no merge base\n",
        )

    monkeypatch.setattr(linter.subprocess, "run", fake_run)
    with pytest.raises(linter.DiffRangeError) as excinfo:
        linter.get_diff_files("base")
    assert "no merge base" in str(excinfo.value)
    assert "--deepen" in str(excinfo.value)
    assert "--unshallow" in str(excinfo.value)


def test_missing_git_binary_is_an_error(linter, repo, monkeypatch):
    def missing(cmd, **kwargs):
        raise FileNotFoundError("git")

    monkeypatch.setattr(linter.subprocess, "run", missing)
    with pytest.raises(linter.DiffRangeError):
        linter.get_diff_files("HEAD")


def test_main_bogus_ref_exits_2(linter, capsys):
    assert linter.main(["--diff", "refs/heads/no-such-ref-xyz"]) == 2
    err = capsys.readouterr().err
    assert "Cannot compute" in err
    assert "refs/heads/no-such-ref-xyz" in err


def test_main_empty_range_stays_exit_0_with_a_distinct_message(linter, capsys):
    assert linter.main(["--diff", "HEAD"]) == 0
    out = capsys.readouterr().out
    assert "nothing changed vs HEAD" in out
    assert "0 file(s) scanned" in out


def test_main_valid_range_still_scans(linter, repo, capsys, monkeypatch):
    (repo / "footgun_src.py").write_text(
        "import subprocess\n"
        "\n"
        "\n"
        "def run(cmd):\n"
        "    return subprocess.run(cmd, capture_output=True, text=True).stdout\n",
        encoding="utf-8",
    )
    _git(repo, "add", "-A")
    _git(
        repo, "-c", "user.email=t@example.com", "-c", "user.name=t",
        "commit", "-qm", "footgun",
    )
    assert linter.main(["--diff", "HEAD~1"]) == 1
    captured = capsys.readouterr()
    assert "footgun_src.py" in captured.out
    assert "1 Windows footgun(s) found" in captured.err
