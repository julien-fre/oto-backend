"""Ranger au format deux champs les clés `qonto` d'avant le partage : `oto-mcp maintenance
qonto-deux-champs` (oto-backend#1906).

Le connecteur `qonto` a deux champs (`providers/qonto.py`) : `login`, non secret, dans
`meta.login` (`in_meta`), et `key`, la clé secrète SEULE, dans le chiffré. Des lignes
posées avant le partage portent la paire `login:clé-secrète` dans le seul chiffré ; la
lecture la rattrape encore (`tools/qonto.py:_authorization`, ce qui suit le dernier `:`).
Cette commande réécrit ces lignes une fois, pour que la lecture de l'ancien format puisse
être retirée ensuite (pas dans ce lot).

- **À blanc par défaut** : toutes les lignes `connector = 'qonto'`, toutes entités et
  tous comptes, déchiffrées par le chemin du coffre (`credentials_store._reveal`), et
  jugées. Rien n'est écrit.
- **`appliquer=True`** : chaque ligne convertible est relue SOUS VERROU (`FOR UPDATE`,
  une transaction par ligne), REJUGÉE, puis réécrite par l'entonnoir d'écriture du coffre
  (`credentials_store._upsert`, même AAD que la ligne, `version` + 1, `set_by` gardé) :
  le chiffré devient la seule clé, `meta.login` le préfixe, le reste de `meta` relu et
  repassé tel quel.
- **Un écart ne se tranche jamais en silence** : `meta.login` déjà posé et DIFFÉRENT du
  préfixe, préfixe vide, clé vide après le `:` — comptés à part, jamais convertis.
- **Une erreur sur une ligne** est comptée et journalisée (son rang et le type de
  l'erreur), puis on passe à la suivante ; la ligne en erreur reste intacte.
- **Jamais une valeur** : ni secret, ni login, ni identifiant de ligne — des comptes.
  Idempotent : relancée, elle ne trouve plus rien à convertir.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger(__name__)

CONNECTEUR = "qonto"

# Les verdicts d'une ligne. Les quatre derniers sont « à convertir » (le chiffré porte un
# `:`) ; seuls LOGIN_ABSENT et LOGIN_EGAL se convertissent.
INDECHIFFRABLE = "indechiffrable"
DEUX_CHAMPS = "deux_champs"
LOGIN_ABSENT = "login_absent"
LOGIN_EGAL = "login_egal"
LOGIN_DIFFERENT = "login_different"
PREFIXE_VIDE = "prefixe_vide"
CLE_VIDE = "cle_vide"

A_CONVERTIR = (LOGIN_ABSENT, LOGIN_EGAL, LOGIN_DIFFERENT, PREFIXE_VIDE, CLE_VIDE)
CONVERTIBLES = (LOGIN_ABSENT, LOGIN_EGAL)

_SQL_LIGNES = (
    "SELECT entity_type, entity_id, connector, account, secret_enc, meta "
    "FROM connector_credentials WHERE connector = %s "
    "ORDER BY entity_type, entity_id, account")

_SQL_VERROU = (
    "SELECT entity_type, entity_id, connector, account, secret_enc, meta, version, set_by "
    "FROM connector_credentials WHERE entity_type = %s AND entity_id = %s "
    "AND connector = %s AND account = %s FOR UPDATE")


@dataclass(frozen=True)
class Jugement:
    verdict: str
    cle: Optional[str] = None     # la clé seule, nettoyée — seulement si convertible
    login: Optional[str] = None   # le préfixe — seulement si convertible


@dataclass
class Rapport:
    applique: bool
    comptes: dict = field(default_factory=lambda: {
        v: 0 for v in (INDECHIFFRABLE, DEUX_CHAMPS) + A_CONVERTIR})
    total: int = 0
    converties: int = 0
    erreurs: int = 0

    @property
    def a_convertir(self) -> int:
        return sum(self.comptes[v] for v in A_CONVERTIR)

    @property
    def refusees(self) -> int:
        return self.a_convertir - sum(self.comptes[v] for v in CONVERTIBLES)


def _champ(nom: str):
    from .. import providers
    return next(f for f in providers.REGISTRY[CONNECTEUR].secret_fields if f.name == nom)


def juger(clair: str, meta: Optional[dict]) -> Jugement:
    """Le verdict d'une ligne, depuis son clair et son `meta`. Pur."""
    from ..credentials_store import clean_field_value
    if ":" not in clair:
        return Jugement(DEUX_CHAMPS)
    prefixe, _, reste = clair.rpartition(":")
    login = clean_field_value(_champ("login"), prefixe)
    cle = clean_field_value(_champ("key"), reste)
    if not login:
        return Jugement(PREFIXE_VIDE)
    if not cle:
        return Jugement(CLE_VIDE)
    deja = (meta or {}).get("login")
    deja = deja.strip() if isinstance(deja, str) else deja
    if deja:
        if deja != login:
            return Jugement(LOGIN_DIFFERENT)
        return Jugement(LOGIN_EGAL, cle=cle, login=login)
    return Jugement(LOGIN_ABSENT, cle=cle, login=login)


