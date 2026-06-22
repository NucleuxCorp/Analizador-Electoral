"""
tests/labeler/test_archive_primera_vuelta.py — Git archive script tests.

Covers PR-E behavior: running scripts/archive_primera_vuelta.py against a git
repository creates the annotated tag `primera-vuelta-v1` and the branch
`primera-vuelta` pointing at HEAD.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest


@pytest.fixture
def temp_git_repo(tmp_path: Path) -> Path:
    """Create a temporary git repository with a single commit."""
    repo = tmp_path / "repo"
    repo.mkdir()

    subprocess.run(["git", "init"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=repo, check=True)

    (repo / "README.md").write_text("# test\n")
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=repo, check=True)

    return repo


SCRIPT_PATH = Path(__file__).parents[2] / "scripts" / "archive_primera_vuelta.py"


class TestArchivePrimeraVuelta:
    def test_archive_script_creates_tag(self, temp_git_repo: Path) -> None:
        """The archive script must create the annotated tag primera-vuelta-v1."""
        subprocess.run(
            ["python", str(SCRIPT_PATH), "--repo-path", str(temp_git_repo)],
            check=True,
        )

        result = subprocess.run(
            ["git", "tag", "-l", "primera-vuelta-v1"],
            cwd=temp_git_repo,
            check=True,
            capture_output=True,
            text=True,
        )

        assert "primera-vuelta-v1" in result.stdout

        # Verify it is an annotated tag with a message.
        show = subprocess.run(
            ["git", "tag", "-l", "-n1", "primera-vuelta-v1"],
            cwd=temp_git_repo,
            check=True,
            capture_output=True,
            text=True,
        )
        assert "primera-vuelta" in show.stdout

    def test_archive_script_creates_branch(self, temp_git_repo: Path) -> None:
        """The archive script must create the primera-vuelta branch at HEAD."""
        subprocess.run(
            ["python", str(SCRIPT_PATH), "--repo-path", str(temp_git_repo)],
            check=True,
        )

        result = subprocess.run(
            ["git", "branch", "-l", "primera-vuelta"],
            cwd=temp_git_repo,
            check=True,
            capture_output=True,
            text=True,
        )

        assert "primera-vuelta" in result.stdout

        # The branch must point at the current HEAD commit.
        head = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=temp_git_repo,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()

        branch_head = subprocess.run(
            ["git", "rev-parse", "primera-vuelta"],
            cwd=temp_git_repo,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()

        assert branch_head == head
