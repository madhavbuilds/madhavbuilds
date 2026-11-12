#!/usr/bin/env python3
"""Render the SVG cards for the madhavbuilds profile README.

    python render_cards.py            animated cards into cards/, README block updated
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
GH = "https://github.com/madhavbuilds"

# --------------------------------------------------------------------------------------
# Content. Edit freely.
# --------------------------------------------------------------------------------------
PROFILE = {
    "first": "Madhav",
    "last": "Panchal",
    "label": "Builder · open to freelance & remote",
    "line": "I build websites, apps, AI tools and Chrome extensions, and ship them. "
            "Based in Helsinki.",
    "site": "madhavpanchal.com",
    "href": "https://madhavpanchal.com/",
    # Drawn as a line engraving. Any portrait on a plain light background works best.
    "portrait": ROOT / "assets" / "Cyber Streetwear Portrait with Neon Accents (1).png",
    # Crop box as fractions of the image (left, top, right, bottom): head and shoulders.
    "crop": (0.165, 0.02, 0.84, 0.80),
}

# The three colour tiles. {repos} is filled in from data/github.json.
TILES = [
    {"slug": "build", "label": "Build", "big": "{repos} repos", "colour": "blue",
     "body": "Websites, apps, AI tools and Chrome extensions, all public on GitHub.",
     "href": f"{GH}?tab=repositories"},
    {"slug": "ship", "label": "Ship", "big": "3 packages", "colour": "red",
     "body": "create-own-ai on npm, Vett on PyPI and the EvalKit dataset on Hugging Face.",
     "href": f"{GH}/create-own-ai"},
    {"slug": "local", "label": "Local", "big": "0 API keys", "colour": "yellow",
     "body": "lantern and create-own-ai run on your own machine with Ollama. Nothing leaves it.",
     "href": f"{GH}/lantern"},
]

# "What I'm coding": repos in order, shown three to a row. None uses your pinned repos.
FEATURED = ["neurogenesis", "lantern", "create-own-ai", "EvalKit", "Vett", "Forge"]
# Short card descriptions (three lines max). Falls back to the GitHub description.
DESCRIPTIONS = {
    "neurogenesis": "A neural net that starts as one neuron, grows where it gets stuck and "
                    "prunes what it doesn't use. NumPy only.",
    "lantern": "A local answer engine. Web search through SearXNG, cited answers from your "
               "own Ollama model.",
    "create-own-ai": "One command builds a chat-with-your-files app that runs entirely on "
                     "your computer.",
    "EvalKit": "An open LLM benchmark in five Indian languages, from Hinglish input to "
               "cultural knowledge.",
    "Vett": "Codebase health checks for leaked secrets, risky APIs, complexity and TODOs.",
    "Forge": "Offline vibe coding: chat, generate a website, preview it and iterate with "
             "local AI.",
}
# Shown after the language on a repo card.
HIGHLIGHTS = {"create-own-ai": "on npm", "Vett": "on PyPI", "EvalKit": "on Hugging Face"}

FOOTER = [
    ("madhavpanchal.com", "https://madhavpanchal.com/"),
    ("linkedin", "https://www.linkedin.com/in/madhavpanchal/"),
    ("email", "mailto:madhavpanchal1716@gmail.com"),
]

# --------------------------------------------------------------------------------------
# Look
# --------------------------------------------------------------------------------------
C = {
    "bg": "#f4f3ef",          # paper
    "ink": "#121212",
    "body": "#3b3a36",
    "muted": "#7a7870",
    "line": "#e3e1da",
    "faint": "#cfccc3",       # the month still in progress
    "blue": "#2e54ff",
    "red": "#ec3a25",
    "yellow": "#fdc626",
    "engrave": "#2a2925",
    "gh_dark": "#f0f6fc",     # heading text on GitHub's dark theme
    "gh_light": "#1f2328",    # and on the light theme
}
HEAT = ["#e0e2ef", "#b9c4fb", "#8299ff", "#4c6aff", "#1d3bd1"]   # less -> more

GF = "https://raw.githubusercontent.com/google/fonts/main/ofl/"
INTER = GF + "inter/Inter%5Bopsz%2Cwght%5D.ttf"
# role -> font file (URL or local path) and variable-axis instance.
FONTS = {
    "r": {"src": INTER, "axes": {"wght": 400, "opsz": 14}},
    "s": {"src": INTER, "axes": {"wght": 600, "opsz": 14}},
    "g": {"src": GF + "instrumentserif/InstrumentSerif-Regular.ttf"},
    "i": {"src": GF + "instrumentserif/InstrumentSerif-Italic.ttf"},
    "k": {"src": GF + "jetbrainsmono/JetBrainsMono%5Bwght%5D.ttf", "axes": {"wght": 450}},
}
WEIGHT = {"r": 400, "s": 600, "g": 400, "i": 400, "k": 400}
ITALIC = {"i"}
SANS = 'system-ui,-apple-system,"Segoe UI",Roboto,Helvetica,Arial,sans-serif'
FALLBACK = {"g": 'Georgia,"Times New Roman",serif', "i": 'Georgia,"Times New Roman",serif',
            "k": 'ui-monospace,SFMono-Regular,Menlo,Consolas,monospace'}

W = 840          # full-width card viewBox width
PAD = 36
RADIUS = 14
TW = 276         # third-width card (tiles, repos)

# Start time (seconds) of each card, so the README plays top to bottom.
T_HERO, T_HEAT, T_MONTHLY, T_TILES, T_HEADING, T_REPOS = 0.0, 0.5, 0.9, 1.3, 1.6, 1.7

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
@keyframes nudge{from{opacity:0;transform:translate(-6px,6px)}}
@keyframes wipe{from{transform:scaleY(1)}}
.rise{animation:rise .7s cubic-bezier(.2,.7,.2,1) both}
.fade{animation:fade .6s ease-out both}
.grow{transform-box:fill-box;transform-origin:50% 100%;animation:grow .75s cubic-bezier(.3,.8,.2,1) both}
.pop{transform-box:fill-box;transform-origin:50% 50%;animation:pop .45s cubic-bezier(.3,1.5,.5,1) both}
.nudge{animation:nudge .5s cubic-bezier(.2,.7,.2,1) both}
.wipe{transform-box:fill-box;transform-origin:50% 100%;transform:scaleY(0);animation:wipe 1.6s cubic-bezier(.5,0,.3,1) both}
@media (prefers-reduced-motion:reduce){*{animation:none!important}}
"""


