"""Lire un élément, fabriquer une ligne : chemins, gabarits, filtres, `where`. Pur.

**Un langage volontairement petit.** Un chemin pointé (`profile.title`,
`email[0].email`), un gabarit (`{{params.company|slug}}::{{item.link.linkedin}}`), des
filtres de casse et des NORMALISEURS (`domain`, `email`, `linkedin_slug`…) — ceux qui
font qu'une même société, écrite par deux outils, se reconnaît. Tout ce qui demande davantage — expression régulière, condition, calcul —
va dans une fonction (`oto_function`), jamais dans une syntaxe qu'on ferait grandir ici.

**Tolérant à la forme, jamais inventif.** Un chemin qui ne mène nulle part rend `None`
(une API tierce change de forme sans prévenir, et une ligne à moitié vide se voit au
remplissage par colonne), mais rien ici ne devine une valeur.
"""
from __future__ import annotations

import re
import unicodedata
from typing import Any, Optional
from urllib.parse import unquote, urlsplit

_SEGMENT = re.compile(r"([^.\[\]]+)|\[(\d+)\]")
_GABARIT = re.compile(r"\{\{\s*([^{}]+?)\s*\}\}")
FILTRES = ("slug", "lower", "upper", "strip", "unaccent", "domain", "email",
           "email_domain", "linkedin_slug", "url", "digits")
#: Les filtres qui NORMALISENT une valeur pour la comparer (`where.normalize`, les
#: correspondances avec un tableau) : une valeur qui n'a pas la forme rend `None`.
NORMALISEURS = ("lower", "unaccent", "slug", "domain", "email", "email_domain",
                "linkedin_slug", "url", "digits")
_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_LINKEDIN = re.compile(r"/(in|company|school|showcase)/([^/?#]+)", re.IGNORECASE)


class GabaritInvalide(ValueError):
    """Un gabarit cite une portée ou un filtre inconnus."""


def _segments(chemin: str) -> list:
    out: list = []
    for nom, index in _SEGMENT.findall(chemin or ""):
        out.append(int(index) if index else nom)
    return out


def lire(obj: Any, chemin: str) -> Any:
    """La valeur au bout de `chemin` dans `obj`, ou `None`. Chemin vide = `obj`."""
    cur = obj
    for seg in _segments(chemin):
        if isinstance(seg, int):
            if not isinstance(cur, list) or seg >= len(cur):
                return None
            cur = cur[seg]
        elif isinstance(cur, dict):
            cur = cur.get(seg)
        else:
            return None
        if cur is None:
            return None
    return cur


def sans_accents(texte: str) -> str:
    decompose = unicodedata.normalize("NFKD", texte)
    return "".join(c for c in decompose if not unicodedata.combining(c))


def slug(texte: Any) -> str:
    """Minuscules, accents retirés, chaque suite de caractères non alphanumériques
    réduite à `_`, et aucun `_` aux bords : `Café Lumière` → `cafe_lumiere`, `4B
    Conseil` → `4b_conseil`, `Acme Group (Ex : Old Acme)` → `acme_group_ex_old_acme`.
    C'est la forme des clés déjà écrites par les procédures de sourcing : la changer
    dédoublerait chaque ligne au premier passage d'une recette."""
    base = sans_accents(str(texte if texte is not None else "")).lower()
    return re.sub(r"[^a-z0-9]+", "_", base).strip("_")


def _hote(texte: str) -> Optional[str]:
    t = texte.strip().lower()
    if not t:
        return None
    if "@" in t and "/" not in t:
        t = t.rsplit("@", 1)[1]
    t = urlsplit(t if "://" in t else f"//{t}").hostname or ""
    t = t.removeprefix("www.").strip(".")
    return t if "." in t else None


def domaine(texte: Any) -> Optional[str]:
    """Le domaine d'une adresse web ou d'un e-mail, sans `www.` ni chemin :
    `https://www.Acme.com/about` → `acme.com`, `jane@acme.com` → `acme.com`."""
    return _hote(str(texte)) if texte is not None else None


def email(texte: Any) -> Optional[str]:
    """Un e-mail en minuscules, ou None s'il n'en a pas la forme — jamais une phrase
    d'erreur recopiée dans une colonne `email`."""
    t = str(texte).strip().lower() if texte is not None else ""
    return t if _EMAIL.match(t) else None


def linkedin_slug(texte: Any) -> Optional[str]:
    """L'identifiant d'un profil ou d'une page LinkedIn, quelle que soit l'URL :
    `https://fr.linkedin.com/in/Jane-Doe/?x=1` → `jane-doe`. Un identifiant nu passe."""
    t = unquote(str(texte or "")).strip()
    m = _LINKEDIN.search(t)
    if m:
        return m.group(2).lower()
    return t.lower() if t and "/" not in t and " " not in t else None


def url(texte: Any) -> Optional[str]:
    """Une adresse web avec son schéma, l'hôte en minuscules, sans `/` final."""
    t = str(texte or "").strip()
    if not t or " " in t:
        return None
    morceaux = urlsplit(t if "://" in t else f"https://{t}")
    if not morceaux.hostname or "." not in morceaux.hostname:
        return None
    base = f"{morceaux.scheme}://{morceaux.netloc.lower()}{morceaux.path}".rstrip("/")
    return base + (f"?{morceaux.query}" if morceaux.query else "")


