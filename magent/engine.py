"""引擎编排层：项目初始化、阶段运行（含门禁重试回路）、完成记账。"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

from . import pipeline
from .agent import AgentSession
from .config import resolve_skills_root
from .gates import run_stage_gates
from .llm import ContextLimitError, LLMClient, LLMError
from .manifest import ManifestStore, ManifestError
from .tools import FinishSignal, ToolBox

READ_SCRIPT_REL = "mm-orchestrator/scripts/read_complete.py"


@dataclass
class StageResult:
    stage: str
    outcome: str  # complete | paused | context_limit | already_complete | upstream_blocked | error
    detail: str = ""


def _noop_log(event: dict) -> None:
    pass


def _run_skeleton_script(script: Path, args: list[str], root: Path) -> None:
    proc = subprocess.run(
        [sys.executable, str(script), *args],
        cwd=root,
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
        timeout=120,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"{script.name} 失败（exit {proc.returncode}）：{(proc.stderr or proc.stdout)[-800:]}")


def init_project(
    root: Path,
    title: str,
    prefs: dict,
    problem_files: list[tuple[str, bytes]],
    skills_root: Path,
    log=_noop_log,
) -> ManifestStore:
    """创建 PROJECT_ROOT：落盘题面 → 跑仓库骨架脚本 → 引擎记账初始状态。"""
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    data_dir = root / "data"
    data_dir.mkdir(exist_ok=True)
    saved_names = []
    for original_name, content in problem_files:
        safe_name = Path(original_name).name or f"题目{len(saved_names) + 1}.bin"
        (data_dir / safe_name).write_bytes(content)
        saved_names.append(safe_name)
    log({"type": "stage", "msg": f"题面文件已写入 data/：{saved_names}"})

    orchestrator_scripts = skills_root / "mm-orchestrator" / "scripts"
    _run_skeleton_script(
        orchestrator_scripts / "init_project_skeleton.py",
        ["--root", str(root), "--title", title],
        root,
    )
    _run_skeleton_script(orchestrator_scripts / "init_paper_gates.py", ["--root", str(root)], root)

    schema_src = skills_root / "mm-orchestrator" / "project-manifest.schema.json"
    schema_dst = root / "project-manifest.schema.json"
    if schema_src.is_file() and not schema_dst.exists():
        shutil.copyfile(schema_src, schema_dst)

    _patch_plan_prefs(root / "plan.md", prefs)

    prefs_dir = root / ".magent"
    prefs_dir.mkdir(exist_ok=True)
    (prefs_dir / "prefs.json").write_text(
        json.dumps(prefs, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    store = ManifestStore(root)
    try:
        store.load()
    except ManifestError:
        store.init_minimal(title)
    store.add_change_log(by="MAgent/engine", action=f"init project: {title}")
    store.save()
    return store


def _patch_plan_prefs(plan_path: Path, prefs: dict) -> None:
    if not plan_path.is_file():
        return
    text = plan_path.read_text(encoding="utf-8", errors="replace")
    block = (
        f"\n## 用户偏好（MAgent 记录）\n\n"
        f"- 侧重点：{prefs.get('focus', '均衡')}\n"
        f"- 子问题数：{prefs.get('subproblems', '待赛题分析确定')}\n"
        f"- 记录时间：{time.strftime('%Y-%m-%d %H:%M:%S')}\n"
    )
    if "用户偏好（MAgent 记录）" not in text:
        plan_path.write_text(text.rstrip() + "\n" + block, encoding="utf-8")


def _list_problem_files(root: Path) -> list[str]:
    data_dir = root / "data"
    if not data_dir.is_dir():
        return []
    return sorted(p.name for p in data_dir.iterdir() if p.is_file())


def _selected_model_names(manifest: ManifestStore) -> list[str]:
    names = []
    for model in manifest.data.get("selected_models", []):
        name = model.get("name") or model.get("id")
        if name:
            names.append(str(name))
    return names


def _finalize(
    root: Path,
    stage: pipeline.StageDef,
    manifest: ManifestStore,
    finish: FinishSignal,
    skills_root: Path,
) -> list[dict]:
    """门禁全过后记账：登记产物、填阶段字段、翻状态、追加 change_log。"""
    registered = manifest.register_artifacts(finish.artifacts, stage.key)

    fields: dict = {}
    for field_name, path in stage.finalize_fields.items():
        if (root / path).is_file():
            fields[field_name] = path
    if stage.key == "paper_plan":
        target = pipeline.paper_budget_target(root)
        if target is not None:
            fields["planned_body_pages"] = target
    if stage.key == "paper_final":
        extras = finish.extras or {}
        fields["final_body_pages"] = int(extras["final_body_pages"])
        fields["compile_passes"] = int(extras["compile_passes"])
        fields.setdefault("draft_mode", "none")
    if stage.key == "verification" and (finish.extras or {}).get("conditional"):
        manifest.set_stage_fields(stage.key, fields)
        manifest.add_change_log(
            by="MAgent/engine",
            action=f"{stage.key} conditional（{finish.summary[:120]}）",
            affected_ids=[a["id"] for a in registered],
        )
        manifest.set_status(stage.key, "conditional")
        manifest.save()
        return registered

    manifest.set_stage_fields(stage.key, fields)
    manifest.add_change_log(
        by="MAgent/engine",
        action=f"{stage.key} complete：{finish.summary[:160]}",
        affected_ids=[a["id"] for a in registered],
    )
    manifest.set_status(stage.key, "complete")
    manifest.save()
    return registered


def run_stage(
    root: Path,
    stage_key: str,
    cfg: dict,
    log=_noop_log,
    llm: LLMClient | None = None,
) -> StageResult:
    """运行一个阶段：agent 会话 + 门禁重试回路 + 记账。

    paused 状态保留 in_progress，用户点"继续修复"时以续跑模式再次进入。
    """
    root = Path(root)
    stage = pipeline.STAGES[stage_key]
    skills_root, _source = resolve_skills_root(cfg)
    limits = cfg.get("limits", {})
    read_script = skills_root / READ_SCRIPT_REL
    if not read_script.is_file():
        return StageResult(stage_key, "error", f"找不到共享读取协议脚本：{read_script}")

    manifest = ManifestStore(root)
    try:
        manifest.load()
    except ManifestError as exc:
        return StageResult(stage_key, "error", str(exc))

    current = manifest.stage_status(stage_key)
    if current == "complete":
        return StageResult(stage_key, "already_complete", "该阶段已完成")
    if current == "n_a":
        return StageResult(stage_key, "already_complete", "该阶段已标记不适用")

    # 上游检查（n_a 视为满足）
    blocked = [
        u for u in stage.upstream
        if manifest.stage_status(u) not in ("complete", "n_a")
    ]
    if blocked:
        return StageResult(stage_key, "upstream_blocked", f"上游阶段未完成：{', '.join(blocked)}")

    if current != "in_progress":
        manifest.set_status(stage_key, "in_progress")
        manifest.save()

    prefs: dict = {}
    prefs_path = root / ".magent" / "prefs.json"
    if prefs_path.is_file():
        try:
            prefs = json.loads(prefs_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            prefs = {}
    problem_files = _list_problem_files(root)

    llm = llm or LLMClient(cfg["provider"])
    toolbox = ToolBox(root, run_timeout_sec=limits.get("run_timeout_sec", 600), log=log)
    extra_rules = ""
    if stage.key == "paper_final":
        extra_rules = (
            "- finish.extras 必须包含：final_body_pages（最终正文页数，25~30 整数）、"
            "compile_passes（编译遍数，≥2 整数）。写作轮与独立审查轮都要完成后才能 finish。"
        )
    if stage.key == "verification":
        extra_rules = "- 若验收结论为有条件通过，finish.extras 传 {\"conditional\": true} 并在 summary 说明范围。"

    system_prompt = pipeline.build_system_prompt(
        stage, skills_root, root, sys.executable, extra_rules
    )
    resumed = current == "in_progress"
    first_prompt = pipeline.build_user_prompt(
        stage, root, prefs, problem_files, resumed=resumed
    )

    session = AgentSession(
        llm=llm,
        toolbox=toolbox,
        system_prompt=system_prompt,
        transcript_path=root / ".magent" / "sessions" / f"{stage_key}-{time.strftime('%Y%m%d-%H%M%S')}.jsonl",
        log=log,
        max_turns=int(limits.get("max_turns", 150)),
    )

    retry_rounds = int(limits.get("retry_rounds", 3))
    prompt = first_prompt
    for round_no in range(retry_rounds + 1):
        try:
            outcome = session.run(prompt)
        except FinishSignal as signal:
            outcome = signal
        except ContextLimitError as exc:
            log({"type": "error", "msg": str(exc)})
            return StageResult(stage_key, "context_limit", str(exc))
        except LLMError as exc:
            log({"type": "error", "msg": str(exc)})
            return StageResult(stage_key, "paused", f"模型调用失败：{exc}")

        if not isinstance(outcome, FinishSignal):
            detail = {  # type: ignore[comparison-overlap]
                "max_turns": f"已达最大轮数 {limits.get('max_turns', 150)}，请点「继续修复」续跑",
                "no_progress": "模型连续无工具调用，请点「继续修复」续跑",
            }.get(outcome, f"会话中止：{outcome}")
            log({"type": "stage", "msg": f"{stage.title}：{detail}"})
            return StageResult(stage_key, "paused", detail)

        log({"type": "stage", "msg": f"收到 finish（第 {round_no + 1} 轮）：{outcome.summary[:200]}"})
        selected_models = _selected_model_names(manifest)
        report = run_stage_gates(
            stage, root, skills_root, sys.executable,
            finish=outcome,
            selected_models=selected_models,
        )
        for item in report.items:
            log(item.as_event())

        if report.all_ok:
            try:
                registered = _finalize(root, stage, manifest, outcome, skills_root)
            except ManifestError as exc:
                log({"type": "error", "msg": f"记账失败：{exc}"})
                prompt = f"finish 后的引擎记账失败：{exc}\n请核对产物文件后重新 finish。"
                continue
            log({
                "type": "stage",
                "msg": f"✅ {stage.title} 完成：登记 {len(registered)} 个产物，状态已置 complete",
            })
            return StageResult(stage_key, "complete", outcome.summary)

        log({"type": "stage", "msg": f"❌ 门禁驳回（第 {round_no + 1} 轮），错误已回喂模型"})
        prompt = (
            "你的 finish 被引擎门禁驳回。以下是失败项与输出原文，请修复后重新调用 finish"
            "（不要跳过或绕过机检）：\n\n" + report.failure_text()
        )

    log({"type": "stage", "msg": f"{stage.title}：{retry_rounds} 轮门禁未通过，挂起等人工处理"})
    return StageResult(stage_key, "paused", f"{retry_rounds} 轮门禁未通过，需人工介入")
