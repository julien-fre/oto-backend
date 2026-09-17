"""PayFit — la projection en LISTE BLANCHE de tout ce qui sort du connecteur.

Séparée de `payfit.py` (outils) pour qu'une seule partie décide ce qui SORT d'une
entreprise, d'un collaborateur, d'un contrat ou d'une absence : la partie à relire
quand l'API change. Un champ absent d'une liste ci-dessous ne passe pas, y compris un
champ que l'API ajouterait demain ou qu'une clé aux scopes larges ferait apparaître.
Le pourquoi (données de paie, NIR, IBAN, santé) est dans la docstring de `payfit.py`.
"""
from __future__ import annotations

from typing import Any, Callable, Optional

from .. import output_projection

_WITHHELD = ("données personnelles retirées : NIR, IBAN/BIC, date et lieu de naissance, "
             "nationalité, sexe, adresses, téléphones et e-mails personnels, temps de "
             "travail, mutuelle et prévoyance, motif de rupture, rémunération ; le motif "
             "d'une absence n'est servi que s'il est un congé ordinaire (sinon `absence`).")

_COMPANY = ("id", "name", "country", "identificationNumber", "address", "city",
            "postalCode", "nbActiveContracts")
_COLLABORATOR = ("id", "firstName", "lastName", "secondLastName", "managerId",
                 "teamName", "terminationDate")
_COLLABORATOR_CONTRACT = ("id", "startDate", "endDate", "status")
_CONTRACT = ("contractId", "collaboratorId", "companyId", "jobName", "status",
             "startDate", "endDate")
# Variante FR : nature du contrat (CDI, CDD…), statut conventionnel, convention
# collective. PAS le motif de rupture (codes d'inaptitude), ni NIR, ni mutuelle.
_CONTRACT_FR = _CONTRACT + ("natureContratDsn", "statutConventionnelDsn", "idcc")
_ABSENCE = ("id", "contractId", "status")
_MOMENT = ("date", "moment")

# Types d'absence servis tels quels : des congés ordinaires qui ne disent rien de la
# santé ni de la vie familiale. Tout AUTRE type — maladie, accident du travail,
# maternité, enfant malade, deuil, type ajouté demain — sort en `absence`.
ORDINARY_ABSENCE_TYPES = frozenset({
    "fr_conges_payes", "fr_rtt", "fr_repos", "fr_sans_solde", "fr_teletravail",
    "fr_ecole", "uk_annual_leave", "uk_paid_leave", "uk_unpaid_leave", "uk_remote",
    "es_vacaciones", "es_teletrabajo", "es_compensacion_dias_trabajados",
})
GENERIC_ABSENCE = "absence"


def _pick(obj: Any, keys: tuple) -> Any:
    if not isinstance(obj, dict):
        return None
    return {k: obj[k] for k in keys if k in obj}


def company(c: Any) -> Any:
    return _pick(c, _COMPANY)


def collaborator(c: Any) -> Any:
    out = _pick(c, _COLLABORATOR)
    if out is None:
        return None
    if "emails" in c:
        # Seul l'e-mail déclaré professionnel : `personal` et `unknown` restent dehors.
        out["emails"] = [e["email"] for e in c.get("emails") or []
                         if isinstance(e, dict) and e.get("type") == "professional"
                         and isinstance(e.get("email"), str)]
    if "contracts" in c:
        out["contracts"] = [_pick(k, _COLLABORATOR_CONTRACT)
                            for k in c.get("contracts") or [] if isinstance(k, dict)]
    return out


def contract(c: Any) -> Any:
    return _pick(c, _CONTRACT)


def contract_fr(c: Any) -> Any:
    return _pick(c, _CONTRACT_FR)


def absence(a: Any) -> Any:
    out = _pick(a, _ABSENCE)
    if out is None:
        return None
    for key in ("startDate", "endDate"):
        if key in a:
            out[key] = _pick(a[key], _MOMENT)
    if "type" in a:
        out["type"] = a["type"] if a["type"] in ORDINARY_ABSENCE_TYPES else GENERIC_ABSENCE
    return out


def page(env: Any, key: str, shape: Callable[[Any], Any], id_key: str,
         fields: Optional[list] = None) -> dict:
    """Une page de liste : `{count, next_cursor, <key>: [...], withheld}`.

    ⚠️ `fields` s'applique APRÈS la liste blanche et ne peut que retirer : `["*"]`
    rend la vue par défaut, jamais le brut de l'amont (aucune échappatoire vers les
    données personnelles). `id_key` est toujours gardé."""
    env = env if isinstance(env, dict) else {}
    meta = env.get("meta") if isinstance(env.get("meta"), dict) else {}
    rows = [shape(r) for r in env.get(key) or [] if isinstance(r, dict)]
    out = {"count": meta.get("count"), "next_cursor": meta.get("nextPageToken") or None,
           key: rows}
    if fields is not None and output_projection.RAW not in fields:
        out = output_projection.project(out, items_path=key,
                                        fields=set(fields) | {id_key})
    out["withheld"] = _WITHHELD
    return out


def one(obj: Any, key: str, shape: Callable[[Any], Any]) -> dict:
    return {key: shape(obj), "withheld": _WITHHELD}
