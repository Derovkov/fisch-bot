"""
Refresh game data from Fischipedia, the official Fisch wiki (fischipedia.org).

    python fischwiki.py mutations      # -> ui/mutations.json
    python fischwiki.py fish           # -> ui/fish.json (fish + relics, crates...)
    python fischwiki.py baits          # -> ui/baits.json (+ ui/icons/baits)

Mutations: every row of the wiki's "Mutations" table (name, type, value,
priority, source categories), plus each mutation page's "Obtainment" section:
which rods, enchants, weathers, seasons, events, baits... give it, and the rod
chances where the page states them. Each mutation gets a `group` for quests
(the user, 2026-10-03: "an index of all possible mutations from ONLY rods; if a
mutation could only be given naturally or needs an enchant, a different index"):

    rod      -- a fishing rod can give it (see `rods`; maybe other ways too)
    enchant  -- no rod, but an enchant can (see `enchants`)
    natural  -- neither: caught naturally / weather / season / bait / appraisal ...
    event    -- event, limited, admin or exclusive only (not farmable on demand)

Uses the wiki's public API, ~10 requests. Run it when the game updates.
"""
from __future__ import annotations

import html
import json
import re
import sys
import time
import urllib.parse
import urllib.request
from datetime import date
from pathlib import Path

API = "https://fischipedia.org/w/api.php"
UA = {"User-Agent": "FischBot-wiki-sync/1.0 (personal fishing-bot project)"}
OUT = Path(__file__).with_name("ui") / "mutations.json"

SOURCE_CATS = ["Fishing Rods", "Fishing Rod", "Harpoon Guns", "Spears", "Enchantments",
               "Enchantment", "Companions", "Bait", "Accessories", "Status Effects",
               "Weather", "Localized Events", "Admin Events", "Admin Event", "Codes", "NPCs",
               "Appraisal", "Appraising", "Natural", "Treasure Chests"]
CAT_NORM = {"Fishing Rod": "Fishing Rods", "Enchantment": "Enchantments",
            "Admin Event": "Admin Events", "Appraising": "Appraisal"}
# Obtainment templates -> field
TEMPLATES = {"Rod": "rods", "Enchantment": "enchants", "Weather": "weather",
             "Event": "events", "Season": "seasons", "Bait": "baits",
             "Companion": "companions", "Item": "items", "Spear": "spears",
             "Harpoon": "harpoons", "Code": "codes"}


def api(**params) -> dict:
    params.setdefault("format", "json")
    params.setdefault("formatversion", "2")
    url = API + "?" + urllib.parse.urlencode(params)
    for attempt in range(3):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=UA),
                                        timeout=30) as r:
                return json.load(r)
        except OSError:
            if attempt == 2:
                raise
            time.sleep(2)
    raise RuntimeError("unreachable")


def _text(cell: str) -> str:
    return html.unescape(re.sub(r"<[^>]+>", " ", cell)).strip()


def mutation_table() -> list[dict]:
    """Rows of the wiki's mutation list (rendered HTML of the Mutations page)."""
    page = api(action="parse", page="Mutations", prop="text")["parse"]["text"]
    rows = []
    for tr in re.findall(r"<tr[^>]*>(.*?)</tr>", page, flags=re.S):
        cells = dict(re.findall(r'<td class="([a-z]+)[^"]*"[^>]*>(.*?)</td>', tr, flags=re.S))
        if "name" not in cells:
            continue
        link = re.search(r'href="/wiki/([^"]+)"', cells["name"])
        src = " " + re.sub(r"\s+", " ", _text(cells.get("source", ""))) + " "
        cats = []
        for c in sorted(SOURCE_CATS, key=len, reverse=True):
            if f" {c} " in src:
                cats.append(CAT_NORM.get(c, c))
                src = src.replace(f" {c} ", " ")
        value = _text(cells.get("value", "")).split()[-1] if cells.get("value") else ""
        rows.append({
            "name": re.sub(r"\s+", " ", _text(cells["name"])),
            "page": urllib.parse.unquote(link.group(1)).replace("_", " ") if link else None,
            "type": _text(cells.get("type", "")),
            "value": value,
            "priority": _text(cells.get("priority", "")),
            "appraisable": "Yes" in cells.get("appraisable", "") or "✓" in cells.get("appraisable", ""),
            "sources": list(dict.fromkeys(cats)),
        })
    return rows


