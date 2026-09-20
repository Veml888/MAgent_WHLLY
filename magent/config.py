"""MAgent 全局配置（~/.magent/config.json）。

schema 2.0：多模型服务（providers）+ 按阶段路由（routing）。
旧版（1.0 的单 provider）配置在加载时自动迁移。
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path

CONFIG_DIR = Path.home() / ".magent"
CONFIG_PATH = CONFIG_DIR / "config.json"

# 软件内置的 skills 副本（发布包自带；用 `python -m magent sync-skills` 从仓库刷新）
EMBEDDED_SKILLS = Path(__file__).resolve().parent / "skills"
# 历史版本曾把外部仓库路径写进默认配置；加载时自动迁移为"内置"
LEGACY_SKILLS_DEFAULT = r"D:\AAA-MMW\MM_workflow\skills"

DEFAULT_PROVIDER: dict = {
    "id": "deepseek",
    "name": "DeepSeek",
    "base_url": "https://api.deepseek.com",
    "api_key": "",
    "model": "deepseek-chat",
    "temperature": None,
}

DEFAULTS: dict = {
    "schema_version": "2.0",
    # skills 根目录：留空 = 使用软件内置副本；也可指向外部 MM_workflow/skills
    "skills_root": "",
    "providers": [dict(DEFAULT_PROVIDER)],
    # routing[stage_key] = provider_id；未配置的阶段用 routing["default"]
    "routing": {"default": "deepseek"},
    "limits": {
        "max_turns": 150,        # 每阶段会话最大工具调用轮数
        "retry_rounds": 3,       # finish 被门禁拒绝后的最大重试轮数
        "run_timeout_sec": 600,  # run_command 默认超时
    },
    "recent_projects": [],
    # 侧边栏「工作区」列表：可同时打开多个项目（各自独立运行）
    "workspaces": [],
}


def _merge(base: dict, override: dict) -> dict:
    out = dict(base)
    for key, value in (override or {}).items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _merge(out[key], value)
        else:
            out[key] = value
    return out


def new_provider_id() -> str:
    return "svc-" + uuid.uuid4().hex[:8]


def _migrate(data: dict) -> dict:
    """把历史配置升级到 schema 2.0（单 provider → providers 列表 + 路由）。"""
    # 1.x：provider 是单个字典，且 routing 可能不存在
    if "providers" not in data:
        old = data.pop("provider", None)
        if isinstance(old, dict) and (old.get("base_url") or old.get("model")):
            pid = str(old.get("name") or "default").strip().lower().replace(" ", "-") or "default"
            data["providers"] = [
                {
                    "id": pid,
                    "name": old.get("name") or "默认服务",
                    "base_url": old.get("base_url", ""),
                    "api_key": old.get("api_key", ""),
                    "model": old.get("model", ""),
                    "temperature": old.get("temperature"),
                }
            ]
            routing = data.get("routing") if isinstance(data.get("routing"), dict) else {}
            routing.setdefault("default", pid)
            data["routing"] = routing
        else:
            data["providers"] = [dict(DEFAULT_PROVIDER)]
    data["schema_version"] = "2.0"
    return data


def load() -> dict:
    data: dict = {}
    if CONFIG_PATH.is_file():
        try:
            data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            data = {}
    data = _migrate(data)
    cfg = _merge(DEFAULTS, data)
    # 迁移：旧默认值（外部仓库路径）改为"使用内置副本"
    if cfg.get("skills_root") == LEGACY_SKILLS_DEFAULT:
        cfg["skills_root"] = ""
    # 至少要有一个模型服务，否则界面无处可配
    if not [p for p in cfg.get("providers", []) if isinstance(p, dict)]:
        cfg["providers"] = [dict(DEFAULT_PROVIDER)]
    ids = {p["id"] for p in cfg["providers"] if p.get("id")}
    routing = cfg.get("routing") if isinstance(cfg.get("routing"), dict) else {}
    if routing.get("default") not in ids:
        routing["default"] = cfg["providers"][0]["id"]
    cfg["routing"] = routing
    return cfg


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


# ---------- 模型服务与阶段路由 ----------

def get_provider(cfg: dict, provider_id: str | None) -> dict | None:
    if not provider_id:
        return None
    for prov in cfg.get("providers", []):
        if isinstance(prov, dict) and prov.get("id") == provider_id:
            return prov
    return None


def provider_for_stage(cfg: dict, stage_key: str) -> dict:
    """阶段 → 模型服务：优先阶段专属路由，其次默认路由，最后第一个服务。"""
    routing = cfg.get("routing") if isinstance(cfg.get("routing"), dict) else {}
    prov = get_provider(cfg, routing.get(stage_key)) or get_provider(cfg, routing.get("default"))
    if prov is None:
        providers = [p for p in cfg.get("providers", []) if isinstance(p, dict)]
        if not providers:
            raise RuntimeError("尚未配置任何模型服务，请到设置页添加")
        prov = providers[0]
    return prov


def add_recent_project(cfg: dict, root: str) -> None:
    root = str(Path(root).resolve())
    recent = [r for r in cfg.get("recent_projects", []) if r != root]
    recent.insert(0, root)
    cfg["recent_projects"] = recent[:10]


def masked(cfg: dict) -> dict:
    """返回给前端的安全副本：所有 API Key 只保留末 4 位。"""
    out = json.loads(json.dumps(cfg, ensure_ascii=False))
    for prov in out.get("providers", []):
        key = prov.get("api_key", "")
        if key:
            prov["api_key"] = "***" + key[-4:] if len(key) > 4 else "***"
    return out
