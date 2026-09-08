"""One thin client for any OpenAI-compatible chat endpoint.

Ollama, LM Studio, llama.cpp server, vLLM, TGI and the hosted Chinese and US
APIs all speak this shape, so the bench needs no per-runtime adapters. What it
DOES need to absorb, because local runtimes differ wildly here:

  * reasoning models that emit <think>...</think> in `content`, or put it in a
    separate `reasoning_content` field (GLM, Qwen3, DeepSeek-R1)
  * runtimes with no native tool calling, which need the tool list injected as
    text and the call parsed back out of prose
  * runtimes that advertise tools but return the call as a JSON blob in
    `content` anyway

Every response comes back as a ToolAwareReply so graders never care which of
those happened -- but `tool_mode_used` records it, because "this model only
works in prompted mode" is itself a finding.
"""
from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from typing import Any

import requests

THINK_RE = re.compile(r"<think>.*?</think>|<thinking>.*?</thinking>", re.S | re.I)
FENCE_RE = re.compile(r"```(?:json|tool_call)?\s*(.*?)```", re.S)


@dataclass
class ToolCall:
    name: str
    args: dict


@dataclass
class Reply:
    text: str = ""                      # content with reasoning stripped
    raw_text: str = ""                  # content as returned
    reasoning: str = ""                 # think-block / reasoning_content
    tool_calls: list[ToolCall] = field(default_factory=list)
    prompt_tokens: int = 0
    completion_tokens: int = 0
    latency_s: float = 0.0
    tool_mode_used: str = "none"        # native | prompted | none
    error: str | None = None
    finish_reason: str = ""

    @property
    def tokens_per_s(self) -> float:
        return self.completion_tokens / self.latency_s if self.latency_s > 0 else 0.0

    @property
    def tool_names(self) -> list[str]:
        return [c.name for c in self.tool_calls]


PROMPTED_TOOLS_HEADER = """You can call tools. The available tools are listed below as JSON schemas.

When you want to call a tool, reply with ONLY a JSON object of this exact shape and nothing else:
{"tool_calls": [{"name": "<tool name>", "arguments": {<arguments object>}}]}

You may put several calls in the array. If no tool is needed, reply normally in plain text.

TOOLS:
"""


