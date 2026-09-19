"""构建 MAgent 单文件 exe（dist/MAgent.exe）。

用 PyInstaller 把解释器 + 依赖 + magent 包（含内置 skills/界面）压进一个 exe。
双击即弹桌面窗口；崩溃信息写入 ~/.magent/crash.log。

用法：python scripts/make_exe.py
"""

from __future__ import annotations

import subprocess
import sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]  # scripts/ 的上一级 = 仓库根

EXTRA_COLLECT = [
    # pywebview 的 Windows 后端（动态导入 + 自带 DLL，必须整包收集）
    "--collect-all", "webview",
    "--collect-all", "clr_loader",
    "--collect-all", "pythonnet",
    # PDF 机检与题面抽取
    "--collect-all", "pymupdf",
    # openai SDK 的懒加载模型定义
    "--collect-all", "openai",
]


def main() -> int:
    cmd = [
        sys.executable, "-m", "PyInstaller",
        "--noconfirm", "--clean",
        "--onefile", "--noconsole",
        "--name", "MAgent",
        "--icon", str(ROOT / "assets" / "magent.ico"),
        "--distpath", str(ROOT / "dist"),
        "--workpath", str(ROOT / "build" / "exe"),
        "--specpath", str(ROOT / "build" / "exe"),
        "--collect-data", "magent",
        *EXTRA_COLLECT,
        str(ROOT / "scripts" / "exe_entry.py"),
    ]
    print("构建中（约 2~5 分钟）…")
    proc = subprocess.run(cmd, cwd=ROOT)
    if proc.returncode != 0:
        return proc.returncode
    exe = ROOT / "dist" / "MAgent.exe"
    print(f"✅ 单文件版：{exe}（{exe.stat().st_size / 1048576:.0f} MB）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
