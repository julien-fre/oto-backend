"""Propriétés de connecteur SURCHARGEABLES en base — SQL seul (L6 pièce 2 c2).

La table `connector_settings` est posée par `db/schema/connectors.py` ; ce module est
son unique lecteur/écrivain. Il ne porte AUCUNE politique : qui a le droit de poser une
surcharge est une affaire d'autorisation (`capabilities/`), et ce qu'une surcharge
SIGNIFIE est une affaire de `connectors.cardinality`. Ici, des requêtes.

⚠️ **Personne ne lit cette table à l'appel.** La lecture se fait au boot et sur
rechargement explicite ; le chemin chaud lit un dictionnaire en mémoire
(`connectors.cardinality`). Une lecture par appel serait le mode de panne que
`docs/event-loop-perf.md` documente — la cardinalité est consultée jusqu'à quatre fois
par appel d'outil, sur un serveur mono-loop.
"""
from __future__ import annotations

from typing import Optional

from ._conn import _connect

# Le scope « toute la plateforme ». Convention maison — `platform` n'a pas d'id, comme
# `guides.owner_id` et `grants.grantor_id`.
PLATFORM_SCOPE_ID = "platform"


def list_connector_settings(key: Optional[str] = None, conn=None) -> list[dict]:
    """Toutes les surcharges (ou celles d'une `key`). La table se lit ENTIÈREMENT :
    elle porte une poignée de lignes, et son seul lecteur en veut l'intégralité pour
    en faire un dictionnaire en mémoire."""
    sql = ("SELECT scope_type, scope_id, connector, key, value, set_by, set_at "
           "FROM connector_settings")
    params: tuple = ()
    if key is not None:
        sql += " WHERE key = %s"
        params = (key,)
    sql += " ORDER BY scope_type, scope_id, connector, key"
    if conn is not None:
        return [dict(r) for r in conn.execute(sql, params).fetchall()]
    with _connect() as c:
        return [dict(r) for r in c.execute(sql, params).fetchall()]


def set_connector_setting(scope_type: str, scope_id: str, connector: str, key: str,
                          value: str, set_by: Optional[str] = None) -> None:
    """Pose ou remplace une surcharge. Ne valide NI le scope NI la valeur : le CHECK
    de la table ferme le vocabulaire de scope, et le sens de `value` appartient au
    module qui la lit — la valider ici en ferait un second domicile."""
    with _connect() as conn:
        conn.execute(
            "INSERT INTO connector_settings "
            "(scope_type, scope_id, connector, key, value, set_by) "
            "VALUES (%s, %s, %s, %s, %s, %s) "
            "ON CONFLICT (scope_type, scope_id, connector, key) DO UPDATE SET "
            "value = EXCLUDED.value, set_by = EXCLUDED.set_by, set_at = NOW()",
            (scope_type, str(scope_id), connector, key, value, set_by))


def clear_connector_setting(scope_type: str, scope_id: str, connector: str,
                            key: str) -> bool:
    """Retire une surcharge — la propriété retombe sur le défaut du registre.

    Un vrai DELETE, et c'est la seule table de ce lot où c'en est un : une surcharge
    n'est pas un objet qu'on désigne (aucun binding, aucune arête ne la nomme), c'est
    un réglage. L'archiver ne servirait qu'à garder un réglage mort dans les lectures."""
    with _connect() as conn:
        cur = conn.execute(
            "DELETE FROM connector_settings WHERE scope_type = %s AND scope_id = %s "
            "AND connector = %s AND key = %s",
            (scope_type, str(scope_id), connector, key))
    return (cur.rowcount or 0) > 0
