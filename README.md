# zipdir — Smart folder zipper (skip the junk)

`zipdir.py` zips a directory while **skipping common junk/build/cache files** so your archives stay lean and clean. It also **won’t overwrite** an existing archive — if `out.zip` exists, it will create `out-1.zip`, `out-2.zip`, … automatically.
 
---

## Features

* **Skips clutter by default**

  * Hidden files & folders (any path segment starting with `.`)
  * `node_modules`, Python virtualenvs (`venv`, `.venv`, `env`), `__pycache__`, build caches, VCS folders, OS junk, etc. (see full list below)
* **Non‑clobbering output**: auto‑increments the filename if it already exists (`out.zip → out-1.zip → out-2.zip …`).
* **Dry‑run listing**: preview what would be zipped with `--list`.
* **Extendable ignores**

  * `--exclude/-x` to add glob patterns on the CLI
  * `--zipignore` to supply a file with patterns (one per line)
  * Also auto‑loads a local `.zipignore` from the source folder if present
* **Self‑protection**: if the target zip is inside the source tree, it’s automatically excluded.
* **Reasonable compression**: `ZIP_DEFLATED` with `compresslevel=6` (balanced speed/size).

---

## Installation

1. Save the script as `zipdir.py` anywhere in your `$PATH` (or alongside your project).
2. Requires **Python 3.8+**.
3. (Optional) Make executable on Unix:

   ```bash
   chmod +x zipdir.py
   ```

---

## Usage

Basic:

```bash
python zipdir.py /path/to/source_dir out.zip
```

Dry‑run (no archive is written; just lists files):

```bash
python zipdir.py /path/to/source_dir out.zip --list
```

Add extra excludes (you can repeat `-x`):

```bash
python zipdir.py src out.zip -x "*.mp4" -x ".secret*"
```

Use a `.zipignore` file (one glob per line; `#` for comments):

```bash
python zipdir.py src out.zip --zipignore .zipignore
```

If `out.zip` exists, the script will write `out-1.zip` (or the next free number) instead.

---

## CLI Options

* `src` (positional): Source directory to zip
* `out` (positional): Output `.zip` path
* `--exclude`, `-x` (repeatable): Extra glob pattern to exclude
* `--zipignore <file>`: Path to ignore file (defaults to `./.zipignore` if present)
* `--list`: Dry‑run; print the files that would be included

---

## Default Exclusions (curated)

**Hidden items**: Any path segment starting with `.` is excluded (e.g., `.git`, `.env`, `.cache`).

**Directories**

* VCS/IDE/OS: `.git`, `.hg`, `.svn`, `.idea`, `.vscode`
* Python: `__pycache__`, `.pytest_cache`, `.mypy_cache`, `.ruff_cache`, `.ipynb_checkpoints`, `.tox`, `.nox`, `build`, `dist`, `.venv`, `venv`, `env`, `.env`
* JS/TS: `node_modules`, `.next`, `.nuxt`, `.svelte-kit`, `.angular`, `.parcel-cache`, `.turbo`, `.yarn`, `.pnpm-store`, `out`, `.output`
* General caches/tools: `.cache`, `.gradle`, `.terraform`, `.serverless`, `.vercel`

**Files**

* Locks/manifests: `package-lock.json`, `yarn.lock`, `pnpm-lock.yaml`, `poetry.lock`, `Pipfile.lock`
* OS junk: `.DS_Store`, `Thumbs.db`, `desktop.ini`, `Icon\r`
* Coverage/reports: `.coverage`, `coverage.xml`

**Globs (apply to files *or* directories)**

* Python bytecode / extensions: `*.pyc`, `*.pyd`, `*.pyo`, `*.so`
* Editor/temp: `*~`, `*.swp`, `*.swo`, `*.tmp`, `*.temp`
* Logs: `*.log`
* Env files: `.env*`, `*.env`, `*.env.*`
* Coverage paths: `*/coverage/*`, `*/.coverage/*`
* macOS resource forks: `._*`

> **Tip:** Add your own patterns with `-x` or a `.zipignore` file.

---

## .zipignore format

* One glob pattern per line
* Lines starting with `#` are comments
* Patterns are matched against the **relative POSIX path** from the source root

Example `.zipignore`:

```text
# media & datasets
*.mp4
*.mov
*.mkv
*.zip

# secrets
secrets/**
*.pem
*.key
```

---

## Programmatic use (import)

You can import the helpers if you prefer calling them from Python:

```python
from pathlib import Path
from zipdir import make_zip

count = make_zip(Path("src"), Path("out.zip"), extra_excludes=["*.mp4", "data/**"])
print(f"Added {count} files")
```

Key entry points:

* `make_zip(src_dir: Path, zip_path: Path, extra_excludes=()) -> int`
* `collect_files(src_dir: Path, excludes) -> List[Path]`

Auto‑incrementing output is handled via `next_available_path(Path("out.zip"))` in the CLI `main()`.

---

## Notes & Behavior

* **Cross‑platform**: macOS, Linux, Windows. Uses forward‑slash (`/`) paths inside the archive.
* **Symlinks**: Symlinks are *not* followed (`followlinks=False`).
* **Performance**: Directory pruning avoids entering ignored folders. Compression level 6 balances speed & size.
* **Including hidden files**: Hidden items are excluded by design. If you need them, remove the hidden‑check in `should_exclude()`.

---

## Troubleshooting

* **My archive still contains something I wanted excluded**

  * Confirm the *relative* path matches your glob. Remember patterns match POSIX paths from the source root.
* **The output archive appeared inside itself**

  * The script prevents that automatically by excluding the chosen output path.
* **Windows path quirks**

  * Archive entries use `/` separators, which is standard and widely supported.

---

## License

MIT

---
test
## Changelog (highlights)

* **v1.1**: Non‑clobbering output (`out.zip` → `out-1.zip`, …)
* **v1.0**: Initial release with curated skips, `.zipignore`, and dry‑run
