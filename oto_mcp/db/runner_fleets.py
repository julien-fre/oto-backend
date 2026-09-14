"""Les flottes du runner — la configuration déclarée d'un passage (chantier R4).

Le module stocke la CONFIG et rend l'ÉTAT ; il ne lance rien et ne juge rien.
L'état est un agrégat sur `runner_jobs` rattachés par `fleet_id` : c'est ce qui
rend un passage lisible d'un bout à l'autre sans corréler des horodatages à la
main, et sans qu'une session ait à pousser des messages à une autre.

⚠️ **Un zéro se distingue d'un « personne n'a regardé ».** Un passage sans aucun
travail rattaché ne rend pas des compteurs à zéro — il rend `jobs_total: 0`, et
l'appelant sait que le vide est constaté et non déduit. Un zéro qui peut vouloir
dire « rien trouvé » ou « rien de mesurable » est le défaut le plus coûteux qu'on
ait payé sur ce chantier.

⚠️ **Tout compteur servi est CASTÉ en entier.** Une agrégation PostgreSQL rend
volontiers un `numeric` là où on attend un entier (`SUM` sur un bigint), donc un
`Decimal` que JSON refuse — et la réponse entière part en 500. Le défaut ne se
voit pas en lisant un modèle : il faut sérialiser une vraie réponse
(`tests/api/test_runner_fleets_rest.py`).

⚠️ **Le coût est rendu en JETONS, jamais en monnaie.** Les tarifs changent, ils
diffèrent par fournisseur, et une valeur monétaire figée en base devient fausse
sans que rien ne le dise. Ce qui est mesuré ici est ce que le worker a déclaré ;
la conversion appartient à qui lit, avec un tarif daté.
"""
from __future__ import annotations

import json
from typing import Any, Callable, Optional

from ._conn import _connect

# ⚠️ `rows_at_launch` n'est plus projetée (13/09/2026) : la colonne reste en base, sans
# lecteur ni écrivain. Chaque verbe de la capacité rend la ligne telle que ce SELECT la
# produit — son modèle `Output` DÉCRIT la réponse, il ne la filtre pas —, donc la retirer
# d'ici est la seule façon de cesser de la servir sur tous les verbes à la fois.
_COLS = ("id, org_id, sub, label, procedure, project_id, tools, input, max_steps, "
         "namespace, row_filter, provider, model, temperature, workers, max_rows, "
         "max_tokens, max_consecutive_failures, max_tokens_per_row, status, stop_reason, "
         "armed_at, started_at, stopping_at, heartbeat_at, stopped_at, created_at")

# Ce qu'un passage a le droit de changer une fois déclaré. La CIBLE n'en est pas :
# rediriger un passage en vol vers un autre tableau est exactement le geste que la
# configuration déclarée existe pour empêcher — on en déclare un autre.
# ⚠️ `provider`/`model`/`temperature` n'en sont PAS, pour la raison exacte qui
# gèle la cible : changer le contexte d'exécution en vol rend FAUSSE
# l'attribution des lignes déjà écrites sous le passage. Deux lignes du même
# passage écrites à deux températures ne sont pas comparables, et rien dans la
# donnée ne dirait laquelle vient de quel régime. Le contexte d'exécution est
# aussi peu mutable que ce qu'il vise.
# `status` non plus : il se change par les gestes d'état, jamais par une retouche
# de configuration — un `update` qui l'accepterait rendrait 200 sans rien faire.
CHAMPS_MODIFIABLES = ("label", "tools", "input", "max_steps", "workers", "max_rows",
                      "max_tokens", "max_consecutive_failures", "max_tokens_per_row")


