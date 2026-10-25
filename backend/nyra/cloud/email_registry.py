"""Every e-mail Nyra sends or has sent for it, with a sample made of invented data: what the back office's "E-mails" page
shows and sends as a test.

`ENTRIES` is the list. `sender` says who sends it: "nyra" (through `mailer`, so a test can be sent) or "supabase" (Supabase
Auth sends it from the templates in `supabase/templates/`: preview only). A test (`test_every_mail_kind_is_registered`) fails
when code calls `mailer.send` / `mailer.deliver` with a `kind=` that is not a key here. See « Ajouter un e-mail » in
docs/ARCHITECTURE.md.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Callable, Optional

from . import digest

EXAMPLE_URL = "https://example.com"
_VARIABLES = {"ConfirmationURL": f"{EXAMPLE_URL}/auth/confirm?exemple", "Email": "prenom.nom@example.com"}


@dataclass(frozen=True)
class Entry:
    key: str
    label: str
    description: str
    sender: str  # "nyra" or "supabase"
    sample: Callable[[], tuple[str, str, str]]  # (subject, html, text), invented data only


def digest_data(today: date) -> dict:
    """What `digest.gather` would read for an invented organization: the input of the real `digest.build`."""
    def soon(name: str, days: int) -> dict:
        return {"filename": name, "expiry_date": today + timedelta(days=days), "days": days}

    brand = {"name": "Maison Exemple", "slug": "maison-exemple", "suspect": True, "capped": False, "unfinished": False,
             "new": [{"filename": "affiche-printemps.jpg", "expiry_date": today - timedelta(days=12), "images": 3}], "new_total": 1,
             "soon": [soon("banniere-ete.jpg", 20)], "soon_total": 1, "later": [soon("photo-equipe.jpg", 60)], "later_total": 1}
    return {"org_name": "Maison Exemple", "org_slug": "maison-exemple", "brands": [brand]}


def _digest_sample() -> tuple[str, str, str]:
    mail = digest.build(digest_data(date.today()), EXAMPLE_URL)
    if mail is None:  # the invented data always has something to say
        raise RuntimeError("the sample of the monthly e-mail has nothing to say")
    return mail.subject, mail.html, mail.text


def _templates_dir() -> Path:
    """`supabase/templates`, next to the backend in a checkout, or under the working directory in the web image."""
    for root in (Path(__file__).resolve().parents[3], Path.cwd()):
        if (root / "supabase" / "templates").is_dir():
            return root / "supabase" / "templates"
    raise FileNotFoundError("supabase/templates")


def _supabase_sample(subject: str, template: str) -> Callable[[], tuple[str, str, str]]:
    def sample() -> tuple[str, str, str]:
        raw = (_templates_dir() / template).read_text(encoding="utf-8")
        html = re.sub(r"\{\{\s*\.(\w+)\s*\}\}", lambda found: _VARIABLES[found.group(1)], raw)  # KeyError: a variable to add above
        return subject, html, "Supabase envoie cet e-mail en HTML seulement : il n'a pas de version texte."
    return sample


ENTRIES = (
    Entry("monthly_digest", "Résumé mensuel", "Une fois par mois aux administrateurs : visuels à traiter et droits qui expirent.", "nyra", _digest_sample),
    Entry("invite", "Invitation", "Donne accès à Nyra à une nouvelle personne. Le lien crée son compte.", "supabase",
          _supabase_sample("Votre accès à Nyra", "invite.html")),
    Entry("recovery", "Nouveau mot de passe", "Envoyé à la demande d'une personne qui a oublié son mot de passe.", "supabase",
          _supabase_sample("Nouveau mot de passe — Nyra", "recovery.html")),
)


def get(key: str) -> Optional[Entry]:
    return next((entry for entry in ENTRIES if entry.key == key), None)
