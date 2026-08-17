"""NPM Host - a small Windows GUI for starting/stopping and building npm projects."""

import json
import os
import re
import shutil
import subprocess
import threading
import time
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

APP_NAME = "NPM Host"
CONFIG_DIR = os.path.join(os.environ.get("APPDATA", os.path.expanduser("~")), "NPMHost")
CONFIG_PATH = os.path.join(CONFIG_DIR, "config.json")
LOG_DIR = os.path.join(CONFIG_DIR, "logs")
CREATE_NO_WINDOW = 0x08000000

STATUS_COLORS = {
    "running": "#2e8b3d",
    "stopping": "#b8860b",
    "failed": "#c0392b",
}
DEFAULT_STATUS_COLOR = "#555"


def _safe_filename(name):
    return re.sub(r"[^A-Za-z0-9_-]+", "_", name) or "profile"


def log_path_for(profile_name, kind):
    os.makedirs(LOG_DIR, exist_ok=True)
    return os.path.join(LOG_DIR, f"{_safe_filename(profile_name)}_{kind}.log")


def load_config():
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        data = {}

    # Migrate the old single-directory config format.
    if "directory" in data and "profiles" not in data:
        directory = data.get("directory") or ""
        data = {
            "profiles": [{"name": "Default", "front": directory, "back": directory}],
            "active": "Default",
        } if directory else {"profiles": [], "active": None}

    data.setdefault("profiles", [])
    data.setdefault("active", None)
    return data


def save_config(data):
    os.makedirs(CONFIG_DIR, exist_ok=True)
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)


class AddProfileDialog(tk.Toplevel):
    def __init__(self, parent, existing_names):
        super().__init__(parent)
        self.title("Add Directory")
        self.resizable(False, False)
        self.transient(parent)
        self.grab_set()
        self.result = None
        self.existing_names = existing_names

        self.name_var = tk.StringVar()
        self.front_var = tk.StringVar()
        self.back_var = tk.StringVar()

        pad = {"padx": 10, "pady": 6}

        frame = ttk.Frame(self, padding=12)
        frame.pack(fill=tk.BOTH, expand=True)

        ttk.Label(frame, text="Display name:").grid(row=0, column=0, sticky="w", **pad)
        ttk.Entry(frame, textvariable=self.name_var, width=40).grid(
            row=0, column=1, columnspan=2, sticky="ew", **pad
        )

        ttk.Label(frame, text="Front directory (npm run build):").grid(
            row=1, column=0, sticky="w", **pad
        )
        ttk.Entry(frame, textvariable=self.front_var, width=32).grid(row=1, column=1, sticky="ew", **pad)
        ttk.Button(frame, text="Browse...", command=self._browse_front).grid(row=1, column=2, **pad)

        ttk.Label(frame, text="Back directory (npm start / npm run dev):").grid(
            row=2, column=0, sticky="w", **pad
        )
        ttk.Entry(frame, textvariable=self.back_var, width=32).grid(row=2, column=1, sticky="ew", **pad)
        ttk.Button(frame, text="Browse...", command=self._browse_back).grid(row=2, column=2, **pad)

        btn_frame = ttk.Frame(frame)
        btn_frame.grid(row=3, column=0, columnspan=3, pady=(10, 0))
        ttk.Button(btn_frame, text="Save", command=self._on_save).pack(side=tk.LEFT, padx=6)
        ttk.Button(btn_frame, text="Cancel", command=self.destroy).pack(side=tk.LEFT, padx=6)

        frame.columnconfigure(1, weight=1)
        self.bind("<Return>", lambda _e: self._on_save())
        self.bind("<Escape>", lambda _e: self.destroy())

    def _browse_front(self):
        chosen = filedialog.askdirectory(title="Select front (build) directory", parent=self)
        if chosen:
            self.front_var.set(chosen)

    def _browse_back(self):
        chosen = filedialog.askdirectory(title="Select back (start/stop) directory", parent=self)
        if chosen:
            self.back_var.set(chosen)

    def _on_save(self):
        name = self.name_var.get().strip()
        front = self.front_var.get().strip()
        back = self.back_var.get().strip()

        if not name:
            messagebox.showwarning(APP_NAME, "Please enter a display name.", parent=self)
            return
        if name in self.existing_names:
            messagebox.showwarning(APP_NAME, "That name is already in use.", parent=self)
            return
        if not front and not back:
            messagebox.showwarning(
                APP_NAME, "Please choose at least a front or back directory.", parent=self
            )
            return

        self.result = {"name": name, "front": front, "back": back}
        self.destroy()