def create_fleet(org_id: int, sub: str, *, label: str, procedure: str,
                 tools: list, namespace: Optional[str] = None,
                 row_filter: Optional[dict] = None, project_id: Optional[int] = None,
                 input: Optional[str] = None, max_steps: Optional[int] = None,
                 provider: Optional[str] = None, model: Optional[str] = None,
                 temperature: Optional[float] = None,
                 workers: int = 1, max_rows: Optional[int] = None,
                 max_tokens: Optional[int] = None,
                 max_consecutive_failures: Optional[int] = None,
                 max_tokens_per_row: Optional[int] = None) -> dict:
    with _connect() as conn:
        row = conn.execute(
            f"""
            INSERT INTO runner_fleets
                   (org_id, sub, label, procedure, project_id, tools, input,
                    max_steps, namespace, row_filter, provider, model, temperature, workers,
                    max_rows, max_tokens, max_consecutive_failures,
                    max_tokens_per_row)
            VALUES (%s, %s, %s, %s, %s, %s::jsonb, %s, %s, %s, %s::jsonb, %s, %s,
                    %s, %s, %s, %s, %s, %s)
            RETURNING {_COLS}
            """,
            (org_id, sub, label, procedure, project_id,
             json.dumps(list(tools), ensure_ascii=False), input, max_steps,
             namespace,
             json.dumps(row_filter, ensure_ascii=False) if row_filter is not None else None,
             provider, model, temperature, workers, max_rows, max_tokens,
             max_consecutive_failures, max_tokens_per_row),
        ).fetchone()
    return dict(row)


def list_fleets(org_id: int, statut: Optional[str] = None) -> list[dict]:
    ou, args = "WHERE org_id = %s", [org_id]
    if statut:
        ou += " AND status = %s"
        args.append(statut)
    with _connect() as conn:
        rows = conn.execute(
            f"SELECT {_COLS} FROM runner_fleets {ou} ORDER BY id DESC", tuple(args),
        ).fetchall()
    return [dict(r) for r in rows]


# Espace de verrous consultatifs propre aux campagnes : `pg_try_advisory_xact_lock`
# prend deux entiers, et le premier isole la famille — sans lui, l'id 12 d'une
# campagne collisionnerait avec l'id 12 de n'importe quel autre verrou du produit.
_VERROU_CAMPAGNE = 0x0704_0C41   # « oto campagne »


# Les critères d'ÉLIGIBILITÉ de `campagne_a_servir`, hors verrou — partagés par la
# lecture (sans verrou, pour choisir QUI tirer) et le pick (avec verrou, sur la
# candidate tirée). Toucher l'un sans l'autre les ferait diverger en silence.
_ELIGIBLE = ("f.status IN ('armed', 'running') "
            "AND NOT EXISTS (SELECT 1 FROM runner_jobs j "
            "                 WHERE j.fleet_id = f.id AND j.status = 'pending') "
            "AND (f.max_rows IS NULL "
            "     OR (SELECT COUNT(*) FROM runner_jobs j2 "
            "          WHERE j2.fleet_id = f.id) < f.max_rows)")


