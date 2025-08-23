#!/usr/bin/env python3
"""
zipdir.py - Create a ZIP archive from a folder while skipping unwanted files/folders.

Usage:
    python zipdir.py /path/to/source_dir out.zip
    python zipdir.py /path/to/source_dir out.zip --exclude "*.mp4" --exclude ".secret*"
    python zipdir.py /path/to/source_dir out.zip --zipignore .zipignore

Default skips include:
- Hidden files & folders (anything with a path segment starting with ".")
- node_modules, package-lock.json
- Python env/cache: venv, .venv, env, __pycache__, .pytest_cache, .mypy_cache, .ruff_cache, .tox, .nox
- VCS/IDE/OS: .git, .hg, .svn, .idea, .vscode, .DS_Store, Thumbs.db
- JS/TS build caches: .next, .nuxt, .svelte-kit, .angular, .parcel-cache, .turbo, .yarn, .pnpm-store, out, .output
- General caches: .cache, .gradle, .terraform, .serverless, .vercel
- Locks/reports/junk: yarn.lock, pnpm-lock.yaml, poetry.lock, Pipfile.lock, .coverage, coverage.xml, *.pyc, *.log, *.tmp, swap files, macOS resource forks

You can extend ignoring with --exclude globs or a .zipignore file (one glob per line, '#' comments).
"""

from __future__ import annotations

import argparse
import fnmatch
import os
from pathlib import Path
import posixpath
import sys
import zipfile
from typing import Iterable, List, Set

# --- Defaults ---

DEFAULT_EXCLUDED_DIR_NAMES: Set[str] = {
    # VCS / IDE / OS
    ".git", ".hg", ".svn", ".idea", ".vscode", ".DS_Store",
    # Python
    "__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache", ".ipynb_checkpoints",
    ".tox", ".nox", "build", "dist", ".venv", "venv", "env", ".env",
    # JS/TS
    "node_modules", ".next", ".nuxt", ".svelte-kit", ".angular", ".parcel-cache",
    ".turbo", ".yarn", ".pnpm-store", "out", ".output",
    # General caches
    ".cache", ".gradle", ".terraform", ".serverless", ".vercel",
}

DEFAULT_EXCLUDED_FILE_NAMES: Set[str] = {
    # Locks / metadata
    "package-lock.json", "yarn.lock", "pnpm-lock.yaml", "poetry.lock", "Pipfile.lock",
    # OS junk
    ".DS_Store", "Thumbs.db", "desktop.ini", "Icon\r",
    # Coverage / reports
    ".coverage", "coverage.xml",
}

# Globs apply to files OR directories (match against the *relative* posix path from src root)
DEFAULT_EXCLUDED_GLOBS: Set[str] = {
    # Python bytecode / extensions
    "*.pyc", "*.pyd", "*.pyo", "*.so",
    # Editors / temp
    "*~", "*.swp", "*.swo", "*.tmp", "*.temp",
    # Logs
    "*.log",
    # Env files / secrets (comment out if you want them)
    ".env*", "*.env", "*.env.*",
    # Common build outputs (language-agnostic)
    "*/coverage/*", "*/.coverage/*",
    # macOS resource forks
    "._*",
}

def load_ignore_file(path: Path) -> List[str]:
    """Read ignore patterns from a file (one glob per line, '#' for comments)."""
    patterns: List[str] = []
    try:
        for raw in path.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            patterns.append(line)
    except FileNotFoundError:
        pass
    return patterns

def is_hidden(path: Path) -> bool:
    """Return True if any component of path starts with a dot ('.')."""
    for part in path.parts:
        if part.startswith("."):
            return True
    return False

def normalize_rel(path: Path, root: Path) -> str:
    """Return POSIX-style relative path from root to path."""
    rel = path.relative_to(root)
    return rel.as_posix()

def should_exclude(rel_posix: str, name: str, is_dir: bool, *, name_dir_set: Set[str], name_file_set: Set[str], glob_set: Iterable[str]) -> bool:
    """Decide if an item should be excluded based on name sets, hidden-ness, and glob rules."""
    # Hidden files/folders
    if any(seg.startswith(".") for seg in rel_posix.split("/")):
        return True

    # Direct name checks
    if is_dir and name in name_dir_set:
        return True
    if not is_dir and name in name_file_set:
        return True

    # Glob checks against the relative posix path
    for pat in glob_set:
        if fnmatch.fnmatchcase(rel_posix, pat):
            return True

    return False

