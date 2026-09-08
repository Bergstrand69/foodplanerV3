#!/usr/bin/env python
"""Tests the bench itself, so a score can be trusted.

Two halves:
  1. Unit checks on the graders and the layout validator, with known-good and
     known-bad inputs. A grader that passes everything is worse than no bench.
  2. A mock OpenAI endpoint that plays a scripted model, so the whole pipeline
     (client -> suite -> grade -> report) runs without any local model up.

    python selftest.py
"""
from __future__ import annotations

import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from fpbench.client import Reply, ToolCall, _tool_calls_from_text   # noqa: E402
from fpbench.graders import grade                                    # noqa: E402
from fpbench.layout_validator import validate                        # noqa: E402

FAILS: list[str] = []


def ok(cond, label):
    print(("  ok   " if cond else "  FAIL ") + label)
    if not cond:
        FAILS.append(label)


def reply(text="", calls=None):
    return Reply(text=text, tool_calls=[ToolCall(n, a) for n, a in (calls or [])])


# ---------------------------------------------------------------- validator

GOOD_SHOPPING = """
<ul class="list">
  {{#items}}
  <li><input type="checkbox" data-fp-check="{{item_key}}"> {{quantity}} {{unit}} {{name}}
      <button data-fp-remove="{{item_key}}">x</button></li>
  {{/items}}
  {{^items}}<p>Listan är tom</p>{{/items}}
</ul>
<form data-fp-add><input name="q" placeholder="Lägg till"></form>
"""

GOOD_RECIPE = """
<h1>{{title}}</h1>
{{#servings}}<p>{{current}} av {{base}} portioner</p>{{/servings}}
{{#nutrition}}<p>{{energy_kcal}} kcal, {{protein_g}} g protein ({{source}})</p>{{/nutrition}}
<ul>{{#ingredients}}<li data-fp-ingredient-check="{{index}}">{{display}} {{name}}</li>{{/ingredients}}</ul>
<ol>{{#steps}}<li>{{content}}</li>{{/steps}}</ol>
<button data-fp-cookmode>Koka</button>
"""

GOOD_WEEK = """
{{#days}}
  <div><h3>{{day_name}}</h3>
  {{#meals}}<p>{{meal_sv}}: {{title}}</p>{{/meals}}
  {{^meals}}<em>tomt</em>{{/meals}}
  <button data-fp-assign="{{day}}:dinner">+</button></div>
{{/days}}
"""

print("layout validator")
ok(validate("shopping", GOOD_SHOPPING).ok, "accepts a valid shopping layout")
ok(validate("recipe", GOOD_RECIPE).ok, "accepts a valid recipe layout")
ok(validate("week", GOOD_WEEK).ok, "accepts a valid week layout")

ok(not validate("shopping", GOOD_SHOPPING.replace('data-fp-check="{{item_key}}"', "")).ok,
   "rejects shopping without data-fp-check")
ok(not validate("shopping", GOOD_SHOPPING.replace("<form data-fp-add>", "<form>")).ok,
   "rejects shopping without data-fp-add")
ok(not validate("shopping", GOOD_SHOPPING.replace("{{name}}", "{{brand}}")).ok,
   "rejects an unknown field")
ok(not validate("shopping", GOOD_SHOPPING + "<script>alert(1)</script>").ok,
   "rejects a script tag")
ok(not validate("shopping", GOOD_SHOPPING + '<img src="https://x.com/a.png">').ok,
   "rejects an external URL")
ok(not validate("shopping", GOOD_SHOPPING.replace("data-fp-remove", "data-fp-category")).ok,
   "rejects an invented data-fp-* intent")
ok(not validate("recipe", GOOD_RECIPE.replace(
    "{{#nutrition}}<p>{{energy_kcal}} kcal, {{protein_g}} g protein ({{source}})</p>{{/nutrition}}", "")).ok,
   "rejects a recipe layout with no {{#nutrition}} (task-064)")
ok(not validate("week", GOOD_WEEK.replace('data-fp-assign="{{day}}:dinner"', "")).ok,
   "rejects a week layout with no data-fp-assign (G-014)")
ok(not validate("shopping", "{{#items}}<li>{{name}}</li>").ok, "rejects an unclosed section")
ok(not validate("recipe", GOOD_RECIPE + "{{#items}}{{name}}{{/items}}").ok,
   "rejects a shopping section inside the recipe view")

