# 更新日志

本项目的所有显著变更都记录在此文件中。
格式遵循 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)，
版本号遵循[语义化版本](https://semver.org/lang/zh-CN/)。

## [Unreleased]

### Added

- **数据附件支持**：文件选择器放开格式（Excel / CSV / zip / 图片等均可上传），上传的文件与数据附件统一存入项目 `data/` 目录
- **模型可直读 Excel**：`.xlsx` 用标准库解析（无需额外依赖），分析阶段可直接审计附件数据表
- **zip 自动解压**：上传压缩包自动解压进 `data/`（含路径穿越防护），`data/` 子目录递归展示给模型
- **内置科学计算栈**：运行环境预装 numpy / pandas / scipy / matplotlib / scikit-learn / statsmodels / sympy / networkx / pulp / openpyxl / Pillow，编程阶段与数据图阶段开箱即用

## [0.2.0] - 2026-09-20

### Added

- **多模型服务**：可登记任意多个 OpenAI 兼容服务（DeepSeek / GLM / Kimi / Qwen / Ollama…），每个服务独立配置 Base URL、API Key、模型名，可逐个测试连接
- **阶段路由**：8 个阶段可分别指定模型服务（未指定的跟随默认），例如建模/论文用强模型、图表查字典用快模型
- **独立设置界面**：模型配置从首页移入专门设置页（侧栏「⚙ 设置」进入），首页只保留项目、工序、运行记录
- 首次启动引导横幅：未配置 API Key 时首页提示并可一键跳转设置
- 阶段启动时运行记录会显示本阶段所用的模型服务

### Changed

- 配置 schema 升级到 2.0（`providers` 列表 + `routing` 路由表），旧版单服务配置自动迁移，无需手动处理
- `doctor` 改为逐服务检查连通性并展示阶段路由概况

## [0.1.0] - 2026-09-19

### Added

- 8 阶段数模流水线引擎：赛题分析 → 建模求解 → 编程实现 → 论文策划 → 数据图/示意图 → 论文终稿 → 验收
- 代码级门禁：READ-GATE 完整读取校验 + 26 个机检脚本 + manifest 引擎独占记账（模型无法伪造完成）
- BYOK 模型接入：任意 OpenAI 兼容服务（默认 DeepSeek），可配置 base_url / key / 模型名
- 桌面软件形态：pywebview 原生窗口 + 本地 Web 界面（SSE 实时日志）
- skills 内容层内置，`sync-skills` 命令从上游仓库刷新
- 三种分发产物：标准安装包（Inno Setup，中文向导）/ 单文件 exe / 绿色 zip
- GitHub Actions 自动发布流水线（推 tag 自动构建 + 发 Release，含单测门禁）
- 引擎单元测试 20 项（FakeLLM 驱动的门禁闭环验证）
