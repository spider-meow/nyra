"""The common e-mail layout (cloud/email_layout.py): one HTML and one text from the same blocks, escaped, safe and light.

Pure: no network, no database."""

from __future__ import annotations

import re
from html.parser import HTMLParser

import pytest

from nyra.cloud import email_layout as L

TRAPS = ['<img src=x onerror=alert(1)>.jpg', '"><script>alert(2)</script>', "<b>gras</b> & 'quote'"]
VOID = {"meta", "br", "hr", "img", "link", "input"}


def sample(url="https://nyra.test/o/x/b/y/review"):
    items = [L.Item("affiche.jpg", "3 images en ligne", "expiré le 13/10/2026"), L.Item("banniere.jpg", None, "expire le 14/11/2026\ndans 20 jours")]
    return [L.Paragraph("Bonjour, voici le point."),
            L.Stats([("2", "À traiter", "red"), ("5", "Expirent sous 30 jours", "orange"), ("7", "Sous 31 à 90 jours", "yellow")]),
            L.Notice("La lecture de la marque « Alpha » semble incomplète.", "yellow"),
            L.Section("Nouveaux visuels", "Ils sont en ligne.", [L.Group("Alpha", items, L.Button("Ouvrir À traiter", url), more=4)], "red")]


def mail(blocks=None, **kw):
    return L.compose("Nyra : le point du mois", "2 visuels à traiter", sample() if blocks is None else blocks, **kw)


# --- escaping and links --------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("trap", TRAPS)
def test_every_outside_field_is_escaped_in_the_html_and_plain_in_the_text(trap):
    blocks = [L.Paragraph(trap), L.Stats([(trap, trap, "red"), ("1", "x", "green")]), L.Notice(trap, "yellow"),
              L.Section(trap, trap, [L.Item(trap, trap, trap), L.Group(trap, [L.Item(trap, trap, trap)], L.Button(trap, "https://a.test/"))], "orange")]
    html, text = L.compose(trap, trap, blocks, banner=trap, footer=L.Footer(trap, trap, "https://a.test/s"))
    low = html.lower()
    assert "<img" not in low and "<script" not in low and "<b>gras" not in low and "onerror=alert(1)>" not in low
    assert "&lt;" in html and trap in text


def test_only_http_and_https_addresses_make_a_button():
    for bad in ("javascript:alert(1)", "JaVaScRiPt:alert(1)", "data:text/html,x", "//evil.test/x", "evil.test/x", "", " https://a.test", "https://",
                "ftp://a.test", "https://a.test/x y", "https://a.test/\n"):
        html, text = mail([L.Paragraph("p"), L.Button("Cliquez", bad)])
        assert "href" not in html and "Cliquez" not in html and "Cliquez" not in text, repr(bad)
    html, text = mail([L.Button("Cliquez", 'http://a.test/p?x=1&y="2"')])
    assert 'href="http://a.test/p?x=1&amp;y=&quot;2&quot;"' in html and 'http://a.test/p?x=1&y="2"' in text


def test_a_footer_link_with_a_bad_address_is_left_out():
    html, text = mail(footer=L.Footer("Pied", "Réglages", "javascript:alert(1)"))
    assert "javascript" not in html + text and "Pied" in html and "Réglages" not in html


# --- structure ---------------------------------------------------------------------------------------------------------

class Tags(HTMLParser):
    def __init__(self):
        super().__init__()
        self.stack, self.errors, self.seen = [], [], []

    def handle_starttag(self, tag, attrs):
        self.seen.append((tag, dict(attrs)))
        if tag not in VOID:
            self.stack.append(tag)

    def handle_endtag(self, tag):
        if not self.stack or self.stack.pop() != tag:
            self.errors.append(tag)


def parse(html):
    parser = Tags()
    parser.feed(html)
    parser.close()
    return parser


def test_the_html_is_a_well_formed_email_page():
    html, _ = mail(banner="Bandeau", footer=L.Footer("Pied", "Réglages", "https://a.test/s"))
    p = parse(html)
    assert html.startswith("<!doctype html>") and '<html lang="fr"' in html and "viewport" in html and "color-scheme" in html
    assert p.errors == [] and p.stack == []
    tags = [t for t, _ in p.seen]
    assert tags.count("h1") == 1 and tags.count("table") >= 5
    assert all(a.get("role") == "presentation" for t, a in p.seen if t == "table")
    assert "2 visuels à traiter" in html and "display:none" in html  # the preheader, hidden
    assert 'max-width:600px' in html and "Nyra" in html and "by Axel Project" in html


def test_nothing_is_loaded_from_outside():
    html, _ = mail(footer=L.Footer("Pied", "Réglages", "https://a.test/s"))
    tags = {t for t, _ in parse(html).seen}
    assert not tags & {"img", "link", "script", "iframe", "video", "audio", "object", "embed", "form"}
    assert "url(" not in html and "@import" not in html and "src=" not in html


def test_a_row_of_four_figures_is_one_table_row():
    html, _ = mail([L.Stats([(str(i), f"c{i}", "red") for i in range(4)])])
    row = html[html.index('class="stats"'):]
    row = row[:row.index("</table>")]
    assert row.count("<tr") == 1 and row.count("<td") == 4
    for count in (0, 1, 5):
        with pytest.raises(ValueError):
            L.Stats([("1", "x", "red")] * count)


