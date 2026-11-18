"""Les APPLICATIONS de Custom API : une définition par org et par outil, servie à toutes
les clés qui lui sont liées, quel que soit leur palier (`docs/connector-vault.md` § Les
applications de Custom API).

Avant elles, un connecteur personnalisé ÉTAIT une ligne du coffre : son adresse, son mode
d'authentification et ses actions vivaient sur la clé (champs chiffrés + `config` de son
instance). Une clé personnelle ou d'équipe était donc un autre connecteur, sans actions,
et un outil sans clé d'org ne pouvait pas exister. L'application sépare les deux :

- **l'application** (`db/api_apps`) porte ce qui est NON secret et commun — la
  connexion (`CONNECTION_FIELDS`) et la définition (`custom_ops_definition`). Elle
  appartient à l'org ; seul un admin de l'org la crée, la modifie, la retire ;
- **ses clés** restent des lignes ordinaires du coffre (membre, équipe, org), sous le nom
  de compte de l'application, posées par les surfaces de toujours. À la pose, une clé du
  nom d'une application vivante de l'org reçoit la connexion de l'application
  (`completer_la_pose` : un champ de connexion qui diffère est REFUSÉ, jamais écrasé en
  silence) et le lien `meta.api_app = <id>`. Ce lien, et lui seul, fait d'une clé une clé
  de l'application : une clé posée avant, qui porte le même nom, reste ce qu'elle était ;
- **à l'appel**, la cascade résout la clé comme avant ; `servie` compose la clé servie :
  ses secrets, la connexion de l'application, sa définition. Une clé liée dont
  l'application a été retirée, ou dont l'adresse ou le mode ont changé depuis la pose,
  REFUSE en le nommant (`api_app_removed`, `api_app_key_stale`) : son secret ne part
  jamais vers un hôte pour lequel personne ne l'a posé.

Qui accède à une application = qui détient une clé qui se résout pour lui : sa clé
personnelle, la clé de son équipe ACTIVE, la clé de l'org. Pas d'autre liste d'accès ici
— les règles de comptes (#1499) la porteront.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Optional
from urllib.parse import urlsplit

from .. import credentials_store, custom_ops, custom_ops_definition, egress, group_store
from ..db import api_apps as store
from ..providers import api as api_card

logger = logging.getLogger(__name__)

CONNECTOR = store.CONNECTOR
#: Ce que l'application fixe pour toutes ses clés : l'adresse et la façon de s'y
#: authentifier, jamais un secret.
CONNECTION_FIELDS = ("base_url", "auth_mode", "header_name", "query_param", "doc_path",
                     "token_url", "scope", "session")
#: Ce qu'une clé apporte : ses secrets, et l'identifiant qui va avec (le login d'un
#: couple basic, le client d'un oauth2 client-credentials).
KEY_FIELDS = ("token", "username", "password", "client_id", "client_secret")
_ACCOUNT_MAX = 80


class AppRefused(ValueError):
    """Un refus nommé (`code`) — traduit en 4xx par la REST, en erreur d'outil par MCP."""

    def __init__(self, status: int, code: str, message: str,
                 errors: Optional[list[str]] = None):
        super().__init__(message)
        self.code, self.message, self.status = code, message, status
        self.errors = errors or []


# ── La connexion : validée une fois, à l'écriture de l'application ──────────────────

def _champs() -> dict:
    return {f.name: f for f in api_card.CONNECTOR.credential_fields}


