"""Les colonnes `date` et `datetime` : accepter large, stocker UNE forme (oto-backend#859).

Mesuré sur le parc le 30/09/2026 (225 tableaux, ~42 000 valeurs) : les agents écrivent
une date sous toutes les formes — `Z`, offset, fractions, sans secondes, sans fuseau,
date nue dans une colonne d'instants, heure et fuseau dans une colonne de jours,
nombres. Un validateur strict casserait les écrivains réels ; un validateur laxiste
sans normalisation laisse le tri et les filtres comparer du texte. D'où la règle :
**on lit large, on stocke une forme**, et `lire` est le SEUL juge — l'écriture
(`normaliser_ligne`), la validation (`validation._conformite_scalaire`), les filtres
(`typer_les_clauses`) et le script de reprise (`scripts/normaliser_dates.py`) passent
tous par elle.

**Ce qui se lit** (espaces de tête et de queue retirés) :

- ISO : `2026-09-04T10:00:00Z`, `…+02:00`, `…+0200`, `…+02`, fractions (`.123`),
  sans secondes (`2026-09-04T10:00`), séparateur espace (`2026-09-04 10:00`) ;
- l'usage français, JOUR D'ABORD : `04/09/2026`, `4/9/2026`, `04/09/2026 10:30`
  (secondes permises) — jamais lu mois d'abord : `09/04/2026` est le 9 avril ;
- un horodatage Unix : un nombre JSON, ou une chaîne de 9 chiffres ou plus. Il est en
  SECONDES sous 100 000 000 000, en MILLISECONDES au-delà — la frontière tombe en 5138
  lu en secondes et en 1973 lu en millisecondes, aucun instant plausible n'est donc
  ambigu. Sous 100 000 000 (mars 1973 en secondes), un nombre est REFUSÉ : `2026` y
  serait une année, `45900` un numéro de série de tableur, et les lire en secondes
  depuis 1970 rendrait une date fausse sans le dire ;
- une date IMPRÉCISE, à sa précision : année `2026`, mois `2026-09`, jour `2026-09-04`.

**Ce qui se stocke** :

- colonne `datetime` : un instant devient `AAAA-MM-JJTHH:MM:SSZ`, en UTC, fractions
  retirées ; un instant SANS fuseau est supposé UTC, et l'écriture le dit dans
  `notices` (`normaliser_ligne`). Une date imprécise reste à sa précision — pas de
  « minuit » fabriqué : `2026-09-04` reste `2026-09-04` ;
- colonne `date` : `AAAA-MM-JJ` (ou `AAAA-MM`, `AAAA` à leur précision). Un instant
  AVEC fuseau y est ramené à la date QU'IL PORTE, dans son propre fuseau — pas
  convertie en UTC : `2026-09-04T00:30:00+02:00` est le 4 septembre pour celui qui
  l'a écrit, et le convertir le ferait reculer au 3. Un horodatage Unix, lui, n'a pas
  d'autre fuseau que l'UTC.

**Lu comme début de période** : le tri et les filtres prennent une date imprécise à
son PREMIER instant (UTC) — `2026-09` vaut le 1ᵉʳ septembre à minuit. La BORNE d'un
filtre, elle, couvre sa période entière : cf. `bornes_du_filtre`.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Any, Callable, Optional

from .couches import VALUE_LAYER
from .declaration import _fields, champ_declare, validation_active

TYPES_DATES = ("date", "datetime")

#: Sous ce seuil, un nombre n'est pas un horodatage (cf. docstring du module).
SEUIL_HORODATAGE = 100_000_000
#: À partir de ce seuil, un horodatage est en millisecondes.
SEUIL_MILLISECONDES = 100_000_000_000

#: Ce qu'un refus dit attendre — la même phrase pour l'écriture et pour un filtre.
FORMES_ACCEPTEES = ("ISO (`2026-09-04T10:00:00Z`, `2026-09-04T12:00+02:00`, "
                    "`2026-09-04 10:00`), une date à sa précision (`2026-09-04`, "
                    "`2026-09`, `2026`), `04/09/2026` (jour d'abord, heure `10:30` "
                    "permise) ou un horodatage Unix (secondes ou millisecondes)")

_ANNEE = re.compile(r"^(\d{4})$")
_MOIS = re.compile(r"^(\d{4})-(\d{2})$")
_JOUR = re.compile(r"^(\d{4})-(\d{2})-(\d{2})$")
_INSTANT = re.compile(
    r"^(\d{4})-(\d{2})-(\d{2})[Tt ](\d{2}):(\d{2})(?::(\d{2})(?:[.,](\d+))?)?"
    r"\s*(?:([Zz])|([+-])(\d{2})(?::?(\d{2}))?)?$")
_FRANCAIS = re.compile(
    r"^(\d{1,2})/(\d{1,2})/(\d{4})(?:[ T](\d{1,2}):(\d{2})(?::(\d{2}))?)?$")
_CHIFFRES = re.compile(r"^\d{9,}(?:\.\d+)?$")


@dataclass(frozen=True)
class DateLue:
    """Une valeur LUE : sa forme stockée, sa précision et sa période (UTC).

    `debut`/`fin` = la période, fin EXCLUSIVE ; un instant a `fin == debut`.
    `suppose_utc` = la valeur portait une heure sans fuseau, et l'UTC a été supposé."""
    forme: str
    precision: str          # "annee" | "mois" | "jour" | "instant"
    debut: datetime
    fin: datetime
    suppose_utc: bool = False


