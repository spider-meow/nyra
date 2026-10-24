"""Spreadsheet-safe text for the CSV files the product writes and reads back (library export, organization export)."""

from __future__ import annotations


def csv_cell(value: str) -> str:
    """Spreadsheets run a cell that starts like a formula: such a cell gets a leading apostrophe, which makes it text.

    `=` and `@` always; `+` and `-` unless a number or a space follows ("-20 %" and "+33 6" stay as they are).
    """
    first = value[:1]
    if first in ("=", "@", "\t", "\r") or (first in ("+", "-") and len(value) > 1 and value[1] not in "0123456789 ."):
        return "'" + value
    return value


def csv_text(value: str) -> str:
    """The reverse of `csv_cell`, so an exported file imports back to the same text."""
    return value[1:] if value.startswith("'") and csv_cell(value[1:]) != value[1:] else value
