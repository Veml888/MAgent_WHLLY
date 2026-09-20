"""FakeLLM：按脚本回放响应，用于引擎单测（不联网）。"""

from __future__ import annotations


class FakeLLM:
    def __init__(self, script: list[dict]):
        self.script = list(script)
        self.calls: list[list[dict]] = []
        self.total_prompt_tokens = 0
        self.total_completion_tokens = 0

    def chat(self, messages, tools=None):
        self.calls.append([dict(m) for m in messages])
        if not self.script:
            raise AssertionError("FakeLLM 脚本已耗尽，但引擎仍在调用")
        return self.script.pop(0)

    def assistant_message(self, raw):
        msg = {"role": "assistant", "content": raw.get("content") or ""}
        if raw.get("tool_calls"):
            msg["tool_calls"] = [
                {
                    "id": c["id"],
                    "type": "function",
                    "function": {"name": c["name"], "arguments": c["arguments"]},
                }
                for c in raw["tool_calls"]
            ]
        return msg

    @staticmethod
    def tool_message(call_id, content):
        return {"role": "tool", "tool_call_id": call_id, "content": content}

    @staticmethod
    def tool_call(idx: int, name: str, args: dict) -> dict:
        return {"id": f"call_{idx}", "name": name, "arguments": __import__("json").dumps(args, ensure_ascii=False)}

    @staticmethod
    def turn(*tool_calls, content=""):
        return {"content": content, "reasoning": "", "tool_calls": list(tool_calls),
                "usage": {"prompt_tokens": 10, "completion_tokens": 5}}