def page_texts(titles: list[str]) -> dict[str, str | None]:
    out: dict[str, str | None] = {}
    for i in range(0, len(titles), 50):
        batch = titles[i:i + 50]
        q = api(action="query", prop="revisions", rvprop="content", rvslots="main",
                redirects="1", titles="|".join(batch))["query"]
        norm = {r["from"]: r["to"] for r in q.get("normalized", [])}
        redir = {r["from"]: r["to"] for r in q.get("redirects", [])}
        got = {p["title"]: p["revisions"][0]["slots"]["main"]["content"]
               for p in q["pages"] if p.get("revisions")}
        for t in batch:
            k = norm.get(t, t)
            out[t] = got.get(redir.get(k, k))
    return out


def _clean(line: str) -> str:
    line = re.sub(r"\{\{\s*[A-Za-z ]+\|([^}|]+)(\|[^}]*)?\}\}", r"\1", line)
    line = re.sub(r"\[\[(?:[^\]|]*\|)?([^\]]*)\]\]", r"\1", line)
    line = re.sub(r"'''|''|<[^>]+>", "", line)
    return re.sub(r"\s+", " ", line.lstrip("*: ")).strip()


def obtainment(text: str | None) -> dict:
    """Templates and bullet lines of a mutation page's Obtainment section."""
    out: dict = {}
    if not text:
        return out
    m = re.search(r"==\s*Obtainment\s*==(.*?)(\n==[^=]|\Z)", text, flags=re.S)
    if not m:
        return out
    section = m.group(1)
    how = []
    for line in section.split("\n"):
        if not line.strip().startswith("*"):
            continue
        chance = re.search(r"(\d+(?:\.\d+)?)\s*%", re.sub(r"'''", "", line))
        for tpl, arg in re.findall(r"\{\{\s*([A-Za-z ]+?)\s*\|([^}|]+)", line):
            field = TEMPLATES.get(tpl.strip())
            if not field:
                continue
            arg = arg.strip()
            if field == "rods":
                rods = out.setdefault("rods", {})
                if arg not in rods:
                    rods[arg] = float(chance.group(1)) if chance else None
            else:
                lst = out.setdefault(field, [])
                if arg not in lst:
                    lst.append(arg)
        how.append(_clean(line)[:200])
    if how:
        out["how"] = how[:10]
    return out


def group_of(m: dict) -> str:
    t = m["type"].lower()
    if any(w in t for w in ("event", "limited", "admin", "exclusive", "unobtainable")):
        return "event"
    if m.get("rods") or "Fishing Rods" in m["sources"]:
        return "rod"
    if m.get("enchants") or "Enchantments" in m["sources"]:
        return "enchant"
    return "natural"


def build_mutations() -> dict:
    rows = mutation_table()
    texts = page_texts([r["page"] or r["name"] for r in rows])
    muts = []
    for r in rows:
        m = dict(r)
        m.update(obtainment(texts.get(r["page"] or r["name"])))
        try:
            m["value"] = float(m["value"].rstrip("×x").replace("–", "-").split("-")[-1])
        except ValueError:
            pass
        try:
            m["priority"] = int(m["priority"])
        except ValueError:
            pass
        m["group"] = group_of(m)
        m.pop("page", None)
        muts.append(m)
    return {"source": "https://fischipedia.org/wiki/Mutations",
            "fetched": date.today().isoformat(), "mutations": muts}


# ======================================================================================
# Wikitext helpers
# ======================================================================================


def wikitext(title: str) -> str:
    return page_texts([title])[title] or ""


