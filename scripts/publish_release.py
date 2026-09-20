"""发布 Release：从 Git 凭据管理器取令牌 → 创建 Release（含 tag）→ 上传分发包。

用法：python scripts/publish_release.py
不打印任何凭据。
"""

from __future__ import annotations

import json
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

REPO = "Veml888/MAgent_WHLLY"
TAG = "v0.1.0"
TITLE = "MAgent v0.1.0 · 首个可用版"
ASSETS = [
    Path(__file__).resolve().parents[1] / "dist" / "MAgent-Setup-v0.1.0.exe",
    Path(__file__).resolve().parents[1] / "dist" / "MAgent-v0.1.0-portable.zip",
]

BODY = """## MAgent v0.1.0 —— 首个可用版

CUMCM 数模全流程 Agent 桌面软件：内置 8 阶段流水线（赛题分析 → 建模 → 编程 → 图表 → 论文 → 验收），
BYOK 接任意 OpenAI 兼容模型（默认 DeepSeek），代码级门禁保证"模型只干活、不记账"。

### 下载

- **普通用户（推荐）**：`MAgent-Setup-v0.1.0.exe` —— 标准安装向导，自动建快捷方式，控制面板可卸载
- **免安装**：`MAgent-v0.1.0-portable.zip`（绿色文件夹版，解压双击「启动MAgent.bat」）
- 两者功能完全相同，任选其一

### 使用步骤

1. 双击启动 → 弹出桌面窗口
2. 设置页填 API Base URL / Key / 模型名 → 测试连接
3. 新建项目（路径 + 题目标题 + 题面文件）→ 在工序线上点「启动」

### 说明

- 论文编译需本机 TeX（xelatex）；TikZ 转 PNG 需 poppler（pdftocairo），缺失时引擎会提示
- 本软件调用你自己的模型 API，费用由你的账号承担
- 内置 skills 内容层源自 [MM_workflow](https://github.com/Veml888/MM_workflow)（MIT）
"""


def get_token() -> str:
    out = subprocess.run(
        ["git", "credential", "fill"],
        input="protocol=https\nhost=github.com\n\n",
        capture_output=True, text=True,
    ).stdout
    fields = dict(line.split("=", 1) for line in out.strip().splitlines() if "=" in line)
    token = fields.get("password")
    if not token:
        raise RuntimeError("Git 凭据管理器中没有 github.com 的令牌，请先手动 git push 一次完成登录")
    return token


def request(url: str, token: str, data: dict | bytes | None = None, headers: dict | None = None, method: str = "GET"):
    req = urllib.request.Request(url, method=method)
    req.add_header("Authorization", f"token {token}")
    req.add_header("User-Agent", "magent-release")
    if isinstance(data, dict):
        data = json.dumps(data).encode()
        req.add_header("Content-Type", "application/json")
    elif isinstance(data, bytes):
        req.add_header("Content-Type", "application/zip")
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    with urllib.request.urlopen(req, data=data, timeout=60) as resp:
        return json.loads(resp.read().decode())


def with_retry(fn, tries: int = 4, delay: float = 8.0):
    last = None
    for i in range(tries):
        try:
            return fn()
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                return None  # 资源不存在是正常分支（如"Release 还没创建"）
            if exc.code >= 500:
                last = exc
                print(f"  服务端错误（第 {i + 1} 次）：{exc.code}，{delay}s 后重试…")
                time.sleep(delay)
                continue
            raise  # 4xx 其他错误是真实问题，直接抛
        except (urllib.error.URLError, ConnectionError, TimeoutError) as exc:
            last = exc
            print(f"  网络失败（第 {i + 1} 次）：{exc}，{delay}s 后重试…")
            time.sleep(delay)
    raise last


def main() -> int:
    for asset in ASSETS:
        if not asset.is_file():
            print(f"警告：找不到附件，跳过 {asset.name}")
    token = get_token()

    existing = with_retry(lambda: request(f"https://api.github.com/repos/{REPO}/releases/tags/{TAG}", token))
    if isinstance(existing, dict) and existing.get("id"):
        print(f"Release {TAG} 已存在（id={existing['id']}），跳过创建")
        release_id = existing["id"]
    else:
        rel = with_retry(lambda: request(
            f"https://api.github.com/repos/{REPO}/releases", token,
            data={"tag_name": TAG, "target_commitish": "main", "name": TITLE, "body": BODY, "draft": False, "prerelease": False},
            method="POST",
        ))
        release_id = rel["id"]
        print(f"Release 已创建：{rel['html_url']}")

    already = {a["name"] for a in with_retry(lambda: request(f"https://api.github.com/repos/{REPO}/releases/{release_id}/assets", token))}
    for asset in ASSETS:
        if not asset.is_file():
            continue
        if asset.name in already:
            print(f"附件已存在，跳过：{asset.name}")
            continue
        size = asset.stat().st_size
        upload_url = f"https://uploads.github.com/repos/{REPO}/releases/{release_id}/assets?name={asset.name}"
        print(f"上传 {asset.name}（{size / 1048576:.0f} MB）…")
        data = asset.read_bytes()
        resp = with_retry(lambda url=upload_url, data=data, size=size: request(
            url, token, data=data, headers={"Content-Length": str(size)}, method="POST"))
        print(f"已上传：{resp['browser_download_url']}")
    print("完成。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