def esc(s):
    return html.escape(str(s), quote=True)


class Card:
    def __init__(self, w, h, title, t0=0.0, bg=None, stroke=None):
        self.w, self.h, self.title, self.t0 = w, h, title, t0
        self.bg = C["bg"] if bg is None else bg
        self.stroke = C["line"] if stroke is None else stroke
        self.parts, self.chars = [], {}

    def add(self, raw):
        self.parts.append(raw)

    def anim(self, kind, at):
        """class + delay attributes for an entrance, `at` seconds after this card starts."""
        if STATIC or not kind:
            return ""
        return f' class="{kind}" style="animation-delay:{self.t0 + at:.2f}s"'

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

    def label(self, x, y, s, fill, anchor=None, kind="fade", at=0.0):
        """Small spaced-out monospace caps."""
        self.text(x, y, s.upper(), "k", 10.5, fill, anchor=anchor, ls=1.3, kind=kind, at=at)

    def frame(self):
        if not self.bg:
            return ""
        st = f' stroke="{self.stroke}"' if self.stroke else ""
        return (f'<rect x=".5" y=".5" width="{self.w - 1}" height="{self.h - 1}" '
                f'rx="{RADIUS}" fill="{self.bg}"{st}/>')

    def svg(self):
        css = []
        for role, chars in sorted(self.chars.items()):
            css.append(f'@font-face{{font-family:"mp-{role}";font-weight:{WEIGHT[role]};'
                       f'src:url(data:font/woff;base64,{face(role).woff_b64(chars | {" "})}) '
                       f'format("woff")}}')
            style = "font-style:italic;" if role in ITALIC else ""
            css.append(f'.{role}{{font-family:"mp-{role}",{FALLBACK.get(role, SANS)};'
                       f'font-weight:{WEIGHT[role]};{style}}}')
        css = "\n".join(css) + ("" if STATIC else CSS_MOTION)
        return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {self.w} {self.h}" '
                f'width="{self.w}" height="{self.h}" role="img" aria-label="{esc(self.title)}">'
                f"<title>{esc(self.title)}</title><style>{css}</style>"
                f"{self.frame()}{''.join(self.parts)}</svg>")