def templates(text: str, name: str) -> list[dict]:
    """Every top-level {{name ...}} in text, as {param: value} (positional
    params as "1", "2"...). Nested templates inside values are kept as text."""
    out = []
    for m in re.finditer(r"\{\{\s*" + re.escape(name) + r"\s*[|\n}]", text, flags=re.I):
        i, depth = m.start(), 0
        j = i
        while j < len(text) - 1:
            two = text[j:j + 2]
            if two == "{{":
                depth += 1
                j += 2
                continue
            if two == "}}":
                depth -= 1
                j += 2
                if depth == 0:
                    break
                continue
            j += 1
        body = text[i + 2:j - 2]
        parts, depth, cur = [], 0, ""
        k = 0
        while k < len(body):
            two = body[k:k + 2]
            if two in ("{{", "[["):
                depth += 1
                cur += two
                k += 2
                continue
            if two in ("}}", "]]"):
                depth -= 1
                cur += two
                k += 2
                continue
            if body[k] == "|" and depth == 0:
                parts.append(cur)
                cur = ""
            else:
                cur += body[k]
            k += 1
        parts.append(cur)
        params, pos = {}, 1
        for p in parts[1:]:
            if "=" in p and re.match(r"\s*[\w ]+\s*=", p):
                key, val = p.split("=", 1)
                params[key.strip()] = val.strip()
            else:
                params[str(pos)] = p.strip()
                pos += 1
        out.append(params)
    return out


def icon_urls(files: list[str]) -> dict[str, str]:
    """File name -> download URL (wiki imageinfo)."""
    out: dict[str, str] = {}
    files = list(dict.fromkeys(f for f in files if f))
    for i in range(0, len(files), 50):
        q = api(action="query", prop="imageinfo", iiprop="url",
                titles="|".join("File:" + f for f in files[i:i + 50]))["query"]
        norm = {r["to"]: r["from"] for r in q.get("normalized", [])}
        for p in q["pages"]:
            if p.get("imageinfo"):
                name = norm.get(p["title"], p["title"])[len("File:"):]
                out[name] = p["imageinfo"][0]["url"]
    return out


def download_icons(files: dict[str, str], folder: str) -> dict[str, str]:
    """{key: wiki file} -> {key: local path relative to ui/}, saved as PNG."""
    urls = icon_urls(list(files.values()))
    base = OUT.parent / "icons" / folder
    base.mkdir(parents=True, exist_ok=True)
    out = {}
    for key, f in files.items():
        url = urls.get(f) or urls.get(f.replace("_", " "))
        if not url:
            continue
        safe = re.sub(r"[^A-Za-z0-9_.-]+", "_", key) + ".png"
        req = urllib.request.Request(url, headers=UA)
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                (base / safe).write_bytes(r.read())
            _shrink(base / safe)
            out[key] = f"icons/{folder}/{safe}"
        except OSError:
            pass
    return out


def _shrink(path: Path, size: int = 96) -> None:
    """Icons are matched against ~30px on-screen ones; the wiki serves them
    full size (6.7MB for 62 icons). Keep transparency."""
    import cv2

    im = cv2.imread(str(path), cv2.IMREAD_UNCHANGED)
    if im is None:
        return
    h, w = im.shape[:2]
    if max(h, w) > size:
        s = size / max(h, w)
        im = cv2.resize(im, (max(1, round(w * s)), max(1, round(h * s))),
                        interpolation=cv2.INTER_AREA)
        cv2.imwrite(str(path), im)


def _mutations_in(text: str) -> dict[str, float | None]:
    out: dict[str, float | None] = {}
    for name, pct in re.findall(r"\{\{Mutation\|([^}|]+)\}\}\s*(?:&\s*\{\{Mutation\|[^}]+\}\}\s*)?"
                                r"(?:\(\+?([\d.]+)%\))?", text):
        out[name.strip()] = float(pct) if pct else None
    return out


# ======================================================================================
# Weather (bottom-right icons: hover shows the name)
# ======================================================================================


