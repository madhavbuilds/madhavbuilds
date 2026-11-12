#!/usr/bin/env python3
"""Render the animated SVG cards for the madhavbuilds profile README.

    python render_cards.py            animated cards into cards/
    STATIC=1 python render_cards.py   final frame into preview/, plus preview/index.html

Reads data/github.json (written daily by .github/workflows/profile-cards.yml).
Needs: pip install fonttools pillow
"""
import base64
import datetime as dt
import html
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.parse
import urllib.request
from pathlib import Path

from fontTools.ttLib import TTFont
from fontTools.varLib import instancer

ROOT = Path(__file__).resolve().parent
STATIC = os.environ.get("STATIC") == "1"
OUT = ROOT / ("preview" if STATIC else "cards")
DATA = ROOT / "data" / "github.json"
README = ROOT / "README.md"
FONT_CACHE = ROOT / ".fontcache"

# --------------------------------------------------------------------------------------
# Content. Edit freely.
# --------------------------------------------------------------------------------------
PROFILE = {
    "name": "Madhav Panchal",
    "line": "I build websites, apps, AI tools and Chrome extensions, and ship them.",
    "status": "Open to freelance and remote work",
    "place": "Based in Helsinki",
    "href": "https://madhavpanchal.com/",
    # Optional. Any square-ish JPG/PNG; it is cropped to a circle. Leave missing for no portrait.
    "portrait": ROOT / "assets" / "portrait.jpg",
}

TILES = [
    {"slug": "website", "label": "Website", "handle": "madhavpanchal.com",
     "href": "https://madhavpanchal.com/"},
    {"slug": "linkedin", "label": "LinkedIn", "handle": "in/madhavpanchal",
     "href": "https://www.linkedin.com/in/madhavpanchal/"},
    {"slug": "email", "label": "Email", "handle": "madhavpanchal1716@gmail.com",
     "href": "mailto:madhavpanchal1716@gmail.com"},
]

# Repos to feature, in order. Set to None to use your pinned repos instead.
FEATURED = ["neurogenesis", "lantern", "create-own-ai", "EvalKit", "Vett"]
# Card descriptions. Falls back to the GitHub description for repos not listed here.
DESCRIPTIONS = {
    "neurogenesis":
        "A neural network that starts with one neuron, grows where it gets stuck, prunes "
        "what it doesn't use and stops growing on its own. NumPy only.",
    "lantern":
        "A local answer engine. Searches the web through SearXNG and answers with cited "
        "sources from your own Ollama model, with nothing leaving your machine.",
    "create-own-ai":
        "One command builds a chat-with-your-files app that runs entirely on your computer. "
        "Cited answers from PDFs, docs and code, with no API keys or cloud.",
    "EvalKit":
        "An open benchmark for LLMs in five Indian languages. Tests Hinglish input, real "
        "Indian tasks, cultural knowledge and safety.",
    "Vett":
        "Fast codebase health checks for leaked secrets, risky APIs, complexity and TODOs, "
        "with optional AI analysis and a Markdown report.",
    "AI-powered-Traffic-Analysis-System":
        "Real-time vehicle detection and tracking with YOLOv5. Estimates speeds, counts "
        "vehicles and shows traffic flow from a top-down view.",
}
# Optional note shown in brand teal on a repo card, e.g. where it's published.
HIGHLIGHTS = {
    "create-own-ai": "Published on npm",
    "EvalKit": "Dataset on Hugging Face",
    "Vett": "Published on PyPI",
}

# --------------------------------------------------------------------------------------
# Brand. Colours follow madhavpanchal.com (stone neutrals, theme colour #fafaf9) and the
# teal you already use across the README.
# --------------------------------------------------------------------------------------
C = {
    "bg": "#fafaf9",          # stone-50, the site's theme colour
    "ink": "#1c1917",         # stone-900
    "body": "#57534e",        # stone-600
    "muted": "#78716c",       # stone-500
    "line": "#e7e5e4",        # stone-200
    "brand": "#5DCAA5",
    "brand_deep": "#1D9E75",
    "brand_ink": "#0F6E56",   # brand teal dark enough for text on bg
}

INTER = ("https://raw.githubusercontent.com/google/fonts/main/ofl/inter/"
         "Inter%5Bopsz%2Cwght%5D.ttf")
