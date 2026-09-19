"""OpenAI 兼容 LLM 客户端（BYOK：base_url + api_key + model 任意可换）。"""

from __future__ import annotations

import json


class LLMError(RuntimeError):
    """LLM 调用失败（网络、鉴权、限流等）。"""


class ContextLimitError(LLMError):
    """上下文超限：当前会话已装不下，需要收窄任务或压缩历史。"""


class LLMClient:
    def __init__(self, provider: dict):
        self.base_url = provider["base_url"].rstrip("/")
        self.api_key = provider["api_key"]
        self.model = provider["model"]
        self.temperature = provider.get("temperature")
        self.total_prompt_tokens = 0
        self.total_completion_tokens = 0
        self._client = self._build_client()

    def _build_client(self):
        try:
            from openai import OpenAI
        except ImportError as exc:  # pragma: no cover
            raise LLMError("缺少 openai 包，请先 pip install -r requirements.txt") from exc
        return OpenAI(
            base_url=self.base_url,
            api_key=self.api_key,
            max_retries=4,
            timeout=300.0,
        )

    def chat(self, messages: list[dict], tools: list[dict] | None = None) -> dict:
        """发起一次对话补全；返回统一的 {content, tool_calls, usage} 结构。

        tool_calls 元素形如 {"id", "name", "arguments"(str)}，可直接序列化回
        OpenAI 消息格式。
        """
        kwargs: dict = {
            "model": self.model,
            "messages": messages,
        }
        if tools:
            kwargs["tools"] = tools
        if self.temperature is not None:
            kwargs["temperature"] = self.temperature
        try:
            resp = self._client.chat.completions.create(**kwargs)
        except Exception as exc:
            text = str(exc).lower()
            if "context" in text and ("length" in text or "token" in text):
                raise ContextLimitError(f"上下文超限：{exc}") from exc
            raise LLMError(f"模型调用失败：{exc}") from exc

        choice = resp.choices[0].message
        tool_calls = []
        for call in choice.tool_calls or []:
            tool_calls.append(
                {
                    "id": call.id,
                    "name": call.function.name,
                    "arguments": call.function.arguments or "{}",
                }
            )
        usage = getattr(resp, "usage", None)
        if usage is not None:
            self.total_prompt_tokens += getattr(usage, "prompt_tokens", 0) or 0
            self.total_completion_tokens += getattr(usage, "completion_tokens", 0) or 0
        return {
            "content": choice.content or "",
            "tool_calls": tool_calls,
            "usage": {
                "prompt_tokens": self.total_prompt_tokens,
                "completion_tokens": self.total_completion_tokens,
            },
        }

    def ping(self) -> float:
        """测试连通性，返回延迟毫秒；失败抛 LLMError。"""
        import time

        start = time.monotonic()
        self.chat([{"role": "user", "content": "ping，请只回复 pong。"}])
        return round((time.monotonic() - start) * 1000)

    def assistant_message(self, raw: dict) -> dict:
        """把 chat() 的返回转成可回填 messages 的 OpenAI assistant 消息。"""
        msg: dict = {"role": "assistant", "content": raw["content"] or ""}
        if raw["tool_calls"]:
            msg["tool_calls"] = [
                {
                    "id": call["id"],
                    "type": "function",
                    "function": {
                        "name": call["name"],
                        "arguments": call["arguments"],
                    },
                }
                for call in raw["tool_calls"]
            ]
        return msg

    @staticmethod
    def tool_message(call_id: str, content: str) -> dict:
        return {"role": "tool", "tool_call_id": call_id, "content": content}

    @staticmethod
    def dump(obj) -> str:
        return json.dumps(obj, ensure_ascii=False)
