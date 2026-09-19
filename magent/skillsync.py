"""把外部 MM_workflow/skills 同步进软件内置副本（magent/skills/）。

用法：python -m magent sync-skills [--from D:\\AAA-MMW\\MM_workflow\\skills]
用途：内容层（SKILL.md / references / 机检脚本）在仓库里更新后，一条命令刷新内置副本。
"""

from __future__ import annotations

import shutil
from pathlib import Path

from .config import EMBEDDED_SKILLS

IGNORE = shutil.ignore_patterns("__pycache__", "*.pyc", ".git", ".mypy_cache")


def sync_from(src: Path, log=print) -> dict:
    """用 src 整体替换内置副本。返回 {files, bytes} 摘要。"""
    src = Path(src).resolve()
    marker = src / "mm-orchestrator" / "scripts" / "read_complete.py"
    if not marker.is_file():
        raise FileNotFoundError(f"源目录不像 MM_workflow/skills（找不到 {marker}）")

    if EMBEDDED_SKILLS.exists():
        shutil.rmtree(EMBEDDED_SKILLS)
    shutil.copytree(src, EMBEDDED_SKILLS, ignore=IGNORE)

    files = [p for p in EMBEDDED_SKILLS.rglob("*") if p.is_file()]
    total = sum(p.stat().st_size for p in files)
    log(f"已同步内置 skills：{len(files)} 个文件，共 {total / 1024 / 1024:.1f} MB ← {src}")
    return {"files": len(files), "bytes": total, "source": str(src)}
