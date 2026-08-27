"""Les RÉGLAGES d'une org portés en colonne JSONB : redaction, email, MFA.

Trois politiques qui partagent la même mécanique (merge par clé dans un JSONB de
`orgs`, prose de config jamais un secret) :
- **redaction de champs** par connecteur (`field_filters`, ADR 0015) ;
- **expéditeurs email** par connecteur + fenêtre calme (`email_settings`) et la
  résolution d'un expéditeur (`resolve_sender`) — cf. `docs/email.md` ;
- **MFA** : le drapeau `require_mfa` et l'id de l'organization Logto miroir. Ici
  UNIQUEMENT le drapeau PG ; le provisioning Logto est à `mfa_mirror`.

Feuille du package : n'importe aucun de ses frères.
"""
from __future__ import annotations

import json
from typing import Optional

from .. import db
from ..db import _connect


# --- redaction de champs par connecteur (FieldFilter, ADR 0015) -------------

def get_org_field_filters(org_id: int) -> dict:
    """Politique de redaction de champs de l'org (par connecteur).

    Forme : `{ "<service>": { "salt"?, "rules": [...] } }`. `{}` si rien posé."""
    with _connect() as conn:
        row = conn.execute(
            "SELECT field_filters FROM orgs WHERE id = %s", (org_id,)
        ).fetchone()
        if not row:
            return {}
        return dict(row["field_filters"] or {})


def set_org_field_filters(org_id: int, service: str, block: Optional[dict]) -> bool:
    """Pose (ou efface si block=None) la politique de redaction d'un connecteur.

    Écriture par service (merge dans le JSONB existant) pour ne pas écraser les
    autres connecteurs. Prose de config, pas un secret → colonne en clair."""
    with _connect() as conn:
        with conn.transaction():
            row = conn.execute(
                "SELECT field_filters FROM orgs WHERE id = %s FOR UPDATE", (org_id,)
            ).fetchone()
            if not row:
                return False
            current = dict(row["field_filters"] or {})
            if block is None:
                current.pop(service, None)
            else:
                current[service] = block
            conn.execute(
                "UPDATE orgs SET field_filters = %s::jsonb WHERE id = %s",
                (json.dumps(current), org_id),
            )
            return True


# --- adresses expéditrices d'email de l'org, PAR CONNECTEUR ------------------
# Modèle calqué sur field_filters : JSONB sur orgs, keyé par connecteur. Un
# expéditeur appartient à un connecteur (scaleway/resend) → le transport en dérive
# (providers.EMAIL_CONNECTOR_TRANSPORT). Forme :
#   { "scaleway": {"senders":[{email,name?,reply_to?}], "quiet_hours?":{...}},
#     "resend":   {"senders":[...], "quiet_hours?":{...}} }

# Ordre de résolution d'un expéditeur par défaut (from_email omis).
_EMAIL_CONNECTORS_ORDER = ("scaleway", "resend")


def get_org_email_settings(org_id: int) -> dict:
    """Réglages d'envoi d'email de l'org, keyés PAR CONNECTEUR. `{}` si rien posé.

    Forme : `{ "<connector>": {"senders": [...], "quiet_hours"?: {...}} }`."""
    with _connect() as conn:
        row = conn.execute(
            "SELECT email_settings FROM orgs WHERE id = %s", (org_id,)
        ).fetchone()
        if not row:
            return {}
        return dict(row["email_settings"] or {})


def set_org_email_settings(org_id: int, connector: str, *,
                           senders: Optional[list[dict]] = None,
                           quiet_hours: Optional[dict] = None,
                           clear_quiet_hours: bool = False) -> bool:
    """Met à jour le bloc d'un CONNECTEUR (merge dans le JSONB ; ne touche pas les
    autres connecteurs). False si org absente.

    `senders`/`quiet_hours=None` = ne touche pas ce champ ; `clear_quiet_hours=True`
    = efface la fenêtre du connecteur (retour au défaut plateforme à l'envoi),
    exclusif avec `quiet_hours`. Prose de config (pas un secret) → colonne en clair ;
    la clé Resend, elle, vit dans le coffre (`set_org_secret(org_id, "resend", ...)`)."""
    with _connect() as conn:
        with conn.transaction():
            row = conn.execute(
                "SELECT email_settings FROM orgs WHERE id = %s FOR UPDATE", (org_id,)
            ).fetchone()
            if not row:
                return False
            current = dict(row["email_settings"] or {})
            block = dict(current.get(connector) or {})
            if senders is not None:
                block["senders"] = senders
            if clear_quiet_hours:
                block.pop("quiet_hours", None)
            elif quiet_hours is not None:
                block["quiet_hours"] = quiet_hours
            current[connector] = block
            conn.execute(
                "UPDATE orgs SET email_settings = %s::jsonb WHERE id = %s",
                (json.dumps(current), org_id),
            )
            return True


