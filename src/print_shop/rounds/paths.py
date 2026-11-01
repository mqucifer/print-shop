"""Every host-side touch of the working directory, which model-written code also writes to.

Ported from mqucifer/model-performance `cad_eval/tools.py` at 22ea8b7, which is where its sandbox
escapes were found and closed (its README, "From the second review of #41"):

- A symlink made by a run in the container resolves on the host. `read` and `write` went out
  through one, as far as the harness's own files. So no part of a path may be a link.
- A process left by a run could swap a file for a link between that check and the use. So every
  open walks the path a component at a time from the directory's fd, with `O_NOFOLLOW` on each,
  takes only a regular file, and doesn't block, so a FIFO can't hang the harness.

Here each run is its own container, gone when it ends (`box.py`), so nothing is left to race, and
the walk stays anyway: it's what makes a link in a path harmless at all.
"""

from __future__ import annotations

import errno
import hashlib
import os
import shutil
import stat
from pathlib import Path, PurePosixPath

MAX_PATH = 200
#: Made by the bench or by a run, never part of what a clean run starts from.
MADE = ("bench-out", "renders", "panes", ".pictures", "record", "__pycache__")
#: What counts as text for a round's diff.
TEXT = (".py", ".md", ".txt", ".json", ".csv", ".yaml", ".yml", ".toml")
#: A file bigger than this is named in a diff, not shown.
DIFF_BYTES = 1_000_000


class Outside(OSError):
    """A path that went through a link, or to what isn't a regular file, when opened."""


def a_path(path: str) -> bool:
    """Whether `path` could name a file at all: a file's text sent as its path can't."""
    return 0 < len(path) <= MAX_PATH and path.isprintable()


def inside(directory: Path, path: str) -> Path | None:
    """`path` in the directory, or None for one that leads out of it, by name or through a link."""
    pure = PurePosixPath(path)
    # `.` has no parts: an IndexError ended a run in `cad_eval`.
    if not path or not pure.parts or pure.is_absolute() or ".." in pure.parts:
        return None
    step = directory
    target = directory / pure
    try:
        for part in pure.parts:
            step = step / part
            if step.is_symlink():
                return None
        if not target.resolve().is_relative_to(directory.resolve()):
            return None
    except OSError:
        return None
    return target


def open_inside(directory: Path, path: str, flags: int, make_dirs: bool = False) -> int:
    """A file descriptor for `path`, opened one component at a time, never following a link."""
    parts = PurePosixPath(path).parts
    folder = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
    try:
        for part in parts[:-1]:
            try:
                nested = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=folder)
            except FileNotFoundError:
                if not make_dirs:
                    raise
                os.mkdir(part, 0o755, dir_fd=folder)
                nested = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=folder)
            except OSError as exc:
                if exc.errno in (errno.ELOOP, errno.ENOTDIR):
                    raise Outside(f"{part} is a link or isn't a folder") from exc
                raise
            os.close(folder)
            folder = nested
        try:
            found = os.open(parts[-1], flags | os.O_NOFOLLOW | os.O_NONBLOCK, 0o644, dir_fd=folder)
        except OSError as exc:
            if exc.errno == errno.ELOOP:
                raise Outside(f"{parts[-1]} is a link") from exc
            raise
        if not stat.S_ISREG(os.fstat(found).st_mode):
            os.close(found)
            raise Outside(f"{parts[-1]} isn't a regular file")
        return found
    finally:
        os.close(folder)


def read_inside(directory: Path, path: str) -> str:
    with os.fdopen(open_inside(directory, path, os.O_RDONLY), encoding="utf-8") as fh:
        return fh.read()


def read_bytes_inside(directory: Path, path: str) -> bytes:
    with os.fdopen(open_inside(directory, path, os.O_RDONLY), "rb") as fh:
        return fh.read()


def write_inside(directory: Path, path: str, text: str) -> None:
    flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC
    with os.fdopen(
        open_inside(directory, path, flags, make_dirs=True), "w", encoding="utf-8"
    ) as fh:
        fh.write(text)


def walk_plain(source: Path, skip_made: bool = False) -> list[Path]:
    """The directory's regular files, as paths under it: never a link, to a file or a folder."""
    found: list[Path] = []
    for root, folders, files in os.walk(source, followlinks=False):
        here = Path(root)
        folders[:] = sorted(
            f
            for f in folders
            if not (here / f).is_symlink() and not (skip_made and f.lower() in MADE)
        )
        for name in sorted(files):
            path = here / name
            if not path.is_symlink() and path.is_file():
                found.append(path.relative_to(source))
    return found


def copy_plain(source: Path, target: Path, skip_made: bool = False) -> None:
    """The directory's regular files into `target`: no links, and none of what runs make."""
    target.mkdir(parents=True, exist_ok=True)
    for name in walk_plain(source, skip_made):
        copied = target / name
        copied.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source / name, copied, follow_symlinks=False)


def snapshot(directory: Path) -> dict[str, str]:
    """Each file a clean run starts from, by its hash: a round's starting state."""
    found: dict[str, str] = {}
    for name in walk_plain(directory, skip_made=True):
        digest = hashlib.sha256()
        with os.fdopen(open_inside(directory, str(name), os.O_RDONLY), "rb") as fh:
            for chunk in iter(lambda: fh.read(1 << 20), b""):
                digest.update(chunk)
        found[str(name)] = digest.hexdigest()
    return found


def size_of(directory: Path) -> int:
    """The bytes under the directory, links not followed."""
    total = 0
    for root, _, files in os.walk(directory, followlinks=False):
        for name in files:
            try:
                total += os.lstat(Path(root) / name).st_size
            except OSError:
                continue
    return total


def texts(directory: Path) -> dict[str, str]:
    """The text files a clean run starts from, for a round's diff."""
    found: dict[str, str] = {}
    for name in walk_plain(directory, skip_made=True):
        if name.suffix.lower() not in TEXT:
            continue
        if (directory / name).stat().st_size > DIFF_BYTES:
            found[str(name)] = "(too large to show)\n"
            continue
        try:
            found[str(name)] = read_inside(directory, str(name))
        except (OSError, UnicodeDecodeError):
            continue
    return found
