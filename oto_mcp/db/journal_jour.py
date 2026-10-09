"""Les totaux du journal d'appels par jour UTC (oto-backend#1147) : consolider un jour.

Le journal (`tool_calls`, ~12 M lignes) est la source de vérité des exécutions ; les
écrans de consommation et de monitoring n'ont pas à le relire en entier à chaque vue
d'une fenêtre de 30 ou 90 jours. Ils lisent ici les TOTAUX des jours clos, et le journal
direct pour ce qui n'est pas consolidé (le jour courant, la veille avant le passage de
la maintenance, le morceau d'un jour qu'une fenêtre glissante coupe).

**Un jour consolidé est COMPLET.** `consolider_jour` recalcule un jour clos en UNE
transaction : il retire le jour du registre (les totaux et les jobs partent avec lui,
`ON DELETE CASCADE`), le réinscrit, pose ses totaux et ses jobs. Rejouer un jour ne
change rien (idempotent) ; une lecture concurrente voit l'ancien jour entier ou le neuf
entier, jamais un mélange. Le jour courant est REFUSÉ : il n'est pas clos.

**Les dimensions, et pas plus** : celles que lisent les écrans (`DIMENSIONS`). Les
mesures s'additionnent d'un jour à l'autre (`MESURES`), sauf deux familles, servies
autrement et EXACTEMENT :

- les **distincts** (comptes actifs, membres) : `sub` et `org_id` sont des dimensions,
  un `count(DISTINCT sub)` sur l'union des jours est donc exact ;
- les **jobs distincts** du relevé facturable : une table de CLÉS par jour
  (`journal_jobs_jour`), comptées distinctes sur l'union ;
- les **percentiles** (p95 des durées et des tailles) : les valeurs elles-mêmes, en
  tableau (`durees`, `tailles`, 4 octets par valeur) — `percentile_cont` sur leur union
  rend la même valeur qu'au journal, là où un histogramme à seaux l'aurait approchée.

Natures agrégées : `mcp` (les appels d'outils) et `connector` (les échecs de
résolution de connecteur) — `KINDS`. Le REST, le protocole et le transport restent au
journal : leurs lecteurs sont des vues de la plateforme, appelées à partir vers Grafana
(épic #1147), et le REST est le flux le plus volumineux.

**Mémoire bornée.** Un jour chargé fait ~270 000 lignes : l'agrégation passe par un TRI
(`enable_hashagg = off`, local à la transaction), qui déborde sur disque au-delà de
`work_mem`, plutôt que par une table de hachage qui tiendrait un tableau par groupe en
mémoire — la base est une nano de 4 Go, partagée, qui a déjà manqué de mémoire (04/10).
"""
from __future__ import annotations

import logging
from typing import Optional

from . import journal_calls
from ._conn import _connect

logger = logging.getLogger(__name__)

#: Les natures d'événement agrégées. Le reste du journal ne l'est pas (cf. docstring).
KINDS: tuple[str, ...] = ("mcp", "connector")

#: Les DIMENSIONS d'une ligne de totaux, et leur expression sur une ligne du journal
#: (alias `l`). Ordre = ordre des colonnes de `journal_totaux_jour`.
DIMENSIONS: dict[str, str] = {
    "jour": "(l.created_at AT TIME ZONE 'UTC')::date",
    "kind": "l.kind",
    "org_id": "l.org_id",
    "sub": "l.sub",
    "tool": "l.tool",
    "ok": "l.ok",
    "key_mode": "l.key_mode",
    # L'émetteur DÉCLARÉ (oto#187), tel que `tool_call_stats` le ventile.
    "client_name": f"l.args->'{journal_calls.ARGS_CLIENT_KEY}'->>'name'",
}

#: Les MESURES d'une ligne de totaux : leur agrégat sur les lignes du journal d'un même
#: groupe de dimensions. Les sommes ignorent NULL comme `SUM`/`AVG` ; `quantite` lit une
#: quantité absente comme 1, jamais 0 (commentaire DDL de `tool_calls.quantity`).
MESURES: dict[str, str] = {
    "appels": "count(*)::int",
    "quantite": "sum(COALESCE(l.quantity, 1))::bigint",
    "duree_n": "count(l.duration_ms)::int",
    "duree_somme": "COALESCE(sum(l.duration_ms), 0)::bigint",
    "durees": ("COALESCE(array_agg(l.duration_ms) FILTER "
               "(WHERE l.duration_ms IS NOT NULL), '{}')"),
    "taille_n": "count(l.result_size)::int",
    "taille_somme": "COALESCE(sum(l.result_size), 0)::bigint",
    "tailles": ("COALESCE(array_agg(l.result_size) FILTER "
                "(WHERE l.result_size IS NOT NULL), '{}')"),
    "dernier_at": "max(l.created_at)",
}

#: La borne d'un jour UTC `%s` (une date), en instant : son premier instant.
DEBUT_DU_JOUR = "(%s::date::timestamp AT TIME ZONE 'UTC')"

