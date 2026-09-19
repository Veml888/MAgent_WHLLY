"""门禁引擎单元测试：存在性检查、失败文本、graphics 动态审计跳过逻辑。"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from magent.gates import run_stage_gates
from magent.pipeline import StageDef


def _skills(tmp_path):
    skills = tmp_path / "skills"
    orch = skills / "mm-orchestrator" / "scripts"
    orch.mkdir(parents=True)
    (orch / "validate_manifest.py").write_text("print('PASS')", encoding="utf-8")
    return skills


def test_exists_gate_fail_and_pass(tmp_path):
    root = tmp_path / "proj"
    root.mkdir()
    skills = _skills(tmp_path)
    stage = StageDef(key="analysis", title="t", skill="mm-fake", read_gate=False,
                     exists=["docs/01-report.md"], acceptance=[])
    report = run_stage_gates(stage, root, skills, "python")
    assert not report.all_ok
    assert "docs/01-report.md" in report.failure_text()

    (root / "docs").mkdir()
    (root / "docs" / "01-report.md").write_text("# 报告", encoding="utf-8")
    report = run_stage_gates(stage, root, skills, "python")
    assert report.all_ok


def test_graphics_dynamic_no_files_skips(tmp_path):
    root = tmp_path / "proj2"
    (root / "figures").mkdir(parents=True)
    skills = _skills(tmp_path)
    stage = StageDef(key="graphics", title="t", skill="mm-fake", read_gate=False,
                     exists=[], acceptance=[], dynamic="graphics")
    report = run_stage_gates(stage, root, skills, "python")
    skipped = [i for i in report.items if i.name == "graphics_files"]
    assert skipped and skipped[0].skipped
    assert report.all_ok  # 只有 stub validate_manifest，PASS