def _iso_utc(t: datetime) -> str:
    return t.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _periode(annee: int, mois: Optional[int] = None,
             jour: Optional[int] = None) -> Optional[DateLue]:
    try:
        if jour is not None:
            d = date(annee, mois, jour)
            debut = datetime(d.year, d.month, d.day, tzinfo=timezone.utc)
            return DateLue(d.isoformat(), "jour", debut, debut + timedelta(days=1))
        if mois is not None:
            debut = datetime(annee, mois, 1, tzinfo=timezone.utc)
            fin = (datetime(annee + 1, 1, 1, tzinfo=timezone.utc) if mois == 12
                   else datetime(annee, mois + 1, 1, tzinfo=timezone.utc))
            return DateLue(f"{annee:04d}-{mois:02d}", "mois", debut, fin)
        debut = datetime(annee, 1, 1, tzinfo=timezone.utc)
        return DateLue(f"{annee:04d}", "annee", debut,
                       datetime(annee + 1, 1, 1, tzinfo=timezone.utc))
    except (ValueError, OverflowError):
        return None   # 2026-02-31, mois 13, ou l'an 9999 dont la fin déborde


def _instant(t: datetime, ftype: str, suppose_utc: bool) -> DateLue:
    """Un instant LU. `t` porte son fuseau (celui écrit, ou l'UTC supposé)."""
    if ftype == "date":
        # La date que l'instant PORTE, dans son propre fuseau (docstring du module).
        lue = _periode(t.year, t.month, t.day)
        return lue  # type: ignore[return-value]  # t est déjà une date valide
    utc = t.astimezone(timezone.utc).replace(microsecond=0)
    return DateLue(_iso_utc(utc), "instant", utc, utc, suppose_utc)


def _horodatage(n: float, ftype: str) -> Optional[DateLue]:
    if math.isnan(n) or math.isinf(n) or n < SEUIL_HORODATAGE:
        return None
    secondes = n / 1000 if n >= SEUIL_MILLISECONDES else n
    try:
        t = datetime.fromtimestamp(int(secondes), tz=timezone.utc)
    except (ValueError, OverflowError, OSError):
        return None
    return _instant(t, ftype, False)


