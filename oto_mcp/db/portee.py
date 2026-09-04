"""Le journal des élargissements de portée (ADR 0068 §4) — écriture et lecture.

Une ligne = un moment où un **agent** a fait sortir un contenu du périmètre de son
propriétaire. Le DDL vit dans `db/schema/portee.py` ; ici, les deux gestes qu'on fait
dessus.

⚠️ **Période d'OBSERVATION (décision d'Alexis, 04/09/2026) : rien ne part.** Chaque
ligne porte les destinataires qu'elle AURAIT prévenus et l'urgence qu'elle aurait eue ;
`notifie_at` reste NULL. On veut d'abord mesurer combien d'alertes partiraient, et à
qui, avant d'en envoyer une seule — un canal qu'on ouvre en devinant son volume est un
canal qu'on referme au bout d'une semaine, et qui aura appris à ses destinataires à
l'ignorer.

⚠️ **L'écriture ne casse jamais le geste qu'elle observe.** Un enregistrement qui échoue
n'a pas à faire échouer un partage légitime : la trace est best-effort, comme la
notification qu'elle prépare. Le silence est journalisé (`logger.warning`), jamais avalé
— `scripts/lint_silences.py` l'exigerait de toute façon.
"""
from __future__ import annotations

import json
import logging
from typing import Any, Optional

from ._conn import _connect

logger = logging.getLogger(__name__)


def enregistrer_elargissement(
    *,
    acteur_sub: str,
    org_id: Optional[int],
    ressource_type: str,
    ressource_id: str,
    ressource_nom: Optional[str],
    proprietaire_sub: Optional[str],
    vers: str,
    cible: Optional[str],
    geste: str,
    destinataires: list[str],
    immediat: bool,
) -> Optional[int]:
    """Écrit la ligne d'observation. Rend son id, ou None si l'écriture a échoué.

    Rendre `None` plutôt que lever : l'appelant est un handler de partage en train de
    réussir, et il n'a rien à faire de cette panne-là. Mais il l'apprend — un `None`
    silencieux qu'on ignore, c'est une observation dont on croira les chiffres."""
    try:
        with _connect() as conn:
            row = conn.execute(
                """INSERT INTO portee_elargissements
                     (acteur_sub, org_id, ressource_type, ressource_id, ressource_nom,
                      proprietaire_sub, vers, cible, geste, destinataires, immediat)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s)
                   RETURNING id""",
                (acteur_sub, org_id, ressource_type, str(ressource_id), ressource_nom,
                 proprietaire_sub, vers, cible, geste,
                 json.dumps(sorted(set(destinataires))), bool(immediat)),
            ).fetchone()
            return int(row["id"]) if row else None
    except Exception as e:                                  # noqa: BLE001
        logger.warning("portee: enregistrement impossible (%s %s) : %s",
                       ressource_type, ressource_id, e)
        return None


def list_elargissements(*, limit: int = 100, proprietaire_sub: Optional[str] = None,
                        depuis: Optional[str] = None) -> list[dict[str, Any]]:
    """La lecture d'observation : « qu'est-ce qui serait parti, et à qui ».

    `depuis` est un littéral de date ISO borné par l'appelant, jamais une expression
    SQL : il part en paramètre, pas en concaténation."""
    where, args = [], []
    if proprietaire_sub:
        where.append("proprietaire_sub = %s")
        args.append(proprietaire_sub)
    if depuis:
        where.append("created_at >= %s::timestamptz")
        args.append(depuis)
    clause = (" WHERE " + " AND ".join(where)) if where else ""
    args.append(max(1, min(int(limit), 1000)))
    with _connect() as conn:
        return list(conn.execute(
            "SELECT id, acteur_sub, org_id, ressource_type, ressource_id, "
            "       ressource_nom, proprietaire_sub, vers, cible, geste, "
            "       destinataires, immediat, notifie_at, created_at "
            f"FROM portee_elargissements{clause} "
            "ORDER BY created_at DESC LIMIT %s", tuple(args)).fetchall())


def compter_par_vers(*, depuis: Optional[str] = None) -> list[dict[str, Any]]:
    """Le comptage qui décide si on ouvre le canal : combien de messages partiraient,
    par nature d'élargissement, et combien de personnes distinctes seraient écrites.

    C'est LE chiffre de la période d'observation. Le volume brut ne suffit pas :
    trente élargissements vers une seule personne ne font pas le même produit que
    trente élargissements vers trente personnes."""
    clause, args = ("", [])
    if depuis:
        clause, args = " WHERE created_at >= %s::timestamptz", [depuis]
    with _connect() as conn:
        return list(conn.execute(
            "SELECT vers, immediat, count(*) AS n, "
            "       count(DISTINCT proprietaire_sub) AS proprietaires "
            f"FROM portee_elargissements{clause} "
            "GROUP BY vers, immediat ORDER BY n DESC", tuple(args)).fetchall())