def _filtrer(valeur: Any, filtre: str) -> Any:
    if valeur is None:
        return None
    if filtre == "slug":
        return slug(valeur)
    if filtre == "domain":
        return domaine(valeur)
    if filtre == "email":
        return email(valeur)
    if filtre == "email_domain":
        e = email(valeur)
        return e.rsplit("@", 1)[1] if e else None
    if filtre == "linkedin_slug":
        return linkedin_slug(valeur)
    if filtre == "url":
        return url(valeur)
    texte = str(valeur)
    if filtre == "digits":
        return re.sub(r"\D", "", texte) or None
    if filtre == "lower":
        return texte.lower()
    if filtre == "upper":
        return texte.upper()
    if filtre == "strip":
        return texte.strip()
    if filtre == "unaccent":
        return sans_accents(texte)
    raise GabaritInvalide(f"unknown filter `{filtre}` (known: {', '.join(FILTRES)})")


def normaliser(valeur: Any, filtre: Optional[str]) -> Any:
    """La valeur comparée par une clause : son normaliseur, sinon le pli sans casse ni
    accents des comparaisons de texte."""
    if valeur is None:
        return None
    if filtre:
        return _filtrer(valeur, filtre)
    return _plie(valeur)


def _expression(expr: str, portees: dict) -> Any:
    tete, *filtres = [p.strip() for p in expr.split("|")]
    portee, _, chemin = tete.partition(".")
    if portee not in portees:
        raise GabaritInvalide(
            f"unknown scope `{portee}` in `{{{{{expr}}}}}` (known: {', '.join(portees)})")
    valeur = lire(portees[portee], chemin)
    for f in filtres:
        valeur = _filtrer(valeur, f)
    return valeur


def rendre(gabarit: Any, portees: dict) -> Any:
    """Rend un gabarit, récursivement dans les listes et les objets.

    Une chaîne qui n'est QU'UN gabarit garde le type de sa valeur (une liste reste une
    liste, un nombre un nombre) ; mêlé à du texte, chaque morceau devient du texte et
    une valeur absente un texte vide."""
    if isinstance(gabarit, dict):
        return {k: rendre(v, portees) for k, v in gabarit.items()}
    if isinstance(gabarit, list):
        return [rendre(v, portees) for v in gabarit]
    if not isinstance(gabarit, str) or "{{" not in gabarit:
        return gabarit
    seul = _GABARIT.fullmatch(gabarit.strip())
    if seul:
        return _expression(seul.group(1), portees)

    def _morceau(m: re.Match) -> str:
        v = _expression(m.group(1), portees)
        return "" if v is None else str(v)
    return _GABARIT.sub(_morceau, gabarit)


def portees_citees(gabarit: Any) -> set[str]:
    """Les portées qu'un gabarit cite (`params`, `item`…) — pour refuser à l'écriture
    une recette qui cite une portée inconnue, plutôt qu'à la première page."""
    if isinstance(gabarit, dict):
        return set().union(*(portees_citees(v) for v in gabarit.values()), set())
    if isinstance(gabarit, list):
        return set().union(*(portees_citees(v) for v in gabarit), set())
    if not isinstance(gabarit, str):
        return set()
    return {m.group(1).split("|")[0].strip().partition(".")[0]
            for m in _GABARIT.finditer(gabarit)}


def poser(obj: dict, chemin: str, valeur: Any) -> None:
    """Pose `valeur` au bout d'un chemin pointé d'objets (`custom_fields.oto_row`),
    créant les objets intermédiaires."""
    *tete, fin = [s for s in chemin.split(".") if s]
    for seg in tete:
        obj = obj.setdefault(seg, {})
    obj[fin] = valeur


def sans_vides(valeur: Any) -> Any:
    """Les arguments rendus, sans les clés dont la valeur est vide : une colonne vide
    n'envoie RIEN (jamais `null` ni `""`, qui videraient un champ chez le tiers ou feraient
    refuser l'appel). Récursif dans les objets et les listes."""
    if isinstance(valeur, dict):
        out = {k: sans_vides(v) for k, v in valeur.items()}
        return {k: v for k, v in out.items() if not _vide(v)}
    if isinstance(valeur, list):
        return [x for x in (sans_vides(v) for v in valeur) if not _vide(x)]
    return valeur


def exigees_vides(gabarit: Any, portees: dict, colonnes) -> bool:
    """Un morceau du gabarit qui cite une colonne EXIGÉE rend-il vide ? (`n/a|digits`
    → rien) : l'appel partirait sans son filtre — une recherche rendrait sa page par
    défaut, celle d'un inconnu."""
    if isinstance(gabarit, dict):
        return any(exigees_vides(v, portees, colonnes) for v in gabarit.values())
    if isinstance(gabarit, list):
        return any(exigees_vides(v, portees, colonnes) for v in gabarit)
    if not isinstance(gabarit, str) or not (colonnes_citees(gabarit, "row") & set(colonnes)):
        return False
    return _vide(rendre(gabarit, portees))


