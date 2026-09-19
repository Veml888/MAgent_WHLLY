"""单阶段 agent 会话：system prompt + 工具调用循环 + 转录落盘。"""

from __future__ import annotations

import json
import time
from pathlib import Path

from .llm import LLMClient
from .tools import FinishSignal, ToolBox


class AgentSession:
    """一个阶段一次会话。run() 驱动循环，直到 finish / 上限 / 异常。"""

    def __init__(
        self,
        llm: LLMClient,
        toolbox: ToolBox,
        system_prompt: str,
        transcript_path: Path | None,
        log=None,
        max_turns: int = 150,
    ):
        self.llm = llm
        self.toolbox = toolbox
        self.log = log or (lambda event: None)
        self.max_turns = max_turns
        self.messages: list[dict] = [{"role": "system", "content": system_prompt}]
        self.no_tool_turns = 0
        self.turns = 0
        self.transcript_path = transcript_path
        if transcript_path is not None:
            transcript_path.parent.mkdir(parents=True, exist_ok=True)
            transcript_path.write_text("", encoding="utf-8")

    # ---------- 转录 ----------

    def _record(self, entry: dict) -> None:
        if self.transcript_path is None:
            return
        entry = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S"), **entry}
        with self.transcript_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry, ensure_ascii=False) + "\n")

    # ---------- 主循环 ----------

    def run(self, user_msg: str):
        """驱动 agent 直到结束。

        返回：
        - FinishSignal —— 模型提交完成（由 engine 捕获处理门禁）
        - "max_turns" / "no_progress" —— 会话中止原因
        - 其余异常（LLMError 等）向上抛出
        """
        self.messages.append({"role": "user", "content": user_msg})
        self._record({"role": "user", "content": user_msg})

        while True:
            self.turns += 1
            if self.turns > self.max_turns:
                self.log({"type": "error", "msg": f"已达最大轮数上限 {self.max_turns}"})
                return "max_turns"

            raw = self.llm.chat(self.messages, self.toolbox.schemas())
            assistant_msg = self.llm.assistant_message(raw)
            self.messages.append(assistant_msg)
            self._record({"role": "assistant", **assistant_msg})
            if raw["content"]:
                self.log({"type": "assistant_text", "text": raw["content"][:600]})
            if raw.get("usage"):
                self.log({"type": "usage", **raw["usage"]})

            tool_calls = raw.get("tool_calls") or []
            if not tool_calls:
                self.no_tool_turns += 1
                if self.no_tool_turns >= 2:
                    self.log({"type": "error", "msg": "模型连续两轮未调用任何工具，会话中止"})
                    return "no_progress"
                self.messages.append(
                    {
                        "role": "user",
                        "content": "请通过工具继续工作（read_file/run_command/write_file 等）；"
                        "本阶段全部完成后调用 finish 工具提交产物清单。若遇到无法解决的问题，"
                        "请直接说明原因。",
                    }
                )
                continue
            self.no_tool_turns = 0

            for call in tool_calls:
                self.log(
                    {
                        "type": "tool_call",
                        "name": call["name"],
                        "args": call["arguments"][:300],
                    }
                )
                try:
                    result = self.toolbox.execute(call["name"], call["arguments"])
                except FinishSignal:
                    # 回填 finish 的 tool message，保证消息序列合法（下轮门禁反馈直接可续）
                    self.messages.append(
                        self.llm.tool_message(call["id"], "[引擎] finish 已收到，正在执行门禁审核…")
                    )
                    self._record({"role": "tool", "name": call["name"], "event": "finish"})
                    raise
                self._record({"role": "tool", "name": call["name"], "result": result[:4000]})
                self.messages.append(self.llm.tool_message(call["id"], result))
