#!/usr/bin/env python3
"""Desktop app (Tkinter — ships with Python). Two tabs: Download and Unzip."""

from __future__ import annotations

import queue
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from archiver import extract
from downloader import (AUDIO_FORMATS, QUALITY_PRESETS, VIDEO_FORMATS,
                        DownloadOptions, download, has_ffmpeg)
from network import MODE_LABELS, Connection, NetworkOptions, public_ip

ARCHIVE_TYPES = [("Archives", "*.zip *.tar *.gz *.tgz *.bz2 *.xz *.7z"), ("All files", "*.*")]


class App(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("Video Downloader")
        self.geometry("720x700")
        self.minsize(560, 480)
        self.events: queue.Queue = queue.Queue()

        nb = ttk.Notebook(self)
        nb.pack(fill="both", expand=True, padx=8, pady=8)
        dl, uz = ttk.Frame(nb), ttk.Frame(nb)
        nb.add(dl, text="  Download  ")
        nb.add(uz, text="  Unzip  ")
        self._build_download(dl)
        self._build_unzip(uz)
        self.after(100, self._poll)

    # ---------------- Download tab ----------------
    def _build_download(self, f: ttk.Frame) -> None:
        pad = {"padx": 8, "pady": 4}
        self.output_dir = tk.StringVar(value=str(Path.home() / "Downloads"))
        self.quality = tk.StringVar(value="best")
        self.fmt = tk.StringVar(value="mp4")
        self.audio_only = tk.BooleanVar(value=False)
        self.playlist = tk.BooleanVar(value=False)
        self.subs = tk.BooleanVar(value=False)
        self.compress = tk.BooleanVar(value=False)
        self.zip_output = tk.BooleanVar(value=False)
        self.keep_files = tk.BooleanVar(value=False)
        self.net_mode = tk.StringVar(value=MODE_LABELS["direct"])
        self.net_value = tk.StringVar(value="")
        self.killswitch = tk.BooleanVar(value=True)

        ttk.Label(f, text="Paste video links (one per line):").pack(anchor="w", **pad)
        self.urls = tk.Text(f, height=7, wrap="none")
        self.urls.pack(fill="both", expand=True, **pad)

        row = ttk.Frame(f); row.pack(fill="x", **pad)
        ttk.Label(row, text="Save to:").pack(side="left")
        ttk.Entry(row, textvariable=self.output_dir).pack(side="left", fill="x", expand=True, padx=6)
        ttk.Button(row, text="Browse…", command=self._browse_out).pack(side="left")

        row2 = ttk.Frame(f); row2.pack(fill="x", **pad)
        ttk.Label(row2, text="Quality:").pack(side="left")
        ttk.Combobox(row2, textvariable=self.quality, values=list(QUALITY_PRESETS)[:-1],
                     width=7, state="readonly").pack(side="left", padx=6)
        ttk.Label(row2, text="Format:").pack(side="left")
        self.fmt_box = ttk.Combobox(row2, textvariable=self.fmt, values=VIDEO_FORMATS,
                                    width=6, state="readonly")
        self.fmt_box.pack(side="left", padx=6)
        ttk.Checkbutton(row2, text="Audio only", variable=self.audio_only,
                        command=self._toggle_audio).pack(side="left", padx=6)

        row3 = ttk.Frame(f); row3.pack(fill="x", **pad)
        ttk.Checkbutton(row3, text="Whole playlist", variable=self.playlist).pack(side="left", padx=4)
        ttk.Checkbutton(row3, text="Subtitles", variable=self.subs).pack(side="left", padx=4)
        ttk.Checkbutton(row3, text="Shrink videos (H.265)", variable=self.compress).pack(side="left", padx=4)

        row4 = ttk.Frame(f); row4.pack(fill="x", **pad)
        ttk.Checkbutton(row4, text="Save as one .zip", variable=self.zip_output).pack(side="left", padx=4)
        ttk.Checkbutton(row4, text="Keep files after zipping", variable=self.keep_files).pack(side="left", padx=4)

        conn = ttk.LabelFrame(f, text="Connection")
        conn.pack(fill="x", **pad)
        r1 = ttk.Frame(conn); r1.pack(fill="x", padx=6, pady=3)
        ttk.Combobox(r1, textvariable=self.net_mode, values=list(MODE_LABELS.values()),
                     width=26, state="readonly").pack(side="left")
        self.net_mode.trace_add("write", lambda *_: self._update_conn_fields())
        ttk.Checkbutton(r1, text="Kill switch", variable=self.killswitch).pack(side="left", padx=8)
        self.test_btn = ttk.Button(r1, text="Test", command=self._test_connection)
        self.test_btn.pack(side="right")
        r2 = ttk.Frame(conn); r2.pack(fill="x", padx=6, pady=3)
        self.net_label = ttk.Label(r2, text="", width=14)
        self.net_label.pack(side="left")
        self.net_entry = ttk.Entry(r2, textvariable=self.net_value)
        self.net_entry.pack(side="left", fill="x", expand=True, padx=6)
        self.net_browse = ttk.Button(r2, text="Browse…", command=self._browse_net)
        self.net_browse.pack(side="left")
        self.net_hint = ttk.Label(conn, text="", foreground="gray")
        self.net_hint.pack(anchor="w", padx=6, pady=(0, 4))
        self._update_conn_fields()

        self.dl_button = ttk.Button(f, text="Download", command=self._start_download)
        self.dl_button.pack(**pad)
        self.bar = ttk.Progressbar(f, maximum=100)
        self.bar.pack(fill="x", **pad)
        self.status = ttk.Label(f, text="" if has_ffmpeg() else
                                "Tip: install ffmpeg for best quality, format conversion and shrinking.")
        self.status.pack(anchor="w", **pad)

    # ---------------- Connection ----------------
    _CONN_UI = {  # mode: (field label, hint, show browse)
        "direct": ("", "Sites see your real IP address.", False),
        "tor": ("tor program:", "Uses Tor Browser if it's open, else starts tor. Optional: path to tor.", True),
        "ssh": ("user@host:", "Key-based login to your own server (add :port if not 22).", False),
        "wireguard": ("WireGuard .conf:", "Config file from your VPN provider. Needs wireproxy next to the app.", True),
        "proxy": ("Proxy URL:", "e.g. socks5://user:pass@host:1080 (your VPN provider's SOCKS5 server).", False),
    }

    def _mode(self) -> str:
        return next(k for k, v in MODE_LABELS.items() if v == self.net_mode.get())

    def _update_conn_fields(self) -> None:
        label, hint, browse = self._CONN_UI[self._mode()]
        self.net_label.config(text=label)
        self.net_entry.config(state="normal" if label else "disabled")
        self.net_browse.config(state="normal" if browse else "disabled")
        self.net_hint.config(text=hint)

    def _browse_net(self) -> None:
        if self._mode() == "wireguard":
            p = filedialog.askopenfilename(filetypes=[("WireGuard config", "*.conf"), ("All files", "*.*")])
        else:
            p = filedialog.askopenfilename()
        if p:
            self.net_value.set(p)

    def _network(self) -> NetworkOptions:
        mode, val = self._mode(), self.net_value.get().strip() or None
        net = NetworkOptions(mode=mode, verify=self.killswitch.get())
        if mode == "proxy":
            net.proxy = val
        elif mode == "tor":
            net.tor_path = val
        elif mode == "wireguard":
            net.wg_config = val
        elif mode == "ssh" and val:
            host, _, port = val.rpartition(":") if val.rsplit(":", 1)[-1].isdigit() else (val, "", "")
            net.ssh_target, net.ssh_port = host, int(port) if port else 22
        return net

    def _test_connection(self) -> None:
        self.test_btn.config(state="disabled")
        net = self._network()
        net.verify = True

        def work():
            try:
                with Connection(net, lambda m: self.events.put(("status", m)), interactive=False) as c:
                    msg = (f"Protected. Sites will see IP {c.exit_ip}." if net.mode != "direct"
                           else f"Direct connection. Sites see your real IP: {public_ip(None)}")
                self.events.put(("test", (True, msg)))
            except Exception as e:  # noqa: BLE001
                self.events.put(("test", (False, str(e))))
        threading.Thread(target=work, daemon=True).start()

    def _toggle_audio(self) -> None:
        if self.audio_only.get():
            self.fmt_box.config(values=AUDIO_FORMATS); self.fmt.set("mp3")
        else:
            self.fmt_box.config(values=VIDEO_FORMATS); self.fmt.set("mp4")

    def _browse_out(self) -> None:
        d = filedialog.askdirectory(initialdir=self.output_dir.get())
        if d:
            self.output_dir.set(d)

    def _start_download(self) -> None:
        urls = [u.strip() for u in self.urls.get("1.0", "end").splitlines() if u.strip()]
        if not urls:
            messagebox.showwarning("No links", "Paste at least one video link.")
            return
        self.dl_button.config(state="disabled")
        self.bar["value"] = 0
        audio = self.audio_only.get()
        opts = DownloadOptions(
            output_dir=Path(self.output_dir.get()),
            quality=self.quality.get(),
            video_format="mp4" if audio else self.fmt.get(),
            audio_only=audio,
            audio_format=self.fmt.get() if audio else "mp3",
            playlist=self.playlist.get(),
            subtitles=self.subs.get(),
            compress=self.compress.get(),
            zip_output=self.zip_output.get(),
            keep_files=self.keep_files.get(),
            network=self._network(),
            interactive=False,
        )
        threading.Thread(target=self._download_worker, args=(urls, opts), daemon=True).start()

    def _download_worker(self, urls, opts) -> None:
        try:
            report = download(urls, opts,
                              progress=lambda d: self.events.put(("progress", d)),
                              status=lambda m: self.events.put(("status", m)))
            self.events.put(("done", report))
        except Exception as e:  # noqa: BLE001
            self.events.put(("error", str(e)))

    # ---------------- Unzip tab ----------------
    def _build_unzip(self, f: ttk.Frame) -> None:
        pad = {"padx": 8, "pady": 4}
        self.uz_dest = tk.StringVar(value="")
        ttk.Label(f, text="Archives to extract (.zip, .tar.gz, .7z …):").pack(anchor="w", **pad)
        self.uz_list = tk.Listbox(f, height=6)
        self.uz_list.pack(fill="x", **pad)

        row = ttk.Frame(f); row.pack(fill="x", **pad)
        ttk.Button(row, text="Add archives…", command=self._add_archives).pack(side="left")
        ttk.Button(row, text="Clear", command=lambda: self.uz_list.delete(0, "end")).pack(side="left", padx=6)

        row2 = ttk.Frame(f); row2.pack(fill="x", **pad)
        ttk.Label(row2, text="Extract to:").pack(side="left")
        ttk.Entry(row2, textvariable=self.uz_dest).pack(side="left", fill="x", expand=True, padx=6)
        ttk.Button(row2, text="Browse…", command=self._browse_uz).pack(side="left")
        ttk.Label(f, text="Leave blank to extract next to each archive.").pack(anchor="w", **pad)

        self.uz_button = ttk.Button(f, text="Extract", command=self._start_unzip)
        self.uz_button.pack(**pad)
        self.uz_log = tk.Text(f, height=10, state="disabled")
        self.uz_log.pack(fill="both", expand=True, **pad)

    def _add_archives(self) -> None:
        for p in filedialog.askopenfilenames(filetypes=ARCHIVE_TYPES):
            self.uz_list.insert("end", p)

    def _browse_uz(self) -> None:
        d = filedialog.askdirectory()
        if d:
            self.uz_dest.set(d)

    def _log(self, msg: str) -> None:
        self.uz_log.config(state="normal")
        self.uz_log.insert("end", msg + "\n")
        self.uz_log.see("end")
        self.uz_log.config(state="disabled")

    def _start_unzip(self) -> None:
        archives = list(self.uz_list.get(0, "end"))
        if not archives:
            messagebox.showwarning("No archives", "Add at least one archive.")
            return
        self.uz_button.config(state="disabled")
        dest = self.uz_dest.get().strip() or None
        threading.Thread(target=self._unzip_worker, args=(archives, dest), daemon=True).start()

    def _unzip_worker(self, archives, dest) -> None:
        for a in archives:
            try:
                target = Path(dest) / Path(a).stem if dest and len(archives) > 1 else dest
                out = extract(a, target, progress=lambda m: self.events.put(("uz_log", m)))
                self.events.put(("uz_log", f"✓ {Path(a).name} → {out}"))
            except Exception as e:  # noqa: BLE001
                self.events.put(("uz_log", f"✗ {Path(a).name}: {e}"))
        self.events.put(("uz_done", None))

    # ---------------- Event loop ----------------
    def _poll(self) -> None:
        try:
            while True:
                kind, data = self.events.get_nowait()
                if kind == "progress" and data.get("status") == "downloading":
                    total = data.get("total_bytes") or data.get("total_bytes_estimate") or 0
                    if total:
                        self.bar["value"] = data.get("downloaded_bytes", 0) / total * 100
                    name = Path(data.get("filename", "")).name
                    self.status.config(text=f"{data.get('_percent_str', '').strip()}  {name[:60]}")
                elif kind == "progress" and data.get("status") == "finished":
                    self.status.config(text=f"Finished {Path(data.get('filename', '')).name[:60]}")
                elif kind == "status":
                    self.status.config(text=data[:90])
                elif kind == "done":
                    self.bar["value"] = 100
                    self.dl_button.config(state="normal")
                    msg = f"Done: {data.ok}/{len(data.results)} succeeded."
                    if data.archive:
                        msg += f"  Saved {data.archive.name}"
                    self.status.config(text=msg)
                    if data.ok < len(data.results):
                        failed = "\n".join(u for u, s in data.results.items() if not s)
                        messagebox.showerror("Some downloads failed", failed)
                elif kind == "error":
                    self.dl_button.config(state="normal")
                    self.status.config(text="Stopped. Nothing was downloaded.")
                    messagebox.showerror("Download stopped", data)
                elif kind == "test":
                    self.test_btn.config(state="normal")
                    ok, msg = data
                    self.status.config(text=msg[:90])
                    (messagebox.showinfo if ok else messagebox.showerror)("Connection test", msg)
                elif kind == "uz_log":
                    self._log(data)
                elif kind == "uz_done":
                    self.uz_button.config(state="normal")
        except queue.Empty:
            pass
        self.after(100, self._poll)


if __name__ == "__main__":
    App().mainloop()
