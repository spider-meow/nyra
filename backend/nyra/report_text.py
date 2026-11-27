"""The words of the report (HTML and PDF) in French and English: one dictionary, no translation library.

A message is a text, or (singular, plural) chosen by `n`: French takes the singular for 0 and 1, English for 1 only. Values are
put in with `str.format`. The template asks for messages by key (`t("expired_since", n=3)`); a key that is not here is a text
already in French (a redirect reason, « Page inconnue ») and comes back translated by `PASSED`, or as it is.
"""

from __future__ import annotations

from typing import Callable

LANGS = ("fr", "en")

MESSAGES: dict[str, dict[str, object]] = {
    "fr": {
        "title": "Rapport Nyra",
        "date_unknown": "Date inconnue",
        "expired_since": "Expiré depuis {n} j",
        "expires_today": "Expire aujourd'hui",
        "expires_in": "Expire dans {n} j",
        "also": ("+ {n} autre visuel", "+ {n} autres visuels"),
        "to_check_score": "À vérifier · {n} %",
        "not_read_again": "Page non relue depuis",
        "not_in_library": "Pas dans la bibliothèque",
        "on_pages": "Sur {n} pages",
        "n_remove": "{n} à retirer",
        "n_soon": "{n} bientôt",
        "n_check": "{n} à vérifier",
        "n_unref": ("{n} non référencée", "{n} non référencées"),
        "home": "/ (accueil)",
        "generated": "Généré le {date} à {time} UTC",
        "deadlines": "échéances sous {n} jours",
        "expired_visuals": ("{n} visuel expiré", "{n} visuels expirés"),
        "still_online": "encore en ligne.",
        "no_expired": "Aucun visuel expiré en ligne.",
        "n_images": ("{n} image", "{n} images"),
        "missing_from_library": ("absente de la bibliothèque.", "absentes de la bibliothèque."),
        "pages_to_look": ("{n} page à regarder.", "{n} pages à regarder."),
        "lead": ("{n} page concernée, classée par version du site puis par rubrique.",
                 "{n} pages concernées, classées par version du site puis par rubrique."),
        "sites_read": "Sites lus {when}.",
        "read_unknown": "Date de lecture des sites inconnue.",
        "fig_expired": ("expiré en ligne", "expirés en ligne"),
        "fig_soon": ("expire sous 90 jours", "expirent sous 90 jours"),
        "fig_check": "à vérifier",
        "fig_unref": ("non référencée", "non référencées"),
        "fig_pages": ("page concernée", "pages concernées"),
        "scope": "{refs} références comparées à {images} images lues sur {pages} pages. Les occurrences marquées « faux positif » "
                 "ne figurent pas dans ce rapport.",
        "suspect": "La dernière lecture de {sites} semble incomplète : aucun visuel n'est marqué retiré.",
        "first": "À traiter d'abord",
        "contents": "Sommaire",
        "col_place": "Version et rubrique",
        "col_remove": "À retirer",
        "col_soon": "Bientôt",
        "col_check": "À vérifier",
        "col_unref": "Non réf.",
        "col_page": "Page",
        "nothing": "Rien à signaler sur ce périmètre.",
        "everywhere_title": "Sur de nombreuses pages",
        "everywhere": " : images absentes de la bibliothèque vues sur plus de {n} pages (bandeau, pied de page…), montrées une seule fois.",
        "read_on": "lue le {date}",
        "footer_settings": "Réglages de détection : {settings}. Sites lus : {when}.",
        "footer": "Rapport produit par Nyra. Détection automatique par empreinte perceptuelle et similarité visuelle ; les correspondances "
                  "« à vérifier » demandent une confirmation humaine. Le détail complet, visuels en règle compris, est dans les CSV.",
        "unknown_date": "date inconnue",
        "between": "du {first} au {last}",
        "pdf_footer": "{title} · rapport du {date}",
    },
    "en": {
        "title": "Nyra report",
        "date_unknown": "Date unknown",
        "expired_since": ("Expired {n} day ago", "Expired {n} days ago"),
        "expires_today": "Expires today",
        "expires_in": ("Expires in {n} day", "Expires in {n} days"),
        "also": ("+ {n} other visual", "+ {n} other visuals"),
        "to_check_score": "To check · {n}%",
        "not_read_again": "Page not read again since",
        "not_in_library": "Not in the library",
        "on_pages": "On {n} pages",
        "n_remove": "{n} to remove",
        "n_soon": "{n} expiring soon",
        "n_check": "{n} to check",
        "n_unref": ("{n} not in the library", "{n} not in the library"),
        "home": "/ (home)",
        "generated": "Generated on {date} at {time} UTC",
        "deadlines": "deadlines within {n} days",
        "expired_visuals": ("{n} expired visual", "{n} expired visuals"),
        "still_online": "still online.",
        "no_expired": "No expired visual online.",
        "n_images": ("{n} image", "{n} images"),
        "missing_from_library": ("not in the library.", "not in the library."),
        "pages_to_look": ("{n} page to look at.", "{n} pages to look at."),
        "lead": ("{n} page concerned, sorted by site version, then by section.",
                 "{n} pages concerned, sorted by site version, then by section."),
        "sites_read": "Sites read {when}.",
        "read_unknown": "Date the sites were read unknown.",
        "fig_expired": ("expired online", "expired online"),
        "fig_soon": ("expires within 90 days", "expire within 90 days"),
        "fig_check": "to check",
        "fig_unref": ("not in the library", "not in the library"),
        "fig_pages": ("page concerned", "pages concerned"),
        "scope": "{refs} references compared with {images} images read on {pages} pages. Matches marked as false positives are not "
                 "in this report.",
        "suspect": "The last read of {sites} looks incomplete: no visual is marked as removed.",
        "first": "Deal with first",
        "contents": "Contents",
        "col_place": "Version and section",
        "col_remove": "To remove",
        "col_soon": "Soon",
        "col_check": "To check",
        "col_unref": "Not in library",
        "col_page": "Page",
        "nothing": "Nothing to report for this scope.",
        "everywhere_title": "On many pages",
        "everywhere": ": images not in the library seen on more than {n} pages (banner, footer…), shown once.",
        "read_on": "read on {date}",
        "footer_settings": "Detection settings: {settings}. Sites read: {when}.",
        "footer": "Report produced by Nyra. Automatic detection by perceptual hash and visual similarity; matches marked « to check » need "
                  "a human confirmation. The full detail, visuals with valid rights included, is in the CSV files.",
        "unknown_date": "date unknown",
        "between": "from {first} to {last}",
        "pdf_footer": "{title} · report of {date}",
    },
}

