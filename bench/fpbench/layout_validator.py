"""Python port of FoodPlanner2.0/server/src/uiLayoutValidator.ts.

Why a port and not a call into the real thing: the bench has to run without a
checked-out FoodPlanner2.0, a node toolchain, or a dev server. The rules below
mirror UI_CONTRACT.md per view -- field allowlists, intent allowlists, the
required-intent checks, and the forbidden-markup checks.

If the TS validator changes, change this too. `python -m fpbench.layout_validator
<path to uiLayoutValidator.ts>` prints the field/intent sets found in the TS
source next to the ones here, so drift is visible rather than silent.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

SECTION_RE = re.compile(r"\{\{\s*([#^/])\s*([\w.-]+)\s*\}\}")
VAR_RE = re.compile(r"\{\{\s*(?![#^/!])\s*([\w.-]+)\s*\}\}")
INTENT_RE = re.compile(r"data-fp-([a-z-]+)", re.I)
SCRIPT_RE = re.compile(r"<\s*script", re.I)
ON_HANDLER_RE = re.compile(r"\son[a-z]+\s*=", re.I)
EXTERNAL_URL_RE = re.compile(r"""(?:src|href)\s*=\s*["']\s*(?:https?:)?//""", re.I)
CSS_EXTERNAL_RE = re.compile(r"""url\(\s*['"]?\s*(?:https?:)?//""", re.I)


@dataclass
class Result:
    ok: bool
    reason: str = ""


SPECS: dict[str, dict] = {
    "shopping": {
        "top": {"week_start", "items", "cost", "categories"},
        "sections": {
            "items": {"item_key", "name", "quantity", "unit", "checked", "category"},
            "categories": {"name", "items"},
            "cost": {"total", "currency", "complete", "priced_count", "missing", "stale_prices"},
        },
        "intents": {"check", "remove", "add"},
    },
    "recipe": {
        "top": {"title", "description", "image", "notes", "servings", "nutrition", "cost",
                "ingredients", "steps"},
        "sections": {
            "servings": {"base", "current"},
            "cost": {"status", "stale", "incomplete", "per_recipe", "per_serving", "currency"},
            "nutrition": {"status", "stale", "incomplete", "reason", "source", "computed_at",
                          "energy_kcal", "protein_g", "fat_g", "saturated_fat_g", "carbs_g",
                          "sugars_g", "fiber_g", "salt_g"},
            "ingredients": {"index", "name", "display", "checked"},
            "steps": {"index", "title", "content"},
        },
        "intents": {"ingredient-check", "servings", "cookmode"},
    },
    "week": {
        "top": {"week_start", "notes", "days"},
        "sections": {
            "days": {"day", "day_name", "meals"},
            "meals": {"meal_type", "meal_sv", "title", "drive_file_id", "servings"},
        },
        "intents": {"assign", "clear", "notes"},
    },
}


def _require(view: str, t: str) -> str | None:
    if view == "shopping":
        if not re.search(r"""data-fp-check\s*=\s*["'][^"']*\{\{\s*item_key\s*\}\}[^"']*["']""", t, re.I):
            return "missing a data-fp-check bound to {{item_key}}"
        if not re.search(r"data-fp-add\b", t, re.I):
            return "missing a data-fp-add control"
        return None
    if view == "recipe":
        if not re.search(r"""data-fp-ingredient-check\s*=\s*["'][^"']*\{\{\s*index\s*\}\}[^"']*["']""",
                         t, re.I):
            return "missing a data-fp-ingredient-check bound to {{index}}"
        # task-064: an OPTIONAL data section is invisible in a saved layout, so
        # nutrition is required even before any recipe has it computed.
        if not re.search(r"\{\{\s*#\s*nutrition\s*\}\}", t):
            return "missing the required {{#nutrition}} section"
        return None
    if view == "week":
        if not re.search(r"\{\{\s*#\s*days\s*\}\}", t):
            return "missing the required {{#days}} section"
        if not re.search(r"""data-fp-assign\s*=\s*["'][^"']*\{\{\s*day\s*\}\}[^"']*["']""", t, re.I):
            return "missing a data-fp-assign bound to {{day}}"
        return None
    return None