def lire(valeur: Any, ftype: str = "datetime") -> Optional[DateLue]:
    """La valeur LUE pour une colonne `ftype` (`date` ou `datetime`), ou `None` si elle
    est illisible. Pure, sans effet : c'est l'appelant qui décide de refuser, de garder
    ou de signaler ce qu'elle ne lit pas."""
    if isinstance(valeur, bool):
        return None
    if isinstance(valeur, (int, float)):
        return _horodatage(float(valeur), ftype)
    if not isinstance(valeur, str):
        return None
    s = valeur.strip()
    if _CHIFFRES.match(s):
        return _horodatage(float(s), ftype)
    m = _ANNEE.match(s)
    if m:
        return _periode(int(m[1]))
    m = _MOIS.match(s)
    if m:
        return _periode(int(m[1]), int(m[2]))
    m = _JOUR.match(s)
    if m:
        return _periode(int(m[1]), int(m[2]), int(m[3]))
    m = _INSTANT.match(s)
    if m:
        (an, mo, jo, h, mi, se, _frac, z, signe, oh, om) = m.groups()
        if signe:
            try:
                tz = timezone((1 if signe == "+" else -1)
                              * timedelta(hours=int(oh), minutes=int(om or 0)))
            except ValueError:
                return None
        else:
            tz = timezone.utc
        try:
            t = datetime(int(an), int(mo), int(jo), int(h), int(mi), int(se or 0),
                         tzinfo=tz)
        except ValueError:
            return None
        return _instant(t, ftype, suppose_utc=not (z or signe))
    m = _FRANCAIS.match(s)
    if m:
        jo, mo, an, h, mi, se = m.groups()
        if h is None:
            return _periode(int(an), int(mo), int(jo))
        try:
            t = datetime(int(an), int(mo), int(jo), int(h), int(mi), int(se or 0),
                         tzinfo=timezone.utc)
        except ValueError:
            return None
        return _instant(t, ftype, suppose_utc=True)
    return None


def est_lisible(valeur: Any, ftype: str) -> bool:
    return lire(valeur, ftype) is not None


# --------------------------------------------------------------------------- #
# L'écriture : normaliser une ligne contre sa déclaration
# --------------------------------------------------------------------------- #

def _notice_utc(chemin: str) -> str:
    return (f"`{chemin}` : un instant sans fuseau a été lu en UTC et stocké "
            f"`AAAA-MM-JJTHH:MM:SSZ`. S'il était en heure locale, réécris-le avec son "
            f"fuseau (`2026-09-04T10:00:00+02:00`).")


def _notice_illisible(chemin: str, ftype: str) -> str:
    return (f"`{chemin}` est déclarée `{ftype}` et a reçu une valeur illisible comme "
            f"date : elle est gardée telle quelle (tableau sans validation), mais elle "
            f"ne se trie ni ne se filtre comme une date. Formes lues : "
            f"{FORMES_ACCEPTEES}.")


class _Releve:
    def __init__(self) -> None:
        self.suppose_utc: set = set()
        self.illisibles: dict = {}

    def notices(self, souple: bool) -> set:
        out = {_notice_utc(c) for c in self.suppose_utc}
        if souple:
            out |= {_notice_illisible(c, t) for c, t in self.illisibles.items()}
        return out


def _sur_la_valeur(valeur: Any, fn: Callable[[Any], Any]) -> Any:
    """Applique `fn` à la VALEUR d'une case, qu'elle porte des couches ou non. Les
    couches (`comment`, `link`, `origine`) ne bougent pas : l'origine est la donnée
    TELLE QUE REMISE, on ne la réécrit jamais."""
    if isinstance(valeur, dict) and VALUE_LAYER in valeur:
        nouvelle = fn(valeur[VALUE_LAYER])
        if nouvelle is valeur[VALUE_LAYER]:
            return valeur
        return {**valeur, VALUE_LAYER: nouvelle}
    return fn(valeur)


