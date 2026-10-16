"""The fork Windows scope in ci.yml's `changes` job, EXECUTED as bash.

Listed in ``test/windows-collect-ignore.txt``: it runs the step's real bash in a
throwaway git repository, which needs a POSIX shell and git on PATH. The shape
assertions sit beside it so the wiring and the logic are pinned together.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path
from typing import Any

from macos_lane_helpers import load_workflow


def _ci() -> dict[str, Any]:
    return load_workflow("ci.yml")


def _scope_step() -> dict[str, Any]:
    steps = _ci()["jobs"]["changes"]["steps"]
    return next(step for step in steps if step.get("id") == "windows-scope")


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    ).stdout.strip()


def _repo_with_change(tmp_path: Path, added: str) -> tuple[Path, str]:
    """A repo whose HEAD adds ``added`` to a .py file on top of a base commit."""
    repo = tmp_path / "repo"
    repo.mkdir(parents=True)
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "t@example.invalid")
    _git(repo, "config", "user.name", "t")
    (repo / "mod.py").write_text("x = 1\n", encoding="utf-8")
    _git(repo, "add", "mod.py")
    _git(repo, "commit", "-q", "-m", "base")
    base = _git(repo, "rev-parse", "HEAD")
    (repo / "mod.py").write_text("x = 1\n" + added + "\n", encoding="utf-8")
    _git(repo, "commit", "-q", "-am", "head")
    return repo, base


def _run(
    tmp_path: Path,
    *,
    fork: str = "true",
    paths_hit: str = "false",
    labelled: str = "false",
    full_run: str = "false",
    added: str = "y = 2",
    base: str | None = None,
) -> tuple[dict[str, str], str]:
    bash = shutil.which("bash")
    assert bash is not None, "bash is required to execute the scope step"
    repo, real_base = _repo_with_change(tmp_path, added)
    out = tmp_path / "gh-output"
    out.touch()
    step = _scope_step()
    proc = subprocess.run(
        [bash, "-c", step["run"]],
        cwd=repo,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=30,
        env={
            "PATH": os.environ.get("PATH", ""),
            "HOME": str(tmp_path),
            "FORK": fork,
            "PATHS_HIT": paths_hit,
            "LABELLED": labelled,
            "FULL_RUN": full_run,
            "BASE_SHA": real_base if base is None else base,
            "WINDOWS_PRIMITIVES": step["env"]["WINDOWS_PRIMITIVES"],
            "GITHUB_OUTPUT": str(out),
        },
    )
    assert proc.returncode == 0, f"stdout={proc.stdout!r} stderr={proc.stderr!r}"
    outputs = dict(
        line.split("=", 1) for line in out.read_text(encoding="utf-8").splitlines() if "=" in line
    )
    return outputs, proc.stdout


class TestTheForkWindowsScopeWiring:
    def test_the_windows_job_skips_only_on_an_explicit_false(self) -> None:
        job = _ci()["jobs"]["backend-test-windows"]
        # `!= 'false'`, so an absent output (the changes job failed) still runs it.
        assert job["if"] == "needs.changes.outputs.windows_tests != 'false'"

    def test_the_output_comes_from_the_scope_step(self) -> None:
        outputs = _ci()["jobs"]["changes"]["outputs"]
        assert outputs["windows_tests"] == "${{ steps.windows-scope.outputs.windows_tests }}"

    def test_fork_is_read_from_the_pull_request_head_repository(self) -> None:
        fork = _scope_step()["env"]["FORK"]
        assert "github.event_name == 'pull_request'" in fork
        assert "github.event.pull_request.head.repo.full_name != github.repository" in fork

    def test_the_path_bucket_covers_the_windows_shard_inputs(self) -> None:
        filt = next(s for s in _ci()["jobs"]["changes"]["steps"] if s.get("id") == "filter")[
            "with"
        ]["filters"]
        for pattern in ("'**/*windows*'", "'src/kiro_crew/platform_compat.py'", "'pyproject.toml'"):
            assert pattern in filt


class TestTheForkWindowsScope:
    def test_a_same_repository_head_always_runs(self, tmp_path: Path) -> None:
        outputs, _ = _run(tmp_path, fork="false")
        assert outputs["windows_tests"] == "true"

    def test_a_windows_neutral_fork_head_skips_and_says_how_to_run_it(self, tmp_path: Path) -> None:
        outputs, stdout = _run(tmp_path)
        assert outputs["windows_tests"] == "false"
        assert "ci:windows" in stdout

    def test_a_windows_path_runs_a_fork(self, tmp_path: Path) -> None:
        outputs, _ = _run(tmp_path, paths_hit="true")
        assert outputs["windows_tests"] == "true"

    def test_the_label_and_full_run_run_a_fork(self, tmp_path: Path) -> None:
        assert _run(tmp_path / "a", labelled="true")[0]["windows_tests"] == "true"
        assert _run(tmp_path / "b", full_run="true")[0]["windows_tests"] == "true"

    def test_an_added_windows_branch_runs_a_fork(self, tmp_path: Path) -> None:
        for i, line in enumerate(
            (
                'if sys.platform == "win32":',
                "if IS_WINDOWS:",
                "if os.name == 'nt':",
                "import msvcrt",
                "import winreg",
            )
        ):
            outputs, _ = _run(tmp_path / str(i), added=line)
            assert outputs["windows_tests"] == "true", line

    def test_a_word_that_only_contains_a_needle_does_not(self, tmp_path: Path) -> None:
        outputs, _ = _run(tmp_path, added="mywin32helper = 1")
        assert outputs["windows_tests"] == "false"

    def test_an_unreadable_diff_runs_a_fork(self, tmp_path: Path) -> None:
        outputs, _ = _run(tmp_path, base="0" * 40)
        assert outputs["windows_tests"] == "true"
        outputs, _ = _run(tmp_path / "empty", base="")
        assert outputs["windows_tests"] == "true"
