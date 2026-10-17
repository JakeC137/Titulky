"""Windowed front end for titulky.py, packaged as Titulky.exe.

Files dropped onto Titulky.exe are added to the list. `Titulky.exe --cli ...` runs the
command-line version instead (output goes to titulky.log, since the exe has no console).
Everything shown in the window also goes to titulky.log, to find out what went wrong on another PC.
"""
import json
import multiprocessing
import os
import platform
import queue
import subprocess
import sys
import threading
import time
import tkinter as tk
import traceback
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from tkinter.scrolledtext import ScrolledText

import titulky

SETTINGS_FILE = titulky.DATA_DIR / "settings.json"
LOG_FILE = titulky.DATA_DIR / "titulky.log"
LANGUAGES = [("Auto-detect", None), ("English", "en"), ("German", "de"), ("French", "fr"),
             ("Spanish", "es"), ("Italian", "it"), ("Polish", "pl"), ("Slovak", "sk"),
             ("Russian", "ru"), ("Japanese", "ja"), ("Korean", "ko"), ("Chinese", "zh")]
MODELS = [("large-v3: most accurate", "large-v3"),
          ("turbo: several times faster, nearly as accurate" if titulky.IS_MAC
           else "turbo: faster, better for PCs without an NVIDIA GPU", "turbo")]
DEFAULT_MODEL = "turbo" if titulky.IS_MAC else "large-v3"  # a Mac transcribes on the processor
if titulky.IS_MAC and titulky.FROZEN and titulky.bundled_models():
    # The Mac app offers only the models packed into it, so nothing has to be downloaded.
    MODELS = [m for m in MODELS if m[1] in titulky.bundled_models()]
TRANSLATORS = [("Auto: DeepL if a key is entered, otherwise Google", "auto"),
               ("Google Translate (free)", "google"), ("DeepL (needs API key)", "deepl"),
               ("Don't translate (original language only)", "none")]
FILE_TYPES = [("Audio or video", "*.mp3 *.wav *.m4a *.aac *.flac *.ogg *.opus *.wma "
                                 + " ".join("*" + ext for ext in titulky.VIDEO_EXTS)),
              ("Subtitles", "*.srt"), ("All files", "*.*")]


class GuiReporter(titulky.Reporter):
    """Forwards progress from the worker thread to the window through a queue."""

    def __init__(self, q, cancel):
        super().__init__()
        self.q, self.cancel = q, cancel

    def log(self, msg):
        self.q.put(("log", msg))
        print(msg, flush=True)  # into titulky.log

    def progress(self, label, fraction):
        self.q.put(("progress", label, fraction))

    def check_cancel(self):
        if self.cancel.is_set():
            raise titulky.Cancelled()


def load_settings():
    try:
        return json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def check_folder():
    """Warnings about missing parts of the app, e.g. after copying only some of the folder."""
    if not titulky.FROZEN:
        return []
    problems = []
    if not titulky.bundled_models():
        problems.append("No Whisper model came with Titulky, so it will be downloaded on first use "
                        "(1.6–3 GB).")
    if not titulky.IS_MAC and not (titulky.BUNDLE_DIR / "nvidia" / "cudnn" / "bin").is_dir():
        problems.append("The NVIDIA libraries (_internal\\nvidia) are missing, so the slow CPU "
                        "will be used.")
    if problems and not titulky.IS_MAC:
        problems.append("When moving Titulky to another PC, copy the whole Titulky folder.")
    return problems


