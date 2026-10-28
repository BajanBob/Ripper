# Video Downloader

Download videos (or just the audio) from links on YouTube, Vimeo, X/Twitter, TikTok, Reddit, Instagram, Facebook, direct `.mp4` links and [1,000+ other sites](https://github.com/yt-dlp/yt-dlp/blob/master/supportedsites.md). It includes a desktop app, a command-line tool and a built-in **unzip mode**. Powered by [yt-dlp](https://github.com/yt-dlp/yt-dlp).

## Features
- Single links, many links, or a text file of links; whole playlists
- Quality presets: `best`, `1080p`, `720p`, `480p`
- Video formats: **MP4, MKV, WebM, MOV, AVI**
- Audio-only formats: **MP3, M4A, AAC, WAV, FLAC, Opus, OGG**
- **Save as one .zip**: bundle a batch of downloads into a single archive
- **Shrink videos**: re-encode to H.265 (VP9 for WebM). Files are typically 30–50% smaller
- **Unzip mode**: extract `.zip`, `.tar`, `.tar.gz`, `.tar.bz2`, `.tar.xz` and `.7z`, with protection against malicious paths
- **Private connection modes:** Tor, SSH tunnel, VPN (WireGuard) and proxy, with a kill switch and no DNS leaks
- Optional subtitles, live progress, speed and ETA
- Ready-made **Windows, macOS and Linux** executables via GitHub Actions

> **About saving space:** videos and audio are already compressed, so putting them in a .zip only shrinks them by about 0–2%. The zip option is for keeping a batch together as one file. To actually save space, use **Shrink videos** (`--compress`).

## Download the app (no Python needed)
Every push to `main` builds the apps automatically. Open the repo's **Actions** tab, pick the latest run, and download `VideoDownloader-windows`, `-macos` or `-linux` from **Artifacts**.

To publish a proper release with download links:
```bash
git tag v1.0.0 && git push origin v1.0.0
```
The zipped apps then appear under **Releases**. Each one contains:
- `VideoDownloader` (`.exe` on Windows): the desktop app
- `vdl`: the command-line tool

ffmpeg is bundled, so nothing else needs installing.
- **macOS:** the first time, right-click the app, choose **Open**, and confirm. The app isn't signed with an Apple developer certificate.
- **Linux:** run `chmod +x VideoDownloader vdl` first.

## Run from source
Requires Python 3.9+.
```bash
git clone https://github.com/<your-username>/video-downloader.git
cd video-downloader
pip install -r requirements.txt
python gui.py          # desktop app
python cli.py --help   # command line
```

## Command line

### Download
```bash
python cli.py "https://www.youtube.com/watch?v=VIDEO_ID"     # best quality MP4
python cli.py URL1 URL2 -q 720p -F mkv -o ~/Videos           # 720p MKV in a folder
python cli.py URL --audio --audio-format flac                # audio only
python cli.py -f links.txt --zip                             # every link → one .zip
python cli.py -f links.txt --compress --zip --zip-name trip  # shrink, then trip.zip
python cli.py PLAYLIST_URL --playlist --subs                 # playlist + subtitles
python cli.py URL --tor                                      # through Tor
python cli.py URL --vpn my-vpn.conf                          # through a WireGuard VPN
python cli.py URL --info                                     # info only
```

| Option | Description |
|---|---|
| `-f, --file` | Text file with one URL per line (`#` lines ignored) |
| `-o, --output` | Output folder (default `downloads`) |
| `-q, --quality` | `best`, `1080p`, `720p`, `480p`, or any yt-dlp format string |
| `-F, --format` | `mp4` (default), `mkv`, `webm`, `mov`, `avi` |
| `-a, --audio` | Audio only |
| `--audio-format` | `mp3` (default), `m4a`, `aac`, `wav`, `flac`, `opus`, `ogg` |
| `-p, --playlist` | Download the full playlist |
| `-s, --subs` | Download English subtitles |
| `-c, --compress` | Shrink videos with H.265 (slower, much smaller) |
| `-z, --zip` | Bundle all downloaded files into one .zip |
| `--zip-name` | Archive name (default `videos-<date-time>.zip`) |
| `--keep` | Keep the loose files after zipping |
| `--tor` / `--ssh` / `--vpn` / `--proxy` | Private connection (see Privacy) |
| `--check` | Test the connection and show the IP sites will see |
| `--no-killswitch` | Skip the route check (not recommended) |
| `-i, --info` | Show info only |

### Unzip mode
```bash
python cli.py unzip videos.zip                 # → ./videos/
python cli.py unzip a.zip b.tar.gz -o out/     # → out/a/, out/b.tar/
python cli.py unzip videos.zip --list          # show contents only
```

## Privacy: Tor, SSH tunnel and VPN
Downloads from HTTPS sites (nearly every video site) are **encrypted** in transit. By default they are **not anonymous**: the site sees your IP address, and your ISP sees which site you visit. Choose a protected connection in the app's **Connection** box, or with these command-line flags:

| Mode | Flag | What you need |
|---|---|---|
| **Tor** | `--tor` | [Tor Browser](https://www.torproject.org) open in the background, or the `tor` program (`sudo apt install tor`, `brew install tor`). The app finds Tor Browser or a running tor, or starts tor itself. |
| **SSH tunnel** | `--ssh user@host` | A server you can SSH into. Use key login for the desktop app; the command line can also ask for a password. Optional `--ssh-port`, `--ssh-key`. |
| **VPN (WireGuard)** | `--vpn my.conf` | Your VPN provider's WireGuard `.conf` file (Mullvad, Proton VPN, IVPN, etc.) and [wireproxy](https://github.com/windtf/wireproxy/releases) on your PATH or next to the app. This doesn't need admin rights and only the downloader's traffic goes through the VPN. |
| **VPN / proxy server** | `--proxy URL` | Any proxy, such as your VPN provider's SOCKS5 server: `socks5://user:pass@host:1080` |

Test a route without downloading anything:
```bash
python cli.py --tor --check
```

**How it protects you:**
- **Kill switch (on by default):** before anything is sent to a video site, the app checks the route. With Tor, it asks the Tor Project whether traffic really exits through Tor. Other routes must show a different IP than your real one. If the check fails, nothing is downloaded. (`--no-killswitch` turns this off.)
- **No DNS leaks:** SOCKS routes resolve website names through the tunnel (`socks5h`), so your ISP doesn't see them either.
- **Tunnels close automatically** when the download finishes.

**Limits:** these routes hide your IP from the site, but the Tor exit, SSH server or VPN provider can see your traffic metadata. Many video sites slow down or block Tor and some VPN addresses. Tor downloads are much slower. If you already use a system-wide VPN app, you don't need any of these modes.

## Mobile (iOS / Android)
- **iOS:** not supported. Apple only allows apps through the App Store, which doesn't accept video downloaders for sites like YouTube, and Python desktop apps can't run on iOS.
- **Android:** this desktop app can't be packaged as an APK as-is (Tkinter doesn't run on Android). A separate Android app could be built, or you can run the command-line tool today in [Termux](https://termux.dev): `pkg install python ffmpeg && pip install yt-dlp`.

## Troubleshooting
- **A site stopped working:** sites change often. Run `pip install -U yt-dlp`, or download the latest release.
- **Shrinking is slow:** that's expected. H.265 encoding takes roughly as long as the video plays, or longer on slower PCs.
- **Private or age-restricted videos:** not supported by default; see yt-dlp's cookie options.

## Responsible use
Only download content you own, that is freely licensed, or that you have permission to download. Respect copyright and each site's terms of service.

## License
MIT