def _juger_ligne(r, rang: int) -> Jugement:
    from .. import credentials_store as cs
    try:
        clair = cs._reveal(r, r["entity_type"], r["entity_id"], r["connector"], r["account"])
    except Exception as e:
        # Ni valeur ni identifiant : le rang dans le parcours et le type de l'erreur.
        logger.warning("qonto-deux-champs : ligne n°%d indéchiffrable (%s)",
                       rang, type(e).__name__)
        return Jugement(INDECHIFFRABLE)
    return juger(clair, r["meta"])


def _convertir(cle_ligne: tuple, rang: int) -> str:
    """Relit la ligne sous verrou, la rejuge, et la réécrit si elle est toujours
    convertible. Rend le verdict rejugé ; une transaction à elle seule."""
    from .. import credentials_store as cs
    from ..db import _connect
    with _connect() as conn, conn.transaction():
        r = conn.execute(_SQL_VERROU, cle_ligne).fetchone()
        if r is None:
            raise LookupError("ligne disparue entre la lecture et le verrou")
        j = _juger_ligne(r, rang)
        if j.verdict not in CONVERTIBLES:
            return j.verdict
        meta = dict(r["meta"] or {})
        meta["login"] = j.login
        cs._upsert(conn, r["entity_type"], r["entity_id"], r["connector"], r["account"],
                   j.cle, r["set_by"], meta, expected_version=r["version"])
        return j.verdict


def convertir(*, appliquer: bool) -> Rapport:
    """Le rapport des comptes ; `appliquer=True` réécrit les lignes convertibles."""
    from ..db import _connect
    rapport = Rapport(applique=appliquer)
    with _connect() as conn:
        lignes = conn.execute(_SQL_LIGNES, (CONNECTEUR,)).fetchall()
    rapport.total = len(lignes)
    for rang, r in enumerate(lignes, start=1):
        j = _juger_ligne(r, rang)
        if not appliquer or j.verdict not in CONVERTIBLES:
            rapport.comptes[j.verdict] += 1
            continue
        cle_ligne = (r["entity_type"], r["entity_id"], r["connector"], r["account"])
        try:
            verdict = _convertir(cle_ligne, rang)
        except Exception as e:
            rapport.erreurs += 1
            rapport.comptes[j.verdict] += 1
            logger.error("qonto-deux-champs : ligne n°%d en erreur (%s) — rien n'est "
                         "écrit pour elle", rang, type(e).__name__)
            continue
        rapport.comptes[verdict] += 1
        if verdict in CONVERTIBLES:
            rapport.converties += 1
    return rapport


def lignes_du_rapport(rapport: Rapport) -> list[str]:
    """Le rapport imprimé : des comptes, jamais une valeur."""
    c = rapport.comptes
    mode = ("APPLIQUÉ" if rapport.applique
            else "À BLANC (rien n'est écrit ; --appliquer)")
    out = [
        f"qonto-deux-champs : {mode}",
        f"  lignes qonto                      : {rapport.total}",
        f"  déjà au format deux champs        : {c[DEUX_CHAMPS]}",
        f"  à convertir (paire dans le secret): {rapport.a_convertir}",
        f"    meta.login absent               : {c[LOGIN_ABSENT]}",
        f"    meta.login égal au préfixe      : {c[LOGIN_EGAL]}",
        f"    meta.login DIFFÉRENT (refusées) : {c[LOGIN_DIFFERENT]}",
        f"    préfixe vide (refusées)         : {c[PREFIXE_VIDE]}",
        f"    clé vide après « : » (refusées) : {c[CLE_VIDE]}",
        f"  indéchiffrables                   : {c[INDECHIFFRABLE]}",
    ]
    if rapport.applique:
        out.append(f"  converties                        : {rapport.converties}")
        out.append(f"  erreurs                           : {rapport.erreurs}")
    return out