def arrow(card, x, y, colour, at):
    """A small north-east arrow whose tip is at (x, y)."""
    card.add(f'<g{card.anim("nudge", at)}><path d="M{x - 9} {y + 9} L{x} {y} M{x - 6.5} {y} '
             f'H{x} V{y + 6.5}" fill="none" stroke="{colour}" stroke-width="1.6" '
             f'stroke-linecap="round" stroke-linejoin="round"/></g>')


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
    public = [n for n in user.get("repositories", {}).get("nodes", []) if n]
    repos = {n["name"]: n for n in public}
    repos.update({n["name"]: n for n in pinned})
    if FEATURED is None:
        featured = pinned
    else:
        featured = [repos[n] for n in FEATURED if n in repos]
        missing = [n for n in FEATURED if n not in repos]
        if missing:
            print(f"warning: not found in data/github.json: {', '.join(missing)}")
    return {"weeks": weeks, "days": days, "total": cal["totalContributions"],
            "featured": featured, "public": len(public), "login": user.get("login", "")}


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
    return {"current": current, "longest": longest, "days": len(counts),
            "active": sum(1 for c in counts if c), "busiest": busiest}


def fmt_date(d, year=False):
    return f"{d.day} {d.strftime('%b')}" + (f" {d.year}" if year else "")


def plural(n, word):
    return f"{n:,} {word}{'' if n == 1 else 's'}"


# --------------------------------------------------------------------------------------
# Portrait: a line engraving. Each scan line gets thicker where the photo is darker.
# --------------------------------------------------------------------------------------
def engraving(path, crop, x0, y0, bw, bh, gap=3.2, step=1.4):
    if not path or not Path(path).exists():
        return None
    try:
        from PIL import Image, ImageFilter, ImageOps
    except ImportError:
        print("warning: install pillow to draw the portrait")
        return None
    im = Image.open(path).convert("RGB")
    l, t, r, b = crop
    im = im.crop((int(l * im.width), int(t * im.height), int(r * im.width), int(b * im.height)))
    grey = ImageOps.autocontrast(im.convert("L"), cutoff=1)
    # Light outlines inside the dark clothing, so lapels and folds survive.
    edges = grey.filter(ImageFilter.GaussianBlur(1.5)).filter(ImageFilter.FIND_EDGES)
    cols, rows = int(bw / step), int(bh / gap)
    g = grey.resize((cols, rows), Image.LANCZOS).load()
    e = edges.resize((cols, rows), Image.LANCZOS).load()

    max_w, levels = gap * 0.8, 7
    paths = {}
    for j in range(rows):
        y = y0 + gap * (j + 0.5)
        fy = j / rows
        cur, start = 0, 0
        for i in range(cols + 1):
            if i < cols:
                L = g[i, j]
                dark = min(1.0, max(0.0, (222 - L) / 190)) ** 0.9
                dark = max(0.0, dark - min(0.45, e[i, j] / 110))   # carve edges back out
                # Oval vignette: full ink around the face, fading out towards the corners.
                dx, dy = (i / cols - 0.5) / 0.5, (fy - 0.4) / 0.62
                r = (dx * dx + dy * dy) ** 0.5
                fade = 1.0 if r < 0.72 else max(0.0, 1 - (r - 0.72) / 0.32) ** 1.3
                lvl = round(dark * fade * levels)
            else:
                lvl = 0
            if lvl != cur:
                if cur:
                    paths.setdefault(cur, []).append(
                        f"M{x0 + start * step:.1f} {y:.1f}h{(i - start) * step:.1f}")
                cur, start = lvl, i
    out = []
    for lvl in sorted(paths):
        out.append(f'<path d="{"".join(paths[lvl])}" fill="none" stroke="{C["engrave"]}" '
                   f'stroke-width="{max_w * lvl / levels:.2f}"/>')
    return "".join(out)


