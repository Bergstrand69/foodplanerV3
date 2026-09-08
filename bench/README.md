# FoodPlanner local-model bench

Can a local model do FoodPlanner's AI work instead of Claude?

This is not a generic LLM benchmark. Every case is a real thing FoodPlanner 2.0
asks the model to do, graded against the same contracts the server enforces:
the 29 MCP tools verbatim from `router.ts`, the store-category taxonomy from
`categories.ts`, the gram conversions `set_recipe_nutrition` demands, and a
Python port of the `customize_ui` layout validator. A model that scores well
here can drive the connector. A model that scores well on MMLU tells you
nothing about that.

## Quick start

```bash
pip install -r requirements.txt
python make_fixtures.py          # once, builds the long-context fixtures
python selftest.py               # verify the graders before trusting scores

# point models.yaml at your runtime, then:
python run_bench.py --probe      # is the endpoint alive?
python run_bench.py              # full run, writes results/report-latest.md
```

Any OpenAI-compatible endpoint works: Ollama (`:11434/v1`), LM Studio
(`:1234/v1`), llama.cpp server (`:8080/v1`), vLLM (`:8000/v1`), or a hosted API.
Set `supports_tools: false` for a runtime with no native function calling and
the bench injects the tool list as text and parses the call back out, recording
which mode was used.

```bash
python run_bench.py --list                       # every suite and case, calls nothing
python run_bench.py --suites tool_choice,no_fabrication
python run_bench.py --cases ui-shopping-basic    # exactly one case
python run_bench.py --cases nutrition,'lc-*'     # substring and glob both work
python run_bench.py --models glm-4.7-flash --suites categorize
python run_bench.py --repeat 3                   # 3 runs per case, flakiness reported
```

`--suites`, `--cases` and `--models` combine, and `--list` accepts the same
filters so you can see what a selection covers before spending the time on it.
Iterating on one case is the normal loop: a single `ui_layout` case takes
seconds, the full 50 take a while.

## The suites

FoodPlanner 3.0 draws a line: the AI only does what a script genuinely cannot
(see `../docs/ai-vs-script.md`). The suites are grouped by which side they fall
on.

**The AI's actual job** — these decide whether a local model is good enough:

| Suite | What it decides | Cases |
|---|---|---|
| `recipe_extract` | Messy source into `save_recipe`: TikTok caption, video transcript, OCR'd card, comment thread | 6 |
| `extract_direct` | The same sources asked as a scripted JSON call, no tools | 5 |
| `categorize` | Novel items into the fixed store taxonomy (off-taxonomy = hard fail) | 7 |
| `categorize_direct` | The same items asked as a scripted JSON call, no tools | 7 |

Each pair separates *"does not know the answer"* from *"knows, but will not
emit a tool call"*. Those need completely different fixes, and only the first
is about model quality.

**The chatbot still drives the app** — gates on trust, not capability:

| Suite | What it decides | Cases |
|---|---|---|
| `tool_choice` | Does it route to the right tool with 29 verbose schemas in front of it? | 13 |
| `no_fabrication` | **Gate.** Does it invent drive_file_ids, item_keys, kcal figures, or tools? | 7 |
| `adherence` | **Gate.** Does it *act* on what the tool descriptions say, or just read them? | 7 |
| `plan_week` | Constraint satisfaction across a week, with real recipe ids | 3 |
| `long_context` | Where a 64k window starts dropping detail | 5 |

**Work 3.0 intends to move into a script** — kept as evidence, not as a target:

| Suite | Status | Cases |
|---|---|---|
| `nutrition_map` | **Demoted.** Grams-per-unit is a density table, not a judgement. A poor score here confirms the plan rather than blocking it. | 3 |
| `ui_layout` | Only relevant if 3.0 keeps generative UI; a normal app does not need it. | 6 |

Three suites are marked **gates** in the report. Failing them means the model
cannot be trusted with the connector regardless of its overall score, because
FoodPlanner writes to the user's own Google Drive: a `swap_meal` with an
invented id, a `check_off_item` with a guessed key, or a protein figure stated
as fact when `nutrition_status` said `missing` all either corrupt real data or
mislead a real person.

## Why these cases

