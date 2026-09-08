# FoodPlanner 3.0: what the AI does, and what a script does

**The rule:** the AI only handles work a program genuinely cannot. Everything
deterministic is a script, because a script is right every time, costs nothing,
runs instantly, and never needs a benchmark.

3.0 is a normal meal-planning app with a chatbot alongside it, not an app that
lives inside a chat. The chatbot guides the user and does the messy jobs; the
app does the rest.

## The line

### The AI owns these

| Job | Why no script can do it |
|---|---|
| **Parse a recipe from a messy source** | TikTok captions, video transcripts, comment threads, photos of handwritten cards, rambling blog posts. No schema, no grammar, no consistent units. This is the flagship AI feature. |
| **Categorise a novel product** | The space of product names is open. A table covers "mjölk" and dies on "Oatly iKaffe". |
| **Conversational guidance** | "Vad kan jag laga på det som håller på att bli dåligt?" Interpreting a vague wish into concrete options. |
| **Normalise an ingredient name** | "Arla vispgrädde 40%" to "grädde", once per new name. |
| **Suggest substitutions and variations** | Judgement about what tastes right together. |

### A script owns these

| Job | Why it must not be the AI |
|---|---|
| **Unit and gram conversion** | 1 dl vetemjöl is 60 g. That is a ~100-row density table that is right 100% of the time. GLM-4.7-Flash guessed 200 g for 2 dl flour in this bench. |
| **Nutrition values and arithmetic** | Look up the Livsmedelsverket row by id, multiply, sum, divide by servings. Pure maths on official data. |
| **Cost arithmetic** | Same shape as nutrition. Only the per-item *price estimate* is a judgement call, and even that is better as a scraped or user-entered figure. |
| **Shopping list merge and dedup** | Deterministic set logic on item keys. |
| **Store-walk sorting** | Once categories exist, sorting by taxonomy index is one line of code. |
| **Servings scaling** | Multiplication. |
| **Week and date maths** | Calendar logic. |
| **Recipe search and filtering** | SQL. |

## Why this is the right split

FoodPlanner 2.0 already had the instinct, in the task-049 nutrition design: the
model picks *which food* and *how many grams* (messy), and the server does the
lookup, scaling, summing and per-serving division (exact). 3.0 pushes the line
further in the same direction, because the messy half was still too big:

- **"which food"** can be cached. The item book already binds a name to a
  `livsmedel_id` once and reuses it forever, so the AI is only consulted for
  names nobody has seen before.
- **"how many grams"** should never have been an AI job. It is a density table.
  This is the single clearest win available, and the bench proves it.

That leaves the AI doing only what it is uniquely good at, which also means:

- Far fewer tokens per interaction, so a 64k local model stops being squeezed.
- Fewer places a wrong answer can silently corrupt stored data.
- A much smaller surface to benchmark, and therefore a realistic chance that a
  small local model clears the bar.

## What this means for a local model

The bench in `bench/` now weights the two suites that matter for this split:

- `recipe_extract` (6 cases) — pasted text, TikTok caption, video transcript,
  OCR'd handwritten card, comment thread, plus an edit that must not lose
  fields.
- `categorize` (7 cases) — staples, store-layout edge cases, brand names,
  form-decides-shelf pairs, novel products, user authority, and the closed
  taxonomy.

The three gate suites (`tool_choice`, `no_fabrication`, `adherence`) still
apply, because the chatbot drives the app's tools and must not fabricate ids or
claim work it did not do.

`nutrition_map` is kept but **demoted**: it now measures a capability 3.0
intends to move into a script. Treat a poor score there as confirmation of the
plan rather than a problem to fix by picking a bigger model.

## Hardening the boundary

Where the AI hands data back to the app, a thin deterministic layer turns most
model wobble into a non-event. These are script jobs, not prompt jobs:

**Snap to the taxonomy.** GLM-4.7-Flash classified yoghurt as `"mejieri"`, a
typo of `mejeri`. FoodPlanner 2.0's server rejects the whole `set_item_prices`
call on one bad value, so eight correct classifications were lost to one
misspelling. Fix in code: fuzzy-match every returned category to the nearest of
the eight legal strings (edit distance 1 to 2), and only reject if nothing is
close. The model's judgement was fine; only its spelling was not.

**Validate and retry once.** A structured call that comes back unparseable is
worth exactly one automatic retry with the error appended. Cheaper than a
bigger model, and it converts most format failures into successes.

**Budget for thinking.** Reasoning models spend most of their output budget
inside `<think>`. GLM burned all 2048 tokens reasoning about 24 items and
emitted nothing at all. Any scripted call to a reasoning model needs a generous
`max_tokens` and a check for `finish_reason == "length"`, or it fails silently.

**Cache every answer.** The item book already does this for categories and food
bindings. Every cached answer is one fewer model call, and the cache is where a
user correction lives permanently. The AI should only ever see names nobody has
classified before.

**Never let a model-supplied number reach storage unchecked.** Grams, prices
and portions are all things a script can bound-check. A quantity outside a
plausible range is a bug, not a value.

## Open question

Per-item **price** estimation is still an AI job in 2.0, stored as
`source: estimated`. It has the same shape as gram conversion (a number the
model guesses) but no equivalent lookup table exists, since prices vary by
store and over time. Options: keep it as an AI estimate, let the user enter it
once per item, or scrape a store API. Undecided.
