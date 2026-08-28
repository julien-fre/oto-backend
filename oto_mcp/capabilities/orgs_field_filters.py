"""Capacités de redaction de champs par org (ADR 0009 + ADR 0015).

L'org_admin configure, par connecteur, comment les champs sensibles des réponses
sont redactés avant d'atteindre l'agent (masque, pseudonyme cohérent,
généralisation, hash, suppression). Décision « contrôle total org » : la politique
d'org est autoritaire ; sans politique, repli sur le défaut serveur
(`field_filter_defaults`). Lecture = membre ; écriture = org_admin.

Une déclaration → deux surfaces (MCP `oto_*` + REST `/api/orgs/{id}/field-filters`)
via les adaptateurs. Pattern de référence : `orgs_secrets.py`.
"""
from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, Field

from .. import db, field_filter_defaults, org_store
from ..connectors import field_schema as connector_field_schema
from ..connectors import schema_store as connector_schema_store
from ._authz import ORG_ADMIN_OF, ORG_MEMBER_OF
from ._types import AuthzDenied, Capability, ResolvedCtx, RestBinding

from .registry import CAPABILITIES

_ID = {"id": "org_id"}

# Actions reconnues par le moteur FieldFilter (oto-core). Rejette tout le reste à
# l'écriture (le moteur fail-safe en masque total, mais autant le dire à l'org).
_ACTIONS = {"mask", "drop", "remove", "pseudonym", "generalize", "hash", "anonymize"}

# Schéma des modes pour piloter le formulaire dashboard (action + sous-options).
_ACTION_SCHEMA = [
    {"action": "mask", "label": "Masquer", "params": [
        {"key": "preserve", "type": "select", "label": "Format préservé",
         "options": ["", "email", "phone", "iban"]},
        {"key": "keep_first", "type": "int", "label": "Garder N premiers"},
        {"key": "keep_last", "type": "int", "label": "Garder N derniers"},
    ]},
    {"action": "pseudonym", "label": "Pseudonyme cohérent", "params": [
        {"key": "kind", "type": "select", "label": "Type",
         "options": ["name", "first_name", "last_name", "email", "company",
                     "phone_number", "address"]},
    ]},
    {"action": "generalize", "label": "Généraliser", "params": [
        {"key": "to", "type": "select", "label": "Précision",
         "options": ["year", "month", "department", "range"]},
        {"key": "step", "type": "int", "label": "Pas (mode range)"},
    ]},
    {"action": "hash", "label": "Hacher (SHA-256)", "params": []},
    {"action": "anonymize", "label": "Anonymiser (person_…)", "params": []},
    {"action": "drop", "label": "Supprimer le champ", "params": []},
]


class FieldFiltersView(BaseModel):
    """Politique de redaction de l'org, par connecteur, + de quoi peindre le
    formulaire. Trois pièges de lecture :

    ⚠️ **`filters: {}` veut dire « rien n'est masqué »**, pas « pas encore
    configuré » : la redaction est OPT-IN et `defaults` (`SERVER_DEFAULTS`) est
    **vide par décision** — aucun champ n'est redacté tant qu'une règle d'org ne le
    demande. Ne jamais afficher un état vide comme « protégé par défaut ».

    ⚠️ **`schema` (singulier) et `schemas` (pluriel) n'ont aucun rapport.** `schema`
    = la spec du FORMULAIRE (actions disponibles et leurs sous-options), toujours
    présent ; `schemas` = le catalogue des champs par connecteur, ~160 KB, **absent
    de l'objet** sauf `include_schemas=true` (il dépasse le plafond de tokens MCP).
    Clé absente ≠ catalogue vide.

    `templates` = jeux de règles applicables en un clic ; les appliquer écrit une
    politique d'org ordinaire, ce n'est pas un mode."""
    org_id: int
    # {"<service>": {"rules": [...], "salt"?: str}} — keyé par nom de connecteur.
    filters: dict
    # Défauts serveur. Vide par décision produit (cf. docstring).
    defaults: dict
    templates: dict
    # Spec du formulaire : [{action, label, params:[{key, type, label, options?}]}].
    # Aliasé : le champ s'appelle `schema` sur le fil, mais `BaseModel.schema` est un
    # attribut pydantic — l'attribut python porte donc un underscore final.
    schema_: list[dict] = Field(alias="schema")
    # Catalogue des champs par connecteur (curé ∪ OBSERVÉ sur les vraies réponses).
    # Absent sauf `include_schemas=true`.
    schemas: Optional[dict] = None


