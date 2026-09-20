"""构建 MAgent 安装包（dist/MAgent-Setup-v<版本>.exe）。

用 Inno Setup 把「便携版整套」（内嵌 Python 运行时 + 全部依赖 + 内置 skills）
包成标准安装程序：向导中文 → 默认装到用户目录（免管理员）→ 自动建桌面/开始菜单
快捷方式 → 控制面板可卸载。

注意：安装的是便携版整套而非单文件 exe——单文件 exe 无法作为 Python 解释器
执行门禁脚本与模型解题代码，不具备完整能力。

前置：已运行 python scripts/build_portable.py，且已安装 Inno Setup 6。

用法：python scripts/make_setup.py
"""

from __future__ import annotations

import subprocess
import sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]  # scripts/ 的上一级 = 仓库根
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
UninstallDisplayIcon={{app}}\\magent.ico
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
PrivilegesRequired=lowest
CloseApplications=yes

{languages_section}

[Tasks]
Name: "desktopicon"; Description: "创建桌面快捷方式"; GroupDescription: "附加任务："

[Files]
; 安装「便携版整套」：内嵌 Python 运行时 + 全部依赖 + 内置 skills + 启动脚本。
; 不能用单文件 exe——它无法作为解释器执行门禁脚本与解题代码。
Source: "{root}\\dist\\MAgent\\*"; DestDir: "{{app}}"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "{root}\\assets\\magent.ico"; DestDir: "{{app}}"; Flags: ignoreversion

[Icons]
Name: "{{autoprograms}}\\MAgent"; Filename: "{{app}}\\runtime\\pythonw.exe"; Parameters: "-m magent serve"; WorkingDir: "{{app}}"; IconFilename: "{{app}}\\magent.ico"
Name: "{{autodesktop}}\\MAgent"; Filename: "{{app}}\\runtime\\pythonw.exe"; Parameters: "-m magent serve"; WorkingDir: "{{app}}"; IconFilename: "{{app}}\\magent.ico"; Tasks: desktopicon

[Run]
Filename: "{{app}}\\runtime\\pythonw.exe"; Parameters: "-m magent serve"; WorkingDir: "{{app}}"; Description: "立即运行 MAgent"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
Type: filesandordirs; Name: "{{app}}"
"""


def find_iscc() -> Path:
    for cand in ISCC_CANDIDATES:
        if cand.is_file():
            return cand
    raise FileNotFoundError("找不到 iscc.exe，请先安装 Inno Setup 6：winget install JRSoftware.InnoSetup")


ISL_URL = "https://raw.githubusercontent.com/jrsoftware/issrc/main/Files/Languages/ChineseSimplified.isl"


def ensure_chinese_isl(iscc: Path) -> bool:
    """确保中文向导语言包存在（CI 环境没有）；失败则回退英文向导。"""
    dst = iscc.parent / "Languages" / "ChineseSimplified.isl"
    if dst.is_file() and dst.stat().st_size > 20000:
        return True
    try:
        import urllib.request

        dst.parent.mkdir(exist_ok=True)
        with urllib.request.urlopen(ISL_URL, timeout=60) as resp, dst.open("wb") as fh:
            fh.write(resp.read())
        return dst.stat().st_size > 20000
    except Exception:
        return False


def get_version() -> str:
    text = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    for line in text.splitlines():
        if line.startswith("version"):
            return line.split("=", 1)[1].strip().strip('"')
    return "0.1.0"


def main() -> int:
    portable = ROOT / "dist" / "MAgent"
    if not (portable / "runtime" / "python.exe").is_file():
        print("缺少 dist/MAgent/（便携版整套），请先运行 python scripts/build_portable.py")
        return 1
    iscc = find_iscc()
    if ensure_chinese_isl(iscc):
        languages_section = (
            '[Languages]\n'
            f'Name: "chinesesimplified"; MessagesFile: "{(iscc.parent / "Languages" / "ChineseSimplified.isl")}"\n'
            'Name: "english"; MessagesFile: "compiler:Default.isl"'
        )
    else:
        print("警告：中文向导语言包不可用，安装向导退英文")
        languages_section = '[Languages]\nName: "english"; MessagesFile: "compiler:Default.isl"'
    iss_text = ISS_TEMPLATE.format(root=ROOT, version=get_version(), languages_section=languages_section)
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
