"""服务层接口：项目创建（multipart）、重复目录、元信息。"""

import io
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import pytest
from fastapi.testclient import TestClient

from magent import config as config_mod
from magent.server import app


@pytest.fixture
def client(tmp_path, monkeypatch):
    """隔离配置目录，避免写真实 ~/.magent/config.json。"""
    monkeypatch.setattr(config_mod, "CONFIG_DIR", tmp_path / "cfg")
    monkeypatch.setattr(config_mod, "CONFIG_PATH", tmp_path / "cfg" / "config.json")
    return TestClient(app)


def test_create_project_via_multipart(client, tmp_path):
    """前端以 multipart/form-data 提交（含题面文件）——曾因用 JSON 模型解析而 422。"""
    root = tmp_path / "proj-a"
    resp = client.post(
        "/api/project",
        data={"root": str(root), "title": "2026 A 题", "focus": "精度优先", "subproblems": "3"},
        files={"files": ("题目.txt", io.BytesIO("题面内容".encode()), "text/plain")},
    )
    assert resp.status_code == 200, resp.text
    payload = resp.json()
    assert payload["project_title"] == "2026 A 题"
    assert len(payload["stages"]) == 8
    # 骨架与题面落盘
    assert (root / "project-manifest.json").is_file()
    assert (root / "data" / "题目.txt").read_text(encoding="utf-8") == "题面内容"


def test_create_project_without_files(client, tmp_path):
    root = tmp_path / "proj-b"
    resp = client.post("/api/project", data={"root": str(root), "title": "无附件"})
    assert resp.status_code == 200
    assert (root / "plan.md").is_file()


def test_duplicate_project_dir_gives_409(client, tmp_path):
    root = tmp_path / "proj-c"
    assert client.post("/api/project", data={"root": str(root), "title": "T"}).status_code == 200
    again = client.post("/api/project", data={"root": str(root), "title": "T2"})
    assert again.status_code == 409
    assert "打开已有项目" in again.json()["detail"]


def test_meta_lists_stages_and_recent(client, tmp_path):
    root = tmp_path / "proj-d"
    client.post("/api/project", data={"root": str(root), "title": "T"})
    meta = client.get("/api/meta").json()
    assert len(meta["stages"]) == 8
    assert meta["stages"][0]["key"] == "analysis"
    assert str(root) in meta["recent_projects"]


def test_recent_endpoint_records_open(client, tmp_path):
    root = tmp_path / "proj-e"
    client.post("/api/project", data={"root": str(root), "title": "T"})
    resp = client.post("/api/recent", json={"root": str(root)})
    assert resp.status_code == 200
    assert resp.json()["recent_projects"][0] == str(root)


def test_config_save_preserves_masked_key(client, tmp_path):
    """前端回传掩码 key 时必须保留原真实值（不能把 ***xxxx 存成 key）。"""
    r = client.post("/api/config", json={
        "providers": [{"id": "deepseek", "name": "DeepSeek", "base_url": "https://api.deepseek.com",
                       "api_key": "sk-real-9999", "model": "deepseek-chat"}],
        "routing": {"default": "deepseek"},
    })
    assert r.status_code == 200
    masked = client.get("/api/config").json()
    assert masked["providers"][0]["api_key"] == "***9999"
    # 回传掩码值
    client.post("/api/config", json={"providers": masked["providers"], "routing": masked["routing"]})
    assert config_mod.load()["providers"][0]["api_key"] == "sk-real-9999"
