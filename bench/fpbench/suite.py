"""Load task files, build the conversation each case needs, run it, grade it."""
from __future__ import annotations

import json
import statistics
import time
from dataclasses import dataclass, field, asdict
from fnmatch import fnmatch
from pathlib import Path

import yaml

from .client import ChatClient, Reply
from .fp_tools import PROJECT_INSTRUCTIONS, openai_tools
from .graders import grade


@dataclass
class CaseResult:
    case_id: str
    suite: str
    passed: bool
    failures: list[str]
    latency_s: float
    prompt_tokens: int
    completion_tokens: int
    tool_mode: str
    tools_called: list[str]
    reply_preview: str
    run: int = 0


@dataclass
class ModelResult:
    model: str
    cases: list[CaseResult] = field(default_factory=list)
    started: str = ""
    notes: list[str] = field(default_factory=list)

    def by_suite(self) -> dict[str, list[CaseResult]]:
        out: dict[str, list[CaseResult]] = {}
        for c in self.cases:
            out.setdefault(c.suite, []).append(c)
        return out

    def score(self, cases: list[CaseResult] | None = None) -> float:
        cs = self.cases if cases is None else cases
        return sum(c.passed for c in cs) / len(cs) if cs else 0.0


def load_suites(tasks_dir: Path, only: list[str] | None = None,
                cases: list[str] | None = None) -> list[dict]:
    """`only` filters by suite name, `cases` by case id. A case pattern may be a
    full id, a substring, or a glob ('ui-*', '*nutrition*'); a suite that keeps
    no cases drops out entirely."""
    suites = []
    for path in sorted(tasks_dir.glob("*.yaml")):
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        if not data:
            continue
        data["_path"] = str(path)
        if only and data["suite"] not in only:
            continue
        if cases:
            kept = [c for c in data["cases"] if _matches(c["id"], cases)]
            if not kept:
                continue
            data["cases"] = kept
        suites.append(data)
    return suites


def _matches(case_id: str, patterns: list[str]) -> bool:
    for p in patterns:
        p = p.strip()
        if not p:
            continue
        if any(ch in p for ch in "*?["):
            if fnmatch(case_id, p):
                return True
        elif p.casefold() in case_id.casefold():
            return True
    return False


def build_messages(suite: dict, case: dict) -> list[dict]:
    """A case is a whole conversation. `context` entries replay the tool results
    FoodPlanner would really have returned by that point, so the model is graded
    on the next move rather than on a cold prompt."""
    msgs: list[dict] = []

    system = case.get("system", suite.get("system"))
    if system == "project":
        msgs.append({"role": "system", "content": PROJECT_INSTRUCTIONS})
    elif system:
        msgs.append({"role": "system", "content": system})

    # Where the user's request belongs depends on what triggers the answer.
    # When the replayed tool result IS the answer to their request, the request
    # has to come first, or the conversation reads as "show me that again" and
    # the model is graded on the wrong move.
    user_first = case.get("user_first", False)
    if user_first:
        msgs.append({"role": "user", "content": case["user"]})

    for turn in suite.get("context", []) + case.get("context", []):
        role = turn.get("role")
        if role == "tool_result":
            # Replayed as a plain assistant/user pair: local runtimes disagree on
            # the tool-role message shape, and this keeps the payload identical
            # across every runtime the bench talks to.
            msgs.append({"role": "assistant",
                         "content": f"[called {turn['tool']}]"})
            if "result_file" in turn:
                body = (Path(suite["_path"]).parent.parent / turn["result_file"]).read_text(
                    encoding="utf-8")
            else:
                body = turn["result"]
            if not isinstance(body, str):
                body = json.dumps(body, ensure_ascii=False, indent=1)
            msgs.append({"role": "user",
                         "content": f"[result of {turn['tool']}]\n{body}"})
        else:
            msgs.append({"role": role, "content": turn["content"]})

    if not user_first:
        msgs.append({"role": "user", "content": case["user"]})
    return msgs


def run_case(client: ChatClient, suite: dict, case: dict, run_idx: int) -> CaseResult:
    mode = case.get("mode", suite.get("mode", "tools"))
    subset = case.get("tools", suite.get("tools"))
    tools = openai_tools(subset) if mode == "tools" else None

    msgs = build_messages(suite, case)
    reply: Reply = client.chat(
        msgs, tools,
        temperature=case.get("temperature", suite.get("temperature")),
        max_tokens=case.get("max_tokens", suite.get("max_tokens")),
    )
    passed, failures = grade(reply, case["grade"])

    return CaseResult(
        case_id=case["id"], suite=suite["suite"], passed=passed, failures=failures,
        latency_s=round(reply.latency_s, 2),
        prompt_tokens=reply.prompt_tokens, completion_tokens=reply.completion_tokens,
        tool_mode=reply.tool_mode_used, tools_called=reply.tool_names,
        reply_preview=(reply.text or "")[:300], run=run_idx,
    )


def run_model(cfg: dict, suites: list[dict], repeat: int = 1, verbose: bool = True) -> ModelResult:
    client = ChatClient(cfg)
    res = ModelResult(model=client.name, started=time.strftime("%Y-%m-%d %H:%M:%S"))

    total = sum(len(s["cases"]) for s in suites) * repeat
    done = 0
    for suite in suites:
        for case in suite["cases"]:
            for run_idx in range(repeat):
                cr = run_case(client, suite, case, run_idx)
                res.cases.append(cr)
                done += 1
                if verbose:
                    mark = "PASS" if cr.passed else "FAIL"
                    tail = "" if cr.passed else "  <- " + (cr.failures[0][:110] if cr.failures else "")
                    print(f"  [{done:>3}/{total}] {mark} {suite['suite']}/{cr.case_id}"
                          f" {cr.latency_s:>6.1f}s{tail}", flush=True)

    if client.context:
        worst = max((c.prompt_tokens for c in res.cases), default=0)
        if worst and worst > client.context * 0.5:
            res.notes.append(
                f"largest prompt was {worst} tokens against a {client.context}-token window "
                f"({worst / client.context:.0%}) -- real conversations add history on top of this")
    modes = {c.tool_mode for c in res.cases if c.tool_mode != "none"}
    if "prompted" in modes:
        res.notes.append("some or all tool calls arrived as prompted JSON, not native tool_calls")
    return res


def summarize(res: ModelResult) -> dict:
    lat = [c.latency_s for c in res.cases if c.latency_s > 0]
    return {
        "model": res.model,
        "started": res.started,
        "overall": round(res.score(), 4),
        "cases": len(res.cases),
        "suites": {name: round(res.score(cs), 4) for name, cs in res.by_suite().items()},
        "latency_median_s": round(statistics.median(lat), 2) if lat else 0,
        "latency_p95_s": round(sorted(lat)[int(len(lat) * 0.95) - 1], 2) if len(lat) >= 2 else 0,
        "prompt_tokens_max": max((c.prompt_tokens for c in res.cases), default=0),
        "completion_tokens_total": sum(c.completion_tokens for c in res.cases),
        "notes": res.notes,
        "results": [asdict(c) for c in res.cases],
    }
