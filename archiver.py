"""Zip and unzip helpers (used by the downloader and by the unzip mode).

Supports creating .zip archives and extracting .zip, .tar, .tar.gz/.tgz,
.tar.bz2, .tar.xz and .7z (7z needs the optional `py7zr` package).
Extraction is protected against "zip slip" (paths escaping the target folder).
"""

from __future__ import annotations

import tarfile
import zipfile
from pathlib import Path
from typing import Callable, Iterable

ARCHIVE_EXTS = (".tar.gz", ".tar.bz2", ".tar.xz", ".tgz", ".tbz2", ".txz",
                ".tar", ".zip", ".7z")

Progress = Callable[[str], None]


def is_archive(path: str | Path) -> bool:
    return str(path).lower().endswith(ARCHIVE_EXTS)


def strip_archive_ext(name: str) -> str:
    low = name.lower()
    for ext in ARCHIVE_EXTS:  # longest extensions are listed first
        if low.endswith(ext):
            return name[: -len(ext)]
    return name


def make_zip(files: Iterable[str | Path], dest: str | Path,
             base_dir: str | Path | None = None, level: int = 6,
             progress: Progress | None = None) -> Path:
    """Put files into one .zip archive. Paths are stored relative to base_dir."""
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    base = Path(base_dir).resolve() if base_dir else None
    used: set[str] = set()
    with zipfile.ZipFile(dest, "w", compression=zipfile.ZIP_DEFLATED,
                         compresslevel=level, allowZip64=True) as zf:
        for f in files:
            f = Path(f)
            if not f.is_file():
                continue
            arc = f.name
            if base:
                try:
                    arc = f.resolve().relative_to(base).as_posix()
                except ValueError:
                    pass
            n, original = 1, arc
            while arc in used:
                p = Path(original)
                arc = p.with_name(f"{p.stem} ({n}){p.suffix}").as_posix()
                n += 1
            used.add(arc)
            zf.write(f, arc)
            if progress:
                progress(f"Zipped {arc}")
    return dest


def _check_inside(dest: Path, member: str) -> None:
    target = (dest / member).resolve()
    if not target.is_relative_to(dest.resolve()):
        raise ValueError(f"Unsafe path in archive, refusing to extract: {member}")


def list_contents(archive: str | Path) -> list[str]:
    archive = Path(archive)
    name = archive.name.lower()
    if name.endswith(".zip"):
        with zipfile.ZipFile(archive) as zf:
            return zf.namelist()
    if name.endswith(".7z"):
        py7zr = _need_py7zr()
        with py7zr.SevenZipFile(archive) as z:
            return z.getnames()
    with tarfile.open(archive) as tf:
        return tf.getnames()


def extract(archive: str | Path, dest: str | Path | None = None,
            progress: Progress | None = None) -> Path:
    """Extract an archive. Default destination: a folder named after it."""
    archive = Path(archive)
    if not archive.is_file():
        raise FileNotFoundError(archive)
    dest = Path(dest) if dest else archive.parent / strip_archive_ext(archive.name)
    dest.mkdir(parents=True, exist_ok=True)
    name = archive.name.lower()

    if name.endswith(".zip"):
        with zipfile.ZipFile(archive) as zf:
            for m in zf.infolist():
                _check_inside(dest, m.filename)
            for m in zf.infolist():
                zf.extract(m, dest)
                if progress and not m.is_dir():
                    progress(f"Extracted {m.filename}")
    elif name.endswith(".7z"):
        py7zr = _need_py7zr()
        with py7zr.SevenZipFile(archive) as z:
            names = z.getnames()
            for n in names:
                _check_inside(dest, n)
            z.extractall(dest)
        if progress:
            for n in names:
                progress(f"Extracted {n}")
    elif tarfile.is_tarfile(archive):
        with tarfile.open(archive) as tf:
            members = []
            for m in tf.getmembers():
                _check_inside(dest, m.name)
                if m.issym() or m.islnk() or m.isdev():
                    continue  # skip links/devices for safety
                members.append(m)
            kwargs = {"filter": "data"} if hasattr(tarfile, "data_filter") else {}
            tf.extractall(dest, members=members, **kwargs)
        if progress:
            for m in members:
                if m.isfile():
                    progress(f"Extracted {m.name}")
    else:
        raise ValueError(f"Unsupported archive type: {archive.name}")
    return dest


def _need_py7zr():
    try:
        import py7zr
    except ImportError as e:
        raise RuntimeError(".7z support needs the py7zr package: pip install py7zr") from e
    return py7zr
