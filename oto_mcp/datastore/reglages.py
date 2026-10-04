"""Les DEUX réglages de tête d'un tableau — un par axe (oto#127, 02/10/2026).

Trois réglages gouvernaient un tableau, et aucun ne se comprenait seul (« Je ne
comprends pas ce que sont ces choses », 07/09) : `strict`, `unknown_fields` et
`key_required`. Mesuré sur le parc : 18 tableaux écrivaient `strict: false` et 10
`key_required: false` — on ne réécrit un défaut à sa propre valeur que lorsqu'on
ignore ce qu'il est. Ce n'étaient pas trois réglages, c'étaient DEUX axes :

- **`unknown_columns`** — le sort d'une colonne que le schéma ne déclare pas :
  `"create"` (défaut : elle est créée, en silence), `"report"` (créée, et nommée dans
  `hors_schema`), `"reject"` (refusée, rien n'est écrit). Il remplace `strict` ET
  `unknown_fields`, dont le second n'était que le troisième cran du premier ;
- **`new_rows`** — le droit d'une ligne nouvelle de naître : `"create"` (défaut) ou
  `"reject"` (une écriture qui ne désigne aucune ligne existante est refusée). Il
  remplace `key_required`, qui ne jugeait aucun format de colonne.

Les VALEURS sont des verbes, les mêmes sur les deux axes : ce que la plateforme FAIT
de la chose inconnue (créer, signaler, refuser) — `report` et `reject` sont ceux que
`unknown_fields` employait déjà. Les NOMS disent la chose jugée, au pluriel comme
`fields` : une colonne inconnue, une ligne nouvelle. `columns` plutôt que `fields`,
parce que l'axe juge le premier niveau et que `unknown_fields` est aussi le code du
400 qui refuse un PARAMÈTRE d'appel inconnu — deux sens sous un même nom.

## Ce que `strict` faisait d'autre, et où c'est allé

`strict` portait, outre le relevé hors schéma, trois effets acquis en huit semaines.
Aucun n'est devenu sans objet (relevé du 02/10 sur le code), tous jugent la même
question — « le format déclaré fait-il contrat ? » — et suivent donc l'axe des
colonnes : ils s'arment dès que `unknown_columns` n'est pas `"create"`
(`format_contraignant`).

- **armer la validation** (`declaration.validation_active`) : `required`, les bornes,
  le motif, `max_items` et le type s'arment désormais seuls (J4, 24/09 ; types
  08/09) — mais les `options` d'une colonne de premier niveau, la structure des
  types non armés (`url`, sous-records) et les couches inconnues d'une valeur ne
  sont jugées QUE sous validation armée (181 tableaux portent des `options` sans
  les faire respecter, 08/09) ;
- **fermer les sous-records déclarés** (`validation.validate_row`, #544) ;
- **les gardes de la pose** : la colonne du périmètre de réservation doit être
  déclarée (`claimable.erreurs`), les colonnes orphelines sont signalées
  (`_orphan_columns_warning`).

La seule exigence retirée est celle qui n'avait plus de sens : « `unknown_fields:
"reject"` exige `strict` » — c'est désormais un seul réglage.

Module PUR, sans import du paquet : `declaration` le lit, il ne lit rien.
"""
from __future__ import annotations

from typing import Any, Optional

UNKNOWN_COLUMNS = "unknown_columns"
NEW_ROWS = "new_rows"

CREATE, REPORT, REJECT = "create", "report", "reject"
#: Les crans de chaque axe, dans l'ordre où ils durcissent. Le défaut est le premier.
MODES = {UNKNOWN_COLUMNS: (CREATE, REPORT, REJECT), NEW_ROWS: (CREATE, REJECT)}
DEFAUT = CREATE

#: Les trois anciens réglages, et l'axe que chacun nourrit.
ANCIENS = {"strict": UNKNOWN_COLUMNS, "unknown_fields": UNKNOWN_COLUMNS,
           "key_required": NEW_ROWS}


def _colonnes_declarees(schema: dict) -> bool:
    champs = schema.get("fields")
    return isinstance(champs, list) and any(
        isinstance(f, dict) and isinstance(f.get("key"), str) and f["key"]
        for f in champs)


def _cle_metier(schema: dict) -> bool:
    cle = schema.get("key")
    return isinstance(cle, str) and bool(cle)


def traduire(tete: Any) -> dict:
    """Les nouveaux réglages ÉQUIVALENTS à ce que les anciens faisaient — seulement
    pour l'axe dont un ancien réglage est présent. `{}` si aucun ne l'est.

    L'équivalence est celle du COMPORTEMENT, pas de l'intention : un cran que
    l'ancien code laissait inerte se traduit par ce qu'il faisait, c'est-à-dire rien.

    | `strict` | `unknown_fields` | → `unknown_columns` |
    |---|---|---|
    | absent / faux | (indifférent — `reject` y était inerte) | `create` |
    | vrai | absent / `report` / illisible | `report` |
    | vrai | `reject`, au moins une colonne déclarée | `reject` |
    | vrai | `reject`, aucune colonne déclarée (inerte) | `report` |

    | `key_required` | → `new_rows` |
    |---|---|
    | vrai, `key` déclarée | `reject` |
    | faux, ou sans `key` (inerte) | `create` |
    """
    if not isinstance(tete, dict):
        return {}
    out: dict = {}
    if "strict" in tete or "unknown_fields" in tete:
        if not tete.get("strict"):
            out[UNKNOWN_COLUMNS] = CREATE
        elif tete.get("unknown_fields") == REJECT and _colonnes_declarees(tete):
            out[UNKNOWN_COLUMNS] = REJECT
        else:
            out[UNKNOWN_COLUMNS] = REPORT
    if "key_required" in tete:
        out[NEW_ROWS] = (REJECT if tete.get("key_required") and _cle_metier(tete)
                         else CREATE)
    return out