def campagne_a_servir(org_id: Optional[int],
                      ordonner: Callable[[list[dict]], list[int]]) -> Optional[dict]:
    """La campagne de l'org pour laquelle il faut produire un travail — ou None.

    ⚠️ C'est le cœur du modèle « oto décide, le runner demande ». Un worker ne
    connaît pas la notion de campagne : il demande du travail, et c'est ICI
    qu'on décide s'il y en a un à fabriquer. Rien ne tourne côté runner pour
    dérouler un passage ; l'état du passage EST la règle qui produit ses
    travaux, évaluée à chaque sondage.

    Ce que « à servir » exige, dans l'ordre où ça se refuse :

    - un statut qui accepte de produire (`armed` = demandée, `running` = déjà
      commencée). `stopping` en est exclu : un arrêt demandé ne doit plus rien
      engager, c'est toute la raison d'être de cet état ;
    - **aucun travail déjà en attente** pour elle. C'est la régulation, et elle
      est passive : N workers qui sondent obtiennent au plus N travaux en vol,
      sans qu'aucun réglage ne le dise. Un `pending` non consommé signifie que
      le parc est déjà servi ; en produire un second ne ferait qu'allonger une
      file que personne ne tire plus vite.
    - la borne de lignes (`max_rows`), comptée sur les travaux DÉJÀ produits
      pour ce passage. Appliquée ici, elle ne se contourne pas : il n'existe
      plus d'autre chemin pour enfiler.

    ⚠️ **La FILE se juge ailleurs : `ordonner` rend les candidates à tenter, dans
    l'ordre** (couche capacités, `capabilities/_ordre_de_service.py` — ce module
    reste ignorant du datastore). Trois régimes se sont succédé, chacun né du défaut
    du précédent :

    - jusqu'au 13/09/2026, la plus ancienne armée d'abord, sans regarder la file :
      une chaîne de passes armée d'un coup voyait tout le pool converger sur la
      première, même VIDÉE (860 travaux à vide, 27 min de chaîne figée) ;
    - le 13/09, un tirage au hasard à chance égale : plus de monopole, mais une file
      de 171 lignes tirée une fois sur cinq, comme une file vide qui fabriquait des
      travaux à vide — la passe du milieu est devenue le goulot ;
    - depuis le 14/09, `ordonner` écarte les campagnes sans ligne réservable et
      pondère le tirage par la file, avec un plancher contre la famine. Le compte
      coûte un scan par tableau, gardé 15 s (`_lignes_reservables`).

    ⚠️ **Pourquoi DEUX requêtes, et pas `ORDER BY random()`.** Le verrou est
    dans le `WHERE` : avec l'ancien tri déterministe, Postgres pouvait s'arrêter
    au premier candidat verrouillable sans toucher aux autres (scan dans l'ordre
    de l'index, LIMIT 1 court-circuite). Un tri aléatoire n'a pas cet appui — pour
    trier, Postgres doit d'abord matérialiser TOUTES les lignes qui passent le
    `WHERE`, donc appeler `pg_try_advisory_xact_lock` sur CHAQUE éligible, pas
    seulement la gagnante : autant de verrous pris pour rien à chaque sondage, et
    autant de contention en plus pour les workers concurrents qui visaient une
    AUTRE flotte au même instant. D'où la séparation : la première requête ne
    verrouille rien (elle ne fait que LIRE qui est éligible), l'ordre se décide hors
    SQL (`ordonner`), et seule la candidate tentée est verrouillée — une par
    tentative, jamais toutes."""
    with _connect() as conn:
        candidates = [dict(c) for c in conn.execute(
            f"""
            SELECT {_COLS}
              FROM runner_fleets f
             -- Org nulle = worker de PLATEFORME : n'importe quelle campagne en
             -- cours de n'importe quelle org. (Pas de marqueur de paramètre
             -- dans ce commentaire : psycopg les compte AUSSI ici.)
             WHERE (%s::bigint IS NULL OR f.org_id = %s)
               AND {_ELIGIBLE}
            """,
            (org_id, org_id),
        ).fetchall()]
    if not candidates:
        return None
    # Hors de la connexion ci-dessus : le compte des files ouvre les siennes, et
    # tenir celle-ci pendant ce temps priverait le pool d'une connexion pour rien.
    ordre = ordonner(candidates)
    with _connect() as conn:
        # ⚠️ VERROU par campagne, le temps de la transaction. Sans lui, deux
        # workers qui sondent au même instant lisent tous deux « aucun travail
        # en attente » et en fabriquent chacun un : la régulation passive, qui
        # repose entièrement sur cette lecture, serait contournée par la course
        # qu'elle est censée borner. `try` et non bloquant — un worker qui
        # arrive pendant qu'un autre produit n'attend pas, il repart les mains
        # vides et re-sondera : c'est un sondage, pas une file d'attente.
        conn.execute("SET LOCAL lock_timeout = '200ms'")
        for fid in ordre:
            # Réévalue L'ÉLIGIBILITÉ ENTIÈRE, pas seulement l'existence : entre
            # la lecture ci-dessus et cette tentative, un autre sondage a pu
            # produire un travail pour cette flotte (elle n'a alors plus sa
            # place) — un verrou pris sur un id qui ne serait plus éligible
            # rendrait quand même la ligne, silencieusement faux.
            row = conn.execute(
                f"""
                SELECT {_COLS}
                  FROM runner_fleets f
                 -- `f.id::int` et non `f.id` : la colonne est BIGSERIAL, et
                 -- Postgres n'offre que `(bigint)` ou `(int, int)` — jamais
                 -- `(int, bigint)`. Sans le cast, la requête LÈVE
                 -- `UndefinedFunction`, le `try` de l'appelant l'avale, et le
                 -- sondage rend « aucun travail » pour toujours : mesuré le
                 -- 07/09/2026 sur le canari, la production de travail n'avait
                 -- jamais pu s'exécuter une seule fois.
                 WHERE f.id = %s AND pg_try_advisory_xact_lock(%s, f.id::int)
                   AND {_ELIGIBLE}
                """,
                (fid, _VERROU_CAMPAGNE),
            ).fetchone()
            if row is not None:
                return row
        return None


