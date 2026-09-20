"""构建 MAgent 绿色免安装分发包。

产物：dist/MAgent-v<版本>-portable.zip
结构：runtime/（内嵌 Python + 预装依赖 + magent 包）、启动.bat、使用说明.txt
目标用户无需安装 Python，解压双击即可。

用法：python scripts/build_portable.py
"""

from __future__ import annotations

import shutil
import subprocess
import sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
import zipfile
from pathlib import Path
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[1]  # scripts/ 的上一级 = 仓库根
DIST = ROOT / "dist"
BUILD = DIST / "MAgent"
RUNTIME = BUILD / "runtime"

# 内嵌 Python 下载源（按顺序尝试，均为主流国内可达镜像）
PY_VERSION = "3.13.7"
PY_SOURCES = [
    f"https://registry.npmmirror.com/-/binary/python/{PY_VERSION}/python-{PY_VERSION}-embed-amd64.zip",
    f"https://mirrors.huaweicloud.com/python/{PY_VERSION}/python-{PY_VERSION}-embed-amd64.zip",
    f"https://www.python.org/ftp/python/{PY_VERSION}/python-{PY_VERSION}-embed-amd64.zip",
]
PIP_INDEX = "https://pypi.tuna.tsinghua.edu.cn/simple"


def download_runtime() -> Path:
    zip_path = DIST / f"python-{PY_VERSION}-embed-amd64.zip"
    if zip_path.is_file() and zipfile.is_zipfile(zip_path):
        print(f"复用已下载的运行时：{zip_path.name}")
        return zip_path
    last_error: Exception | None = None
    for url in PY_SOURCES:
        try:
            print(f"下载内嵌 Python：{url}")
            with urlopen(url, timeout=120) as resp, zip_path.open("wb") as fh:
                shutil.copyfileobj(resp, fh)
            return zip_path
        except Exception as exc:  # 网络失败换下一个源
            print(f"  失败：{exc}")
            last_error = exc
    raise RuntimeError(f"所有下载源均失败：{last_error}")


def patch_pth() -> None:
    """让内嵌解释器识别 Lib/site-packages 并自动执行 site.main()。"""
    for pth in RUNTIME.glob("python3*._pth"):
        text = pth.read_text(encoding="utf-8")
        lines = [ln for ln in text.splitlines() if ln.strip() and not ln.strip().startswith("#")]
        if "Lib/site-packages" not in lines:
            lines.append("Lib/site-packages")
        lines.append("import site")
        pth.write_text("\n".join(lines) + "\n", encoding="utf-8")
        print(f"已改写 {pth.name}：加入 site-packages")
        return
    raise RuntimeError("找不到 python3*._pth")


def install_deps() -> None:
    target = RUNTIME / "Lib" / "site-packages"
    target.mkdir(parents=True, exist_ok=True)
    print("预装依赖（PyMuPDF/openai/fastapi 等，走清华镜像）…")
    subprocess.run(
        [sys.executable, "-m", "pip", "install", "--target", str(target),
         "-r", str(ROOT / "requirements.txt"), "-i", PIP_INDEX, "--no-warn-script-location"],
        check=True,
    )


def copy_package() -> None:
    """magent 包（含内置 skills、web）复制进 runtime 的 site-packages。"""
    src = ROOT / "magent"
    dst = RUNTIME / "Lib" / "site-packages" / "magent"
    if dst.exists():
        shutil.rmtree(dst)
    shutil.copytree(
        src, dst,
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc", ".magent"),
    )
    n = sum(1 for p in dst.rglob("*") if p.is_file())
    print(f"magent 包已就位（{n} 个文件，含内置 skills）")


def build_launcher() -> None:
    """生成 dist/MAgent/MAgent.exe（原生启动器，转发到内嵌运行时）。"""
    script = ROOT / "scripts" / "make_launcher.py"
    subprocess.run([sys.executable, str(script)], check=True, cwd=ROOT)


