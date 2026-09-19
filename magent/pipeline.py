"""8 阶段流水线定义：依赖、owner skill、验收命令、产物契约。

命令模板占位符：{PY}=Python 解释器绝对路径，{SK}=skills 根目录。
所有命令在 PROJECT_ROOT 下执行。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from .manifest import STAGE_KEYS


@dataclass
class StageDef:
    key: str
    title: str
    skill: str
    round_label: str = ""                    # paper_final 分 writing / review
    upstream: list[str] = field(default_factory=list)
    receipt: str = "skill-read-receipt.json"
    session_ledger: str = ".read-session.json"
    read_gate: bool = True                   # 测试用假阶段可关
    exists: list[str] = field(default_factory=list)      # 阶段结束时必须存在的文件
    acceptance: list[dict] = field(default_factory=list)  # {"name", "cmd", "when"?}
    dynamic: str = ""                        # "graphics" 走按文件审计
    finalize_fields: dict[str, str] = field(default_factory=dict)  # 阶段字段 <- 产物路径


def _cmd(name: str, parts: list[str], when: str | None = None) -> dict:
    return {"name": name, "cmd": parts, "when": when}


def build_stages() -> dict[str, StageDef]:
    s = STAGE_KEYS
    stages: dict[str, StageDef] = {}

    stages["analysis"] = StageDef(
        key=s[0], title="赛题分析", skill="mm-problem-analysis",
        upstream=[],
        exists=["docs/01-analysis-report.md"],
        acceptance=[
            _cmd("check_analysis_report", ["{PY}", "{SK}/mm-problem-analysis/scripts/check_analysis_report.py", "docs/01-analysis-report.md"]),
        ],
    )
    stages["modeling"] = StageDef(
        key=s[1], title="建模求解", skill="mm-modeling",
        upstream=["analysis"],
        exists=["docs/02-modeling-report.md"],
        acceptance=[
            _cmd("model_dictionary_recheck", ["{PY}", "{SK}/mm-model-dictionary/scripts/query_model_dictionary.py", "--model", "{MODEL}"], when="per_selected_model"),
        ],
    )
    stages["coding"] = StageDef(
        key=s[2], title="编程实现", skill="mm-coding",
        upstream=["modeling"],
        exists=["docs/03-results-report.md", "results/复现清单.json"],
        acceptance=[
            _cmd("check_reproducibility", ["{PY}", "{SK}/mm-coding/scripts/check_reproducibility.py", "results/复现清单.json", "--root", "."]),
        ],
    )
    stages["paper_plan"] = StageDef(
        key=s[5], title="论文策划", skill="mm-orchestrator",
        upstream=["coding"],
        exists=[
            "paper/structure-plan.md", "paper/page-budget.json",
            "paper/figure-requirements.md", "paper/writing-gates.md",
            "paper/draft-audit.md", "paper/draft-metrics.json",
        ],
        acceptance=[
            _cmd("validate_paper_plan", ["{PY}", "{SK}/mm-orchestrator/scripts/validate_paper_plan.py", "paper/page-budget.json", "--root", "."]),
        ],
        finalize_fields={
            "report": "paper/structure-plan.md",
            "gates": "paper/writing-gates.md",
            "page_budget": "paper/page-budget.json",
            "draft_audit": "paper/draft-audit.md",
            "draft_metrics": "paper/draft-metrics.json",
        },
    )
    stages["figures"] = StageDef(
        key=s[3], title="数据图", skill="mm-figures",
        upstream=["paper_plan"],
        exists=["docs/04-figures-report.md"],
        acceptance=[
            _cmd("check_figure_overlap", ["{PY}", "{SK}/mm-figures/scripts/check_figure_overlap.py", "--dir", "figures"]),
        ],
    )
    stages["graphics"] = StageDef(
        key=s[4], title="示意图", skill="mm-graphics",
        upstream=["paper_plan"],
        exists=["docs/05-diagrams-report.md"],
        acceptance=[],
        dynamic="graphics",
    )
    stages["paper_final"] = StageDef(
        key=s[6], title="论文终稿", skill="mm-paper-writing",
        round_label="writing+review",
        upstream=["figures", "graphics"],
        receipt="paper/skill-read-receipt.json",
        session_ledger="paper/.read-session.json",
        exists=[
            "paper/论文.tex", "paper/论文.pdf",
            "paper/sections/00-abstract.tex",
            "paper/writing-audit.md", "paper/review-findings.md",
            "paper/review-audit.md", "paper/page-audit.json",
            "paper/content-gap-report.md",
        ],
        acceptance=[
            _cmd("check_paper_order", ["{PY}", "{SK}/mm-paper-writing/scripts/check_paper_order.py"]),
            _cmd("check_paper_length", ["{PY}", "{SK}/mm-paper-writing/scripts/check_paper_length.py", "paper/论文.pdf"]),
            _cmd("check_line_spacing", ["{PY}", "{SK}/mm-paper-writing/scripts/check_line_spacing.py", "paper/论文.pdf"]),
            _cmd("check_noindent", ["{PY}", "{SK}/mm-paper-writing/scripts/check_noindent.py", "paper/论文.tex"]),
            _cmd("check_quotes", ["{PY}", "{SK}/mm-paper-writing/scripts/check_quotes.py", "paper/论文.tex"]),
            _cmd("check_abstract_symbols", ["{PY}", "{SK}/mm-paper-writing/scripts/check_abstract_symbols.py", "paper/论文.tex"]),
            _cmd("audit_paper_tables", ["{PY}", "{SK}/mm-paper-writing/scripts/audit_paper_tables.py", "paper/论文.tex"]),
            _cmd("audit_submission_language", ["{PY}", "{SK}/mm-paper-writing/scripts/audit_submission_language.py", "paper"]),
            _cmd("audit_abstract_page", ["{PY}", "{SK}/mm-paper-writing/scripts/audit_abstract_page.py", "--pdf", "paper/论文.pdf", "--abstract-tex", "paper/sections/00-abstract.tex"]),
            _cmd("check_paper_cites", ["{PY}", "{SK}/mm-paper-writing/scripts/check_paper_cites.py", "paper/论文.tex"]),
            _cmd("check_paper_refs", ["{PY}", "{SK}/mm-paper-writing/scripts/check_paper_refs.py", "paper/论文.tex", "--root", "."]),
            _cmd("check_layout", ["{PY}", "{SK}/mm-paper-writing/scripts/check_layout.py", "paper/论文.pdf"]),
            _cmd("validate_requirement_coverage", ["{PY}", "{SK}/mm-paper-writing/scripts/validate_requirement_coverage.py", "paper/review-audit.md"]),
        ],
        finalize_fields={
            "read_receipt": "paper/skill-read-receipt.json",
            "writing_audit": "paper/writing-audit.md",
            "review_findings": "paper/review-findings.md",
            "review_audit": "paper/review-audit.md",
            "length_audit": "paper/page-audit.json",
            "content_gap_report": "paper/content-gap-report.md",
        },
    )
    stages["verification"] = StageDef(
        key=s[7], title="验收", skill="mm-verification",
        upstream=["paper_final"],
        exists=["docs/06-verification-report.md"],
        acceptance=[
            _cmd("check_reproducibility", ["{PY}", "{SK}/mm-coding/scripts/check_reproducibility.py", "results/复现清单.json", "--root", "."], when="coding_active"),
            _cmd("check_figure_overlap", ["{PY}", "{SK}/mm-figures/scripts/check_figure_overlap.py", "--dir", "figures"], when="figures_active"),
        ],
    )
    return stages


STAGES: dict[str, StageDef] = build_stages()
ORDER: list[str] = list(STAGES.keys())


def next_stage(manifest_statuses: dict[str, str]) -> str | None:
    """按依赖顺序找下一个可执行阶段（n_a 视为已满足上游）。"""
    done = lambda st: st in ("complete", "n_a")
    for key in ORDER:
        status = manifest_statuses.get(key, "pending")
        if status in ("pending", "failed"):
            stage = STAGES[key]
            if all(done(manifest_statuses.get(u, "pending")) for u in stage.upstream):
                return key
        if status == "in_progress":
            return key  # 有进行中阶段先续跑
    return None


def build_system_prompt(
    stage: StageDef,
    skills_root: Path,
    project_root: Path,
    python_exe: str,
    extra_rules: str = "",
) -> str:
    skill_dir = skills_root / stage.skill
    skill_md = (skill_dir / "SKILL.md").read_text(encoding="utf-8", errors="replace")
    registry = "references/writing-order.json" if stage.skill == "mm-paper-writing" else "references/reading-order.json"
    gate_cmds = "\n".join(f"  - {a['name']}" for a in stage.acceptance) or "  - （本阶段由引擎按产物存在性校验）"
    return f"""{skill_md}

