"""Le RELAIS d'upload du connecteur `pennylane_firm` — la partie synchrone.

Le poste de l'utilisateur envoie un fichier SANS jeton Pennylane (`curl -F file=@…`
sur une URL signée à usage unique) ; le serveur le relaie à la GED de la société avec
le jeton de cabinet du coffre de l'org, et rend l'objet fichier que Pennylane a créé.
La route (`api/pennylane_firm.py`) lit le multipart ; ici vit tout ce qui touche la
base, le coffre ou Pennylane, appelé depuis un thread (`run_in_threadpool`).

- **Jeton** : celui des uploads signés (`upload_tokens.sign/verify`, HMAC, 15 min,
  `jti` à usage unique dans la même table), sous un `typ` DISTINCT (`relay`) — un
  jeton d'upload oto ne s'ouvre pas ici, un jeton de relais ne s'ouvre pas sur
  `/api/upload/{token}`. Cible scellée : `{kind, company_id, parent_folder_id, name}`
  + `sub` + `org`.
- **Org scellée** (`preparer`) : l'appartenance du `sub` à l'org du jeton est
  revérifiée, puis l'org est ÉPINGLÉE pour résoudre le credential — sans quoi la
  cascade retomberait sur l'org « maison » du compte, qui n'est pas forcément celle
  qui a frappé le lien.
- **Plafond** (`max_bytes`) et **concurrence** (`prendre_place`) : réglages propres au
  relais, distincts de ceux des uploads oto.
- **Aucune connexion base pendant le transfert** : `preparer` lit tout ce qu'il faut
  (appartenance, suspension, activation, coffre, doublon) et rend la main ;
  `relayer` ne parle qu'à Pennylane.
"""
from __future__ import annotations

import os
import threading
from dataclasses import dataclass, field
from typing import Optional

from .. import config, upload_tokens
from . import pennylane_firm_socle as socle
from .pennylane_firm_socle import Refus, _client

TYP = "relay"
KIND = "pennylane_firm_dms"
TTL_S = 900
NOM_MAX = 255

#: PROVISOIRE : à relever après mesure de la limite réelle de Pennylane, que sa
#: documentation ne donne pas.
_DEFAUT_MAX_BYTES = 100 * 1024 * 1024
_DEFAUT_CONCURRENCE = 4

#: Le nom stable du dépôt dans le journal d'appels.
OUTIL_JOURNAL = "pennylane_firm_relay"


def max_bytes() -> int:
    raw = os.environ.get("OTO_PENNYLANE_FIRM_UPLOAD_MAX_BYTES")
    return int(raw) if raw else _DEFAUT_MAX_BYTES


def concurrence() -> int:
    raw = os.environ.get("OTO_PENNYLANE_FIRM_RELAY_CONCURRENCY")
    return int(raw) if raw else _DEFAUT_CONCURRENCE


# ── Concurrence : au plus N relais par processus, sans attente ─────────────────────

_PLACES = threading.Lock()
_en_cours = 0


def prendre_place() -> bool:
    """Prend une place de relais, ou rend `False` tout de suite (jamais d'attente :
    un relais tient un fichier sur disque et un thread des minutes durant)."""
    global _en_cours
    with _PLACES:
        if _en_cours >= concurrence():
            return False
        _en_cours += 1
        return True


def rendre_place() -> None:
    global _en_cours
    with _PLACES:
        _en_cours = max(0, _en_cours - 1)


# ── Le jeton ───────────────────────────────────────────────────────────────────────

def valider_nom(name: Optional[str]) -> str:
    nom = (name or "").strip()
    if not 0 < len(nom) <= NOM_MAX:
        raise Refus(400, "invalid_name",
                    f"`name` is the file's name in the GED: 1 to {NOM_MAX} characters.")
    return nom


def frapper(sub: str, org_id: int, company_id: int, parent_folder_id: int,
            name: str) -> dict:
    """Signe le lien de relais. Le contrôle de doublon est joué par l'appelant
    AVANT : un lien ne se frappe que pour un dépôt qui peut réussir."""
    cible = {"kind": KIND, "company_id": int(company_id),
             "parent_folder_id": int(parent_folder_id), "name": name}
    jeton, exp = upload_tokens.sign(sub, org_id, cible, ttl=TTL_S, typ=TYP)
    url = f"{config.public_base_url()}/api/relay/{jeton}"
    return {"url": url, "expires_at": exp, "max_bytes": max_bytes()}


def verifier(jeton: str) -> Optional[dict]:
    """Le payload d'un jeton de RELAIS valide (signature, `typ`, expiration, cible),
    `None` sinon. Ne consomme pas le jeton."""
    payload = upload_tokens.verify(jeton, typ=TYP)
    if payload is None:
        return None
    cible = payload.get("target") or {}
    if cible.get("kind") != KIND or cible.get("parent_folder_id") is None:
        return None
    return payload