def build_weather() -> dict:
    """Weathers, secondary weathers and server modifiers from the Weather
    page, with icons downloaded for matching the bottom-right of the screen."""
    text = wikitext("Weather")
    out = []
    for table in re.findall(r"\{\|(.*?)\n\|\}", text, flags=re.S):
        cap = re.search(r"\|\+\s*([^\n]+)", table)
        group = {"Weather Conditions": "weather", "Sovereign Weathers": "sovereign",
                 "Astral Anomalies": "astral"}.get(cap.group(1).strip() if cap else "", "weather")
        for name, row in re.findall(r'\|-\s*id="([^"]+)"(.*?)(?=\n\|-|\Z)', table, flags=re.S):
            icon = re.search(r"\[\[File:([^|\]]+)", row)
            totem = re.search(r"\{\{Item\|([^}|]*Totem)", row)
            lens = re.search(r"\{\{Item\|([^}|]*Lens)", row)
            out.append({
                "name": name, "group": group,
                "icon_file": icon.group(1).strip() if icon else None,
                "natural": "naturally" in row or group == "sovereign",
                "totem": totem.group(1).strip() if totem else None,
                "lens": lens.group(1).strip() if lens else None,
                "skippable": "{{Yesno|1}}" in row if "{{Yesno" in row else None,
                "mutations": _mutations_in(row),
                "effects": [_clean(l) for l in row.split("\n") if l.strip().startswith("*")][:10],
            })
    # Server modifiers (Shiny Surge, Mutation Surge...): <h4> blocks
    for block in re.findall(r"<h4>(.*?)(?=<h4>|</div>)", text, flags=re.S):
        head, _, body = block.partition("</h4>")
        icon = re.search(r"\[\[File:([^|\]]+)", head)
        name = _clean(re.sub(r"\[\[File:[^\]]+\]\]", "", head))
        totem = re.search(r"\{\{Item\|([^}|]*Totem)", body)
        out.append({"name": name, "group": "modifier",
                    "icon_file": icon.group(1).strip() if icon else None,
                    "natural": True, "totem": totem.group(1).strip() if totem else None,
                    "lens": None, "skippable": None, "mutations": _mutations_in(body),
                    "effects": [_clean(body.replace("----", ""))[:300]]})
    # Time of day and seasons, also shown as icons
    out += [{"name": n, "group": "time", "icon_file": f"{n}.png", "natural": True,
             "totem": "Sundial Totem", "lens": None, "skippable": None, "mutations": {},
             "effects": []} for n in ("Day", "Night")]
    seasons = wikitext("Template:Seasons")
    for f, n in re.findall(r"\[\[File:(([A-Z][a-z]+)[^|\]]*\.png)", seasons):
        if n in ("Spring", "Summer", "Autumn", "Winter") and not any(o["name"] == n for o in out):
            out.append({"name": n, "group": "season", "icon_file": f, "natural": True,
                        "totem": None, "lens": None, "skippable": None, "mutations": {},
                        "effects": []})
    icons = download_icons({o["name"]: o["icon_file"] for o in out if o["icon_file"]},
                           "weather")
    for o in out:
        o["icon"] = icons.get(o["name"])
    return {"source": "https://fischipedia.org/wiki/Weather",
            "fetched": date.today().isoformat(), "weather": out}


# ======================================================================================
# Totems (equip, then click to use)
# ======================================================================================


def build_totems() -> dict:
    text = wikitext("Totems")
    usage = re.search(r"==\s*Usage\s*==(.*?)\n==[^=]", text, flags=re.S)
    out = []
    section = None
    for m in re.finditer(r"\n(===\s*([^=\n]+?)\s*===|====\s*(.+?)\s*====)\n", text):
        if m.group(2):
            section = _clean(m.group(2))
            continue
        head = m.group(3)
        icon = re.search(r"\[\[File:([^|\]]+)", head)
        name = _clean(re.sub(r"\[\[File:[^\]]+\]\]", "", head))
        body = text[m.end():]
        body = body[:min([i for i in (body.find("\n===="), body.find("</div>"))
                          if i >= 0] or [len(body)])]
        effect = re.search(r"is an? (?:[\w ]+ )?totem (?:which|that) (.*?)(?:when interacted|\.\s)",
                           body, flags=re.S)
        weather = re.search(r"\{\{Weather\|([^}|]+)", effect.group(1)) if effect else None
        out.append({
            "name": name, "kind": section,
            "icon_file": icon.group(1).strip() if icon else None,
            "effect": _clean(effect.group(1)) if effect else None,
            "weather": weather.group(1).strip() if weather else None,
            "cooldown": name == "Sundial Totem",       # the user: only the Sundial has one
            "obtain": [_clean(l) for l in body.split("\n")
                       if l.strip().startswith("*")][:8],
        })
    icons = download_icons({o["name"]: o["icon_file"] for o in out if o["icon_file"]},
                           "totems")
    for o in out:
        o["icon"] = icons.get(o["name"])
    return {"source": "https://fischipedia.org/wiki/Totems",
            "fetched": date.today().isoformat(),
            "usage": _clean(usage.group(1))[:1500] if usage else None,
            "totems": out}