# ------------------------------------------------------------------ graders

print("\ngraders")
ok(grade(reply(calls=[("open_app", {})]), {"tool": "open_app"})[0], "tool: match")
ok(not grade(reply(calls=[("search_recipes", {})]), {"tool": "open_app"})[0], "tool: mismatch fails")
ok(not grade(reply(text="Visst!"), {"tool": "open_app"})[0], "tool: no call fails")
ok(not grade(reply(calls=[("get_shopping_list", {})]), {"real_tool_names": True})[0],
   "real_tool_names: invented tool fails")
ok(grade(reply(calls=[("swap_meal", {"drive_file_id": "r_a"})]),
         {"ids_from_set": {"known": ["r_a"], "paths": ["drive_file_id"]}})[0],
   "ids_from_set: known id passes")
ok(not grade(reply(calls=[("swap_meal", {"drive_file_id": "r_invented"})]),
             {"ids_from_set": {"known": ["r_a"], "paths": ["drive_file_id"]}})[0],
   "ids_from_set: invented id fails")
ok(not grade(reply(calls=[("swap_meal", {"day": "onsdag"})]), {"forbid_tools": ["swap_meal"]})[0],
   "forbid_tools fires")

# grams: within tolerance passes, a 1 g/ml assumption for flour fails
maps_ok = [("set_recipe_nutrition", {"mappings": [{"ingredient_index": 0, "livsmedel_id": 1, "grams": 118}]})]
maps_bad = [("set_recipe_nutrition", {"mappings": [{"ingredient_index": 0, "livsmedel_id": 1, "grams": 200}]})]
ok(grade(reply(calls=maps_ok), {"grams": {"gold": {"0": 120}, "tol_pct": 30}})[0],
   "grams: 118 g for 2 dl flour passes")
ok(not grade(reply(calls=maps_bad), {"grams": {"gold": {"0": 120}, "tol_pct": 30}})[0],
   "grams: 200 g (1 g/ml) for 2 dl flour fails")

# categories: legal-but-wrong is a soft miss, off-taxonomy is a hard failure
cat_good = [("set_item_prices", {"items": [{"name": "yoghurt", "category": "mejeri"}]})]
cat_wrong = [("set_item_prices", {"items": [{"name": "yoghurt", "category": "skafferi"}]})]
cat_illegal = [("set_item_prices", {"items": [{"name": "yoghurt", "category": "dairy"}]})]
ok(grade(reply(calls=cat_good), {"categories": {"yoghurt": "mejeri"}})[0], "categories: correct passes")
ok(not grade(reply(calls=cat_wrong), {"categories": {"yoghurt": "mejeri"}})[0],
   "categories: wrong-but-legal fails")
bad = grade(reply(calls=cat_illegal), {"categories": {"yoghurt": "mejeri"}})
ok(not bad[0] and "off-taxonomy" in bad[1][0], "categories: off-taxonomy names the real problem")

# plan: constraints
plan_calls = [("swap_meal", {"day": "måndag", "drive_file_id": "r_halloumi"}),
              ("swap_meal", {"day": "onsdag", "drive_file_id": "r_linsgryta"})]
ok(grade(reply(calls=plan_calls),
         {"plan": {"days": ["måndag", "onsdag"], "known_ids": ["r_halloumi", "r_linsgryta"],
                   "constraints": {"no_repeats": True,
                                   "day_must_be_one_of": {"onsdag": ["r_linsgryta"]}}}})[0],
   "plan: satisfied constraints pass")
plan_num = [("swap_meal", {"day": 0, "drive_file_id": "r_halloumi"}),
            ("swap_meal", {"day": 2, "drive_file_id": "r_linsgryta"})]
ok(grade(reply(calls=plan_num),
         {"plan": {"days": ["måndag", "onsdag"], "known_ids": ["r_halloumi", "r_linsgryta"],
                   "constraints": {"day_must_be_one_of": {"onsdag": ["r_linsgryta"]}}}})[0],
   "plan: numeric days match Swedish constraint names")
plan_dupe = [("swap_meal", {"day": "måndag", "drive_file_id": "r_a"}),
             ("swap_meal", {"day": "tisdag", "drive_file_id": "r_a"})]
ok(not grade(reply(calls=plan_dupe),
             {"plan": {"days": ["måndag", "tisdag"], "known_ids": ["r_a"],
                       "constraints": {"no_repeats": True}}})[0], "plan: repeated dish fails")