def valider_connexion(given: dict, *, base: Optional[dict] = None) -> dict:
    """La connexion normalisée (`base` complétée par `given`, un champ vide l'efface),
    ou `AppRefused` nommant le premier problème. Mêmes règles que la carte : un mode du
    jeu fermé, les champs que le mode rend pertinents et requis, `base_url` et
    `token_url` sous la garde d'egress."""
    unknown = sorted(set(given) - set(CONNECTION_FIELDS))
    if unknown:
        raise AppRefused(400, "invalid_app_connection",
                         f"unknown connection field(s): {', '.join(unknown)} — an app "
                         f"holds {', '.join(CONNECTION_FIELDS)}; keys hold the secrets.")
    out = dict(base or {})
    for k, v in given.items():
        v = "" if v is None else str(v)
        if k != "session":
            v = v.strip()
        if v:
            out[k] = v.rstrip("/") if k == "base_url" else v
        else:
            out.pop(k, None)
    mode = out.get("auth_mode", "")
    if mode not in api_card.AUTH_MODES:
        raise AppRefused(400, "invalid_app_connection",
                         f"`auth_mode` must be one of {', '.join(api_card.AUTH_MODES)}.")
    champs = _champs()
    for name in CONNECTION_FIELDS:
        f = champs[name]
        relevant = not f.when or mode in f.when
        if not relevant:
            out.pop(name, None)
        elif f.required and not out.get(name):
            raise AppRefused(400, "invalid_app_connection",
                             f"`{name}` is required in `{mode}` mode.")
    p = urlsplit(out["base_url"])
    if p.scheme not in ("http", "https") or not p.hostname or p.username or p.password \
            or p.query or p.fragment:
        raise AppRefused(400, "invalid_app_connection",
                         "`base_url` is the API root (https://host/path): no user, no "
                         "`?query`, no `#`.")
    for name in ("base_url", "token_url"):
        if out.get(name):
            try:
                egress.check_url(out[name], connector=CONNECTOR, field=name)
            except egress.EgressRefused as e:
                raise AppRefused(400, "egress_refused", str(e))
    return out


def valider_nom(account: str) -> str:
    """Le nom de compte de l'application — celui sous lequel ses clés se posent. Les
    cartes d'avant se nomment souvent comme elles s'affichent (« Acme Payroll ») : tout
    nom que le coffre accepte l'est ici, sauf `/` (le nom est un segment de route) et les
    caractères de contrôle."""
    a = (account or "").strip()
    if not a or len(a) > _ACCOUNT_MAX or "/" in a or any(ord(c) < 32 for c in a):
        raise AppRefused(400, "invalid_app_account",
                         f"`account` is the app's name, the one its keys are set under: "
                         f"1 to {_ACCOUNT_MAX} characters, no `/` (e.g. `crm_admin`).")
    return a


def valider_definition(given: dict, connection: dict) -> dict:
    auth_header = connection.get("header_name") if connection.get("auth_mode") == "header" \
        else None
    try:
        return custom_ops_definition.validate_definition(given, auth_header=auth_header)
    except custom_ops.OperationsInvalid as e:
        raise AppRefused(400, "invalid_operations", f"{len(e.errors)} problem(s): {e}",
                         errors=list(e.errors))


# ── La pose d'une clé ───────────────────────────────────────────────────────────────

def org_de(entity_type: str, entity_id: str) -> Optional[int]:
    """L'org d'une ligne de coffre partageable : la sienne (org), celle de l'équipe,
    celle du couple (org, sub) d'une clé membre. None pour les autres paliers."""
    try:
        if entity_type == credentials_store.ORG:
            return int(entity_id)
        if entity_type == credentials_store.MEMBER:
            return int(str(entity_id).split(":", 1)[0])
        if entity_type == "group":
            g = group_store.get_group(int(entity_id))
            return int(g["org_id"]) if g else None
    except (TypeError, ValueError):
        return None
    return None


def completer_la_pose(entity_type: str, entity_id: str, connector: str, account: str,
                      api_key: Optional[str],
                      fields: Optional[dict]) -> tuple[Optional[str], Optional[dict], dict]:
    """`(api_key, fields, meta)` d'une pose — inchangés, `meta` vide, quand ce n'est pas
    la clé d'une application vivante de l'org. Sinon les champs de connexion sont ceux de
    l'application (un champ envoyé qui DIFFÈRE est refusé, `api_app_field_fixed`), le nom
    affiché est le sien, et `meta` lie la clé à l'application (`api_app`)."""
    if connector != CONNECTOR or not (account or "").strip():
        return api_key, fields, {}
    org = org_de(entity_type, entity_id)
    app = store.vivante(org, account.strip()) if org is not None else None
    if app is None:
        return api_key, fields, {}
    given = dict(fields or {})
    if (api_key or "").strip() and not given.get("token"):
        given["token"] = api_key.strip()
    conn = app["connection"] or {}
    for name in (*CONNECTION_FIELDS, "label"):
        sent = str(given.get(name) or "").strip()
        fixed = str((conn.get(name) if name != "label" else _label(app)) or "").strip()
        if name == "base_url":
            sent, fixed = sent.rstrip("/"), fixed.rstrip("/")
        if sent and sent != fixed:
            raise AppRefused(400, "api_app_field_fixed", (
                f"`{name}` is set by the app `{app['account']}` for all its keys "
                f"({fixed or 'empty'}): send only the key's own fields "
                f"({', '.join(KEY_FIELDS)}). An org admin changes the app itself."))
        given.pop(name, None)
    given.update({k: v for k, v in conn.items() if k in CONNECTION_FIELDS and v})
    given["label"] = _label(app)
    return None, given, {store.META_KEY: int(app["id"])}