def _lu(schema: Any, axe: str) -> str:
    """La valeur APPLIQUÉE sur un axe. Une valeur illisible rend le défaut : elle est
    refusée à la pose (`erreurs`), donc elle ne peut venir que d'une écriture hors
    surface — et dans le doute on ne durcit pas un tableau vivant sur une faute de
    frappe (même parti que l'ancien `unknown_fields_mode`).

    ⚠️ TRANSITOIRE (le temps de `scripts/renommer_reglages_tete.py`) : un schéma qui
    ne porte pas encore le nouveau réglage est lu par la traduction de l'ancien, pour
    qu'aucun tableau ne change de comportement entre ce déploiement et le passage du
    script. Le commit suivant retire cette lecture."""
    if not isinstance(schema, dict):
        return DEFAUT
    if axe in schema:
        v = schema[axe]
    else:
        v = traduire(schema).get(axe, DEFAUT)
    return v if v in MODES[axe] else DEFAUT


def colonnes_inconnues(schema: Any) -> str:
    """`create`, `report` ou `reject` — le sort appliqué à une colonne non déclarée."""
    return _lu(schema, UNKNOWN_COLUMNS)


def format_contraignant(schema: Any) -> bool:
    """Le format déclaré fait-il CONTRAT ? — vrai dès que `unknown_columns` n'est pas
    `create`. C'est ce qui arme la validation entière, ferme les sous-records déclarés
    et arme les gardes de la pose qui en dépendent (cf. l'en-tête du module)."""
    return colonnes_inconnues(schema) != CREATE


def lignes_nouvelles(schema: Any) -> str:
    """`create` ou `reject` — le sort d'une écriture qui ne désigne aucune ligne.

    ⚠️ Sans `key` déclarée, `reject` ne s'arme pas : la combinaison est refusée à la
    pose, mais un schéma déjà en base qui la porterait rendrait le tableau
    inécrivable — un vieux schéma ne doit pas faire exploser une écriture."""
    v = _lu(schema, NEW_ROWS)
    return v if v == CREATE or _cle_metier(schema) else CREATE


def effectifs(schema: Any) -> dict:
    """Les deux réglages tels que la plateforme les APPLIQUE — servis à la lecture."""
    return {UNKNOWN_COLUMNS: colonnes_inconnues(schema),
            NEW_ROWS: lignes_nouvelles(schema)}


def _crans(axe: str) -> str:
    return " | ".join(f"\"{m}\"" for m in MODES[axe])


def erreurs(schema: dict) -> list[str]:
    """Les refus à la POSE des deux réglages : la valeur, et les deux combinaisons où
    le cran serait inerte ou rendrait le tableau inécrivable."""
    errs: list[str] = []
    for axe in (UNKNOWN_COLUMNS, NEW_ROWS):
        if axe in schema and schema[axe] not in MODES[axe]:
            errs.append(f"{axe}: valeurs possibles {_crans(axe)} ; reçu {schema[axe]!r}")
    anciens = sorted(k for k in ANCIENS if k in schema)
    for axe in (UNKNOWN_COLUMNS, NEW_ROWS):
        meles = [k for k in anciens if ANCIENS[k] == axe]
        if axe in schema and meles:
            errs.append(
                f"{axe} et {', '.join(f'`{k}`' for k in meles)} décident du même axe — "
                f"{', '.join(f'`{k}`' for k in meles)} est remplacé par `{axe}` : "
                f"retire-le")
    if schema.get(UNKNOWN_COLUMNS) == REJECT and not _colonnes_declarees(schema):
        errs.append(
            "unknown_columns: \"reject\" exige au moins une colonne déclarée — sans "
            "référentiel, TOUTE colonne est hors schéma et le tableau devient "
            "inécrivable dès la pose. Déclare le format d'abord, ferme-le ensuite")
    if schema.get(NEW_ROWS) == REJECT and not _cle_metier(schema):
        errs.append(
            "new_rows: \"reject\" exige une clé métier : déclare `key` (la colonne qui "
            "identifie une ligne), sinon aucune écriture ne pourrait viser une ligne "
            "existante et le tableau serait inécrivable")
    return errs


def phrase_equivalent(tete: Optional[dict]) -> str:
    """`unknown_columns: "report"` et `new_rows: "reject"` — l'équivalent exact, écrit
    comme il se pose."""
    t = traduire(tete or {})
    return " et ".join(f"`{axe}: \"{v}\"`" for axe, v in t.items())
