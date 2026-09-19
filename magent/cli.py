"""MAgent 命令行入口。

    python -m magent            # 启动本地服务并打开浏览器（默认行为）
    python -m magent doctor     # 环境自检
    python -m magent status --root <PROJECT_ROOT>
    python -m magent run    --root <PROJECT_ROOT> [--stage <key>]
    python -m magent resume --root <PROJECT_ROOT>
"""

from __future__ import annotations

import argparse
import os
import shutil
import sys
import threading
import time
import traceback
import webbrowser
from pathlib import Path

from rich.console import Console
from rich.table import Table

from . import __version__, pipeline
from .config import ensure, load, resolve_skills_root
from .engine import run_stage
from .manifest import ManifestError, ManifestStore

console = Console()


def _doctor() -> int:
    cfg = ensure()
    provider = cfg["provider"]
    try:
        skills_root, skills_source = resolve_skills_root(cfg)
        skills_note = f"[{skills_source}] {skills_root}"
        skills_ok = True
    except FileNotFoundError as exc:
        skills_root, skills_note, skills_ok = None, str(exc), False

    checks: list[tuple[str, bool, str]] = []

    checks.append(("Python ≥ 3.10", sys.version_info >= (3, 10), sys.version.split()[0]))
    checks.append(("skills 目录", skills_ok, skills_note))
    if skills_ok:
        read_script = skills_root / "mm-orchestrator" / "scripts" / "read_complete.py"
        checks.append(("read_complete.py", read_script.is_file(), str(read_script)))
        missing_skills = [
            s for s in pipeline.STAGES
            if not (skills_root / pipeline.STAGES[s].skill / "SKILL.md").is_file()
        ]
        checks.append(("8 个阶段 SKILL.md 齐全", not missing_skills,
                       "缺失: " + ",".join(missing_skills) if missing_skills else "齐全"))

    try:
        import fitz  # noqa: F401
        checks.append(("PyMuPDF (fitz)", True, "已安装"))
    except ImportError:
        checks.append(("PyMuPDF (fitz)", False, "未安装（多个机检脚本硬依赖）"))

    try:
        import jsonschema  # noqa: F401
        checks.append(("jsonschema", True, "已安装（manifest 全量 schema 校验）"))
    except ImportError:
        checks.append(("jsonschema", False, "未安装（退化为 stdlib 校验）"))

    xelatex = shutil.which("xelatex")
    checks.append(("xelatex（论文编译）", xelatex is not None, xelatex or "未找到——论文阶段需要 TeX 发行版"))
    pdftocairo = shutil.which("pdftocairo")
    checks.append(("pdftocairo（TikZ 转 PNG）", pdftocairo is not None,
                   pdftocairo or "未找到——仅影响 graphics 阶段，可 winget install poppler"))

    key_ok = bool(provider.get("api_key"))
    checks.append(("模型 API Key", key_ok, f"{provider['name']} / {provider['model']}" + ("" if key_ok else "（尚未配置）")))

    if key_ok:
        from .llm import LLMClient, LLMError
        try:
            latency = LLMClient(provider).ping()
            checks.append(("模型连通性", True, f"{latency} ms"))
        except LLMError as exc:
            checks.append(("模型连通性", False, str(exc)[:120]))

    table = Table(title=f"MAgent v{__version__} 环境自检")
    table.add_column("检查项")
    table.add_column("结果")
    table.add_column("说明", overflow="fold")
    all_ok = True
    for name, ok, detail in checks:
        # pdftocairo 缺失不阻断（仅影响 graphics）
        blocking = name not in {"pdftocairo（TikZ 转 PNG）"}
        all_ok = all_ok and (ok or not blocking)
        table.add_row(name, "[green]✔[/green]" if ok else "[red]✘[/red]", detail)
    console.print(table)
    return 0 if all_ok else 1


def _load_manifest_or_exit(root: Path) -> ManifestStore:
    store = ManifestStore(root)
    try:
        store.load()
    except ManifestError as exc:
        console.print(f"[red]{exc}[/red]")
        sys.exit(2)
    return store


def _status(root: Path) -> int:
    store = _load_manifest_or_exit(root)
    table = Table(title=f"{store.data.get('project', {}).get('title', '')} · {root}")
    table.add_column("阶段")
    table.add_column("owner skill")
    table.add_column("状态")
    statuses = store.summary()["stages"]
    for key in pipeline.ORDER:
        stage = pipeline.STAGES[key]
        status = statuses.get(key, "pending")
        color = {
            "complete": "green", "in_progress": "cyan", "failed": "red",
            "n_a": "dim", "conditional": "yellow", "pending": "white",
        }.get(status, "white")
        table.add_row(f"{stage.title} ({key})", stage.skill, f"[{color}]{status}[/{color}]")
    console.print(table)
    console.print(f"产物登记数：{store.summary()['artifacts_count']}")
    return 0


def _rich_log(event: dict) -> None:
    etype = event.get("type")
    if etype == "stage":
        console.print(f"[bold blue]■[/bold blue] {event.get('msg', '')}")
    elif etype == "tool_call":
        console.print(f"  [dim]→ {event.get('name')} {event.get('args', '')[:120]}[/dim]")
    elif etype == "gate":
        mark = "[green]✔[/green]" if event.get("ok") else ("[dim]– 跳过[/dim]" if event.get("skipped") else "[red]✘[/red]")
        console.print(f"  {mark} 门禁 {event.get('name')} (exit {event.get('exit_code')})")
    elif etype == "assistant_text":
        console.print(f"  [italic]{event.get('text', '')[:200]}[/italic]")
    elif etype == "error":
        console.print(f"[red]✗ {event.get('msg', '')}[/red]")
    elif etype == "usage":
        console.print(f"  [dim]tokens 累计：{event.get('prompt_tokens', 0)} in / {event.get('completion_tokens', 0)} out[/dim]")