# role -> font file (URL or local path) and variable-axis instance.
FONTS = {
    "r": {"src": INTER, "axes": {"wght": 400, "opsz": 14}},
    "s": {"src": INTER, "axes": {"wght": 600, "opsz": 14}},
    "d": {"src": INTER, "axes": {"wght": 700, "opsz": 32}},
}
WEIGHT = {"r": 400, "s": 600, "d": 700}
FALLBACK = 'system-ui,-apple-system,"Segoe UI",Roboto,Helvetica,Arial,sans-serif'

W = 840          # full-width card viewBox width
PAD = 32
RADIUS = 16

# Start time (seconds) of each card, so the README plays top to bottom.
T_HERO = 0.0
T_TILES = 0.55   # + 0.1 per tile
T_MONTHLY = 0.95
T_REPOS = 1.55   # + 0.18 per repo
T_REPO_STEP = 0.18


# --------------------------------------------------------------------------------------
# Fonts: download, instance, measure (with GPOS kerning), subset to WOFF.
# --------------------------------------------------------------------------------------
def fetch(src):
    if not src.startswith("http"):
        return Path(src)
    FONT_CACHE.mkdir(exist_ok=True)
    dest = FONT_CACHE / re.sub(r"[^\w.-]", "_", urllib.parse.unquote(src.rsplit("/", 1)[-1]))
    if not dest.exists():
        req = urllib.request.Request(src, headers={"User-Agent": "render-cards"})
        with urllib.request.urlopen(req, timeout=60) as r:
            dest.write_bytes(r.read())
    return dest


def load_kerning(font):
    pairs, class_tables = {}, []
    if "GPOS" in font:
        gpos = font["GPOS"].table
        idx = set()
        for fr in gpos.FeatureList.FeatureRecord:
            if fr.FeatureTag == "kern":
                idx.update(fr.Feature.LookupListIndex)
        for i in sorted(idx):
            lookup = gpos.LookupList.Lookup[i]
            for st in lookup.SubTable:
                if lookup.LookupType == 9:
                    if st.ExtensionLookupType != 2:
                        continue
                    st = st.ExtSubTable
                elif lookup.LookupType != 2:
                    continue
                if st.Format == 1:
                    for first, ps in zip(st.Coverage.glyphs, st.PairSet):
                        for rec in ps.PairValueRecord:
                            v = getattr(rec.Value1, "XAdvance", 0) or 0
                            pairs.setdefault((first, rec.SecondGlyph), v)
                elif st.Format == 2:
                    class_tables.append((set(st.Coverage.glyphs), st.ClassDef1.classDefs,
                                         st.ClassDef2.classDefs, st.Class1Record))
    cache = {}

    def kern(a, b):
        if (a, b) in cache:
            return cache[(a, b)]
        v = pairs.get((a, b))
        if v is None:
            v = 0
            for cov, cd1, cd2, recs in class_tables:
                if a in cov:
                    val = recs[cd1.get(a, 0)].Class2Record[cd2.get(b, 0)].Value1
                    v = getattr(val, "XAdvance", 0) or 0
                    if v:
                        break
        cache[(a, b)] = v
        return v
    return kern


class Face:
    def __init__(self, role, spec):
        self.role = role
        src = fetch(spec["src"])
        axes = spec.get("axes") or {}
        tag = "-".join(f"{k}{v}" for k, v in sorted(axes.items()))
        self.path = FONT_CACHE / f"{src.stem}-{tag}.ttf".replace("[", "").replace("]", "")
        FONT_CACHE.mkdir(exist_ok=True)
        if not self.path.exists():
            f = TTFont(src)
            if "fvar" in f and axes:
                f = instancer.instantiateVariableFont(f, axes)
            f.save(self.path)
        self.font = TTFont(self.path)
        self.upm = self.font["head"].unitsPerEm
        self.cmap = self.font.getBestCmap()
        self.hmtx = self.font["hmtx"].metrics
        self.kern = load_kerning(self.font)

    def has(self, ch):
        return ord(ch) in self.cmap

    def glyphs(self, s):
        return [self.cmap.get(ord(ch), ".notdef") for ch in s]

    def width(self, s, size, ls=0.0):
        gs = self.glyphs(s)
        units = sum(self.hmtx[g][0] for g in gs)
        units += sum(self.kern(a, b) for a, b in zip(gs, gs[1:]))
        return units * size / self.upm + ls * len(s)

    def offsets(self, s, size, ls=0.0):
        """x offset of each character when the string is set as one run."""
        gs, xs, x = self.glyphs(s), [], 0.0
        for i, g in enumerate(gs):
            xs.append(x)
            x += self.hmtx[g][0] * size / self.upm + ls
            if i + 1 < len(gs):
                x += self.kern(g, gs[i + 1]) * size / self.upm
        return xs

    def woff_b64(self, chars):
        tool = shutil.which("pyftsubset")
        cmd = [tool] if tool else [sys.executable, "-m", "fontTools.subset"]
        with tempfile.TemporaryDirectory() as tmp:
            txt, out = Path(tmp) / "chars.txt", Path(tmp) / "out.woff"
            txt.write_text("".join(sorted(chars)), encoding="utf-8")
            subprocess.run(cmd + [str(self.path), f"--text-file={txt}", f"--output-file={out}",
                                  "--flavor=woff", "--layout-features=kern", "--no-hinting",
                                  "--desubroutinize"], check=True)
            return base64.b64encode(out.read_bytes()).decode()


