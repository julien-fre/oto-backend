#!/usr/bin/env python3
"""Retire les orgs perso existantes : l'étiquette tombe, les vides s'archivent.

Plus aucune org perso ne naît depuis le lot « plus d'org perso » (05/11/2026,
`org_store/personal.py`). Celles qui existent gardent `orgs.personal_of` tant que ce
geste n'est pas joué. Il fait, dans cet ordre :

1. **ranger** — un projet ou un tableau perso SANS org de création
   (`context_org_id` nul) se listait dans l'org perso de son propriétaire. Il y est
   rangé (`context_org_id` = cette org), pour qu'il reste listé là où on le voyait ;
2. **marquer** — chaque org perso devient une org ordinaire posée par l'inscription
   (`auto_created = TRUE`) : elle cède toujours la place d'org active à une org
   rejointe et reste hors quota, comme avant ;
3. **archiver** (option `--archiver`) les orgs perso VIDES : seul membre, ni projet
   (hors « Découverte »), ni tableau, ni procédure, ni clé, ni compte connecté, ni
   abonnement — et dont le propriétaire a au moins une AUTRE org vivante (sans elle,
   l'archiver lui en reposerait aussitôt une neuve) ;
4. **vider l'étiquette** — `personal_of = NULL` partout.

⚠️ Lancé À LA MAIN, APRÈS le déploiement du lot (avant, archiver l'unique org d'un
compte ou rebooter lui recrée une org perso). **À blanc par défaut** : sans
`--appliquer`, il liste ce qu'il ferait et n'écrit rien. Idempotent.

    ./.venv/bin/python -m scripts.retirer_orgs_perso                 # rapport
    ./.venv/bin/python -m scripts.retirer_orgs_perso --appliquer     # 1, 2, 4
    ./.venv/bin/python -m scripts.retirer_orgs_perso --appliquer --archiver   # + 3
"""
from __future__ import annotations

import argparse
import sys
from typing import Optional

from oto_mcp import discovery, org_store
from oto_mcp.db._conn import _connect_autocommit

_PERSO = "SELECT id, name, personal_of FROM orgs WHERE personal_of IS NOT NULL AND archived_at IS NULL ORDER BY id"

# Une org perso VIDE, archivable sans rien perdre ni laisser son propriétaire sans org.
_VIDES = """
SELECT o.id, o.name, o.personal_of FROM orgs o
 WHERE o.personal_of IS NOT NULL AND o.archived_at IS NULL
   AND NOT EXISTS (SELECT 1 FROM org_members m WHERE m.org_id = o.id AND m.sub <> o.personal_of)
   AND EXISTS (SELECT 1 FROM org_members m JOIN orgs o2 ON o2.id = m.org_id
                WHERE m.sub = o.personal_of AND o2.id <> o.id AND o2.archived_at IS NULL)
   AND NOT EXISTS (SELECT 1 FROM projects p
                    WHERE ((p.owner_type = 'org' AND p.owner_id = o.id::text)
                           OR p.context_org_id = o.id
                           OR (p.owner_type = 'user' AND p.owner_id = o.personal_of
                               AND p.context_org_id IS NULL))
                      AND p.name <> %(decouverte)s AND p.archived_at IS NULL)
   AND NOT EXISTS (SELECT 1 FROM user_datastores d
                    WHERE (d.owner_type = 'org' AND d.owner_id = o.id::text)
                       OR d.context_org_id = o.id
                       OR (d.owner_type = 'user' AND d.owner_id = o.personal_of
                           AND d.context_org_id IS NULL))
   AND NOT EXISTS (SELECT 1 FROM org_instructions i
                    WHERE i.owner_type = 'org' AND i.owner_id = o.id::text)
   AND NOT EXISTS (SELECT 1 FROM connector_credentials c
                    WHERE (c.entity_type = 'org' AND c.entity_id = o.id::text)
                       OR (c.entity_type = 'member' AND c.entity_id LIKE o.id::text || ':%%'))
   AND NOT EXISTS (SELECT 1 FROM unipile_accounts a WHERE a.org_id = o.id)
 ORDER BY o.id
"""


def _rapport(conn) -> tuple[list[dict], list[dict], int, int]:
    perso = [dict(r) for r in conn.execute(_PERSO).fetchall()]
    vides = [dict(r) for r in conn.execute(_VIDES, {"decouverte": discovery.PROJECT_NAME}).fetchall()]
    projets = conn.execute(
        "SELECT count(*) AS n FROM projects p JOIN orgs o ON o.personal_of = p.owner_id "
        " WHERE p.owner_type = 'user' AND p.context_org_id IS NULL AND o.archived_at IS NULL"
    ).fetchone()["n"]
    tableaux = conn.execute(
        "SELECT count(*) AS n FROM user_datastores d JOIN orgs o ON o.personal_of = d.owner_id "
        " WHERE d.owner_type = 'user' AND d.context_org_id IS NULL AND o.archived_at IS NULL"
    ).fetchone()["n"]
    return perso, vides, projets, tableaux


def main(argv: Optional[list[str]] = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--appliquer", action="store_true", help="écrire (sinon : à blanc)")
    p.add_argument("--archiver", action="store_true", help="archiver aussi les orgs perso vides")
    a = p.parse_args(argv)

    with _connect_autocommit() as conn:
        perso, vides, projets, tableaux = _rapport(conn)
    print(f"{len(perso)} org(s) perso vivante(s)")
    print(f"{projets} projet(s) et {tableaux} tableau(x) perso sans org de création à ranger")
    print(f"{len(vides)} org(s) perso VIDE(S) archivable(s) :")
    for o in vides:
        print(f"  org {o['id']:>5}  « {o['name']} »  (compte {o['personal_of']})")
    if not a.appliquer:
        print("\nÀ blanc : rien écrit. --appliquer pour écrire.")
        return 0

    with _connect_autocommit() as conn:
        n1 = conn.execute(
            "UPDATE projects p SET context_org_id = o.id FROM orgs o "
            " WHERE o.personal_of = p.owner_id AND o.archived_at IS NULL "
            "   AND p.owner_type = 'user' AND p.context_org_id IS NULL").rowcount
        n2 = conn.execute(
            "UPDATE user_datastores d SET context_org_id = o.id FROM orgs o "
            " WHERE o.personal_of = d.owner_id AND o.archived_at IS NULL "
            "   AND d.owner_type = 'user' AND d.context_org_id IS NULL").rowcount
        n3 = conn.execute(
            "UPDATE orgs SET auto_created = TRUE WHERE personal_of IS NOT NULL").rowcount
    print(f"\nrangés : {n1} projet(s), {n2} tableau(x) ; marquées auto_created : {n3}")

    archivees = 0
    if a.archiver:
        for o in vides:
            try:
                if org_store.archive_org(int(o["id"])):
                    archivees += 1
                    print(f"  archivée org {o['id']}")
            except Exception as e:  # abonnement actif, etc. : on la garde, on le dit
                print(f"  GARDÉE org {o['id']} : {type(e).__name__}: {e}")

    with _connect_autocommit() as conn:
        n4 = conn.execute("UPDATE orgs SET personal_of = NULL WHERE personal_of IS NOT NULL").rowcount
    print(f"archivées : {archivees} ; étiquettes vidées : {n4}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