def _run(root: Path, stage: str | None) -> int:
    cfg = load()
    store = _load_manifest_or_exit(root)
    if stage is None:
        stage = pipeline.next_stage(store.summary()["stages"])
        if stage is None:
            console.print("[green]全部阶段已处理完毕。[/green]")
            return 0
    result = run_stage(root, stage, cfg, log=_rich_log)
    color = {"complete": "green", "paused": "yellow", "error": "red"}.get(result.outcome, "white")
    console.print(f"[{color}bold]结果：{result.outcome}[/{color}bold] {result.detail}")
    return 0 if result.outcome == "complete" else 1


def _wait_port_ready(host: str, port: int, timeout: float = 15.0) -> bool:
    import socket
    import time

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with socket.create_connection((host, port), timeout=0.5):
                return True
        except OSError:
            time.sleep(0.2)
    return False


def _serve(host: str, port: int, no_window: bool = False) -> int:
    """启动本地服务；默认用原生桌面窗口承载界面（不打开浏览器）。

    关闭窗口 = 退出软件。--no-window 或缺 pywebview 时回退为浏览器模式。
    """
    import uvicorn

    from .server import app

    url = f"http://{host}:{port}"
    server = uvicorn.Server(uvicorn.Config(app, host=host, port=port, log_level="warning"))
    threading.Thread(target=server.run, daemon=True).start()
    _wait_port_ready(host, port)

    if not no_window:
        try:
            import webview

            webview.create_window(
                "MAgent · 数模全流程 Agent",
                url,
                width=1280,
                height=840,
                min_size=(960, 640),
                background_color="#f5f6f8",
            )
            console.print(f"[bold green]MAgent v{__version__} 已启动（桌面窗口）：{url}[/bold green]")
            webview.start()
            return 0
        except Exception as exc:
            console.print(f"[yellow]桌面窗口不可用（{exc}），回退浏览器模式[/yellow]")

    console.print(f"[bold green]MAgent v{__version__} 启动：{url}[/bold green]")
    webbrowser.open(url)
    try:
        while server.should_exit is False:
            threading.Event().wait(3600)
    except KeyboardInterrupt:
        server.should_exit = True
    return 0


def _sync_skills(src: str | None) -> int:
    from .skillsync import sync_from

    candidates = [src] if src else [
        r"D:\AAA-MMW\MM_workflow\skills",
        str(Path.home() / "MM_workflow" / "skills"),
    ]
    for cand in candidates:
        if cand and (Path(cand) / "mm-orchestrator" / "scripts" / "read_complete.py").is_file():
            sync_from(Path(cand), log=console.print)
            return 0
    console.print("[red]未找到可用的 skills 源目录，请用 --from 指定 MM_workflow/skills 路径[/red]")
    return 1


def main() -> int:
    # pythonw（无控制台双击启动）下 stdout/stderr 为 None，rich/logging 一碰即崩。
    # 统一重定向到空设备；真实报错走 _main 的崩溃日志。
    if sys.stdout is None:
        sys.stdout = open(os.devnull, "w", encoding="utf-8")
    if sys.stderr is None:
        sys.stderr = open(os.devnull, "w", encoding="utf-8")
    try:
        return _main()
    except SystemExit:
        raise
    except Exception:
        crash_log = Path.home() / ".magent" / "crash.log"
        crash_log.parent.mkdir(parents=True, exist_ok=True)
        with crash_log.open("a", encoding="utf-8") as fh:
            fh.write(f"\n===== {time.strftime('%Y-%m-%d %H:%M:%S')} =====\n")
            traceback.print_exc(file=fh)
        raise


def _main() -> int:
    parser = argparse.ArgumentParser(prog="magent", description="MAgent 数模全流程 Agent 软件")
    parser.add_argument("--version", action="version", version=f"MAgent {__version__}")
    sub = parser.add_subparsers(dest="command")

    serve_p = sub.add_parser("serve", help="启动 MAgent（默认桌面窗口；--no-window 走浏览器）")
    serve_p.add_argument("--host", default="127.0.0.1")
    serve_p.add_argument("--port", type=int, default=8765)
    serve_p.add_argument("--no-window", action="store_true", help="不开桌面窗口，改用浏览器打开")

    sub.add_parser("doctor", help="环境自检")
    sync_p = sub.add_parser("sync-skills", help="把外部 MM_workflow/skills 同步进软件内置副本")
    sync_p.add_argument("--from", dest="src", default=None)
    run_p = sub.add_parser("run", help="运行一个阶段（默认下一个可执行阶段）")
    run_p.add_argument("--root", required=True)
    run_p.add_argument("--stage", choices=pipeline.ORDER)
    resume_p = sub.add_parser("resume", help="从 manifest 续跑下一个阶段")
    resume_p.add_argument("--root", required=True)
    status_p = sub.add_parser("status", help="查看阶段状态")
    status_p.add_argument("--root", required=True)

    args = parser.parse_args()
    ensure()

    if args.command in (None, "serve"):
        host, port, no_window = "127.0.0.1", 8765, False
        if args.command == "serve":
            host, port, no_window = args.host, args.port, args.no_window
        return _serve(host, port, no_window)
    if args.command == "doctor":
        return _doctor()
    if args.command == "sync-skills":
        return _sync_skills(args.src)
    if args.command == "status":
        return _status(Path(args.root).resolve())
    if args.command == "run":
        return _run(Path(args.root).resolve(), args.stage)
    if args.command == "resume":
        return _run(Path(args.root).resolve(), None)
    parser.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())