---
# MAgent 运行引擎注入（本节优先级最高，与上文冲突时以本节为准）

- PROJECT_ROOT（你的所有文件操作与命令执行都被限制在此目录内）：`{project_root}`
- 本阶段 owner skill 目录：`{skill_dir}`
- 本阶段必读注册表：`{skill_dir}/{registry}`
- 共享读取协议脚本（唯一副本）：`{skills_root / "mm-orchestrator" / "scripts" / "read_complete.py"}`
- Python 解释器绝对路径（run_command 调脚本时使用）：`{python_exe}`

## 本阶段硬性流程
1. 开工前：用 run_command 完成完整读取门禁四步（plan → 建 `.read-session.json` 账本 → 按 plan 输出的 chunks 逐块 chunk → write-receipt → verify）。**verify exit 0 之前不得做任何本阶段工作**。
2. 按上方 SKILL.md 的工作流与规范完成本阶段全部工作（含其要求的自检脚本）。
3. 全部完成、自检通过后，调用 `finish` 工具提交产物相对路径清单与总结。引擎将独立重跑下述机检，不通过会驳回并要求你修复。

## 引擎将重跑的机检（你在收尾前必须自跑并通过）
{gate_cmds}

## 禁止事项
- 直接读写 `project-manifest.json`（引擎独占记账，工具会拒绝）。
- 不调用 finish 就宣称完成；跳过或伪造机检结果。
- 修改 `data/`（只读输入）与 `.magent/`（引擎内部目录）。

