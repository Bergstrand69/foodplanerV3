# Can GLM-4.7-Flash do FoodPlanner's AI work?

**Run:** 2026-09-08 · glm-4.7-flash:latest via Ollama, 64k context, temp 0.2
**Suites:** the four that matter for the 3.0 split (see `ai-vs-script.md`)
**Reproduce:** `python run_bench.py --models glm-4.7-flash --suites categorize,categorize_direct,recipe_extract,extract_direct`

## Verdict

**Yes for recipe parsing. Yes for categorisation, but only as a scripted call,
not as a chatbot tool.**

The model's *knowledge* is good enough for both jobs 3.0 assigns it. What fails
is the plumbing around it, and every failure found here has a fix in code
rather than in a bigger model.

| Suite | Mode | Score |
|---|---|---|
| `recipe_extract` | tool call | **83%** (5/6) |
| `extract_direct` | scripted JSON | 40% (2/5) |
| `categorize` | tool call | **14%** (1/7) |
| `categorize_direct` | scripted JSON | 14% of cases, but **82% of items** (46/56) |

Median 30.7s per case on this hardware.

## Finding 1: recipe parsing works, and it is the strong result

5 of 6, including every deliberately hostile source:

- a TikTok caption with emoji, hashtags and slang quantities ("en rejäl skvätt
  grädde typ 2 dl")
- an OCR'd handwritten card, including reading `vetemj0l` as `vetemjöl`
- a recipe split across a comment thread, correctly ignoring the chatter and
  the follow-up variation ("går det med gula linser?")
- an edit that resent the whole document without losing `notes` or `source_url`

This is the flagship AI feature and a local model handles it. It is also the
job with no scripted alternative, so this is the result that mattered most.

### The one weakness is consistent and fixable

It drops **one trailing ingredient**, every time, in both modes:

| Case | Dropped | Where it appeared in the source |
|---|---|---|
| `rx-video-transcript` | potatis | "servera med kokt potatis" (last line) |
| `xd-comment-thread` | lime | "smaka av med lime" (last line) |
| `xd-tiktok-caption` | olivolja | "fräs vitlöken i olivolja" (inside the method) |

Once the method reads as finished, later mentions stop registering. That is not
a model-quality problem to solve by upgrading; it is a post-processing pass:
scan the source for food words the model did not return and surface them for
one-tap confirmation. Cheap, deterministic, and it turns the app's weakest
extraction moment into a UI affordance.

## Finding 2: categorisation is a plumbing failure, not a knowledge failure

Identical items, two ways of asking:

- **As a chatbot tool (`categorize`): 1/7.** Five of the six failures were
  *"called no tool"* while the model printed a tidy **Kategori** table in
  prose. It knew the answers. It would not emit `set_item_prices`.
- **As a scripted JSON call (`categorize_direct`): 82% of items correct.**

That gap is the whole argument for the 3.0 architecture. Categorisation should
be a direct call from the app with a strict output contract, never a tool the
chatbot decides to invoke.

Per-case pass rates understate this badly, because one bad value fails a whole
case (correctly: 2.0's server rejects the entire `set_item_prices` batch on one
illegal category). Item-level accuracy is the honest number:

| Case | Correct |
|---|---|
| `cd-form-decides-shelf` | 8/8 |
| `cd-brand-names` | 7/8 |
| `cd-novel-products` | 7/8 |
| `cd-basics` | 6/8 |
| `cd-long-batch` (24 items) | 18/24 |
| `cd-edge-cases`, `cd-no-invented-shelf` | no answer (see Finding 4) |

It got `Bregott`, `Wasa Sport`, `Findus ärtor`, `nduja`, `kombucha` and
`surdegsknäcke från Vretstorp` right. A lookup table gets none of those, which
is exactly why this stays an AI job.

## Finding 3: the errors are systematic, so a better prompt should fix them

The mistakes repeat identically across cases:

| Item | Model said | Correct | Pattern |
|---|---|---|---|
| kaffe | dryck | skafferi | classified by what it *becomes* |
| Zoégas Skånerost | dryck | skafferi | same, same run |
| fiskpinnar | kött & fisk | fryst | classified by what it *is made of* |
| kokosmjölk | mejeri | skafferi | "mjölk" in the name |
| havredryck | mejeri | dryck | "dryck" in the name, ignored |

The bias is one thing: it classifies by **what a product is** rather than
**where it sits in a Swedish store**. The prompt says "efter var de står i en
vanlig svensk livsmedelsbutik" but says it once, abstractly.

Next step before considering a bigger model: add 6 to 8 few-shot examples to
the system prompt, chosen to teach exactly this distinction (kaffe → skafferi,
fiskpinnar → fryst, havredryck → dryck). That is a 20-minute change with a
measurable result, and this bench measures it.

`jäst → skafferi` is not really an error: dry yeast is skafferi, fresh is
chilled. That gold answer is ambiguous and should be softened.

## Finding 4: thinking mode is pathological on short prompts

Two cases produced **nothing at all** after burning the full 8000-token budget
and **225 seconds each**:

```
cd-edge-cases        no answer (227.3s, 8000 tok)
cd-no-invented-shelf no answer (225.9s, 8000 tok)
```

Both are trivial: eight items and four items. GLM entered `<think>` and never
came out. Raising the budget from 2048 to 8000 did not help; it just made the
failure more expensive.

This is the most operationally dangerous finding, because it is silent, and
because those two prompts are *easier* than the ones that succeeded. Actions:

1. **Disable thinking for scripted calls.** These are classification tasks with
   no reasoning to do.
2. **Always check `finish_reason == "length"`.** An empty answer at exactly the
   token ceiling is a runaway, not a bad answer. The bench now reports this
   explicitly instead of "no items[] array".
3. **Set a wall-clock timeout well under 225s** and treat a timeout as a retry.

## Recommended architecture

Confirms the split in `ai-vs-script.md`, with the plumbing spelled out:

| Job | How to call it |
|---|---|
| Parse a recipe from a messy source | Direct scripted call. Tool mode also works (83%), so either is viable; pair it with a "did we miss a food word?" scan. |
| Categorise novel items | Direct scripted call **only**. Never a chatbot tool. Batch the backlog, cache every answer in the item book. |
| Grams, nutrition, cost, merging, sorting | Script. Not the model. |
| Conversational guidance | Chatbot with tools, gated by `tool_choice` / `no_fabrication` / `adherence`. |

Every AI response crossing back into the app needs the deterministic layer from
`ai-vs-script.md`: snap to taxonomy, validate and retry once, bound-check
numbers, cache the result.

## What this report does not establish

- **The three gate suites did not run.** `tool_choice`, `no_fabrication` and
  `adherence` decide whether the chatbot half is safe, and this run says
  nothing about them. The earlier full run scored 85% / 100% / 57%; the
  adherence result is the open concern.
- **Single run, temperature 0.2.** No variance data. `--repeat 3` before
  trusting any of these numbers for a real decision.
- **Only one model.** `qwen3:4b` and `llama3.1:8b` are configured and unrun.
- **Some gold answers are debatable.** `jäst` is flagged above. Others
  (`Zoégas` as skafferi vs dryck) reflect a real store's layout but are not
  self-evident.
- **`recipe_extract` vs `extract_direct` are not a clean comparison.** The tool
  suite has 6 cases to the direct suite's 5, and different ones. The direct
  suite scoring lower is suggestive, not conclusive.

## Fixed during this run

The bench had two defects, both found by disbelieving its own output:

1. **Six cases replayed the tool result before the user's request**, which
   reads as "show me that again" and graded the model on the wrong move. Fixed
   with `user_first: true`.
2. **Empty answers were reported as "no items[] array"** when the real cause
   was token exhaustion inside `<think>`. Now diagnosed explicitly.

`python selftest.py` checks the graders against known-good and known-bad inputs
and passes. Run it before trusting any score.