# ======================================================================================
# Quest givers
# ======================================================================================


def parse_task(line: str) -> dict:
    """One quest objective line: count, items (fish) and their required
    mutation/attributes ({{Fish|Abaia|attrs=Gusty}}), perfect-catch flag."""
    items, attrs = [], []
    for kind in ("Fish", "Item"):
        for t in templates(line, kind):
            if t.get("1"):
                items.append(t["1"])
            if t.get("attrs"):
                attrs += [a.strip() for a in t["attrs"].split(",")]
    count = re.search(r"\b(\d+)\b", _clean(line))
    return {"text": _clean(line), "count": int(count.group(1)) if count else None,
            "items": items, "mutations": list(dict.fromkeys(attrs)),
            "perfect": "perfect catch" in line.lower()}


def build_quests() -> dict:
    """Every NPC in the wiki's Quest NPCs category with its quests, plus the
    repeatable Angler (core NPC, 11 locations). The user wants main and
    repeatable quests; the wiki does not mark main vs minor reliably (the
    in-game Quest Book does), so all are kept with what is known."""
    members, cont = [], {}
    while True:
        j = api(action="query", list="categorymembers", cmtitle="Category:Quest NPCs",
                cmlimit="500", **cont)
        members += [m["title"] for m in j["query"]["categorymembers"]]
        if "continue" not in j:
            break
        cont = {"cmcontinue": j["continue"]["cmcontinue"]}
    members.append("Angler")
    texts = page_texts(members)
    npcs = []
    for title in members:
        text = texts.get(title) or ""
        box = (templates(text, "NPCInfoBox") or templates(text, "NPCInfobox") or [{}])[0]
        gps = templates(box.get("gps", ""), "Coordinates")
        quests = []
        for q in templates(text, "Quest"):
            steps = []
            for k in sorted((k for k in q if re.fullmatch(r"step\d+", k)),
                            key=lambda k: int(k[4:])):
                n = k[4:]
                tasks = [parse_task(l) for l in q.get("tasks" + n, "").split("\n") if l.strip()]
                steps.append({"step": _clean(q[k]), "tasks": tasks})
            icon = q.get("icon", "")
            quests.append({"name": _clean(q.get("name", "")), "icon": icon or None,
                           "repeatable": "challenge" in icon.lower(), "steps": steps})
        loc = box.get("location", "")
        npcs.append({
            "npc": re.sub(r"\s*\(.*?\)$", "", title) if title != "Angler" else "Angler",
            "page": title,
            "locations": [_clean(x) for x in loc.split(";") if x.strip()],
            "gps": [gps[0].get("1"), gps[0].get("2"), gps[0].get("3")] if gps else None,
            "core": box.get("core") == "1",
            "repeatable": title == "Angler" or any(q["repeatable"] for q in quests),
            "quests": quests,
        })
    return {"source": "https://fischipedia.org/wiki/Category:Quest_NPCs",
            "fetched": date.today().isoformat(), "npcs": npcs}


# ======================================================================================
# Fish and other fishables
# ======================================================================================


FISH_FIELDS = ("rarity", "type", "time", "weather", "season", "bait")


def build_fish() -> dict:
    """Everything catchable: the wiki's Category:Fish, which also holds the
    non-fish fishables (relics, crates...; `nonfish = 1` in the infobox).
    Used to split quest text like "Gusty Empyrean Relic" into the item and
    its mutation (an item name may itself contain a mutation word)."""
    members, cont = [], {}
    while True:
        j = api(action="query", list="categorymembers", cmtitle="Category:Fish",
                cmlimit="500", cmnamespace="0", **cont)
        members += [m["title"] for m in j["query"]["categorymembers"]]
        if "continue" not in j:
            break
        cont = {"cmcontinue": j["continue"]["cmcontinue"]}
    texts = page_texts(members)
    out = []
    for title in members:
        box = (templates(texts.get(title) or "", "FishInfobox") or [{}])[0]
        o = {"name": title, "nonfish": box.get("nonfish", "").strip() == "1",
             "locations": [_clean(x) for x in re.split(r"[;,]", box.get("bestiary", ""))
                           if _clean(x)]}
        for f in FISH_FIELDS:
            v = _clean(box.get(f, ""))
            if v:
                o[f] = v
        out.append(o)
    return {"source": "https://fischipedia.org/wiki/Category:Fish",
            "fetched": date.today().isoformat(), "fish": out}


