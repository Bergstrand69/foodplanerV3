"""Deterministic graders. No LLM judge anywhere in the default path.

Every check returns (passed, detail). A case passes only when all of its checks
pass, and the report keeps each failing detail -- because "scored 40%" is
useless next to "invented a drive_file_id in 3 of 5 planning cases".
"""
from __future__ import annotations

import json
import re
import unicodedata

from .client import Reply
from .fp_tools import ITEM_CATEGORIES, TOOL_NAMES
from .layout_validator import validate as validate_layout

Check = tuple[bool, str]

# Swedish grams per unit for the ingredient-conversion suite. These are the
# judgement calls set_recipe_nutrition asks the model to make ("use the food's
# real density, not 1 g/ml"), which is exactly where small models fail.
DENSITY_NOTE = "grams per stated amount, Swedish home-cooking convention"


# --------------------------------------------------------------------- helpers

def _norm(s: str) -> str:
    """Casefold + strip diacritics, so 'Grädde' matches 'gradde' when a model
    mangles Swedish characters. Category checks deliberately skip this."""
    s = unicodedata.normalize("NFKD", str(s)).casefold()
    return "".join(c for c in s if not unicodedata.combining(c))


def _flat(obj) -> str:
    return json.dumps(obj, ensure_ascii=False).casefold()


def _dig(obj, path: str):
    cur = obj
    for part in path.split("."):
        if isinstance(cur, list):
            try:
                cur = cur[int(part)]
                continue
            except (ValueError, IndexError):
                return None
        if not isinstance(cur, dict) or part not in cur:
            return None
        cur = cur[part]
    return cur


def _all_call_args(reply: Reply) -> list[dict]:
    return [c.args for c in reply.tool_calls]


def _json_payload(reply: Reply):
    """The model's structured answer, whether it arrived as a tool call or as a
    JSON body in the text (some runtimes never emit tool_calls)."""
    if reply.tool_calls:
        return reply.tool_calls[0].args
    txt = reply.text.strip()
    fence = re.search(r"```(?:json)?\s*(.*?)```", txt, re.S)
    if fence:
        txt = fence.group(1).strip()
    try:
        return json.loads(txt)
    except ValueError:
        return None


# ---------------------------------------------------------------- tool checks

def check_tool(reply: Reply, expected: str) -> Check:
    if not reply.tool_calls:
        return False, f"called no tool (expected {expected}); said: {reply.text[:120]!r}"
    if reply.tool_calls[0].name == expected:
        return True, ""
    return False, f"called {reply.tool_names} (expected {expected} first)"


def check_tool_in(reply: Reply, allowed: list[str]) -> Check:
    if not reply.tool_calls:
        return False, f"called no tool (expected one of {allowed}); said: {reply.text[:120]!r}"
    if reply.tool_calls[0].name in allowed:
        return True, ""
    return False, f"called {reply.tool_names[0]} (expected one of {allowed})"


def check_forbid_tools(reply: Reply, forbidden: list[str]) -> Check:
    hit = [n for n in reply.tool_names if n in forbidden]
    if hit:
        return False, f"called forbidden {hit} -- this writes unverified data"
    return True, ""


def check_no_tool(reply: Reply) -> Check:
    if reply.tool_calls:
        return False, f"called {reply.tool_names} but should have answered in text"
    return True, ""


def check_real_tool_names(reply: Reply) -> Check:
    bogus = [n for n in reply.tool_names if n not in TOOL_NAMES]
    if bogus:
        return False, f"invented tool name(s) {bogus}"
    return True, ""


def check_args_contain(reply: Reply, want: dict) -> Check:
    if not reply.tool_calls:
        return False, "no tool call to inspect"
    blob = _flat(reply.tool_calls[0].args)
    missing = [f"{k}={v}" for k, v in want.items() if _norm(str(v)) not in _norm(blob)]
    if missing:
        return False, f"args missing {missing}; got {json.dumps(reply.tool_calls[0].args, ensure_ascii=False)[:200]}"
    return True, ""


def check_args_equal(reply: Reply, want: dict) -> Check:
    if not reply.tool_calls:
        return False, "no tool call to inspect"
    args = reply.tool_calls[0].args
    bad = []
    for path, expected in want.items():
        got = _dig(args, path)
        if isinstance(expected, str) and isinstance(got, str):
            if _norm(got) != _norm(expected):
                bad.append(f"{path}={got!r} (want {expected!r})")
        elif got != expected:
            bad.append(f"{path}={got!r} (want {expected!r})")
    return (False, "; ".join(bad)) if bad else (True, "")


