"""The one layout of every e-mail Nyra sends: `compose` turns a list of blocks into `(html, text)`. See « Ajouter un e-mail ».

    html, text = compose(title, preheader, [Paragraph(...), Stats([(value, label, tone)]), Notice(text),
                         Section(title, intro, [Group(brand, [Item(name, detail, right)], Button(label, url), more=3)], tone)],
                         banner=None, footer=Footer(text, link_label, link_url))

Blocks are plain dataclasses; tones are `red`, `orange`, `yellow`, `neutral`. Every text from outside is escaped; a
`Button` or footer link whose address is not `http(s)://host...` is left out. The HTML is made for mail clients: tables with
`role="presentation"`, styles written in the tags (the `<style>` block only holds dark mode and small screens), no image, no
font, no sheet from outside. One mail stays under `MAX_HTML_BYTES` (Gmail clips near 102 000): when it would not, the lists are
cut (20 lines per brand, then 10, 5, 3, 1, 0, then fewer brands) and say « et N autres » or « et N autres marques ». The palette below is the application's (`frontend/src/index.css`) and Supabase's templates'.
"""

from __future__ import annotations

import html as html_lib
import logging
from dataclasses import dataclass, replace
from typing import Optional, Sequence
from urllib.parse import urlsplit

log = logging.getLogger("nyra.cloud.email_layout")
MAX_HTML_BYTES = 90_000
# (items kept per list, groups kept per section): tried in turn until the HTML fits. ponytail: cut the lists, never the sections.
_LADDER = ((20, 50), (10, 50), (5, 50), (3, 20), (1, 10), (0, 5), (0, 2))

LIGHT = {"page": "#fdfcf8", "card": "#ffffff", "sect": "#fdfcf8", "line": "#ebe7db", "ink": "#1a3300",
         "muted": "#5f6e50", "faint": "#667556", "btn": "#1a3300", "btn_ink": "#ffffff"}  # faint: #8a9680 reads at 3.1:1, under 4.5
DARK = {"page": "#12170d", "card": "#1c2415", "sect": "#232d1b", "line": "#33402a", "ink": "#f1f0e6",
        "muted": "#a9b59b", "faint": "#8d9a80", "btn": "#f6e27f", "btn_ink": "#1a3300"}
# tone: (text, background, dark text, dark background)
TONES = {"red": ("#a8261b", "#fbe7e3", "#f4a399", "#3b1a16"), "orange": ("#a34e0c", "#fcebdb", "#f2b883", "#3b2410"),
         "yellow": ("#7d6200", "#f8f0d2", "#f6e27f", "#332e0e"),  # (no green: no e-mail needs one yet)
         "neutral": ("#3d5226", "#f7f4ec", "#c9d3bb", "#262f1d")}
SANS = "Inter,'Segoe UI',Arial,sans-serif"
SERIF = "'Bricolage Grotesque',Georgia,serif"
_TABLE = 'role="presentation" width="100%" cellpadding="0" cellspacing="0"'


@dataclass(frozen=True)
class Paragraph:
    text: str


@dataclass(frozen=True)
class Stats:
    """2 to 4 key figures in one row: `(value, label, tone)`."""
    figures: Sequence[tuple]

    def __post_init__(self) -> None:
        if not 2 <= len(self.figures) <= 4:
            raise ValueError("Stats holds 2 to 4 figures.")


@dataclass(frozen=True)
class Notice:
    """A warning band (yellow)."""
    text: str


@dataclass(frozen=True)
class Button:
    label: str
    url: Optional[str]


@dataclass(frozen=True)
class Item:
    """A line: `primary` in bold, `secondary` under it, `right` at the end (a second line after a newline)."""
    primary: str
    secondary: Optional[str] = None
    right: str = ""


@dataclass(frozen=True)
class Group:
    """A subtitle (a brand), its lines (`more` = how many others there are), and its button."""
    title: str
    items: Sequence[Item]
    button: Optional[Button] = None
    more: int = 0


@dataclass(frozen=True)
class Section:
    """A card with a tone dot: its groups (`more` = how many other groups were cut)."""
    title: str
    intro: str
    items: Sequence[Group]
    tone: str
    more: int = 0