FACES = {}


def face(role):
    if role not in FACES:
        FACES[role] = Face(role, FONTS[role])
    return FACES[role]


def clean(s, role="r"):
    """Drop characters the font can't draw (emoji etc.) and tidy whitespace."""
    f = face(role)
    s = "".join(ch for ch in (s or "") if ch.isspace() or f.has(ch))
    return re.sub(r"\s+", " ", s).strip()


def wrap(s, role, size, max_w):
    f, lines, cur = face(role), [], ""
    for word in s.split():
        trial = f"{cur} {word}".strip()
        if cur and f.width(trial, size) > max_w:
            lines.append(cur)
            cur = word
        else:
            cur = trial
    if cur:
        lines.append(cur)
    return lines


def fit(s, role, size, max_w, min_size=10):
    while size > min_size and face(role).width(s, size) > max_w:
        size -= 0.5
    return size


# --------------------------------------------------------------------------------------
# Card builder
# --------------------------------------------------------------------------------------
CSS_MOTION = """
@keyframes rise{from{opacity:0;transform:translateY(12px)}}
@keyframes fade{from{opacity:0}}
@keyframes grow{from{transform:scaleY(0)}}
@keyframes pop{from{opacity:0;transform:scale(.3)}}
@keyframes draw{from{stroke-dashoffset:1}}
@keyframes nudge{from{opacity:0;transform:translate(-6px,6px)}}
.rise{animation:rise .7s cubic-bezier(.2,.7,.2,1) both}
.fade{animation:fade .6s ease-out both}
.grow{transform-box:fill-box;transform-origin:50% 100%;animation:grow .75s cubic-bezier(.3,.8,.2,1) both}
.pop{transform-box:fill-box;transform-origin:50% 50%;animation:pop .45s cubic-bezier(.3,1.5,.5,1) both}
.draw{stroke-dasharray:1 1;animation:draw 1.2s cubic-bezier(.6,0,.2,1) both}
.nudge{animation:nudge .5s cubic-bezier(.2,.7,.2,1) both}
@media (prefers-reduced-motion:reduce){*{animation:none!important}.smil{display:none}}
"""


def esc(s):
    return html.escape(str(s), quote=True)


class Card:
    def __init__(self, w, h, title, t0=0.0):
        self.w, self.h, self.title, self.t0 = w, h, title, t0
        self.parts, self.chars = [], {}

    def add(self, raw):
        self.parts.append(raw)

    def anim(self, kind, at):
        """class + delay attributes for an entrance, `at` seconds after this card starts."""
        if STATIC or not kind:
            return ""
        return f' class="{kind}" style="animation-delay:{self.t0 + at:.2f}s"'

    def smil(self, raw):
        """SMIL extras are decorative only: hidden under reduced motion, left out when STATIC."""
        if not STATIC:
            self.add(raw)

    def begin(self, at):
        return f"{self.t0 + at:.2f}s"

    def _use(self, role, s):
        self.chars.setdefault(role, set()).update(s)

    def run(self, s, role, size, fill, ls=None):
        """A tspan for a mixed-style text line."""
        self._use(role, s)
        ls_attr = f' letter-spacing="{ls}"' if ls else ""
        return (f'<tspan class="{role}" font-size="{size:g}" fill="{fill}"{ls_attr}>'
                f"{esc(s)}</tspan>")

    def text(self, x, y, s, role, size, fill, anchor=None, ls=None, kind=None, at=0.0):
        self._use(role, s)
        a = f' text-anchor="{anchor}"' if anchor else ""
        l = f' letter-spacing="{ls}"' if ls else ""
        self.add(f'<g{self.anim(kind, at)}><text x="{x:.1f}" y="{y:.1f}" class="{role}" '
                 f'font-size="{size:g}" fill="{fill}"{a}{l}>{esc(s)}</text></g>')

    def runs(self, x, y, spans, anchor=None, kind=None, at=0.0):
        a = f' text-anchor="{anchor}"' if anchor else ""
        self.add(f'<g{self.anim(kind, at)}><text x="{x:.1f}" y="{y:.1f}"{a}>'
                 + "".join(spans) + "</text></g>")

    def frame(self):
        return (f'<rect x=".5" y=".5" width="{self.w - 1}" height="{self.h - 1}" '
                f'rx="{RADIUS}" fill="{C["bg"]}" stroke="{C["line"]}"/>')

    def svg(self):
        faces = []
        for role, chars in sorted(self.chars.items()):
            faces.append(f'@font-face{{font-family:"mp-{role}";font-weight:{WEIGHT[role]};'
                         f'src:url(data:font/woff;base64,{face(role).woff_b64(chars | {" "})}) '
                         f'format("woff")}}')
            faces.append(f'.{role}{{font-family:"mp-{role}",{FALLBACK};'
                         f'font-weight:{WEIGHT[role]}}}')
        css = "\n".join(faces) + ("" if STATIC else CSS_MOTION)
        return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {self.w} {self.h}" '
                f'width="{self.w}" height="{self.h}" role="img" aria-label="{esc(self.title)}">'
                f"<title>{esc(self.title)}</title><style>{css}</style>"
                f"{self.frame()}{''.join(self.parts)}</svg>")


