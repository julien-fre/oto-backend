"""PayFit — les ENVELOPPES de sortie et la seule clé que ce connecteur renomme.

Séparé de `payfit.py` et de ses frères pour qu'une seule partie décide de la FORME
de ce qui sort : la pagination, la projection `fields`, et le sort du type d'absence.

## Ce qui a changé le 17/09/2026, et pourquoi

Ce module portait une **liste blanche** : seuls quelques champs nommés sortaient de
l'entreprise, du collaborateur, du contrat, de l'absence. Le retrait était EN DUR —
personne ne pouvait l'ouvrir, pas même l'entreprise propriétaire de ses propres
données de paie. Décision d'Alexis (signal d'usage #1063) : **on sert tout ce que
l'API expose**, et la protection passe désormais par les **filtres de champs par
org** (ADR 0009/0015), avec des **défauts serveur protecteurs** posés dans
`field_filter_defaults.SERVER_DEFAULTS["payfit"]` — qu'un org_admin peut lever,
connecteur par connecteur.

Conséquence directe : il n'y a plus de `_pick`. Une réponse sort telle que l'API la
livre, et ce qui la réduit est (1) le scope de la clé PayFit, (2) la politique de
rédaction de l'org, (3) `fields` quand l'appelant veut moins de tokens.

## La clé renommée, et pourquoi c'est nécessaire ici

⚠️ **`FieldFilter` matche par NOM DE CLÉ FEUILLE, à toute profondeur.** Une règle
sur `type` toucherait donc AUSSI `emails[].type`, `phoneNumbers[].type`,
`addresses[].type`, `analyticCodes[].type` et `documents[].type` — cinq champs
anodins corrompus pour en protéger un. Le mécanisme ne sait pas dire « `type`, mais
seulement sous `absences` » : c'est sa limite, pas un oubli de configuration.

Le type d'une absence est donc servi sous **`absence_type`**, un nom de feuille qui
n'appartient qu'à lui — et c'est CE nom que le défaut serveur masque. La clé `type`
de l'amont n'est pas servie : deux noms pour la même donnée, l'un filtré et l'autre
non, serait une passoire.

`absence_category` l'accompagne, calculé ici : `ordinary_leave` pour un congé
ordinaire, `restricted` pour tout le reste. Il reste lisible quand `absence_type`
est masqué — un pilotage de charge a besoin de savoir qu'une personne est absente et
que ce n'est pas un congé payé, sans lire un motif médical. Il ne nomme JAMAIS la
santé : `restricted` couvre aussi bien un arrêt maladie qu'un mariage.
"""
from __future__ import annotations

from typing import Any, Callable, Optional

from .. import output_projection

# Ce que le DÉFAUT SERVEUR masque sur ce connecteur, dit à l'agent pour qu'il ne
# prenne pas un `••••` pour une donnée absente — et dit à l'org comment le lever.
REDACTION = (
    "défaut serveur de rédaction : NIR (et NTT), IBAN/BIC et `absence_type` sont "
    "masqués. Ce n'est pas une absence de donnée — un org_admin lève la règle pour "
    "ce connecteur (dashboard, ou `oto_org_settings domain=field_filters "
    "service=payfit`). `absence_category` reste lisible dans tous les cas.")

# Les congés ORDINAIRES : ceux qui ne disent rien de la santé ni de la vie familiale.
# Tout AUTRE type — maladie, accident du travail, maternité, enfant malade, deuil,
# mariage, ou un type ajouté demain — tombe en `restricted`. La liste est fermée
# côté sûreté : un type inconnu n'est jamais « ordinaire ».
ORDINARY_ABSENCE_TYPES = frozenset({
    "fr_conges_payes", "fr_rtt", "fr_repos", "fr_sans_solde", "fr_teletravail",
    "fr_ecole", "fr_absence_remuneree", "uk_annual_leave", "uk_paid_leave",
    "uk_unpaid_leave", "uk_remote", "es_vacaciones", "es_teletrabajo",
    "es_compensacion_dias_trabajados",
})
ORDINARY = "ordinary_leave"
RESTRICTED = "restricted"


def absence(a: Any) -> Any:
    """Une absence, telle que l'API la livre, sauf `type` → `absence_type` +
    `absence_category` (cf. docstring du module)."""
    if not isinstance(a, dict):
        return a
    out = {k: v for k, v in a.items() if k != "type"}
    if "type" in a:
        out["absence_type"] = a["type"]
        out["absence_category"] = (
            ORDINARY if a["type"] in ORDINARY_ABSENCE_TYPES else RESTRICTED)
    return out


def page(env: Any, key: str, id_key: str, *, fields: Optional[list] = None,
         shape: Optional[Callable[[Any], Any]] = None,
         redaction: Optional[str] = None) -> dict:
    """Une page de liste : `{count, next_cursor, <key>: [...]}`.

    `fields` ne peut que RETIRER, et `id_key` est toujours gardé : c'est une
    économie de tokens, jamais un pouvoir de lecture — il n'y a plus rien à ouvrir
    par ce chemin, tout est déjà servi. `["*"]` rend la vue complète.
    """
    env = env if isinstance(env, dict) else {}
    meta = env.get("meta") if isinstance(env.get("meta"), dict) else {}
    rows = env.get(key) or []
    if shape is not None:
        rows = [shape(r) for r in rows]
    out = {"count": meta.get("count"), "next_cursor": meta.get("nextPageToken") or None,
           key: rows}
    if fields is not None and output_projection.RAW not in fields:
        out = output_projection.project(out, items_path=key,
                                        fields=set(fields) | {id_key})
    if redaction:
        out["redaction"] = redaction
    return out


def rows(items: Any, key: str, id_key: str, *, fields: Optional[list] = None,
         shape: Optional[Callable[[Any], Any]] = None,
         redaction: Optional[str] = None) -> dict:
    """Une liste NON paginée servie sous `key` — l'API en a plusieurs (écritures
    comptables, contrats de mutuelle, documents). Même projection, pas de curseur :
    inventer un `next_cursor: null` ferait croire à une pagination qui n'existe pas.
    """
    items = items if isinstance(items, list) else []
    if shape is not None:
        items = [shape(r) for r in items]
    out = {"count": len(items), key: items}
    if fields is not None and output_projection.RAW not in fields:
        out = output_projection.project(out, items_path=key,
                                        fields=set(fields) | {id_key})
    if redaction:
        out["redaction"] = redaction
    return out


def one(obj: Any, key: str, *, shape: Optional[Callable[[Any], Any]] = None,
        redaction: Optional[str] = None) -> dict:
    out = {key: shape(obj) if shape is not None else obj}
    if redaction:
        out["redaction"] = redaction
    return out
