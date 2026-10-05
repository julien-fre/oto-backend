"""Le réglage de tête d'un tableau — UN seul encore : `new_rows` (oto#127, oto#124).

Trois réglages gouvernaient un tableau (`strict`, `unknown_fields`, `key_required`),
remplacés le 02/10/2026 par deux, un par axe (oto#127) :

- **`new_rows`** — le droit d'une ligne nouvelle de naître : `"create"` (défaut) ou
  `"reject"` (une écriture qui ne désigne aucune ligne existante est refusée). Il
  remplace `key_required`. C'est le seul réglage de tête qui reste ;
- **`unknown_columns`** — `create` | `report` | `reject`, le sort d'une colonne non
  déclarée et, hors `create`, la validation COMPLÈTE du format. **RETIRÉ le
  05/10/2026 (oto#124) : plus aucun réglage, les colonnes et les valeurs sont toujours
  vérifiées.** Une colonne non déclarée est refusée sur tous les tableaux
  (`colonnes_non_declarees`) et le format déclaré fait contrat partout
  (`validation_complete`), tous deux à partir du 21/10/2026, avec un préavis d'ici là.

⚠️ **La seule lecture qui reste d'`unknown_columns` est transitoire.** Refusé à la pose
et au patch (`schema_keys.CLES_RETIREES`, `refus_parametres`), il reste STOCKÉ sur une
centaine de tableaux jusqu'au passage de `scripts/retirer_unknown_columns.py`. D'ici
au 21/10, `format_contraignant` le lit encore — pour que ces tableaux gardent la
validation complète qu'ils avaient, au lieu de retomber au préavis — et
`colonnes_inconnues` garde le relevé `report` et le refus `reject` de la colonne non
déclarée. À partir de la date, `validation_complete.complete` ne le consulte plus, et
la colonne non déclarée est refusée avant que le relevé ou le cran `reject` ne la
voient : le script se lance APRÈS la date, puis ces lectures se retirent.

Les VALEURS sont des verbes : ce que la plateforme FAIT de la chose inconnue.

Les anciens noms (`strict`, `unknown_fields`, `key_required`) sont REFUSÉS à la pose
et au patch, avec ce qui les remplace (`refus_anciens`, `refus_parametres`).

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
    """Le réglage ÉQUIVALENT à ce que les anciens faisaient — `{}` si aucun ne s'en
    traduit. Seul `key_required` a encore un équivalent : `strict` et `unknown_fields`
    n'en ont plus, leur axe a disparu (oto#124 — toujours vérifier).

    | `key_required` | → `new_rows` |
    |---|---|
    | vrai, `key` déclarée | `reject` |
    | faux, ou sans `key` (inerte) | `create` |
    """
    if not isinstance(tete, dict) or "key_required" not in tete:
        return {}
    return {NEW_ROWS: (REJECT if tete.get("key_required") and _cle_metier(tete)
                       else CREATE)}


def _lu(schema: Any, axe: str) -> str:
    """La valeur APPLIQUÉE sur un axe. Une valeur illisible rend le défaut : elle est
    refusée à la pose (`erreurs`), donc elle ne peut venir que d'une écriture hors
    surface — et dans le doute on ne durcit pas un tableau vivant sur une faute de
    frappe."""
    if not isinstance(schema, dict):
        return DEFAUT
    v = schema.get(axe, DEFAUT)
    return v if v in MODES[axe] else DEFAUT


def colonnes_inconnues(schema: Any) -> str:
    """TRANSITOIRE (oto#124) — le cran `unknown_columns` STOCKÉ, d'ici au retrait du
    stocké : `report` garde le relevé `hors_schema`, `reject` le refus de la colonne
    non déclarée, jusqu'au 21/10/2026 (`hors_schema`). Après la date, la colonne non
    déclarée est refusée avant eux."""
    return _lu(schema, UNKNOWN_COLUMNS)


def format_contraignant(schema: Any) -> bool:
    """TRANSITOIRE (oto#124) — le réglage STOCKÉ mettait-il le format sous contrat ?
    Lu par `validation_complete.complete` jusqu'au 21/10/2026 seulement, pour que les
    tableaux réglés `report`/`reject` gardent la validation complète d'ici là ; à
    partir de la date, elle s'applique à tous et ce réglage n'est plus consulté."""
    return colonnes_inconnues(schema) != CREATE


def lignes_nouvelles(schema: Any) -> str:
    """`create` ou `reject` — le sort d'une écriture qui ne désigne aucune ligne.

    ⚠️ Sans `key` déclarée, `reject` ne s'arme pas : la combinaison est refusée à la
    pose, mais un schéma déjà en base qui la porterait rendrait le tableau
    inécrivable — un vieux schéma ne doit pas faire exploser une écriture."""
    v = _lu(schema, NEW_ROWS)
    return v if v == CREATE or _cle_metier(schema) else CREATE


def effectifs(schema: Any) -> dict:
    """Le réglage de tête tel que la plateforme l'APPLIQUE — servi à la lecture."""
    return {NEW_ROWS: lignes_nouvelles(schema)}


def _crans(axe: str) -> str:
    return " | ".join(f"\"{m}\"" for m in MODES[axe])


def erreurs(schema: dict) -> list[str]:
    """Les refus à la POSE du réglage : la valeur, et la combinaison qui rendrait le
    tableau inécrivable. (`unknown_columns` est refusé par le vocabulaire,
    `schema_keys.CLES_RETIREES`.)"""
    errs: list[str] = []
    if NEW_ROWS in schema and schema[NEW_ROWS] not in MODES[NEW_ROWS]:
        errs.append(f"{NEW_ROWS}: valeurs possibles {_crans(NEW_ROWS)} ; "
                    f"reçu {schema[NEW_ROWS]!r}")
    if schema.get(NEW_ROWS) == REJECT and not _cle_metier(schema):
        errs.append(
            "new_rows: \"reject\" exige une clé métier : déclare `key` (la colonne qui "
            "identifie une ligne), sinon aucune écriture ne pourrait viser une ligne "
            "existante et le tableau serait inécrivable")
    return errs


#: Le jour de la bascule des anciens noms, dit dans chaque refus.
REMPLACES_LE = "02/10/2026"
#: Le jour où `unknown_columns` a été retiré (oto#124).
RETIRE_LE = "05/10/2026"

#: La phrase qui dit pourquoi il n'y a plus de réglage — la même partout.
PLUS_AUCUN_REGLAGE = ("plus aucun réglage : les colonnes et les valeurs sont toujours "
                      "vérifiées — une colonne non déclarée est refusée, le format "
                      "déclaré (options, forme des valeurs, sous-records) fait contrat "
                      "sur tous les tableaux")


def refus_unknown_columns() -> str:
    """Le refus d'`unknown_columns` posé ou modifié (tête d'un schéma)."""
    return (f"`unknown_columns` a été retiré le {RETIRE_LE} — {PLUS_AUCUN_REGLAGE}. "
            f"Rien n'a été posé : retire-le du schéma. Pour écrire une colonne nouvelle, "
            f"déclare-la (`fields`) ; pour accepter une valeur, étends ses `options`.")


def _inertes(tete: dict) -> list[str]:
    """Les crans que l'ancien code laissait sans effet dans cette combinaison."""
    if tete.get("key_required") and not _cle_metier(tete):
        return ["`key_required` sans `key` ne fermait rien"]
    return []


def refus_anciens(tete: dict, poses: list) -> str:
    """Le refus des anciens réglages posés en TÊTE d'un schéma, avec ce qui les
    remplace, calculé sur la combinaison reçue."""
    noms = ", ".join(f"`{k}`" for k in poses)
    bouts = []
    if any(k in poses for k in ("strict", "unknown_fields")):
        bouts.append(f"`strict` et `unknown_fields` n'ont plus d'équivalent — "
                     f"{PLUS_AUCUN_REGLAGE} (`unknown_columns`, qui les avait "
                     f"remplacés, a été retiré le {RETIRE_LE}) : retire-les")
    if "key_required" in poses:
        equivalent = traduire(tete)[NEW_ROWS]
        inertes = _inertes(tete)
        bouts.append(f"`key_required` se dit `\"{NEW_ROWS}\": \"{equivalent}\"` (le "
                     f"droit d'une ligne nouvelle de naître : {_crans(NEW_ROWS)})"
                     + (f" ({' ; '.join(inertes)} : l'équivalent est donc ce qu'il "
                        f"faisait)" if inertes else ""))
    return (f"tête : {noms} {'a' if len(poses) == 1 else 'ont'} été "
            f"remplacé{'' if len(poses) == 1 else 's'} le {REMPLACES_LE}. Rien n'a été "
            f"posé. " + " ; ".join(bouts) + ".")


def residu(tete: dict) -> str:
    """Ce qu'un ancien réglage encore STOCKÉ voulait dire — pour l'avertissement de
    lecture : il n'est plus appliqué."""
    out = (f"les anciens réglages de tête ne sont plus lus depuis le {REMPLACES_LE}")
    equivalent = traduire(tete)
    if equivalent:
        out += (f" ; `key_required` se dit `{NEW_ROWS}: \"{equivalent[NEW_ROWS]}\"` — "
                f"pose-le (`data_patch_schema`)")
    return out + " ; retire-les (`data_set_schema`)"


def residu_unknown_columns(valeur: Any) -> str:
    """Ce qu'`unknown_columns` encore STOCKÉ fait d'ici à son retrait (oto#124)."""
    return (f"`unknown_columns: {valeur!r}` (tête) a été retiré le {RETIRE_LE} — "
            f"{PLUS_AUCUN_REGLAGE}. Il reste stocké jusqu'à son retrait par la "
            f"plateforme, sans geste de ta part ; le modifier est refusé")


#: Les PARAMÈTRES retirés de `data_patch_schema`.
PARAMETRES_PATCH = (*ANCIENS, UNKNOWN_COLUMNS)


def refus_parametres(valeurs: dict, outil: str = "data_patch_schema") -> Optional[str]:
    """Le refus des paramètres retirés de `data_patch_schema`, ou `None` s'il n'y en a
    pas. `key_required` a son équivalent (`new_rows`) ; `strict`, `unknown_fields` et
    `unknown_columns` n'en ont plus (oto#124)."""
    recus = [k for k in PARAMETRES_PATCH if k in valeurs]
    if not recus:
        return None
    noms = ", ".join(f"`{k}`" for k in recus)
    bouts = []
    sans = [k for k in recus if k != "key_required"]
    if sans:
        bouts.append(f"{', '.join(f'`{k}`' for k in sans)} : retiré"
                     f"{'' if len(sans) == 1 else 's'} — {PLUS_AUCUN_REGLAGE} ; "
                     f"rejoue `{outil}` sans {'lui' if len(sans) == 1 else 'eux'}")
    if "key_required" in valeurs:
        v = REJECT if valeurs["key_required"] else CREATE
        bouts.append(f"`key_required` a été remplacé le {REMPLACES_LE} par `new_rows` "
                     f"(le droit d'une ligne nouvelle de naître : {_crans(NEW_ROWS)}) — "
                     f"rejoue `{outil}` avec {NEW_ROWS}=\"{v}\"")
    return (f"{noms} : rien n'a été écrit. " + " ; ".join(bouts) + ".")
