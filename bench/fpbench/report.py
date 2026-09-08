"""Turn run summaries into a readable verdict.

The scoreboard is the least interesting half. What tells you whether a model
can carry FoodPlanner is the failure list: a model that scores 70% by being
fluent but invents drive_file_ids is unusable, while one that scores 55% and
never fabricates is a candidate.
"""
from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

# Suites that gate viability: failing these means the model cannot drive the
# connector safely, no matter how well it writes.
CRITICAL = {"tool_choice", "no_fabrication", "adherence"}


def _bar(score: float, width: int = 18) -> str:
    filled = round(score * width)
    return "#" * filled + "." * (width - filled)


def markdown(summaries: list[dict], repeat: int = 1) -> str:
    out: list[str] = []
    out.append("# FoodPlanner local-model bench\n")
    out.append(f"Models: {len(summaries)} · runs per case: {repeat}\n")

    all_suites: list[str] = []
    for s in summaries:
        for name in s["suites"]:
            if name not in all_suites:
                all_suites.append(name)

    # Scoreboard
    out.append("\n## Scoreboard\n")
    head = "| Suite | " + " | ".join(s["model"] for s in summaries) + " |"
    out.append(head)
    out.append("|" + "---|" * (len(summaries) + 1))
    for suite in all_suites:
        crit = " **(gate)**" if suite in CRITICAL else ""
        cells = []
        for s in summaries:
            v = s["suites"].get(suite)
            cells.append("-" if v is None else f"{v:.0%}")
        out.append(f"| {suite}{crit} | " + " | ".join(cells) + " |")
    out.append("| **overall** | " + " | ".join(f"**{s['overall']:.0%}**" for s in summaries) + " |")

    # Cost of running it
    out.append("\n## Speed and context\n")
    out.append("| Model | median latency | p95 | max prompt tokens | completion tokens |")
    out.append("|---|---|---|---|---|")
    for s in summaries:
        out.append(f"| {s['model']} | {s['latency_median_s']}s | {s['latency_p95_s']}s | "
                   f"{s['prompt_tokens_max']} | {s['completion_tokens_total']} |")

    # Per-model detail
    for s in summaries:
        out.append(f"\n## {s['model']}\n")
        for note in s.get("notes", []):
            out.append(f"> {note}\n")
        for suite in all_suites:
            if suite not in s["suites"]:
                continue
            out.append(f"\n### {suite} — {s['suites'][suite]:.0%} `{_bar(s['suites'][suite])}`\n")
            fails = [r for r in s["results"] if r["suite"] == suite and not r["passed"]]
            if not fails:
                out.append("All cases passed.\n")
                continue
            # With repeats, a case that fails sometimes is a different problem
            # from one that always fails: say which.
            counts = Counter(r["case_id"] for r in fails)
            seen: set[str] = set()
            for r in fails:
                if r["case_id"] in seen:
                    continue
                seen.add(r["case_id"])
                n = counts[r["case_id"]]
                flaky = f" (failed {n}/{repeat} runs)" if repeat > 1 else ""
                out.append(f"- **{r['case_id']}**{flaky}")
                for f in r["failures"]:
                    out.append(f"  - {f}")
                if r["tools_called"]:
                    out.append(f"  - called: `{', '.join(r['tools_called'])}`")
                elif r["reply_preview"]:
                    out.append(f"  - said: {r['reply_preview'][:180]!r}")

    # Verdict
    out.append("\n## Verdict\n")
    for s in summaries:
        gates = {k: v for k, v in s["suites"].items() if k in CRITICAL}
        if not gates:
            out.append(f"- **{s['model']}**: overall {s['overall']:.0%}. No gate suite ran "
                       f"({', '.join(sorted(CRITICAL))}), so this says nothing about whether the "
                       f"model is safe to drive the connector.")
            continue
        worst_name = min(gates, key=gates.get)
        worst = gates[worst_name]
        if worst >= 0.9 and s["overall"] >= 0.8:
            v = "viable, drives the connector correctly on this sample"
        elif worst >= 0.7:
            v = "promising but not safe yet, the gate suites still fail"
        else:
            v = "not viable as the connector's model, it misroutes or fabricates"
        out.append(f"- **{s['model']}**: {v} (overall {s['overall']:.0%}, "
                   f"weakest gate `{worst_name}` at {worst:.0%})")
    return "\n".join(out) + "\n"


def write(summaries: list[dict], out_dir: Path, repeat: int = 1, stamp: str = "latest") -> tuple[Path, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    md_path = out_dir / f"report-{stamp}.md"
    json_path = out_dir / f"results-{stamp}.json"
    md_path.write_text(markdown(summaries, repeat), encoding="utf-8")
    json_path.write_text(json.dumps(summaries, ensure_ascii=False, indent=2), encoding="utf-8")
    return md_path, json_path