class App:
    def __init__(self, root, files):
        self.root = root
        self.settings = load_settings()
        self.q = queue.Queue()
        self.cancel = threading.Event()
        self.cache = {}           # keeps the Whisper model loaded between runs
        self.running = False
        self.current = ""
        self.last_folder = None

        root.title("Titulky: Czech subtitles")
        root.minsize(720, 640)
        root.report_callback_exception = self.on_error
        main = ttk.Frame(root, padding=12)
        main.pack(fill="both", expand=True)
        main.columnconfigure(0, weight=1)
        main.rowconfigure(7, weight=1)

        ttk.Label(main, text="Files (audio, video, or .srt):").grid(row=0, column=0, sticky="w")
        files_frame = ttk.Frame(main)
        files_frame.grid(row=1, column=0, sticky="ew", pady=(4, 0))
        self.listbox = tk.Listbox(files_frame, height=6, selectmode="extended", activestyle="none")
        self.listbox.pack(side="left", fill="both", expand=True)
        buttons = ttk.Frame(files_frame)
        buttons.pack(side="left", fill="y", padx=(8, 0))
        ttk.Button(buttons, text="Add files…", command=self.add_files).pack(fill="x")
        ttk.Button(buttons, text="Remove", command=self.remove_selected).pack(fill="x", pady=4)
        ttk.Button(buttons, text="Clear", command=self.clear).pack(fill="x")

        opts = ttk.LabelFrame(main, text="Create subtitles", padding=8)
        opts.grid(row=2, column=0, sticky="ew", pady=(10, 0))
        opts.columnconfigure(1, weight=1)
        self.lang = self.combo(opts, 0, "Spoken language:", LANGUAGES, self.settings.get("lang"))
        self.model = self.combo(opts, 1, "Whisper model:", MODELS,
                                self.settings.get("model", DEFAULT_MODEL))
        self.translator = self.combo(opts, 2, "Translation:", TRANSLATORS,
                                     self.settings.get("translator", "auto"))
        ttk.Label(opts, text="DeepL API key:").grid(row=3, column=0, sticky="w", pady=2, padx=(0, 8))
        self.deepl_key = tk.StringVar(value=self.settings.get("deepl_key", ""))
        ttk.Entry(opts, textvariable=self.deepl_key, show="•").grid(row=3, column=1, sticky="ew", pady=2)
        self.embed = tk.BooleanVar(value=self.settings.get("embed_into_video", False))
        ttk.Checkbutton(opts, variable=self.embed,
                        text="Also put the subtitles into the video (for TVs)").grid(
            row=4, column=0, columnspan=2, sticky="w", pady=(6, 0))
        create = ttk.Button(opts, text="Create subtitles", command=lambda: self.start("create"))
        create.grid(row=5, column=0, sticky="w", pady=(8, 0))

        video = ttk.LabelFrame(main, text="Subtitles inside the video", padding=8)
        video.grid(row=3, column=0, sticky="ew", pady=(10, 0))
        ttk.Label(video, wraplength=640, justify="left", text=(
            "Saves name.titulky.mkv (or .mp4, ...) next to the video; the original stays as it is "
            "and an existing name.titulky file is updated. Corrected the .srt files? Add the video "
            "or its .srt files and put them back in.")).pack(anchor="w")
        row = ttk.Frame(video)
        row.pack(fill="x", pady=(8, 0))
        self.action_buttons = [create] + [
            ttk.Button(row, text=text, command=lambda a=action: self.start(a))
            for text, action in [("Put the .srt files into the video", "embed"),
                                 ("Remove Titulky's subtitles", "remove_ours"),
                                 ("Remove all subtitles", "remove_all")]]
        for b in self.action_buttons[1:]:
            b.pack(side="left", padx=(0, 6))

        actions = ttk.Frame(main)
        actions.grid(row=4, column=0, sticky="ew", pady=(10, 0))
        self.stop_btn = ttk.Button(actions, text="Stop", command=self.cancel.set, state="disabled")
        self.stop_btn.pack(side="left")
        self.open_btn = ttk.Button(actions, text="Open folder", command=self.open_folder,
                                   state="disabled")
        self.open_btn.pack(side="right")

        self.status = tk.StringVar(value="Add files, then choose what to do.")
        ttk.Label(main, textvariable=self.status).grid(row=5, column=0, sticky="w", pady=(10, 2))
        self.bar = ttk.Progressbar(main, maximum=1.0)
        self.bar.grid(row=6, column=0, sticky="ew")
        self.log_box = ScrolledText(main, height=8, state="disabled", wrap="word",
                                    font=("Menlo", 11) if titulky.IS_MAC else ("Consolas", 9))
        self.log_box.grid(row=7, column=0, sticky="nsew", pady=(8, 0))

        self.paths = []           # full paths, parallel to the listbox rows
        for f in files:
            self.add_path(f)
        for problem in check_folder():
            self.append_log(problem)
            print(problem)
        root.protocol("WM_DELETE_WINDOW", self.on_close)
        if titulky.IS_MAC:
            # Files dropped on the app's icon arrive as events, not as arguments; same for Cmd+Q.
            root.createcommand("::tk::mac::OpenDocument", lambda *files: [self.add_path(f) for f in files])
            root.createcommand("::tk::mac::Quit", self.on_close)
        root.after(100, self.poll)

    def combo(self, parent, row, label, choices, current):
        """Read-only dropdown; returns a function giving the selected value."""
        ttk.Label(parent, text=label).grid(row=row, column=0, sticky="w", pady=2, padx=(0, 8))
        values = [v for _, v in choices]
        box = ttk.Combobox(parent, values=[n for n, _ in choices], state="readonly")
        box.current(values.index(current) if current in values else 0)
        box.grid(row=row, column=1, sticky="ew", pady=2)
        return lambda: values[box.current()]

    def add_path(self, f):
        path = Path(os.path.normpath(f))
        if path not in self.paths:
            self.paths.append(path)
            self.listbox.insert("end", f"{path.name}      ({path.parent})")

    def add_files(self):
        for f in filedialog.askopenfilenames(title="Choose files", filetypes=FILE_TYPES):
            self.add_path(f)

    def remove_selected(self):
        for i in reversed(self.listbox.curselection()):
            self.listbox.delete(i)
            del self.paths[i]

    def clear(self):
        self.listbox.delete(0, "end")
        self.paths.clear()

    def append_log(self, text):
        self.log_box.configure(state="normal")
        self.log_box.insert("end", text + "\n")
        self.log_box.see("end")
        self.log_box.configure(state="disabled")

    def set_running(self, running):
        self.running = running
        for b in self.action_buttons:
            b.configure(state="disabled" if running else "normal")
        self.stop_btn.configure(state="normal" if running else "disabled")

    def start(self, action):
        files = list(self.paths)
        if not files:
            messagebox.showinfo("Titulky", "Add at least one file first.")
            return
        if action == "create":
            opts = dict(lang=self.lang(), model=self.model(), translator=self.translator(),
                        deepl_key=self.deepl_key.get().strip() or None,
                        embed_into_video=self.embed.get())
            if opts["translator"] == "deepl" and not opts["deepl_key"]:
                messagebox.showwarning("Titulky", "Enter your DeepL API key, or choose Google.")
                return
            try:
                SETTINGS_FILE.write_text(json.dumps({**opts, "deepl_key": opts["deepl_key"] or ""},
                                                    indent=2), encoding="utf-8")
            except OSError:
                pass
            job = lambda path, report: titulky.process(path, report, self.cache, **opts)
        else:
            files = titulky.videos_for(files)
            job = {"embed": titulky.embed,
                   "remove_ours": lambda path, report: titulky.remove_subtitles(path, report, True),
                   "remove_all": lambda path, report: titulky.remove_subtitles(path, report, False),
                   }[action]
        self.cancel.clear()
        self.set_running(True)
        threading.Thread(target=self.run, args=(files, job), daemon=True).start()

    def run(self, files, job):
        """Worker thread: process the files one by one."""
        report = GuiReporter(self.q, self.cancel)
        failed = 0
        for i, path in enumerate(files, 1):
            self.q.put(("file", f"File {i}/{len(files)}: {path.name}", path.parent))
            report.log(f"== {path.name}")
            try:
                if not path.is_file():
                    raise RuntimeError("file not found")
                job(path, report)
            except titulky.Cancelled:
                report.log("  stopped")
                self.q.put(("done", "Stopped."))
                return
            except Exception as e:
                traceback.print_exc()  # into titulky.log
                report.log(f"  ERROR: {e}")
                failed += 1
            report.log("")
        self.q.put(("done", f"Finished with {failed} error(s), see the log below." if failed
                    else "Done: the results are next to the original files."))

    def poll(self):
        try:
            while True:
                kind, *data = self.q.get_nowait()
                if kind == "log":
                    self.append_log(data[0])
                elif kind == "progress":
                    label, fraction = data
                    self.bar["value"] = fraction
                    self.status.set(f"{self.current}, {label} {fraction:.0%}")
                elif kind == "file":
                    self.current, self.last_folder = data
                    self.status.set(self.current)
                    self.bar["value"] = 0
                    self.open_btn.configure(state="normal")
                elif kind == "done":
                    self.status.set(data[0])
                    self.bar["value"] = 1
                    self.set_running(False)
                    self.root.bell()
        except queue.Empty:
            pass
        self.root.after(100, self.poll)

    def open_folder(self):
        if self.last_folder:
            if titulky.IS_MAC:
                subprocess.Popen(["open", str(self.last_folder)])
            else:
                os.startfile(self.last_folder)

    def on_error(self, *exc):
        traceback.print_exception(*exc)
        messagebox.showerror("Titulky", f"Something went wrong: {exc[1]}\n\nDetails are in {LOG_FILE}")

    def on_close(self):
        if self.running and not messagebox.askyesno(
                "Titulky", "Titulky is still working. Stop and quit?"):
            return
        self.root.destroy()
        os._exit(0)  # don't wait for the worker thread or CUDA teardown