class FieldFilterSet(BaseModel):
    """Politique de redaction d'UN connecteur écrite (ou effacée).

    ⚠️ **`rules` change de type entre la requête et la réponse** : en entrée c'est la
    LISTE des règles, en sortie c'est leur NOMBRE. Même nom, deux types.

    ⚠️ `service` n'est **pas validé contre le catalogue de connecteurs** : une faute
    de frappe (`salesfroce`) répond `ok: true` et stocke une politique qui ne
    s'appliquera jamais. Rien dans la réponse ne le signale — relire `filters` de
    `GET /field-filters` et comparer aux connecteurs réels reste à la charge du client.

    `cleared: true` = la politique de ce connecteur a été RETIRÉE ; le repli est le
    défaut serveur, qui est vide ⟹ plus rien n'est masqué pour ce connecteur."""
    ok: bool
    org_id: int
    service: str
    cleared: bool
    # Nombre de règles posées (0 quand `cleared`).
    rules: int


class FieldFilterPreview(BaseModel):
    """Dry-run : l'échantillon passé à travers le filtre. `redacted` a la forme de ce
    qui a été envoyé (objet ou liste), pas une enveloppe.

    ⚠️ Un `redacted` **identique à l'entrée** ne veut pas dire « rien de sensible » :
    c'est le cas normal quand aucune règle ne s'applique (aucune politique posée pour
    ce service, et le défaut serveur est vide). Le dry-run ne détecte rien — il
    APPLIQUE des règles."""
    org_id: int
    service: str
    redacted: Any


class GetFieldFiltersInput(BaseModel):
    org_id: int
    # Le bloc `schemas` (catalogue de champs de TOUS les connecteurs) pèse ~160 KB et
    # dépasse le plafond de tokens MCP (oto-backend#109) — omis par défaut, opt-in.
    include_schemas: bool = False


class SetFieldFilterInput(BaseModel):
    org_id: int
    service: str
    rules: Optional[list[dict]] = None     # None efface la politique du service
    salt: Optional[str] = None


class PreviewFieldFilterInput(BaseModel):
    org_id: int
    service: str
    payload: Any                            # un échantillon de réponse réel (dict ou list)
    rules: Optional[list[dict]] = None      # règles à tester ; None = politique effective du service
    salt: Optional[str] = None


def _validate_rules(rules: list[dict]) -> None:
    for rule in rules:
        if not rule.get("fields"):
            raise AuthzDenied(400, "rule_without_fields", "Chaque règle doit lister des `fields`.")
        action = rule.get("action", "mask")
        if action not in _ACTIONS:
            raise AuthzDenied(400, "unknown_action",
                              f"Action inconnue : {action!r} (attendu {sorted(_ACTIONS)}).")


def _get_field_filters(ctx: ResolvedCtx, inp: GetFieldFiltersInput) -> dict:
    if not org_store.get_org(inp.org_id):
        raise AuthzDenied(404, "unknown_org", f"Org #{inp.org_id} inconnue.")
    filters = org_store.get_org_field_filters(inp.org_id)
    out = {
        "org_id": inp.org_id,
        "filters": filters,
        "defaults": field_filter_defaults.SERVER_DEFAULTS,   # vide : rien par défaut
        "templates": field_filter_defaults.TEMPLATES,         # jeux applicables en 1 clic
        "schema": _ACTION_SCHEMA,
    }
    if not inp.include_schemas:
        return out
    # Schéma par connecteur = OBSERVÉ (squelette des vraies réponses, source de vérité)
    # fusionné avec le curé (libellés/sensibilité). Union de tous les services connus
    # (observés + déjà configurés + curés) pour ne rien cacher. Volumineux (~160 KB) →
    # opt-in (#109).
    observed = db.get_all_connector_schemas()   # {service: {name: {type, paths}}}
    services = set(connector_field_schema.CONNECTOR_FIELD_SCHEMA) | set(filters) | set(observed)

    def _merged_schema(svc: str) -> list[dict]:
        curated = connector_field_schema.schema_for(svc)
        seen = {f["name"].lower() for f in curated}
        extra = [f for f in connector_schema_store.as_fields(observed.get(svc, {}))
                 if f["name"].lower() not in seen]
        return curated + extra

    out["schemas"] = {svc: _merged_schema(svc) for svc in sorted(services)}
    return out