# --------------------------------------------------------------------------------------
# Cards
# --------------------------------------------------------------------------------------
def card_hero():
    p = PROFILE
    H = 384
    card = Card(W, H, f"{p['first']} {p['last']}. {p['line']}", T_HERO)
    px, py, pw, ph = 500, 22, 312, H - 44
    art = engraving(p.get("portrait"), p.get("crop", (0, 0, 1, 1)), px, py, pw, ph)
    text_w = (px - PAD - 20) if art else (W - 2 * PAD)
    x = PAD

    if art:
        card.add(f'<g{card.anim("fade", 0.1)}>{art}</g>')
        # A paper-coloured sheet that slides off the portrait, top to bottom.
        if not STATIC:
            card.add(f'<rect x="{px - 2}" y="{py - 2}" width="{pw + 4}" height="{ph + 4}" '
                     f'fill="{C["bg"]}"{card.anim("wipe", 0.15)}/>')

    card.add(f'<rect x="{x}" y="50" width="7" height="7" fill="{C["blue"]}"'
             f'{card.anim("pop", 0)}/>')
    card.label(x + 16, 57.5, p["label"], C["muted"], at=0.05)

    # Name, set letter by letter so it can rise in sequence.
    k = 0
    for row, (word, role) in enumerate(((p["first"], "g"), (p["last"], "i"))):
        size = 112
        while size > 40 and face(role).width(word, size, -1.5) > text_w:
            size -= 2
        xs = face(role).offsets(word, size, -1.5)
        card._use(role, word)
        y = 162 + row * 92
        for ch, dx in zip(word, xs):
            card.add(f'<g{card.anim("rise", 0.1 + k * 0.035)}><text x="{x + dx:.1f}" y="{y}" '
                     f'class="{role}" font-size="{size}" fill="{C["ink"]}">{esc(ch)}</text></g>')
            k += 1

    line = clean(p["line"])
    for n, ln in enumerate(wrap(line, "r", 16.5, text_w)[:2]):
        card.text(x, 302 + n * 25, ln, "r", 16.5, C["body"], kind="rise", at=0.6 + n * 0.05)

    card.text(x, 352, p["site"], "k", 12, C["ink"], kind="fade", at=0.8)
    sw = face("k").width(p["site"], 12)
    card.add(f'<line x1="{x}" x2="{x + sw:.1f}" y1="356.5" y2="356.5" stroke="{C["ink"]}" '
             f'stroke-width=".8"{card.anim("fade", 0.8)}/>')
    arrow(card, x + sw + 16, 343, C["blue"], 0.9)
    return card


