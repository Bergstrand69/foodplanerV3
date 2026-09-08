"""The FoodPlanner 2.0 MCP tool surface, transcribed from
FoodPlanner2.0/server/src/mcp/router.ts (29 tools, commit bd2ff71).

Descriptions are kept word for word on purpose: their length and their
imperative style ("do NOT do it automatically -- ASK the user first") are
exactly what a small local model has to cope with. Trimming them would make
the bench measure a payload production never sends.

Refresh: re-read the `const tools: Tool[]` array in router.ts after any
FoodPlanner change that adds or reworks a tool, and mirror it here.
"""

TOOLS = [
    dict(name="open_app", description=(
        "Open the FoodPlanner app — an interactive fullscreen UI with the weekly plan, recipe "
        "search and the shopping list. Call when the user asks to open/show the app; returns the "
        "current week as initial data."
    ), parameters={"type": "object", "properties": {}}),

    dict(name="search_recipes", description=(
        "Search the user's recipes by title text and/or tags."
    ), parameters={"type": "object", "properties": {
        "query": {"type": "string"},
        "tags": {"type": "array", "items": {"type": "string"}},
    }}),

    dict(name="get_recipe", description=(
        "Fetch one recipe by its Drive file id (returns the full recipe JSON, rendered as an "
        "interactive cooking view with servings scaling, step timers and a cooking mode). The card "
        "shows the whole recipe — do NOT restate ingredients/steps in text; one line of commentary "
        "at most. IMPORTANT — act on freshness, do not just render: the response carries "
        "`nutrition_status` and `cost_status` (fresh | stale | missing). COST: if cost_status is NOT "
        "fresh, fix it IMMEDIATELY in this same turn without asking (run set_item_prices for unpriced "
        "items, then set_recipe_cost); cost is a cheap ESTIMATE (source: estimated). NUTRITION: "
        "computing nutrition is COSTLY (a Livsmedelsverket lookup plus a mapping for each "
        "ingredient), so do NOT do it automatically — if nutrition_status is NOT fresh, ASK the user "
        "first whether they want nutrition fetched (tell them it takes a moment); only if they agree, "
        "reuse nutrition_status.known_mappings where present (items already bound to Livsmedelsverket "
        "foods — do not re-search those), run search_livsmedel for the remaining lines, then "
        "set_recipe_nutrition. When nutrition_status IS fresh and the user has a customized recipe "
        "layout, make sure that layout has a nutrition section (it is a required part of the recipe "
        "view contract); if a saved layout lacks it, offer to update it with customize_ui. The card "
        "shows values on its next render. `fresh` means the stored value is verified against the "
        "current ingredients — do not recompute it."
    ), parameters={"type": "object", "properties": {"drive_file_id": {"type": "string"}},
                   "required": ["drive_file_id"]}),

    dict(name="edit_recipe", description=(
        "Open the interactive recipe EDITOR (form UI) for a recipe. Use when the user wants to edit a "
        "recipe hands-on ('öppna redigeraren', 'låt mig ändra själv'). For chat-driven edits keep "
        "using update_recipe. The editor card is the UI — don't restate the recipe in text."
    ), parameters={"type": "object", "properties": {"drive_file_id": {"type": "string"}},
                   "required": ["drive_file_id"]}),

    dict(name="save_recipe", description=(
        "Create a recipe in the user's FoodPlanner Drive folder. steps[] must be complete, ordered "
        "instructions a home cook can follow without this chat — one action per step, "
        "temperatures/times/sizes stated in the step that needs them, in the user's language. Never "
        "compress to 'mix and cook' summaries; the file must stand on its own. PREFER rich step "
        "objects: {title, content, timer_seconds?} — set timer_seconds when the step involves waiting "
        "(baking, simmering), and embed ingredient amounts in content as {0001}-style placeholders "
        "(4-digit 1-based index into ingredients[]) so the cooking view can scale them live. Keep "
        "placeholders in sync if you reorder ingredients."
    ), parameters={"type": "object", "properties": {
        "title": {"type": "string"},
        "description": {"type": "string", "description": "short tagline for the dish"},
        "tags": {"type": "array", "items": {"type": "string"}},
        "servings": {"type": "number"},
        "ingredients": {"type": "array", "items": {"type": "object", "properties": {
            "name": {"type": "string"}, "quantity": {"type": "number"}, "unit": {"type": "string"}},
            "required": ["name"]}},
        "steps": {"type": "array", "items": {"anyOf": [
            {"type": "string"},
            {"type": "object", "properties": {"title": {"type": "string"},
                                              "content": {"type": "string"},
                                              "timer_seconds": {"type": "number"}},
             "required": ["content"]}]}},
        "notes": {"type": "string", "description": "tips, variations, substitutions"},
        "nutrition": {"type": "object"},
        "source_url": {"type": "string"},
        "image": {"type": "string", "description": "http(s) URL of a photo of the dish"},
    }, "required": ["title"]}),

    dict(name="update_recipe", description=(
        "Update an existing recipe (title, tags, ingredients, steps, servings, image, ...). REPLACES "
        "the whole recipe file: call get_recipe first, apply the change to the full document, and send "
        "ALL fields back — omitted fields are lost. If rewriting steps[], follow the same rules as "
        "save_recipe (rich step objects preferred; keep {0001} placeholders in sync with ingredient "
        "order)."
    ), parameters={"type": "object", "properties": {
        "drive_file_id": {"type": "string"}, "title": {"type": "string"},
        "description": {"type": "string"}, "tags": {"type": "array", "items": {"type": "string"}},
        "servings": {"type": "number"},
        "ingredients": {"type": "array", "items": {"type": "object", "properties": {
            "name": {"type": "string"}, "quantity": {"type": "number"}, "unit": {"type": "string"}},
            "required": ["name"]}},
        "steps": {"type": "array", "items": {"anyOf": [
            {"type": "string"},
            {"type": "object", "properties": {"title": {"type": "string"},
                                              "content": {"type": "string"},
                                              "timer_seconds": {"type": "number"}},
             "required": ["content"]}]}},
        "notes": {"type": "string"}, "nutrition": {"type": "object"},
        "source_url": {"type": "string"}, "image": {"type": "string"},
    }, "required": ["drive_file_id", "title"]}),

    dict(name="import_recipe", description=(
        "Import a recipe from a URL (parses the page structured data) and save it to Drive. A newly "
        "imported recipe has NO computed nutrition or cost. COST: right after importing, in the same "
        "turn, PRICE it automatically (set_item_prices for its ingredients, then set_recipe_cost) — "
        "cost is a cheap estimate. NUTRITION: do NOT fetch it automatically (a Livsmedelsverket lookup "
        "plus a per-ingredient mapping is costly) — ASK the user whether they want nutrition fetched "
        "(tell them it takes a moment), and only map its ingredients (search_livsmedel -> "
        "set_recipe_nutrition) if they agree."
    ), parameters={"type": "object", "properties": {"url": {"type": "string"}}, "required": ["url"]}),

    dict(name="build_shopping_list", description=(
        "ONE-OFF merged shopping list from specific recipes — no weekly plan involved, no check-off "
        "state, nothing saved. Use for ad-hoc asks like 'what do I need for these two dishes?'. For "
        "the week's real list use get_plan_shopping_list instead. Pass items[] to scale servings (e.g. "
        "a 4-serving recipe cooked for 6), or plain recipe_ids[] for unscaled."
    ), parameters={"type": "object", "properties": {
        "recipe_ids": {"type": "array", "items": {"type": "string"}},
        "items": {"type": "array", "items": {"type": "object", "properties": {
            "drive_file_id": {"type": "string"}, "servings": {"type": "number"}},
            "required": ["drive_file_id"]}},
    }}),

    dict(name="delete_recipe", description="Delete a recipe by its Drive file id.",
         parameters={"type": "object", "properties": {"drive_file_id": {"type": "string"}},
                     "required": ["drive_file_id"]}),

    dict(name="sync_recipes", description=(
        "Re-index the user's whole FoodPlanner Drive folder and prune ghost entries. Use when search "
        "results look stale or wrong — a recipe is missing after being added, a deleted recipe still "
        "shows up, or files were edited by hand in Drive."
    ), parameters={"type": "object", "properties": {}}),

    dict(name="get_weekly_plan", description=(
        "Get the user's meal plan for a week (creates an empty plan on first use). week_start is any "
        "date in that week (YYYY-MM-DD); omit for the current week. An interactive card renders the "
        "plan — do NOT restate it in text; add at most a one-line summary or suggestion."
    ), parameters={"type": "object", "properties": {"week_start": {"type": "string"}}}),

    dict(name="swap_meal", description=(
        "Assign a recipe to one meal slot of a weekly plan (replaces whatever was there), or clear the "
        "slot by omitting drive_file_id. day is 0-6 (0=Monday) or a weekday name."
    ), parameters={"type": "object", "properties": {
        "week_start": {"type": "string",
                       "description": "any date in the target week (YYYY-MM-DD); omit for current week"},
        "day": {"anyOf": [{"type": "integer", "minimum": 0, "maximum": 6}, {"type": "string"}]},
        "meal_type": {"type": "string", "enum": ["breakfast", "lunch", "dinner", "snack"],
                      "default": "dinner"},
        "drive_file_id": {"type": "string", "description": "recipe to assign; omit to clear the slot"},
        "servings": {"type": "number"},
    }, "required": ["day"]}),

    dict(name="get_plan_shopping_list", description=(
        "THE week's shopping list: merged from the weekly plan's meals (scaled by each meal's "
        "servings) + custom items, with each line's check-off state. Prefer this whenever a weekly "
        "plan exists; build_shopping_list is only for ad-hoc lists outside the plan. Lines carry a "
        "store `category` from the user's item book and come sorted in store-walk order (frukt & grönt "
        "first, övrigt last). IMPORTANT — if the response contains a non-empty `uncategorized` list, "
        "categorize those items IMMEDIATELY in the same turn, without asking the user: call "
        "set_item_prices with items [{ name, unit, category }] using the fixed taxonomy (frukt & "
        "grönt, bröd, mejeri, kött & fisk, fryst, skafferi, dryck, övrigt); the category choice is "
        "your judgement. An interactive card renders the full list — do NOT restate the items in text; "
        "add at most a one-line summary or observation."
    ), parameters={"type": "object", "properties": {"week_start": {"type": "string"}}}),

    dict(name="get_shopping_session", description=(
        "THE shopping list for a DATE RANGE (e.g. 'thursday through next wednesday', 'the next 10 "
        "days') — merged from the meals of every day in the range, across as many weeks as it spans, "
        "plus the session's own manual items, with each line's check-off state. Use INSTEAD of "
        "get_plan_shopping_list when the user shops for a span rather than one ISO week. Returns the "
        "saved range (null when unset — call set_shopping_dates first). Lines carry a store `category` "
        "from the user's item book and come sorted in store-walk order. IMPORTANT — if the response "
        "contains a non-empty `uncategorized` list, categorize those items IMMEDIATELY in the same "
        "turn, without asking the user: call set_item_prices with items [{ name, unit, category }] "
        "using the fixed taxonomy (frukt & grönt, bröd, mejeri, kött & fisk, fryst, skafferi, dryck, "
        "övrigt); the category choice is your judgement. The interactive card renders the list and a "
        "calendar to pick the range — do NOT restate the items in text; add at most a one-line summary."
    ), parameters={"type": "object", "properties": {}}),

    dict(name="set_shopping_dates", description=(
        "Set the shopping session's date range (inclusive, ISO YYYY-MM-DD). The range may span weeks "
        "and months. The derived items are recomputed from the meals in the new range, while the "
        "user's manual items, tick-offs and removed lines are ALWAYS preserved across a date change. "
        "Returns the newly derived list."
    ), parameters={"type": "object", "properties": {
        "start_date": {"type": "string", "description": "first day of the range (YYYY-MM-DD)"},
        "end_date": {"type": "string", "description": "last day of the range, inclusive (YYYY-MM-DD)"},
    }, "required": ["start_date", "end_date"]}),

    dict(name="check_off_item", description=(
        "Tick or untick one line of a shopping list. item_key comes from get_plan_shopping_list (a "
        "week's list) or get_shopping_session (a date range). Pass session=true to target the "
        "date-range session instead of a week."
    ), parameters={"type": "object", "properties": {
        "week_start": {"type": "string"},
        "session": {"type": "boolean", "default": False,
                    "description": "true = the date-range shopping session; week_start is then ignored"},
        "item_key": {"type": "string"},
        "checked": {"type": "boolean", "default": True},
    }, "required": ["item_key"]}),

    dict(name="set_week_notes", description=(
        "Set or clear the weekly plan's free-text note (e.g. 'Saturday we eat at grandma's — only plan "
        "6 dinners'). Pass an empty string or omit notes to clear. Notes come back with get_weekly_plan."
    ), parameters={"type": "object", "properties": {
        "week_start": {"type": "string"}, "notes": {"type": "string"}}}),

    dict(name="add_shopping_item", description=(
        "Add a custom item to a shopping list — things that aren't recipe ingredients (dish soap, "
        "coffee, snacks). Targets a week by default; pass session=true to add to the date-range "
        "session instead (where it survives every date change)."
    ), parameters={"type": "object", "properties": {
        "week_start": {"type": "string"},
        "session": {"type": "boolean", "default": False},
        "name": {"type": "string"}, "quantity": {"type": "number"}, "unit": {"type": "string"},
    }, "required": ["name"]}),

    dict(name="remove_shopping_item", description=(
        "Remove ANY line from a shopping list (custom items are deleted; derived ingredient lines are "
        "hidden so they don't reappear). Pass the line's item_key (preferred, from "
        "get_plan_shopping_list / get_shopping_session) or a plain name. restore=true un-hides a "
        "previously removed line. Pass session=true to target the date-range session instead of a week."
    ), parameters={"type": "object", "properties": {
        "week_start": {"type": "string"}, "session": {"type": "boolean", "default": False},
        "item_key": {"type": "string"}, "name": {"type": "string"},
        "restore": {"type": "boolean", "default": False},
    }}),

    dict(name="get_preferences", description=(
        "Read the user's saved food preferences/rules (diet days, allergies, time limits). They are "
        "also auto-applied to every FoodPlanner prompt."
    ), parameters={"type": "object", "properties": {}}),

    dict(name="search_livsmedel", description=(
        "Search Livsmedelsverket's official Swedish food database (livsmedelsdatabasen, per 100 g) by "
        "name. Use it to turn an ingredient line into a food id before calling set_recipe_nutrition: "
        "search the plain food word ('grädde', 'vetemjöl'), then pick the entry that best matches what "
        "the recipe means (prefer the plain staple over a composite dish, and match fat percentages "
        "when the recipe states one). The match is plain substring — double-check the Swedish spelling "
        "before searching (a typo returns nothing). If a search returns nothing: strip brand names and "
        "modifiers and search the base staple ('Arla vispgrädde 40%' -> 'vispgrädde' -> 'grädde'), "
        "then try a synonym. If even the base staple genuinely is not in the database, do NOT force a "
        "bad match — deliberately leave that line unmapped in set_recipe_nutrition so it is flagged as "
        "not counted. Returns livsmedel_id + name + the per-100 g values."
    ), parameters={"type": "object", "properties": {
        "query": {"type": "string"}, "limit": {"type": "number"}}, "required": ["query"]}),

    dict(name="set_recipe_nutrition", description=(
        "Compute and store a recipe's nutrition PER SERVING from your ingredient mapping. Call it when "
        "get_recipe reports nutrition_status.status = 'stale' or 'missing'. You supply the messy part "
        "— for EVERY ingredient line: its ingredient_index (0-based, in the recipe's ingredients[] "
        "order), the livsmedel_id you picked via search_livsmedel, and grams (convert the line's amount "
        "to grams yourself: '2 dl grädde' -> about 200 g; use the food's real density, not 1 g/ml, for "
        "things like oil or flour). The SERVER does the exact part: it looks up the official per-100 g "
        "values, scales by your grams, sums, divides by servings, fingerprints the ingredients and "
        "stores the result in the recipe file. Never compute the numbers yourself and never pass them "
        "in. Skip a line only when it has no meaningful mass (a pinch of salt, water for boiling) OR "
        "when the food genuinely cannot be found in the database even as its base staple (see "
        "search_livsmedel) — a deliberate skip is honest and reported as 'not counted', while a forced "
        "wrong mapping poisons the total silently. Unmapped lines are reported as 'not counted' rather "
        "than silently ignored. REUSE KNOWN BINDINGS: when get_recipe supplied "
        "nutrition_status.known_mappings, reuse those livsmedel_id values for those lines instead of "
        "searching again (grams are still yours to judge per line — amounts differ per recipe). The "
        "mappings you use are saved back to the user's item book automatically, so common items are "
        "mapped once across all recipes; a binding the user stated themselves is never overwritten by "
        "yours."
    ), parameters={"type": "object", "properties": {
        "drive_file_id": {"type": "string"},
        "mappings": {"type": "array", "description": "One entry per ingredient line you can map.",
                     "items": {"type": "object", "properties": {
                         "ingredient_index": {"type": "number",
                                              "description": "0-based index into the recipe ingredients[]"},
                         "livsmedel_id": {"type": "number", "description": "from search_livsmedel"},
                         "grams": {"type": "number",
                                   "description": "grams of that food this line represents"}},
                         "required": ["ingredient_index", "livsmedel_id", "grams"]}},
    }, "required": ["drive_file_id", "mappings"]}),

    dict(name="get_prices", description=(
        "Read the user's item book — the per-item data shared across ALL recipes and weeks, stored in "
        "their Drive. Each item may carry: (1) a price used to cost recipes and shopping lists — "
        "price_per_100 (per 100 g / ml / piece), its source (estimated | user | looked_up) and a "
        "freshness status (a price older than the max age reads as stale); (2) its saved "
        "Livsmedelsverket binding (`livsmedel`: livsmedel_id + food_name + source) — which food the "
        "item IS, mapped once and reused so you never re-search a known item (get_recipe surfaces "
        "these as known_mappings); (3) its store `category` (fixed taxonomy, drives shopping-list "
        "grouping; a user-set category is authoritative). Read this before re-estimating prices so you "
        "only price items that are missing or stale."
    ), parameters={"type": "object", "properties": {}}),

    dict(name="set_item_prices", description=(
        "Estimate and store per-ITEM prices in the user's item book (shared across ALL recipes and "
        "weeks — price 'grädde' once and every dish/week using it is costed). Call it when get_recipe "
        "reports cost_status.status = 'missing'/'stale' or lists missing_prices, or when a shopping "
        "total is incomplete. For each item pass { name, price, quantity, unit } — a REALISTIC total "
        "price for that amount at a normal Swedish grocery store (e.g. { name: 'grädde', price: 25, "
        "quantity: 5, unit: 'dl' }); the SERVER converts it to a per-100 figure and keys it by item "
        "identity. You may instead pass { name, price_per_100 }. These are ESTIMATES: they are stored "
        "with source: estimated and must never be described as exact/looked-up prices. Use the SAME "
        "item name and unit the recipe/shopping line uses so the price lines up. Do NOT do the "
        "arithmetic — send the raw estimate. CATEGORY: an item may also/instead carry `category` — its "
        "store section, EXACTLY one of: frukt & grönt, bröd, mejeri, kött & fisk, fryst, skafferi, "
        "dryck, övrigt (anything else is rejected). Classify on your own judgement whenever a shopping "
        "list reports the item in `uncategorized` — do it in the same turn, without asking. Pass "
        "category_source: 'user' ONLY when the user themselves stated the category; a user-set "
        "category is authoritative and a claude-set one never overwrites it. A category-only entry ({ "
        "name, unit, category }, no price) is allowed and keeps any existing price/binding. BINDING "
        "OVERRIDE: ONLY when the user has EXPLICITLY stated which Livsmedelsverket food an item is, an "
        "entry may also carry { livsmedel_id, food_name } (both, from search_livsmedel) — this saves a "
        "USER-authoritative binding in the item book that automatic mappings never overwrite. A "
        "binding-only entry ({ name, unit, livsmedel_id, food_name }, no price) is allowed and keeps "
        "any existing price. Never pass livsmedel_id on your own initiative — your own mappings are "
        "saved automatically via set_recipe_nutrition."
    ), parameters={"type": "object", "properties": {
        "items": {"type": "array", "items": {"type": "object", "properties": {
            "name": {"type": "string"}, "price": {"type": "number"},
            "quantity": {"type": "number"}, "unit": {"type": "string"},
            "price_per_100": {"type": "number"}, "category": {"type": "string"},
            "category_source": {"type": "string"}, "livsmedel_id": {"type": "number"},
            "food_name": {"type": "string"}}, "required": ["name"]}},
    }, "required": ["items"]}),

    dict(name="set_recipe_cost", description=(
        "Compute and store a recipe's cost from the price book. Call it after pricing the recipe's "
        "items with set_item_prices, when get_recipe reports cost_status is 'missing'/'stale'. You "
        "pass ONLY the recipe id — the server reads the recipe, converts each ingredient amount to "
        "base units, looks up the per-item price, sums the total and per-serving cost, fingerprints "
        "the ingredients and stores it in the recipe file. Ingredients with no price come back in "
        "`missing` (they are NOT counted as free) — price them with set_item_prices and call again. "
        "The cost is an ESTIMATE (source: estimated in v1); never present it as an exact price."
    ), parameters={"type": "object", "properties": {"drive_file_id": {"type": "string"}},
                   "required": ["drive_file_id"]}),

    dict(name="set_preferences", description=(
        "Replace the user's saved food preference rules with a complete new list. Read get_preferences "
        "first, apply the user's change to that list, then send ALL rules (this replaces, not "
        "appends). Stored in the user's own Drive."
    ), parameters={"type": "object", "properties": {
        "rules": {"type": "array", "items": {"type": "string"}}}, "required": ["rules"]}),

    dict(name="customize_ui", description=(
        "Save a custom layout for a FoodPlanner view so all future renders use it (generate once, "
        "reuse forever). Call ONLY when the user asks to change how a view looks. You author "
        "`template` (logic-less Mustache markup) + `css`; pass the user's request verbatim as "
        "`intent`. STRICT CONTRACT, enforced by a validator that rejects on any breach:\n"
        "- Template is Mustache-style: {{field}} interpolates (auto-escaped), {{#items}}...{{/items}} "
        "repeats per item, {{^items}}...{{/items}} is the empty state. No expressions.\n"
        "- shopping data fields: top-level `week_start`, `items`, `cost`, `categories`; inside "
        "{{#items}}: `item_key`, `name`, `quantity`, `unit`, `checked`, `category`. `categories` "
        "(OPTIONAL) is the SAME list pre-grouped for store walking: [{ name, items }] in fixed store "
        "order. Referencing any other field is rejected.\n"
        "- Interactivity is ONLY via data-attributes (NO JavaScript): put "
        "`data-fp-check=\"{{item_key}}\"` on each row — REQUIRED; `data-fp-add` on a <form> or its "
        "text <input> — REQUIRED; `data-fp-remove=\"{{item_key}}\"` optional per row. No other "
        "data-fp-* attribute is allowed.\n"
        "- recipe data fields (the cooking OVERVIEW): top-level `title`, `description`, `image`, "
        "`notes`, `servings`, `nutrition`, `cost`, `ingredients`, `steps`. `servings` is an object "
        "section: {{#servings}}{{current}}/{{base}}{{/servings}}. `nutrition` is a REQUIRED object "
        "section (every recipe layout MUST include a {{#nutrition}} section) with the PER-SERVING "
        "values — inside it: `energy_kcal`, `protein_g`, `fat_g`, `saturated_fat_g`, `carbs_g`, "
        "`sugars_g`, `fiber_g`, `salt_g`, plus `status`, `stale` (flag), `incomplete` (flag), "
        "`source`. `cost` is an OPTIONAL object section (estimate); inside it: `status`, `stale`, "
        "`incomplete`, `per_recipe`, `per_serving`, `currency`. Inside {{#ingredients}}: `index`, "
        "`name`, `display`, `checked`. Inside {{#steps}}: `index`, `title`, `content`. recipe intents: "
        "`data-fp-ingredient-check=\"{{index}}\"` per ingredient — REQUIRED; "
        "`data-fp-servings=\"inc\"`/`\"dec\"`; `data-fp-cookmode`.\n"
        "- week data fields: top-level `week_start`, `notes`, `days`. Inside {{#days}}: `day`, "
        "`day_name`, `meals`; inside {{#meals}}: `meal_type`, `meal_sv`, `title`, `drive_file_id`, "
        "`servings`. Use {{^meals}} for an empty day. The template MUST contain a {{#days}} section — "
        "REQUIRED. week intents: `data-fp-assign=\"{{day}}:{{meal_type}}\"` — REQUIRED (every week "
        "layout MUST give the user a way to add a recipe); `data-fp-clear=\"{{day}}:{{meal_type}}\"`; "
        "`data-fp-notes` on a <textarea>.\n"
        "- Forbidden: <script>, inline on*= handlers, and external URLs (http(s)://, protocol-relative "
        "//, src/href to another origin). CSS may reference host theme variables and data: URIs only.\n"
        "On rejection you get the reason back — fix it and retry. view is an enum: \"shopping\", "
        "\"recipe\" or \"week\"."
    ), parameters={"type": "object", "properties": {
        "view": {"type": "string", "enum": ["shopping", "recipe", "week"]},
        "template": {"type": "string",
                     "description": "Mustache-style markup with data-fp-* intents (no JS)"},
        "css": {"type": "string", "description": "CSS using host theme variables / data: URIs only"},
        "intent": {"type": "string", "description": "the user's design request in their own words"},
    }, "required": ["view", "template"]}),

    dict(name="get_ui_layout", description=(
        "Read the saved custom layout for a view (template, css, intent), or null if the view still "
        "uses the built-in default. Use before editing an existing custom layout."
    ), parameters={"type": "object", "properties": {
        "view": {"type": "string", "enum": ["shopping", "recipe", "week"]}}, "required": ["view"]}),

    dict(name="reset_ui", description=(
        "Delete a view's saved custom layout so it reverts to the built-in default. Use when the user "
        "says \"reset\"/\"back to normal\"."
    ), parameters={"type": "object", "properties": {
        "view": {"type": "string", "enum": ["shopping", "recipe", "week"]}}, "required": ["view"]}),
]

