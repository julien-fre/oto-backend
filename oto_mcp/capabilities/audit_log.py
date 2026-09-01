"""Export du journal d'audit org-scopé (ADR 0009 ; oto-backend#67, #770).

Le trust center public annonce un « journal d'audit de tous les appels d'outils ».
Ce journal existe (`tool_calls`, via le calllog) mais n'était lisible que par un
opérateur plateforme (`/api/admin/monitoring/*`). Ici on l'ouvre à un **org_admin**
pour SON org — preuve de conformité (RGPD art. 28, ISO 42001), revue, dossier client.

Surface : capacité **REST-only** `GET /api/orgs/{id}/audit-log/export`, gatée
`ORG_ADMIN_OF`. Retourne du JSON structuré — le bouton « exporter CSV » du dashboard
sérialise ce JSON côté client (l'adaptateur REST des capacités ne produit que du JSON ;
pas de stream text/csv ici).

**Ce que cet export doit pouvoir faire, et qu'il ne faisait pas (#770).** Ce n'est pas
une lentille de confort : c'est une pièce qu'un client produit pour se justifier devant
un auditeur ou un délégué à la protection des données. Il ne rendait que
`count = len(calls)` **après troncature** — un fichier de 1000 lignes ne disait donc pas
si 1000 ou 50 000 appels avaient eu lieu, et rien ne distinguait « il n'y a eu que ça »
de « voici les 1000 premiers d'un nombre inconnu ». **Une pièce qui ne dit pas si elle
est complète n'atteste de rien**, et une absence dans une vue plafonnée se lit comme un
zéro. La réponse porte désormais `total`, `truncated`, `next_cursor` et
`until_effectif`.

⚠️ **Le piège à ne pas reproduire, et il est dans le remède.** Un total calculé sur un
autre jeu que la page qu'il coiffe est PIRE que pas de total : il a l'air d'attester.
`db.export_tool_calls_for_org` le garantit par construction — une seule construction de
clauses pour les deux lectures, une seule transaction en REPEATABLE READ, et une borne
haute toujours posée (gelée au premier appel, reportée par le curseur) qui CLÔT la
fenêtre. La concaténation des pages vaut donc exactement son `total`.

Org-scoping = **exact** : on filtre `tool_calls.org_id` (l'org sous laquelle l'appel a
été émis, stampée par le seam `current_org` à l'insert) — PAS l'appartenance des
membres (un membre de N orgs ne pollue donc pas l'export). ⚠ Les appels antérieurs à la
colonne `org_id` (NULL) n'apparaissent dans aucun export — non reconstructibles.
Jamais d'args ni de secret (garantie calllog) — colonnes : horodatage, user (sub/email),
outil, namespace, durée, ok, erreur.
"""
from __future__ import annotations

import base64
import binascii
import json
from typing import Optional

from pydantic import BaseModel, field_validator

from .. import db
from ..tool_visibility import namespace_of
from ._authz import ORG_ADMIN_OF
from ._types import (AuthzDenied, Capability, DeclaredError, ResolvedCtx, RestBinding,
                     cap_limit)
from .registry import CAPABILITIES

_ID = {"id": "org_id"}


# ── Le curseur : opaque, et il porte la FENÊTRE, pas seulement la position ────
#
# Un curseur qui ne porterait que la position laisserait la fenêtre se rouvrir à
# chaque page — le journal est alimenté en continu et trié récent d'abord, donc le
# `total` de la page 2 dépasserait celui de la page 1. L'export servirait deux
# vérités successives, et sa concaténation ne vaudrait plus son total. La fenêtre
# voyage donc AVEC la position, et l'appelant n'a rien à réémettre.
#
# ⚠️ Il ne porte QUE ça : ni sub, ni outil, ni erreur. Un curseur traverse des URL
# et les journaux d'accès des proxys ; y loger une donnée du journal la sortirait
# du périmètre que cette capacité protège.

_CURSEUR_ILLISIBLE = (
    "`cursor` illisible — tronqué, réécrit, ou pris d'un autre export. Reprends "
    "l'export sans `cursor` : la fenêtre sera regelée à cet instant.")


def _encode_cursor(since: Optional[str], until: str, position: tuple[str, int]) -> str:
    """Base64url (sans remplissage) d'un objet compact `{s, u, t, i}`."""
    brut = json.dumps({"s": since, "u": until,
                       "t": position[0], "i": int(position[1])},
                      separators=(",", ":")).encode()
    return base64.urlsafe_b64encode(brut).decode().rstrip("=")


