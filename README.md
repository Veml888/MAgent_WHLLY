# MAgent —— CUMCM 数模全流程 Agent 软件

[![Build & Release](https://github.com/Veml888/MAgent_WHLLY/actions/workflows/release.yml/badge.svg)](https://github.com/Veml888/MAgent_WHLLY/actions/workflows/release.yml)
[![Release](https://img.shields.io/github/v/release/Veml888/MAgent_WHLLY)](https://github.com/Veml888/MAgent_WHLLY/releases)
[![License: MIT](https://img.shields.io/github/license/Veml888/MAgent_WHLLY)](LICENSE)

把 [MM_workflow](https://github.com/Veml888/MM_workflow) 的 8 阶段数模流水线装进一个**新手可用的桌面软件**：
双击图标 → 弹出独立软件窗口（原生窗口，不是浏览器）→ 填自己的模型 API Key →
传题面 → 点启动，剩下的交给流水线。

核心原则：**模型只干活，不记账**。阶段状态、产物哈希、变更历史全部由引擎（代码）
独占记账并强制门禁——模型调 `finish` 申请完成，引擎独立重跑全部机检脚本，
不通过就驳回重修，任何"声称完成"都骗不过去。

## 快速开始

```
1. 双击 start_magent.bat（开发机）或分发包里的「启动MAgent.bat」
   → 弹出 MAgent 桌面窗口（关闭窗口即退出软件）
2. 左侧「⚙ 设置」→ 添加模型服务：填 Base URL / API Key / 模型名 → 测试连接
   - 默认 DeepSeek：https://api.deepseek.com + deepseek-chat
   - 可添加多个服务（GLM/Kimi/Qwen/Ollama…），并在「阶段路由」里
     为不同阶段指定不同模型（如建模/论文用强模型、图表用快模型）
3. 「项目」区：点「选择题面文件…」传题面、「上传附件文件夹…」传数据目录 → 点「创建项目」。
   项目名（如 `2026-国赛B题`）与位置（D 盘）由软件自动生成，无需填写；
   想用已有文件夹：点左侧工作区「＋」选任意文件夹，非项目时点横幅一键初始化
4. 「工序」区三种运行粒度：**全自动运行**（一键跑完 8 阶段）／**逐步运行**（每阶段停下确认）／
   单个阶段「启动」；运行中可停止，运行记录实时显示所用模型与门禁结果
```

桌面窗口由 pywebview（Windows WebView2 内核）承载；异常时用
`python -m magent serve --no-window` 回退浏览器模式。

命令行方式（可选）：`python -m magent serve` / `doctor` / `status --root <目录>` / `run --root <目录>`

## 工作原理

```
┌─ MAgent 引擎（代码，可信）────────────────────────────┐
│  pipeline  8 阶段状态机（依赖/并行/n_a/返工规则）      │
│  gates     门禁：read-verify + 阶段机检 + manifest 校验 │
│  manifest  账本独占写权：状态/SHA256/change_log        │
│  tools     模型工具：读/写/列/命令/finish（带路径安全） │
└──────────────┬───────────────────────────────────────┘
               │ 每阶段一个独立 agent 会话
┌──────────────▼───────────────────────────────────────┐
│  模型（BYOK，任意 OpenAI 兼容）                        │
│  system prompt = 阶段 SKILL.md + 引擎运行规则          │
│  先跑 READ-GATE 四步（read_complete.py）→ 干活 → finish │
└──────────────────────────────────────────────────────┘
```

- **每阶段独立会话**：阶段间只通过 `docs/NN-*.md` 报告与 manifest 交接，上下文不会爆。
- **读取协议原样复用**：模型用 `run_command` 跑 `read_complete.py` 的 plan/chunk/
  write-receipt/verify 四步，文件内容强制进入上下文；引擎收尾再验一遍回执。
- **门禁映射**（阶段 → 机检，全部 exit 0 才算完成）：
  analysis→check_analysis_report；modeling→模型字典复核；coding→check_reproducibility；
  paper_plan→validate_paper_plan；figures→check_figure_overlap；graphics→audit_tikz/audit_svg；
  paper_final→11 项论文机检 + requirement-coverage；verification→复跑 + validate_manifest。

## skills 内嵌与更新

- 发布包**自带完整 skills 内容层**（`magent/skills/`，8 阶段 SKILL.md + references +
  26 个机检脚本 + 读取协议），无需单独获取 MM_workflow 仓库。
- 内容层在仓库里更新后（如 `git pull`），一条命令刷新内置副本：

  ```
  python -m magent sync-skills            # 默认从 D:\AAA-MMW\MM_workflow\skills 同步
  python -m magent sync-skills --from 其他路径\skills
  ```

- 设置页的"skills 目录"留空即用内置副本；也可指向外部目录覆盖（调试新内容时用）。

## 环境要求

- Windows 10/11 + Python 3.10+（本机已验证 3.13）
- 可选：TeX 发行版（论文编译需要 xelatex）、Poppler 的 `pdftocairo`（TikZ 图转 PNG）
  —— `python -m magent doctor` 会逐项自检并给出安装建议。
- 分发包**已内置科学计算栈**（numpy / pandas / scipy / matplotlib / scikit-learn /
  statsmodels / sympy / networkx / pulp / openpyxl / Pillow），编程与绘图阶段无需另装。

## 题面与数据附件

- 支持一次上传多个文件：题面（PDF/DOCX/MD/TXT）+ 数据附件（Excel/CSV/zip 等）
- 全部原样存入项目 `data/`（对模型只读，防止篡改题面数据）；zip 会自动解压
- 模型可直接读 PDF/DOCX/TXT/CSV/xlsx 预览；完整数据在编程阶段用 pandas 处理
- 图片格式题面无法被模型识别，请先转成 PDF 或文字

## 目录约定

- 项目（PROJECT_ROOT）内：`data/`（只读题面与数据附件）、`docs/`、`code/`、`results/`、
  `figures/`、`paper/`、`project-manifest.json`（引擎独占写）。
- `.magent/`：引擎内部目录（prefs、sessions 转录、events 日志），模型禁写。
- 左侧「工作区」可同时打开多个项目（同时做多道题互不干扰），每个工作区可展开文件树；
  点击文件即可预览（文本/图片/PDF），也可用系统程序打开；运行中的工作区有脉冲指示灯。

## 题面与数据上传

- 单文件多选：题面 PDF/DOCX/MD/TXT + 数据附件（Excel/CSV/zip/图片）一次选中
- **整文件夹**：点「选择数据文件夹…」上传整个附件文件夹，按原目录结构存入 `data/`
- zip 自动解压进 `data/`；`data/` 对模型只读

## 分发形态

| 形态 | 说明 |
|---|---|
| **安装包**（推荐） | `MAgent-Setup-v*.exe`：中文向导，自动建桌面/开始菜单快捷方式，控制面板可卸载 |
| **绿色包** | `MAgent-v*-portable.zip`：解压即用，双击「启动MAgent.bat」 |

两者装完都是**同一套自包含环境**：内嵌 Python 运行时 + 全部依赖（含科学计算栈）
+ 内置 skills，用户电脑无需安装任何东西。

> 为什么没有单文件 exe：Agent 必须能真正执行 Python 脚本（门禁机检脚本 + 模型写的
> 解题代码），而 PyInstaller 单文件冻结程序无法充当 Python 解释器，能力不完整，
> 因此不作为分发形态。

## 版本能力（v0.2）

- ✅ 桌面窗口 + 独立设置界面、多模型服务 + 阶段路由、数据附件（Excel/CSV/zip）支持、
  内置科学计算栈、8 阶段门禁引擎、断点续跑（manifest 驱动 + 会话续跑模式）、
  n_a 标记、环境自检、两种分发形态（安装包 / 绿色包）、CI 自动发布
- ⏳ 后续：5a/5b 图表并行、token 用量与费用统计、产物预览、多项目看板、Tauri 壳

## 免责

本软件调用你自己的模型 API，费用由你的账号承担；竞赛使用请遵守当年国赛的
AI 使用规范（论文含 AI 使用声明章节，流水线已内置）。

## 致谢与许可

- 内置 skills 内容层（`magent/skills/`）源自 [MM_workflow](https://github.com/Veml888/MM_workflow)（MIT License）
- 本仓库以 [MIT License](LICENSE) 开源