def write_launcher_and_readme() -> None:
    # 桌面版：pythonw 无控制台窗口，双击 = 打开软件窗口；start 让 cmd 立即退出
    (BUILD / "启动MAgent.bat").write_text(
        "@echo off\r\n"
        "cd /d %~dp0\r\n"
        "start \"\" \"%~dp0runtime\\pythonw.exe\" -m magent serve\r\n",
        encoding="utf-8",
    )
    # 备用：浏览器模式（带控制台日志，便于排查问题）
    (BUILD / "启动MAgent（浏览器模式）.bat").write_text(
        "@echo off\r\n"
        "chcp 65001 >nul\r\n"
        "cd /d %~dp0\r\n"
        "runtime\\python.exe -m magent serve --no-window\r\n"
        "pause\r\n",
        encoding="utf-8",
    )
    (BUILD / "使用说明.txt").write_text(
        "MAgent —— CUMCM 数模全流程 Agent 软件\n"
        "=====================================\n\n"
        "使用步骤：\n"
        "1. 双击「启动MAgent.bat」→ 弹出 MAgent 软件窗口（这是正常形态，\n"
        "   不是浏览器；关闭窗口即退出软件）\n"
        "2. 首次使用：点左侧「⚙ 设置」→ 「＋ 添加服务」填入你自己的模型 API Key\n"
        "   （默认 DeepSeek：https://api.deepseek.com / deepseek-chat，\n"
        "    任何 OpenAI 兼容服务改 Base URL 和模型名即可）→ 点「测试连接」\n"
        "   同一页的「阶段路由」可给不同阶段指定不同模型（如建模用强模型、画图用快模型）\n"
        "3. 新建题目：右侧「项目」区只填「题目标题」→ 路径自动生成为 D:\\标题\n"
        "   （也可点「浏览…」自选位置）；「选择题面文件…」传题面 PDF，\n"
        "   「上传附件文件夹…」可整个文件夹上传（Excel/CSV 等，按原目录结构存入 data/）\n"
        "   → 点「创建项目」\n"
        "4. 运行：工序区点「▶ 全自动运行」一键跑完 8 个阶段（每阶段过门禁自动进入下一步），\n"
        "   或点「▶ 逐步运行」每阶段停下等你确认；也可单独点某个阶段的「启动」\n\n"
        "多题目并行：\n"
        "- 左侧「工作区」可同时打开多道题（新建或点 ＋ 添加已有项目）\n"
        "- 点工作区名字切换；▸ 展开它的文件树，点文件即可预览（文本/图片/PDF）\n"
        "- 运行中的工作区有脉冲绿灯；切换时日志自动回放\n\n"
        "常见问题：\n"
        "- 桌面窗口打不开时，用「启动MAgent（浏览器模式）.bat」备用，\n"
        "  并把控制台里的报错发给作者；\n"
        "- 论文编译需要 TeX：未安装时论文阶段交付 .tex，环境自检会提示；\n"
        "- TikZ 图转 PNG 需要 pdftocairo：可执行 winget install poppler；\n"
        "- 全部数据只保存在本机，模型调用走你自己的 API 账号。\n",
        encoding="utf-8",
    )


def make_zip() -> Path:
    zip_path = DIST / f"MAgent-v{get_version()}-portable.zip"
    if zip_path.exists():
        zip_path.unlink()
    print("压缩分发包…")
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
        for path in BUILD.rglob("*"):
            zf.write(path, path.relative_to(DIST))
    size_mb = zip_path.stat().st_size / 1024 / 1024
    print(f"✅ 分发包：{zip_path}（{size_mb:.0f} MB）")
    return zip_path


def get_version() -> str:
    text = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    for line in text.splitlines():
        if line.startswith("version"):
            return line.split("=", 1)[1].strip().strip('"')
    return "0.1.0"


def main() -> int:
    DIST.mkdir(exist_ok=True)
    if BUILD.exists():
        shutil.rmtree(BUILD)
    BUILD.mkdir()

    runtime_zip = download_runtime()
    print("解压内嵌 Python…")
    with zipfile.ZipFile(runtime_zip) as zf:
        zf.extractall(RUNTIME)
    patch_pth()
    install_deps()
    copy_package()
    write_launcher_and_readme()
    build_launcher()

    # 冒烟：内嵌解释器能加载 magent 并跑 --version；桌面壳依赖齐全
    smoke = subprocess.run(
        [str(RUNTIME / "python.exe"), "-m", "magent", "--version"],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    print(f"内嵌解释器冒烟：exit {smoke.returncode} → {smoke.stdout.strip() or smoke.stderr.strip()}")
    gui_smoke = subprocess.run(
        [str(RUNTIME / "python.exe"), "-c", "import webview, clr; print('gui ok')"],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    print(f"桌面壳依赖冒烟：exit {gui_smoke.returncode} → {gui_smoke.stdout.strip() or gui_smoke.stderr.strip()}")
    if smoke.returncode != 0 or gui_smoke.returncode != 0:
        return 1
    make_zip()
    return 0


if __name__ == "__main__":
    sys.exit(main())
