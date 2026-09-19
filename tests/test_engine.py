"""引擎集成测试：FakeLLM + 假阶段定义，验证门禁驳回/回喂/记账闭环。"""

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))

from fake_llm import FakeLLM
from magent.engine import run_stage
from magent.manifest import ManifestStore
from magent.pipeline import StageDef

EVENTS: list[dict] = []


@pytest.fixture
def project(tmp_path):
    """一个带最小 manifest 与机检脚手架的假项目。"""
    root = tmp_path / "proj"
    root.mkdir()
    store = ManifestStore(root)
    store.init_minimal("引擎测试题")

    # 假 skills_root：含 SKILL.md（build_system_prompt 需要读取）
    skills = tmp_path / "skills" / "mm-fake"
    skills.mkdir(parents=True)
    (skills / "SKILL.md").write_text("# 假阶段 skill", encoding="utf-8")
    # 假机检脚本：存在 flag 文件时 PASS，否则 FAIL
    scripts = skills / "scripts"
    scripts.mkdir()
    (scripts / "check_fake.py").write_text(
        "import sys; sys.exit(0 if __import__('pathlib').Path('flag.txt').exists() else 1)",
        encoding="utf-8",
    )
    # 门禁引擎永远会追加 validate_manifest：给一个 exit 0 的桩；
    # 引擎启动前还会检查 read_complete.py 存在性：同样给桩（read_gate=False 用不到其逻辑）
    orch = tmp_path / "skills" / "mm-orchestrator" / "scripts"
    orch.mkdir(parents=True)
    (orch / "validate_manifest.py").write_text("print('manifest validation: PASS (stub)')", encoding="utf-8")
    (orch / "read_complete.py").write_text("# stub", encoding="utf-8")
    (tmp_path / "cfg_limits.json").write_text("{}", encoding="utf-8")
    return root, tmp_path / "skills"


def _fake_stage() -> StageDef:
    return StageDef(
        key="analysis", title="假阶段", skill="mm-fake",
        upstream=[], read_gate=False,
        exists=["docs/01-report.md"],
        acceptance=[{"name": "check_fake", "cmd": ["{PY}", "{SK}/mm-fake/scripts/check_fake.py"]}],
    )


@pytest.fixture
def patched_stage(monkeypatch):
    import magent.engine as engine
    stage = _fake_stage()
    monkeypatch.setattr(engine.pipeline, "STAGES", {"analysis": stage})
    monkeypatch.setattr(engine.pipeline, "ORDER", ["analysis"])
    return stage


def test_gate_reject_then_pass_loop(project, patched_stage):
    """第一轮 finish 被门禁驳回（flag 不存在）→ 引擎回喂 → 第二轮写 flag 后过门禁 → complete。"""
    root, skills = project
    events: list[dict] = []
    llm = FakeLLM([
        FakeLLM.turn(
            FakeLLM.tool_call(1, "write_file", {"path": "docs/01-report.md", "content": "# 报告 v1"}),
            FakeLLM.tool_call(2, "finish", {
                "artifacts": ["docs/01-report.md"], "summary": "第一次提交（flag 未写，机检应失败）"})),
        FakeLLM.turn(
            FakeLLM.tool_call(3, "write_file", {"path": "flag.txt", "content": "ok"}),
            FakeLLM.tool_call(4, "finish", {
                "artifacts": ["docs/01-report.md"], "summary": "第二次提交"})),
    ])
    cfg = {
        "skills_root": str(skills),
        "limits": {"max_turns": 20, "retry_rounds": 3, "run_timeout_sec": 60},
        "provider": {"base_url": "http://x", "api_key": "k", "model": "m", "temperature": None},
    }
    result = run_stage(root, "analysis", cfg, log=events.append, llm=llm)

    assert result.outcome == "complete", result.detail
    store = ManifestStore(root)
    store.load()
    assert store.stage_status("analysis") == "complete"
    arts = store.data["artifacts"]
    assert len(arts) == 1 and arts[0]["path"] == "docs/01-report.md"
    assert any("analysis complete" in c["action"] for c in store.data["change_log"])
    # 第一轮确实被驳回过：引擎发回了包含失败门禁的反馈
    second_user = llm.calls[1][0]["content"] if llm.calls[1][0]["role"] == "user" else None
    gate_feedback = [m for m in llm.calls[1] if m["role"] == "user" and "驳回" in m.get("content", "")]
    assert gate_feedback, "引擎应把门禁失败原文回喂给模型"
    assert "check_fake" in gate_feedback[0]["content"]


def test_max_turns_pauses(project, patched_stage):
    root, skills = project
    # 模型一直不 finish：用无限空闲文本（FakeLLM 脚本耗尽会抛错，故给有限轮）
    script = [FakeLLM.turn(content="我再想想…")] * 6
    llm = FakeLLM(script)
    cfg = {
        "skills_root": str(skills),
        "limits": {"max_turns": 4, "retry_rounds": 1, "run_timeout_sec": 60},
        "provider": {"base_url": "http://x", "api_key": "k", "model": "m", "temperature": None},
    }
    result = run_stage(root, "analysis", cfg, log=EVENTS.append, llm=llm)
    assert result.outcome == "paused"
    store = ManifestStore(root)
    store.load()
    assert store.stage_status("analysis") == "in_progress"  # 挂起而非失败


def test_upstream_blocked(project, patched_stage):
    root, skills = project
    store = ManifestStore(root)
    store.load()
    patched = patched_stage
    patched.upstream = ["modeling"]  # analysis 的上游是未完成的 modeling
    cfg = {
        "skills_root": str(skills),
        "limits": {"max_turns": 5, "retry_rounds": 1, "run_timeout_sec": 60},
        "provider": {"base_url": "http://x", "api_key": "k", "model": "m", "temperature": None},
    }
    result = run_stage(root, "analysis", cfg, log=EVENTS.append, llm=FakeLLM([]))
    assert result.outcome == "upstream_blocked", f"detail={result.detail!r} events={EVENTS!r}"
    assert store.stage_status("analysis") == "pending"