def validate(view: str, template: str, css: str = "") -> Result:
    if view not in SPECS:
        return Result(False, f"unknown view '{view}' (expected shopping | recipe | week)")
    if not template or not template.strip():
        return Result(False, "empty template")
    spec = SPECS[view]

    # 1. Forbidden markup.
    if SCRIPT_RE.search(template):
        return Result(False, "template contains a <script> tag")
    if ON_HANDLER_RE.search(template):
        return Result(False, "template contains an inline on*= handler")
    if EXTERNAL_URL_RE.search(template):
        return Result(False, "template references an external URL")
    if css and (SCRIPT_RE.search(css) or CSS_EXTERNAL_RE.search(css)):
        return Result(False, "css references an external URL or script")

    # 2. Sections must nest and close, and only known sections may open.
    stack: list[str] = []
    known_sections = set(spec["sections"])
    for kind, name in SECTION_RE.findall(template):
        if kind in "#^":
            if name not in known_sections:
                return Result(False, f"unknown section {{{{#{name}}}}} for view '{view}'")
            if name in stack:
                return Result(False, f"section {{{{#{name}}}}} is nested inside itself")
            stack.append(name)
        else:
            if not stack or stack[-1] != name:
                exp = stack[-1] if stack else "nothing"
                return Result(False, f"closing {{{{/{name}}}}} but the open section is {exp}")
            stack.pop()
    if stack:
        return Result(False, f"unclosed section {{{{#{stack[-1]}}}}}")

    # 3. Every interpolation must be legal where it appears.
    depth: list[str] = []
    for m in re.finditer(r"\{\{[^}]*\}\}", template):
        tok = m.group(0)
        sec = SECTION_RE.fullmatch(tok.strip())
        if sec:
            kind, name = sec.group(1), sec.group(2)
            if kind in "#^":
                depth.append(name)
            else:
                depth.pop()
            continue
        var = VAR_RE.fullmatch(tok.strip())
        if not var:
            continue
        field = var.group(1)
        if depth:
            allowed = set(spec["sections"].get(depth[-1], set()))
            # A section nested in another (categories > items, days > meals)
            # also sees its own fields; the runtime context is the inner item.
            if depth[-1] in ("categories",) and field in spec["sections"]["items"]:
                allowed |= spec["sections"]["items"]
            if field not in allowed:
                return Result(False,
                              f"field {{{{{field}}}}} is not available inside {{{{#{depth[-1]}}}}}")
        else:
            if field not in spec["top"]:
                return Result(False, f"unknown top-level field {{{{{field}}}}} for view '{view}'")

    # 4. Only the view's own intents, and the required ones must be present.
    for intent in {i.lower() for i in INTENT_RE.findall(template)}:
        if intent not in spec["intents"]:
            return Result(False, f"data-fp-{intent} is not an allowed intent for view '{view}'")
    missing = _require(view, template)
    if missing:
        return Result(False, missing)

    return Result(True)


if __name__ == "__main__":  # drift check against the TS source
    import json
    import sys
    if len(sys.argv) < 2:
        print(json.dumps({v: {"top": sorted(s["top"]), "intents": sorted(s["intents"]),
                              "sections": {k: sorted(f) for k, f in s["sections"].items()}}
                          for v, s in SPECS.items()}, ensure_ascii=False, indent=2))
        raise SystemExit(0)
    ts = open(sys.argv[1], encoding="utf-8").read()
    for view, spec in SPECS.items():
        for f in sorted(spec["top"] | {x for s in spec["sections"].values() for x in s}):
            if f"'{f}'" not in ts:
                print(f"DRIFT: '{f}' ({view}) is in the Python port but not in the TS validator")
        for i in sorted(spec["intents"]):
            if f"'{i}'" not in ts:
                print(f"DRIFT: intent '{i}' ({view}) is in the Python port but not in the TS validator")
    print("drift check done")
