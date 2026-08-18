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

KIND_COMMANDS = {"start": ["start"], "dev": ["run", "dev"], "build": ["run", "build"]}
KIND_LABELS = {"start": "npm start", "dev": "npm run dev", "build": "npm run build"}
KIND_DIRECTORY = {"start": "back", "dev": "back", "build": "front"}
IDLE_STATUS = {"start": "stopped", "dev": "stopped", "build": "idle"}
RUNNING_MARKER = "● "


def new_state():
    return {
        kind: {"proc": None, "status": IDLE_STATUS[kind], "stopping": False}
        for kind in KIND_COMMANDS
    }


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

        self.states = {}
        self._display_to_name = {}

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

    def _state(self, name):
        return self.states.setdefault(name, new_state())

    def _active_state(self):
        if self.active_name is None:
            return new_state()
        return self._state(self.active_name)

    def _is_busy(self, name):
        state = self.states.get(name)
        return bool(state) and any(k["proc"] is not None for k in state.values())

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
        self._display_to_name = {}
        displays = []
        for profile in self.profiles:
            name = profile["name"]
            display = (RUNNING_MARKER + name) if self._is_busy(name) else name
            self._display_to_name[display] = name
            displays.append(display)
        self.profile_combo.configure(values=displays)

        active_display = ""
        for display, name in self._display_to_name.items():
            if name == self.active_name:
                active_display = display
                break
        self.profile_var.set(active_display)

    def _on_profile_selected(self, _event=None):
        self.active_name = self._display_to_name.get(self.profile_var.get(), self.active_name)
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
        profile = self._active_profile()
        state = self._active_state()
        running = state["start"]["proc"] is not None
        dev_running = state["dev"]["proc"] is not None
        building = state["build"]["proc"] is not None

        self.start_btn.configure(text="NPM Stop" if running else "NPM Start", bg="#c0392b" if running else "#2e8b3d", fg="white")
        self.dev_btn.configure(text="NPM Stop Dev" if dev_running else "NPM Run Dev", bg="#c0392b" if dev_running else "#2e8b3d", fg="white")
        self.build_btn.configure(text="Building..." if building else "NPM Run Build")

        has_profile = profile is not None
        self.start_btn.configure(state="normal" if (has_profile and not building and not dev_running) else "disabled")
        self.dev_btn.configure(state="normal" if (has_profile and not building and not running) else "disabled")
        self.build_btn.configure(state="normal" if (has_profile and not building) else "disabled")

        for kind, var, label in (
            ("start", self.start_status_var, self.start_status_label),
            ("dev", self.dev_status_var, self.dev_status_label),
            ("build", self.build_status_var, self.build_status_label),
        ):
            status = state[kind]["status"]
            var.set(f"{kind}: {status}")
            label.configure(fg=STATUS_COLORS.get(status, DEFAULT_STATUS_COLOR))

    # ---------- process control ----------

    def _toggle_start(self):
        self._toggle("start")

    def _toggle_dev(self):
        self._toggle("dev")

    def _run_build(self):
        profile = self._active_profile()
        if profile and self._state(profile["name"])["build"]["proc"] is None:
            self._launch(profile, "build")

    def _toggle(self, kind):
        profile = self._active_profile()
        if not profile:
            return
        if self._state(profile["name"])[kind]["proc"] is None:
            self._launch(profile, kind)
        else:
            self._stop(profile["name"], kind)

    def _launch(self, profile, kind):
        name = profile["name"]
        entry = self._state(name)[kind]
        directory = profile.get(KIND_DIRECTORY[kind])
        if not directory or not os.path.isdir(directory):
            messagebox.showwarning(
                APP_NAME,
                f"'{name}' has no valid {KIND_DIRECTORY[kind]} directory set.",
            )
            return
        npm = self._npm_path()
        if not npm:
            return

        log_file = log_path_for(name, kind)
        entry["stopping"] = False
        try:
            entry["proc"] = subprocess.Popen(
                [npm] + KIND_COMMANDS[kind],
                cwd=directory,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
                creationflags=CREATE_NO_WINDOW,
            )
        except OSError as exc:
            entry["proc"] = None
            entry["status"] = "failed"
            self._refresh()
            messagebox.showerror(APP_NAME, f"Failed to start npm: {exc}")
            return

        entry["status"] = "running"
        threading.Thread(
            target=self._stream_output, args=(name, kind, entry["proc"], log_file), daemon=True
        ).start()
        self._refresh()

    def _stop(self, name, kind):
        entry = self._state(name)[kind]
        proc = entry["proc"]
        if proc is None:
            return
        entry["stopping"] = True
        entry["status"] = "stopping"
        self._refresh()
        self._kill(proc)

    def _kill(self, proc):
        try:
            subprocess.run(
                ["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                capture_output=True,
                creationflags=CREATE_NO_WINDOW,
            )
        except OSError:
            pass

    def _stream_output(self, name, kind, proc, log_file):
        with open(log_file, "a", encoding="utf-8") as f:
            f.write(
                f"\n===== {KIND_LABELS[kind]} | {name} | "
                f"{time.strftime('%Y-%m-%d %H:%M:%S')} =====\n"
            )
            f.flush()
            for line in proc.stdout:
                f.write(line)
                f.flush()
            exit_code = proc.wait()
            f.write(f"===== exited with code {exit_code} =====\n")

        def finish():
            entry = self._state(name)[kind]
            entry["proc"] = None
            if entry["stopping"] or exit_code == 0:
                entry["status"] = IDLE_STATUS[kind]
            else:
                entry["status"] = "failed"
            entry["stopping"] = False
            self._refresh()

        self.root.after(0, finish)

    def _on_close(self):
        active = [
            entry["proc"]
            for state in self.states.values()
            for entry in state.values()
            if entry["proc"] is not None
        ]
        if active:
            plural = "es" if len(active) > 1 else ""
            if not messagebox.askyesno(
                APP_NAME, f"{len(active)} process{plural} still running. Stop and exit?"
            ):
                return
            for proc in active:
                self._kill(proc)
        self.root.destroy()


def main():
    root = tk.Tk()
    NpmHostApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