# ── L'appel : la clé servie ────────────────────────────────────────────────────────

@dataclass(frozen=True)
class CleServie:
    """La clé que les outils `api*` utilisent : la ligne gagnante de la cascade (son
    propriétaire, son compte, ses secrets), composée avec son application quand elle y
    est liée. Les champs ne sortent jamais par `repr` (#564)."""
    entity_type: str
    entity_id: str
    account: str
    fields: dict = field(repr=False)
    app: Optional[dict] = field(default=None, repr=False)


def _origine(url: str) -> str:
    """`scheme://hôte:port` — ce qui désigne QUI reçoit la requête. Le chemin, lui, peut
    changer sans que le secret change de destinataire."""
    p = urlsplit((url or "").strip())
    port = p.port or {"https": 443, "http": 80}.get((p.scheme or "").lower())
    return f"{(p.scheme or '').lower()}://{(p.hostname or '').lower()}:{port}"


def destination(conn: dict) -> tuple:
    """Tout ce qui décide OÙ partent les secrets d'une clé, pour une connexion donnée :
    l'origine de `base_url`, le mode, l'origine de `token_url` (oauth2 : le secret client
    y est posté) et les étapes de connexion (session, visitor : leurs hôtes reçoivent le
    mot de passe ou les cookies). Deux connexions de même destination servent la même clé ;
    sinon la clé posée pour l'une ne part pas vers l'autre."""
    mode = (conn.get("auth_mode") or "").strip()
    return (_origine(conn.get("base_url", "")), mode,
            _origine(conn.get("token_url", "")) if mode == "oauth2" else "",
            (conn.get("session") or "") if mode in ("session", "visitor") else "")


def servie(rc) -> CleServie:
    """La clé servie pour `rc` (une `ResolvedCredential` de `api`). Une clé non liée
    passe inchangée — le comportement d'avant les applications."""
    et, eid, account = rc.entity_type, str(rc.entity_id), rc.account
    app_id = store.lien(et, eid, account)
    if app_id is None:
        return CleServie(et, eid, account, rc.fields)
    app = store.get(app_id)
    if app is None or app["archived_at"] is not None or org_de(et, eid) != app["org_id"]:
        raise AppRefused(409, "api_app_removed", (
            f"the Custom API app `{account}` this key belongs to was removed by an org "
            "admin: ask them, or remove the key."))
    fields = rc.fields
    conn = app["connection"] or {}
    if destination(fields) != destination(conn):
        raise AppRefused(409, "api_app_key_stale", (
            f"the app `{account}` now sends its keys to "
            f"{_origine(conn.get('base_url', ''))} (`{conn.get('auth_mode')}` mode), and "
            f"this key was set for {_origine(fields.get('base_url', ''))} "
            f"(`{fields.get('auth_mode')}` mode, same token URL and sign-in steps "
            "required): it is not sent there. Set the key again on the app's card."))
    merged = {**{k: v for k, v in fields.items() if k not in CONNECTION_FIELDS},
              **{k: v for k, v in conn.items() if k in CONNECTION_FIELDS}}
    return CleServie(et, eid, account, merged, app)


def definition(app: dict) -> dict:
    """La définition d'une application, chaque clé présente (même forme que celle d'une
    instance, `api_operations.definition_of`)."""
    d = app.get("definition") or {}
    ops = d.get("operations")
    return {"label": _label(app), "description": d.get("description"),
            "logo_domain": d.get("logo_domain"), "headers": d.get("headers") or {},
            "variables": d.get("variables") or {},
            "operations": ops if isinstance(ops, list) else []}


def _label(app: dict) -> str:
    return ((app.get("definition") or {}).get("label") or app["account"]).strip()


# ── Ce que voit un membre ───────────────────────────────────────────────────────────

