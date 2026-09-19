"""manifest 记账层：状态机、产物哈希、篡改覆盖。"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import pytest

from magent.manifest import ManifestError, ManifestStore


@pytest.fixture
def store(tmp_path):
    s = ManifestStore(tmp_path)
    s.init_minimal("测试题")
    return s


def test_legal_transitions(store):
    store.set_status("analysis", "in_progress")
    store.set_status("analysis", "complete")
    assert store.stage_status("analysis") == "complete"


def test_illegal_transition_rejected(store):
    with pytest.raises(ManifestError):
        store.set_status("analysis", "complete")  # pending -> complete 非法
    with pytest.raises(ManifestError):
        store.set_status("paper_final", "complete")  # pending -> complete 非法


def test_verification_allows_conditional(store):
    store.set_status("verification", "in_progress")
    store.set_status("verification", "conditional")
    assert store.stage_status("verification") == "conditional"


def test_register_artifacts_sha_and_version(store, tmp_path):
    f = tmp_path / "docs"
    f.mkdir()
    (f / "01-analysis-report.md").write_text("hello", encoding="utf-8")
    entries = store.register_artifacts(["docs/01-analysis-report.md"], "analysis")
    assert entries[0]["version"] == "v1"
    assert entries[0]["sha256"] == __import__("hashlib").sha256(b"hello").hexdigest().upper()

    (f / "01-analysis-report.md").write_text("hello v2", encoding="utf-8")
    entries = store.register_artifacts(["docs/01-analysis-report.md"], "analysis")
    assert entries[0]["version"] == "v2"
    assert len(store.data["artifacts"]) == 1  # 不重复登记，而是版本升级


def test_register_missing_artifact_rejected(store):
    with pytest.raises(ManifestError):
        store.register_artifacts(["docs/不存在.md"], "analysis")


def test_tamper_overwritten_by_save(store, tmp_path):
    """防线二：模型绕道改磁盘 manifest，引擎 save() 用内存权威状态覆盖。"""
    store.set_status("analysis", "in_progress")
    store.save()
    disk = json.loads((tmp_path / "project-manifest.json").read_text(encoding="utf-8"))
    disk["stages"]["analysis"]["status"] = "complete"  # 模型篡改
    (tmp_path / "project-manifest.json").write_text(json.dumps(disk), encoding="utf-8")

    store.set_status("analysis", "complete")
    store.save()
    reloaded = ManifestStore(tmp_path)
    reloaded.load()
    assert reloaded.stage_status("analysis") == "complete"


def test_change_log_appended(store):
    store.add_change_log("MAgent/engine", "analysis complete")
    assert store.data["change_log"][-1]["by"] == "MAgent/engine"