#: Durée maximale de la consolidation d'UN jour, par défaut (maintenance). Mesuré à
#: quelques secondes pour un jour chargé ; la borne coupe un jour pathologique sans
#: tenir la base, et le jour reste NON consolidé (la transaction est annulée).
DUREE_MAX_MS = 120_000

#: Au plus tant de jours rattrapés par un passage de la maintenance (une nuit manquée,
#: un week-end sans timer). Au-delà, c'est le script de rattrapage, lancé à la main.
MAINTENANCE_JOURS_MAX = 7

#: Verrou consultatif de la consolidation : deux consolidations du même jour (le timer et
#: un rattrapage à la main) se suivent au lieu de se croiser.
_VERROU = "hashtext('oto.journal_jour')"


class JourNonClos(ValueError):
    """Consolider le jour courant (ou un jour futur) : refusé, il n'est pas complet."""


def _projection(predicat: str, *, mesures: tuple[str, ...] = tuple(MESURES)) -> str:
    """Le SELECT qui agrège les lignes du journal satisfaisant `predicat` (alias `l`)
    par jour et par dimensions. UNE définition, partagée par la consolidation et par la
    lecture du journal direct (`source`) : les deux ne peuvent pas diverger."""
    dims = ",\n               ".join(f"{expr} AS {nom}" for nom, expr in DIMENSIONS.items())
    mes = ",\n               ".join(f"{MESURES[nom]} AS {nom}" for nom in mesures)
    rangs = ", ".join(str(i) for i in range(1, len(DIMENSIONS) + 1))
    return (f"SELECT {dims},\n               {mes}\n"
            f"          FROM tool_calls l\n"
            f"         WHERE {predicat}\n"
            f"         GROUP BY {rangs}")


def _expr_job() -> str:
    # Import différé : `usage` lit les totaux (et donc ce module) à son import.
    from .usage import _BILLABLE_JOB_ID_SQL
    return _BILLABLE_JOB_ID_SQL


def predicat_jobs(alias_ok: str = "l") -> str:
    """Ce qui fait d'une ligne du journal un job facturable relevé : la ligne de la
    lentille de facturation (`kind='mcp'`, `ok`, sous une org) qui nomme un job."""
    return (f"{alias_ok}.kind = 'mcp' AND {alias_ok}.ok AND {alias_ok}.org_id IS NOT NULL "
            f"AND {_expr_job()} IS NOT NULL")


def consolider_jour(jour: str, *, duree_max_ms: int = DUREE_MAX_MS) -> dict:
    """(Re)calcule les totaux et les jobs du jour UTC `jour` ('YYYY-MM-DD'), en UNE
    transaction bornée à `duree_max_ms`. Rend `{jour, lignes, totaux, jobs}`.

    Lève `JourNonClos` pour le jour courant ou un jour futur. Une borne dépassée lève
    l'annulation de PostgreSQL (`QueryCanceled`) : rien n'est écrit, le jour reste dans
    son état précédent (consolidé avant, ou absent du registre)."""
    debut = DEBUT_DU_JOUR
    predicat = (f"l.created_at >= {debut} AND l.created_at < "
                f"((%s::date + 1)::timestamp AT TIME ZONE 'UTC') "
                f"AND l.kind = ANY(%s)")
    params = (jour, jour, list(KINDS))
    with _connect() as conn, conn.transaction():
        conn.execute(f"SET LOCAL statement_timeout = {int(duree_max_ms)}")
        conn.execute("SET LOCAL enable_hashagg = off")
        conn.execute(f"SELECT pg_advisory_xact_lock({_VERROU})")
        clos = conn.execute(
            "SELECT %s::date < (now() AT TIME ZONE 'UTC')::date AS clos", (jour,)
        ).fetchone()["clos"]
        if not clos:
            raise JourNonClos(f"{jour} : le jour n'est pas clos (UTC), il ne se consolide pas.")
        conn.execute("DELETE FROM journal_jours_consolides WHERE jour = %s", (jour,))
        conn.execute("INSERT INTO journal_jours_consolides (jour, lignes) VALUES (%s, 0)",
                     (jour,))
        colonnes = ", ".join(list(DIMENSIONS) + list(MESURES))
        totaux = conn.execute(
            f"""
            WITH ins AS (
                INSERT INTO journal_totaux_jour ({colonnes})
                {_projection(predicat)}
                RETURNING appels
            )
            SELECT count(*) AS n, COALESCE(sum(appels), 0) AS lignes FROM ins
            """, params).fetchone()
        jobs = conn.execute(
            f"""
            WITH ins AS (
                INSERT INTO journal_jobs_jour (jour, org_id, tool, key_mode, job_id)
                SELECT DISTINCT {DIMENSIONS['jour']}, l.org_id, l.tool, l.key_mode,
                       {_expr_job()}
                  FROM tool_calls l
                 WHERE {predicat} AND {predicat_jobs()}
                RETURNING 1
            )
            SELECT count(*) AS n FROM ins
            """, params).fetchone()
        conn.execute(
            "UPDATE journal_jours_consolides SET lignes = %s, consolide_at = now() "
            "WHERE jour = %s", (int(totaux["lignes"]), jour))
    return {"jour": jour, "lignes": int(totaux["lignes"]), "totaux": int(totaux["n"]),
            "jobs": int(jobs["n"])}