class ChatClient:
    def __init__(self, cfg: dict):
        self.name: str = cfg["name"]
        self.base_url: str = cfg["base_url"].rstrip("/")
        self.model: str = cfg["model"]
        self.api_key: str | None = cfg.get("api_key")
        self.context: int = int(cfg.get("context") or 0)
        self.temperature: float = cfg.get("temperature", 0.2)
        self.max_tokens: int = cfg.get("max_tokens", 2048)
        self.timeout_s: int = cfg.get("timeout_s", 300)
        self.supports_tools: bool = cfg.get("supports_tools", True)
        self.strip_think: bool = cfg.get("strip_think", True)
        self.extra_body: dict = cfg.get("extra_body") or {}

    # ---------------------------------------------------------------- request

    def chat(self, messages: list[dict], tools: list[dict] | None = None,
             temperature: float | None = None, max_tokens: int | None = None) -> Reply:
        msgs = [dict(m) for m in messages]
        mode = "none"

        if tools:
            if self.supports_tools:
                mode = "native"
            else:
                mode = "prompted"
                schema = json.dumps([t["function"] for t in tools], ensure_ascii=False, indent=None)
                msgs.insert(0, {"role": "system", "content": PROMPTED_TOOLS_HEADER + schema})

        body: dict[str, Any] = {
            "model": self.model,
            "messages": msgs,
            "temperature": self.temperature if temperature is None else temperature,
            "max_tokens": self.max_tokens if max_tokens is None else max_tokens,
            "stream": False,
        }
        if tools and mode == "native":
            body["tools"] = tools
        body.update(self.extra_body)

        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        t0 = time.perf_counter()
        try:
            r = requests.post(f"{self.base_url}/chat/completions", json=body,
                              headers=headers, timeout=self.timeout_s)
        except requests.RequestException as e:
            return Reply(error=f"{type(e).__name__}: {e}", latency_s=time.perf_counter() - t0,
                         tool_mode_used=mode)
        latency = time.perf_counter() - t0

        if r.status_code >= 400:
            detail = r.text[:400].replace("\n", " ")
            # A runtime that rejects the tools field is a finding, not a crash:
            # retry once in prompted mode so the suite still produces a score.
            if tools and mode == "native" and self._looks_like_tool_rejection(r.status_code, detail):
                self.supports_tools = False
                out = self.chat(messages, tools, temperature, max_tokens)
                out.error = (out.error or "") or None
                return out
            return Reply(error=f"HTTP {r.status_code}: {detail}", latency_s=latency,
                         tool_mode_used=mode)

        try:
            data = r.json()
        except ValueError:
            return Reply(error=f"non-JSON response: {r.text[:300]}", latency_s=latency,
                         tool_mode_used=mode)

        return self._parse(data, latency, mode)

    @staticmethod
    def _looks_like_tool_rejection(status: int, detail: str) -> bool:
        d = detail.lower()
        return status in (400, 422, 501) and ("tool" in d or "function" in d)

    # ----------------------------------------------------------------- parse

    def _parse(self, data: dict, latency: float, mode: str) -> Reply:
        try:
            choice = data["choices"][0]
            msg = choice.get("message") or {}
        except (KeyError, IndexError):
            return Reply(error=f"unexpected response shape: {json.dumps(data)[:300]}",
                         latency_s=latency, tool_mode_used=mode)

        raw = msg.get("content") or ""
        reasoning = msg.get("reasoning_content") or msg.get("reasoning") or ""

        text = raw
        if self.strip_think:
            found = THINK_RE.findall(raw)
            if found:
                reasoning = (reasoning + "\n" + "\n".join(found)).strip()
            text = THINK_RE.sub("", raw).strip()

        calls: list[ToolCall] = []
        used = "none"
        for tc in msg.get("tool_calls") or []:
            fn = tc.get("function") or {}
            calls.append(ToolCall(name=fn.get("name", ""), args=_loads(fn.get("arguments"))))
        if calls:
            used = "native"
        else:
            parsed = _tool_calls_from_text(text)
            if parsed:
                calls, used = parsed, "prompted"
                # The JSON blob was the whole answer; don't grade it as prose.
                if text.strip().startswith("{") or FENCE_RE.search(text):
                    text = FENCE_RE.sub("", text).strip()

        usage = data.get("usage") or {}
        return Reply(
            text=text, raw_text=raw, reasoning=reasoning, tool_calls=calls,
            prompt_tokens=int(usage.get("prompt_tokens") or 0),
            completion_tokens=int(usage.get("completion_tokens") or 0),
            latency_s=latency, tool_mode_used=used if calls else mode,
            finish_reason=choice.get("finish_reason") or "",
        )


def _loads(v) -> dict:
    if isinstance(v, dict):
        return v
    if not v:
        return {}
    try:
        out = json.loads(v)
        return out if isinstance(out, dict) else {"_value": out}
    except (ValueError, TypeError):
        return {"_unparsed": str(v)[:500]}


def _tool_calls_from_text(text: str) -> list[ToolCall]:
    """Dig a tool call out of prose. Small models wrap it in a fence, prefix it
    with chatter, or emit a bare {"name": ..., "arguments": ...}."""
    if not text:
        return []
    candidates: list[str] = [m.strip() for m in FENCE_RE.findall(text)]
    stripped = text.strip()
    if stripped.startswith("{") or stripped.startswith("["):
        candidates.append(stripped)
    # Last resort: the first balanced {...} in the text.
    brace = _first_object(text)
    if brace:
        candidates.append(brace)

    for cand in candidates:
        try:
            obj = json.loads(cand)
        except ValueError:
            continue
        calls = _normalize_calls(obj)
        if calls:
            return calls
    return []


def _normalize_calls(obj) -> list[ToolCall]:
    if isinstance(obj, list):
        out: list[ToolCall] = []
        for o in obj:
            out.extend(_normalize_calls(o))
        return out
    if not isinstance(obj, dict):
        return []
    if "tool_calls" in obj:
        return _normalize_calls(obj["tool_calls"])
    name = obj.get("name") or obj.get("tool") or obj.get("function")
    if isinstance(name, dict):                       # {"function": {"name": ...}}
        return _normalize_calls(name)
    if isinstance(name, str):
        args = obj.get("arguments", obj.get("args", obj.get("parameters", {})))
        return [ToolCall(name=name, args=_loads(args))]
    return []


def _first_object(text: str) -> str | None:
    start = text.find("{")
    if start == -1:
        return None
    depth, in_str, esc = 0, False, False
    for i, ch in enumerate(text[start:], start):
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return text[start:i + 1]
    return None
