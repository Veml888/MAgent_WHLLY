"""工具集安全：路径越界、manifest 禁读写、data/ 只读、run_command。"""

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))

from magent.tools import FinishSignal, ToolBox, ToolError


@pytest.fixture
def box(tmp_path):
    root = tmp_path / "proj"
    (root / "data").mkdir(parents=True)
    (root / "data" / "题目.txt").write_text("题面内容", encoding="utf-8")
    return ToolBox(root)


def test_escape_denied(box):
    with pytest.raises(ToolError):
        box.resolve_in_root("../outside.txt")
    with pytest.raises(ToolError):
        box.resolve_in_root("C:\\Windows\\system32\\cmd.exe")


def test_manifest_read_and_write_denied(box):
    # 拒绝以错误结果形式返回给模型（模型能看见原因，但写不进去）
    read_result = json.loads(box.execute("read_file", json.dumps({"path": "project-manifest.json"})))
    assert "error" in read_result and "独占记账" in read_result["error"]
    write_result = json.loads(box.execute("write_file", json.dumps({"path": "project-manifest.json", "content": "x"})))
    assert "error" in write_result


def test_magent_dir_denied(box):
    result = json.loads(box.execute("write_file", json.dumps({"path": ".magent/evil.json", "content": "x"})))
    assert "error" in result


def test_data_readonly(box):
    result = json.loads(box.execute("write_file", json.dumps({"path": "data/new.txt", "content": "x"})))
    assert "error" in result
    # 读正常
    out = box.execute("read_file", json.dumps({"path": "data/题目.txt"}))
    assert "题面内容" in out


def test_write_and_read_roundtrip(box):
    box.execute("write_file", json.dumps({"path": "docs/a.md", "content": "第一行\n第二行"}))
    out = box.execute("read_file", json.dumps({"path": "docs/a.md", "start_line": 2}))
    assert "第二行" in out and "第一行" not in out


def test_run_command(box):
    out = json.loads(box.execute(
        "run_command",
        json.dumps({"command": [sys.executable, "-c", "print('ok')"]}),
    ))
    assert out["exit_code"] == 0 and "ok" in out["stdout"]


def test_run_command_timeout(box):
    out = json.loads(box.execute(
        "run_command",
        json.dumps({"command": [sys.executable, "-c", "import time; time.sleep(10)"], "timeout_sec": 1}),
    ))
    assert out["exit_code"] == -1 and "TIMEOUT" in out["stderr"]


def test_finish_signal(box, tmp_path):
    (tmp_path / "proj" / "out.md").write_text("x", encoding="utf-8")
    with pytest.raises(FinishSignal) as exc_info:
        box.execute("finish", json.dumps({"artifacts": ["out.md"], "summary": "done"}))
    assert exc_info.value.summary == "done"
    # 不存在的产物在 finish 处即报错
    result = json.loads(box.execute("finish", json.dumps({"artifacts": ["nope.md"], "summary": ""})))
    assert "error" in result


import sys  # noqa: E402
