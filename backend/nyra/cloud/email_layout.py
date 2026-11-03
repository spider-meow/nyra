"""The one layout of every e-mail Nyra sends: `compose` turns a list of blocks into `(html, text)`. See « Ajouter un e-mail ».

    html, text = compose(title, preheader, [Paragraph(...), Stats([(value, label, tone)]),
                         Brand(name, [(value, label, tone)], notes=[...], button=Button(label, url), quiet="Rien à traiter")],
                         banner=None, footer=Footer(text, link_label, link_url))

Blocks are plain dataclasses; tones are `red`, `orange`, `yellow`, `neutral`. Every text from outside is escaped; a
`Button` or footer link whose address is not `http(s)://host...` is left out. The HTML is made for mail clients: tables with
`role="presentation"`, styles written in the tags (the `<style>` block only holds dark mode and small screens), no image, no
font, no sheet from outside. One mail should stay under `MAX_HTML_BYTES` (Gmail clips near 102 000): the layout cannot cut, so
the caller bounds what it sends and a warning is logged when it is not enough. The palette below is the application's (`frontend/src/index.css`) and Supabase's templates'.
"""

from __future__ import annotations

import html as html_lib
import logging
from dataclasses import dataclass
from typing import Optional, Sequence
from urllib.parse import urlsplit

log = logging.getLogger("nyra.cloud.email_layout")
MAX_HTML_BYTES = 90_000

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
class Button:
    label: str
    url: Optional[str]


@dataclass(frozen=True)
class Brand:
    """A card for one brand: its figures (`(value, label, tone)`, each on a line), its notes (yellow warnings), its button.
    With no figure, only `quiet` is said (« rien à traiter »): one short line."""
    title: str
    figures: Sequence[tuple] = ()
    notes: Sequence[str] = ()
    button: Optional[Button] = None
    quiet: str = ""


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


def _notice_html(text: str, tone: str, margin: str = "0 0 16px") -> str:
    fg, bg = TONES[tone][:2]
    return (f'<table {_TABLE} style="margin:{margin}"><tr><td class="t-{tone}" style="background:{bg};color:{fg};border-left:4px solid {fg};'
            f'border-radius:6px;padding:12px 14px;font-size:14px;line-height:1.5">{_e(text)}</td></tr></table>')


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


def _brand(b: Brand) -> tuple[str, str]:
    name = f'<p class="ink" style="margin:0;font-family:{SERIF};font-size:18px;line-height:1.3;font-weight:800">{_e(b.title)}</p>'
    box = (f'<table {_TABLE} class="sect" style="margin:0 0 12px;background:{LIGHT["sect"]};border:1px solid {LIGHT["line"]};border-radius:12px"><tr>'
           f'<td class="sect-pad" style="padding:14px 18px 16px">')
    chips = "".join(f'<tr><td style="padding:6px 0 0"><span class="t-{tone}" style="display:inline-block;min-width:26px;padding:2px 9px;border-radius:999px;'
                    f'background:{TONES[tone][1]};color:{TONES[tone][0]};font-weight:700;text-align:center">{_e(value)}</span>'
                    f'<span style="padding-left:10px;font-size:15px">{_e(label)}</span></td></tr>' for value, label, tone in b.figures)
    quiet = f'<p class="muted" style="margin:2px 0 0;font-size:14px;color:{LIGHT["muted"]}">{_e(b.quiet)}</p>' if not b.figures else ""
    notes = "".join(_notice_html(n, "yellow", "12px 0 0") for n in b.notes)
    button, button_text = _button(b.button)
    lines = [f"- {_line(value)} {_line(label)}" for value, label, _ in b.figures] or [_line(b.quiet)]
    text = [_line(b.title)] + lines + [_line(n) for n in b.notes] + ([button_text] if button_text else [])
    return f'{box}{name}{quiet}<table {_TABLE}>{chips}</table>{notes}{button}</td></tr></table>', "\n".join(text)


_RENDER = {Paragraph: _paragraph, Stats: _stats, Brand: _brand}


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


def compose(title: str, preheader: str, blocks: Sequence, *, banner: Optional[str] = None, footer: Optional[Footer] = None) -> tuple[str, str]:
    """`(html, text)` for these blocks. `banner` is a line above the title (the « e-mail de test » one). The layout cuts nothing: past
    `MAX_HTML_BYTES` a warning is logged (the caller bounds the number of blocks)."""
    html, text = _page(title, preheader, blocks, banner, footer or _FOOTER)
    if len(html.encode("utf-8")) >= MAX_HTML_BYTES:
        log.warning("an e-mail is %d bytes: Gmail may clip it", len(html.encode("utf-8")))
    return html, text