# style
ok(grade(reply(text="Här är veckans lista, 14 varor kvar att handla."), {"swedish": True})[0],
   "swedish: detects Swedish")
ok(not grade(reply(text="Here is your list for the week."), {"swedish": True})[0],
   "swedish: rejects English")
ok(not grade(reply(text="Listan är klar — 14 varor."), {"no_em_dash": True})[0], "no_em_dash fires")
ok(not grade(reply(text="Lax, spenat, grädde och pasta."),
             {"contains_none": ["spenat", "grädde"]})[0], "contains_none catches a restated card")
ok(not grade(reply(text="Den har cirka 32 g protein per portion."),
             {"regex_absent": {"pattern": r"\d+\s*g\s*protein"}})[0],
   "regex_absent catches an invented nutrition figure")
ok(grade(reply(text="Vill du att jag hämtar näringsvärden?"), {"asks_question": True})[0],
   "asks_question: True")
ok(not grade(reply(text="Vill du att jag kategoriserar dem?"), {"asks_question": False})[0],
   "asks_question: False fires when it asks")

# an error never reads as a model failure of a specific kind
e = grade(Reply(error="Connection refused"), {"tool": "open_app"})
ok(not e[0] and "request failed" in e[1][0], "a transport error is reported as such")

# ------------------------------------------------- prompted tool-call parsing

print("\nprompted tool-call parsing")
ok(_tool_calls_from_text('{"tool_calls":[{"name":"open_app","arguments":{}}]}')[0].name == "open_app",
   "bare tool_calls object")
ok(_tool_calls_from_text('```json\n{"name":"search_recipes","arguments":{"query":"lax"}}\n```')[0]
   .args["query"] == "lax", "fenced single call")
ok(_tool_calls_from_text('Visst! {"name":"open_app","arguments":{}} Hoppas det hjälper.')[0]
   .name == "open_app", "call embedded in prose")
ok(_tool_calls_from_text("Jag kan tyvärr inte hjälpa till med det.") == [], "prose is not a call")


# ------------------------------------------------------------- mock endpoint

SCRIPT = {
    "Öppna FoodPlanner": {"tool_calls": [{"id": "1", "type": "function",
                                          "function": {"name": "open_app", "arguments": "{}"}}]},
    "lax": {"tool_calls": [{"id": "1", "type": "function",
                            "function": {"name": "search_recipes",
                                         "arguments": '{"query":"lax"}'}}]},
}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        last = body["messages"][-1]["content"]
        msg = {"role": "assistant", "content": "<think>hmm</think>Här är det."}
        for key, extra in SCRIPT.items():
            if key.casefold() in last.casefold():
                msg.update(extra)
                break
        out = {"choices": [{"message": msg, "finish_reason": "stop"}],
               "usage": {"prompt_tokens": 1200, "completion_tokens": 40}}
        raw = json.dumps(out).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)


print("\nend-to-end against a mock endpoint")
srv = HTTPServer(("127.0.0.1", 0), Handler)
threading.Thread(target=srv.serve_forever, daemon=True).start()
port = srv.server_address[1]

from fpbench import report                              # noqa: E402
from fpbench.suite import load_suites, run_model, summarize  # noqa: E402

suites = load_suites(Path(__file__).parent / "tasks", ["tool_choice"])
res = run_model({"name": "mock", "base_url": f"http://127.0.0.1:{port}/v1", "model": "mock",
                 "supports_tools": True, "strip_think": True, "timeout_s": 20},
                suites, repeat=1, verbose=False)
s = summarize(res)
ok(len(res.cases) == len(suites[0]["cases"]), f"ran all {len(suites[0]['cases'])} tool_choice cases")
ok(any(c.passed for c in res.cases), "the scripted correct answers pass")
ok(any(not c.passed for c in res.cases), "the scripted wrong answers fail")
ok(all(c.reply_preview and "<think>" not in c.reply_preview for c in res.cases),
   "reasoning blocks are stripped before grading")

md = report.markdown([s], 1)
ok("Scoreboard" in md and "Verdict" in md, "report renders scoreboard and verdict")
ok("(gate)" in md, "gate suites are marked in the report")
srv.shutdown()

print(f"\n{'ALL PASS' if not FAILS else str(len(FAILS)) + ' FAILED'}")
for f in FAILS:
    print(f"  - {f}")
raise SystemExit(1 if FAILS else 0)