# ── Préparer (base, coffre) puis relayer (Pennylane seul) ──────────────────────────

@dataclass
class Preparation:
    sub: str
    org_id: int
    company_id: int
    parent_folder_id: int
    name: str
    key_mode: Optional[str] = None
    # Le jeton de cabinet : jamais rendu par `repr` (une trace d'exception le
    # recopierait sinon dans un journal).
    key: str = field(default="", repr=False)


def preparer(sub: str, org_id: Optional[int], cible: dict) -> Preparation:
    """Rejoue l'org SCELLÉE, résout le jeton, applique le contrôle de doublon.
    Synchrone : appelé hors boucle. Lève `Refus`."""
    from .. import access, org_suspension, roles, session_org
    from ..connectors import activation
    from ..mcp_errors import McpError

    if org_id is None:
        raise Refus(403, "no_organization",
                    "This link was minted outside any organization: mint another one "
                    "within the organization that holds the firm token.")
    if not roles.is_org_member(sub, org_id):
        raise Refus(403, "not_an_org_member",
                    f"The account that minted this link is no longer a member of "
                    f"organization {org_id}.")
    if (texte := org_suspension.refus(org_id)):
        raise Refus(403, "org_suspended", texte)
    if activation.cran_qui_coupe(socle.CONNECTOR, org_id, None) is not None:
        raise Refus(403, "connector_disabled",
                    f"The connector `{socle.CONNECTOR}` is disabled for organization "
                    f"{org_id}.")
    epingle = session_org.set_call_org(org_id)
    try:
        rc = access.resolve_credential(socle.CONNECTOR, sub=sub)
    except McpError as e:
        detail = getattr(getattr(e, "error", None), "message", None) or str(e)
        raise Refus(424, "credential_unavailable",
                    f"No firm token reachable for organization {org_id}: {detail}") from e
    finally:
        session_org.reset_call_org(epingle)
    company_id, dossier = int(cible["company_id"]), int(cible["parent_folder_id"])
    nom = valider_nom(cible.get("name"))
    c = _client(rc.key)
    socle.verifier_absent(c, org_id, company_id, dossier, nom)
    return Preparation(sub=sub, org_id=int(org_id), company_id=company_id,
                       parent_folder_id=dossier, name=nom, key_mode=rc.mode, key=rc.key)


def relayer(prep: Preparation, fichier, filename: Optional[str],
            content_type: Optional[str]) -> dict:
    """Envoie `fichier` (objet fichier SEEKABLE, lu en flux par la lib, jamais
    chargé en entier) à la GED. Réserve le nom, le confirme au succès, le libère à
    l'échec ; un 422 invalide l'entrée du cache. Ne touche pas la base."""
    c = _client(prep.key)
    socle.reserver(c, prep.org_id, prep.company_id, prep.parent_folder_id, prep.name)
    reussi = False
    try:
        fichier.seek(0)
        resultat = socle.traduire(lambda: c.upload_dms_file(
            prep.company_id, fichier, filename or prep.name, prep.parent_folder_id,
            name=prep.name, content_type=content_type), "the upload")
        reussi = True
        return resultat
    except Refus as e:
        if e.status == 422:
            socle.invalider(prep.org_id, prep.company_id, prep.parent_folder_id)
        raise
    finally:
        if reussi:
            socle.confirmer(prep.org_id, prep.company_id, prep.parent_folder_id,
                            prep.name)
        else:
            socle.liberer(prep.org_id, prep.company_id, prep.parent_folder_id,
                          prep.name)


def ligne_journal(payload: dict, *, ok: bool, error: Optional[str],
                  duree_ms: int, octets: Optional[int],
                  file_id=None, key_mode: Optional[str] = None) -> dict:
    """La ligne `tool_calls` d'un dépôt. `kind='mcp'` : le dépôt est la fin du geste
    d'un agent (`pennylane_firm_upload_url`), et `connector` désigne les ÉCHECS de
    résolution de credential — une ligne réussie y passerait pour une panne. Jamais
    le jeton, ni de Pennylane ni du lien."""
    cible = payload.get("target") or {}
    args = {"company_id": cible.get("company_id"),
            "parent_folder_id": cible.get("parent_folder_id"),
            "name": cible.get("name"), "bytes": octets}
    if file_id is not None:
        args["file_id"] = file_id
    return {"kind": "mcp", "tool": OUTIL_JOURNAL, "sub": payload.get("sub"),
            "org_id": payload.get("org"), "args": args, "ok": ok, "error": error,
            "duration_ms": duree_ms, "key_mode": key_mode}
