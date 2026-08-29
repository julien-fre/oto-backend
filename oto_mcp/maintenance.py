"""Les travaux de maintenance du backend — hors du démarrage (ADR 0065, lot 0).

Quatre travaux tournaient à la fin d'`init_db`, donc **à chaque boot de chaque
environnement**, dans une fenêtre de healthcheck finie (120 s, sonde directe
`127.0.0.1:9103`). Ils avaient la forme d'un cron et le coût d'un cron : purger,
re-projeter, poser des index — le seul poste du démarrage dont la durée suit la
taille de la base, donc le seul qui transformera un jour un déploiement sain en
rollback sans que rien n'ait changé dans le lot.

Ils sont ici, chacun nommé, chacun jouable seul :

    oto-mcp maintenance retention     purge du fil des runs + des runs sans faits
    oto-mcp maintenance blocks        re-projection du corps des nœuds en blocs
    oto-mcp maintenance key-indexes   index d'unicité de clé métier par namespace
    oto-mcp maintenance check-boot    rejoue l'ordre du boot en transaction ANNULÉE
    oto-mcp maintenance all           les trois premiers, dans l'ordre

    oto-mcp maintenance key-index-rebuild   (#421 — voir plus bas, PAS dans `all`)
    oto-mcp maintenance journal-tokens      purge rétroactive des jetons écrits en
                                            clair dans le journal (#558) — À BLANC
                                            par défaut, `--apply` pour écrire

Trois propriétés qu'aucun de ces travaux ne perd en changeant de porte :
**idempotents** (les rejouer ne change rien), **fail-open par travail** (un échec
est journalisé, les autres continuent, le code de sortie reste 0 sauf `--strict`),
**journalisés** (une ligne par travail, avec sa durée et son compte).

⚠️ **La base est PARTAGÉE prod/preprod.** Le timer n'est posé que côté PROD
(`deploy/oto-backend.sh`) : deux exécutants sur la même base ne feraient que se
disputer les mêmes lignes.
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
import time
from typing import Callable

logger = logging.getLogger("oto_mcp.maintenance")

# Fenêtre de rétention du JOURNAL d'appels, en jours — **une seule politique** (#426),
# le même nom d'environnement et le même défaut que `deploy/archive_tool_calls.py`.
# C'était le point du lot : il y en avait DEUX, 30 j au boot et 90 j au timer, et la
# plus courte gagnait en silence — le boot supprimait sans exporter, donc l'archive S3
# posée le 27/08 n'aurait jamais trouvé un mois complet à prendre (mesuré le 28/08 :
# 0 ligne au-delà de 30 j sur 969 314). Ce module ne purge PAS le journal (c'est
# l'archive qui le fait, après l'avoir exporté) ; il purge à la MÊME borne ce qui
# n'a pas d'archive : les étiquettes de runs devenues orphelines.
_JOURNAL_RETENTION_DAYS = int(os.environ.get("OTO_JOURNAL_RETENTION_DAYS", "90"))
# Le fil des runs hébergés a SA fenêtre, et ce n'est pas une seconde politique de
# rétention du journal : c'est un autre objet. Le fil est l'état d'exécution d'un
# run, pas sa vérité — celle-ci est au journal, et un run se reprend par le journal
# (ADR 0064-D3). On l'efface donc plus court, sans archive.
_RUN_THREAD_RETENTION_DAYS = int(
    os.environ.get("OTO_MCP_RUN_THREAD_RETENTION_DAYS", "30"))


# --------------------------------------------------------------------------- #
# Les travaux
# --------------------------------------------------------------------------- #

def retention(*, dry_run: bool = False) -> dict:
    """Purge bornée : les runs sans faits, puis le fil des runs hébergés.

    **Ne touche pas `tool_calls`.** La rétention du journal appartient à
    `deploy/archive_tool_calls.py` (timer `oto-journal-archive`), qui EXPORTE le mois
    au froid S3 avant de le supprimer. Le boot, lui, supprimait à 30 jours *sans rien
    exporter* — mesuré le 2026-08-28 : 0 ligne au-delà de 30 j dans une table de
    969 314 lignes, donc l'archive posée le 27/08 n'aurait jamais trouvé un seul mois
    à archiver. Ce n'était pas une politique en double, c'était une politique qui en
    annulait une autre.

    Un run dont les faits sont partis au froid devient une étiquette qui annonce
    « prospection Q3 → done » au-dessus d'une page vide (#289) : on l'efface, à la
    même borne que le journal.
    """
    from .db import run_thread, usage
    out: dict = {"retention_days": _JOURNAL_RETENTION_DAYS,
                 "run_thread_days": _RUN_THREAD_RETENTION_DAYS}
    if dry_run:
        out["orphan_runs"] = usage.count_orphan_runs(_JOURNAL_RETENTION_DAYS)
        out["run_messages"] = run_thread.count_prunable_run_messages(
            _RUN_THREAD_RETENTION_DAYS)
        return out
    out["orphan_runs"] = usage.prune_orphan_runs(_JOURNAL_RETENTION_DAYS)
    out["run_messages"] = run_thread.prune_run_messages(_RUN_THREAD_RETENTION_DAYS)
    return out


def blocks(*, dry_run: bool = False) -> dict:
    """Re-projette en blocs le corps des nœuds dont le marqueur ne correspond plus.

    Sorti du boot alors même que son régime stable y coûtait 130 ms : ce n'est pas le
    régime stable qui décide, c'est la ROTATION DE MARQUEUR. Le jour où le marqueur
    est passé de `blocks_md5` à `blocks_md5_v2`, les 1 526 nœuds ont été re-parsés au
    démarrage — 4 allers-retours chacun sur une base managée à 3,1 ms, soit ~19 s
    ajoutés à la fenêtre du healthcheck par un lot qui ne savait pas les ajouter.
    C'est exactement le déploiement sain que l'ADR 0065 veut cesser de rollbacker.

    Sortir est sûr parce que les blocs sont une PROJECTION : leur seul lecteur est la
    vue d'un nœud (`db/node_view.py`), et un retard d'un tir de timer y est visible
    comme un corps affiché depuis sa source, pas comme une erreur.
    """
    from .db import blocks as db_blocks
    if dry_run:
        return {"stale": db_blocks.count_stale_nodes()}
    return {"parsed": db_blocks.backfill_node_blocks()}


def key_indexes(*, dry_run: bool = False) -> dict:
    """Pose l'index UNIQUE de clé métier des namespaces qui en déclarent une (#109 ch.3).

    Sorti du boot parce que son coût est le NOMBRE DE NAMESPACES, pas leur contenu, et
    que ce nombre ne fait que croître : 204 namespaces à clé le 2026-08-28, un
    aller-retour chacun pour vérifier que l'index est là — 644 ms mesurées, payées
    trois fois par boot (`init_db` était appelé trois fois), pour zéro index manquant.

    Fail-open PAR namespace : un tableau récalcitrant est journalisé et n'empêche ni
    les autres, ni le reste de la maintenance. Son chemin d'écriture reste
    l'applicatif historique tant que son index n'est pas posé.
    """
    from .db import datastore as ds
    targets = ds.datastore_namespaces_with_key()
    manquants = [ns for ns in targets if not ds.datastore_has_key_index(ns["id"])]
    if dry_run:
        return {"namespaces": len(targets), "missing": len(manquants)}
    poses, resorbes, echecs = 0, 0, 0
    for ns in manquants:
        try:
            removed = ds.datastore_merge_key_duplicates(ns["id"], ns["key"])
            ds.datastore_ensure_key_index(ns["id"], ns["key"])
            poses += 1
            resorbes += removed
            if removed:
                logger.info("key-index ns=%s key=%s : %d doublon(s) résorbé(s)",
                            ns["id"], ns["key"], removed)
        except Exception:
            echecs += 1
            logger.warning("key-index ns=%s : échec (fail-open)", ns.get("id"),
                           exc_info=True)
    return {"namespaces": len(targets), "posed": poses,
            "duplicates_merged": resorbes, "failed": echecs}


def key_index_rebuild(*, dry_run: bool = False) -> dict:
    """Reconstruit TOUS les index de clé métier sur l'expression polymorphe (#318).

    ⚠️ **Ce travail n'a jamais tourné en production** (oto-backend#421) : son appel
    était placé après une boucle dont chaque branche `return` ou `raise`, donc aucun
    chemin ne l'atteignait. Le lot 0 retire l'appel mort et met la fonction ici, où
    elle est appelable — **délibérément hors de `all` et sans timer** : la faire
    tourner pour la première fois est une décision qui change l'état de la production,
    pas un effet de bord de sortie de maintenance.
    """
    from .db import _init
    if dry_run:
        from .db import datastore as ds
        return {"namespaces": len(ds.datastore_namespaces_with_key()),
                "note": "aucun index reconstruit (dry-run) — cf. oto-backend#421"}
    return {"rebuilt": _init.migrate_business_key_indexes()}


def journal_tokens(*, dry_run: bool = True) -> dict:
    """Purge RÉTROACTIVE des jetons écrits en clair dans le journal (#558).

    Le masquage à l'écriture ne vaut que pour les lignes à venir : celles déjà
    posées portent, sur toute la fenêtre de rétention, des jetons d'upload, de
    partage et d'invitation en clair. Cette commande les ramène à la route réduite
    — une RÉPARATION, pas une suppression : la télémétrie de surface (qui, quand,
    quel code, quelle durée) reste, elle cesse seulement de nommer le secret.

    ⚠️ **À BLANC PAR DÉFAUT, et hors de `all`.** Elle réécrit des lignes servies
    aux lentilles de supervision sur une base PARTAGÉE prod/preprod : la lancer est
    une décision, pas un effet de bord de sortie de maintenance. Même règle que
    `key-index-rebuild` (#421). `--apply` pour écrire.

    Ce qu'elle purge est DÉRIVÉ de la table de routes servie et du registre de
    capacités, jamais d'une liste tenue à la main — c'est la même déclaration que
    le masquage à l'écriture, et deux listes divergeraient.
    """
    from . import journal_secrets
    from .api import routes as api_routes
    from .db import journal_purge
    # `make_routes` ne fait que capturer le verifier au montage (cf.
    # `tests/api/test_api_routes_table_frozen.py`) : un objet nu suffit à obtenir
    # la table, et c'est elle qui déclare `{token}` / `{code}`.
    api_routes.make_routes(object(), mcp_instance=None)
    plans = journal_secrets.journal_purge_plans()
    if not plans:
        raise RuntimeError(
            "aucune route à paramètre secret déclarée — la table de routes n'a pas "
            "été montée, la purge ne saurait pas quoi chercher")
    out: dict = {"applied": not dry_run,
                 "routes": journal_purge.purge_route_tokens(plans, dry_run=dry_run)}
    out["args"] = journal_purge.purge_arg_tokens(
        journal_secrets.secret_arg_names_by_tool(), journal_secrets.mask,
        dry_run=dry_run)
    return out


def check_boot(*, dry_run: bool = True) -> dict:
    """Rejoue l'ORDRE DU BOOT (DDL assemblé PUIS les ALTER) en transaction ANNULÉE.

    Le garde-fou qui manquait le 2026-08-27 (#450) : un index posé dans le DDL de base
    sur une colonne qui naît d'un ALTER. Le DDL seul passait, la migration seule
    passait — **c'est leur ordre qui échouait**, et rien ne jouait cet ordre ailleurs
    qu'au démarrage d'un vrai serveur. Ici, il se joue contre n'importe quelle base,
    y compris une base SERVIE, sans y laisser de trace : la transaction est annulée.

    `dry_run` n'est pas optionnel dans les faits — cette commande n'écrit jamais.
    """
    from .db._conn import _connect
    from .db._init import replay_boot_schema_dry
    with _connect() as conn:
        replay_boot_schema_dry(conn)
    return {"replayed": True, "committed": False}


# Ce que `all` enchaîne — l'ordre compte : la purge d'abord (elle réduit ce que les
# deux suivants ont à regarder), la re-projection ensuite, les index en dernier
# (seuls à poser du DDL). `key-index-rebuild` et `check-boot` n'y sont PAS : le
# premier change l'état de la prod pour la première fois (#421), le second est un
# diagnostic.
_TRAVAUX: dict[str, Callable[..., dict]] = {
    "retention": retention,
    "blocks": blocks,
    "key-indexes": key_indexes,
    "key-index-rebuild": key_index_rebuild,
    "journal-tokens": journal_tokens,
    "check-boot": check_boot,
}
# Travaux dont l'écriture est un ACTE, pas une routine : à blanc par défaut, et
# c'est `--apply` qui écrit. Ils ne sont dans aucun timer et jamais dans `all`.
_ACTES = ("journal-tokens",)
_ALL = ("retention", "blocks", "key-indexes")


def run(noms: list[str], *, dry_run: bool = False, strict: bool = False) -> int:
    """Joue les travaux nommés, chacun chronométré et journalisé. Rend un code de sortie.

    **Fail-open par défaut** : un travail qui casse est journalisé et n'empêche pas les
    suivants, et le code reste 0 — un timer de maintenance qui rougit pour une purge
    ratée réveille quelqu'un pour rien. `--strict` inverse ce choix, pour la CI.
    """
    echecs = 0
    for nom in noms:
        fn = _TRAVAUX[nom]
        debut = time.monotonic()
        try:
            out = fn(dry_run=dry_run)
            logger.info("maintenance %s%s : %.0f ms — %s", nom,
                        " (à blanc)" if dry_run else "",
                        (time.monotonic() - debut) * 1000, out)
        except Exception:
            echecs += 1
            logger.error("maintenance %s : ÉCHEC après %.0f ms", nom,
                         (time.monotonic() - debut) * 1000, exc_info=True)
    return 1 if (echecs and strict) else 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        prog="oto-mcp maintenance",
        description="Les travaux de maintenance du backend (ADR 0065, lot 0).")
    p.add_argument("travail", choices=sorted(_TRAVAUX) + ["all"],
                   help="le travail à jouer, ou `all` pour " + ", ".join(_ALL))
    p.add_argument("--dry-run", action="store_true",
                   help="compte ce qu'il y aurait à faire, n'écrit rien")
    p.add_argument("--apply", action="store_true",
                   help=("écrit, pour les travaux qui sont à blanc par défaut ("
                         + ", ".join(_ACTES) + ")"))
    p.add_argument("--strict", action="store_true",
                   help="code de sortie 1 si un travail échoue (CI)")
    args = p.parse_args(argv)
    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    noms = list(_ALL) if args.travail == "all" else [args.travail]
    # Le sens du défaut s'INVERSE pour un acte : ailleurs `--dry-run` est l'opt-in
    # d'un travail qui écrit, ici `--apply` est l'opt-in d'un travail qui compte.
    a_blanc = (not args.apply) if args.travail in _ACTES else args.dry_run
    if args.apply and args.travail not in _ACTES:
        p.error("--apply ne vaut que pour : " + ", ".join(_ACTES))
    return run(noms, dry_run=a_blanc, strict=args.strict)


if __name__ == "__main__":
    sys.exit(main())