def accuser_arrets_effectifs(org_id: Optional[int]) -> list[int]:
    """`stopping` → `stopped` pour les campagnes dont plus AUCUN travail ne tourne.

    ⚠️ Ce geste appartenait à l'ordonnanceur (`op=ack_stop`), et l'écart entre
    « arrêt demandé » et « arrêt effectif » était le seul diagnostic d'un
    ordonnanceur mort. Le renversement a supprimé l'ordonnanceur : **plus
    personne n'accuse**, et l'écart ne diagnostique plus rien — il ne se referme
    jamais. Mesuré le 07/09/2026 : une campagne arrêtée restait `stopping`
    indéfiniment, c'est-à-dire qu'un arrêt demandé n'était jamais un fait.

    Ce qu'on garde de l'ancienne règle, et qui est l'essentiel : `stopped` reste
    un FAIT CONSTATÉ, jamais une intention recopiée. Le fait, ici, est
    vérifiable sans rien juger du travail — aucun travail `pending` ni `claimed`
    ne subsiste, donc le passage ne tourne plus. Un arrêt demandé pendant qu'un
    agent travaille encore attend sa fin, exactement comme avant.

    Rend les ids accusés, pour que l'appelant puisse le journaliser."""
    with _connect() as conn:
        lignes = conn.execute(
            """
            UPDATE runner_fleets f
               SET status = 'stopped', stopped_at = NOW()
             WHERE (%s::bigint IS NULL OR f.org_id = %s) AND f.status = 'stopping'
               AND NOT EXISTS (SELECT 1 FROM runner_jobs j
                                WHERE j.fleet_id = f.id
                                  AND j.status IN ('pending', 'claimed'))
            RETURNING f.id
            """,
            (org_id, org_id)).fetchall()
    return [int(r["id"]) for r in lignes]


def arreter_campagnes_epuisees(org_id: Optional[int]) -> list[int]:
    """Arrête les campagnes dont les N derniers travaux ont TOUS échoué.

    ⚠️ Cette borne comptait avant, mais elle était portée par un ordonnanceur
    qu'un humain lançait — et qui s'arrêtait donc de lui-même quand personne ne
    le relançait. Maintenant que le sondage des workers fait avancer un passage,
    **plus rien ne s'arrête tout seul** : une campagne qui échoue en boucle
    régénérerait du travail indéfiniment, la nuit, sans que personne regarde.
    C'est la combinaison qui coûte le plus cher — augmenter la cadence en
    retirant le garde-fou.

    ⚠️ Elle ARRÊTE, elle ne se contente pas d'exclure. Une campagne qu'on cesse
    de servir sans le dire reste `running` et n'avance plus : un état qui ment,
    et le genre de silence qu'on découvre trois semaines plus tard. Le motif est
    écrit dans `stop_reason`, à l'endroit où on le cherchera.

    Rend les ids arrêtés — pour que l'appelant puisse le journaliser.
    """
    with _connect() as conn:
        rows = conn.execute(
            """
            UPDATE runner_fleets f
               SET status = 'stopped', stopped_at = NOW(),
                   stop_reason = 'max_consecutive_failures'
             WHERE (%s::bigint IS NULL OR f.org_id = %s)
               AND f.status IN ('armed', 'running')
               AND f.max_consecutive_failures IS NOT NULL
               AND (SELECT COUNT(*) FROM (
                        SELECT j.status FROM runner_jobs j
                         WHERE j.fleet_id = f.id AND j.status IN ('done', 'failed')
                         ORDER BY j.id DESC
                         LIMIT f.max_consecutive_failures) d
                     WHERE d.status = 'failed') >= f.max_consecutive_failures
            RETURNING f.id
            """,
            (org_id, org_id),
        ).fetchall()
    return [r["id"] for r in rows]


