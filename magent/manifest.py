"""project-manifest.json 的引擎独占记账层。

模型永远不直接写 manifest；引擎内存持有权威状态，每次更新整体重写磁盘
文件（防篡改覆盖），所有变更追加 change_log。
"""

from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path

STAGE_KEYS = [
    "analysis",
    "modeling",
    "coding",
    "paper_plan",
    "figures",
    "graphics",
    "paper_final",
    "verification",
]

# 合法状态迁移；verification 额外允许 conditional
_TRANSITIONS: dict[str, set[str]] = {
    "pending": {"in_progress", "n_a"},
    "in_progress": {"complete", "failed", "n_a", "conditional"},
    "failed": {"in_progress", "n_a"},
    "n_a": {"in_progress"},
    "conditional": {"in_progress"},
    "complete": set(),
}


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest().upper()


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def minimal_manifest(title: str = "未命名题目") -> dict:
    """schema 2.0 最小骨架（与仓库 init_project_skeleton 等价，供测试/兜底）。"""
    return {
        "schema_version": "2.0",
        "competition": "CUMCM",
        "project": {"title": title, "language": "Python", "created_at": _now()},
        "requirements": [],
        "problems": [],
        "datasets": [],
        "assumptions": [],
        "model_candidates": [],
        "selected_models": [],
        "implemented_models": [],
        "validation_plans": [],
        "symbols": [],
        "results": [],
        "figures": [],
        "artifacts": [],
        "paper_gates": [],
        "rework": [],
        "change_log": [],
        "stages": {
            "analysis": {"status": "pending"},
            "modeling": {"status": "pending"},
            "coding": {"status": "pending"},
            "figures": {"status": "pending"},
            "graphics": {"status": "pending"},
            "paper_plan": {"status": "pending"},
            "paper_final": {"status": "pending", "draft_mode": "none"},
            "verification": {"status": "pending"},
        },
    }


class ManifestError(RuntimeError):
    pass


class ManifestStore:
    def __init__(self, root: Path):
        self.root = Path(root)
        self.path = self.root / "project-manifest.json"
        self.data: dict = {}

    # ---------- 读写 ----------

    def load(self) -> dict:
        if not self.path.is_file():
            raise ManifestError(f"manifest 不存在：{self.path}")
        self.data = json.loads(self.path.read_text(encoding="utf-8"))
        return self.data

    def save(self) -> None:
        """用内存权威状态整体重写磁盘文件（防篡改），原子替换。"""
        tmp = self.path.with_suffix(".json.tmp")
        tmp.write_text(
            json.dumps(self.data, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        tmp.replace(self.path)

    def init_minimal(self, title: str) -> dict:
        self.data = minimal_manifest(title)
        self.save()
        return self.data

    # ---------- 阶段状态 ----------

    def get_stage(self, key: str) -> dict:
        try:
            return self.data["stages"][key]
        except KeyError as exc:
            raise ManifestError(f"manifest 缺少阶段字段：{key}") from exc

    def set_status(self, key: str, status: str, *, enforce: bool = True) -> None:
        stage = self.get_stage(key)
        current = stage.get("status", "pending")
        if enforce and status not in _TRANSITIONS.get(current, set()):
            raise ManifestError(
                f"非法状态迁移：{key} {current} -> {status}（合法：{sorted(_TRANSITIONS.get(current, set()))}）"
            )
        stage["status"] = status

    def set_stage_fields(self, key: str, fields: dict) -> None:
        self.get_stage(key).update(fields)

    # ---------- 产物登记 ----------

    def register_artifacts(self, rel_paths: list[str], producer_stage: str) -> list[dict]:
        """登记产物：计算 SHA256；同路径重复登记则版本号 +1。"""
        artifacts = self.data.setdefault("artifacts", [])
        registered = []
        for rel in rel_paths:
            path = (self.root / rel).resolve()
            if not path.is_file():
                raise ManifestError(f"产物不存在，无法登记：{rel}")
            digest = sha256_of(path)
            existing = next((a for a in artifacts if a.get("path") == rel), None)
            if existing is None:
                seq = len(artifacts) + 1
                entry = {
                    "id": f"ART-{seq:03d}",
                    "path": rel,
                    "sha256": digest,
                    "producer_stage": producer_stage,
                    "version": "v1",
                }
                artifacts.append(entry)
            else:
                old_version = int(str(existing.get("version", "v1")).lstrip("v") or 1)
                existing.update(
                    {
                        "sha256": digest,
                        "producer_stage": producer_stage,
                        "version": f"v{old_version + 1}",
                    }
                )
                entry = existing
            registered.append(entry)
        return registered

    def add_change_log(self, by: str, action: str, affected_ids: list[str] | None = None, version: str | None = None) -> None:
        self.data.setdefault("change_log", []).append(
            {
                "at": _now(),
                "by": by,
                "action": action,
                "affected_ids": affected_ids or [],
                "version": version or "v1",
            }
        )

    # ---------- 查询 ----------

    def stage_status(self, key: str) -> str:
        return self.get_stage(key).get("status", "pending")

    def summary(self) -> dict:
        return {
            "project_title": self.data.get("project", {}).get("title", ""),
            "stages": {k: self.stage_status(k) for k in STAGE_KEYS},
            "artifacts_count": len(self.data.get("artifacts", [])),
        }
