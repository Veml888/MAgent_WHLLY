"""生成 MAgent.exe 启动器（原生小 exe，仅转发启动内嵌运行时）。

用 Windows 自带的 C# 编译器（csc.exe）编译 scripts/launcher.cs，
产物约 10KB，不打包 Python、不触发杀毒误报。

用法：python scripts/make_launcher.py
输出：dist/MAgent/MAgent.exe
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parents[1]
CSC_CANDIDATES = [
    Path(r"C:\Windows\Microsoft.NET\Framework64\v4.0.30319\csc.exe"),
    Path(r"C:\Windows\Microsoft.NET\Framework\v4.0.30319\csc.exe"),
]


def find_csc() -> Path:
    for path in CSC_CANDIDATES:
        if path.is_file():
            return path
    raise FileNotFoundError("找不到 Windows 自带的 C# 编译器 csc.exe")


def main() -> int:
    out_dir = ROOT / "dist" / "MAgent"
    if not (out_dir / "runtime" / "python.exe").is_file():
        print("缺少 dist/MAgent/runtime（请先运行 python scripts/build_portable.py）")
        return 1
    target = out_dir / "MAgent.exe"
    winforms = Path(r"C:\Windows\Microsoft.NET\Framework64\v4.0.30319\System.Windows.Forms.dll")
    cmd = [
        str(find_csc()),
        "/nologo",
        "/target:winexe",
        "/platform:anycpu",
        "/optimize+",
        f"/out:{target}",
        f"/win32icon:{ROOT / 'assets' / 'magent.ico'}",
        f"/reference:{winforms}",
        str(ROOT / "scripts" / "launcher.cs"),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if proc.returncode != 0:
        print("编译失败：\n" + (proc.stdout or "") + (proc.stderr or ""))
        return proc.returncode
    size_kb = target.stat().st_size / 1024
    print(f"✅ 启动器：{target}（{size_kb:.0f} KB）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
