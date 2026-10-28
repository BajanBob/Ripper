#!/usr/bin/env python3
"""Command-line video downloader with a built-in unzip mode.

Download examples:
    python cli.py https://www.youtube.com/watch?v=VIDEO_ID
    python cli.py URL1 URL2 -q 720p -F mkv -o ~/Videos
    python cli.py -f links.txt --zip --compress
    python cli.py URL --audio --audio-format m4a
    python cli.py URL --tor
    python cli.py URL --ssh me@myserver.com
    python cli.py URL --vpn my-vpn.conf
    python cli.py URL --proxy socks5://user:pass@vpn-provider:1080
    python cli.py --tor --check
    python cli.py URL --info

Unzip examples:
    python cli.py unzip videos.zip
    python cli.py unzip a.zip b.tar.gz -o extracted/
    python cli.py unzip videos.zip --list
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from archiver import extract, list_contents
from downloader import (AUDIO_FORMATS, QUALITY_PRESETS, VIDEO_FORMATS,
                        DownloadOptions, download, get_info, has_ffmpeg,
                        read_url_file)
from network import Connection, NetworkOptions, public_ip


def progress_hook(d: dict) -> None:
    status = d.get("status")
    name = Path(d.get("filename", "")).name
    if status == "downloading":
        pct = d.get("_percent_str", "").strip()
        speed = d.get("_speed_str", "").strip()
        eta = d.get("_eta_str", "").strip()
        sys.stdout.write(f"\r  ↓ {pct:>6}  {speed:>11}  ETA {eta:>8}  {name[:50]}")
        sys.stdout.flush()
    elif status == "finished":
        sys.stdout.write(f"\r  ✓ Downloaded {name[:70]}{' ' * 20}\n")


def say(msg: str) -> None:
    print(f"  • {msg}")


def fmt_duration(seconds) -> str:
    if not seconds:
        return "?"
    seconds = int(seconds)
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def unzip_main(argv: list[str]) -> int:
    p = argparse.ArgumentParser(prog="cli.py unzip",
                                description="Extract .zip, .tar(.gz/.bz2/.xz) and .7z archives.")
    p.add_argument("archives", nargs="+", help="Archive file(s) to extract")
    p.add_argument("-o", "--output", help="Destination folder (default: a folder named after each archive)")
    p.add_argument("-l", "--list", action="store_true", help="List contents only")
    args = p.parse_args(argv)

    failed = 0
    for a in args.archives:
        try:
            if args.list:
                print(f"\n{a}:")
                for n in list_contents(a):
                    print(f"  {n}")
                continue
            dest = Path(args.output) / Path(a).stem if args.output and len(args.archives) > 1 \
                else (Path(args.output) if args.output else None)
            out = extract(a, dest, progress=say)
            print(f"✓ {a} → {out.resolve()}")
        except Exception as e:  # noqa: BLE001
            failed += 1
            print(f"✗ {a}: {e}")
    return 0 if not failed else 2


def download_main(argv: list[str]) -> int:
    p = argparse.ArgumentParser(
        description="Download videos from links (YouTube, Vimeo, X, TikTok and 1,000+ sites).",
        epilog="Unzip mode: python cli.py unzip ARCHIVE [ARCHIVE ...] [-o DEST] [--list]")
    p.add_argument("urls", nargs="*", help="One or more video URLs")
    p.add_argument("-f", "--file", help="Text file with one URL per line")
    p.add_argument("-o", "--output", default="downloads", help="Output folder (default: downloads)")
    p.add_argument("-q", "--quality", default="best",
                   help=f"One of {', '.join(QUALITY_PRESETS)} or a raw yt-dlp format string")
    p.add_argument("-F", "--format", default="mp4", choices=VIDEO_FORMATS, help="Video file format (default mp4)")
    p.add_argument("-a", "--audio", action="store_true", help="Extract audio only")
    p.add_argument("--audio-format", default="mp3", choices=AUDIO_FORMATS)
    p.add_argument("-p", "--playlist", action="store_true", help="Download the whole playlist if the link is one")
    p.add_argument("-s", "--subs", action="store_true", help="Also download English subtitles")
    p.add_argument("-c", "--compress", action="store_true",
                   help="Shrink videos by re-encoding to H.265 (saves ~30-50%%, slower)")
    p.add_argument("-z", "--zip", action="store_true", help="Bundle all downloaded files into one .zip")
    p.add_argument("--zip-name", help="Name for the .zip (default: videos-<date>.zip)")
    p.add_argument("--keep", action="store_true", help="Keep the loose files after zipping")
    net = p.add_argument_group("private connection (pick one)")
    route = net.add_mutually_exclusive_group()
    route.add_argument("--tor", action="store_true",
                       help="Go through Tor (uses Tor Browser/tor if running, else starts tor)")
    route.add_argument("--ssh", metavar="USER@HOST", help="SSH tunnel through your own server")
    route.add_argument("--vpn", metavar="WG.CONF", help="VPN using your provider's WireGuard .conf (needs wireproxy)")
    route.add_argument("--proxy", metavar="URL",
                       help="Any proxy, incl. a VPN provider's SOCKS5: socks5://user:pass@host:1080")
    net.add_argument("--ssh-port", type=int, default=22)
    net.add_argument("--ssh-key", help="SSH private key file")
    net.add_argument("--tor-path", help="Path to the tor program if it isn't found automatically")
    net.add_argument("--no-killswitch", action="store_true",
                     help="Don't verify the route before downloading (not recommended)")
    net.add_argument("--check", action="store_true", help="Only test the connection and show the IP sites will see")
    p.add_argument("-i", "--info", action="store_true", help="Show video info only, don't download")
    args = p.parse_args(argv)

    mode = ("tor" if args.tor else "ssh" if args.ssh else "wireguard" if args.vpn
            else "proxy" if args.proxy else "direct")
    network = NetworkOptions(mode=mode, proxy=args.proxy, ssh_target=args.ssh,
                             ssh_port=args.ssh_port, ssh_key=args.ssh_key,
                             wg_config=args.vpn, tor_path=args.tor_path,
                             verify=not args.no_killswitch)

    if args.check:
        try:
            with Connection(network, say) as conn:
                if mode == "direct":
                    print(f"Direct connection. Sites see your real IP: {public_ip(None)}")
                elif not network.verify:
                    conn.verify()
            return 0
        except Exception as e:  # noqa: BLE001
            print(f"✗ {e}")
            return 3

    urls = list(args.urls)
    if args.file:
        urls += read_url_file(args.file)
    if not urls:
        p.print_help()
        return 1

    if args.info:
        for url in urls:
            try:
                info = get_info(url, network, say)
            except ConnectionError as e:
                print(f"✗ Stopped, nothing sent to the site: {e}")
                return 3
            if not info:
                print(f"✗ Could not read {url}")
                continue
            print(f"\nTitle:    {info.get('title')}")
            print(f"Uploader: {info.get('uploader')}")
            print(f"Duration: {fmt_duration(info.get('duration'))}")
            heights = sorted({f.get('height') for f in info.get('formats', []) if f.get('height')})
            if heights:
                print(f"Qualities: {', '.join(f'{h}p' for h in heights)}")
        return 0

    if not has_ffmpeg():
        print("⚠ ffmpeg not found: falling back to single-file formats; format conversion "
              "and shrinking are disabled. Install ffmpeg for best results.\n")

    opts = DownloadOptions(
        output_dir=Path(args.output).expanduser(),
        quality=args.quality,
        video_format=args.format,
        audio_only=args.audio,
        audio_format=args.audio_format,
        playlist=args.playlist,
        subtitles=args.subs,
        compress=args.compress,
        zip_output=args.zip,
        zip_name=args.zip_name,
        keep_files=args.keep,
        network=network,
    )

    print(f"Downloading {len(urls)} link(s) to {opts.output_dir.resolve()}\n")
    try:
        report = download(urls, opts, progress_hook, say)
    except (ConnectionError, ValueError) as e:
        print(f"✗ Stopped, nothing was downloaded: {e}")
        return 3

    print(f"\nDone: {report.ok}/{len(report.results)} succeeded.")
    for url, success in report.results.items():
        if not success:
            print(f"  ✗ Failed: {url}")
    if report.archive:
        print(f"  📦 Archive: {report.archive.resolve()} ({report.archive.stat().st_size / 1e6:.1f} MB)")
    return 0 if report.ok == len(report.results) else 2


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if argv and argv[0] in ("unzip", "extract"):
        return unzip_main(argv[1:])
    return download_main(argv)


if __name__ == "__main__":
    sys.exit(main())
