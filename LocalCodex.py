"""Local Codex AI desktop manager for Windows.

This application deliberately uses only Python's standard library so a fresh
Python installation is enough to open it.  Long-running system operations are
run in a background worker and streamed to the Activity tab.
"""

from __future__ import annotations

import json
import hashlib
import os
import queue
import re
import shutil
import subprocess
import sys
import threading
import time
import tkinter as tk
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from tkinter import filedialog, messagebox, simpledialog, ttk
from typing import Callable, Iterable


APP_NAME = "Local Codex AI"
ROOT = Path(__file__).resolve().parent
PROJECTS_DIR = ROOT / "projects"
CONFIG_DIR = Path(os.environ.get("LOCALAPPDATA", ROOT)) / "LocalCodexAI"
CONFIG_FILE = CONFIG_DIR / "config.json"
LOG_FILE = CONFIG_DIR / "manager.log"
CODEX_CONFIG_FILE = Path.home() / ".codex" / "config.toml"
DEFAULT_MODEL = "gpt-oss:20b"
MODEL_CHOICES = (
    "gpt-oss:20b",
    "qwen3-coder:30b",
    "qwen3:8b",
    "deepseek-r1:8b",
)


@dataclass(frozen=True)
class Program:
    name: str
    executable: str
    version_args: tuple[str, ...]
    winget_id: str | None = None


PROGRAMS = (
    Program("Python", "python", ("--version",), "Python.Python.3.13"),
    Program("Node.js LTS", "node", ("--version",), "OpenJS.NodeJS.LTS"),
    Program("Git", "git", ("--version",), "Git.Git"),
    Program("Ollama", "ollama", ("--version",), "Ollama.Ollama"),
    Program("Codex CLI", "codex", ("--version",)),
)


class Settings:
    """Small, tolerant JSON configuration store."""

    defaults = {
        "model": DEFAULT_MODEL,
        "active_project": str(ROOT),
        "context_length": "65536",
        "launch_mode": "Ollama 방식으로 실행",
        "unrestricted": False,
        "chatgpt_executable": "",
        "oss_provider": "ollama",
        "auto_install": True,
    }

    def __init__(self) -> None:
        self.data = dict(self.defaults)
        self.load()

    def load(self) -> None:
        try:
            incoming = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
            if isinstance(incoming, dict):
                self.data.update({key: value for key, value in incoming.items() if key in self.defaults})
                if self.data["launch_mode"] == "Ollama launch":
                    self.data["launch_mode"] = "Ollama 방식으로 실행"
                if "active_project" in incoming:
                    return
        except (OSError, json.JSONDecodeError):
            pass
        # The PowerShell launcher kept this value in the repository.  Import it
        # once so existing users retain their active workspace after upgrading.
        legacy_active_project = ROOT / "ActiveProject.txt"
        try:
            candidate = Path(legacy_active_project.read_text(encoding="utf-8").strip())
            if candidate.is_dir():
                self.data["active_project"] = str(candidate)
        except OSError:
            pass

    def save(self) -> None:
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        temp = CONFIG_FILE.with_suffix(".tmp")
        temp.write_text(json.dumps(self.data, ensure_ascii=False, indent=2), encoding="utf-8")
        temp.replace(CONFIG_FILE)


