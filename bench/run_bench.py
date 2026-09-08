#!/usr/bin/env python
"""FoodPlanner local-model bench.

    python run_bench.py                          # every model in models.yaml, every suite
    python run_bench.py --models glm-4.7-flash   # one model
    python run_bench.py --suites tool_choice,adherence
    python run_bench.py --cases ui-shopping-basic        # one case
    python run_bench.py --cases nutrition,'lc-*'         # substring and glob
    python run_bench.py --repeat 3               # 3 runs per case, flakiness reported
    python run_bench.py --list                   # show suites and cases, call nothing
    python run_bench.py --probe                  # check the endpoint answers, then stop
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).parent))

from fpbench import report                                   # noqa: E402
from fpbench.client import ChatClient                        # noqa: E402
from fpbench.suite import load_suites, run_model, summarize  # noqa: E402

ROOT = Path(__file__).parent


def load_models(path: Path, only: list[str] | None) -> list[dict]:
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    defaults = raw.get("defaults") or {}
    out = []
    for m in raw.get("models") or []:
        cfg = {**defaults, **m}
        if only and cfg["name"] not in only:
            continue
        if cfg.get("api_key_env"):
            import os
            cfg["api_key"] = os.environ.get(cfg["api_key_env"])
        out.append(cfg)
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--models-file", default=str(ROOT / "models.yaml"))
    ap.add_argument("--tasks-dir", default=str(ROOT / "tasks"))
    ap.add_argument("--out-dir", default=str(ROOT / "results"))
    ap.add_argument("--models", help="comma-separated model names from models.yaml")
    ap.add_argument("--suites", help="comma-separated suite names")
    ap.add_argument("--cases", help="comma-separated case ids, substrings or globs "
                                    "(e.g. ui-shopping-basic, nutrition, 'lc-*')")
    ap.add_argument("--repeat", type=int, default=1, help="runs per case (variance check)")
    ap.add_argument("--list", action="store_true", help="list suites and cases, then exit")
    ap.add_argument("--probe", action="store_true", help="one hello call per model, then exit")
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args()

    only_models = [s.strip() for s in args.models.split(",")] if args.models else None
    only_suites = [s.strip() for s in args.suites.split(",")] if args.suites else None
    only_cases = [s.strip() for s in args.cases.split(",")] if args.cases else None

    suites = load_suites(Path(args.tasks_dir), only_suites, only_cases)
    if not suites:
        what = []
        if only_suites:
            what.append(f"suites {only_suites}")
        if only_cases:
            what.append(f"cases {only_cases}")
        print(f"Nothing matched {' and '.join(what) or args.tasks_dir}. "
              f"Run --list to see what exists.", file=sys.stderr)
        return 1

    if args.list:
        for s in suites:
            print(f"\n{s['suite']}  ({len(s['cases'])} cases)  {s.get('description','')}")
            for c in s["cases"]:
                print(f"    {c['id']:<28} {c['user'][:78]}")
        total = sum(len(s["cases"]) for s in suites)
        print(f"\n{len(suites)} suites, {total} cases")
        print("\nSelect with:  --suites <name,...>   --cases <id|substring|glob,...>")
        return 0

    models = load_models(Path(args.models_file), only_models)
    if not models:
        print("No models selected. Check models.yaml and --models.", file=sys.stderr)
        return 1

    if args.probe:
        for cfg in models:
            c = ChatClient(cfg)
            print(f"{c.name} -> {c.base_url} ({c.model})")
            r = c.chat([{"role": "user", "content": "Svara med exakt ordet: OK"}], None)
            if r.error:
                print(f"  UNREACHABLE: {r.error}")
            else:
                print(f"  {r.latency_s:.1f}s  {r.completion_tokens} tok  reply={r.text.strip()[:60]!r}")
        return 0

    stamp = time.strftime("%Y%m%d-%H%M%S")
    summaries = []
    for cfg in models:
        print(f"\n=== {cfg['name']} ({cfg['model']} @ {cfg['base_url']}) ===", flush=True)
        res = run_model(cfg, suites, repeat=args.repeat, verbose=not args.quiet)
        s = summarize(res)
        summaries.append(s)
        print(f"--- {cfg['name']}: {s['overall']:.0%} overall, "
              f"median {s['latency_median_s']}s/case", flush=True)

    md, js = report.write(summaries, Path(args.out_dir), repeat=args.repeat, stamp=stamp)
    report.write(summaries, Path(args.out_dir), repeat=args.repeat, stamp="latest")
    print(f"\nReport: {md}\nRaw:    {js}")
    print("\n" + "\n".join(report.markdown(summaries, args.repeat).split("## Verdict")[-1].strip().split("\n")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