def card_heat(data):
    weeks = data["weeks"]
    gap = 3
    step = (W - 2 * PAD + gap) / len(weeks)
    cell = step - gap
    top = 104
    bottom = top + 7 * step - gap
    H = int(bottom + 52)
    card = Card(W, H, f"Contribution graph: {plural(data['total'], 'contribution')} "
                      f"in the last year, refreshed daily", T_HEAT)
    card.runs(PAD, 70, [card.run("Every day, ", "g", 36, C["ink"], ls=-0.4),
                        card.run("in blue", "i", 36, C["blue"], ls=-0.4)], kind="rise", at=0)
    card.label(W - PAD, 66, "Refreshed daily", C["muted"], anchor="end", at=0.1)

    nonzero = sorted(c for _, c in data["days"] if c)

    def q(p):
        return nonzero[min(len(nonzero) - 1, int(p * len(nonzero)))] if nonzero else 0
    cuts = [q(.25), q(.5), q(.75)]

    def level(c):
        return 0 if not c else 1 + sum(c > t for t in cuts)

    for wi, week in enumerate(weeks):
        for d, c in week:
            row = (d.weekday() + 1) % 7      # Sunday first, like GitHub
            x, y = PAD + wi * step, top + row * step
            card.add(f'<rect x="{x:.1f}" y="{y:.1f}" width="{cell:.1f}" height="{cell:.1f}" '
                     f'rx="2" fill="{HEAT[level(c)]}"'
                     f'{card.anim("pop", 0.15 + wi * 0.014 + row * 0.02)}><title>'
                     f'{esc(plural(c, "contribution"))} on {fmt_date(d, True)}</title></rect>')

    ly = bottom + 26
    lx = W - PAD
    card.text(lx, ly, "more", "k", 10, C["muted"], anchor="end", kind="fade", at=0.9)
    lx -= face("k").width("more", 10) + 6 + len(HEAT) * (cell + 2) - 2
    for n, col in enumerate(HEAT):
        card.add(f'<rect x="{lx + n * (cell + 2):.1f}" y="{ly - cell + 1:.1f}" width="{cell:.1f}" '
                 f'height="{cell:.1f}" rx="2" fill="{col}"{card.anim("fade", 0.9)}/>')
    card.text(lx - 6, ly, "less", "k", 10, C["muted"], anchor="end", kind="fade", at=0.9)
    legend_x = lx - 6 - face("k").width("less", 10) - 16

    # Month labels under the grid, at the first full week of each month.
    prev, last_wi = None, -9
    for wi, week in enumerate(weeks):
        first = week[0][0]
        x = PAD + wi * step
        name = first.strftime("%b")
        if first.month != prev and wi - last_wi >= 3 and (prev is not None or first.day <= 7) \
                and x + face("k").width(name, 10) < legend_x:
            card.text(x, ly, name, "k", 10, C["muted"], kind="fade", at=0.3 + wi * 0.014)
            last_wi = wi
        prev = first.month
    return card


def card_monthly(data):
    days, H = data["days"], 348
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
    bd, bc = st["busiest"]

    card = Card(W, H, f"{total:,} contributions in the last 12 months. Current streak "
                      f"{st['current']} days, {st['active']} of {st['days']} days with a "
                      f"contribution, busiest day {fmt_date(bd, True)} with {bc}.", T_MONTHLY)
    card.label(PAD, 62, "The last 12 months on GitHub", C["muted"], at=0)
    card.runs(PAD, 116, [card.run(f"{total:,}", "g", 54, C["ink"], ls=-0.8),
                         card.run(" contributions", "i", 54, C["ink"], ls=-0.8)],
              kind="rise", at=0.05)

    # Bars: ink, the best month in blue, the month in progress pale.
    x0, x1, base, top = PAD, 524, 292, 168
    slot = (x1 - x0) / 12
    bw = slot * 0.74
    peak = max(sums.values()) or 1
    for i, key in enumerate(months):
        v = sums[key]
        h = max(2.0, (base - top) * v / peak) if v else 0
        cx = x0 + slot * i + slot / 2
        is_cur, is_peak = i == len(months) - 1, v == peak and v
        fill = C["faint"] if is_cur else (C["blue"] if is_peak else C["ink"])
        at = 0.25 + i * 0.05
        if h:
            card.add(f'<rect x="{cx - bw / 2:.1f}" y="{base - h:.1f}" width="{bw:.1f}" '
                     f'height="{h:.1f}" rx="1.5" fill="{fill}"{card.anim("grow", at)}>'
                     f'<title>{esc(plural(v, "contribution"))}</title></rect>')
        if is_peak:
            card.text(cx, base - h - 9, f"{v:,}", "k", 11, C["blue"], anchor="middle",
                      kind="fade", at=at + 0.5)
        mname = dt.date(key[0], key[1], 1).strftime("%b")
        card.text(cx, base + 22, mname, "k", 10, C["muted"], anchor="middle", kind="fade", at=at)

    # Stats column
    sx, sw = 572, W - PAD - 572
    longest = ("and the longest" if st["current"] >= st["longest"]
               else f"longest {st['longest']}")
    rows = [
        ([(f"{st['current']}", "g", C["ink"]),
          (" day" if st["current"] == 1 else " days", "i", C["ink"])],
         f"current streak, {longest}"),
        ([(f"{st['active']}", "g", C["ink"]), (f" / {st['days']}", "g", C["muted"])],
         "days with a contribution"),
        ([(f"{bc:,}", "g", C["ink"])], f"on the busiest day, {fmt_date(bd, True)}" if bd else ""),
    ]
    for j, (big, caption) in enumerate(rows):
        y = 92 + j * 84
        card.runs(sx, y, [card.run(s, role, 40, col, ls=-0.6) for s, role, col in big],
                  kind="rise", at=0.5 + j * 0.1)
        card.text(sx, y + 24, caption, "r", 12.5, C["muted"], kind="rise", at=0.55 + j * 0.1)
        if j < len(rows) - 1:
            card.add(f'<line x1="{sx}" x2="{sx + sw}" y1="{y + 47.5}" y2="{y + 47.5}" '
                     f'stroke="{C["line"]}"{card.anim("fade", 0.6 + j * 0.1)}/>')
    return card