def check_arg_absent(reply: Reply, paths: list[str]) -> Check:
    if not reply.tool_calls:
        return True, ""
    present = [p for p in paths if _dig(reply.tool_calls[0].args, p) not in (None, "", [])]
    if present:
        return False, f"passed {present} it could not know -- fabricated value"
    return True, ""


def check_ids_from_set(reply: Reply, known: list[str], paths: list[str]) -> Check:
    """Hallucination guard: every drive_file_id the model used must be one the
    conversation actually gave it."""
    bad = []
    for args in _all_call_args(reply):
        for p in paths:
            v = _dig(args, p)
            if v is not None and str(v) not in known:
                bad.append(str(v))
    if bad:
        return False, f"used unknown id(s) {bad} -- not in the recipes it was shown"
    return True, ""


# ------------------------------------------------------------- content checks

def check_categories(reply: Reply, gold: dict[str, str]) -> Check:
    """Items -> store section. Two failure modes are separated on purpose: an
    off-taxonomy string is a HARD failure (the server rejects the whole call),
    a wrong-but-legal category is a soft miss."""
    payload = _json_payload(reply)
    items = None
    if isinstance(payload, dict):
        items = payload.get("items")
    if not isinstance(items, list):
        return False, f"no items[] array in the answer; got {str(payload)[:160]}"

    got = {}
    illegal = []
    for it in items:
        if not isinstance(it, dict):
            continue
        name, cat = it.get("name"), it.get("category")
        if not name or cat is None:
            continue
        got[_norm(name)] = cat
        if cat not in ITEM_CATEGORIES:
            illegal.append(f"{name}->{cat!r}")
    if illegal:
        return False, f"off-taxonomy category (server would reject the call): {illegal}"

    misses, absent = [], []
    for name, want in gold.items():
        have = got.get(_norm(name))
        if have is None:
            absent.append(name)
        elif have != want:
            misses.append(f"{name}: {have} (gold {want})")
    if absent:
        return False, f"did not classify {absent}"
    if misses:
        return False, f"{len(misses)}/{len(gold)} wrong: {misses}"
    return True, ""


def check_grams(reply: Reply, gold: dict[str, float], tol_pct: float = 30.0) -> Check:
    """Ingredient line -> grams. Keyed by 0-based ingredient_index as
    set_recipe_nutrition expects."""
    payload = _json_payload(reply)
    maps = payload.get("mappings") if isinstance(payload, dict) else None
    if not isinstance(maps, list):
        return False, f"no mappings[] array; got {str(payload)[:160]}"
    by_idx = {}
    for m in maps:
        if isinstance(m, dict) and "ingredient_index" in m:
            by_idx[str(m["ingredient_index"])] = m
    bad, absent = [], []
    for idx, want in gold.items():
        m = by_idx.get(str(idx))
        if m is None:
            absent.append(idx)
            continue
        g = m.get("grams")
        if not isinstance(g, (int, float)):
            bad.append(f"[{idx}] grams={g!r}")
        elif abs(g - want) > want * tol_pct / 100:
            bad.append(f"[{idx}] {g} g (want ~{want} g, +/-{tol_pct:.0f}%)")
    if absent:
        return False, f"no mapping for line(s) {absent}"
    if bad:
        return False, "; ".join(bad)
    return True, ""


def check_search_terms(reply: Reply, accepted: list[list[str]]) -> Check:
    """Each search_livsmedel query must be a base staple, not a brand line."""
    queries = [str(c.args.get("query", "")) for c in reply.tool_calls
               if c.name == "search_livsmedel"]
    if not queries:
        return False, "never called search_livsmedel"
    bad = []
    for i, group in enumerate(accepted):
        ok = any(any(_norm(a) == _norm(q) for a in group) for q in queries)
        if not ok:
            bad.append(f"nothing matching {group}")
    if bad:
        return False, f"queries {queries}: {'; '.join(bad)}"
    return True, ""


def check_recipe_json(reply: Reply, min_ingredients: int, min_steps: int) -> Check:
    payload = _json_payload(reply)
    if not isinstance(payload, dict):
        return False, f"no JSON recipe object; got {reply.text[:160]!r}"
    problems = []
    if not payload.get("title"):
        problems.append("no title")
    ing = payload.get("ingredients")
    if not isinstance(ing, list) or len(ing) < min_ingredients:
        problems.append(f"{len(ing) if isinstance(ing, list) else 0} ingredients (want >= {min_ingredients})")
    else:
        unnamed = [i for i, x in enumerate(ing) if not (isinstance(x, dict) and x.get("name"))]
        if unnamed:
            problems.append(f"ingredient(s) {unnamed} have no name")
        quantified = sum(1 for x in ing if isinstance(x, dict) and isinstance(x.get("quantity"), (int, float)))
        if quantified < len(ing) * 0.6:
            problems.append(f"only {quantified}/{len(ing)} ingredients carry a numeric quantity")
    steps = payload.get("steps")
    if not isinstance(steps, list) or len(steps) < min_steps:
        problems.append(f"{len(steps) if isinstance(steps, list) else 0} steps (want >= {min_steps}) "
                        "-- save_recipe forbids compressing to a summary")
    return (False, "; ".join(problems)) if problems else (True, "")