def etat() -> dict:
    """Le registre en bref : `{premier, dernier, jours, hier, trous}` — `premier`/`dernier`
    les jours consolidés extrêmes (`None` si aucun), `jours` leur nombre, `hier` la veille
    UTC, `trous` le nombre de jours absents entre les deux extrêmes (0 attendu)."""
    with _connect() as conn:
        r = conn.execute(
            """
            SELECT min(jour) AS premier, max(jour) AS dernier, count(*) AS jours,
                   ((now() AT TIME ZONE 'UTC')::date - 1) AS hier
              FROM journal_jours_consolides
            """).fetchone()
    jours = int(r["jours"])
    trous = 0
    if jours:
        from datetime import date
        etendue = (date.fromisoformat(str(r["dernier"]))
                   - date.fromisoformat(str(r["premier"]))).days + 1
        trous = etendue - jours
    return {"premier": r["premier"], "dernier": r["dernier"], "jours": jours,
            "hier": r["hier"], "trous": trous}


def jours_a_consolider(*, du: Optional[str] = None, au: Optional[str] = None,
                       refaire: bool = False) -> list[str]:
    """Les jours à consolider sur `[du, au]` (défauts : le premier jour du journal, la
    veille UTC), dans l'ordre qui garde la couverture CONTIGUË : d'abord les jours
    après le dernier consolidé, en avançant ; puis ceux d'avant le premier, en
    reculant. Ainsi un rattrapage interrompu ne laisse jamais de trou que les lecteurs
    refuseraient. `refaire` reprend aussi les jours déjà consolidés (du plus récent au
    plus ancien)."""
    with _connect() as conn:
        r = conn.execute(
            """
            SELECT COALESCE(%s::date, (SELECT (min(created_at) AT TIME ZONE 'UTC')::date
                                         FROM tool_calls)) AS du,
                   LEAST(COALESCE(%s::date, (now() AT TIME ZONE 'UTC')::date - 1),
                         (now() AT TIME ZONE 'UTC')::date - 1) AS au
            """, (du, au)).fetchone()
        if r["du"] is None or str(r["du"]) > str(r["au"]):
            return []
        rows = conn.execute(
            """
            SELECT to_char(g.d, 'YYYY-MM-DD') AS jour,
                   EXISTS (SELECT 1 FROM journal_jours_consolides c
                            WHERE c.jour = g.d::date) AS consolide
              FROM generate_series(%s::date, %s::date, interval '1 day') AS g(d)
             ORDER BY g.d DESC
            """, (r["du"], r["au"])).fetchall()
        bornes = conn.execute(
            "SELECT to_char(min(jour), 'YYYY-MM-DD') AS premier, "
            "to_char(max(jour), 'YYYY-MM-DD') AS dernier FROM journal_jours_consolides"
        ).fetchone()
    if refaire:
        return [x["jour"] for x in rows]
    manquants = [x["jour"] for x in rows if not x["consolide"]]
    if bornes["dernier"] is None:
        return manquants                      # du plus récent au plus ancien
    apres = sorted(j for j in manquants if j > bornes["dernier"])
    avant = sorted((j for j in manquants if j < bornes["premier"]), reverse=True)
    # Un jour manquant ENTRE les deux extrêmes est un trou : on le comble d'abord.
    trous = sorted(j for j in manquants if bornes["premier"] < j < bornes["dernier"])
    return trous + apres + avant


def maintenance(*, dry_run: bool = False) -> dict:
    """Le passage quotidien (`oto-mcp maintenance journal-jour`) : consolide les jours
    clos qui suivent le dernier consolidé, la veille comprise, au plus
    `MAINTENANCE_JOURS_MAX`. Sur un registre VIDE, la veille seule : l'historique est
    l'affaire du rattrapage (`scripts/rattraper_journal_jour.py`), lancé à la main, pas
    d'une maintenance dans la fenêtre d'un timer.

    Un jour qui échoue arrête le passage (le suivant creuserait un trou) et lève : le
    travail est journalisé en échec, les lecteurs refuseront les fenêtres qui le
    couvrent dès qu'il aura deux jours, en nommant le geste qui le rattrape."""
    e = etat()
    if e["dernier"] is None:
        jours = [str(e["hier"])]
    else:
        jours = jours_a_consolider(du=_lendemain(str(e["dernier"])))
    plus = max(0, len(jours) - MAINTENANCE_JOURS_MAX)
    jours = jours[:MAINTENANCE_JOURS_MAX]
    if dry_run:
        return {"a_consolider": jours, "au_dela": plus}
    faits = []
    for jour in jours:
        faits.append(consolider_jour(jour))
    if plus:
        logger.error("journal-jour : %d jour(s) en retard au-delà de %d — lancer "
                     "scripts/rattraper_journal_jour.py", plus, MAINTENANCE_JOURS_MAX)
    return {"consolides": faits, "au_dela": plus}


def _lendemain(jour: str) -> str:
    from datetime import date, timedelta
    return (date.fromisoformat(jour) + timedelta(days=1)).isoformat()