@dataclass(frozen=True)
class Footer:
    text: str
    link_label: Optional[str] = None
    link_url: Optional[str] = None


_FOOTER = Footer("Nyra surveille l'usage de vos visuels et de leurs droits.")


def _e(value: object) -> str:
    return html_lib.escape(str(value), quote=True)


def _line(value: object) -> str:
    return " ".join(str(value).split())


def _url(url: Optional[str]) -> Optional[str]:
    """The address when it is an http(s) one with a host and no space or control character, else None."""
    if not url or any(c.isspace() or ord(c) < 32 for c in url):
        return None
    parts = urlsplit(url)
    return url if parts.scheme in ("http", "https") and parts.netloc else None


# --- the style block: dark mode and small screens only ----------------------------------------------------------------------

def _css() -> str:
    d = DARK
    rules = [f".bg{{background:{d['page']} !important}}", f".card{{background:{d['card']} !important;border-color:{d['line']} !important}}",
             f".sect{{background:{d['sect']} !important;border-color:{d['line']} !important}}", f".ink{{color:{d['ink']} !important}}",
             f".muted{{color:{d['muted']} !important}}", f".faint{{color:{d['faint']} !important}}",
             f".btn{{background:{d['btn']} !important;color:{d['btn_ink']} !important}}", f".rule{{border-color:{d['line']} !important}}"]
    for name, (_, _, fg, bg) in TONES.items():
        rules += [f".t-{name}{{background:{bg} !important;color:{fg} !important;border-color:{fg} !important}}", f".f-{name}{{color:{fg} !important}}"]
    small = ".pad{padding:24px 18px !important}.wrap{padding:16px 8px !important}.fig{font-size:22px !important}.sect-pad{padding:16px 14px !important}"
    return f"@media (prefers-color-scheme: dark){{{''.join(rules)}}}@media (max-width:480px){{{small}}}"


_CSS = _css()


# --- blocks: each gives (html, text) ------------------------------------------------------------------------------------------

def _paragraph(b: Paragraph) -> tuple[str, str]:
    return f'<p style="margin:0 0 16px">{_e(b.text)}</p>', _line(b.text)


def _notice_html(text: str, tone: str) -> str:
    fg, bg = TONES[tone][:2]
    return (f'<table {_TABLE} style="margin:0 0 16px"><tr><td class="t-{tone}" style="background:{bg};color:{fg};border-left:4px solid {fg};'
            f'border-radius:6px;padding:12px 14px;font-size:14px;line-height:1.5">{_e(text)}</td></tr></table>')


def _notice(b: Notice) -> tuple[str, str]:
    return _notice_html(b.text, "yellow"), _line(b.text)


def _stats(b: Stats) -> tuple[str, str]:
    cells, last = [], len(b.figures) - 1
    for i, (value, label, tone) in enumerate(b.figures):
        fg, bg = TONES[tone][:2]
        pad = f"0 {0 if i == last else 3}px 0 {0 if i == 0 else 3}px"
        cells.append(f'<td width="{100 // len(b.figures)}%" valign="top" style="padding:{pad}"><div class="t-{tone}" style="background:{bg};color:{fg};'
                     f'border-radius:10px;padding:14px 6px;text-align:center"><div class="fig" style="font-family:{SERIF};font-size:32px;font-weight:800;'
                     f'line-height:1.1">{_e(value)}</div><div style="margin-top:4px;font-size:13px;line-height:1.35">{_e(label)}</div></div></td>')
    html = f'<table {_TABLE} class="stats" style="margin:0 0 20px"><tr>{"".join(cells)}</tr></table>'
    return html, "\n".join(f"{_line(label)} : {_line(value)}" for value, label, _ in b.figures)


def _button(b: Optional[Button]) -> tuple[str, str]:
    url = _url(b.url) if b else None
    if not b or not url:
        return "", ""
    a = (f'<a href="{_e(url)}" class="btn" style="display:inline-block;padding:12px 24px;border-radius:8px;font-size:15px;font-weight:600;color:{LIGHT["btn_ink"]};'
         f'text-decoration:none">{_e(b.label)}</a>')
    html = (f'<table role="presentation" cellpadding="0" cellspacing="0" style="margin:14px 0 4px"><tr><td class="btn" bgcolor="{LIGHT["btn"]}" '
            f'style="background:{LIGHT["btn"]};border-radius:8px">{a}</td></tr></table>')
    return html, f"{_line(b.label)} : {url}"