def _set_field_filter(ctx: ResolvedCtx, inp: SetFieldFilterInput) -> dict:
    if not org_store.get_org(inp.org_id):
        raise AuthzDenied(404, "unknown_org", f"Org #{inp.org_id} inconnue.")
    service = (inp.service or "").strip()
    if not service:
        raise AuthzDenied(400, "empty_service", "service vide.")

    block: Optional[dict]
    if inp.rules is None:
        block = None     # efface la politique de ce connecteur
    else:
        _validate_rules(inp.rules)
        block = {"rules": inp.rules}
        if inp.salt:
            block["salt"] = inp.salt

    org_store.set_org_field_filters(inp.org_id, service, block)
    return {"ok": True, "org_id": inp.org_id, "service": service,
            "cleared": block is None, "rules": 0 if block is None else len(inp.rules or [])}


def _preview_field_filter(ctx: ResolvedCtx, inp: PreviewFieldFilterInput) -> dict:
    """Dry-run : passe un échantillon de réponse réel à travers le filtre et renvoie
    la version redactée — pour voir EXACTEMENT ce qui est masqué (clés imbriquées
    incluses), sans deviner. `rules` fournis = on teste ce brouillon ; sinon on applique
    la politique effective du service (org → défaut serveur)."""
    if not org_store.get_org(inp.org_id):
        raise AuthzDenied(404, "unknown_org", f"Org #{inp.org_id} inconnue.")
    if not isinstance(inp.payload, (dict, list)):
        raise AuthzDenied(400, "bad_payload", "`payload` doit être un objet ou une liste JSON.")
    service = (inp.service or "").strip()

    from oto.tools.common import FieldFilter

    if inp.rules is not None:
        _validate_rules(inp.rules)
        block: Optional[dict] = {"rules": inp.rules, "salt": inp.salt} if inp.salt else {"rules": inp.rules}
    else:
        configured = org_store.get_org_field_filters(inp.org_id)
        block = configured.get(service) or field_filter_defaults.SERVER_DEFAULTS.get(service)

    ff = FieldFilter(rules=(block or {}).get("rules", []), salt=(block or {}).get("salt"))
    return {"org_id": inp.org_id, "service": service, "redacted": ff.apply(inp.payload)}


CAPABILITIES += [
    Capability(
        key="org.field_filters.get", handler=_get_field_filters, Input=GetFieldFiltersInput,
        authz=ORG_MEMBER_OF("org_id"), Output=FieldFiltersView,
        description=("Read the org's field-redaction policy per connector, plus the "
                     "server defaults and the available redaction modes/params. Pass "
                     "include_schemas=true to also get the full per-connector field "
                     "catalog (large — omitted by default)."),
        rest=RestBinding("GET", "/api/orgs/{id}/field-filters", _ID),
    ),
    Capability(
        key="org.field_filters.set", handler=_set_field_filter, Input=SetFieldFilterInput,
        authz=ORG_ADMIN_OF("org_id"), Output=FieldFilterSet,
        description=("Set the org's field-redaction rules for one connector (service). "
                     "Each rule = {fields:[...], action, ...params}. Actions: mask "
                     "(+preserve email/phone/iban or keep_first/keep_last), pseudonym "
                     "(+kind), generalize (+to year/month/department/range), hash, "
                     "anonymize, drop. Pass rules=null to clear the connector's policy "
                     "(falls back to the server default). The org policy is authoritative."),
        rest=RestBinding("PUT", "/api/orgs/{id}/field-filters/{service}", _ID),
    ),
    Capability(
        key="org.field_filters.preview", handler=_preview_field_filter, Input=PreviewFieldFilterInput,
        authz=ORG_MEMBER_OF("org_id"), Output=FieldFilterPreview,
        description=("Dry-run a connector's field-redaction on a real sample response: "
                     "returns the redacted payload so you can SEE exactly which fields "
                     "(incl. nested keys) get masked. Pass `rules` to test a draft, or omit "
                     "to apply the service's effective policy (org → server default)."),
        rest=RestBinding("POST", "/api/orgs/{id}/field-filters/{service}/preview", _ID),
    ),
]
