"""MAgent 全局配置（~/.magent/config.json）。"""

from __future__ import annotations

import json
from pathlib import Path

CONFIG_DIR = Path.home() / ".magent"
CONFIG_PATH = CONFIG_DIR / "config.json"

# 软件内置的 skills 副本（发布包自带；用 `python -m magent sync-skills` 从仓库刷新）
EMBEDDED_SKILLS = Path(__file__).resolve().parent / "skills"
# 历史版本曾把外部仓库路径写进默认配置；加载时自动迁移为"内置"
LEGACY_SKILLS_DEFAULT = r"D:\AAA-MMW\MM_workflow\skills"

DEFAULTS: dict = {
    "schema_version": "1.0",
    # skills 根目录：留空 = 使用软件内置副本；也可指向外部 MM_workflow/skills
    "skills_root": "",
    "provider": {
        "name": "deepseek",
        "base_url": "https://api.deepseek.com",
        "api_key": "",
        "model": "deepseek-chat",
        "temperature": None,
    },
    "limits": {
        "max_turns": 150,       # 每阶段会话最大工具调用轮数
        "retry_rounds": 3,      # finish 被门禁拒绝后的最大重试轮数
        "run_timeout_sec": 600,  # run_command 默认超时
    },
    "recent_projects": [],
}


def _merge(base: dict, override: dict) -> dict:
    out = dict(base)
    for key, value in (override or {}).items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _merge(out[key], value)
        else:
            out[key] = value
    return out


def load() -> dict:
    if CONFIG_PATH.is_file():
        try:
            data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            data = {}
        cfg = _merge(DEFAULTS, data)
    else:
        cfg = json.loads(json.dumps(DEFAULTS, ensure_ascii=False))
    # 迁移：旧默认值（外部仓库路径）改为"使用内置副本"
    if cfg.get("skills_root") == LEGACY_SKILLS_DEFAULT:
        cfg["skills_root"] = ""
    return cfg


def resolve_skills_root(cfg: dict) -> tuple[Path, str]:
    """返回 (skills 根目录, 来源标签)。显式配置优先，其次内置副本。"""
    raw = str(cfg.get("skills_root") or "").strip()
    if raw and Path(raw).is_dir():
        return Path(raw).resolve(), "外部目录"
    if EMBEDDED_SKILLS.is_dir():
        return EMBEDDED_SKILLS, "软件内置"
    raise FileNotFoundError(
        "skills 目录缺失：配置指向的路径不存在，且软件内置副本缺失。"
        "请在设置页填写 MM_workflow 的 skills 路径，或运行 python -m magent sync-skills"
    )


def save(cfg: dict) -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text(
        json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def ensure() -> dict:
    """加载配置，首次使用时落盘一份默认值。"""
    cfg = load()
    if not CONFIG_PATH.is_file():
        save(cfg)
    return cfg


def add_recent_project(cfg: dict, root: str) -> None:
    root = str(Path(root).resolve())
    recent = [r for r in cfg.get("recent_projects", []) if r != root]
    recent.insert(0, root)
    cfg["recent_projects"] = recent[:10]


def masked(cfg: dict) -> dict:
    """返回给前端的安全副本：api_key 只保留末 4 位。"""
    out = json.loads(json.dumps(cfg, ensure_ascii=False))
    key = out.get("provider", {}).get("api_key", "")
    if key:
        out["provider"]["api_key"] = "***" + key[-4:] if len(key) > 4 else "***"
    return out