def marquer_demarree(fleet_id: int) -> None:
    """`armed` → `running` au PREMIER travail produit — et seulement là.

    L'état ne dit plus « un ordonnanceur m'a prise » (il n'y en a plus), il dit
    « j'ai commencé à produire ». C'est la même information pour qui regarde un
    écran, et elle ne dépend plus d'un processus qui doit se déclarer vivant.
    """
    with _connect() as conn:
        conn.execute(
            "UPDATE runner_fleets SET status = 'running', started_at = NOW() "
            " WHERE id = %s AND status = 'armed'",
            (fleet_id,),
        )


def get_fleet(fleet_id: int, org_id: int) -> Optional[dict]:
    with _connect() as conn:
        row = conn.execute(
            f"SELECT {_COLS} FROM runner_fleets WHERE id = %s AND org_id = %s",
            (fleet_id, org_id),
        ).fetchone()
    return dict(row) if row else None


def update_fleet(fleet_id: int, org_id: int, champs: dict[str, Any]) -> Optional[dict]:
    champs = {c: v for c, v in champs.items() if c in CHAMPS_MODIFIABLES}
    if not champs:
        return get_fleet(fleet_id, org_id)
    sets, args = [], []
    for c, v in champs.items():
        if c == "tools":
            sets.append(f"{c} = %s::jsonb")
            args.append(json.dumps(list(v), ensure_ascii=False))
        else:
            sets.append(f"{c} = %s")
            args.append(v)
    args += [fleet_id, org_id]
    with _connect() as conn:
        row = conn.execute(
            f"UPDATE runner_fleets SET {', '.join(sets)} "
            f"WHERE id = %s AND org_id = %s RETURNING {_COLS}",
            tuple(args),
        ).fetchone()
    return dict(row) if row else None


def set_status(fleet_id: int, org_id: int, statut: str,
               raison: Optional[str] = None) -> Optional[dict]:
    """Change l'état, et ÉCRIT la raison quand le passage s'arrête.

    ⚠️ `stop_reason` n'est jamais déduit d'un statut : « arrêtée » sans raison
    oblige l'opérateur à rouvrir les journaux pour savoir si le budget a coupé, si
    la file s'est vidée, ou si un outil est tombé.
    """
    horodatage = {"running": "started_at = NOW(), heartbeat_at = NOW()",
                  "stopped": "stopped_at = NOW()",
                  "done": "stopped_at = NOW()",
                  "failed": "stopped_at = NOW()"}.get(statut)
    sets = ["status = %s", "stop_reason = %s"]
    args: list[Any] = [statut, raison]
    if horodatage:
        sets.append(horodatage)
    args += [fleet_id, org_id]
    with _connect() as conn:
        row = conn.execute(
            f"UPDATE runner_fleets SET {', '.join(sets)} "
            f"WHERE id = %s AND org_id = %s RETURNING {_COLS}",
            tuple(args),
        ).fetchone()
    return dict(row) if row else None