def _ingredient_names(payload) -> list[str] | None:
    if not isinstance(payload, dict):
        return None
    ing = payload.get("ingredients")
    if not isinstance(ing, list):
        return None
    out = []
    for x in ing:
        if isinstance(x, dict) and x.get("name"):
            out.append(str(x["name"]))
        elif isinstance(x, str):
            out.append(x)
    return out


def check_ingredients_include(reply: Reply, wanted: list) -> Check:
    """Every ingredient the source actually mentions must survive extraction.
    An entry may be a list of accepted spellings ('creme fraiche' / 'gräddfil')."""
    names = _ingredient_names(_json_payload(reply))
    if names is None:
        return False, f"no ingredients[] to check; got {reply.text[:140]!r}"
    blob = _norm(" | ".join(names))
    missing = []
    for want in wanted:
        forms = want if isinstance(want, list) else [want]
        if not any(_norm(f) in blob for f in forms):
            missing.append(forms[0])
    if missing:
        return False, f"dropped {missing} from: {names}"
    return True, ""


def check_ingredients_exclude(reply: Reply, forbidden: list[str]) -> Check:
    """The other half of extraction: nothing may be added that the source never
    said. A model that helpfully adds 'salt och peppar' to a TikTok caption has
    invented an ingredient the user will then shop for."""
    names = _ingredient_names(_json_payload(reply))
    if names is None:
        return False, "no ingredients[] to check"
    blob = _norm(" | ".join(names))
    added = [f for f in forbidden if _norm(f) in blob]
    if added:
        return False, f"invented {added} (not in the source): {names}"
    return True, ""


def check_ingredient_count(reply: Reply, spec: dict) -> Check:
    names = _ingredient_names(_json_payload(reply))
    if names is None:
        return False, "no ingredients[] to check"
    lo, hi = spec.get("min", 0), spec.get("max", 999)
    if not (lo <= len(names) <= hi):
        return False, f"{len(names)} ingredients, expected {lo}-{hi}: {names}"
    return True, ""


def check_field_between(reply: Reply, spec: dict) -> Check:
    """For values the source only implies, e.g. servings in a caption that says
    'räcker till hela familjen'. A wide band accepts judgement, not guessing."""
    payload = _json_payload(reply)
    v = _dig(payload, spec["path"]) if isinstance(payload, dict) else None
    if v is None:
        return (True, "") if spec.get("optional") else (False, f"{spec['path']} missing")
    if not isinstance(v, (int, float)):
        return False, f"{spec['path']}={v!r} is not a number"
    if not (spec["min"] <= v <= spec["max"]):
        return False, f"{spec['path']}={v} outside {spec['min']}-{spec['max']}"
    return True, ""


def check_placeholders(reply: Reply) -> Check:
    """{0001} placeholders must be 1-based indices that exist in ingredients[]."""
    payload = _json_payload(reply)
    if not isinstance(payload, dict):
        return False, "no JSON recipe object"
    n = len(payload.get("ingredients") or [])
    bad = []
    for s in payload.get("steps") or []:
        content = s.get("content", "") if isinstance(s, dict) else str(s)
        for tok in re.findall(r"\{(\d{4})\}", content):
            if not (1 <= int(tok) <= n):
                bad.append(f"{{{tok}}} out of range (1..{n})")
    if bad:
        return False, "; ".join(sorted(set(bad)))
    return True, ""


def check_layout(reply: Reply, view: str) -> Check:
    payload = _json_payload(reply)
    if not isinstance(payload, dict):
        return False, f"no customize_ui payload; got {reply.text[:160]!r}"
    if payload.get("view") and payload["view"] != view:
        return False, f"view={payload['view']!r} (want {view!r})"
    tpl = payload.get("template")
    if not isinstance(tpl, str) or not tpl.strip():
        return False, "no template string in the call"
    res = validate_layout(view, tpl, payload.get("css") or "")
    return (True, "") if res.ok else (False, f"validator rejects: {res.reason}")


