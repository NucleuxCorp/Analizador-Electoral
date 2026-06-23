#!/usr/bin/env python3
"""
scripts/archive_primera_vuelta.py — Create the primera-vuelta git archive.

Creates an annotated tag `primera-vuelta-v1` and a branch `primera-vuelta`
pointing at the current HEAD. No folders are renamed and nothing is pushed.
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

TAG = "primera-vuelta-v1"
BRANCH = "primera-vuelta"
TAG_MESSAGE = "Primera vuelta — portal de etiquetado E-14 (snapshot previo al corte a segunda vuelta)"


def run_git(repo: Path, args: list[str], *, check: bool = True) -> subprocess.CompletedProcess[str]:
    """Run a git command in the target repository."""
    return subprocess.run(["git", *args], cwd=repo, check=check, text=True)


def archive(repo: Path) -> int:
    """Create the annotated tag and branch for the primera vuelta archive."""
    if not (repo / ".git").is_dir():
        print(f"Error: {repo} is not a git repository.", file=sys.stderr)
        return 1

    run_git(repo, ["tag", "--annotate", TAG, "-m", TAG_MESSAGE])
    run_git(repo, ["branch", BRANCH])

    print(f"Tag {TAG} creado. Branch {BRANCH} creada desde HEAD.")
    print("")
    print("Para subir al remoto cuando estés listo:")
    print(f"  git push origin {TAG}")
    print(f"  git push origin {BRANCH}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Create the primera-vuelta git tag and branch.",
    )
    parser.add_argument(
        "--repo-path",
        type=Path,
        default=Path.cwd(),
        help="Path to the git repository (default: current directory).",
    )
    args = parser.parse_args(argv)

    return archive(args.repo_path)


if __name__ == "__main__":
    raise SystemExit(main())