def _decode_cursor(cursor: str) -> tuple[Optional[str], str, tuple[str, int]]:
    """→ `(since, until_gelé, (horodatage, id))`. Lève un 400, jamais une panne.

    Un curseur abîmé par un copier-coller n'est pas une panne de serveur : c'est une
    demande malformée, et l'appelant n'a qu'une chose à faire — que le 500 ne dirait
    pas. Même arbitrage et même code que `node_rows` (#621), pour qu'un intégrateur
    n'ait pas deux façons de traiter le même accident.
    """
    try:
        clair = base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4))
        charge = json.loads(clair)
        since, until, quand, ident = (charge["s"], charge["u"], charge["t"],
                                      int(charge["i"]))
    except (binascii.Error, ValueError, TypeError, KeyError, UnicodeDecodeError):
        raise AuthzDenied(400, "invalid_cursor", _CURSEUR_ILLISIBLE)
    if not until or not quand:
        raise AuthzDenied(400, "invalid_cursor", _CURSEUR_ILLISIBLE)
    return since, until, (quand, ident)


class AuditCall(BaseModel):
    """Une ligne d'audit. Jamais d'arguments ni de secret (garantie calllog).
    `created_at` sort en `"YYYY-MM-DD HH:MM:SS"` — pas d'ISO, pas d'offset (le tzinfo
    est retiré par le row factory, pas converti), alors que `since`/`until` en entrée
    sont, eux, de l'ISO. ⚠️ Il est donc tronqué à la SECONDE : deux lignes peuvent
    partager le même `created_at` servi. Le curseur, lui, ne s'en sert pas — il porte
    la microseconde, sans quoi il sauterait ces lignes-là en silence."""
    id: int
    created_at: str
    sub: Optional[str] = None
    # None si aucun compte `users` ne correspond au sub (compte machine, user purgé).
    email: Optional[str] = None
    tool: Optional[str] = None
    ok: Optional[bool] = None
    error: Optional[str] = None
    duration_ms: Optional[int] = None
    # Dérivé du nom d'outil (1er token avant `_`), None si `tool` est vide.
    namespace: Optional[str] = None


class AuditExport(BaseModel):
    """Journal d'audit d'une org — pièce de conformité (RGPD art. 28, ISO 42001).

    **Lire d'abord `total` et `count`, dans cet ordre.** `total` est la population de
    la FENÊTRE ; `count` (= `len(calls)`) les lignes de CETTE réponse. Ils sont
    garantis décrire le même jeu : une seule construction de filtres, une seule
    transaction au snapshot figé. Quand ils diffèrent, `truncated` vaut `true` et
    `next_cursor` dit où prendre la suite ; la concaténation des pages vaut
    exactement `total` lignes.

    `until_effectif` est la borne haute RÉELLEMENT appliquée : celle du demandeur,
    ou, s'il n'en donne pas, l'instant gelé au premier appel et reporté par le
    curseur. C'est ce qui fait de l'export une **période fermée** — la seule forme
    qui puisse s'exhiber comme exhaustive. `until`, lui, reste le réécho de ce qui a
    été reçu (`null` si rien).

    Trois limites subsistent, et aucune n'est réparable par la pagination :

    ⚠️ **La rétention du journal est de `OTO_JOURNAL_RETENTION_DAYS` jours, 90 par
    défaut** — le timer d'archivage exporte le mois au froid puis le supprime. Au-delà,
    les lignes n'existent plus ici : un `since` ancien rend une fenêtre VIDE qui
    ressemble à « aucune activité ». Ce n'est pas une pagination, c'est une
    disparition. ⚠️ **Avant le 2026-08-28, la fenêtre réelle n'était que d'un mois** :
    le boot purgeait à 30 jours sans archiver (corrigé par l'ADR 0065 lot 0) — un
    export portant sur une période antérieure est incomplet, et `total` ne peut pas
    le dire : il compte ce qui EXISTE, pas ce qui a eu lieu.

    ⚠️ **Seuls les appels d'OUTILS MCP sont journalisés** (`kind='mcp'`). Les gestes
    faits au dashboard (poser une clé, changer un rôle) n'y figurent pas : ce journal
    trace ce que l'agent a exécuté, pas tout ce qui a été fait dans l'org.

    ⚠️ **Les appels antérieurs à la colonne `org_id`** (NULL) n'apparaissent dans aucun
    export d'org et ne sont pas reconstructibles — c'est la borne basse historique de
    l'instrument, en deçà de laquelle un total à 0 ne veut pas dire « rien n'a eu lieu ».

    Le scope est EXACT : les appels ÉMIS SOUS cette org (`tool_calls.org_id`), jamais
    l'appartenance — un membre de N orgs n'apporte ici que ce qu'il y a fait."""
    org_id: int
    # Borne basse de la fenêtre : celle reçue, ou celle que le curseur reporte.
    since: Optional[str] = None
    # Borne haute réécho TELLE QUE REÇUE (None si omise) — pas de valeur par défaut
    # substituée. Ce qui a réellement été appliqué est `until_effectif`.
    until: Optional[str] = None
    until_effectif: str
    total: int
    count: int
    truncated: bool
    next_cursor: Optional[str] = None
    calls: list[AuditCall]