def candidates(org_id: int, sub: Optional[str],
               group_id: Optional[int]) -> list[tuple[str, str]]:
    """Les lignes du coffre qui pourraient servir `sub`, dans l'ordre de la cascade :
    la sienne dans l'org, celle de son équipe active, celle de l'org."""
    out = []
    if sub:
        out.append((credentials_store.MEMBER, credentials_store.member_id(org_id, sub)))
    if group_id is not None:
        out.append(("group", str(group_id)))
    out.append((credentials_store.ORG, str(org_id)))
    return out


def acces_par_app(org_id: int, sub: Optional[str],
                  group_id: Optional[int]) -> dict[int, str]:
    """`{id d'application: palier}` de la clé qui servirait `sub` pour chaque application
    où il en a une — UNE requête pour toutes, par le préfixe de la clé primaire du coffre
    (jamais un balayage de `meta`)."""
    rang = {"member": 0, "group": 1, "org": 2}
    out: dict[int, str] = {}
    for r in store.liens_de(candidates(org_id, sub, group_id)):
        niveau = "member" if r["entity_type"] == credentials_store.MEMBER else r["entity_type"]
        if r["app_id"] not in out or rang[niveau] < rang[out[r["app_id"]]]:
            out[r["app_id"]] = niveau
    return out


def acces(app: dict, org_id: int, sub: Optional[str],
          group_id: Optional[int]) -> Optional[str]:
    """Le palier de la clé qui servirait `sub` pour CETTE application, ou None."""
    return acces_par_app(org_id, sub, group_id).get(int(app["id"]))


def resume(app: dict, *, access: Optional[str]) -> dict:
    d = definition(app)
    return {"account": app["account"], "label": d["label"],
            "description": d["description"], "logo_domain": d["logo_domain"],
            "actions_count": len(d["operations"]), "scope": "app", "access": access,
            "operations": d["operations"]}


def equipe_active(sub: str, org_id: int) -> Optional[int]:
    """L'équipe dont la clé servirait les agents de `sub` dans cette org : celle de
    l'appel (`access.current_group`), sinon son équipe ACTIVE si elle est de l'org. Une
    page du tableau de bord consulte l'org (`X-Oto-Org`) sans désigner d'équipe — le
    niveau org pour la cascade d'un appel REST ; mais ses agents, eux, appellent depuis
    son équipe active, et c'est ce que la page doit dire."""
    from .. import access
    g = access.current_group(sub)
    if g is not None:
        return g
    ag = group_store.get_active_group(sub)
    if ag is not None and (group_store.get_group(ag) or {}).get("org_id") == int(org_id):
        return ag
    return None


def pour_membre(org_id: int, sub: Optional[str],
                group_id: Optional[int] = None) -> list[dict]:
    """Les applications de l'org vues par `sub` : chacune avec le palier de la clé qui
    le servirait (`access`, None = pas de clé pour lui). `group_id` = son équipe ACTIVE,
    lue ici quand elle n'est pas donnée. Fail-open, journalisé : une liste qui tombe ne
    doit pas emporter celle des connecteurs d'avant."""
    try:
        apps = store.lister(org_id)
        if not apps:
            return []
        if group_id is None and sub:
            group_id = equipe_active(sub, org_id)
        servies = acces_par_app(org_id, sub, group_id)
        return [resume(a, access=servies.get(int(a["id"]))) for a in apps]
    except Exception:
        logger.warning("api_apps : applications de l'org %s illisibles (omises)", org_id,
                       exc_info=True)
        return []


def _iso(v) -> Optional[str]:
    return v if isinstance(v, (str, type(None))) else v.isoformat()


def apercu(app: dict, *, avec_cles: bool) -> dict[str, Any]:
    """Ce qu'on lit d'une application : sa connexion (non secrète), sa définition — et,
    `avec_cles` (un admin de l'org), le palier de chacune de ses clés. Jamais un secret.
    ⚠️ `avec_cles` lit `cles_liees`, que rien n'indexe : réservé à la page d'un admin."""
    out = {**resume(app, access=None), "id": int(app["id"]),
           "connection": app["connection"] or {}, "definition": definition(app)}
    if avec_cles:
        out["keys"] = [_cle(c) for c in store.cles_liees(int(app["id"]))]
    return out


def _cle(c: dict) -> dict:
    membre = c["entity_type"] == credentials_store.MEMBER
    return {"level": "member" if membre else c["entity_type"],
            "owner": str(c["entity_id"]).split(":", 1)[-1] if membre else str(c["entity_id"]),
            "set_at": _iso(c["set_at"]), "set_by": c["set_by"]}