def show_error(text):
    """Error box that works even when the window can't be created."""
    try:
        if titulky.IS_MAC:
            subprocess.run(["osascript", "-e", f"display alert \"Titulky\" message {json.dumps(text)} "
                                               "as critical"], timeout=600)
        else:
            import ctypes
            ctypes.windll.user32.MessageBoxW(None, text, "Titulky", 0x10)
    except Exception:
        pass


def main():
    # Without a console window (Titulky.exe, or Titulky.app started from Finder) keep all output
    # in a log file.
    if sys.stdout is None or (titulky.FROZEN and not sys.stdout.isatty()):
        try:
            sys.stdout = sys.stderr = open(LOG_FILE, "w", encoding="utf-8", buffering=1)
        except OSError:
            sys.stdout = sys.stderr = open(os.devnull, "w", encoding="utf-8")
    else:
        sys.stdout.reconfigure(errors="replace")  # output that can't show Czech letters
    try:
        print(f"Titulky started {time.strftime('%Y-%m-%d %H:%M')}, {platform.platform()}, "
              f"folder {titulky.APP_DIR}")
        if "--cli" in sys.argv:
            titulky.main([a for a in sys.argv[1:] if a != "--cli"])
        titulky.init_runtime()
        if not titulky.IS_MAC:
            try:
                import ctypes
                ctypes.windll.shcore.SetProcessDpiAwareness(1)  # sharp text on high-DPI screens
            except Exception:
                pass
        root = tk.Tk()
        App(root, [a for a in sys.argv[1:] if Path(a).is_file()])
        root.mainloop()
    except Exception as e:
        traceback.print_exc()
        show_error(f"Titulky could not start:\n\n{e}\n\nDetails are in {LOG_FILE}")
        sys.exit(1)


if __name__ == "__main__":
    # Python's helper processes (on a Mac one starts as soon as huggingface_hub creates a progress
    # bar) run the app's own executable; this lets them do their job instead of opening a window.
    multiprocessing.freeze_support()
    main()