class NpmHostApp:
    def __init__(self, root):
        self.root = root
        self.root.title(APP_NAME)
        self.root.geometry("700x480")
        self.root.minsize(560, 380)
        self.root.configure(bg="white")

        config = load_config()
        self.profiles = config["profiles"]
        self.active_name = config.get("active")
        if self.active_name not in [p["name"] for p in self.profiles]:
            self.active_name = self.profiles[0]["name"] if self.profiles else None

        self.start_proc = None
        self.dev_proc = None
        self.build_proc = None
        self.start_status = "stopped"
        self.dev_status = "stopped"
        self.build_status = "idle"
        self.stopping = False
        self.dev_stopping = False

        self._build_ui()
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        self._refresh()

    # ---------- config helpers ----------

    def _persist(self):
        save_config({"profiles": self.profiles, "active": self.active_name})

    def _active_profile(self):
        for p in self.profiles:
            if p["name"] == self.active_name:
                return p
        return None

    # ---------- UI ----------

    def _build_ui(self):
        style = ttk.Style()
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass

        # Body
        body = tk.Frame(self.root, bg="white")
        body.pack(fill=tk.BOTH, expand=True, padx=14, pady=12)

        # Top row: profile name/status (left) + action buttons (right)
        top_row = tk.Frame(body, bg="white")
        top_row.pack(fill=tk.X)

        name_col = tk.Frame(top_row, bg="white")
        name_col.pack(side=tk.LEFT, anchor="w")

        self.profile_var = tk.StringVar()
        self.profile_combo = ttk.Combobox(
            name_col,
            textvariable=self.profile_var,
            state="readonly",
            font=("Segoe UI", 14),
            width=22,
        )
        self.profile_combo.pack(anchor="w")
        self.profile_combo.bind("<<ComboboxSelected>>", self._on_profile_selected)

        status_font = ("Segoe UI", 9)
        self.start_status_var = tk.StringVar(value="start: stopped")
        self.start_status_label = tk.Label(
            name_col, textvariable=self.start_status_var, bg="white", fg=DEFAULT_STATUS_COLOR, font=status_font
        )
        self.start_status_label.pack(anchor="w")

        self.dev_status_var = tk.StringVar(value="dev: stopped")
        self.dev_status_label = tk.Label(
            name_col, textvariable=self.dev_status_var, bg="white", fg=DEFAULT_STATUS_COLOR, font=status_font
        )
        self.dev_status_label.pack(anchor="w")

        self.build_status_var = tk.StringVar(value="build: idle")
        self.build_status_label = tk.Label(
            name_col, textvariable=self.build_status_var, bg="white", fg=DEFAULT_STATUS_COLOR, font=status_font
        )
        self.build_status_label.pack(anchor="w")

        btn_col = tk.Frame(top_row, bg="white")
        btn_col.pack(side=tk.RIGHT)

        btn_font = ("Segoe UI", 11)
        self.start_btn = tk.Button(
            btn_col,
            text="NPM Start",
            font=btn_font,
            relief=tk.SOLID,
            borderwidth=2,
            padx=14,
            pady=8,
            command=self._toggle_start,
        )
        self.start_btn.pack(side=tk.LEFT, padx=(0, 8))

        self.dev_btn = tk.Button(
            btn_col,
            text="NPM Run Dev",
            font=btn_font,
            relief=tk.SOLID,
            borderwidth=2,
            padx=14,
            pady=8,
            command=self._toggle_dev,
        )
        self.dev_btn.pack(side=tk.LEFT, padx=(0, 8))

        self.build_btn = tk.Button(
            btn_col,
            text="NPM Run Build",
            font=btn_font,
            relief=tk.SOLID,
            borderwidth=2,
            padx=14,
            pady=8,
            command=self._run_build,
        )
        self.build_btn.pack(side=tk.LEFT)

        # Spacer fills the remaining space, pushing the bottom row down
        tk.Frame(body, bg="white").pack(fill=tk.BOTH, expand=True)

        # Bottom row: log folder link (left) + add directory button (right)
        bottom_row = tk.Frame(body, bg="white")
        bottom_row.pack(fill=tk.X, pady=(10, 0))

        log_link = tk.Label(
            bottom_row,
            text="Logs: " + LOG_DIR,
            bg="white",
            fg="#0563c1",
            font=("Segoe UI", 8, "underline"),
            cursor="hand2",
        )
        log_link.pack(side=tk.LEFT, anchor="s")
        log_link.bind("<Button-1>", lambda _e: self._open_log_folder())

        tk.Button(
            bottom_row,
            text="Add directory",
            font=("Segoe UI", 10),
            relief=tk.SOLID,
            borderwidth=2,
            padx=10,
            pady=4,
            command=self._add_directory,
        ).pack(side=tk.RIGHT)

    # ---------- profile management ----------

    def _refresh_profile_combo(self):
        names = [p["name"] for p in self.profiles]
        self.profile_combo.configure(values=names)
        self.profile_var.set(self.active_name or "")

    def _on_profile_selected(self, _event=None):
        self.active_name = self.profile_var.get()
        self._persist()
        self._refresh()

    def _add_directory(self):
        dialog = AddProfileDialog(self.root, [p["name"] for p in self.profiles])
        self.root.wait_window(dialog)
        if dialog.result:
            self.profiles.append(dialog.result)
            self.active_name = dialog.result["name"]
            self._persist()
            self._refresh()

    # ---------- helpers ----------

    def _open_log_folder(self):
        os.makedirs(LOG_DIR, exist_ok=True)
        os.startfile(LOG_DIR)

    def _npm_path(self):
        npm = shutil.which("npm")
        if not npm:
            messagebox.showerror(APP_NAME, "npm was not found on PATH. Please install Node.js.")
        return npm

    def _refresh(self):
        self._refresh_profile_combo()
        running = self.start_proc is not None
        dev_running = self.dev_proc is not None
        building = self.build_proc is not None
        profile = self._active_profile()

        self.start_btn.configure(text="NPM Stop" if running else "NPM Start", bg="#c0392b" if running else "#2e8b3d", fg="white")
        self.dev_btn.configure(text="NPM Stop Dev" if dev_running else "NPM Run Dev", bg="#c0392b" if dev_running else "#2e8b3d", fg="white")
        self.build_btn.configure(text="Building..." if building else "NPM Run Build")

        has_profile = profile is not None
        self.start_btn.configure(state="normal" if (has_profile and not building and not dev_running) else "disabled")
        self.dev_btn.configure(state="normal" if (has_profile and not building and not running) else "disabled")
        self.build_btn.configure(state="normal" if (has_profile and not building) else "disabled")

        self.start_status_var.set(f"start: {self.start_status}")
        self.start_status_label.configure(
            fg=STATUS_COLORS.get(self.start_status, DEFAULT_STATUS_COLOR)
        )
        self.dev_status_var.set(f"dev: {self.dev_status}")
        self.dev_status_label.configure(
            fg=STATUS_COLORS.get(self.dev_status, DEFAULT_STATUS_COLOR)
        )
        self.build_status_var.set(f"build: {self.build_status}")
        self.build_status_label.configure(
            fg=STATUS_COLORS.get(self.build_status, DEFAULT_STATUS_COLOR)
        )

    # ---------- process control ----------

    def _toggle_start(self):
        if self.start_proc is None:
            self._start_npm_start()
        else:
            self._stop_npm_start()

    def _start_npm_start(self):
        profile = self._active_profile()
        if not profile:
            return
        directory = profile.get("back")
        if not directory or not os.path.isdir(directory):
            messagebox.showwarning(APP_NAME, "This profile has no valid back directory set.")
            return
        npm = self._npm_path()
        if not npm:
            return

        log_file = log_path_for(profile["name"], "start")
        self.stopping = False
        try:
            self.start_proc = subprocess.Popen(
                [npm, "start"],
                cwd=directory,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
                creationflags=CREATE_NO_WINDOW,
            )
        except OSError as exc:
            self.start_status = "failed"
            self._refresh()
            messagebox.showerror(APP_NAME, f"Failed to start npm: {exc}")
            self.start_proc = None
            return

        self.start_status = "running"
        threading.Thread(
            target=self._stream_output, args=(self.start_proc, "start", log_file), daemon=True
        ).start()
        self._refresh()

    def _stop_npm_start(self):
        proc = self.start_proc
        if proc is None:
            return
        self.stopping = True
        self.start_status = "stopping"
        self._refresh()
        try:
            subprocess.run(
                ["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                capture_output=True,
                creationflags=CREATE_NO_WINDOW,
            )
        except OSError:
            pass

    def _toggle_dev(self):
        if self.dev_proc is None:
            self._start_npm_dev()
        else:
            self._stop_npm_dev()

    def _start_npm_dev(self):
        profile = self._active_profile()
        if not profile:
            return
        directory = profile.get("back")
        if not directory or not os.path.isdir(directory):
            messagebox.showwarning(APP_NAME, "This profile has no valid back directory set.")
            return
        npm = self._npm_path()
        if not npm:
            return

        log_file = log_path_for(profile["name"], "dev")
        self.dev_stopping = False
        try:
            self.dev_proc = subprocess.Popen(
                [npm, "run", "dev"],
                cwd=directory,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
                creationflags=CREATE_NO_WINDOW,
            )
        except OSError as exc:
            self.dev_status = "failed"
            self._refresh()
            messagebox.showerror(APP_NAME, f"Failed to start npm: {exc}")
            self.dev_proc = None
            return

        self.dev_status = "running"
        threading.Thread(
            target=self._stream_output, args=(self.dev_proc, "dev", log_file), daemon=True
        ).start()
        self._refresh()

    def _stop_npm_dev(self):
        proc = self.dev_proc
        if proc is None:
            return
        self.dev_stopping = True
        self.dev_status = "stopping"
        self._refresh()
        try:
            subprocess.run(
                ["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                capture_output=True,
                creationflags=CREATE_NO_WINDOW,
            )
        except OSError:
            pass

    def _run_build(self):
        if self.build_proc is not None:
            return
        profile = self._active_profile()
        if not profile:
            return
        directory = profile.get("front")
        if not directory or not os.path.isdir(directory):
            messagebox.showwarning(APP_NAME, "This profile has no valid front directory set.")
            return
        npm = self._npm_path()
        if not npm:
            return

        log_file = log_path_for(profile["name"], "build")
        try:
            self.build_proc = subprocess.Popen(
                [npm, "run", "build"],
                cwd=directory,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
                creationflags=CREATE_NO_WINDOW,
            )
        except OSError as exc:
            self.build_status = "failed"
            self._refresh()
            messagebox.showerror(APP_NAME, f"Failed to start npm: {exc}")
            self.build_proc = None
            return

        self.build_status = "running"
        threading.Thread(
            target=self._stream_output, args=(self.build_proc, "build", log_file), daemon=True
        ).start()
        self._refresh()

    def _stream_output(self, proc, kind, log_file):
        commands = {"start": "npm start", "dev": "npm run dev", "build": "npm run build"}
        with open(log_file, "a", encoding="utf-8") as f:
            f.write(f"\n===== {commands[kind]} | {time.strftime('%Y-%m-%d %H:%M:%S')} =====\n")
            f.flush()
            for line in proc.stdout:
                f.write(line)
                f.flush()
            exit_code = proc.wait()
            f.write(f"===== exited with code {exit_code} =====\n")

        def finish():
            if kind == "start":
                self.start_proc = None
                if self.stopping:
                    self.start_status = "stopped"
                else:
                    self.start_status = "failed" if exit_code != 0 else "stopped"
                self.stopping = False
            elif kind == "dev":
                self.dev_proc = None
                if self.dev_stopping:
                    self.dev_status = "stopped"
                else:
                    self.dev_status = "failed" if exit_code != 0 else "stopped"
                self.dev_stopping = False
            else:
                self.build_proc = None
                self.build_status = "failed" if exit_code != 0 else "idle"
            self._refresh()

        self.root.after(0, finish)

    def _on_close(self):
        active = [p for p in (self.start_proc, self.dev_proc, self.build_proc) if p is not None]
        if active:
            if not messagebox.askyesno(
                APP_NAME, "A process is still running. Stop it and exit?"
            ):
                return
            for proc in active:
                try:
                    subprocess.run(
                        ["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                        capture_output=True,
                        creationflags=CREATE_NO_WINDOW,
                    )
                except OSError:
                    pass
        self.root.destroy()


def main():
    root = tk.Tk()
    NpmHostApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