def colonnes_citees(gabarit: Any, portee: str) -> set[str]:
    """Les colonnes qu'un gabarit lit dans une portée (`{{row.siren|digits}}` →
    `siren`) — pour ne sélectionner que les lignes où elles sont remplies."""
    if isinstance(gabarit, dict):
        return set().union(*(colonnes_citees(v, portee) for v in gabarit.values()), set())
    if isinstance(gabarit, list):
        return set().union(*(colonnes_citees(v, portee) for v in gabarit), set())
    if not isinstance(gabarit, str):
        return set()
    out = set()
    for m in _GABARIT.finditer(gabarit):
        tete = m.group(1).split("|")[0].strip()
        nom, _, chemin = tete.partition(".")
        if nom == portee and chemin:
            out.add(_segments(chemin)[0])
    return out


def _plie(v: Any) -> str:
    return sans_accents(str(v)).casefold()


def _vide(v: Any) -> bool:
    return v is None or (isinstance(v, (str, list, dict)) and not v)


def _portees(params: dict, row: Optional[dict], item: Any = None) -> dict:
    out: dict = {"params": params}
    if row is not None:
        out["row"] = row
    if item is not None:
        out["item"] = item
    return out


def garde(item: Any, clauses: list[dict], params: dict, *, row: Optional[dict] = None,
          ensembles: Optional[dict] = None) -> bool:
    """L'élément passe-t-il TOUTES les clauses `where` ? Comparaisons de texte sans
    casse ni accents : `Spain` = `spain`, `Côte` = `cote` — ou par le normaliseur de la
    clause (`normalize`). `in_table` / `not_in_table` lisent `ensembles[i]`, les valeurs
    normalisées du tableau de la clause `i`, chargées une fois par exécution."""
    for i, c in enumerate(clauses or []):
        v = lire(item, c["path"])
        op = c["op"]
        if op in ("in_table", "not_in_table"):
            cle = normaliser(v, c.get("normalize"))
            dedans = cle is not None and cle in (ensembles or {}).get(i, set())
            # Un élément sans valeur n'est pas « dans » la liste d'exclusion : il passe.
            if (op == "in_table") != dedans:
                return False
            continue
        attendu = rendre(c.get("value"), _portees(params, row))
        if c.get("normalize") and op not in ("empty", "not_empty"):
            v = normaliser(v, c["normalize"])
            attendu = [x for x in (normaliser(a, c["normalize"]) for a in attendu or [])
                       if x is not None] \
                if isinstance(attendu, list) else normaliser(attendu, c["normalize"])
        if op == "empty":
            ok = _vide(v)
        elif op == "not_empty":
            ok = not _vide(v)
        elif op in ("eq", "ne"):
            egal = v is not None and _plie(v) == _plie(attendu)
            ok = egal if op == "eq" else not egal
        elif op in ("in", "not_in"):
            dedans = v is not None and _plie(v) in {_plie(a) for a in attendu or []}
            ok = dedans if op == "in" else not dedans
        else:  # contains_any — validé par le contrat
            texte = "" if v is None else _plie(v)
            ok = any(_plie(a) in texte for a in attendu or [])
        if not ok:
            return False
    return True


def cellule(item: Any, spec: Any, params: dict, *, row: Optional[dict] = None) -> Any:
    """La valeur d'une colonne pour un élément, d'après sa spécification."""
    if isinstance(spec, str):
        return lire(item, spec)
    if "const" in spec:
        return spec["const"]
    if "template" in spec:
        v = rendre(spec["template"], _portees(params, row, item))
    else:
        v = lire(item, spec["path"])
    if v is None:
        v = spec.get("default")
    borne: Optional[int] = spec.get("max")
    if borne and isinstance(v, str) and len(v) > borne:
        v = v[:borne]
    return v


def ligne(item: Any, correspondance: dict, valeurs: dict, params: dict, *,
          row: Optional[dict] = None) -> dict:
    """La ligne d'un élément : ses colonnes, puis les valeurs fixes de la recette."""
    out = {col: cellule(item, spec, params, row=row)
           for col, spec in (correspondance or {}).items()}
    for col, gab in (valeurs or {}).items():
        out[col] = rendre(gab, _portees(params, row))
    return out


def cle(item: Any, spec: dict, rangee: dict, params: dict, *,
        row: Optional[dict] = None) -> Optional[str]:
    """La valeur de clé d'un élément : son gabarit, sinon la colonne-clé de la ligne."""
    if spec.get("template"):
        portees = _portees(params, row, item)
        # Un morceau absent annule la clé : `acme::` n'identifie personne, et deux
        # éléments sans profil se fondraient en une seule ligne.
        if any(_vide(_expression(m.group(1), portees))
               for m in _GABARIT.finditer(spec["template"])):
            return None
        v = rendre(spec["template"], portees)
    else:
        v = rangee.get(spec["column"])
    if v is None or (isinstance(v, str) and not v.strip()):
        return None
    return str(v)