SV_DAYS = ["måndag", "tisdag", "onsdag", "torsdag", "fredag", "lördag", "söndag"]
EN_DAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]


def _day_keys(value) -> list[str]:
    """Every spelling of one weekday: swap_meal takes 0-6 or a name, in either
    language, so a constraint and an answer can be written differently and still
    refer to the same slot."""
    v = _norm(value).strip()
    if v == "":
        return []
    idx = None
    if v.isdigit():
        idx = int(v)
    else:
        for i, (sv, en) in enumerate(zip(SV_DAYS, EN_DAYS)):
            if v.startswith(_norm(sv)[:3]) or v.startswith(en[:3]):
                idx = i
                break
    if idx is None or not (0 <= idx <= 6):
        return [v]
    return [str(idx), _norm(SV_DAYS[idx]), EN_DAYS[idx]]


def check_plan(reply: Reply, days_required: list[str], known_ids: list[str],
               constraints: dict) -> Check:
    """A week plan is graded on whether the server would accept it AND whether
    it honours the user's stated rules."""
    calls = [c for c in reply.tool_calls if c.name == "swap_meal"]
    if not calls:
        payload = _json_payload(reply)
        plan = payload.get("plan") if isinstance(payload, dict) else None
        if not isinstance(plan, list):
            return False, "no swap_meal calls and no plan[] array"
        calls = [type("C", (), {"args": p})() for p in plan if isinstance(p, dict)]

    problems = []
    seen_days, used_ids = {}, []
    for c in calls:
        rid = c.args.get("drive_file_id")
        if rid is not None:
            used_ids.append(str(rid))
            if str(rid) not in known_ids:
                problems.append(f"invented recipe id {rid!r}")
        # swap_meal accepts a weekday name OR 0-6; index both so a constraint
        # written in Swedish still matches a model that answered numerically.
        for key in _day_keys(c.args.get("day", "")):
            seen_days[key] = str(rid)

    for d in days_required:
        if not (set(_day_keys(d)) & set(seen_days)):
            problems.append(f"no meal assigned for {d}")

    if constraints.get("no_repeats") and len(set(used_ids)) != len(used_ids):
        dupes = {i for i in used_ids if used_ids.count(i) > 1}
        problems.append(f"repeated recipe(s) {sorted(dupes)} in one week")

    for day, want_id_set in (constraints.get("day_must_be_one_of") or {}).items():
        got = next((seen_days[k] for k in _day_keys(day) if k in seen_days), None)
        if got is None:
            problems.append(f"{day} unassigned, constraint unmet")
        elif got not in want_id_set:
            problems.append(f"{day} got {got} which breaks the stated rule")

    return (False, "; ".join(problems)) if problems else (True, "")


# ------------------------------------------------------------- style checks

SV_MARKERS = ("och ", "att ", "det ", "för ", "inte ", "med ", "här ", "din ", "är ",
              "på ", "till ", "veckan", "recept", "inköps")


def check_swedish(reply: Reply) -> Check:
    t = reply.text.casefold()
    if not t.strip():
        return False, "empty reply"
    hits = sum(1 for m in SV_MARKERS if m in t)
    has_letters = any(c in t for c in "åäö")
    if hits >= 2 or (has_letters and hits >= 1):
        return True, ""
    return False, f"does not read as Swedish ({hits} markers, åäö={has_letters}): {reply.text[:120]!r}"


def check_no_em_dash(reply: Reply) -> Check:
    if "—" in reply.text or "–" in reply.text:
        return False, "contains an em/en dash (project style rule: use . , : or parentheses)"
    return True, ""


def check_max_chars(reply: Reply, limit: int) -> Check:
    n = len(reply.text.strip())
    if n > limit:
        return False, f"{n} chars (limit {limit}) -- the card is the deliverable, not the text"
    return True, ""


def check_contains_none(reply: Reply, needles: list[str]) -> Check:
    t = _norm(reply.text)
    hit = [n for n in needles if _norm(n) in t]
    if hit:
        return False, f"restated card contents in text: {hit}"
    return True, ""


def check_contains_any(reply: Reply, needles: list[str]) -> Check:
    t = _norm(reply.text)
    if any(_norm(n) in t for n in needles):
        return True, ""
    return False, f"none of {needles} appeared; said: {reply.text[:160]!r}"


def check_asks_question(reply: Reply, want: bool = True) -> Check:
    asked = "?" in reply.text
    if want and not asked:
        return False, f"did not ask the user anything: {reply.text[:160]!r}"
    if not want and asked:
        return False, f"asked instead of acting: {reply.text[:160]!r}"
    return True, ""