# ======================================================================================
# Baits
# ======================================================================================


BAIT_STATS = {"pref_luck": "preferred_luck", "univ_luck": "universal_luck",
              "resilience": "resilience", "lure": "lure_speed"}


def build_baits() -> dict:
    """Every bait (Category:Bait): rarity, the four stats the bag's bait cards
    show, its ability, and the mutations that ability can give (Cupcakes:
    10% Birthday Candle) -- for the Useables tab and quest planning."""
    j = api(action="query", list="categorymembers", cmtitle="Category:Bait",
            cmlimit="500", cmnamespace="0")
    members = [m["title"] for m in j["query"]["categorymembers"]]
    texts = page_texts(members)
    out = []
    for title in members:
        box = (templates(texts.get(title) or "", "BaitInfobox") or [{}])[0]
        if not box:
            continue
        o = {"name": re.sub(r"\s*\(Bait\)$", "", title), "page": title,
             "rarity": _clean(box.get("rarity", "")) or None,
             "icon_file": (box.get("image") or "").strip() or None}
        for k, f in BAIT_STATS.items():
            v = re.sub(r"[^\d.-]", "", box.get(k, ""))
            o[f] = float(v) if re.fullmatch(r"-?\d+(\.\d+)?", v) else None
        ability = box.get("ability", "").strip()
        o["ability"] = _clean(ability) or None
        muts = {}
        for t in templates(ability, "Mutation"):
            pct = re.search(r"([\d.]+)%", ability)
            muts[t.get("1", "").strip()] = float(pct.group(1)) if pct else None
        o["mutations"] = {k: v for k, v in muts.items() if k}
        out.append(o)
    icons = download_icons({o["name"]: o["icon_file"] for o in out if o["icon_file"]}, "baits")
    for o in out:
        o["icon"] = icons.get(o["name"])
        del o["icon_file"]
    return {"source": "https://fischipedia.org/wiki/Category:Bait",
            "fetched": date.today().isoformat(), "baits": out}


def _write(name: str, data: dict) -> Path:
    path = OUT.parent / name
    path.write_text(json.dumps(data, indent=1, ensure_ascii=False), encoding="utf-8")
    return path


def main() -> None:
    what = sys.argv[1:] or ["mutations"]
    if "all" in what:
        what = ["mutations", "weather", "totems", "quests", "fish", "baits"]
    for w in what:
        if w == "mutations":
            data = build_mutations()
            OUT.write_text(json.dumps(data, indent=1, ensure_ascii=False), encoding="utf-8")
            groups: dict[str, int] = {}
            for m in data["mutations"]:
                groups[m["group"]] = groups.get(m["group"], 0) + 1
            print(f"{len(data['mutations'])} mutations -> {OUT}  groups: {groups}")
        elif w == "weather":
            data = build_weather()
            p = _write("weather.json", data)
            print(f"{len(data['weather'])} weathers/modifiers -> {p} "
                  f"({sum(1 for o in data['weather'] if o['icon'])} icons)")
        elif w == "totems":
            data = build_totems()
            p = _write("totems.json", data)
            print(f"{len(data['totems'])} totems -> {p} "
                  f"({sum(1 for o in data['totems'] if o['icon'])} icons)")
        elif w == "quests":
            data = build_quests()
            p = _write("quests.json", data)
            nq = sum(len(n["quests"]) for n in data["npcs"])
            print(f"{len(data['npcs'])} quest NPCs, {nq} quests -> {p}")
        elif w == "fish":
            data = build_fish()
            p = _write("fish.json", data)
            print(f"{len(data['fish'])} fishables -> {p} "
                  f"({sum(1 for o in data['fish'] if o['nonfish'])} non-fish)")
        elif w == "baits":
            data = build_baits()
            p = _write("baits.json", data)
            print(f"{len(data['baits'])} baits -> {p} "
                  f"({sum(1 for o in data['baits'] if o['icon'])} icons)")
        else:
            raise SystemExit("usage: python fischwiki.py "
                             "[mutations|weather|totems|quests|fish|baits|all]")


if __name__ == "__main__":
    main()