# ── Les gestes d'ÉTAT, nommés — et la transition qu'ils exigent ──────────────
# ⚠️ Chacun est CONDITIONNEL sur l'état de départ, dans le même UPDATE. Sans ça,
# deux appels concurrents (un opérateur et un ordonnanceur) écriraient l'un sur
# l'autre, et le dernier gagnerait — y compris pour ressusciter un passage arrêté.
# Rendre `False` quand la transition n'était pas permise laisse l'appelant DIRE
# qu'il n'a rien changé, au lieu de croire qu'il a agi.

def armer(fleet_id: int, org_id: int) -> Optional[dict]:
    """`draft`/`stopped`/`done`/`failed` → `armed` : on DEMANDE que ça tourne.

    ⚠️ Ce n'est PAS `running`. Une intention déclarée et un fait constaté ne
    partagent jamais une colonne : `running` veut dire qu'un ordonnanceur l'a
    PRISE et donne signe. Une flotte armée que personne n'a réclamée doit se lire
    « armée, personne ne l'a prise » — pas « en cours ».

    ⚠️ N'écrit plus `rows_at_launch` (13/09/2026). Ce compte ne voyait que le
    `row_filter` de la flotte — ni le périmètre déclaré du tableau, ni les baux — et
    annonçait du travail qu'aucune réservation ne servait. La colonne garde, sans
    lecteur, la valeur posée par un armement antérieur.
    """
    with _connect() as conn:
        row = conn.execute(
            f"UPDATE runner_fleets SET status = 'armed', armed_at = NOW(), "
            f"    stop_reason = NULL, stopping_at = NULL "
            f"WHERE id = %s AND org_id = %s "
            f"  AND status IN ('draft', 'stopped', 'done', 'failed') "
            f"RETURNING {_COLS}",
            (fleet_id, org_id),
        ).fetchone()
    return dict(row) if row else None


def prendre(fleet_id: int, org_id: int) -> Optional[dict]:
    """`armed` → `running` : un ordonnanceur l'a prise. C'est le FAIT."""
    with _connect() as conn:
        row = conn.execute(
            f"UPDATE runner_fleets SET status = 'running', started_at = NOW(), "
            f"    heartbeat_at = NOW() "
            f"WHERE id = %s AND org_id = %s AND status = 'armed' "
            f"RETURNING {_COLS}",
            (fleet_id, org_id),
        ).fetchone()
    return dict(row) if row else None


def demander_arret(fleet_id: int, org_id: int, raison: str) -> Optional[dict]:
    """`armed`/`running` → `stopping` : l'arrêt est DEMANDÉ, pas encore effectif.

    ⚠️ Entre cet appel et la lecture par la boucle, le passage CONTINUE — il
    réserve, il appelle, il dépense. Écrire `stopped` ici annoncerait un arrêt qui
    n'a pas eu lieu, et **croire qu'on a coupé une dépense qui continue est pire
    que croire qu'on a lancé un passage qui ne tourne pas** : dans un cas on
    attend, dans l'autre on part tranquille pendant que ça brûle.
    """
    with _connect() as conn:
        row = conn.execute(
            f"UPDATE runner_fleets SET status = 'stopping', stopping_at = NOW(), "
            f"    stop_reason = %s "
            f"WHERE id = %s AND org_id = %s AND status IN ('armed', 'running') "
            f"RETURNING {_COLS}",
            (raison, fleet_id, org_id),
        ).fetchone()
    return dict(row) if row else None