# --------------------------------------------------------------------------------------
# Data
# --------------------------------------------------------------------------------------
def load_data():
    raw = json.loads(DATA.read_text(encoding="utf-8"))
    user = raw.get("data", raw)["user"]
    cal = user["contributionsCollection"]["contributionCalendar"]
    weeks = [[(dt.date.fromisoformat(d["date"]), d["contributionCount"])
              for d in w["contributionDays"]] for w in cal["weeks"]]
    days = [d for w in weeks for d in w]
    pinned = [n for n in user.get("pinnedItems", {}).get("nodes", []) if n]
    repos = {n["name"]: n for n in user.get("repositories", {}).get("nodes", []) if n}
    repos.update({n["name"]: n for n in pinned})
    if FEATURED is None:
        featured = pinned
    else:
        featured = [repos[n] for n in FEATURED if n in repos]
        missing = [n for n in FEATURED if n not in repos]
        if missing:
            print(f"warning: not found in data/github.json: {', '.join(missing)}")
    return {"weeks": weeks, "days": days, "total": cal["totalContributions"],
            "featured": featured, "login": user.get("login", "")}


def stats(days):
    counts = [c for _, c in days]
    i = len(counts) - 1
    if i >= 0 and counts[i] == 0:      # today isn't over yet; don't break the streak for it
        i -= 1
    current = 0
    while i >= 0 and counts[i] > 0:
        current += 1
        i -= 1
    longest = run = 0
    for c in counts:
        run = run + 1 if c else 0
        longest = max(longest, run)
    busiest = max(days, key=lambda d: (d[1], d[0])) if days else (None, 0)
    return {"current": current, "longest": longest,
            "active": sum(1 for c in counts if c), "busiest": busiest}


def mix(a, b, t):
    pa = [int(a[i:i + 2], 16) for i in (1, 3, 5)]
    pb = [int(b[i:i + 2], 16) for i in (1, 3, 5)]
    return "#" + "".join(f"{round(x + (y - x) * t):02x}" for x, y in zip(pb, pa))


def fmt_date(d, year=False):
    return f"{d.day} {d.strftime('%b')}" + (f" {d.year}" if year else "")


def plural(n, word):
    return f"{n:,} {word}{'' if n == 1 else 's'}"