def collect_files(src_dir: Path, excludes: Iterable[str]) -> List[Path]:
    """Traverse src_dir and return a list of file Paths that should be included."""
    src_dir = src_dir.resolve()
    include_files: List[Path] = []

    # Compile combined glob set
    combined_globs: Set[str] = set(DEFAULT_EXCLUDED_GLOBS)
    combined_globs.update(excludes)

    # Walk and prune
    for root, dirs, files in os.walk(src_dir, topdown=True, followlinks=False):
        root_path = Path(root)
        rel_root = root_path.relative_to(src_dir).as_posix() if root_path != src_dir else ""

        # Prune directories in-place
        pruned: List[str] = []
        for d in list(dirs):  # iterate over a copy since we'll modify dirs
            d_rel = posixpath.join(rel_root, d) if rel_root else d
            if should_exclude(d_rel, d, True,
                              name_dir_set=DEFAULT_EXCLUDED_DIR_NAMES,
                              name_file_set=DEFAULT_EXCLUDED_FILE_NAMES,
                              glob_set=combined_globs):
                pruned.append(d)
        if pruned:
            dirs[:] = [d for d in dirs if d not in pruned]

        # Files
        for f in files:
            f_rel = posixpath.join(rel_root, f) if rel_root else f
            if should_exclude(f_rel, f, False,
                              name_dir_set=DEFAULT_EXCLUDED_DIR_NAMES,
                              name_file_set=DEFAULT_EXCLUDED_FILE_NAMES,
                              glob_set=combined_globs):
                continue
            include_files.append(root_path / f)

    return include_files

def next_available_path(path: Path) -> Path:
    """
    If `path` exists, return 'stem-1.suffix', 'stem-2.suffix', ... until unused.
    Example: out.zip -> out-1.zip -> out-2.zip ...
    """
    path = path.resolve()
    if not path.exists():
        return path

    parent = path.parent
    stem = path.stem
    suffix = path.suffix
    i = 1
    while True:
        candidate = parent / f"{stem}-{i}{suffix}"
        if not candidate.exists():
            return candidate
        i += 1

def make_zip(src_dir: Path, zip_path: Path, extra_excludes: Iterable[str] = ()) -> int:
    """
    Create zip_path from src_dir while skipping default and extra_excludes patterns.
    Returns the number of files added.
    """
    src_dir = src_dir.resolve()
    zip_path = zip_path.resolve()

    # If output zip is inside source tree, exclude it explicitly
    extra = list(extra_excludes)
    try:
        zip_rel = zip_path.relative_to(src_dir).as_posix()
        extra.append(zip_rel)
    except ValueError:
        pass  # not inside src

    files = collect_files(src_dir, extra)
    files = [p for p in files if p != zip_path]

    zip_path.parent.mkdir(parents=True, exist_ok=True)

    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
        for fp in files:
            arcname = fp.relative_to(src_dir).as_posix()
            zf.write(fp, arcname)
    return len(files)

def parse_args(argv: List[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Zip a folder while skipping common junk/build/cache files.")
    p.add_argument("src", type=Path, help="Source directory to zip")
    p.add_argument("out", type=Path, help="Output .zip file path")
    p.add_argument("--exclude", "-x", action="append", default=[], help="Extra glob pattern to exclude (can be used multiple times)")
    p.add_argument("--zipignore", type=Path, default=None, help="Optional ignore file path (one glob per line). If omitted, '.zipignore' in the source dir is used when present.")
    p.add_argument("--list", action="store_true", help="Dry run: list files that would be included and exit")
    return p.parse_args(argv)

def main(argv: List[str] | None = None) -> int:
    ns = parse_args(sys.argv[1:] if argv is None else argv)
    src: Path = ns.src
    out: Path = ns.out

    if not src.exists() or not src.is_dir():
        print(f"Error: source directory not found: {src}", file=sys.stderr)
        return 2

    # Load ignore patterns
    extra: List[str] = list(ns.exclude)
    ignore_file = ns.zipignore if ns.zipignore is not None else (src / ".zipignore")
    extra.extend(load_ignore_file(ignore_file))

    # Choose a non-clobbering output path (appends -1, -2, ...)
    final_out = next_available_path(out)

    if ns.list:
        files = collect_files(src, extra)
        print(f"Would create {final_out} with {len(files)} files:\n")
        for fp in files:
            print(fp.relative_to(src).as_posix())
        return 0

    count = make_zip(src, final_out, extra_excludes=extra)
    if final_out != out:
        print(f"Note: '{out}' already exists. Using '{final_out.name}'.")
    print(f"Created {final_out} with {count} files from {src}")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())