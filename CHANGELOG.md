# 更新日志

本项目的所有显著变更都记录在此文件中。
格式遵循 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)，
版本号遵循[语义化版本](https://semver.org/lang/zh-CN/)。

## [Unreleased]

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