They come from the roadmap and the gotchas skill, not from imagination:

- **G-016** — a status field in a tool result does not make a model act on it.
  `ad-cost-fixed-silently` checks it fixes cost without asking;
  `ad-import-asks-before-nutrition` checks it *does* ask before the expensive
  nutrition path (task-064 made that opt-in).
- **G-014 / G-020** — a custom layout that drops a required intent silently
  removes a feature, and an optional data section is invisible forever. The
  `ui_layout` suite fails any recipe template without `{{#nutrition}}` and any
  week template without `data-fp-assign`.
- **task-062** — there is deliberately no name→category table in the code, so
  the model *is* the classifier. `categorize` measures that directly.
- **task-049** — 2.0 split nutrition into a messy half (which food, how many
  grams) and an exact half (lookup, scale, sum). `nm-grams-conversion` checks
  2 dl flour is ~120 g, not 200. GLM-4.7-Flash answered 200, which is precisely
  why 3.0 moves gram conversion into a density table instead of buying a bigger
  model to guess better.

## Reading the report

`results/report-latest.md` has a scoreboard, a speed/context table, then the
failure detail per suite. The scoreboard is the least useful part. The failure
list is the point: a model that scores 70% by being fluent while inventing
recipe ids is unusable, and one that scores 55% while never fabricating is a
candidate you can prompt-engineer forward.

With `--repeat 3` a case that fails 1/3 runs is marked flaky, which matters at
temperature > 0: an intermittent fabrication is still a fabrication.

The context note is worth reading too. The tool payload alone is **~6.6k
tokens** before a single message, and `lc-needle-32k` reaches ~54k against a
64k window. Real conversations add turn history on top of both.

## Layout

```
bench/
  run_bench.py           CLI
  make_fixtures.py       builds the long-context item books
  selftest.py            tests the graders and the whole pipeline (no model needed)
  models.yaml            endpoints under test
  tasks/*.yaml           the suites
  fixtures/              generated long-context payloads
  fpbench/
    fp_tools.py          the 29 MCP tools, verbatim from router.ts
    client.py            OpenAI-compatible client (+ think-stripping, prompted tools)
    layout_validator.py  Python port of uiLayoutValidator.ts
    graders.py           deterministic checks
    suite.py             load, run, score
    report.py            markdown + JSON output
```

## Adding a case

Task files are YAML. A case is a whole conversation: `context` replays the tool
results FoodPlanner would really have returned by that point, so the model is
graded on its *next move* rather than on a cold prompt.

```yaml
- id: my-case
  context:
    - role: tool_result
      tool: get_recipe
      result: {drive_file_id: "r_x", title: "...", cost_status: {status: "missing"}}
  user: "Visa receptet"
  grade:
    tool: set_item_prices        # first call must be this
    forbid_tools: [search_livsmedel]
    swedish: true
```

Add `user_first: true` when the replayed tool result *is* the answer to the
user's request. Without it the request lands after the result, which reads as
"show me that again" and grades the model on the wrong move.

Available checks are the keys of `DISPATCH` in `graders.py`: `tool`, `tool_in`,
`forbid_tools`, `no_tool`, `real_tool_names`, `args_contain`, `args_equal`,
`arg_absent`, `ids_from_set`, `categories`, `grams`, `search_terms`,
`recipe_json`, `placeholders`, `layout`, `plan`, `swedish`, `no_em_dash`,
`max_chars`, `contains_none`, `contains_any`, `asks_question`, `regex`,
`regex_absent`, `tool_call_count`, `ingredients_include`, `ingredients_exclude`,
`ingredient_count`, `field_between`. A case passes only when every check passes.

## Keeping it honest

Two files mirror FoodPlanner 2.0 and will drift if that repo changes:
`fpbench/fp_tools.py` (the tool array in `router.ts`) and
`fpbench/layout_validator.py` (`uiLayoutValidator.ts`). The validator port has a
drift check:

```bash
python -m fpbench.layout_validator ../../FoodPlanner2.0/server/src/uiLayoutValidator.ts
```

Grading is deliberately deterministic end to end. There is no LLM judge, so a
score cannot drift with a judge model's mood, and every failure names the
specific breach rather than a rubric number.
