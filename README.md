# MAgent —— CUMCM 数模全流程 Agent 软件

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
2. 「模型与路径设置」：填 API Base URL / Key / 模型名 → 测试连接
   - 默认 DeepSeek：https://api.deepseek.com + deepseek-chat
   - 任意 OpenAI 兼容服务（GLM/Kimi/Qwen/Ollama…）改三个字段即可
3. 「新建项目」：填项目路径、题目标题、侧重点 → 选择题面文件（PDF/DOCX/MD/TXT）→ 创建
4. 流水线卡片上点「启动」，从赛题分析阶段开始跑
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

## 目录约定

- 项目（PROJECT_ROOT）内：`data/`（只读题面）、`docs/`、`code/`、`results/`、
  `figures/`、`paper/`、`project-manifest.json`（引擎独占写）。
- `.magent/`：引擎内部目录（prefs、sessions 转录、events 日志），模型禁写。

## 当前版本（v0.1）范围

- ✅ CLI 内核 + 本地 Web 面板、DeepSeek 等 OpenAI 兼容 BYOK、8 阶段门禁引擎、
  断点续跑（manifest 驱动 + 会话续跑模式）、n_a 标记、环境自检
- ⏳ 后续：5a/5b 图表并行、分阶段模型路由、桌面打包（Tauri）、TeX 一键安装、多项目看板

## 免责

本软件调用你自己的模型 API，费用由你的账号承担；竞赛使用请遵守当年国赛的
AI 使用规范（论文含 AI 使用声明章节，流水线已内置）。