#: Ce que le parcours d'une ligne appelle sur chaque case de date :
#: `(valeur, type, chemin) -> nouvelle valeur` (la même, si rien ne change).
SurUneDate = Callable[[Any, str, str], Any]


def _composite(valeur: Any, champs: list, chemin: str, fn: SurUneDate) -> Any:
    if not isinstance(valeur, dict):
        return valeur
    out = valeur
    for f in champs:
        cle = f.get("key") if isinstance(f, dict) else None
        if not isinstance(cle, str) or cle not in valeur:
            continue
        nouvelle = _valeur(valeur[cle], f, f"{chemin}.{cle}", fn)
        if nouvelle is not valeur[cle]:
            if out is valeur:
                out = dict(valeur)
            out[cle] = nouvelle
    return out


def _valeur(valeur: Any, decl: dict, chemin: str, fn: SurUneDate) -> Any:
    """Une case contre SA déclaration — récursive sur `object.fields`, `list.of`."""
    ftype = decl.get("type")
    if ftype in TYPES_DATES:
        return _sur_la_valeur(valeur, lambda v: fn(v, ftype, chemin))
    if ftype == "object" and isinstance(decl.get("fields"), list):
        return _sur_la_valeur(
            valeur, lambda v: _composite(v, decl["fields"], chemin, fn))
    if ftype == "list" and isinstance(decl.get("of"), dict):
        of = decl["of"]

        def _liste(v: Any) -> Any:
            if not isinstance(v, list):
                return v
            if isinstance(of.get("fields"), list):
                items = [_composite(it, of["fields"], f"{chemin}[]", fn) for it in v]
            elif of.get("type") in TYPES_DATES:
                items = [_sur_la_valeur(it, lambda x: fn(x, of["type"], f"{chemin}[]"))
                         for it in v]
            else:
                return v
            return v if all(a is b for a, b in zip(items, v)) else items
        return _sur_la_valeur(valeur, _liste)
    return valeur


def parcourir_ligne(schema: Optional[dict], data: Any, fn: SurUneDate) -> Any:
    """Applique `fn` à chaque case DÉCLARÉE `date`/`datetime`, à tout niveau qu'une
    déclaration atteint (`object.fields`, `list.of.fields`, `list.of.type`), la valeur
    déballée de ses couches. Ne mute rien : rend une copie dès qu'une case change,
    l'entrée sinon. Le seul parcours — l'écriture et le script de reprise le partagent,
    chacun avec sa règle par case."""
    if not isinstance(data, dict):
        return data
    out = data
    for f in _fields(schema):
        cle = f.get("key") if isinstance(f, dict) else None
        if not isinstance(cle, str) or cle not in data:
            continue
        nouvelle = _valeur(data[cle], f, cle, fn)
        if nouvelle is not data[cle]:
            if out is data:
                out = dict(data)
            out[cle] = nouvelle
    return out


def normaliser_ligne(schema: Optional[dict], data: dict) -> tuple[dict, set]:
    """`(données normalisées, notices)` — LA normalisation d'écriture des dates.

    Ne touche que les colonnes DÉCLARÉES `date`/`datetime` (`parcourir_ligne`).
    Idempotente — une forme stockée se relit à l'identique, ce qui permet à deux
    chemins d'écriture successifs (append promu en fusion) de passer deux fois sans
    rien dire de plus.

    Une valeur illisible n'est jamais réécrite : elle reste telle quelle, et c'est la
    validation qui la refuse (`types_declares` arme le type au premier niveau ; les
    sous-champs se jugent sous `validation_active`). Sur un tableau sans validation,
    elle est signalée dans les notices — gardée, jamais en silence."""
    if not isinstance(data, dict):
        return data, set()
    releve = _Releve()

    def _case(valeur: Any, ftype: str, chemin: str) -> Any:
        if valeur is None or valeur == "":
            return valeur
        lue = lire(valeur, ftype)
        if lue is None:
            # `@empty` se résout plus loin (`@keep`/`@clear` sont refusés avant) : ce
            # ne sont pas des dates.
            if not (isinstance(valeur, str) and valeur.startswith("@")):
                releve.illisibles.setdefault(chemin, ftype)
            return valeur
        if lue.suppose_utc:
            releve.suppose_utc.add(chemin)
        return valeur if lue.forme == valeur else lue.forme

    out = parcourir_ligne(schema, data, _case)
    return out, releve.notices(souple=not validation_active(schema))