{extra_rules}"""


def build_user_prompt(
    stage: StageDef,
    project_root: Path,
    prefs: dict,
    problem_files: list[str],
    resumed: bool = False,
) -> str:
    upstream = "\n".join(f"  - {p}" for p in stage.exists) or "  - （无）"
    files = "、".join(problem_files) if problem_files else "（data/ 下暂无文件，请先与用户确认题面）"
    focus = prefs.get("focus", "均衡")
    subproblems = prefs.get("subproblems", "待赛题分析确定")
    if resumed:
        head = (
            f"本阶段「{stage.title}」的上一会话被中断。请先用 list_dir/read_file 盘点 "
            f"PROJECT_ROOT 现状（哪些产物已存在、机检是否已过），从中断处继续，不要重做已完成的部分。"
        )
    else:
        head = f"请开始执行「{stage.title}」阶段（owner skill：{stage.skill}）。"
    return f"""{head}

- 用户偏好：侧重点={focus}；子问题数={subproblems}
- 题面与数据：{files}
- 本阶段结束时必须存在的产物（相对 PROJECT_ROOT）：
{upstream}
若发现上游产物或题面缺失，请在回复中说明并停止，不要臆造内容。"""


def paper_budget_target(root: Path) -> int | None:
    """读 paper/page-budget.json 的 target_body_pages（应为 28/29）。"""
    budget = root / "paper" / "page-budget.json"
    if not budget.is_file():
        return None
    try:
        data = json.loads(budget.read_text(encoding="utf-8"))
        return int(data.get("target_body_pages"))
    except (OSError, ValueError, json.JSONDecodeError):
        return None