class JobRunner:
    """Serial background command runner with a thread-safe output queue."""

    def __init__(self) -> None:
        self.events: queue.Queue[tuple[str, object]] = queue.Queue()
        self.busy = False

    def submit(self, title: str, work: Callable[[], None]) -> bool:
        if self.busy:
            self.events.put(("log", "다른 작업이 진행 중입니다. 완료될 때까지 기다리세요."))
            return False
        self.busy = True

        def target() -> None:
            self.events.put(("state", True))
            self.events.put(("log", f"\n=== {title} ==="))
            try:
                work()
                self.events.put(("log", f"=== 완료: {title} ==="))
            except Exception as exc:  # Surface useful errors without killing the UI.
                self.events.put(("log", f"오류: {exc}"))
            finally:
                self.busy = False
                self.events.put(("state", False))

        threading.Thread(target=target, daemon=True, name="local-codex-job").start()
        return True

    def command(
        self,
        args: Iterable[str],
        *,
        cwd: Path | None = None,
        env: dict[str, str] | None = None,
        check: bool = True,
    ) -> int:
        command = [str(item) for item in args]
        self.events.put(("log", "> " + subprocess.list2cmdline(command)))
        if command[0].lower().endswith((".cmd", ".bat")):
            command = ["cmd.exe", "/d", "/c", subprocess.list2cmdline(command)]
        process = subprocess.Popen(
            command,
            cwd=str(cwd) if cwd else None,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        assert process.stdout is not None
        for line in process.stdout:
            self.events.put(("log", line.rstrip()))
        code = process.wait()
        if check and code:
            raise RuntimeError(f"명령이 종료 코드 {code}로 실패했습니다: {command[0]}")
        return code


class LocalCodexApp(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title(APP_NAME)
        self.minsize(980, 680)
        self.geometry("1180x780")
        self.settings = Settings()
        self.runner = JobRunner()
        self.program_status: dict[str, tuple[bool, str]] = {}
        self.model_var = tk.StringVar(value=str(self.settings.data["model"]))
        self.project_var = tk.StringVar(value=str(self.settings.data["active_project"]))
        self.context_var = tk.StringVar(value=str(self.settings.data["context_length"]))
        self.launch_mode_var = tk.StringVar(value=str(self.settings.data["launch_mode"]))
        self.unrestricted_var = tk.BooleanVar(value=bool(self.settings.data["unrestricted"]))
        self.chatgpt_path_var = tk.StringVar(value=str(self.settings.data["chatgpt_executable"]))
        self.oss_provider_var = tk.StringVar(value=str(self.settings.data["oss_provider"]))
        self.auto_install_var = tk.BooleanVar(value=bool(self.settings.data["auto_install"]))
        self.chat_status_var = tk.StringVar(value="Ollama API 상태 확인 전")
        self.codex_provider_status_var = tk.StringVar(value="Codex 로컬 제공자 설정 확인 전")
        self.local_chat_status_var = tk.StringVar(value="준비됨")
        self.chat_history: list[dict[str, str]] = []
        self.status_var = tk.StringVar(value="준비됨")
        self._auto_setup_started = False
        self._configure_style()
        self._build_ui()
        self.protocol("WM_DELETE_WINDOW", self._close)
        PROJECTS_DIR.mkdir(parents=True, exist_ok=True)
        self.after(80, self._consume_events)
        self.after(180, self.refresh_everything)
        self.after(1200, self.ensure_startup_prerequisites)

    def _configure_style(self) -> None:
        style = ttk.Style(self)
        if "clam" in style.theme_names():
            style.theme_use("clam")
        self.configure(background="#eef2f7")
        style.configure("TFrame", background="#eef2f7")
        style.configure("TLabel", background="#eef2f7", foreground="#172238", font=("Segoe UI", 10))
        style.configure("Title.TLabel", background="#eef2f7", foreground="#111c31", font=("Segoe UI", 21, "bold"))
        style.configure("Subtitle.TLabel", background="#eef2f7", foreground="#62718a", font=("Segoe UI", 9))
        style.configure("Card.TLabelframe", background="#ffffff", bordercolor="#d8e0ec", relief="solid", padding=15)
        style.configure("Card.TLabelframe.Label", background="#ffffff", foreground="#253653", font=("Segoe UI", 10, "bold"))
        style.configure("TNotebook", background="#eef2f7", borderwidth=0, tabmargins=(0, 0, 0, 0))
        style.configure("TNotebook.Tab", background="#e2e8f1", foreground="#52627a", padding=(14, 9), font=("Segoe UI", 9, "bold"))
        style.map("TNotebook.Tab", background=[("selected", "#ffffff"), ("active", "#f7f9fc")], foreground=[("selected", "#175cd3"), ("active", "#1d2939")])
        style.configure("Treeview", background="#ffffff", fieldbackground="#ffffff", foreground="#23334d", rowheight=29, bordercolor="#d8e0ec", font=("Segoe UI", 9))
        style.configure("Treeview.Heading", background="#e9eef6", foreground="#3b4b63", padding=(7, 8), font=("Segoe UI", 9, "bold"))
        style.map("Treeview", background=[("selected", "#d9e8ff")], foreground=[("selected", "#123b79")])
        style.configure("TButton", background="#ffffff", foreground="#253653", bordercolor="#cbd5e1", padding=(10, 7), font=("Segoe UI", 9, "bold"))
        style.map("TButton", background=[("active", "#f4f7fb"), ("disabled", "#e7ebf1")], foreground=[("disabled", "#9aa6b8")])
        style.configure("Accent.TButton", background="#2563eb", foreground="#ffffff", bordercolor="#2563eb", padding=(13, 8), font=("Segoe UI", 9, "bold"))
        style.map("Accent.TButton", background=[("active", "#1d4ed8"), ("disabled", "#9bb8ef")], foreground=[("disabled", "#ffffff")])
        style.configure("TEntry", fieldbackground="#ffffff", foreground="#172238", padding=7)
        style.configure("TCombobox", fieldbackground="#ffffff", foreground="#172238", padding=6)
        style.configure("TCheckbutton", background="#ffffff", foreground="#253653", font=("Segoe UI", 9))

    def _build_ui(self) -> None:
        outer = ttk.Frame(self, padding=18)
        outer.pack(fill="both", expand=True)
        header = ttk.Frame(outer)
        header.pack(fill="x", pady=(0, 14))
        ttk.Label(header, text=APP_NAME, style="Title.TLabel").pack(side="left")
        ttk.Label(header, text="Windows 로컬 AI 작업 환경 관리자", style="Subtitle.TLabel").pack(side="left", padx=14, pady=(8, 0))
        ttk.Button(header, text="로컬 채팅", command=lambda: self.notebook.select(self.local_chat_tab)).pack(side="right")
        ttk.Button(header, text="ChatGPT 열기", command=self.open_chatgpt).pack(side="right", padx=8)
        ttk.Label(header, textvariable=self.status_var, style="Subtitle.TLabel").pack(side="right", pady=(8, 0))

        self.notebook = ttk.Notebook(outer)
        self.notebook.pack(fill="both", expand=True)
        self.dashboard_tab = ttk.Frame(self.notebook, padding=16)
        self.programs_tab = ttk.Frame(self.notebook, padding=16)
        self.models_tab = ttk.Frame(self.notebook, padding=16)
        self.projects_tab = ttk.Frame(self.notebook, padding=16)
        self.chatgpt_tab = ttk.Frame(self.notebook, padding=16)
        self.local_chat_tab = ttk.Frame(self.notebook, padding=16)
        self.settings_tab = ttk.Frame(self.notebook, padding=16)
        self.activity_tab = ttk.Frame(self.notebook, padding=16)
        for tab, label in (
            (self.dashboard_tab, "대시보드"),
            (self.programs_tab, "프로그램"),
            (self.models_tab, "모델"),
            (self.projects_tab, "프로젝트"),
            (self.chatgpt_tab, "ChatGPT 연동"),
            (self.local_chat_tab, "로컬 채팅"),
            (self.settings_tab, "설정"),
            (self.activity_tab, "활동 로그"),
        ):
            self.notebook.add(tab, text=label)
        self._build_dashboard()
        self._build_programs()
        self._build_models()
        self._build_projects()
        self._build_chatgpt_integration()
        self._build_local_chat()
        self._build_settings()
        self._build_activity()

    def _build_dashboard(self) -> None:
        tab = self.dashboard_tab
        ttk.Label(tab, text="작업 시작", style="Title.TLabel").pack(anchor="w")
        ttk.Label(tab, text="로컬 도구를 준비하고 프로젝트를 선택한 뒤, 터미널에서 Codex를 실행하세요.", style="Subtitle.TLabel").pack(anchor="w", pady=(2, 18))
        cards = ttk.Frame(tab)
        cards.pack(fill="both", expand=True)
        cards.columnconfigure((0, 1), weight=1)
        cards.rowconfigure((0, 1), weight=1)
        setup = ttk.LabelFrame(cards, text="1. 기본 준비", style="Card.TLabelframe")
        setup.grid(row=0, column=0, sticky="nsew", padx=(0, 8), pady=(0, 8))
        ttk.Label(setup, text="Python, Node.js, Git, Ollama, Codex CLI 등 필요한 도구를 설치합니다.", wraplength=430).pack(anchor="w")
        ttk.Button(setup, text="필수 도구 모두 준비", style="Accent.TButton", command=self.prepare_all).pack(anchor="w", pady=(16, 0))
        model = ttk.LabelFrame(cards, text="2. 로컬 모델", style="Card.TLabelframe")
        model.grid(row=0, column=1, sticky="nsew", padx=(8, 0), pady=(0, 8))
        ttk.Label(model, text="선택된 Ollama 모델").pack(anchor="w")
        ttk.Label(model, textvariable=self.model_var, font=("Segoe UI", 15, "bold")).pack(anchor="w", pady=(5, 12))
        ttk.Button(model, text="서버 시작 및 모델 다운로드", command=self.pull_selected_model).pack(anchor="w")
        project = ttk.LabelFrame(cards, text="3. 프로젝트", style="Card.TLabelframe")
        project.grid(row=1, column=0, sticky="nsew", padx=(0, 8), pady=(8, 0))
        ttk.Label(project, text="현재 작업 공간", style="Subtitle.TLabel").pack(anchor="w")
        ttk.Label(project, textvariable=self.project_var, wraplength=430).pack(anchor="w", pady=(5, 15))
        row = ttk.Frame(project)
        row.pack(anchor="w")
        ttk.Button(row, text="폴더 선택", command=self.choose_project).pack(side="left")
        ttk.Button(row, text="프로젝트 의존성 설치", command=self.install_dependencies).pack(side="left", padx=8)
        launch = ttk.LabelFrame(cards, text="4. 실행", style="Card.TLabelframe")
        launch.grid(row=1, column=1, sticky="nsew", padx=(8, 0), pady=(8, 0))
        ttk.Label(launch, text="이 관리자를 계속 사용하도록 별도 터미널에서 실행합니다.", wraplength=430).pack(anchor="w")
        row = ttk.Frame(launch)
        row.pack(anchor="w", pady=(16, 0))
        ttk.Button(row, text="Codex 실행", style="Accent.TButton", command=lambda: self.launch_codex(False)).pack(side="left")
        ttk.Button(row, text="이전 대화 재개", command=lambda: self.launch_codex(True)).pack(side="left", padx=8)

    def _build_programs(self) -> None:
        ttk.Label(self.programs_tab, text="프로그램 관리", style="Title.TLabel").pack(anchor="w")
        ttk.Label(self.programs_tab, text="각 프로그램의 버전 명령으로 상태를 확인합니다. Codex CLI 외 설치는 winget을 사용합니다.", style="Subtitle.TLabel").pack(anchor="w", pady=(2, 14))
        self.program_tree = ttk.Treeview(self.programs_tab, columns=("status", "version", "installer"), show="headings", height=9)
        for key, title, width in (("status", "상태", 130), ("version", "버전 / 세부 정보", 440), ("installer", "설치 방식", 210)):
            self.program_tree.heading(key, text=title)
            self.program_tree.column(key, width=width, anchor="w")
        self.program_tree.pack(fill="x", expand=False)
        actions = ttk.Frame(self.programs_tab)
        actions.pack(fill="x", pady=14)
        ttk.Button(actions, text="상태 새로고침", command=self.refresh_programs).pack(side="left")
        ttk.Button(actions, text="선택 항목 설치", command=self.install_selected_program).pack(side="left", padx=8)
        ttk.Button(actions, text="winget ID로 설치", command=self.install_custom_package).pack(side="left")
        ttk.Button(actions, text="모두 준비", style="Accent.TButton", command=self.prepare_all).pack(side="left")
        ttk.Button(actions, text="Ollama 서버 시작", command=self.start_ollama).pack(side="left", padx=8)

    def _build_models(self) -> None:
        ttk.Label(self.models_tab, text="Ollama 모델", style="Title.TLabel").pack(anchor="w")
        ttk.Label(self.models_tab, text="다운로드를 누른 모델만 내려받습니다. 대형 모델은 수 GB 이상의 저장 공간을 사용합니다.", style="Subtitle.TLabel").pack(anchor="w", pady=(2, 14))
        choose = ttk.LabelFrame(self.models_tab, text="사용할 모델", style="Card.TLabelframe")
        choose.pack(fill="x")
        self.model_combo = ttk.Combobox(choose, textvariable=self.model_var, values=MODEL_CHOICES, width=36)
        self.model_combo.pack(side="left", padx=(0, 8))
        ttk.Button(choose, text="선택 저장", command=self.save_model).pack(side="left")
        ttk.Button(choose, text="선택 모델 다운로드", style="Accent.TButton", command=self.pull_selected_model).pack(side="left", padx=8)
        self.model_tree = ttk.Treeview(self.models_tab, columns=("name", "size", "modified"), show="headings", height=12)
        for key, title, width in (("name", "모델", 350), ("size", "크기", 140), ("modified", "수정 시각", 260)):
            self.model_tree.heading(key, text=title)
            self.model_tree.column(key, width=width, anchor="w")
        self.model_tree.pack(fill="both", expand=True, pady=14)
        row = ttk.Frame(self.models_tab)
        row.pack(fill="x")
        ttk.Button(row, text="설치된 모델 새로고침", command=self.refresh_models).pack(side="left")
        ttk.Button(row, text="선택한 설치 모델 사용", command=self.use_selected_model).pack(side="left", padx=8)

    def _build_projects(self) -> None:
        ttk.Label(self.projects_tab, text="프로젝트", style="Title.TLabel").pack(anchor="w")
        ttk.Label(self.projects_tab, text="저장소를 이 앱의 projects 폴더에 복제하거나 원하는 로컬 폴더에서 작업할 수 있습니다.", style="Subtitle.TLabel").pack(anchor="w", pady=(2, 14))
        self.project_tree = ttk.Treeview(self.projects_tab, columns=("name", "path"), show="headings", height=12)
        self.project_tree.heading("name", text="프로젝트")
        self.project_tree.heading("path", text="경로")
        self.project_tree.column("name", width=260, anchor="w")
        self.project_tree.column("path", width=700, anchor="w")
        self.project_tree.pack(fill="both", expand=True)
        actions = ttk.Frame(self.projects_tab)
        actions.pack(fill="x", pady=14)
        ttk.Button(actions, text="로컬 폴더 선택", command=self.choose_project).pack(side="left")
        ttk.Button(actions, text="Git 저장소 복제", command=self.clone_repository).pack(side="left", padx=8)
        ttk.Button(actions, text="선택 항목 사용", style="Accent.TButton", command=self.use_selected_project).pack(side="left")
        ttk.Button(actions, text="선택 폴더 열기", command=self.open_selected_project).pack(side="left", padx=8)
        ttk.Button(actions, text="새로고침", command=self.refresh_projects).pack(side="left")

    def _build_chatgpt_integration(self) -> None:
        ttk.Label(self.chatgpt_tab, text="ChatGPT 데스크톱(Codex)과 로컬 AI", style="Title.TLabel").pack(anchor="w")
        ttk.Label(
            self.chatgpt_tab,
            text="ChatGPT 데스크톱의 Codex 모델 선택기에 Ollama 모델을 추가합니다. 클라우드 모델은 그대로 유지됩니다.",
            style="Subtitle.TLabel",
        ).pack(anchor="w", pady=(2, 14))
        notice = ttk.LabelFrame(self.chatgpt_tab, text="Codex 모델 선택기 연동", style="Card.TLabelframe")
        notice.pack(fill="x")
        ttk.Label(
            notice,
            text=(
                "공식 Ollama 연동을 사용해 로컬 모델과 ChatGPT 기본 모델을 같은 Codex 선택기에 표시합니다. "
                "아래에서 선택한 설치 모델을 기본 Ollama 모델로 지정합니다. 설정 후 ChatGPT 앱을 직접 다시 시작해야 반영됩니다. "
                "일반 Chat 및 음성 대화는 기존 ChatGPT 연결을 계속 사용합니다."
            ),
            wraplength=950,
        ).pack(anchor="w")
        status = ttk.LabelFrame(self.chatgpt_tab, text="연결 및 제공자 상태", style="Card.TLabelframe")
        status.pack(fill="x", pady=(14, 0))
        ttk.Label(status, textvariable=self.chat_status_var, font=("Segoe UI", 11, "bold")).pack(anchor="w")
        ttk.Label(status, textvariable=self.codex_provider_status_var, style="Subtitle.TLabel").pack(anchor="w", pady=(4, 0))
        row = ttk.Frame(status)
        row.pack(anchor="w", pady=(10, 0))
        ttk.Button(row, text="상태 새로고침", command=self.refresh_chat_integration).pack(side="left")
        ttk.Button(row, text="Ollama 서버 시작", command=self.start_ollama).pack(side="left", padx=8)
        ttk.Button(row, text="선택 모델을 ChatGPT에 추가", style="Accent.TButton", command=self.configure_chatgpt_ollama).pack(side="left", padx=8)
        launch = ttk.LabelFrame(self.chatgpt_tab, text="ChatGPT 데스크톱 실행", style="Card.TLabelframe")
        launch.pack(fill="x", pady=(14, 0))
        ttk.Label(launch, text="ChatGPT 실행 파일을 한 번 지정하면 여기에서 바로 열 수 있습니다.", style="Subtitle.TLabel").pack(anchor="w")
        ttk.Label(launch, textvariable=self.chatgpt_path_var, wraplength=900).pack(anchor="w", pady=(6, 10))
        row = ttk.Frame(launch)
        row.pack(anchor="w")
        ttk.Button(row, text="ChatGPT 실행 파일 지정", command=self.choose_chatgpt_executable).pack(side="left")
        ttk.Button(row, text="ChatGPT 데스크톱 열기", style="Accent.TButton", command=self.open_chatgpt).pack(side="left", padx=8)
        ttk.Button(row, text="로컬 채팅으로 이동", command=lambda: self.notebook.select(self.local_chat_tab)).pack(side="left")

    def _build_local_chat(self) -> None:
        ttk.Label(self.local_chat_tab, text="Ollama 로컬 채팅", style="Title.TLabel").pack(anchor="w")
        ttk.Label(
            self.local_chat_tab,
            text="선택한 로컬 모델과 대화합니다. 대화 내용은 이 창의 메모리에만 유지되며 ChatGPT 대화와 공유되지 않습니다.",
            style="Subtitle.TLabel",
        ).pack(anchor="w", pady=(2, 10))
        top = ttk.Frame(self.local_chat_tab)
        top.pack(fill="x", pady=(0, 8))
        ttk.Label(top, text="현재 모델:").pack(side="left")
        ttk.Label(top, textvariable=self.model_var, font=("Segoe UI", 10, "bold")).pack(side="left", padx=(6, 18))
        ttk.Label(top, textvariable=self.local_chat_status_var, style="Subtitle.TLabel").pack(side="left")
        ttk.Button(top, text="대화 지우기", command=self.clear_local_chat).pack(side="right")
        output_frame = ttk.Frame(self.local_chat_tab)
        output_frame.pack(fill="both", expand=True)
        self.local_chat_output = tk.Text(output_frame, wrap="word", font=("Segoe UI", 11), state="disabled", height=18)
        output_scroll = ttk.Scrollbar(output_frame, orient="vertical", command=self.local_chat_output.yview)
        self.local_chat_output.configure(yscrollcommand=output_scroll.set)
        self.local_chat_output.pack(side="left", fill="both", expand=True)
        output_scroll.pack(side="right", fill="y")
        input_frame = ttk.LabelFrame(self.local_chat_tab, text="메시지", style="Card.TLabelframe")
        input_frame.pack(fill="x", pady=(10, 0))
        self.local_chat_input = tk.Text(input_frame, wrap="word", height=4, font=("Segoe UI", 11))
        self.local_chat_input.pack(fill="x")
        action = ttk.Frame(input_frame)
        action.pack(fill="x", pady=(8, 0))
        ttk.Button(action, text="로컬 모델에 보내기", style="Accent.TButton", command=self.send_local_chat).pack(side="left")
        ttk.Button(action, text="모델 다운로드", command=self.pull_selected_model).pack(side="left", padx=8)

    def _build_settings(self) -> None:
        ttk.Label(self.settings_tab, text="설정", style="Title.TLabel").pack(anchor="w")
        ttk.Label(self.settings_tab, text="Windows 사용자별로 LocalAppData\\LocalCodexAI에 저장됩니다.", style="Subtitle.TLabel").pack(anchor="w", pady=(2, 14))
        form = ttk.LabelFrame(self.settings_tab, text="실행 설정", style="Card.TLabelframe")
        form.pack(fill="x")
        form.columnconfigure(1, weight=1)
        ttk.Label(form, text="Ollama 컨텍스트 길이").grid(row=0, column=0, sticky="w", pady=7)
        ttk.Entry(form, textvariable=self.context_var, width=18).grid(row=0, column=1, sticky="w", pady=7, padx=12)
        ttk.Label(form, text="실행 방식").grid(row=1, column=0, sticky="w", pady=7)
        ttk.Combobox(form, textvariable=self.launch_mode_var, state="readonly", values=("Ollama 방식으로 실행", "Codex 직접 실행"), width=25).grid(row=1, column=1, sticky="w", pady=7, padx=12)
        ttk.Checkbutton(form, text="제한 없는 Codex 모드 사용 (승인 및 샌드박스 보호 해제)", variable=self.unrestricted_var).grid(row=2, column=0, columnspan=2, sticky="w", pady=7)
        ttk.Checkbutton(form, text="앱 시작 시 필수 프로그램이 없으면 자동 설치", variable=self.auto_install_var).grid(row=3, column=0, columnspan=2, sticky="w", pady=7)
        ttk.Button(form, text="설정 저장", style="Accent.TButton", command=self.save_settings).grid(row=4, column=0, sticky="w", pady=(14, 0))
        info = ttk.LabelFrame(self.settings_tab, text="경로", style="Card.TLabelframe")
        info.pack(fill="x", pady=(14, 0))
        ttk.Label(info, text=f"앱 폴더: {ROOT}", wraplength=900).pack(anchor="w")
        ttk.Label(info, text=f"프로젝트 폴더: {PROJECTS_DIR}", wraplength=900).pack(anchor="w", pady=4)
        ttk.Label(info, text=f"설정 및 로그: {CONFIG_DIR}", wraplength=900).pack(anchor="w")

    def _build_activity(self) -> None:
        ttk.Label(self.activity_tab, text="활동 로그", style="Title.TLabel").pack(anchor="w")
        ttk.Label(self.activity_tab, text="설치와 명령 출력은 이 화면 및 로컬 로그 파일에 기록됩니다.", style="Subtitle.TLabel").pack(anchor="w", pady=(2, 10))
        holder = ttk.Frame(self.activity_tab)
        holder.pack(fill="both", expand=True)
        self.log_text = tk.Text(holder, wrap="word", font=("Cascadia Mono", 10), state="disabled")
        scroll = ttk.Scrollbar(holder, orient="vertical", command=self.log_text.yview)
        self.log_text.configure(yscrollcommand=scroll.set)
        self.log_text.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")
        ttk.Button(self.activity_tab, text="화면 지우기", command=self.clear_log).pack(anchor="w", pady=(10, 0))

    def _consume_events(self) -> None:
        try:
            while True:
                kind, value = self.runner.events.get_nowait()
                if kind == "log":
                    self.log(str(value))
                elif kind == "project":
                    self.set_project(Path(value))
                elif kind == "local_reply":
                    self.chat_history.append({"role": "assistant", "content": str(value)})
                    self._append_local_message("로컬 모델", str(value))
                    self.local_chat_status_var.set("준비됨")
                elif kind == "state":
                    self.status_var.set("작업 중…" if value else "준비됨")
                    if not value:
                        if self.local_chat_status_var.get() == "응답 생성 중…":
                            self.local_chat_status_var.set("오류로 중단됨")
                        self.refresh_everything()
        except queue.Empty:
            pass
        self.after(100, self._consume_events)

    def log(self, message: str) -> None:
        stamp = time.strftime("%H:%M:%S")
        entry = f"[{stamp}] {message}\n"
        self.log_text.configure(state="normal")
        self.log_text.insert("end", entry)
        self.log_text.see("end")
        self.log_text.configure(state="disabled")
        try:
            CONFIG_DIR.mkdir(parents=True, exist_ok=True)
            with LOG_FILE.open("a", encoding="utf-8") as log_file:
                log_file.write(entry)
        except OSError:
            pass

    def clear_log(self) -> None:
        self.log_text.configure(state="normal")
        self.log_text.delete("1.0", "end")
        self.log_text.configure(state="disabled")

    @staticmethod
    def resolve_command(executable: str) -> str | None:
        found = shutil.which(executable)
        if found:
            return found
        local_programs = Path(os.environ.get("LOCALAPPDATA", "")) / "Programs"
        python_candidates = tuple(local_programs.glob("Python/Python*/python.exe"))
        bundled_nodes = tuple((ROOT / "tools").glob("node-v*/node.exe"))
        bundled_npms = tuple((ROOT / "tools").glob("node-v*/npm.cmd"))
        bundled_corepacks = tuple((ROOT / "tools").glob("node-v*/corepack.cmd"))
        candidates = {
            "npm": (*bundled_npms, Path(os.environ.get("ProgramFiles", "")) / "nodejs" / "npm.cmd", Path(os.environ.get("APPDATA", "")) / "npm" / "npm.cmd"),
            "node": (*bundled_nodes, Path(os.environ.get("ProgramFiles", "")) / "nodejs" / "node.exe"),
            "corepack": (*bundled_corepacks, Path(os.environ.get("ProgramFiles", "")) / "nodejs" / "corepack.cmd"),
            "git": (ROOT / "tools" / "git" / "cmd" / "git.exe", Path(os.environ.get("ProgramFiles", "")) / "Git" / "cmd" / "git.exe"),
            "ollama": (Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Ollama" / "ollama.exe", Path(os.environ.get("ProgramFiles", "")) / "Ollama" / "ollama.exe"),
            "codex": (ROOT / "tools" / "npm-global" / "codex.cmd", Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "OpenAI" / "Codex" / "bin" / "codex.exe", Path(os.environ.get("APPDATA", "")) / "npm" / "codex.cmd"),
            "python": python_candidates,
        }
        for path in candidates.get(executable, ()):
            if path.is_file():
                return str(path)
        return None

    def refresh_everything(self) -> None:
        self.refresh_programs()
        self.refresh_models()
        self.refresh_projects()
        self.refresh_chat_integration()

    def ensure_startup_prerequisites(self) -> None:
        if self._auto_setup_started or not self.auto_install_var.get():
            return
        missing = [program.name for program in PROGRAMS if not self.program_status.get(program.name, (False, ""))[0]]
        if not missing:
            return
        if not self.resolve_command("winget"):
            self.log("필수 프로그램이 없지만 winget을 찾지 못해 자동 설치를 시작할 수 없습니다. App Installer를 업데이트하세요.")
            self.notebook.select(self.programs_tab)
            return
        self._auto_setup_started = True
        self.notebook.select(self.activity_tab)
        self.log("필수 프로그램이 없어 자동 설치를 시작합니다: " + ", ".join(missing))
        self.prepare_all()

    def refresh_chat_integration(self) -> None:
        try:
            codex_config = CODEX_CONFIG_FILE.read_text(encoding="utf-8")
            catalog_match = re.search(r'^\s*model_catalog_json\s*=\s*["\']([^"\']+)["\']\s*$', codex_config, re.MULTILINE)
            base_match = re.search(r'^\s*openai_base_url\s*=\s*["\']([^"\']+)["\']\s*$', codex_config, re.MULTILINE)
            if catalog_match and base_match and "/api/codex/v1" in base_match.group(1):
                catalog = Path(catalog_match.group(1))
                if catalog.exists():
                    self.codex_provider_status_var.set("ChatGPT Codex에 Ollama 모델이 추가되어 있습니다 · 앱 재시작 후 선택 가능")
                else:
                    self.codex_provider_status_var.set("Ollama 연동 설정은 있으나 모델 목록 파일이 없습니다")
            else:
                self.codex_provider_status_var.set("ChatGPT Codex에 Ollama 모델이 아직 추가되지 않았습니다")
        except OSError:
            codex_config = ""
            self.codex_provider_status_var.set("Codex 설정 파일을 찾지 못했습니다")
        endpoint = "http://127.0.0.1:11434/api/tags"
        try:
            with urllib.request.urlopen(endpoint, timeout=2) as response:
                if response.status == 200:
                    self.chat_status_var.set(f"Ollama 로컬 API 연결됨 · {endpoint}")
                    return
        except (OSError, urllib.error.URLError):
            pass
        self.chat_status_var.set(f"Ollama API에 연결되지 않았습니다 · {endpoint.rsplit('/', 1)[0]}")

    def configure_chatgpt_ollama(self) -> None:
        """Use Ollama's supported additive ChatGPT Desktop integration."""
        ollama = self.resolve_command("ollama")
        if not ollama:
            messagebox.showerror(APP_NAME, "Ollama를 찾지 못했습니다. 프로그램 탭에서 Ollama를 먼저 설치하세요.")
            return

        try:
            with urllib.request.urlopen("http://127.0.0.1:11434/api/tags", timeout=3) as response:
                inventory = json.loads(response.read().decode("utf-8"))
            installed = [str(item.get("name", "")).strip() for item in inventory.get("models", []) if item.get("name")]
        except (OSError, urllib.error.URLError, json.JSONDecodeError) as exc:
            messagebox.showerror(APP_NAME, f"Ollama 서버에 연결할 수 없습니다. 서버를 시작한 뒤 다시 시도하세요.\n\n{exc}")
            return
        if not installed:
            messagebox.showerror(APP_NAME, "설치된 Ollama 모델이 없습니다. 모델을 먼저 다운로드하세요.")
            return

        requested = self.model_var.get().strip()
        model = requested if requested in installed else installed[0]
        env = os.environ.copy()
        windows_powershell = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "WindowsPowerShell" / "v1.0"
        # Ollama's Windows launcher calls powershell.exe by name. GUI launchers
        # may inherit a PATH without System32, causing false "not installed" errors.
        if (windows_powershell / "powershell.exe").is_file():
            env["PATH"] = str(windows_powershell) + os.pathsep + env.get("PATH", "")

        def work() -> None:
            self.runner.command([ollama, "launch", "chatgpt", "--config", "--model", model], env=env)

            def finish() -> None:
                self.model_var.set(model)
                self.settings.data["model"] = model
                self.settings.save()
                self.refresh_chat_integration()
                self.log("Ollama 로컬 모델과 ChatGPT 기본 모델을 Codex 선택기에 함께 구성했습니다. 앱을 직접 다시 시작하면 반영됩니다.")

            self.after(0, finish)

        self.runner.submit("ChatGPT Codex에 Ollama 모델 추가", work)

    def choose_chatgpt_executable(self) -> None:
        path = filedialog.askopenfilename(
            title="ChatGPT 실행 파일 선택",
            filetypes=(("실행 파일 또는 바로 가기", "*.exe *.lnk"), ("모든 파일", "*.*")),
        )
        if not path:
            return
        self.chatgpt_path_var.set(path)
        self.settings.data["chatgpt_executable"] = path
        self.settings.save()
        self.log(f"ChatGPT 실행 파일을 저장했습니다: {path}")

    def open_chatgpt(self) -> None:
        configured = Path(self.chatgpt_path_var.get()) if self.chatgpt_path_var.get().strip() else None
        candidates = (
            configured,
            Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "ChatGPT" / "ChatGPT.exe",
            Path(os.environ.get("ProgramFiles", "")) / "ChatGPT" / "ChatGPT.exe",
        )
        executable = next((path for path in candidates if path and path.is_file()), None)
        if executable is None:
            try:
                os.startfile(r"shell:AppsFolder\OpenAI.Codex_2p2nqsd0c76g0!App")
                self.log("Windows 앱 목록에서 ChatGPT 데스크톱을 열었습니다.")
                return
            except OSError:
                messagebox.showinfo(APP_NAME, "ChatGPT 실행 파일을 찾지 못했습니다. 먼저 'ChatGPT 실행 파일 지정'을 선택하세요.")
                return
        try:
            if executable.suffix.lower() == ".lnk":
                os.startfile(executable)
            else:
                subprocess.Popen([str(executable)])
            self.log("ChatGPT 데스크톱을 열었습니다.")
        except OSError as exc:
            messagebox.showerror(APP_NAME, f"ChatGPT를 열 수 없습니다: {exc}")

    def _append_local_message(self, sender: str, content: str) -> None:
        self.local_chat_output.configure(state="normal")
        self.local_chat_output.insert("end", f"{sender}\n", ("sender",))
        self.local_chat_output.insert("end", content.strip() + "\n\n")
        self.local_chat_output.tag_configure("sender", font=("Segoe UI", 11, "bold"))
        self.local_chat_output.see("end")
        self.local_chat_output.configure(state="disabled")

    def clear_local_chat(self) -> None:
        self.chat_history.clear()
        self.local_chat_output.configure(state="normal")
        self.local_chat_output.delete("1.0", "end")
        self.local_chat_output.configure(state="disabled")
        self.local_chat_status_var.set("준비됨")

    def send_local_chat(self) -> None:
        prompt = self.local_chat_input.get("1.0", "end").strip()
        if not prompt:
            return
        model = self.model_var.get().strip()
        if not model:
            messagebox.showerror(APP_NAME, "먼저 로컬 모델을 선택하세요.")
            return
        self.local_chat_input.delete("1.0", "end")
        self.chat_history.append({"role": "user", "content": prompt})
        self._append_local_message("나", prompt)
        self.local_chat_status_var.set("응답 생성 중…")
        messages = list(self.chat_history)

        def work() -> None:
            payload = json.dumps({"model": model, "messages": messages, "stream": False}).encode("utf-8")
            request = urllib.request.Request(
                "http://127.0.0.1:11434/api/chat",
                data=payload,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            try:
                with urllib.request.urlopen(request, timeout=300) as response:
                    answer = json.loads(response.read().decode("utf-8"))
                content = str(answer.get("message", {}).get("content", "")).strip()
                if not content:
                    raise RuntimeError("로컬 모델이 비어 있는 응답을 반환했습니다.")
                self.runner.events.put(("local_reply", content))
            except urllib.error.URLError as exc:
                raise RuntimeError("Ollama API에 연결할 수 없습니다. Ollama 서버를 시작하고 모델을 내려받은 뒤 다시 시도하세요.") from exc
        self.runner.submit(f"로컬 모델 응답 생성: {model}", work)

    def refresh_programs(self) -> None:
        for item in self.program_tree.get_children():
            self.program_tree.delete(item)
        self.program_status.clear()
        for program in PROGRAMS:
            path = self.resolve_command(program.executable)
            detail = "찾지 못함"
            present = path is not None
            if path:
                try:
                    version_command = [path, *program.version_args]
                    if path.lower().endswith((".cmd", ".bat")):
                        version_command = ["cmd.exe", "/d", "/c", subprocess.list2cmdline(version_command)]
                    result = subprocess.run(version_command, capture_output=True, text=True, timeout=5, encoding="utf-8", errors="replace", creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
                    detail = (result.stdout or result.stderr).strip().splitlines()[0] if (result.stdout or result.stderr).strip() else path
                except (OSError, subprocess.SubprocessError):
                    detail = path
            installer = "npm 패키지" if program.name == "Codex CLI" else ("winget" if program.winget_id else "수동")
            self.program_status[program.name] = (present, detail)
            self.program_tree.insert("", "end", iid=program.name, values=("설치됨" if present else "없음", detail, installer))

    def refresh_models(self) -> None:
        for item in self.model_tree.get_children():
            self.model_tree.delete(item)
        ollama = self.resolve_command("ollama")
        if not ollama:
            return
        try:
            result = subprocess.run([ollama, "list"], capture_output=True, text=True, timeout=8, encoding="utf-8", errors="replace", creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            for line in result.stdout.splitlines()[1:]:
                match = re.match(r"^(\S+)\s+\S+\s+([\d.]+\s+\S+)\s+(.+)$", line.strip())
                if match:
                    self.model_tree.insert("", "end", values=match.groups())
        except (OSError, subprocess.SubprocessError):
            pass

    def refresh_projects(self) -> None:
        for item in self.project_tree.get_children():
            self.project_tree.delete(item)
        projects: list[Path] = []
        if PROJECTS_DIR.exists():
            projects.extend(sorted((path for path in PROJECTS_DIR.iterdir() if path.is_dir()), key=lambda path: path.name.lower()))
        active = Path(self.project_var.get())
        if active.is_dir() and active not in projects and active != ROOT:
            projects.insert(0, active)
        for project in projects:
            self.project_tree.insert("", "end", values=(project.name, str(project)))

    def install_selected_program(self) -> None:
        selection = self.program_tree.selection()
        if not selection:
            self.log("먼저 목록에서 프로그램을 선택하세요.")
            return
        program = next(item for item in PROGRAMS if item.name == selection[0])
        self.install_program(program)

    def install_program(self, program: Program) -> None:
        def work() -> None:
            if program.name == "Codex CLI":
                node = self.resolve_command("npm")
                if not node:
                    raise RuntimeError("먼저 Node.js LTS를 설치하세요. Codex CLI에는 npm이 필요합니다.")
                self.runner.command([node, "install", "-g", "@openai/codex"])
            elif program.winget_id:
                winget = self.resolve_command("winget")
                if not winget:
                    raise RuntimeError("Windows 패키지 관리자(winget)를 찾지 못했습니다. App Installer를 업데이트한 뒤 다시 시도하세요.")
                self.runner.command([winget, "install", "--id", program.winget_id, "--exact", "--source", "winget", "--accept-source-agreements", "--accept-package-agreements"])
            else:
                raise RuntimeError(f"{program.name}에 설정된 설치 방식이 없습니다.")
        self.runner.submit(f"{program.name} 설치", work)

    def install_custom_package(self) -> None:
        package_id = simpledialog.askstring(APP_NAME, "winget 패키지 ID를 입력하세요 (예: Microsoft.VisualStudioCode):", parent=self)
        if not package_id:
            return
        package_id = package_id.strip()
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", package_id):
            messagebox.showerror(APP_NAME, "올바른 winget 패키지 ID를 입력하세요.")
            return

        def work() -> None:
            winget = self.require_command("winget")
            self.runner.command([winget, "install", "--id", package_id, "--exact", "--source", "winget", "--accept-source-agreements", "--accept-package-agreements"])
        self.runner.submit(f"패키지 설치: {package_id}", work)

    def prepare_all(self) -> None:
        def work() -> None:
            for program in PROGRAMS:
                if self.resolve_command(program.executable):
                    self.runner.events.put(("log", f"{program.name}: 이미 설치되어 있습니다."))
                    continue
                if program.name == "Codex CLI":
                    npm = self.resolve_command("npm")
                    if not npm:
                        raise RuntimeError("Node.js 설치는 끝났지만 현재 프로세스에서 npm을 찾지 못했습니다. 앱을 다시 시작한 뒤 '모두 준비'를 다시 실행하세요.")
                    self.runner.command([npm, "install", "-g", "@openai/codex"])
                elif program.winget_id:
                    winget = self.resolve_command("winget")
                    if not winget:
                        raise RuntimeError("Windows 패키지 관리자(winget)를 찾지 못했습니다. App Installer를 업데이트한 뒤 다시 시도하세요.")
                    self.runner.command([winget, "install", "--id", program.winget_id, "--exact", "--source", "winget", "--accept-source-agreements", "--accept-package-agreements"])
            self.runner.events.put(("log", "필수 도구 확인이 끝났습니다. 새로 설치한 명령을 찾지 못하면 앱을 다시 시작하세요."))
        self.runner.submit("필수 도구 모두 준비", work)

    def start_ollama(self) -> None:
        def work() -> None:
            ollama = self.require_command("ollama")
            try:
                subprocess.run([ollama, "list"], capture_output=True, timeout=3, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
                self.runner.events.put(("log", "Ollama 서버가 이미 실행 중입니다."))
                return
            except (OSError, subprocess.SubprocessError):
                pass
            subprocess.Popen([ollama, "serve"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            for _ in range(30):
                time.sleep(1)
                try:
                    if subprocess.run([ollama, "list"], capture_output=True, timeout=3, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)).returncode == 0:
                        self.runner.events.put(("log", "Ollama 서버를 시작했습니다."))
                        return
                except (OSError, subprocess.SubprocessError):
                    pass
            raise RuntimeError("30초 안에 Ollama 서버가 준비되지 않았습니다.")
        self.runner.submit("Ollama 서버 시작", work)

    def require_command(self, command: str) -> str:
        found = self.resolve_command(command)
        if not found:
            raise RuntimeError(f"{command}을(를) 찾지 못했습니다. 먼저 프로그램 탭에서 설치하세요.")
        return found

    def save_model(self) -> bool:
        model = self.model_var.get().strip()
        if not model or any(char.isspace() for char in model):
            messagebox.showerror(APP_NAME, "올바른 Ollama 모델 이름을 입력하세요. 예: qwen3:8b")
            return False
        self.settings.data["model"] = model
        self.settings.save()
        self.log(f"모델 선택을 저장했습니다: {model}")
        return True

    def pull_selected_model(self) -> None:
        if not self.save_model():
            return
        model = self.model_var.get().strip()
        if not model:
            return
        def work() -> None:
            ollama = self.require_command("ollama")
            env = os.environ.copy()
            env["OLLAMA_CONTEXT_LENGTH"] = self.context_var.get().strip() or "65536"
            self.runner.command([ollama, "pull", model], env=env)
        self.runner.submit(f"Ollama 모델 다운로드: {model}", work)

    def use_selected_model(self) -> None:
        selection = self.model_tree.selection()
        if not selection:
            self.log("먼저 설치된 모델을 선택하세요.")
            return
        self.model_var.set(str(self.model_tree.item(selection[0], "values")[0]))
        self.save_model()

    def choose_project(self) -> None:
        path = filedialog.askdirectory(title="프로젝트 폴더 선택", initialdir=self.project_var.get() if Path(self.project_var.get()).is_dir() else str(ROOT))
        if path:
            self.set_project(Path(path))

    def set_project(self, path: Path) -> None:
        self.project_var.set(str(path.resolve()))
        self.settings.data["active_project"] = self.project_var.get()
        self.settings.save()
        self.refresh_projects()
        self.log(f"현재 프로젝트: {self.project_var.get()}")

    def clone_repository(self) -> None:
        url = simpledialog.askstring(APP_NAME, "Git 저장소 URL(HTTPS 또는 SSH):", parent=self)
        if not url:
            return
        url = url.strip()
        if not (url.startswith("https://") or url.startswith("git@")):
            messagebox.showerror(APP_NAME, "올바른 HTTPS 또는 SSH Git 저장소 URL을 입력하세요.")
            return
        name = url.rstrip("/").rsplit("/", 1)[-1].removesuffix(".git")
        if not name or any(char in name for char in '<>:"/\\|?*'):
            messagebox.showerror(APP_NAME, "URL에서 안전한 폴더 이름을 만들 수 없습니다.")
            return
        destination = PROJECTS_DIR / name
        if destination.exists():
            self.set_project(destination)
            self.log(f"프로젝트가 이미 있습니다: {destination}")
            return
        def work() -> None:
            git = self.require_command("git")
            PROJECTS_DIR.mkdir(parents=True, exist_ok=True)
            self.runner.command([git, "clone", url, str(destination)])
            self.runner.events.put(("project", destination))
        self.runner.submit(f"저장소 복제: {name}", work)

    def use_selected_project(self) -> None:
        selection = self.project_tree.selection()
        if not selection:
            self.log("먼저 프로젝트를 선택하세요.")
            return
        values = self.project_tree.item(selection[0], "values")
        self.set_project(Path(str(values[1])))

    def open_selected_project(self) -> None:
        selection = self.project_tree.selection()
        path = Path(str(self.project_tree.item(selection[0], "values")[1])) if selection else Path(self.project_var.get())
        if path.is_dir():
            os.startfile(path)  # Windows-only application.

    def install_dependencies(self) -> None:
        project = Path(self.project_var.get())
        if not project.is_dir():
            messagebox.showerror(APP_NAME, "먼저 존재하는 프로젝트 폴더를 선택하세요.")
            return
        self.runner.submit("프로젝트 의존성 설치", lambda: self.install_project_dependencies(project))

    def install_project_dependencies(self, project: Path) -> None:
        package = project / "package.json"
        requirements = project / "requirements.txt"
        pyproject = project / "pyproject.toml"
        setup_py = project / "setup.py"
        dependency_files = (
            "package.json", "package-lock.json", "npm-shrinkwrap.json", "pnpm-lock.yaml", "yarn.lock",
            "requirements.txt", "pyproject.toml", "setup.py", "uv.lock", "poetry.lock",
        )
        manifests = [project / name for name in dependency_files if (project / name).is_file()]
        if not manifests:
            self.runner.events.put(("log", "지원되는 의존성 파일이 없어 설치를 건너뜁니다."))
            return
        signature = "|".join(f"{path.name}:{path.stat().st_size}:{path.stat().st_mtime_ns}" for path in manifests)
        project_key = hashlib.sha256(str(project.resolve()).casefold().encode("utf-8")).hexdigest()
        state_file = CONFIG_DIR / "deps" / f"{project_key}.json"
        required_dirs = []
        if package.exists():
            required_dirs.append(project / "node_modules")
        if any(path.exists() for path in (requirements, pyproject, setup_py)):
            required_dirs.append(project / ".venv")
        try:
            previous = json.loads(state_file.read_text(encoding="utf-8"))
            if previous.get("signature") == signature and all(path.exists() for path in required_dirs):
                self.runner.events.put(("log", "의존성 파일이 바뀌지 않아 설치를 건너뜁니다."))
                return
        except (OSError, json.JSONDecodeError, AttributeError):
            pass
        if package.exists():
            if (project / "pnpm-lock.yaml").exists():
                corepack = self.require_command("corepack")
                self.runner.command([corepack, "pnpm", "install", "--frozen-lockfile"], cwd=project)
            elif (project / "yarn.lock").exists():
                corepack = self.require_command("corepack")
                self.runner.command([corepack, "yarn", "install", "--immutable"], cwd=project)
            else:
                npm = self.require_command("npm")
                self.runner.command([npm, "ci" if (project / "package-lock.json").exists() else "install"], cwd=project)
        if requirements.exists() or pyproject.exists() or setup_py.exists():
            python = self.require_command("python")
            venv_python = project / ".venv" / "Scripts" / "python.exe"
            if not venv_python.exists():
                self.runner.command([python, "-m", "venv", str(project / ".venv")])
            if requirements.exists():
                self.runner.command([str(venv_python), "-m", "pip", "install", "-r", str(requirements)])
            if pyproject.exists() or setup_py.exists():
                self.runner.command([str(venv_python), "-m", "pip", "install", "-e", str(project)])
        state_file.parent.mkdir(parents=True, exist_ok=True)
        temp = state_file.with_suffix(".tmp")
        temp.write_text(json.dumps({"signature": signature}, ensure_ascii=False), encoding="utf-8")
        temp.replace(state_file)

    def save_settings(self) -> bool:
        context = self.context_var.get().strip()
        if not context.isdecimal() or int(context) < 1024:
            messagebox.showerror(APP_NAME, "컨텍스트 길이는 1024 이상의 정수여야 합니다.")
            return False
        self.settings.data.update({
            "model": self.model_var.get().strip() or DEFAULT_MODEL,
            "active_project": self.project_var.get(),
            "context_length": context,
            "launch_mode": self.launch_mode_var.get(),
            "unrestricted": self.unrestricted_var.get(),
            "chatgpt_executable": self.chatgpt_path_var.get(),
            "oss_provider": self.oss_provider_var.get(),
            "auto_install": self.auto_install_var.get(),
        })
        self.settings.save()
        self.log("설정을 저장했습니다.")
        return True

    def launch_codex(self, resume: bool) -> None:
        if not self.save_settings():
            return
        project = Path(self.project_var.get())
        if not project.is_dir():
            messagebox.showerror(APP_NAME, "먼저 존재하는 프로젝트 폴더를 선택하세요.")
            return
        model = self.model_var.get().strip()
        unrestricted = self.unrestricted_var.get()
        try:
            if self.launch_mode_var.get() == "Ollama 방식으로 실행":
                executable = self.require_command("ollama")
                command = [executable, "launch", "codex", "--model", model, "--yes", "--"]
                if resume:
                    command.extend(("resume", "--all"))
                if unrestricted:
                    command.append("--dangerously-bypass-approvals-and-sandbox")
                command.extend(("-C", str(project)))
            else:
                executable = self.require_command("codex")
                command = [executable]
                if resume:
                    command.extend(("resume", "--all"))
                if unrestricted:
                    command.append("--dangerously-bypass-approvals-and-sandbox")
                command.extend(("-C", str(project)))
        except RuntimeError as exc:
            messagebox.showerror(APP_NAME, str(exc))
            return
        env = os.environ.copy()
        env["OLLAMA_CONTEXT_LENGTH"] = self.context_var.get().strip() or "65536"
        def work() -> None:
            self.install_project_dependencies(project)
            subprocess.Popen(["cmd.exe", "/k", subprocess.list2cmdline(command)], cwd=str(project), env=env, creationflags=subprocess.CREATE_NEW_CONSOLE)
            self.runner.events.put(("log", "새 터미널을 열었습니다: " + subprocess.list2cmdline(command)))
        self.runner.submit("프로젝트 준비 후 Codex 실행", work)

    def _close(self) -> None:
        self.save_settings()
        self.destroy()


def main() -> None:
    if sys.platform != "win32":
        raise SystemExit("Local Codex AI는 현재 Windows만 지원합니다.")
    LocalCodexApp().mainloop()


if __name__ == "__main__":
    main()
