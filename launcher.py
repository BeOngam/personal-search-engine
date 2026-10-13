from __future__ import annotations

import queue
import subprocess
import sys
import threading
import urllib.error
import urllib.request
import webbrowser
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
import tkinter as tk


APP_URL = "http://localhost:8501"
API_URL = "http://localhost:8000/health"
QDRANT_URL = "http://localhost:16333/healthz"
BACKGROUND = "#0b1220"
PANEL = "#111c2e"
PANEL_LIGHT = "#17243a"
TEXT = "#e8eef8"
MUTED = "#94a3b8"
ACCENT = "#5eead4"
GREEN = "#34d399"
RED = "#fb7185"


def find_project_root() -> Path | None:
    executable_dir = Path(sys.executable).resolve().parent
    source_dir = Path(__file__).resolve().parent
    candidates = [executable_dir, *executable_dir.parents, source_dir]
    for candidate in candidates:
        if (candidate / "docker-compose.yml").is_file() and (
            candidate / "start.ps1"
        ).is_file():
            return candidate
    return None


class SearchEngineLauncher:
    def __init__(self) -> None:
        self.root = tk.Tk()
        self.root.title("Personal Search Engine")
        self.root.geometry("760x690")
        self.root.minsize(680, 600)
        self.root.configure(bg=BACKGROUND)

        self.events: queue.Queue[tuple[str, object]] = queue.Queue()
        self.busy = False
        self.auto_open = False
        self.health_check_running = False
        self._configure_style()
        self._build_window()
        self.root.protocol("WM_DELETE_WINDOW", self._close)
        self.root.after(150, self._drain_events)
        self.root.after(500, self._refresh_health)

    def _configure_style(self) -> None:
        style = ttk.Style(self.root)
        style.theme_use("clam")
        style.configure(
            "TButton",
            font=("Segoe UI", 10, "bold"),
            padding=(14, 10),
            borderwidth=0,
            background=PANEL_LIGHT,
            foreground=TEXT,
        )
        style.map(
            "TButton",
            background=[("active", "#263650"), ("disabled", PANEL)],
            foreground=[("disabled", MUTED)],
        )
        style.configure(
            "Accent.TButton",
            background="#0f766e",
            foreground="#f0fdfa",
        )
        style.map("Accent.TButton", background=[("active", "#115e59")])
        style.configure(
            "Stop.TButton",
            background="#3a1c2a",
            foreground="#fecdd3",
        )
        style.map("Stop.TButton", background=[("active", "#572337")])

    def _build_window(self) -> None:
        content = tk.Frame(self.root, bg=BACKGROUND, padx=28, pady=24)
        content.pack(fill="both", expand=True)

        header = tk.Frame(content, bg=BACKGROUND)
        header.pack(fill="x")
        logo = tk.Canvas(
            header, width=54, height=54, bg=BACKGROUND, highlightthickness=0
        )
        logo.pack(side="right", padx=(16, 0))
        logo.create_oval(3, 3, 51, 51, fill="#123a45", outline="")
        logo.create_text(
            27, 27, text="P", fill=ACCENT, font=("Segoe UI", 22, "bold")
        )
        heading = tk.Frame(header, bg=BACKGROUND)
        heading.pack(side="right", fill="y")
        tk.Label(
            heading,
            text="Personal Search",
            bg=BACKGROUND,
            fg=TEXT,
            font=("Segoe UI Semibold", 21),
            anchor="e",
        ).pack(anchor="e")
        tk.Label(
            heading,
            text="جست‌وجوی خصوصی اسناد روی رایانهٔ شما",
            bg=BACKGROUND,
            fg=MUTED,
            font=("Segoe UI", 10),
            anchor="e",
        ).pack(anchor="e", pady=(2, 0))

        self.root_status = tk.Label(
            header,
            text="●  در حال بررسی",
            bg=PANEL,
            fg=MUTED,
            font=("Segoe UI", 9, "bold"),
            padx=12,
            pady=8,
        )
        self.root_status.pack(side="left", anchor="n", pady=6)

        self._section_label(content, "وضعیت سرویس‌ها")
        status_row = tk.Frame(content, bg=BACKGROUND)
        status_row.pack(fill="x", pady=(0, 20))
        self.status_labels: dict[str, tk.Label] = {}
        for name, title in (
            ("qdrant", "پایگاه برداری"),
            ("api", "موتور جست‌وجو"),
            ("ui", "رابط کاربری"),
        ):
            card = tk.Frame(status_row, bg=PANEL, padx=15, pady=14)
            card.pack(side="right", fill="x", expand=True, padx=(0, 10))
            tk.Label(
                card,
                text=title,
                bg=PANEL,
                fg=MUTED,
                font=("Segoe UI", 9),
                anchor="e",
            ).pack(fill="x")
            status = tk.Label(
                card,
                text="●  بررسی‌نشده",
                bg=PANEL,
                fg=MUTED,
                font=("Segoe UI", 10, "bold"),
                anchor="e",
            )
            status.pack(fill="x", pady=(7, 0))
            self.status_labels[name] = status

        project_card = tk.Frame(content, bg=PANEL, padx=16, pady=14)
        project_card.pack(fill="x", pady=(0, 18))
        tk.Label(
            project_card,
            text="پوشهٔ پروژه",
            bg=PANEL,
            fg=MUTED,
            font=("Segoe UI", 9),
            anchor="e",
        ).pack(fill="x")
        project_row = tk.Frame(project_card, bg=PANEL)
        project_row.pack(fill="x", pady=(7, 0))
        self.project_path = tk.StringVar()
        self.project_entry = tk.Entry(
            project_row,
            textvariable=self.project_path,
            state="readonly",
            readonlybackground=PANEL,
            fg=TEXT,
            insertbackground=TEXT,
            relief="flat",
            font=("Segoe UI", 9),
            justify="left",
        )
        self.project_entry.pack(side="right", fill="x", expand=True, ipady=7)
        self.browse_button = ttk.Button(
            project_row, text="انتخاب پوشه", command=self._choose_project
        )
        self.browse_button.pack(side="left", padx=(0, 12))
        detected_root = find_project_root()
        if detected_root:
            self.project_path.set(str(detected_root))

        actions = tk.Frame(content, bg=BACKGROUND)
        actions.pack(fill="x", pady=(0, 18))
        self.start_button = ttk.Button(
            actions,
            text="▶  اجرای پروژه",
            style="Accent.TButton",
            command=self._start,
        )
        self.start_button.pack(side="right")
        self.stop_button = ttk.Button(
            actions,
            text="■  توقف سرویس‌ها",
            style="Stop.TButton",
            command=self._stop,
            state="disabled",
        )
        self.stop_button.pack(side="right", padx=(0, 10))
        self.open_button = ttk.Button(
            actions,
            text="بازکردن محیط جست‌وجو",
            command=lambda: webbrowser.open(APP_URL),
            state="disabled",
        )
        self.open_button.pack(side="left")

        self._section_label(content, "گزارش اجرا")
        log_frame = tk.Frame(content, bg=PANEL, padx=12, pady=12)
        log_frame.pack(fill="both", expand=True)
        self.log = tk.Text(
            log_frame,
            height=10,
            wrap="word",
            bg=PANEL,
            fg="#cbd5e1",
            insertbackground=TEXT,
            relief="flat",
            font=("Cascadia Mono", 9),
            state="disabled",
        )
        self.log.pack(side="right", fill="both", expand=True)
        scrollbar = ttk.Scrollbar(log_frame, orient="vertical", command=self.log.yview)
        scrollbar.pack(side="left", fill="y")
        self.log.configure(yscrollcommand=scrollbar.set)
        self._append_log("لانچر آماده است. برای شروع، «اجرای پروژه» را بزنید.")

        tk.Label(
            content,
            text="برای اجرا به Docker Desktop و پوشهٔ کامل پروژه نیاز است.",
            bg=BACKGROUND,
            fg=MUTED,
            font=("Segoe UI", 9),
            anchor="e",
        ).pack(fill="x", pady=(12, 0))

    @staticmethod
    def _section_label(parent: tk.Widget, text: str) -> None:
        tk.Label(
            parent,
            text=text,
            bg=BACKGROUND,
            fg=TEXT,
            font=("Segoe UI Semibold", 11),
            anchor="e",
        ).pack(fill="x", pady=(12, 9))

    def _append_log(self, text: str) -> None:
        self.log.configure(state="normal")
        self.log.insert("end", text.rstrip() + "\n")
        self.log.see("end")
        self.log.configure(state="disabled")

    def _choose_project(self) -> None:
        selected = filedialog.askdirectory(title="پوشهٔ اصلی پروژه را انتخاب کنید")
        if selected:
            self.project_path.set(selected)

    def _project_root(self) -> Path | None:
        raw_path = self.project_path.get().strip()
        if not raw_path:
            return None
        root = Path(raw_path).expanduser()
        if (root / "docker-compose.yml").is_file() and (root / "start.ps1").is_file():
            return root
        return None

    def _set_busy(self, busy: bool) -> None:
        self.busy = busy
        state = "disabled" if busy else "normal"
        self.start_button.configure(state=state)
        self.browse_button.configure(state=state)
        if busy:
            self.stop_button.configure(state="disabled")

    def _run_command(self, arguments: list[str], cwd: Path) -> None:
        try:
            process = subprocess.Popen(
                arguments,
                cwd=cwd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            if process.stdout is not None:
                for line in process.stdout:
                    self.events.put(("log", line))
            return_code = process.wait()
            self.events.put(("finished", str(return_code)))
        except OSError as error:
            self.events.put(("error", str(error)))

    def _start(self) -> None:
        project = self._project_root()
        if project is None:
            messagebox.showerror(
                "پوشهٔ پروژه پیدا نشد",
                "پوشه‌ای را انتخاب کنید که فایل‌های docker-compose.yml و start.ps1 در آن باشد.",
            )
            return
        self._set_busy(True)
        self.auto_open = True
        self._append_log("در حال ساخت و اجرای سرویس‌ها...")
        command = [
            "powershell.exe",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(project / "start.ps1"),
            "-NoBrowser",
        ]
        threading.Thread(
            target=self._run_command, args=(command, project), daemon=True
        ).start()

    def _stop(self) -> None:
        project = self._project_root()
        if project is None:
            return
        self._set_busy(True)
        self._append_log("در حال متوقف‌کردن سرویس‌ها...")
        threading.Thread(
            target=self._run_command,
            args=(["docker", "compose", "down"], project),
            daemon=True,
        ).start()

    @staticmethod
    def _is_healthy(url: str) -> bool:
        try:
            with urllib.request.urlopen(url, timeout=1.5) as response:
                return 200 <= response.status < 400
        except (urllib.error.URLError, TimeoutError, OSError):
            return False

    def _refresh_health(self) -> None:
        endpoints = {
            "qdrant": QDRANT_URL,
            "api": API_URL,
            "ui": APP_URL,
        }
        if not self.health_check_running:
            self.health_check_running = True
            threading.Thread(
                target=self._check_health, args=(endpoints,), daemon=True
            ).start()
        self.root.after(5000, self._refresh_health)

    def _check_health(self, endpoints: dict[str, str]) -> None:
        self.events.put(
            (
                "health",
                {
                    service: self._is_healthy(url)
                    for service, url in endpoints.items()
                },
            )
        )

    def _apply_health(self, health: dict[str, bool]) -> None:
        for service, healthy in health.items():
            self.status_labels[service].configure(
                text="●  آماده" if healthy else "●  متوقف",
                fg=GREEN if healthy else RED,
            )
        qdrant = health.get("qdrant", False)
        api = health.get("api", False)
        ui = health.get("ui", False)
        if qdrant and api and ui:
            self.root_status.configure(text="●  همهٔ سرویس‌ها آماده‌اند", fg=GREEN)
            self.open_button.configure(state="normal")
            if self.auto_open:
                webbrowser.open(APP_URL)
                self.auto_open = False
        else:
            self.root_status.configure(text="●  سرویس‌ها در حال آماده‌سازی", fg=ACCENT)
            self.open_button.configure(state="disabled")
        self.stop_button.configure(
            state="normal" if not self.busy and (qdrant or api or ui) else "disabled"
        )

    def _drain_events(self) -> None:
        while True:
            try:
                event, value = self.events.get_nowait()
            except queue.Empty:
                break
            if event == "log":
                assert isinstance(value, str)
                self._append_log(value)
            elif event == "finished":
                assert isinstance(value, str)
                self._set_busy(False)
                if value == "0":
                    self._append_log("فرمان با موفقیت پایان یافت.")
                else:
                    self.auto_open = False
                    self._append_log(f"فرمان با کد خطای {value} پایان یافت.")
            elif event == "error":
                assert isinstance(value, str)
                self._set_busy(False)
                self.auto_open = False
                self._append_log(f"خطا در اجرای فرمان: {value}")
                messagebox.showerror("خطای اجرا", value)
            elif event == "health":
                assert isinstance(value, dict)
                self.health_check_running = False
                self._apply_health(value)
        self.root.after(150, self._drain_events)

    def _close(self) -> None:
        self.root.destroy()

    def run(self) -> None:
        self.root.mainloop()


if __name__ == "__main__":
    SearchEngineLauncher().run()