# --------------------------------------------------------------------------------------
# Cards
# --------------------------------------------------------------------------------------
def portrait_b64(path):
    if not path or not Path(path).exists():
        return None
    try:
        from PIL import Image
    except ImportError:
        print("warning: install pillow to embed the portrait")
        return None
    im = Image.open(path).convert("RGB")
    s = min(im.size)
    im = im.crop(((im.width - s) // 2, (im.height - s) // 2,
                  (im.width + s) // 2, (im.height + s) // 2)).resize((360, 360), Image.LANCZOS)
    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=84, optimize=True)
    return base64.b64encode(buf.getvalue()).decode()


def card_hero():
    p = PROFILE
    H = 236
    card = Card(W, H, f"{p['name']}. {p['line']}", T_HERO)
    img = portrait_b64(p.get("portrait"))
    r = 82
    cx, cy = W - PAD - 6 - r, H / 2
    text_w = (cx - r - 40 - PAD) if img else (W - 2 * PAD)
    x = PAD + 4

    # Name, set letter by letter so it can rise in sequence.
    size, ls = 58, -1.7
    size = fit(p["name"], "d", size, text_w)
    xs = face("d").offsets(p["name"], size, ls)
    card._use("d", p["name"])
    for i, (ch, dx) in enumerate(zip(p["name"], xs)):
        if ch == " ":
            continue
        card.add(f'<g{card.anim("rise", 0.05 + i * 0.035)}><text x="{x + dx:.1f}" y="104" '
                 f'class="d" font-size="{size:g}" fill="{C["ink"]}">{esc(ch)}</text></g>')

    line = clean(p["line"])
    lsize = fit(line, "r", 19, text_w)
    card.text(x, 142, line, "r", lsize, C["body"], kind="rise", at=0.55)

    # Availability, with a dot that ripples a few times.
    dot_x, dot_y = x + 5, 186
    card.add(f'<g{card.anim("fade", 0.75)}><circle cx="{dot_x}" cy="{dot_y}" r="5" '
             f'fill="{C["brand_deep"]}"/></g>')
    card.smil(f'<circle class="smil" cx="{dot_x}" cy="{dot_y}" r="5" fill="none" '
              f'stroke="{C["brand_deep"]}" stroke-width="1.5" opacity="0">'
              f'<animate attributeName="r" values="5;15" dur="1.8s" begin="{card.begin(1.0)}" '
              f'repeatCount="3"/><animate attributeName="opacity" values=".8;0" dur="1.8s" '
              f'begin="{card.begin(1.0)}" repeatCount="3"/></circle>')
    card.runs(x + 18, dot_y + 5.5, [
        card.run(p["status"] + ". ", "s", 15, C["ink"]),
        card.run(p["place"] + ".", "r", 15, C["muted"]),
    ], kind="rise", at=0.75)

    if img:
        card.add(f'<defs><clipPath id="pc"><circle cx="{cx}" cy="{cy}" r="{r}"/></clipPath></defs>'
                 f'<g{card.anim("fade", 0.3)}><image href="data:image/jpeg;base64,{img}" '
                 f'x="{cx - r}" y="{cy - r}" width="{2 * r}" height="{2 * r}" '
                 f'clip-path="url(#pc)" preserveAspectRatio="xMidYMid slice"/></g>')
        card.add(f'<circle cx="{cx}" cy="{cy}" r="{r + 7}" fill="none" stroke="{C["brand"]}" '
                 f'stroke-width="2" stroke-linecap="round" pathLength="1" '
                 f'transform="rotate(-90 {cx} {cy})"{card.anim("draw", 0.3)}/>')
    return card


def card_tile(tile, i):
    TW, TH = 280, 112
    card = Card(TW, TH, f"{tile['label']}: {tile['handle']}", T_TILES + 0.1 * i)
    card.text(24, 50, tile["label"], "s", 22, C["ink"], kind="rise", at=0.0)
    hsize = fit(tile["handle"], "r", 14, TW - 48)
    card.text(24, 84, tile["handle"], "r", hsize, C["muted"], kind="rise", at=0.08)
    ax, ay = TW - 24, 24   # external-link arrow, top right
    card.add(f'<g{card.anim("nudge", 0.3)}><path d="M{ax - 11} {ay + 11} L{ax} {ay} M{ax - 8} {ay} '
             f'H{ax} V{ay + 8}" fill="none" stroke="{C["brand_deep"]}" stroke-width="2" '
             f'stroke-linecap="round" stroke-linejoin="round"/></g>')
    return card


def card_monthly(data):
    days, H = data["days"], 324
    st = stats(days)
    last = days[-1][0]
    months, y, m = [], last.year, last.month
    for _ in range(12):
        months.insert(0, (y, m))
        y, m = (y, m - 1) if m > 1 else (y - 1, 12)
    sums = {k: 0 for k in months}
    for d, c in days:
        if (d.year, d.month) in sums:
            sums[(d.year, d.month)] += c
    total = sum(sums.values())
    cur_name = last.strftime("%B")

    card = Card(W, H, f"{total} contributions in the last 12 months. Current streak "
                      f"{st['current']} days, {st['active']} active days, busiest day "
                      f"{fmt_date(st['busiest'][0], True)} with {st['busiest'][1]}.", T_MONTHLY)
    card.text(PAD, 54, "Contributions per month", "s", 17, C["ink"], kind="rise", at=0)
    card.text(PAD, 78, f"{total:,} in the last 12 months, {cur_name} still counting",
              "r", 14, C["muted"], kind="rise", at=0.06)

    # Bars
    x0, x1, base, top = PAD, 556, 270, 128
    slot = (x1 - x0) / 12
    bw = 26
    peak = max(sums.values()) or 1
    card.add(f'<line x1="{x0}" x2="{x1}" y1="{base + .5}" y2="{base + .5}" stroke="{C["line"]}"/>')
    for i, key in enumerate(months):
        v = sums[key]
        h = max(2.0, (base - top) * v / peak) if v else 0
        cx = x0 + slot * i + slot / 2
        is_cur = i == len(months) - 1
        fill = mix(C["brand"], C["bg"], 0.45) if is_cur else (
            C["brand_deep"] if v == peak else C["brand"])
        at = 0.2 + i * 0.05
        if h:
            card.add(f'<rect x="{cx - bw / 2:.1f}" y="{base - h:.1f}" width="{bw}" height="{h:.1f}" '
                     f'rx="3" fill="{fill}"{card.anim("grow", at)}/>')
        card.text(cx, base - h - 8, f"{v:,}", "s" if v == peak else "r", 12,
                  C["ink"] if v == peak else C["muted"], anchor="middle", kind="fade", at=at + 0.45)
        mname = dt.date(key[0], key[1], 1).strftime("%b")
        card.text(cx, base + 20, mname, "r", 12, C["muted"], anchor="middle", kind="fade", at=at)
        if i == 0 or key[1] == 1:
            card.text(cx, base + 36, str(key[0]), "r", 11, mix(C["muted"], C["bg"], 0.7),
                      anchor="middle", kind="fade", at=at)

    # Stats column
    sx = 604
    card.add(f'<line x1="{sx - 20.5}" x2="{sx - 20.5}" y1="104" y2="{base + 36}" '
             f'stroke="{C["line"]}"/>')
    streak_label = "Current streak" if st["current"] >= st["longest"] else \
        f"Current streak, longest {st['longest']}"
    bd, bc = st["busiest"]
    rows = [
        (streak_label, f"{st['current']}", " day" if st["current"] == 1 else " days"),
        ("Active days in the last year", f"{st['active']:,}", ""),
        ("Busiest day", f"{bc:,}", f" on {fmt_date(bd)}" if bd else ""),
    ]
    for j, (label, big, small) in enumerate(rows):
        y = 120 + j * 68
        card.text(sx, y, label, "r", 13, C["muted"], kind="rise", at=0.5 + j * 0.1)
        spans = [card.run(big, "d", 30, C["ink"], ls=-0.6)]
        if small:
            spans.append(card.run(small, "r", 15, C["body"]))
        card.runs(sx, y + 34, spans, kind="rise", at=0.55 + j * 0.1)
    return card


def star_path(cx, cy, R, r):
    import math
    pts = []
    for k in range(10):
        a = -math.pi / 2 + k * math.pi / 5
        rad = R if k % 2 == 0 else r
        pts.append(f"{cx + rad * math.cos(a):.2f},{cy + rad * math.sin(a):.2f}")
    return "M" + " L".join(pts) + " Z"


def card_repo(repo, i):
    name = repo["name"]
    desc = clean(DESCRIPTIONS.get(name) or repo.get("description") or "")
    lines = wrap(desc, "r", 15, 620)[:3] if desc else []
    first = 82
    meta_y = (first + 24 * (len(lines) - 1) if lines else 50) + 38
    H = int(meta_y + 26)
    card = Card(W, H, f"{name}. {desc}", T_REPOS + T_REPO_STEP * i)

    nsize = fit(name, "s", 20, W - 2 * PAD)
    card.text(PAD, 50, name, "s", nsize, C["ink"], kind="rise", at=0)
    for k, ln in enumerate(lines):
        card.text(PAD, first + 24 * k, ln, "r", 15, C["body"], kind="rise", at=0.06 + 0.04 * k)

    x, at = PAD, 0.2
    lang = repo.get("primaryLanguage")
    if lang:
        card.add(f'<g{card.anim("pop", at)}><circle cx="{x + 5}" cy="{meta_y - 4.5}" r="5" '
                 f'fill="{lang.get("color") or C["muted"]}"/></g>')
        card.text(x + 16, meta_y, lang["name"], "r", 13, C["body"], kind="fade", at=at)
        x += 16 + face("r").width(lang["name"], 13) + 22
    for count, word, icon in ((repo.get("stargazerCount", 0), "star", "star"),
                              (repo.get("forkCount", 0), "fork", "fork")):
        if not count:
            continue
        if icon == "star":
            shape = (f'<path d="{star_path(x + 6, meta_y - 4.5, 6.2, 2.7)}" '
                     f'fill="{C["muted"]}"/>')
        else:
            gx, gy = x + 6, meta_y - 4.5
            dots = "".join(f'<circle cx="{a:.1f}" cy="{b:.1f}" r="1.9"/>'
                           for a, b in ((gx - 3.6, gy - 4.6), (gx + 3.6, gy - 4.6), (gx, gy + 4.6)))
            shape = (f'<g fill="none" stroke="{C["muted"]}" stroke-width="1.4">{dots}'
                     f'<path d="M{gx - 3.6} {gy - 2.7} V{gy - 1.4} Q{gx - 3.6} {gy} {gx - 2} {gy} '
                     f'H{gx + 2} Q{gx + 3.6} {gy} {gx + 3.6} {gy - 1.4} V{gy - 2.7} M{gx} {gy} '
                     f'V{gy + 2.7}"/></g>')
        card.add(f'<g{card.anim("pop", at + 0.05)}>{shape}</g>')
        label = plural(count, word)
        card.text(x + 17, meta_y, label, "r", 13, C["body"], kind="fade", at=at + 0.05)
        x += 17 + face("r").width(label, 13) + 22
    note = HIGHLIGHTS.get(name)
    if note:
        card.text(x, meta_y, note, "s", 13, C["brand_ink"], kind="fade", at=at + 0.1)
    if repo.get("pushedAt"):
        d = dt.datetime.fromisoformat(repo["pushedAt"].replace("Z", "+00:00")).date()
        card.text(W - PAD, meta_y, f"Updated {fmt_date(d, True)}", "r", 13, C["muted"],
                  anchor="end", kind="fade", at=at + 0.1)
    return card


def card_graph(data, t0):
    weeks, days = data["weeks"], data["days"]
    gap = 3
    step = (W - 2 * PAD + gap) / len(weeks)
    cell = step - gap
    top = 100
    H = int(top + 7 * step + 28)
    total = data["total"]
    card = Card(W, H, f"{total:,} contributions in the last year", t0)
    card.text(PAD, 54, f"{plural(total, 'contribution')} in the last year", "s", 17, C["ink"],
              kind="rise", at=0)

    nonzero = sorted(c for _, c in days if c)

    def q(p):
        return nonzero[min(len(nonzero) - 1, int(p * len(nonzero)))] if nonzero else 0
    cuts = [q(.25), q(.5), q(.75)]
    levels = [C["line"], mix(C["brand"], C["bg"], 0.35), mix(C["brand"], C["bg"], 0.7),
              C["brand"], C["brand_deep"]]

    def level(c):
        if not c:
            return 0
        return 1 + sum(c > t for t in cuts)

    # Legend, right-aligned with the title
    lx = W - PAD
    more_w = face("r").width("More", 12)
    card.text(lx, 52, "More", "r", 12, C["muted"], anchor="end", kind="fade", at=0.1)
    lx -= more_w + 8 + 5 * (cell + 3) - 3
    for k, col in enumerate(levels):
        card.add(f'<rect x="{lx + k * (cell + 3):.1f}" y="{52 - cell + 1:.1f}" width="{cell:.1f}" '
                 f'height="{cell:.1f}" rx="2.5" fill="{col}"{card.anim("fade", 0.1)}/>')
    card.text(lx - 8, 52, "Less", "r", 12, C["muted"], anchor="end", kind="fade", at=0.1)

    # Month labels
    prev_month, last_label = None, -9
    for wi, week in enumerate(weeks):
        mth = week[0][0].month
        if mth != prev_month and wi - last_label >= 3 and wi < len(weeks) - 2:
            if prev_month is not None or week[0][0].day <= 7:
                card.text(PAD + wi * step, top - 10, week[0][0].strftime("%b"), "r", 12,
                          C["muted"], kind="fade", at=0.15 + wi * 0.016)
                last_label = wi
        prev_month = mth

    # Cells, revealed in a diagonal wave
    for wi, week in enumerate(weeks):
        for d, c in week:
            row = (d.weekday() + 1) % 7      # Sunday first, like GitHub
            x, y = PAD + wi * step, top + row * step
            card.add(f'<rect x="{x:.1f}" y="{y:.1f}" width="{cell:.1f}" height="{cell:.1f}" '
                     f'rx="2.5" fill="{levels[level(c)]}"'
                     f'{card.anim("pop", 0.2 + wi * 0.016 + row * 0.025)}><title>'
                     f'{esc(plural(c, "contribution"))} on {fmt_date(d, True)}</title></rect>')

    # Mark today once the wave has passed
    d, c = days[-1]
    wi, row = len(weeks) - 1, (d.weekday() + 1) % 7
    x, y = PAD + wi * step - 2, top + row * step - 2
    card.smil(f'<rect class="smil" x="{x:.1f}" y="{y:.1f}" width="{cell + 4:.1f}" '
              f'height="{cell + 4:.1f}" rx="4" fill="none" stroke="{C["ink"]}" stroke-width="1.5" '
              f'opacity="0"><animate attributeName="opacity" values="0;1;0" dur="1.4s" '
              f'begin="{card.begin(1.4)}" repeatCount="2"/></rect>')
    return card


# --------------------------------------------------------------------------------------
# Output
# --------------------------------------------------------------------------------------
def slug(s):
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")


def readme_block(cards_dir, repos, data):
    p = PROFILE
    lines = [
        f'<a href="{p["href"]}"><img src="{cards_dir}/hero.svg" width="100%" '
        f'alt="{esc(p["name"])}. {esc(p["line"])}"></a>',
        "",
        '<p align="center">',
    ]
    for t in TILES:
        lines.append(f'<a href="{t["href"]}"><img src="{cards_dir}/tile-{t["slug"]}.svg" '
                     f'width="32.5%" alt="{esc(t["label"])}: {esc(t["handle"])}"></a>')
    lines += ["</p>", "",
              f'<img src="{cards_dir}/contributions-monthly.svg" width="100%" '
              f'alt="Contributions per month, with streak, active days and busiest day">', ""]
    for r in repos:
        lines += [f'<a href="{r["url"]}"><img src="{cards_dir}/repo-{slug(r["name"])}.svg" '
                  f'width="100%" alt="{esc(r["name"])}"></a>', ""]
    lines += [f'<img src="{cards_dir}/contribution-graph.svg" width="100%" '
                  f'alt="Contribution graph for the last year">']
    return "\n".join(lines)


def main():
    if not DATA.exists():
        sys.exit(f"missing {DATA.relative_to(ROOT)}; see the README in this folder for the gh command")
    data = load_data()
    OUT.mkdir(exist_ok=True)
    for old in OUT.glob("*.svg"):
        old.unlink()

    repos = data["featured"]
    cards = {"hero": card_hero()}
    for i, t in enumerate(TILES):
        cards[f"tile-{t['slug']}"] = card_tile(t, i)
    cards["contributions-monthly"] = card_monthly(data)
    for i, r in enumerate(repos):
        cards[f"repo-{slug(r['name'])}"] = card_repo(r, i)
    cards["contribution-graph"] = card_graph(data, T_REPOS + T_REPO_STEP * len(repos) + 0.15)

    for name, card in cards.items():
        svg = card.svg()
        (OUT / f"{name}.svg").write_text(svg, encoding="utf-8")
        print(f"{OUT.name}/{name}.svg  {len(svg.encode()) / 1024:.1f} KB")

    block = readme_block(OUT.name, repos, data)
    if STATIC:
        (OUT / "index.html").write_text(
            "<!doctype html><meta charset=utf-8><title>Profile preview</title>"
            "<style>body{margin:0;background:#fff;font:14px system-ui}"
            "main{max-width:830px;margin:32px auto;padding:0 16px}"
            "img{vertical-align:top}p{margin:12px 0;text-align:center}"
            "main>a,main>img{display:block;margin:12px 0}</style><main>"
            + block.replace(f'src="{OUT.name}/', 'src="') + "</main>", encoding="utf-8")
        print(f"preview: {OUT / 'index.html'}  (final frame, no animation)")
    elif README.exists():
        text = README.read_text(encoding="utf-8")
        start, end = "<!-- cards:start -->", "<!-- cards:end -->"
        if start in text and end in text:
            head, rest = text.split(start, 1)
            _, tail = rest.split(end, 1)
            README.write_text(f"{head}{start}\n{block}\n{end}{tail}", encoding="utf-8")


if __name__ == "__main__":
    main()
