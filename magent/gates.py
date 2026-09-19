"""门禁引擎：模型调 finish 后，由代码独立重跑全部机检。

三层门禁：
1. 完整读取回执校验（read_complete.py verify）
2. 阶段验收脚本（stage-pipeline.md 的映射，全部 exit 0）
3. manifest 校验（validate_manifest.py）+ 产物存在性

任何一项失败即驳回 finish，错误原文回喂给模型修复。
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from .pipeline import StageDef
from .tools import FinishSignal

GATE_TIMEOUT = 600


@dataclass
class GateItem:
    name: str
    cmd: str
    exit_code: int | None = None
    ok: bool = False
    skipped: bool = False
    tail: str = ""

    def as_event(self) -> dict:
        return {
            "type": "gate",
            "name": self.name,
            "exit_code": self.exit_code,
            "ok": self.ok,
            "skipped": self.skipped,
        }


@dataclass
class GateReport:
    items: list[GateItem] = field(default_factory=list)

    @property
    def all_ok(self) -> bool:
        return all(item.ok or item.skipped for item in self.items)

    def failure_text(self) -> str:
        lines = []
        for item in self.items:
            if item.skipped:
                continue
            if not item.ok:
                lines.append(f"✗ {item.name}（exit {item.exit_code}）\n{item.tail[-2000:]}")
        return "\n".join(lines) or "（未知门禁失败）"


def run_gate_command(name: str, cmd: list[str], root: Path) -> GateItem:
    item = GateItem(name=name, cmd=" ".join(cmd))
    try:
        proc = subprocess.run(
            cmd,
            cwd=root,
            text=True,
            encoding="utf-8",
            errors="replace",
            capture_output=True,
            timeout=GATE_TIMEOUT,
        )
        item.exit_code = proc.returncode
        item.ok = proc.returncode == 0
        output = (proc.stdout or "") + (proc.stderr or "")
    except subprocess.TimeoutExpired:
        item.exit_code = -1
        item.ok = False
        output = f"[TIMEOUT] {GATE_TIMEOUT}s"
    except OSError as exc:
        item.exit_code = -1
        item.ok = False
        output = f"[OSERROR] {exc}"
    item.tail = output[-2500:]
    return item


def _exists_gate(path: str, root: Path) -> GateItem:
    ok = (root / path).is_file()
    return GateItem(
        name=f"exists:{path}",
        cmd=f"test -f {path}",
        exit_code=0 if ok else 1,
        ok=ok,
        tail="" if ok else f"缺少本阶段必要产物：{path}",
    )


def run_stage_gates(
    stage: StageDef,
    root: Path,
    skills_root: Path,
    python_exe: str,
    finish: FinishSignal | None = None,
    figure_files: list[Path] | None = None,
    selected_models: list[str] | None = None,
) -> GateReport:
    report = GateReport()

    def add(item: GateItem) -> None:
        report.items.append(item)

    def substitute(parts: list[str]) -> list[str]:
        return [p.replace("{PY}", python_exe).replace("{SK}", str(skills_root)) for p in parts]

    # 1) 读取回执校验
    if stage.read_gate:
        add(
            run_gate_command(
                "read_complete_verify",
                substitute([
                    "{PY}",
                    "{SK}/mm-orchestrator/scripts/read_complete.py",
                    "verify",
                    "--skill", stage.skill,
                    "--receipt", stage.receipt,
                    "--session", stage.session_ledger,
                ]),
                root,
            )
        )

    # 2) 产物存在性
    for path in stage.exists:
        add(_exists_gate(path, root))

    # 3) 阶段验收脚本
    for acc in stage.acceptance:
        when = acc.get("when")
        if when == "per_selected_model":
            if not selected_models:
                add(GateItem(name=acc["name"], cmd="", exit_code=None, ok=True, skipped=True,
                             tail="manifest.selected_models 为空，跳过模型字典复核"))
            else:
                for model_name in selected_models:
                    cmd = [p.replace("{PY}", python_exe).replace("{SK}", str(skills_root)).replace("{MODEL}", model_name)
                           for p in acc["cmd"]]
                    add(run_gate_command(f"{acc['name']}[{model_name}]", cmd, root))
            continue
        add(run_gate_command(acc["name"], substitute(acc["cmd"]), root))

    # 4) graphics 阶段按产物文件逐一审计
    if stage.dynamic == "graphics":
        figures_dir = root / "figures"
        tex_files = sorted(figures_dir.glob("*.tex")) if figures_dir.is_dir() else []
        svg_files = sorted(figures_dir.glob("*.svg")) if figures_dir.is_dir() else []
        audit_targets = [(f, "audit_tikz") for f in tex_files] + [(f, "audit_svg") for f in svg_files]
        if not audit_targets:
            add(GateItem(name="graphics_files", cmd="", exit_code=None, ok=True, skipped=True,
                         tail="figures/ 下无 .tex/.svg 产物"))
        for path, script in audit_targets[:20]:
            add(
                run_gate_command(
                    f"{script}:{path.name}",
                    substitute(["{PY}", f"{{SK}}/mm-graphics/scripts/{script}.py", path.name]),
                    root,
                )
            )

    # 5) paper_final 的 finish extras 契约
    if stage.key == "paper_final" and finish is not None:
        extras = finish.extras or {}
        final_pages = extras.get("final_body_pages")
        compiles = extras.get("compile_passes")
        pages_ok = isinstance(final_pages, int) and 25 <= final_pages <= 30
        compiles_ok = isinstance(compiles, int) and compiles >= 2
        add(
            GateItem(
                name="finish_extras_paper",
                cmd="final_body_pages∈[25,30] 且 compile_passes≥2",
                exit_code=0 if (pages_ok and compiles_ok) else 1,
                ok=pages_ok and compiles_ok,
                tail="" if (pages_ok and compiles_ok)
                else f"finish.extras 缺少合法字段：final_body_pages={final_pages!r}（需 25~30 整数），"
                     f"compile_passes={compiles!r}（需 ≥2 整数）",
            )
        )

    # 6) manifest 校验（永远最后跑）
    add(
        run_gate_command(
            "validate_manifest",
            [python_exe, str(skills_root / "mm-orchestrator" / "scripts" / "validate_manifest.py"),
             "project-manifest.json"],
            root,
        )
    )
    return report
