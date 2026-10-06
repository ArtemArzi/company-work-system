"""Small stdlib OS contracts. No business/runtime modules are imported here."""
from __future__ import annotations
import contextlib
import os
from pathlib import Path, PurePosixPath
import re
import shlex
import stat
import tempfile
import unicodedata

WINDOWS = os.name == "nt"


def portable_path(relative):
    if not isinstance(relative, str) or not relative or relative != PurePosixPath(relative).as_posix():
        raise ValueError("canonical relative path required")
    for part in relative.split("/"):
        stem = part.split(".", 1)[0].upper()
        if (part in {"", ".", ".."} or part.endswith((".", " ")) or
            re.search(r'[\\:<>"|?*\x00-\x1f\x7f]', part) or
            stem in {"CON", "PRN", "AUX", "NUL"} or re.fullmatch(r"(?:COM|LPT)[1-9¹²³]", stem)):
            raise ValueError("nonportable path component: " + part)
    return relative


def path_collisions(relatives):
    seen = {}
    for relative in relatives:
        portable_path(relative)
        # Check directory components too: A/x and a/y collide on Windows/macOS.
        for i in range(1, len(relative.split("/")) + 1):
            prefix = "/".join(relative.split("/")[:i])
            key = unicodedata.normalize("NFC", prefix).casefold()
            if key in seen and seen[key] != prefix:
                raise ValueError("case/Unicode path collision: " + seen[key] + " / " + prefix)
            seen[key] = prefix


def alias(file):
    file = Path(file)
    try:
        metadata = file.lstat()  # Do not follow a Windows file-typed directory link.
    except FileNotFoundError:
        return False
    return stat.S_ISLNK(metadata.st_mode) or bool(getattr(metadata, "st_file_attributes", 0) & 0x400)


def no_alias_parents(root, file, include_leaf=True):
    root, file = Path(root), Path(file)
    candidates = [file, *file.parents] if include_leaf else list(file.parents)
    for candidate in candidates:
        if candidate == root:
            break
        if alias(candidate):
            raise ValueError("symlink/junction source is not canonical")


def projection(root, directory, name, allow_absent=True):
    """Validate one declared projection; never follow a copied method directory."""
    root = Path(root).resolve()
    file = root / directory / name
    expected = "../../skills/" + name
    no_alias_parents(root, file, include_leaf=False)
    source = root / "skills" / name / "SKILL.md"
    no_alias_parents(root, source)
    if not source.is_file():
        raise ValueError("canonical skill missing")
    if file.is_symlink():
        if file.readlink().as_posix() != expected or file.resolve() != root / "skills" / name:
            raise ValueError("projection target changed")
        return "symlink"
    if not file.exists() and allow_absent:
        return "explicit-read"
    if file.is_file() and not alias(file) and file.read_bytes() == expected.encode("utf-8"):
        return "git-placeholder"
    raise ValueError("projection missing/changed")


def atomic_text(file, value):
    file = Path(file)
    file.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=".write-", dir=file.parent)
    replaced = False
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(value)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, file)
        replaced = True
        if not WINDOWS:
            directory = os.open(file.parent, os.O_RDONLY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
    except OSError as exc:
        if replaced:
            raise OSError("replacement applied; directory durability failed; read back before retry") from exc
        raise
    finally:
        if os.path.exists(name):
            os.unlink(name)


@contextlib.contextmanager
def process_lock(file):
    file = Path(file)
    file.parent.mkdir(parents=True, exist_ok=True)
    with file.open("a+b") as stream:
        if WINDOWS:
            import msvcrt
            stream.seek(0)
            try:
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError as exc:
                raise BlockingIOError("company operation already running") from exc
            try:
                yield
            finally:
                stream.seek(0)
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
            try:
                yield
            finally:
                fcntl.flock(stream, fcntl.LOCK_UN)


def shell_command(arguments, windows=False):
    return "& " + " ".join("'" + str(arg).replace("'", "''") + "'" for arg in arguments) if windows else shlex.join([str(arg) for arg in arguments])