def accuser_arret(fleet_id: int, org_id: int, raison: Optional[str] = None) -> bool:
    """`stopping`/`running` → `stopped` : l'ordonnanceur a accusé réception.

    ⚠️ C'est LUI qui pose ce statut, jamais l'opérateur — sans quoi l'écart entre
    « demandé » et « effectif » disparaîtrait, et avec lui le seul diagnostic d'un
    ordonnanceur mort : *un arrêt demandé qui ne devient jamais un arrêt effectif*.

    ⚠️ Ce raisonnement valait TANT QU'UN ORDONNANCEUR EXISTAIT. Depuis le
    renversement, plus personne n'appelle ce verbe et l'écart ne diagnostique
    plus rien : il ne se referme jamais. `accuser_arrets_effectifs` le referme
    au sondage, sur un fait constaté — plus aucun travail en vol — et non sur
    une intention. Ce verbe-ci reste servi pour un ordonnanceur externe.
    """
    with _connect() as conn:
        row = conn.execute(
            "UPDATE runner_fleets SET status = 'stopped', stopped_at = NOW(), "
            "    stop_reason = COALESCE(%s, stop_reason) "
            "WHERE id = %s AND org_id = %s AND status IN ('stopping', 'running') "
            "RETURNING id",
            (raison, fleet_id, org_id),
        ).fetchone()
    return row is not None


def arret_demande(fleet_id: int, org_id: int) -> bool:
    """L'ordonnanceur demande : « dois-je m'arrêter ? » — une lecture, pas un état
    local. C'est ce qui rend `op=stop` RÉEL au lieu d'être une écriture que
    personne ne lit."""
    with _connect() as conn:
        row = conn.execute(
            "SELECT status FROM runner_fleets WHERE id = %s AND org_id = %s",
            (fleet_id, org_id),
        ).fetchone()
    return bool(row) and dict(row)["status"] in ("stopping", "stopped")


def run_appartient_a_flotte(run_id: str, fleet_id: int) -> bool:
    """Ce déroulé tourne-t-il POUR cette flotte ?

    ⚠️ La question n'est pas « ce run existe-t-il » mais « est-ce CELUI qu'on
    voudrait couper ». Un agent doit pouvoir arrêter une AUTRE flotte de son org —
    c'est même le cas utile : un opérateur qui pilote par la conversation. Ce
    qu'on interdit, c'est qu'il coupe celle qui l'exécute.
    """
    with _connect() as conn:
        row = conn.execute(
            "SELECT 1 FROM runner_jobs WHERE run_id = %s AND fleet_id = %s LIMIT 1",
            (run_id, fleet_id),
        ).fetchone()
    return row is not None


def battre(fleet_id: int, org_id: int) -> bool:
    """Le battement de l'ordonnanceur — ce qui distingue le VIVANT du RÉSIDU.

    Une flotte `running` qui ne bat plus n'est pas une concurrence à attendre :
    c'est un reste de passage mort. Sans cette distinction, un second passage se
    heurte à un refus que rien ne justifie, quelqu'un désarme à la main — et
    désarmer devient le geste normal.
    """
    with _connect() as conn:
        row = conn.execute(
            "UPDATE runner_fleets SET heartbeat_at = NOW() "
            "WHERE id = %s AND org_id = %s AND status = 'running' RETURNING id",
            (fleet_id, org_id),
        ).fetchone()
    return row is not None


