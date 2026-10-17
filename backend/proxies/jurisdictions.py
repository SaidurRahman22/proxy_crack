"""Legal blocs, as a data table rather than an opinion baked into code.

A chain whose hops sit in three different countries can still be a chain
whose hops sit in three countries that share intelligence with each other
by treaty. That is a real weakness and worth saying -- but *which* groupings
matter is a political judgement that changes, that reasonable operators
disagree about, and that this codebase has no business asserting as fact.

So the groupings live in a dict, the default is overridable wholesale by
`settings.PROXY_JURISDICTION_BLOCS`, and the code that consumes them knows
nothing about any particular bloc. Adding "my own threat model" is a
settings change, not a patch.

What the defaults are and are not
---------------------------------
The Five/Nine/Fourteen Eyes memberships below are the commonly-cited
compositions of the UKUSA-derived signals-intelligence arrangements. They
are **not** a legal instrument: the underlying agreements are partly
classified, membership is described differently by different sources, and
intelligence sharing happens between states that appear on no such list.
The EU entry is the 27 member states as of 2026 and is a straightforward
legal fact by comparison.

Treat a bloc finding as "these hops sit in countries with a documented
habit of sharing", not as "this traffic will be correlated". The honest
framing is in `proxies.diversity` and it is deliberate.

Country codes are ISO 3166-1 alpha-2, upper case. `Proxy.country` is free
text and may hold a name rather than a code, so `normalise_country` does a
small amount of coercion and otherwise reports the value as unknown rather
than guessing.
"""

from django.conf import settings

__all__ = [
    "DEFAULT_BLOCS",
    "blocs",
    "blocs_containing",
    "normalise_country",
    "smallest_bloc_containing",
]


FIVE_EYES = ("AU", "CA", "NZ", "GB", "US")
NINE_EYES = FIVE_EYES + ("DK", "FR", "NL", "NO")
FOURTEEN_EYES = NINE_EYES + ("BE", "DE", "IT", "ES", "SE")

EU_27 = (
    "AT", "BE", "BG", "HR", "CY", "CZ", "DK", "EE", "FI", "FR", "DE", "GR",
    "HU", "IE", "IT", "LV", "LT", "LU", "MT", "NL", "PL", "PT", "RO", "SK",
    "SI", "ES", "SE",
)

#: `{key: {"label": str, "countries": (ISO-3166-1 alpha-2, ...)}}`.
#:
#: Order matters only for tie-breaking when two blocs are the same size;
#: `smallest_bloc_containing` prefers the most specific (fewest members),
#: because "all three hops are in the Five Eyes" is a sharper statement
#: than "all three hops are in the Fourteen Eyes" even though both are
#: true.
DEFAULT_BLOCS = {
    "five_eyes": {"label": "Five Eyes", "countries": FIVE_EYES},
    "nine_eyes": {"label": "Nine Eyes", "countries": NINE_EYES},
    "fourteen_eyes": {"label": "Fourteen Eyes", "countries": FOURTEEN_EYES},
    "eu": {"label": "European Union", "countries": EU_27},
}


# A handful of spellings seen in provider CSV exports. Deliberately tiny:
# this is a convenience for the common case, not a country-name database.
# Anything not here and not a two-letter code is reported as unknown, which
# is the honest answer -- see the module docstring in `proxies.diversity`
# on why "unknown" is never allowed to read as "fine".
_NAME_TO_CODE = {
    "united states": "US",
    "united states of america": "US",
    "usa": "US",
    "united kingdom": "GB",
    "great britain": "GB",
    "uk": "GB",
    "netherlands": "NL",
    "the netherlands": "NL",
    "germany": "DE",
    "france": "FR",
    "canada": "CA",
    "australia": "AU",
    "new zealand": "NZ",
    "sweden": "SE",
    "switzerland": "CH",
    "singapore": "SG",
    "japan": "JP",
    "brazil": "BR",
    "india": "IN",
    "russia": "RU",
    "romania": "RO",
    "moldova": "MD",
    "iceland": "IS",
    "panama": "PA",
    "seychelles": "SC",
}


def normalise_country(value):
    """`"nl"` / `"Netherlands"` -> `"NL"`. Anything else -> `""`.

    Returning `""` rather than the raw value is the point: a jurisdiction
    check that silently compares `"Netherlands"` with `"NL"` and finds
    them different would report diversity that does not exist.
    """
    text = (value or "").strip()
    if not text:
        return ""
    if len(text) == 2 and text.isalpha():
        return text.upper()
    return _NAME_TO_CODE.get(text.lower(), "")


def blocs():
    """The configured bloc map, normalised to upper-case country codes.

    `settings.PROXY_JURISDICTION_BLOCS` replaces the default map entirely
    rather than merging into it. Merging would mean an operator who
    disagrees with a default grouping cannot remove it, only add to it --
    and the whole reason this is configuration is that the defaults are
    contestable.
    """
    configured = getattr(settings, "PROXY_JURISDICTION_BLOCS", None)
    source = DEFAULT_BLOCS if configured is None else configured

    cleaned = {}
    for key, entry in (source or {}).items():
        if isinstance(entry, dict):
            label = entry.get("label") or str(key)
            countries = entry.get("countries") or ()
        else:
            # Bare sequence of country codes; label defaults to the key.
            label = str(key)
            countries = entry
        codes = tuple(
            sorted({str(code).strip().upper() for code in countries if str(code).strip()})
        )
        if codes:
            cleaned[str(key)] = {"label": str(label), "countries": codes}
    return cleaned


def blocs_containing(countries):
    """Every bloc that contains **all** of `countries`.

    An empty or partly-unknown country set returns nothing: a bloc claim
    about hops whose country we do not know would be a guess.
    """
    codes = {normalise_country(code) for code in countries}
    if not codes or "" in codes:
        return []
    return [
        {"key": key, "label": entry["label"], "countries": entry["countries"]}
        for key, entry in blocs().items()
        if codes <= set(entry["countries"])
    ]


def smallest_bloc_containing(countries):
    """The most specific bloc containing all `countries`, or `None`.

    Most specific = fewest member states, because that is the sharper
    claim. Ties are broken by key so the answer is deterministic.
    """
    found = blocs_containing(countries)
    if not found:
        return None
    return min(found, key=lambda entry: (len(entry["countries"]), entry["key"]))
