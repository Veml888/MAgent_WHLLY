"""配置层：schema 2.0 多服务 + 阶段路由 + 旧配置迁移。"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import pytest

from magent import config as config_mod


@pytest.fixture
def cfg_dir(tmp_path, monkeypatch):
    """把配置目录指向临时目录，避免污染真实 ~/.magent。"""
    monkeypatch.setattr(config_mod, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(config_mod, "CONFIG_PATH", tmp_path / "config.json")
    return tmp_path


def test_defaults_when_no_file(cfg_dir):
    cfg = config_mod.load()
    assert cfg["schema_version"] == "2.0"
    assert cfg["providers"][0]["id"] == "deepseek"
    assert cfg["routing"]["default"] == "deepseek"


def test_migration_from_v1_single_provider(cfg_dir):
    (cfg_dir / "config.json").write_text(
        json.dumps({
            "schema_version": "1.0",
            "provider": {
                "name": "deepseek",
                "base_url": "https://api.deepseek.com",
                "api_key": "sk-old",
                "model": "deepseek-chat",
            },
            "limits": {"max_turns": 42},
        }),
        encoding="utf-8",
    )
    cfg = config_mod.load()
    assert cfg["schema_version"] == "2.0"
    assert "provider" not in cfg  # 旧字段被迁移
    assert len(cfg["providers"]) == 1
    prov = cfg["providers"][0]
    assert prov["id"] == "deepseek" and prov["api_key"] == "sk-old"
    assert cfg["routing"]["default"] == "deepseek"
    assert cfg["limits"]["max_turns"] == 42  # 其他字段保留


def test_provider_for_stage_routing(cfg_dir):
    cfg = config_mod.load()
    cfg["providers"] = [
        {"id": "a", "name": "A", "base_url": "http://a", "api_key": "ka", "model": "ma"},
        {"id": "b", "name": "B", "base_url": "http://b", "api_key": "kb", "model": "mb"},
    ]
    cfg["routing"] = {"default": "a", "paper_final": "b"}
    assert config_mod.provider_for_stage(cfg, "analysis")["id"] == "a"
    assert config_mod.provider_for_stage(cfg, "paper_final")["id"] == "b"


def test_provider_for_stage_falls_back_when_route_missing(cfg_dir):
    cfg = config_mod.load()
    cfg["providers"] = [{"id": "only", "name": "Only", "base_url": "http://x", "api_key": "k", "model": "m"}]
    cfg["routing"] = {"default": "only", "coding": "deleted-service"}
    assert config_mod.provider_for_stage(cfg, "coding")["id"] == "only"


def test_masked_keys(cfg_dir):
    cfg = config_mod.load()
    cfg["providers"] = [
        {"id": "p", "name": "P", "base_url": "http://x", "api_key": "sk-1234567890", "model": "m"},
        {"id": "q", "name": "Q", "base_url": "http://y", "api_key": "", "model": "m2"},
    ]
    masked = config_mod.masked(cfg)
    assert masked["providers"][0]["api_key"] == "***7890"
    assert masked["providers"][1]["api_key"] == ""
    # 原配置不被污染
    assert cfg["providers"][0]["api_key"] == "sk-1234567890"


def test_save_then_load_roundtrip(cfg_dir):
    cfg = config_mod.load()
    cfg["providers"].append({"id": "glm", "name": "GLM", "base_url": "http://glm", "api_key": "k2", "model": "glm-4"})
    cfg["routing"]["coding"] = "glm"
    config_mod.save(cfg)
    again = config_mod.load()
    assert [p["id"] for p in again["providers"]] == ["deepseek", "glm"]
    assert again["routing"]["coding"] == "glm"