def test_the_base_styles_are_inline_and_the_style_block_only_holds_dark_mode_and_small_screens():
    html, _ = mail()
    css = re.search(r"<style>(.*?)</style>", html, re.S).group(1)
    assert "prefers-color-scheme: dark" in css and "max-width" in css
    for cls in ("bg", "card", "ink", "muted", "btn"):
        assert f".{cls}" in css
    body = html[html.index("<body"):]
    assert "font-family:" in body and "background:#fdfcf8" in body and "line-height:1.6" in body and "font-size:15px" in body


def test_the_button_is_a_coloured_table_cell_with_a_link_inside():
    html, _ = mail()
    assert re.search(r'<td[^>]*class="btn"[^>]*background:#1a3300[^>]*><a href="https://nyra.test/o/x/b/y/review"[^>]*>Ouvrir À traiter</a></td>', html)


# --- text --------------------------------------------------------------------------------------------------------------------

def test_the_text_says_what_the_html_says_without_any_tag():
    html, text = mail(banner="Bandeau test", footer=L.Footer("Pied de page", "Réglages", "https://a.test/s"))
    assert "<" not in text and ">" not in text.replace("> ", "")  # no markup
    labels = ["Nyra : le point du mois", "Bonjour, voici le point.", "À traiter", "Expirent sous 30 jours", "Sous 31 à 90 jours", "semble incomplète",
              "Nouveaux visuels", "Ils sont en ligne.", "Alpha", "affiche.jpg", "3 images en ligne", "expiré le 13/10/2026", "banniere.jpg",
              "expire le 14/11/2026", "dans 20 jours", "et 4 autres", "Ouvrir À traiter", "https://nyra.test/o/x/b/y/review", "Bandeau test",
              "Pied de page", "Réglages", "https://a.test/s", "Nyra by Axel Project"]
    for label in labels:
        assert label in text, label
        assert label.replace("&", "&amp;") in html or label in html, label
    assert text.startswith("Bandeau test")


# --- dark mode ---------------------------------------------------------------------------------------------------------------

def test_dark_mode_rules_cover_page_card_text_and_button():
    html, _ = mail()
    dark = html[html.index("prefers-color-scheme: dark"):]
    for rule, color in ((".bg{", L.DARK["page"]), (".card{", L.DARK["card"]), (".ink{", L.DARK["ink"]), (".btn{", L.DARK["btn"])):
        assert rule in dark and color in dark, rule
    assert "#f6e27f" in dark and "#1a3300" in dark
    assert 'class="bg"' in html and 'class="card' in html


# --- contrast ----------------------------------------------------------------------------------------------------------------

def luminance(color: str) -> float:
    channels = [int(color[i:i + 2], 16) / 255 for i in (1, 3, 5)]
    lin = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in channels]
    return 0.2126 * lin[0] + 0.7152 * lin[1] + 0.0722 * lin[2]


def ratio(a: str, b: str) -> float:
    hi, lo = sorted((luminance(a), luminance(b)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


TEXT_PAIRS = [("ink", "page"), ("ink", "card"), ("ink", "sect"), ("second", "card"), ("muted", "card"), ("muted", "page"), ("muted", "sect"),
              ("faint", "page"), ("btn_ink", "btn")]


@pytest.mark.parametrize("palette", ["LIGHT", "DARK"])
def test_every_text_and_background_pair_of_the_palette_is_readable(palette):
    colors = getattr(L, palette)
    for fg, bg in TEXT_PAIRS:
        assert ratio(colors[fg], colors[bg]) >= 4.5, (palette, fg, bg, colors[fg], colors[bg], ratio(colors[fg], colors[bg]))


def test_every_tone_is_readable_in_light_and_dark():
    assert set(L.TONES) == {"red", "orange", "yellow", "green", "neutral"}
    for name, (fg, bg, dark_fg, dark_bg) in L.TONES.items():
        assert ratio(fg, bg) >= 4.5, ("light", name, ratio(fg, bg))
        assert ratio(dark_fg, dark_bg) >= 4.5, ("dark", name, ratio(dark_fg, dark_bg))
        assert ratio(fg, L.LIGHT["card"]) >= 3 and ratio(dark_fg, L.DARK["card"]) >= 3, name  # a dot or a big figure on the card


# --- size --------------------------------------------------------------------------------------------------------------------

def heavy(brands, sections=1, lines=20):
    name = "visuel-campagne-ete-tres-long-nom-de-fichier-"
    group = lambda b: L.Group(f"Marque {b}", [L.Item(f"{name}{i}.jpg", "3 images en ligne", "expiré le 13/10/2026\ndans 20 jours") for i in range(lines)],
                              L.Button("Voir la bibliothèque", f"https://nyra.test/o/org/b/marque-{b}/library"), more=5)
    return [L.Section(f"Section {s}", "intro", [group(b) for b in range(brands)], "red") for s in range(sections)]


def test_a_full_summary_stays_under_gmails_clipping_limit():
    html, text = mail(heavy(10))
    assert len(html.encode()) < L.MAX_HTML_BYTES < 95_000 and re.search(r"et \d+ autres", html) and "marque-9/library" in html


def test_an_oversized_mail_cuts_its_lists_with_and_n_others_instead_of_growing():
    html, text = mail(heavy(50, sections=3))
    assert len(html.encode()) < L.MAX_HTML_BYTES and len(text.encode()) < L.MAX_HTML_BYTES
    assert re.search(r"et \d+ autres", html) and re.search(r"et \d+ autres", text)
    assert "Section 2" in html  # every section survives, the lists are what shrinks