def fleet_state(fleet_id: int, org_id: int) -> Optional[dict]:
    """L'ÉTAT d'un passage : sa config, l'avancement de ses travaux, ce qu'il a
    consommé, et ce qui est mort en route.

    Rend `None` si la flotte n'existe pas dans cette org — un état vide et un état
    inexistant ne se ressemblent pas et ne doivent pas se répondre pareil.
    """
    fleet = get_fleet(fleet_id, org_id)
    if not fleet:
        return None
    with _connect() as conn:
        agg = conn.execute(
            """
            SELECT COUNT(*)                                            AS jobs_total,
                   COUNT(*) FILTER (WHERE status = 'pending')          AS pending,
                   COUNT(*) FILTER (WHERE status = 'claimed')          AS claimed,
                   COUNT(*) FILTER (WHERE status = 'done')             AS done,
                   COUNT(*) FILTER (WHERE status = 'failed')           AS failed,
                   COUNT(*) FILTER (WHERE status = 'failed'
                                      AND attempts >= max_attempts)    AS abandoned,
                   -- ⚠️ `SUM` sur un bigint rend un NUMERIC en PostgreSQL, donc un
                   -- Decimal côté client — que rien ne normalise et que JSON refuse.
                   -- Sans ce cast, `state` rendait 500 sur TOUTE flotte, y compris
                   -- vierge (COALESCE rend `Decimal('0')`).
                   -- Celui sur `MAX` est une SYMÉTRIE DÉFENSIVE, pas une garde :
                   -- le cast interne type déjà la valeur, donc aucun test ne peut
                   -- le faire tomber. Il est là pour qu'un futur passage de MAX à
                   -- SUM ne réintroduise pas la panne — et il est nommé pour ce
                   -- qu'il est, parce qu'une protection qu'on n'a jamais vue mordre
                   -- ne doit pas se faire passer pour une garde éprouvée.
                   -- Le CASE ne lit que les nombres : un `usage_tokens` mal formé
                   -- levait sur le cast et faisait tomber l'état entier (14/09/2026).
                   COALESCE(SUM(CASE WHEN jsonb_typeof(result->'usage_tokens') = 'number'
                                     THEN (result->'usage_tokens')::numeric END), 0)::bigint
                       AS usage_tokens,
                   MAX(CASE WHEN jsonb_typeof(result->'usage_tokens') = 'number'
                            THEN (result->'usage_tokens')::numeric END)::bigint
                       AS heaviest_row_tokens,
                   MAX(finished_at)                                    AS last_finished,
                   -- Ce que les travaux TERMINÉS disent de leur issue (14/09/2026,
                   -- oto#243) : `done` recouvrait « une ligne traitée » et « rien
                   -- trouvé ». Comparé en JSONB, jamais casté — un résultat mal formé
                   -- ne doit pas faire tomber l'état de toute la flotte.
                   -- À vide = a appelé la file (compte d'appels tel que le worker le
                   -- déclare) ET son run n'a reçu aucune ligne (compté par la
                   -- plateforme à la réservation, `runs.lignes_reservees`).
                   COUNT(*) FILTER (WHERE status IN ('done', 'failed')
                       AND jsonb_typeof(result->'tool_counts'->'data_claim_next') = 'number'
                       AND result->'tool_counts'->'data_claim_next' > '0'::jsonb
                       AND EXISTS (SELECT 1 FROM runs r
                                    WHERE r.run_id = runner_jobs.run_id
                                      AND r.lignes_reservees = 0))       AS empty_jobs,
                   COUNT(*) FILTER (WHERE status IN ('done', 'failed')
                       AND result->>'stopped' IN ('max_tokens', 'max_steps')
                       AND jsonb_typeof(result->'tool_counts'->'data_write') = 'number'
                       AND result->'tool_counts'->'data_write' > '0'::jsonb)
                                                                       AS stopped_after_write,
                   -- Le NON MESURÉ se compte, il ne se lit pas comme un zéro : travail
                   -- sans run, ou run ouvert avant la mesure (oto#244 pour l'usage :
                   -- SUM ignore les inconnus en silence).
                   COUNT(*) FILTER (WHERE status IN ('done', 'failed')
                       AND NOT EXISTS (SELECT 1 FROM runs r
                                        WHERE r.run_id = runner_jobs.run_id
                                          AND r.lignes_reservees IS NOT NULL))
                                                                       AS reservation_unmeasured,
                   COUNT(*) FILTER (WHERE status IN ('done', 'failed')
                       AND jsonb_typeof(result->'usage_tokens') IS DISTINCT FROM 'number')
                                                                       AS usage_unknown
              FROM runner_jobs
             WHERE fleet_id = %s AND org_id = %s
            """,
            (fleet_id, org_id),
        ).fetchone()
    etat = dict(agg)
    # Un passage sans aucun travail rattaché le DIT, au lieu de rendre des
    # compteurs à zéro qu'on lirait comme « rien ne s'est passé ».
    etat["no_jobs_attached"] = etat["jobs_total"] == 0
    return {"fleet": fleet, "state": etat}
