"""Core download logic shared by the CLI and GUI.

Wraps yt-dlp so any site it supports (YouTube, Vimeo, X/Twitter, TikTok,
Reddit, Instagram, direct .mp4 links, and 1,000+ more) works out of the box.
"""

from __future__ import annotations

import glob
import os
import shutil
import subprocess
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable, Iterable

import yt_dlp

from archiver import make_zip
from network import Connection, NetworkOptions

QUALITY_PRESETS = {
    "best": "bestvideo*+bestaudio/best",
    "1080p": "bestvideo*[height<=1080]+bestaudio/best[height<=1080]",
    "720p": "bestvideo*[height<=720]+bestaudio/best[height<=720]",
    "480p": "bestvideo*[height<=480]+bestaudio/best[height<=480]",
    "audio": "bestaudio/best",
}
VIDEO_FORMATS = ["mp4", "mkv", "webm", "mov", "avi"]
AUDIO_FORMATS = ["mp3", "m4a", "aac", "wav", "flac", "opus", "ogg"]
VIDEO_EXTS = {f".{x}" for x in VIDEO_FORMATS}
SUB_EXTS = {".vtt", ".srt", ".ass", ".ttml"}

ProgressCallback = Callable[[dict], None]
StatusCallback = Callable[[str], None]


@dataclass
class DownloadOptions:
    output_dir: Path = Path("downloads")
    quality: str = "best"
    video_format: str = "mp4"
    audio_only: bool = False
    audio_format: str = "mp3"
    playlist: bool = False
    subtitles: bool = False
    compress: bool = False          # re-encode video to H.265 to save space
    zip_output: bool = False        # bundle everything into one .zip
    zip_name: str | None = None
    keep_files: bool = False        # keep loose files after zipping
    network: NetworkOptions = field(default_factory=NetworkOptions)  # Tor / SSH / VPN / proxy
    interactive: bool = True        # allow SSH password prompts (CLI)
    filename_template: str = "%(title)s [%(id)s].%(ext)s"
    extra: dict = field(default_factory=dict)


@dataclass
class DownloadReport:
    results: dict[str, bool] = field(default_factory=dict)
    files: list[Path] = field(default_factory=list)
    archive: Path | None = None

    @property
    def ok(self) -> int:
        return sum(self.results.values())


def find_ffmpeg() -> str | None:
    """System ffmpeg if installed, otherwise the copy bundled by imageio-ffmpeg."""
    exe = shutil.which("ffmpeg")
    if exe:
        return exe
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:  # noqa: BLE001
        return None


def has_ffmpeg() -> bool:
    return find_ffmpeg() is not None


def build_ydl_opts(opts: DownloadOptions, progress: ProgressCallback | None = None,
                   proxy: str | None = None) -> dict:
    opts.output_dir.mkdir(parents=True, exist_ok=True)
    quality = "audio" if opts.audio_only else opts.quality
    fmt = QUALITY_PRESETS.get(quality, quality)  # allow raw yt-dlp format strings

    ydl_opts: dict = {
        "format": fmt,
        "outtmpl": str(opts.output_dir / opts.filename_template),
        "noplaylist": not opts.playlist,
        "ignoreerrors": True,
        "quiet": True,
        "no_warnings": True,
        "windowsfilenames": True,
    }
    if proxy:
        ydl_opts["proxy"] = proxy

    ffmpeg = find_ffmpeg()
    if ffmpeg:
        ydl_opts["ffmpeg_location"] = ffmpeg
        pps: list[dict] = []
        if opts.audio_only:
            pps.append({"key": "FFmpegExtractAudio",
                        "preferredcodec": opts.audio_format,
                        "preferredquality": "192"})
        else:
            vf = opts.video_format.lower()
            merge = "mp4" if vf in ("mp4", "mov") else "mkv"
            ydl_opts["merge_output_format"] = merge
            if vf != merge:
                pps.append({"key": "FFmpegVideoConvertor", "preferedformat": vf})
        if pps:
            ydl_opts["postprocessors"] = pps
    elif "+" in fmt:
        # Without ffmpeg we can't merge separate video/audio streams.
        ydl_opts["format"] = "best"

    if opts.subtitles:
        ydl_opts.update({"writesubtitles": True, "writeautomaticsub": True,
                         "subtitleslangs": ["en.*"]})
    if progress:
        ydl_opts["progress_hooks"] = [progress]

    ydl_opts.update(opts.extra)
    return ydl_opts