def _row(item: Item) -> tuple[str, str]:
    first, _, second = item.right.partition("\n")
    cell = f'class="rule" valign="top" style="padding:10px 0;border-top:1px solid {LIGHT["line"]}'
    sec = f'<br><span class="muted" style="font-size:13px;color:{LIGHT["muted"]}">{_e(item.secondary)}</span>' if item.secondary else ""
    left = f'<td {cell};word-break:break-word"><b>{_e(item.primary)}</b>{sec}</td>'
    sub = f'<br><span class="muted" style="color:{LIGHT["muted"]}">{_e(second)}</span>' if second else ""
    right = f'<td {cell};padding:12px 0 10px 12px;font-size:13px;white-space:nowrap" align="right"><b>{_e(first)}</b>{sub}</td>'
    text = _line(item.primary) + (f" : {_line(first)}" + (f" ({_line(second)})" if second else "") if item.right else "")
    return f"<tr>{left}{right}</tr>", f"- {text}" + (f", {_line(item.secondary)}" if item.secondary else "")


def _more_row(count: int, noun: str = "") -> tuple[str, str]:
    """« et 3 autres » (lines) or, with `noun`, « et 3 autres marques » (groups)."""
    text = f"et {count} {'autre' if count == 1 else 'autres'}" + (f" {noun}{'' if count == 1 else 's'}" if noun else "")
    return (f'<tr><td colspan="2" class="rule muted" style="padding:10px 0;border-top:1px solid {LIGHT["line"]};font-size:13px;color:{LIGHT["muted"]}">{text}</td></tr>',
            f"- {text}")


def _rows(items: Sequence[Item], more: int, noun: str = "") -> tuple[str, str]:
    pairs = [_row(i) for i in items] + ([_more_row(more, noun)] if more else [])
    if not pairs:
        return "", ""
    return f'<table {_TABLE}>{"".join(h for h, _ in pairs)}</table>', "\n".join(t for _, t in pairs)


def _group(g: Group) -> tuple[str, str]:
    rows, rows_text = _rows(g.items, g.more)
    button, button_text = _button(g.button)
    head = (f'<p class="ink" style="margin:18px 0 2px;font-size:12px;font-weight:700;letter-spacing:.06em;text-transform:uppercase">{_e(g.title)}</p>')
    return head + rows + button, "\n".join(part for part in (_line(g.title), rows_text, button_text) if part)


def _section(b: Section) -> tuple[str, str]:
    fg = TONES[b.tone][0]
    parts = [_group(g) for g in b.items] + [_rows([], b.more, "marque")]
    head = (f'<h2 class="ink" style="margin:0 0 4px;font-family:{SERIF};font-size:19px;line-height:1.25;font-weight:800"><span class="f-{b.tone}" '
            f'style="color:{fg}">&#9679;</span> {_e(b.title)}</h2><p class="muted" style="margin:0;font-size:14px;color:{LIGHT["muted"]}">{_e(b.intro)}</p>')
    html = (f'<table {_TABLE} class="sect" style="margin:0 0 20px;background:{LIGHT["sect"]};border:1px solid {LIGHT["line"]};border-radius:12px"><tr>'
            f'<td class="sect-pad" style="padding:18px 20px 20px">{head}{"".join(h for h, _ in parts)}</td></tr></table>')
    text = "\n".join([_line(b.title), "-" * min(len(_line(b.title)), 40), _line(b.intro)] + [t for _, t in parts if t])
    return html, text


_RENDER = {Paragraph: _paragraph, Notice: _notice, Stats: _stats, Section: _section}


# --- the page -----------------------------------------------------------------------------------------------------------------

def _footer(f: Footer) -> tuple[str, str]:
    url = _url(f.link_url) if f.link_label else None
    link = (f'<br><a href="{_e(url)}" class="muted" style="color:{LIGHT["muted"]};text-decoration:underline">{_e(f.link_label)}</a>') if url else ""
    html = (f'<tr><td align="center" class="faint" style="padding:20px 8px 0;font-size:12px;line-height:1.5;color:{LIGHT["faint"]}">'
            f'<b>Nyra by Axel Project</b><br>{_e(f.text)}{link}</td></tr>')
    return html, "\n".join(["Nyra by Axel Project", _line(f.text)] + ([f"{_line(f.link_label)} : {url}"] if url else []))