# --------------------------------------------------------------------------- #
# La lecture : typer les filtres d'une colonne date
# --------------------------------------------------------------------------- #

#: Les opérateurs qui COMPARENT une date. `contains` reste textuel (`2026-09` y trouve
#: le mois écrit), `empty`/`not_empty` ne comparent rien.
OPS_DATES = ("eq", "ne", "in", "gt", "gte", "lt", "lte")


def bornes_du_filtre(valeur: Any, champ: str) -> list:
    """La BORNE d'un filtre `[debut, fin]` en ISO UTC — sa période entière.

    Une borne IMPRÉCISE couvre sa période : `lte 2026-09-04` va jusqu'à la fin du 4
    (`< 2026-09-05T00:00:00Z`), `gt 2026-09` commence au 1ᵉʳ octobre, `eq 2026` est
    l'année entière. Un instant est un point (`debut == fin`). La valeur stockée, elle,
    est lue à son DÉBUT de période : `eq 2026-09` retrouve `2026-09-04T10:00:00Z`,
    `2026-09-15` et `2026-09` ; `eq 2026-09-04` ne retrouve pas `2026-09` (qui commence
    le 1ᵉʳ). Illisible ⟹ refus nommé : un filtre qui comparerait du texte rendrait un
    résultat faux sans erreur."""
    lue = lire(valeur, "datetime")
    if lue is None:
        raise ValueError(
            f"filtre sur `{champ}` (colonne de dates) : {valeur!r} n'est pas une date "
            f"lisible — attendu {FORMES_ACCEPTEES}.")
    return [_iso_utc(lue.debut), _iso_utc(lue.fin)]


def typer_les_clauses(clauses: list, schema_of: Callable[[], Optional[dict]]) -> list:
    """Les clauses de filtre, celles qui COMPARENT une colonne date annotées de leurs
    bornes (`dates` = les colonnes visées qui sont des dates, `bornes` = une période par
    valeur). Le moteur SQL (`db.query`) compare alors des instants, pas du texte.

    Premier niveau seulement : une colonne nommée par son nom nu, seule ou dans
    `fields`. Un attribut d'élément de liste (`col[].sous`) reste textuel. Le schéma
    n'est lu que si une clause compare."""
    if not any(isinstance(c, dict) and c.get("op") in OPS_DATES for c in clauses or []):
        return clauses
    schema = schema_of()
    out = []
    for c in clauses:
        if not isinstance(c, dict) or c.get("op") not in OPS_DATES:
            out.append(c)
            continue
        cibles = c.get("fields") if c.get("fields") is not None else [c.get("field")]
        dates = [k for k in (cibles if isinstance(cibles, (list, tuple)) else [])
                 if isinstance(k, str)
                 and (champ_declare(schema, k) or {}).get("type") in TYPES_DATES]
        if not dates:
            out.append(c)
            continue
        valeurs = c.get("value") if c["op"] == "in" else [c.get("value")]
        if not isinstance(valeurs, list):
            valeurs = [valeurs]
        valeurs = [v for v in valeurs if v is not None and str(v) != ""]
        if c["op"] != "in" and not valeurs:
            out.append(c)   # `eq null` : le refus existant (`_ds_text`) parle
            continue
        out.append({**c, "dates": dates,
                    "bornes": [bornes_du_filtre(v, dates[0]) for v in valeurs]})
    return out
