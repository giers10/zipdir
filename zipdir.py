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
import os
from pathlib import Path
import posixpath
import sys
import zipfile
from typing import Iterable, List, Set

# Use real .gitignore semantics
try:
    from pathspec import PathSpec
    from pathspec.patterns.gitwildmatch import GitWildMatchPattern
except ImportError:
    print(
        "Error: This script now uses 'pathspec' for .gitignore-compatible matching.\n"
        "Install it with:\n  python -m pip install pathspec",
        file=sys.stderr,
    )
    raise SystemExit(3)

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

def _collect_negation_prefixes(patterns: Iterable[str]) -> Set[str]:
    """
    From a sequence of GitIgnore-style patterns, collect directory prefixes that
    appear in negations (patterns starting with '!'). We use these to avoid
    pruning directories that might contain re-included files.
    """
    prefixes: Set[str] = set()
    for raw in patterns:
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if not line.startswith("!"):
            continue

        pat = line[1:].lstrip("/")  # drop '!' and leading root anchor
        if not pat:
            continue

        # If the pattern ends with '/', it's a directory; otherwise grab parents.
        is_dir_pat = pat.endswith("/")
        path_no_slash = pat[:-1] if is_dir_pat else pat
        parts = [p for p in path_no_slash.split("/") if p]

        # Add all parent directory prefixes ending with '/'
        if parts:
            accum = ""
            for i in range(len(parts) - (0 if is_dir_pat else 1)):
                accum = f"{accum}{parts[i]}/"
                prefixes.add(accum)

        # Also, if it explicitly targets a directory, include that directory
        if is_dir_pat:
            prefixes.add(path_no_slash + "/")
        elif len(parts) > 1:
            # file under a dir: add its parent dir
            prefixes.add("/".join(parts[:-1]) + "/")

    return prefixes


def build_ignore_spec(excludes: Iterable[str]) -> tuple[PathSpec, Set[str]]:
    """
    Build a PathSpec with .gitignore semantics from defaults + user patterns.
    Returns (spec, negation_prefixes).
    """
    lines: List[str] = []

    # Default "hidden everything" like your original behavior (can be overridden via !)
    # In .gitignore semantics, patterns without '/' match in any directory.
    lines.append(".*")

    # Convert default directory names into dir patterns (match anywhere)
    for d in DEFAULT_EXCLUDED_DIR_NAMES:
        # 'd/' matches that directory at any depth
        lines.append(f"{d}/")

    # Default file names (match anywhere)
    for f in DEFAULT_EXCLUDED_FILE_NAMES:
        lines.append(f)

    # Existing glob-style defaults (already POSIX). These work under gitwild too.
    lines.extend(DEFAULT_EXCLUDED_GLOBS)

    # User/CLI/.zipignore additions (support '/', '**', and '!' negations)
    lines.extend(excludes)

    spec = PathSpec.from_lines(GitWildMatchPattern, lines)
    neg_prefixes = _collect_negation_prefixes(lines)
    return spec, neg_prefixes


def collect_files(src_dir: Path, excludes: Iterable[str]) -> List[Path]:
    """
    Traverse src_dir and return a list of file Paths to include, honoring
    .gitignore-style patterns. We prune directories when the spec ignores them
    AND no negation ('!') pattern could re-include something beneath.
    """
    src_dir = src_dir.resolve()
    include_files: List[Path] = []

    spec, neg_prefixes = build_ignore_spec(excludes)

    for root, dirs, files in os.walk(src_dir, topdown=True, followlinks=False):
        root_path = Path(root)
        rel_root = root_path.relative_to(src_dir).as_posix() if root_path != src_dir else ""

        # Prune directories (but keep if a later '!' could re-include children)
        for d in list(dirs):
            d_rel = (posixpath.join(rel_root, d) if rel_root else d) + "/"
            if spec.match_file(d_rel):
                # If any negation prefix lies inside d_rel, don't prune
                if not any(neg.startswith(d_rel) for neg in neg_prefixes):
                    dirs.remove(d)

        # Files
        for f in files:
            f_rel = posixpath.join(rel_root, f) if rel_root else f
            if spec.match_file(f_rel):
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