def get_org_mfa(org_id: int) -> dict:
    """État MFA d'une org : `{require_mfa: bool, logto_org_id: str|None}`.
    `require_mfa` = l'org impose le 2ᵉ facteur à ses membres ; `logto_org_id` =
    l'organization Logto MIROIR (None si le MFA n'a jamais été activé). Défaut
    inerte si l'org est absente."""
    with _connect() as conn:
        row = conn.execute(
            "SELECT require_mfa, logto_org_id FROM orgs WHERE id = %s", (org_id,)
        ).fetchone()
        if not row:
            return {"require_mfa": False, "logto_org_id": None}
        return {"require_mfa": bool(row["require_mfa"]),
                "logto_org_id": row["logto_org_id"]}


def set_org_require_mfa(org_id: int, require: bool) -> bool:
    """Pose le drapeau `require_mfa` de l'org (toggle org_admin). False si org
    absente. **Ne provisionne PAS** l'org Logto miroir — c'est la couche
    `mfa_mirror` qui, après ce flag, crée/supprime l'organization Logto et
    enregistre son id via `set_org_logto_org_id`. Ici, uniquement le drapeau PG."""
    with _connect() as conn:
        row = conn.execute(
            "UPDATE orgs SET require_mfa = %s WHERE id = %s RETURNING id",
            (bool(require), org_id),
        ).fetchone()
        return row is not None


def set_org_logto_org_id(org_id: int, logto_org_id: Optional[str]) -> bool:
    """Mémorise (ou efface avec None) l'id de l'organization Logto miroir de l'org.
    False si org absente."""
    with _connect() as conn:
        row = conn.execute(
            "UPDATE orgs SET logto_org_id = %s WHERE id = %s RETURNING id",
            (logto_org_id, org_id),
        ).fetchone()
        return row is not None


def list_scheduled_emails(org_id: int, status: str = "pending") -> list[dict]:
    """Emails programmés de l'org (délégation au journal db)."""
    return db.list_scheduled_emails(org_id, status=status)


def cancel_scheduled_email(org_id: int, email_id: int) -> bool:
    """Annule un email encore en attente de l'org (délégation au journal db)."""
    return db.cancel_scheduled_email(org_id, email_id)


def _email_connectors_in_order(settings: dict) -> list[str]:
    """Connecteurs email présents, dans un ordre déterministe (scaleway, resend,
    puis tout autre keyé inattendu trié)."""
    present = list(settings.keys())
    ordered = [c for c in _EMAIL_CONNECTORS_ORDER if c in present]
    return ordered + sorted(c for c in present if c not in _EMAIL_CONNECTORS_ORDER)


def resolve_sender(org_id: int, from_email: Optional[str] = None
                   ) -> Optional[tuple[dict, str]]:
    """`(sender, connector)` à utiliser, ou None si l'org n'a aucun expéditeur.

    `from_email` fourni = doit matcher un expéditeur déclaré (dans n'importe quel
    connecteur) ; absent = le 1er expéditeur du 1er connecteur (ordre déterministe).
    Le connecteur retourné détermine le transport (EMAIL_CONNECTOR_TRANSPORT)."""
    settings = get_org_email_settings(org_id)
    want = from_email.strip().lower() if from_email else None
    for connector in _email_connectors_in_order(settings):
        senders = (settings.get(connector) or {}).get("senders") or []
        for s in senders:
            if want is None:
                return s, connector
            if (s.get("email") or "").strip().lower() == want:
                return s, connector
    return None


def org_email_quiet_hours(org_id: int, connector: str) -> Optional[dict]:
    """Fenêtre calme d'un connecteur email de l'org (None = pas posée)."""
    return (get_org_email_settings(org_id).get(connector) or {}).get("quiet_hours")