def _page(title: str, preheader: str, blocks: Sequence, banner: Optional[str], footer: Footer) -> tuple[str, str]:
    pairs = ([(_notice_html(banner, "neutral"), _line(banner))] if banner else []) + [(f'<h1 class="ink" style="margin:0 0 14px;font-family:{SERIF};font-size:26px;'
             f'line-height:1.2;font-weight:800">{_e(title)}</h1>', _line(title))] + [_RENDER[type(b)](b) for b in blocks]
    foot_html, foot_text = _footer(footer)
    header = (f'<tr><td align="center" style="padding:0 0 20px"><div class="ink" style="font-family:{SERIF};font-size:26px;font-weight:800;line-height:1;'
              f'color:{LIGHT["ink"]}">Nyra</div><div class="muted" style="margin-top:6px;font-size:11px;letter-spacing:.18em;text-transform:uppercase;'
              f'color:{LIGHT["muted"]}">by Axel Project</div></td></tr>')
    html = (f'<!doctype html><html lang="fr"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">'
            f'<meta name="color-scheme" content="light dark"><meta name="supported-color-schemes" content="light dark"><title>{_e(title)}</title>'
            f'<style>{_CSS}</style></head><body class="bg" style="margin:0;padding:0;background:{LIGHT["page"]};font-family:{SANS};font-size:15px;line-height:1.6;'
            f'color:{LIGHT["ink"]}"><div style="display:none;max-height:0;overflow:hidden;opacity:0;color:{LIGHT["page"]}">{_e(preheader)}</div>'
            f'<table {_TABLE} class="bg" style="background:{LIGHT["page"]}"><tr><td align="center" class="wrap" style="padding:32px 12px">'
            f'<!--[if mso]><table role="presentation" width="600" align="center"><tr><td><![endif]-->'
            f'<table {_TABLE} style="max-width:600px">{header}<tr><td class="card pad ink" style="background:{LIGHT["card"]};border:1px solid {LIGHT["line"]};'
            f'border-radius:16px;padding:32px;color:{LIGHT["ink"]}">{"".join(h for h, _ in pairs)}</td></tr>{foot_html}</table>'
            f'<!--[if mso]></td></tr></table><![endif]--></td></tr></table></body></html>')
    return html, "\n\n".join(t for t in [t for _, t in pairs] + [foot_text] if t)


def _shrink(blocks: Sequence, cap: int, groups: int) -> list:
    """The same blocks with at most `cap` lines per group and `groups` groups per section; what is cut becomes `more`."""
    out = []
    for b in blocks:
        if isinstance(b, Section):
            kept = [replace(g, items=g.items[:cap], more=g.more + max(0, len(g.items) - cap)) for g in b.items[:groups]]
            b = replace(b, items=kept, more=b.more + max(0, len(b.items) - groups))
        out.append(b)
    return out


def compose(title: str, preheader: str, blocks: Sequence, *, banner: Optional[str] = None, footer: Optional[Footer] = None) -> tuple[str, str]:
    """`(html, text)` for these blocks. `banner` is a line above the title (the « e-mail de test » one). While the HTML is not under
    `MAX_HTML_BYTES`, the lists are cut by `_LADDER` (20 lines per group, then 10, 5, 3, 1, 0; fewer groups per section). Only lists
    are cut: if the title, the notices or the sections themselves are too many, the last step is kept and a warning is logged."""
    footer = footer or _FOOTER
    html, text = _page(title, preheader, blocks, banner, footer)
    for cap, groups in _LADDER:
        if len(html.encode("utf-8")) < MAX_HTML_BYTES:
            return html, text
        html, text = _page(title, preheader, _shrink(blocks, cap, groups), banner, footer)
    if len(html.encode("utf-8")) >= MAX_HTML_BYTES:
        log.warning("an e-mail is %d bytes after every list was cut: Gmail may clip it", len(html.encode("utf-8")))
    return html, text
