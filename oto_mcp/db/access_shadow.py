"""Le compteur de la double lecture L7 — SQL seul (blueprint ADR 0053, lot L7).

La table `access_shadow_l7` est posée par `db/schema/grants.py` ; ce module en est
l'unique lecteur/écrivain. Il ne porte AUCUNE politique : ce qui est comparé, et
comment une divergence se classe, vit dans `access/chain_shadow.py`. Ici, des requêtes.

**`origine` — qui a écrit.** Prod et preprod partagent la MÊME base. Sans cette
colonne leurs compteurs se mélangent, et « une fenêtre en prod » — la mesure qui
autorise la bascule d'autorité — n'est pas lisible. Elle est dérivée de ce que le
process sait de lui-même (`config.origine_du_process`), et vaut `NULL` quand il ne
peut pas savoir (dev, tests) comme sur les lignes écrites avant elle : un inconnu se
déclare, il ne se devine pas.

⚠️ **L'écriture ne suppose PAS la forme de la clé primaire**, et c'est délibéré.
Étendre la clé à `origine` est un ordre NON additif, donc une commande explicite
(`scripts/migrate_shadow_origine.py`, ADR 0065) qui ne tourne pas au même instant que
le déploiement. Entre les deux, l'ancienne clé tient encore et deux origines se
partagent une ligne : l'écriture le détecte et **fusionne**, c'est-à-dire qu'elle rend
exactement le comportement d'avant la colonne. Un `ON CONFLICT` figé sur l'une des deux
formes, lui, casserait d'un côté ou de l'autre de la migration.
"""
from __future__ import annotations

import json
import logging
from typing import Optional

import psycopg

from .. import config
from ._conn import _connect

logger = logging.getLogger(__name__)

_COLONNES = "day, connector, org_id, classe, n, first_at, last_at, sample, origine"

# « L'appelant n'a rien dit » ≠ « l'appelant dit : origine inconnue ». Les deux
# existent, et `None` ne peut pas porter les deux sens : sans cette sentinelle, un
# test (ou un appelant) qui veut écrire une ligne AMBIGUË se voit attribuer l'origine
# du process — le bug était invisible tant qu'aucune variable d'environnement n'était
# posée, et il est apparu au banc complet, où un test voisin en pose une.
_AUTO = object()


def bump_shadow(connector: str, org_id: Optional[int], classe: str, n: int = 1,
                sample: Optional[dict] = None, origine=_AUTO) -> None:
    """Ajoute `n` occurrences à la classe du jour, pour l'origine de CE process.

    `sample` n'est posé qu'à la naissance de la ligne : le premier écart d'un jour est
    celui qu'on veut retrouver, pas le dernier."""
    if n <= 0:
        return
    origine = config.origine_du_process() if origine is _AUTO else origine
    payload = json.dumps(sample or {}, ensure_ascii=False, sort_keys=True)
    with _connect() as conn:
        touchees = conn.execute(
            "UPDATE access_shadow_l7 SET n = n + %s, last_at = NOW() "
            "WHERE day = CURRENT_DATE AND connector = %s AND org_id = %s "
            "AND classe = %s AND origine IS NOT DISTINCT FROM %s",
            (int(n), connector, int(org_id or 0), classe, origine)).rowcount
        if touchees:
            return
        try:
            # Bloc imbriqué : un échec d'unicité ne doit pas condamner la transaction
            # entière (sans savepoint, tout ce qui suit serait refusé).
            with conn.transaction():
                conn.execute(
                    "INSERT INTO access_shadow_l7 "
                    "(day, connector, org_id, classe, n, sample, origine) "
                    "VALUES (CURRENT_DATE, %s, %s, %s, %s, %s::jsonb, %s)",
                    (connector, int(org_id or 0), classe, int(n), payload, origine))
            return
        except psycopg.errors.UniqueViolation:
            pass
        # L'ANCIENNE clé primaire tient encore (la commande de migration n'a pas
        # tourné) : une ligne existe déjà pour ce jour, cette classe et cette org,
        # sous une AUTRE origine. On la crédite — les deux environnements se
        # partagent la ligne, exactement comme avant la colonne. Perdre le compte
        # serait pire que le mélanger.
        conn.execute(
            "UPDATE access_shadow_l7 SET n = n + %s, last_at = NOW() "
            "WHERE day = CURRENT_DATE AND connector = %s AND org_id = %s AND classe = %s",
            (int(n), connector, int(org_id or 0), classe))


def read_shadow(days: int = 7, connector: Optional[str] = None,
                classe: Optional[str] = None,
                origine: Optional[str] = None) -> list[dict]:
    """Les lignes des `days` derniers jours, les plus récentes d'abord.

    `origine` filtre sur l'environnement qui a écrit ; `None` = tout, **origine
    inconnue comprise** — c'est à l'appelant de dire ce qu'il compte, et la lentille,
    elle, se restreint à la prod pour son verdict.

    Pas de LIMIT : la population est bornée par (jours × connecteurs servis × orgs
    actives × classes × origines), et une fenêtre d'observation qu'on tronque ne
    prouve rien. L'appelant borne `days`."""
    sql = [f"SELECT {_COLONNES}", "  FROM access_shadow_l7",
           " WHERE day > CURRENT_DATE - %s::int"]
    args: list = [int(days)]
    if connector:
        sql.append(" AND connector = %s")
        args.append(connector)
    if classe:
        sql.append(" AND classe = %s")
        args.append(classe)
    if origine:
        sql.append(" AND origine = %s")
        args.append(origine)
    sql.append(" ORDER BY day DESC, connector, org_id, classe")
    with _connect() as conn:
        return [dict(r) for r in conn.execute("\n".join(sql), tuple(args)).fetchall()]
