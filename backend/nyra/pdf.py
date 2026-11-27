"""The report as an A4 PDF, printed by Chromium from the report's own HTML (`ReportFiles.print_html`).

Chromium does not number the pages of a table of contents by itself. Two passes do it: the first print says on which
sheet each rubrique starts (each heading carries an invisible marker, `NYRAMARK<anchor>Z`, found in the text of the
sheets), the second prints with those numbers written in. The table of contents has the same height in both passes
(« p. – » becomes « p. 12 » on the same line), so the numbers stay true.
"""

from __future__ import annotations

import html
import io
import re

MARKER = re.compile(r"NYRAMARK([a-z]+)Q([a-z]+)Z")  # letters only: the digits of a subset font do not always come back as text
MARGIN = {"top": "14mm", "bottom": "16mm", "left": "14mm", "right": "14mm"}
MAX_SHEETS = 2000  # the text of more sheets is not read: the table of contents keeps « p. – »


def _footer(text: str) -> str:
    return ('<div style="font:8px Inter,system-ui,sans-serif;color:#8a9680;width:100%;padding:0 14mm;display:flex;'
            f'justify-content:space-between"><span>{html.escape(text)}</span>'
            '<span><span class="pageNumber"></span> / <span class="totalPages"></span></span></div>')


def _letters(number: int) -> str:
    out = ""
    while True:
        number, digit = divmod(number, 26)
        out = chr(97 + digit) + out
        if not number:
            return out


def _number(letters: str) -> int:
    value = 0
    for letter in letters:
        value = value * 26 + ord(letter) - 97
    return value


def marker(anchor: str) -> str:
    """The invisible text that says where the rubrique `v<i>-<j>` starts (`by_place` gives the anchors)."""
    version, rubric = anchor[1:].split("-")
    return f"NYRAMARK{_letters(int(version))}Q{_letters(int(rubric))}Z"


def sheets_of(pdf: bytes) -> dict[str, int]:
    """On which sheet (from 1) each rubrique starts: its anchor (`v0-3`) -> sheet."""
    from pypdf import PdfReader

    found: dict[str, int] = {}
    for number, sheet in enumerate(PdfReader(io.BytesIO(pdf)).pages[:MAX_SHEETS], start=1):
        for version, rubric in MARKER.findall(re.sub(r"\s", "", sheet.extract_text() or "")):
            found.setdefault(f"v{_number(version)}-{_number(rubric)}", number)
    return found


def report_pdf(print_html: str, *, footer: str) -> bytes:
    """Print the report in A4, with the page numbers of its table of contents. Needs Chromium (the worker has it)."""
    from playwright.sync_api import sync_playwright

    options = {"format": "A4", "print_background": True, "margin": MARGIN, "display_header_footer": True,
               "header_template": "<span></span>", "footer_template": _footer(footer)}
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        try:
            page = browser.new_page()
            page.set_content(print_html, wait_until="load")
            first = page.pdf(**options)
            numbers = sheets_of(first)
            if not numbers:
                return first
            page.evaluate("""numbers => document.querySelectorAll('[data-page]').forEach(cell => {
                if (numbers[cell.dataset.page]) cell.textContent = 'p. ' + numbers[cell.dataset.page]; })""", numbers)
            return page.pdf(**options)
        finally:
            browser.close()