def card_tile(tile, i, data):
    TH = 184
    colour = C[tile["colour"]]
    fg = C["ink"] if tile["colour"] == "yellow" else "#ffffff"
    soft = "#3b3209" if tile["colour"] == "yellow" else "#ffffffd9"
    big = tile["big"].format(repos=data["public"])
    card = Card(TW, TH, f"{tile['label']}: {big}. {tile['body']}", T_TILES + 0.1 * i,
                bg=colour, stroke="")
    card.label(22, 38, tile["label"], soft, at=0)
    arrow(card, TW - 22, 29, fg, 0.3)
    size = 44
    while size > 26 and face("i").width(big, size, -0.6) > TW - 44:
        size -= 1
    card.text(22, 104, big, "i", size, fg, ls=-0.6, kind="rise", at=0.05)
    for n, ln in enumerate(wrap(clean(tile["body"]), "r", 12.5, TW - 44)[:3]):
        card.text(22, 134 + n * 17.5, ln, "r", 12.5, fg, kind="rise", at=0.12 + n * 0.04)
    return card


def card_heading(start, italic, fill, t0):
    card = Card(W, 64, start + italic, t0, bg="", stroke="")
    card.runs(2, 46, [card.run(start, "g", 34, fill, ls=-0.4),
                      card.run(italic, "i", 34, fill, ls=-0.4)], kind="rise", at=0)
    return card


DOTS = ["blue", "red", "yellow"]


def card_repo(repo, i):
    TH = 172
    name = repo["name"]
    desc = clean(DESCRIPTIONS.get(name) or repo.get("description") or "")
    card = Card(TW, TH, f"{name}. {desc}", T_REPOS + 0.08 * i)
    card.add(f'<rect x="20" y="31" width="7" height="7" fill="{C[DOTS[i % 3]]}"'
             f'{card.anim("pop", 0)}/>')
    card.text(36, 39.5, name, "s", min(16, fit(name, "s", 16, TW - 56)), C["ink"],
              kind="rise", at=0)
    for n, ln in enumerate(wrap(desc, "r", 12.5, TW - 40)[:4]):
        card.text(20, 72 + n * 18, ln, "r", 12.5, C["body"], kind="rise", at=0.06 + n * 0.04)

    lang = (repo.get("primaryLanguage") or {}).get("name")
    stars = repo.get("stargazerCount", 0)
    bits = [b for b in (lang, f"★ {stars}" if stars and face("k").has("★") else
                        (plural(stars, "star") if stars else None), HIGHLIGHTS.get(name)) if b]
    card.text(20, TH - 22, "  ·  ".join(bits), "k", 10.5, C["muted"], kind="fade", at=0.25)
    return card