# Texts made elsewhere in French (`report.REDIRECT_REASONS`, `report.NO_PAGE`, a version with no site), in English.
PASSED = {
    "en": {
        "Redirection permanente, règle non activée": "Permanent redirect, rule not turned on",
        "Redirection temporaire": "Temporary redirect",
        "Redirigée par un script": "Redirected by a script",
        "Renvoyée vers l'accueil ou une page parente": "Sent to the home page or a parent page",
        "Image de la page d'arrivée non lue": "Image of the landing page not read",
        "Renvoyée vers une autre rubrique ou un autre pays": "Sent to another section or country",
        "Renvoyée vers un autre site": "Sent to another site",
        "Renvoyée vers une barrière (consentement, connexion, âge, pays)": "Sent to a barrier (consent, login, age, country)",
        "La page d'arrivée a répondu en erreur": "The landing page answered with an error",
        "Page inconnue": "Unknown page",
        "Site inconnu": "Unknown site",
    },
}


def translator(lang: str) -> Callable[..., str]:
    """`t(key, n=None, **values)` in `lang` (French for any other value)."""
    lang = lang if lang in LANGS else "fr"
    messages, passed = MESSAGES[lang], PASSED.get(lang, {})

    def t(key: str, n: int | None = None, **values: object) -> str:
        text = messages.get(key)
        if text is None:
            return passed.get(key, key)
        if isinstance(text, tuple):
            one = n is not None and (n <= 1 if lang == "fr" else n == 1)
            text = text[0] if one else text[1]
        return str(text).format(n=n, **values)

    return t
