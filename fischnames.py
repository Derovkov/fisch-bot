"""
Split catch / quest text into attributes, mutation and fish.

    split_catch("Big Gusty Empyrean Relic")   -> (("Big",), "Gusty", "Empyrean Relic")
    requirement("Obtain 1 Gusty Empyrean Relic for Vance")
        -> {"items": ["Empyrean Relic"], "mutation": "Gusty", "attributes": []}

Fisch rules (the user, 2026-10-03):
  * a fish or other fishable carries at most ONE mutation;
  * sizes (Tiny, Small, Big, Giant) and Shiny / Sparkling (and Glitched) are
    attributes and do not count toward that limit;
  * item names can contain mutation words ("Empyrean Relic" -- Empyrean is
    also a mutation), so the item is found first, from the fish index
    (ui/fish.json, `python fischwiki.py fish`), and only the words in front
    of it are read as the attributes + mutation.

The indexes are loaded once and kept (~1.7k fishables, ~350 mutations): the
lookups are dictionary hits per word group, far cheaper than the OCR that
produced the text.
"""
from __future__ import annotations

from difflib import SequenceMatcher, get_close_matches
import json
from pathlib import Path
import re
from typing import Optional

UI = Path(__file__).with_name("ui")
# Attributes stack in front of a mutation ("Glitched Shiny Sparkling Big Silver
# Isonade", wiki Mutations page); sizes are attributes too.
ATTRIBUTES = ("Shiny", "Sparkling", "Glitched", "Tiny", "Small", "Big", "Giant")
FUZZY = 0.85                        # OCR slips on names ("Evertum" for "Everturn")
STOP = {"for", "of", "to", "in", "from", "at", "with", "and", "or", "any", "kind", "fish"}

_cache: dict = {}


def norm(text: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9' ]", " ", text.lower())).strip()


def _load(name: str, key: str) -> list[dict]:
    try:
        return json.loads((UI / name).read_text(encoding="utf-8")).get(key, [])
    except (OSError, ValueError):
        return []


def mutation_names() -> list[str]:
    """Mutation names (attributes left out), longest first."""
    if "muts" not in _cache:
        names = [m["name"] for m in _load("mutations.json", "mutations")
                 if m.get("type") != "Attributes"]
        _cache["muts"] = sorted(names, key=lambda n: -len(n.split()))
    return _cache["muts"]


def _fish() -> tuple[dict[str, str], dict[int, list[str]], int]:
    """normalised name -> name, normalised names by word count, longest count."""
    if "fish" not in _cache:
        exact = {norm(o["name"]): o["name"] for o in _load("fish.json", "fish") if o.get("name")}
        by_n: dict[int, list[str]] = {}
        for k in exact:
            by_n.setdefault(len(k.split()), []).append(k)
        _cache["fish"] = (exact, by_n, max(by_n, default=0))
    return _cache["fish"]


def fish_name(words: list[str], fuzzy: bool = True) -> Optional[str]:
    """The fishable these words name exactly (or within OCR slips), else None."""
    exact, by_n, _ = _fish()
    key = norm(" ".join(words))
    if key in exact:
        return exact[key]
    if fuzzy and len(key) >= 5:
        hit = get_close_matches(key, by_n.get(len(key.split()), []), n=1, cutoff=FUZZY)
        if hit:
            return exact[hit[0]]
    return None


def _attribute(word: str) -> Optional[str]:
    w = norm(word)
    return next((a for a in ATTRIBUTES if SequenceMatcher(None, w, a.lower()).ratio() >= FUZZY), None)


def split_modifiers(words: list[str]) -> tuple[list[str], Optional[str], list[str]]:
    """(attributes, mutation, leftover words) from the words in front of an
    item. At most one mutation; attributes can sit anywhere among them."""
    attrs, rest = [], []
    for w in words:
        a = _attribute(w)
        if a and a not in attrs:
            attrs.append(a)
        else:
            rest.append(w)
    mutation = None
    for name in mutation_names():
        k = len(name.split())
        if k > len(rest):
            continue
        for i in range(len(rest) - k + 1):
            if SequenceMatcher(None, norm(" ".join(rest[i:i + k])), norm(name)).ratio() >= FUZZY:
                mutation, rest = name, rest[:i] + rest[i + k:]
                break
        if mutation:
            break
    return attrs, mutation, rest


def split_catch(text: str) -> tuple[tuple, Optional[str], Optional[str]]:
    """(attributes, mutation, fish) for a caught-fish name such as "Big Gusty
    Empyrean Relic". The longest tail that names a fishable is the fish, so
    a mutation word inside the fish's own name is never taken off it."""
    words = text.split()
    for start in range(len(words)):                 # longest tail first
        fish = fish_name(words[start:])
        if fish:
            attrs, mutation, _ = split_modifiers(words[:start])
            return tuple(attrs), mutation, fish
    # Not in the index (new fish, OCR damage): take modifiers off the front.
    attrs, mutation = [], None
    while len(words) > 1:
        a = _attribute(words[0])
        if a and a not in attrs:
            attrs.append(a)
            words = words[1:]
            continue
        if mutation is None:
            for name in mutation_names():
                k = len(name.split())
                if k < len(words) and SequenceMatcher(
                        None, norm(" ".join(words[:k])), norm(name)).ratio() >= FUZZY:
                    mutation, words = name, words[k:]
                    break
            else:
                break
            continue
        break
    return tuple(attrs), mutation, " ".join(words).strip(" !.") or None


def find_items(words: list[str]) -> list[tuple[int, int, str]]:
    """Fishables named in a word list: [(start, end, name)], longest match
    first at each position, non-overlapping, left to right."""
    _, _, longest = _fish()
    out, i = [], 0
    clean = [w.strip(",.;:()!") for w in words]
    while i < len(clean):
        hit = None
        if clean[i] and norm(clean[i]) not in STOP:
            for n in range(min(longest, len(clean) - i), 0, -1):
                group = clean[i:i + n]
                if any(norm(g) in STOP or not g for g in group[1:]):
                    continue
                name = fish_name(group, fuzzy=n > 1 or len(group[0]) >= 6)
                if name:
                    hit = (i, i + n, name)
                    break
        if hit:
            out.append(hit)
            i = hit[1]
        else:
            i += 1
    return out


def requirement(text: str) -> dict:
    """What a quest objective asks for: {"items", "mutation", "attributes"}.
    "Catch and return 1 Rotting Glaciaseer Sturgeon, Floraseer Sturgeon, or
    Umbraleaf Sturgeon" -> the 3 sturgeons, mutation Rotting (it applies to
    every item listed). Without a known item, the mutation is read from the
    words right after the count ("1 Gusty <something>")."""
    words = text.split()
    count = next((i for i, w in enumerate(words) if re.fullmatch(r"\d+", w.strip(",."))), None)
    lo = count + 1 if count is not None else 0
    items = find_items(words[lo:])
    if items:
        front = words[lo:lo + items[0][0]]
    else:
        front, generic = [], False
        for w in words[lo:]:
            if norm(w) in ("fish", "fishes", "fishables"):
                generic = True                      # "3 Gusty fish of any kind"
                break
            if norm(w) in STOP or w.endswith(","):
                break
            front.append(w)
        if not generic:
            front = front[:-1]                      # the last word is the item itself
    attrs, mutation, _ = split_modifiers(front)
    return {"items": list(dict.fromkeys(n for _, _, n in items)), "mutation": mutation,
            "attributes": attrs}