# --------------------------------------------------------------------------------------
# Output
# --------------------------------------------------------------------------------------
def slug(s):
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")


def readme_block(d, repos, data):
    p = PROFILE
    a = esc
    lines = [
        f'<a href="{p["href"]}"><img src="{d}/hero.svg" width="100%" '
        f'alt="{a(p["first"])} {a(p["last"])}. {a(p["line"])}"></a>',
        "",
        f'<img src="{d}/heatmap.svg" width="100%" alt="Contribution graph for the last year">',
        "",
        f'<img src="{d}/contributions.svg" width="100%" '
        f'alt="Contributions per month, with streak, active days and busiest day">',
        "",
        '<p align="center">',
    ]
    for t in TILES:
        big = t["big"].format(repos=data["public"])
        lines.append(f'<a href="{t["href"]}"><img src="{d}/tile-{t["slug"]}.svg" width="32.8%" '
                     f'alt="{a(t["label"])}: {a(big)}. {a(t["body"])}"></a>')
    lines += [
        "</p>", "",
        "<picture>",
        f'<source media="(prefers-color-scheme: dark)" srcset="{d}/heading-coding-dark.svg">',
        f'<img src="{d}/heading-coding-light.svg" width="100%" alt="What I\'m coding">',
        "</picture>", "",
    ]
    for r0 in range(0, len(repos), 3):
        lines.append('<p align="center">')
        for r in repos[r0:r0 + 3]:
            lines.append(f'<a href="{r["url"]}"><img src="{d}/repo-{slug(r["name"])}.svg" '
                         f'width="32.8%" alt="{a(r["name"])}"></a>')
        lines += ["</p>", ""]
    lines.append('<p align="center">' + " / ".join(
        f'<a href="{href}">{a(text)}</a>' for text, href in FOOTER) + "</p>")
    return "\n".join(lines)


def main():
    if not DATA.exists():
        sys.exit(f"missing {DATA.relative_to(ROOT)}; run the Profile cards workflow first")
    data = load_data()
    OUT.mkdir(exist_ok=True)
    for old in OUT.glob("*.svg"):
        old.unlink()

    repos = data["featured"]
    cards = {"hero": card_hero(), "heatmap": card_heat(data),
             "contributions": card_monthly(data)}
    for i, t in enumerate(TILES):
        cards[f"tile-{t['slug']}"] = card_tile(t, i, data)
    for mode in ("dark", "light"):
        cards[f"heading-coding-{mode}"] = card_heading("What I'm ", "coding",
                                                      C[f"gh_{mode}"], T_HEADING)
    for i, r in enumerate(repos):
        cards[f"repo-{slug(r['name'])}"] = card_repo(r, i)

    for name, card in cards.items():
        svg = card.svg()
        (OUT / f"{name}.svg").write_text(svg, encoding="utf-8")
        print(f"{OUT.name}/{name}.svg  {len(svg.encode()) / 1024:.1f} KB")

    block = readme_block(OUT.name, repos, data)
    if STATIC:
        page = block.replace(f'src="{OUT.name}/', 'src="').replace(f'srcset="{OUT.name}/',
                                                                   'srcset="')
        (OUT / "index.html").write_text(
            "<!doctype html><meta charset=utf-8><title>Profile preview</title>"
            "<style>body{margin:0;background:#0d1117;color:#f0f6fc;font:14px system-ui}"
            "main{max-width:830px;margin:32px auto;padding:0 16px}a{color:#4493f8}"
            "img{vertical-align:top}p{margin:16px 0}main>a,main>img,picture img"
            "{display:block;margin:16px 0}</style><main>" + page + "</main>",
            encoding="utf-8")
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
