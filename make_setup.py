"""构建 MAgent 安装包（dist/MAgent-Setup-v<版本>.exe）。

用 Inno Setup 把单文件版 MAgent.exe 包成标准安装程序：
向导全中文 → 默认装到用户目录（不需要管理员权限）→ 自动建桌面/开始菜单
快捷方式 → 控制面板可卸载。

前置：已安装 Inno Setup 6（winget install JRSoftware.InnoSetup），
以及 dist/MAgent.exe（先运行 python make_exe.py）。

用法：python make_setup.py
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
ISCC_CANDIDATES = [
    Path.home() / "AppData" / "Local" / "Programs" / "Inno Setup 6" / "iscc.exe",
    Path(r"C:\Program Files (x86)\Inno Setup 6\iscc.exe"),
    Path(r"C:\Program Files\Inno Setup 6\iscc.exe"),
]

ISS_TEMPLATE = """[Setup]
AppId={{{{7B2F4A31-9C2D-4E86-A5D9-2F4B6D9E1C77}}}}
AppName=MAgent
AppVersion={version}
AppPublisher=Veml888
AppPublisherURL=https://github.com/Veml888/MAgent_WHLLY
DefaultDirName={{autopf}}\\MAgent
DisableProgramGroupPage=yes
LicenseFile={root}\\LICENSE
OutputDir={root}\\dist
OutputBaseFilename=MAgent-Setup-v{version}
SetupIconFile={root}\\assets\\magent.ico
UninstallDisplayIcon={{app}}\\MAgent.exe
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
PrivilegesRequired=lowest
CloseApplications=yes

[Languages]
Name: "chinesesimplified"; MessagesFile: "{isldir}\\ChineseSimplified.isl"
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "创建桌面快捷方式"; GroupDescription: "附加任务："

[Files]
Source: "{root}\\dist\\MAgent.exe"; DestDir: "{{app}}"; Flags: ignoreversion
Source: "{root}\\dist\\MAgent\\使用说明.txt"; DestDir: "{{app}}"; Flags: ignoreversion

[Icons]
Name: "{{autoprograms}}\\MAgent"; Filename: "{{app}}\\MAgent.exe"
Name: "{{autodesktop}}\\MAgent"; Filename: "{{app}}\\MAgent.exe"; Tasks: desktopicon

[Run]
Filename: "{{app}}\\MAgent.exe"; Description: "立即运行 MAgent"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
Type: filesandordirs; Name: "{{app}}"
"""


def find_iscc() -> Path:
    for cand in ISCC_CANDIDATES:
        if cand.is_file():
            return cand
    raise FileNotFoundError("找不到 iscc.exe，请先安装 Inno Setup 6：winget install JRSoftware.InnoSetup")


def get_version() -> str:
    text = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    for line in text.splitlines():
        if line.startswith("version"):
            return line.split("=", 1)[1].strip().strip('"')
    return "0.1.0"


def main() -> int:
    if not (ROOT / "dist" / "MAgent.exe").is_file():
        print("缺少 dist/MAgent.exe，请先运行 python make_exe.py")
        return 1
    iscc = find_iscc()
    isldir = iscc.parent / "Languages"
    iss_text = ISS_TEMPLATE.format(root=ROOT, version=get_version(), isldir=isldir)
    iss_path = ROOT / "build" / "installer.iss"
    iss_path.parent.mkdir(exist_ok=True)
    iss_path.write_text(iss_text, encoding="utf-8")

    print("编译安装包…")
    proc = subprocess.run([str(iscc), str(iss_path)], cwd=ROOT, text=True,
                          encoding="utf-8", errors="replace", capture_output=True)
    tail = (proc.stdout or proc.stderr).strip().splitlines()[-3:]
    print("\n".join(tail))
    if proc.returncode != 0:
        return proc.returncode
    out = ROOT / "dist" / f"MAgent-Setup-v{get_version()}.exe"
    print(f"✅ 安装包：{out}（{out.stat().st_size / 1048576:.0f} MB）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