def get_info(url: str, network: NetworkOptions | None = None,
             status: StatusCallback | None = None) -> dict | None:
    """Fetch metadata (title, duration, formats) without downloading."""
    with Connection(network, status) as conn:
        o = {"quiet": True, "no_warnings": True, "skip_download": True}
        if conn.proxy:
            o["proxy"] = conn.proxy
        with yt_dlp.YoutubeDL(o) as ydl:
            return ydl.extract_info(url, download=False)


def compress_video(path: Path, ffmpeg: str | None = None,
                   status: StatusCallback | None = None) -> Path:
    """Re-encode a video to H.265 (VP9 for .webm). Typically 30-50% smaller.

    Keeps the original if encoding fails or the result isn't smaller.
    """
    ffmpeg = ffmpeg or find_ffmpeg()
    ext = path.suffix.lower()
    if not ffmpeg or ext not in VIDEO_EXTS or ext == ".avi":
        return path
    say = status or (lambda _m: None)
    tmp = path.with_name(f"{path.stem}.shrinking{path.suffix}")
    if ext == ".webm":
        vargs = ["-c:v", "libvpx-vp9", "-crf", "36", "-b:v", "0", "-row-mt", "1"]
    else:
        vargs = ["-c:v", "libx265", "-crf", "28", "-preset", "medium"]
        if ext in (".mp4", ".mov"):
            vargs += ["-tag:v", "hvc1"]
    say(f"Shrinking {path.name} (this can take a while)…")
    cmd = [ffmpeg, "-y", "-loglevel", "error", "-i", str(path), *vargs,
           "-c:a", "copy", "-c:s", "copy", str(tmp)]
    try:
        subprocess.run(cmd, check=True, capture_output=True)
    except (subprocess.CalledProcessError, OSError):
        tmp.unlink(missing_ok=True)
        say(f"Could not shrink {path.name}; kept the original.")
        return path
    before, after = path.stat().st_size, tmp.stat().st_size
    if after >= before:
        tmp.unlink(missing_ok=True)
        say(f"{path.name} is already compact; kept the original.")
        return path
    os.replace(tmp, path)
    say(f"Shrunk {path.name}: {before / 1e6:.1f} MB → {after / 1e6:.1f} MB")
    return path


def download(urls: Iterable[str], opts: DownloadOptions,
             progress: ProgressCallback | None = None,
             status: StatusCallback | None = None) -> DownloadReport:
    """Download each URL, then optionally shrink and zip the results.

    With a Tor/SSH/VPN/proxy route, the route is opened and checked first;
    if the check fails a ConnectionError is raised and nothing is downloaded.
    """
    say = status or (lambda _m: None)
    report = DownloadReport()
    collected: list[Path] = []

    with Connection(opts.network, say, interactive=opts.interactive) as conn:
        ydl_opts = build_ydl_opts(opts, progress, conn.proxy)
        # Called with the final file path after all post-processing.
        ydl_opts["post_hooks"] = [lambda fp: collected.append(Path(fp))]
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            for url in urls:
                url = url.strip()
                if not url or url.startswith("#"):
                    continue
                try:
                    report.results[url] = ydl.download([url]) == 0
                except Exception:  # noqa: BLE001 - report and keep going
                    report.results[url] = False

    files: list[Path] = []
    for f in collected:
        if f.exists() and f not in files:
            files.append(f)
        if opts.subtitles:
            for sib in f.parent.glob(glob.escape(f.stem) + ".*"):
                if sib.suffix.lower() in SUB_EXTS and sib not in files:
                    files.append(sib)

    if opts.compress and not opts.audio_only:
        ffmpeg = find_ffmpeg()
        if not ffmpeg:
            say("Shrinking skipped: ffmpeg not found.")
        else:
            files = [compress_video(f, ffmpeg, say) for f in files]

    report.files = files
    if opts.zip_output and files:
        name = opts.zip_name or f"videos-{datetime.now():%Y%m%d-%H%M%S}"
        if not name.lower().endswith(".zip"):
            name += ".zip"
        say(f"Creating {name}…")
        report.archive = make_zip(files, opts.output_dir / name,
                                  base_dir=opts.output_dir, progress=say)
        if not opts.keep_files:
            for f in files:
                f.unlink(missing_ok=True)
    return report


def read_url_file(path: str | Path) -> list[str]:
    lines = Path(path).read_text(encoding="utf-8").splitlines()
    return [ln.strip() for ln in lines if ln.strip() and not ln.strip().startswith("#")]
