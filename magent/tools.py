"""模型可用的工具集：read_file / write_file / list_dir / run_command / finish。

安全约定：
- 所有路径以 PROJECT_ROOT 为基准（相对路径），绝对路径也必须落在 PROJECT_ROOT 内；
- project-manifest.json、.magent/**（引擎内部）对 write_file 永久禁写；
- data/** 是只读输入（题面与数据），write_file 禁写；
- finish 只抛 FinishSignal，由引擎决定是否真正完成（模型不记账）。
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

MAX_READ_LINES = 1500
MAX_READ_CHARS = 60_000
MAX_STDOUT_CHARS = 100_000
MAX_STDERR_CHARS = 30_000


class ToolError(RuntimeError):
    """工具执行失败（路径越界、禁写、参数非法等）。"""


class FinishSignal(Exception):
    """模型调用 finish 提交阶段产物；由引擎捕获并进入门禁。"""

    def __init__(self, artifacts: list[str], summary: str, extras: dict | None = None):
        super().__init__(summary)
        self.artifacts = artifacts
        self.summary = summary
        self.extras = extras or {}


class ToolBox:
    def __init__(self, project_root: Path, run_timeout_sec: int = 600, log=None):
        self.root = Path(project_root).resolve()
        self.run_timeout_sec = run_timeout_sec
        self.log = log or (lambda event: None)

    # ---------- 路径安全 ----------

    def resolve_in_root(self, path_str: str) -> Path:
        if not path_str or not str(path_str).strip():
            raise ToolError("路径不能为空")
        p = Path(path_str)
        candidate = (self.root / p) if not p.is_absolute() else p
        resolved = candidate.resolve()
        root = self.root
        try:
            resolved.relative_to(root)
        except ValueError as exc:
            raise ToolError(f"路径越界：{path_str} 不在 PROJECT_ROOT 内") from exc
        return resolved

    def _deny_read(self, resolved: Path) -> None:
        if resolved == self.root / "project-manifest.json":
            raise ToolError("project-manifest.json 由 MAgent 引擎独占记账，模型禁止读写")

    def _deny_write(self, resolved: Path) -> None:
        root = self.root
        rel_parts = resolved.relative_to(root).parts
        if resolved == root / "project-manifest.json":
            raise ToolError("project-manifest.json 由 MAgent 引擎独占记账，模型禁止读写")
        if ".magent" in rel_parts:
            raise ToolError(".magent/ 是引擎内部目录，禁止读写")
        if rel_parts and rel_parts[0] == "data":
            raise ToolError("data/ 是只读输入目录，禁止写入")

    # ---------- 工具实现 ----------

    def read_file(self, path_str: str, start_line: int = 1, end_line: int | None = None) -> str:
        path = self.resolve_in_root(path_str)
        self._deny_read(path)
        if not path.is_file():
            raise ToolError(f"文件不存在：{path_str}")
        suffix = path.suffix.lower()
        if suffix == ".pdf":
            return self._read_pdf(path)
        if suffix == ".docx":
            return self._read_docx(path)
        if suffix == ".xlsx":
            return self._read_xlsx(path)
        if suffix == ".xls":
            return (
                f"[{path_str}] 旧版 .xls 无法直接解析。请在编程阶段用 pandas 读取"
                "（pd.read_excel 需要 xlrd 时改用 .xlsx），或先另存为 .xlsx/.csv。"
            )
        if suffix in {".zip", ".rar", ".7z"}:
            return (
                f"[{path_str}] 压缩包不能直接读取。编程阶段可用 python 的 zipfile 解压后再处理"
                "（若为 .rar/.7z 需要先转为 zip）。"
            )
        if suffix in {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp", ".tif", ".tiff"}:
            return (
                f"[二进制图片：{path_str}] 模型无法直接读取图片内容。"
                "请让用户粘贴文字版题面，或使用 PDF/DOCX/MD/TXT 题面。"
            )
        text = path.read_text(encoding="utf-8", errors="replace")
        lines = text.splitlines()
        total = len(lines)
        start = max(1, int(start_line or 1))
        end = min(total, int(end_line)) if end_line else total
        window = lines[start - 1 : end]
        if len(window) > MAX_READ_LINES:
            window = window[:MAX_READ_LINES]
            truncated = True
        else:
            truncated = False
        out_lines = [f"{i:04d}: {line}" for i, line in enumerate(window, start=start)]
        body = "\n".join(out_lines)
        if len(body) > MAX_READ_CHARS:
            body = body[:MAX_READ_CHARS] + "\n...[内容过长已截断，请用 start_line/end_line 分段读取]"
        elif truncated:
            body += f"\n...[仅显示前 {MAX_READ_LINES} 行，共 {total} 行]"
        header = f"[{path_str}] 共 {total} 行，显示 {start}-{start + len(window) - 1} 行"
        return header + "\n" + body

    def _read_pdf(self, path: Path) -> str:
        try:
            import fitz
        except ImportError as exc:
            raise ToolError("读取 PDF 需要 PyMuPDF（pip install PyMuPDF）") from exc
        doc = fitz.open(path)
        parts = []
        used = 0
        for i, page in enumerate(doc, start=1):
            text = page.get_text("text").strip()
            block = f"--- 第 {i} 页 ---\n{text}"
            if used + len(block) > MAX_READ_CHARS:
                parts.append(f"...[PDF 内容超过 {MAX_READ_CHARS} 字符截断]")
                break
            parts.append(block)
            used += len(block)
        doc.close()
        return "\n\n".join(parts)

    def _read_docx(self, path: Path) -> str:
        import re
        import zipfile

        try:
            with zipfile.ZipFile(path) as zf:
                xml = zf.read("word/document.xml").decode("utf-8", errors="replace")
        except (KeyError, zipfile.BadZipFile) as exc:
            raise ToolError(f"无法解析 DOCX：{exc}") from exc
        xml = xml.replace("</w:p>", "</w:p>\n")
        text = re.sub(r"<[^>]+>", "", xml)
        text = re.sub(r"\n{3,}", "\n\n", text).strip()
        if len(text) > MAX_READ_CHARS:
            text = text[:MAX_READ_CHARS] + "\n...[内容过长已截断]"
        return f"[{path.name}] DOCX 文本内容：\n{text}"

    def _read_xlsx(self, path: Path, max_sheets: int = 3, max_rows: int = 120, max_cols: int = 30) -> str:
        """用标准库解析 .xlsx（zip + XML），无需 openpyxl，供模型审计附件数据。"""
        import xml.etree.ElementTree as ET
        import zipfile

        NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
        try:
            with zipfile.ZipFile(path) as zf:
                shared: list[str] = []
                if "xl/sharedStrings.xml" in zf.namelist():
                    root = ET.fromstring(zf.read("xl/sharedStrings.xml"))
                    for si in root.iter(f"{{{NS}}}si"):
                        shared.append("".join(t.text or "" for t in si.iter(f"{{{NS}}}t")))
                sheets = sorted(
                    n for n in zf.namelist()
                    if n.startswith("xl/worksheets/sheet") and n.endswith(".xml")
                )
                if not sheets:
                    raise ToolError(f"xlsx 内没有工作表：{path.name}")
                parts: list[str] = []
                for sheet_name in sheets[:max_sheets]:
                    root = ET.fromstring(zf.read(sheet_name))
                    rows_out: list[str] = []
                    count = 0
                    for row in root.iter(f"{{{NS}}}row"):
                        if count >= max_rows:
                            rows_out.append(f"...[超过 {max_rows} 行截断]")
                            break
                        cells: list[str] = []
                        for cell in row.iter(f"{{{NS}}}c"):
                            value_el = cell.find(f"{{{NS}}}v")
                            raw = value_el.text if value_el is not None else ""
                            if cell.get("t") == "s" and raw and raw.isdigit() and int(raw) < len(shared):
                                raw = shared[int(raw)]
                            cells.append((raw or "").strip())
                        rows_out.append(" | ".join(cells[:max_cols]))
                        count += 1
                    parts.append(f"--- {sheet_name.split('/')[-1]}（前 {count} 行）---\n"
                                 + "\n".join(rows_out))
        except zipfile.BadZipFile as exc:
            raise ToolError(f"无法解析 xlsx：{exc}") from exc
        text = "\n".join(parts)
        if len(text) > MAX_READ_CHARS:
            text = text[:MAX_READ_CHARS] + "\n...[内容过长已截断，编程阶段请用 pandas 完整读取]"
        return f"[{path.name}] Excel 内容（仅预览，完整数据请在编程阶段用 pandas 读取）：\n{text}"

    def write_file(self, path_str: str, content: str) -> str:
        path = self.resolve_in_root(path_str)
        self._deny_write(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        data = content if isinstance(content, str) else str(content)
        path.write_text(data, encoding="utf-8", newline="\n")
        self.log({"type": "file_written", "path": path_str, "bytes": len(data.encode("utf-8"))})
        return json.dumps({"written": path_str, "bytes": len(data.encode("utf-8"))}, ensure_ascii=False)

    def list_dir(self, path_str: str = ".") -> str:
        path = self.resolve_in_root(path_str or ".")
        if not path.is_dir():
            raise ToolError(f"目录不存在：{path_str}")
        entries = []
        for item in sorted(path.iterdir(), key=lambda x: (x.is_file(), x.name)):
            if len(entries) >= 500:
                entries.append({"name": "...[超过 500 项截断]", "type": "note"})
                break
            if item.is_dir():
                entries.append({"name": item.name, "type": "dir"})
            else:
                entries.append({"name": item.name, "type": "file", "bytes": item.stat().st_size})
        return json.dumps({"dir": path_str, "entries": entries}, ensure_ascii=False)

    def run_command(self, command: list[str], timeout_sec: int | None = None) -> str:
        if not isinstance(command, list) or not command or not all(
            isinstance(c, str) and c.strip() for c in command
        ):
            raise ToolError("command 必须是非空字符串列表")
        timeout = min(int(timeout_sec or self.run_timeout_sec), 600)
        env = dict(os.environ)
        env["PYTHONIOENCODING"] = "utf-8"
        try:
            proc = subprocess.run(
                command,
                cwd=self.root,
                env=env,
                text=True,
                encoding="utf-8",
                errors="replace",
                capture_output=True,
                timeout=timeout,
            )
            exit_code = proc.returncode
            stdout, stderr = proc.stdout or "", proc.stderr or ""
        except subprocess.TimeoutExpired as exc:
            exit_code = -1
            stdout = (exc.stdout or b"").decode("utf-8", errors="replace") if isinstance(exc.stdout, bytes) else (exc.stdout or "")
            stderr = f"[TIMEOUT] 命令超过 {timeout} 秒被强制终止"
        except OSError as exc:
            return json.dumps({"exit_code": -1, "stdout": "", "stderr": f"[OSERROR] {exc}", "command": command}, ensure_ascii=False)

        stdout = self._truncate(stdout, MAX_STDOUT_CHARS)
        stderr = self._truncate(stderr, MAX_STDERR_CHARS)
        self.log({"type": "command", "command": command[:3], "exit_code": exit_code})
        return json.dumps(
            {"exit_code": exit_code, "stdout": stdout, "stderr": stderr, "command": command},
            ensure_ascii=False,
        )

    @staticmethod
    def _truncate(text: str, cap: int) -> str:
        if len(text) <= cap:
            return text
        head = cap * 3 // 4
        tail = cap - head
        return text[:head] + "\n...[中间内容截断]...\n" + text[-tail:]

    # ---------- finish ----------

    def finish(self, artifacts: list[str], summary: str, extras: dict | None = None) -> str:
        if not isinstance(artifacts, list) or not all(isinstance(a, str) for a in artifacts):
            raise ToolError("finish.artifacts 必须是相对路径字符串列表")
        for a in artifacts:
            path = self.resolve_in_root(a)
            if not path.is_file():
                raise ToolError(f"finish 产物不存在：{a}")
        raise FinishSignal(artifacts, summary or "", extras)

    # ---------- OpenAI 工具 schema ----------

    def schemas(self) -> list[dict]:
        return [
            {
                "type": "function",
                "function": {
                    "name": "read_file",
                    "description": "读取 PROJECT_ROOT 内的文本文件（支持 .pdf/.docx 自动抽取文本；图片不可读）。返回带行号内容。",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "path": {"type": "string", "description": "相对 PROJECT_ROOT 的路径"},
                            "start_line": {"type": "integer", "description": "起始行（1 起，默认 1）"},
                            "end_line": {"type": "integer", "description": "结束行（含），默认到文件尾"},
                        },
                        "required": ["path"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "write_file",
                    "description": "写文件（UTF-8）。禁止写 project-manifest.json、.magent/**、data/**。",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "path": {"type": "string"},
                            "content": {"type": "string"},
                        },
                        "required": ["path", "content"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "list_dir",
                    "description": "列出目录内容。",
                    "parameters": {
                        "type": "object",
                        "properties": {"path": {"type": "string", "description": "默认 ."}},
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "run_command",
                    "description": "在 PROJECT_ROOT 内执行命令（列表形式，无 shell）。用于跑 python 机检脚本、read_complete.py 等。默认超时 600 秒。",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "command": {"type": "array", "items": {"type": "string"}},
                            "timeout_sec": {"type": "integer"},
                        },
                        "required": ["command"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "finish",
                    "description": "声明本阶段完成并提交产物清单。引擎会重跑全部机检门禁，不通过会驳回。调用后本轮结束。",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "artifacts": {
                                "type": "array",
                                "items": {"type": "string"},
                                "description": "本阶段产出的文件相对路径列表",
                            },
                            "summary": {"type": "string", "description": "一段话总结本阶段成果"},
                            "extras": {
                                "type": "object",
                                "description": "按阶段需要的补充字段（如论文阶段的 final_body_pages、compile_passes）",
                            },
                        },
                        "required": ["artifacts", "summary"],
                    },
                },
            },
        ]

    def execute(self, name: str, arguments: str | dict) -> str:
        """执行一个工具调用，返回可放入 tool message 的字符串。"""
        try:
            args = json.loads(arguments) if isinstance(arguments, str) else dict(arguments)
        except json.JSONDecodeError as exc:
            return json.dumps({"error": f"参数不是合法 JSON：{exc}"}, ensure_ascii=False)
        handler = {
            "read_file": lambda: self.read_file(
                args.get("path", ""),
                args.get("start_line", 1),
                args.get("end_line"),
            ),
            "write_file": lambda: self.write_file(args.get("path", ""), args.get("content", "")),
            "list_dir": lambda: self.list_dir(args.get("path", ".")),
            "run_command": lambda: self.run_command(args.get("command", []), args.get("timeout_sec")),
            "finish": lambda: self.finish(args.get("artifacts", []), args.get("summary", ""), args.get("extras")),
        }.get(name)
        if handler is None:
            return json.dumps({"error": f"未知工具：{name}"}, ensure_ascii=False)
        try:
            return handler()
        except FinishSignal:
            raise
        except ToolError as exc:
            return json.dumps({"error": str(exc)}, ensure_ascii=False)
        except Exception as exc:  # 工具内部意外错误也以结果形式返回，模型可自纠
            return json.dumps({"error": f"工具执行异常：{exc}"}, ensure_ascii=False)