def check_regex(reply: Reply, pattern: str) -> Check:
    if re.search(pattern, reply.text, re.I | re.S):
        return True, ""
    return False, f"no match for /{pattern}/ in: {reply.text[:160]!r}"


def check_regex_absent(reply: Reply, spec) -> Check:
    """Catches numbers stated as fact when the tool said the value is missing."""
    pattern, why = (spec, "") if isinstance(spec, str) else (spec["pattern"], spec.get("why", ""))
    m = re.search(pattern, reply.text, re.I | re.S)
    if m:
        return False, f"said {m.group(0)!r}{' -- ' + why if why else ''}"
    return True, ""


def check_tool_call_count(reply: Reply, spec: dict) -> Check:
    """A model that re-searches every already-bound item burns the token budget
    the item book exists to save."""
    name = spec["tool"]
    n = sum(1 for c in reply.tool_calls if c.name == name)
    lo, hi = spec.get("min", 0), spec.get("max", 99)
    if n < lo:
        return False, f"called {name} {n} times (want at least {lo})"
    if n > hi:
        return False, f"called {name} {n} times (want at most {hi}) -- ignored known_mappings"
    return True, ""


# ------------------------------------------------------------------ dispatch

DISPATCH = {
    "tool": lambda r, v: check_tool(r, v),
    "tool_in": lambda r, v: check_tool_in(r, v),
    "forbid_tools": lambda r, v: check_forbid_tools(r, v),
    "no_tool": lambda r, v: check_no_tool(r) if v else (True, ""),
    "real_tool_names": lambda r, v: check_real_tool_names(r) if v else (True, ""),
    "args_contain": lambda r, v: check_args_contain(r, v),
    "args_equal": lambda r, v: check_args_equal(r, v),
    "arg_absent": lambda r, v: check_arg_absent(r, v),
    "ids_from_set": lambda r, v: check_ids_from_set(r, v["known"], v.get("paths", ["drive_file_id"])),
    "categories": lambda r, v: check_categories(r, v),
    "grams": lambda r, v: check_grams(r, v["gold"], v.get("tol_pct", 30.0)),
    "search_terms": lambda r, v: check_search_terms(r, v),
    "recipe_json": lambda r, v: check_recipe_json(r, v.get("min_ingredients", 1), v.get("min_steps", 1)),
    "placeholders": lambda r, v: check_placeholders(r) if v else (True, ""),
    "ingredients_include": lambda r, v: check_ingredients_include(r, v),
    "ingredients_exclude": lambda r, v: check_ingredients_exclude(r, v),
    "ingredient_count": lambda r, v: check_ingredient_count(r, v),
    "field_between": lambda r, v: check_field_between(r, v),
    "layout": lambda r, v: check_layout(r, v),
    "plan": lambda r, v: check_plan(r, v["days"], v["known_ids"], v.get("constraints", {})),
    "swedish": lambda r, v: check_swedish(r) if v else (True, ""),
    "no_em_dash": lambda r, v: check_no_em_dash(r) if v else (True, ""),
    "max_chars": lambda r, v: check_max_chars(r, v),
    "contains_none": lambda r, v: check_contains_none(r, v),
    "contains_any": lambda r, v: check_contains_any(r, v),
    "asks_question": lambda r, v: check_asks_question(r, bool(v)),
    "regex": lambda r, v: check_regex(r, v),
    "regex_absent": lambda r, v: check_regex_absent(r, v),
    "tool_call_count": lambda r, v: check_tool_call_count(r, v),
}


def grade(reply: Reply, spec: dict) -> tuple[bool, list[str]]:
    """Run every check in `spec`. Returns (passed, failure details)."""
    if reply.error:
        return False, [f"request failed: {reply.error}"]
    # A reasoning model can spend its whole budget inside <think> and emit
    # nothing. That is a budget/config finding, not "the answer was wrong", and
    # every downstream check would report a confusing symptom of it.
    if reply.finish_reason == "length" and not reply.text.strip() and not reply.tool_calls:
        return False, [f"ran out of tokens ({reply.completion_tokens}) inside its reasoning block "
                       f"before emitting an answer; raise max_tokens or disable thinking"]
    failures = []
    for key, value in spec.items():
        fn = DISPATCH.get(key)
        if fn is None:
            failures.append(f"unknown check '{key}' in the task file")
            continue
        try:
            ok, detail = fn(reply, value)
        except Exception as e:                       # a grader bug must not read as a model failure
            failures.append(f"grader '{key}' crashed: {type(e).__name__}: {e}")
            continue
        if not ok:
            failures.append(f"{key}: {detail}")
    return (not failures), failures