TOOL_NAMES = [t["name"] for t in TOOLS]

# The real claude.ai Project instructions FoodPlanner ships (projectInstructions.ts).
PROJECT_INSTRUCTIONS = """You are my meal planner, powered by the FoodPlanner connector. Ground every answer in the FoodPlanner tools instead of guessing:

- My recipes live in search_recipes / get_recipe. When presenting a recipe, show its image if the recipe has one.
- The week's meals live in get_weekly_plan. When I wonder what to eat, check the plan first, then my recipe collection.
- Planning: assign or change meals with swap_meal. Never say a meal is planned unless the tool call succeeded.
- Shopping: get_plan_shopping_list shows an interactive checklist; when I say I bought or already have something, persist it with check_off_item.
- New recipes: import_recipe for any URL; save_recipe for dishes I describe.
- For bigger jobs (planning a whole week, importing, shopping), the connector's prompts — plan_week, import_recipe, shopping_list — contain the full playbooks; follow them when the task matches.
- Match my language (often Swedish — svara på svenska då).
- If a tool says Drive isn't connected, tell me to open the connector's sign-in page and use "Connect Google Drive".

The interactive cards ARE the deliverable: when a card renders the plan, list or recipe, never restate its contents in text — one line of summary or insight at most. Keep all answers compact."""

# The store-walk taxonomy (widgets/src/lib/categories.ts). Exact strings — the
# server rejects anything else, so a near-miss like "frukt och grönt" is a hard
# failure, not a near-hit.
ITEM_CATEGORIES = [
    "frukt & grönt", "bröd", "mejeri", "kött & fisk",
    "fryst", "skafferi", "dryck", "övrigt",
]


def openai_tools(subset=None):
    """The tool list in OpenAI function-calling shape."""
    src = TOOLS if subset is None else [t for t in TOOLS if t["name"] in subset]
    return [{"type": "function", "function": {"name": t["name"],
                                              "description": t["description"],
                                              "parameters": t["parameters"]}} for t in src]