class AuditExportInput(BaseModel):
    org_id: int
    since: Optional[str] = None       # borne basse ISO (timestamptz), incluse
    until: Optional[str] = None       # borne haute ISO, incluse
    limit: int = 1000
    # Opaque : renvoyé TEL QUEL, jamais recomposé. Sa composition est à nous.
    cursor: Optional[str] = None

    # C'est la lentille la plus exposée : sur un EXPORT, un grand nombre paraît
    # légitime, donc rien ne le rendait suspect. Écrête au défaut servi (#300).
    @field_validator("limit")
    @classmethod
    def _cap_limit(cls, v):
        return cap_limit(v, 1000)


def _export(ctx: ResolvedCtx, inp: AuditExportInput) -> dict:
    since, until, before = inp.since, inp.until, None
    if inp.cursor is not None:
        if inp.since or inp.until:
            # REFUSÉ, jamais ignoré. Honorer les deux servirait un `total` calculé
            # sur une fenêtre différente de celle où la page a été prise — le défaut
            # exact que ce lot ferme, réintroduit par la porte de derrière.
            raise AuthzDenied(
                400, "window_with_cursor",
                "`cursor` porte déjà la fenêtre de cet export : ne repasse ni "
                "`since` ni `until` avec lui. Les honorer rendrait un `total` qui "
                "ne décrit pas la page servie. Pour changer de fenêtre, repars sans "
                "`cursor`.")
        since, until, before = _decode_cursor(inp.cursor)

    page = db.export_tool_calls_for_org(inp.org_id, since=since, until=until,
                                        limit=inp.limit, before=before)
    calls = page["calls"]
    for c in calls:
        c["namespace"] = namespace_of(c["tool"]) if c.get("tool") else None
    suivant = page["next"]
    return {
        "org_id": inp.org_id,
        "since": since,
        "until": inp.until,
        "until_effectif": page["until_effectif"],
        "total": page["total"],
        "count": len(calls),
        # « Cette réponse ne porte pas toute la fenêtre » — la question que se pose
        # celui qui tient la pièce. Sur une page de continuation, elle reste vraie :
        # la pièce complète est la concaténation, pas la page.
        "truncated": len(calls) < page["total"],
        "next_cursor": (_encode_cursor(since, page["until_effectif"], suivant)
                        if suivant else None),
        "calls": calls,
    }


CAPABILITIES += [
    Capability(
        key="org.audit_log.export", handler=_export, Input=AuditExportInput,
        authz=ORG_ADMIN_OF("org_id"), Output=AuditExport,
        # Deux refus en propre, deux gestes différents derrière le même statut :
        # `invalid_cursor` se corrige en repartant du début, `window_with_cursor` en
        # retirant les bornes de la requête.
        errors=(
            DeclaredError(400, "invalid_cursor",
                          "`cursor` illisible, tronqué, ou pris d'un autre export"),
            DeclaredError(400, "window_with_cursor",
                          "`since`/`until` repassés avec un `cursor`, qui porte "
                          "déjà la fenêtre"),
        ),
        description="Org audit log of tool calls (org_admin): timestamp, user, tool, "
                    "namespace, duration, ok/error — never args or secrets. Scoped to "
                    "calls emitted UNDER this org. Window via since/until (ISO). "
                    "IT STATES ITS OWN COMPLETENESS: `total` is the whole window, "
                    "`count` this response, and both are guaranteed to describe the "
                    "SAME set. `truncated` says this response is not the whole "
                    "window; send `next_cursor` back verbatim as `cursor` to get the "
                    "rest — do NOT resend since/until with it, the cursor carries the "
                    "window (400 `window_with_cursor`). `until_effectif` is the upper "
                    "bound actually applied, frozen at the first call, which makes "
                    "the export a CLOSED period. Beware: calls older than the org "
                    "column, and calls past the retention window, exist in no export "
                    "— there `total: 0` does not mean nothing happened.",
        rest=RestBinding("GET", "/api/orgs/{id}/audit-log/export", _ID),
    ),
]
