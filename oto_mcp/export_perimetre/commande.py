"""`oto-mcp perimetre export|import|journal` — l'outil en ligne de commande (#1088).

    oto-mcp perimetre export --org 12 [--org 13 …] --sortie perimetre.jsonl [--sans-journal]
        lit la base de CETTE instance (`DATABASE_URL`), en lecture seule, et son
        stockage objet (`OTO_MCP_S3_*`). Les secrets sont rechiffrés depuis la clé de
        cette instance (`OTO_MCP_MASTER_KEY`) vers la clé de l'instance cible, lue dans
        `OTO_EXPORT_CLE_CIBLE` pour cette seule exécution ; les objets désignés partent
        dans `perimetre.jsonl.objets.tar`, scellés sous la même clé cible.
        `--sans-journal` laisse le journal d'appels : il voyage par tranches.

    oto-mcp perimetre import perimetre.jsonl
        verse le fichier dans la base de l'instance (`DATABASE_URL`), née par `init_db`
        et où l'app n'a JAMAIS démarré — sinon refus, qui nomme les tables déjà semées —,
        et l'archive des objets dans SON stockage (`OTO_MCP_S3_*`), en
        réécrivant les URL vers SA base publique ; secrets et objets doivent être
        chiffrés sous SA clé (`OTO_MCP_MASTER_KEY`).

    oto-mcp perimetre journal export --org 12 --depuis 2026-09-01 --jusqu-a 2026-10-01 \
            --sortie journal-09.jsonl [--faits-de-run-complets]
        la tranche `[depuis, jusqu-a)` du journal du même périmètre, mêmes variables ;
        une borne sans fuseau est en UTC.

    oto-mcp perimetre journal import journal-09.jsonl
        la verse dans l'instance née par `init_db`, avant ou après l'import
        principal ; rejouée, elle n'insère rien.

Un refus s'imprime tel quel, nommé, et sort en code 2 ; rien n'est écrit. Le résumé ne
cite jamais une clé, seulement l'empreinte de la clé cible.
"""
from __future__ import annotations

import argparse
import json
import os
import sys

import psycopg
from psycopg.rows import dict_row

from .. import media_store
from ..crypto import parse_key
from ..db._conn import _database_url
from .comptes import ComptesHorsRegle
from .decouverte import ClassementIncomplet, JournalNonDetachable
from .extraction import ReferencesHorsPerimetre, SecretsChiffres, exporter
from .importation import ImportRefuse, importer
from .journal import TrancheRefusee, exporter_tranche, importer_tranche, instant
from .objets import ObjetsRefuses, StockageS3
from .perimetre import PerimetreRefuse
from .rechiffrement import RechiffrementImpossible

REFUS = (ClassementIncomplet, PerimetreRefuse, ComptesHorsRegle, SecretsChiffres,
         ReferencesHorsPerimetre, RechiffrementImpossible, ObjetsRefuses, ImportRefuse,
         JournalNonDetachable, TrancheRefusee, FileExistsError)


def _stockage() -> StockageS3:
    """Le stockage objet de CETTE instance, tel que `media_store` le sert."""
    return StockageS3(media_store._get_client(), media_store._bucket())


def _cle_cible() -> bytes | None:
    brute = os.environ.get("OTO_EXPORT_CLE_CIBLE")
    return parse_key(brute, "OTO_EXPORT_CLE_CIBLE") if brute else None


def _resume(resultat: dict, cles: tuple[str, ...]) -> dict:
    """Le résumé d'un export : jamais une clé, seulement l'empreinte de la clé cible."""
    resume = {k: resultat[k] for k in (
        "perimetre", "tenant", "comptes_hors_perimetre", "cle_cible", "empreinte", *cles)}
    resume["objets"] = {k: resultat["objets"][k] for k in ("archive", "empreinte")}
    resume["objets"]["nombre"] = len(resultat["objets"]["liste"])
    resume["lignes"] = {t: v["lignes"] for t, v in resultat["tables"].items()
                        if v.get("lignes")}
    return resume


def _analyseur() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="oto-mcp perimetre",
                                description="Export et import par périmètre de propriétaire.")
    sous = p.add_subparsers(dest="geste", required=True)
    e = sous.add_parser("export", help="exporter le périmètre d'une ou plusieurs orgs")
    e.add_argument("--org", type=int, action="append", required=True, dest="orgs")
    e.add_argument("--sortie", required=True)
    e.add_argument("--sans-journal", action="store_true",
                   help="laisser le journal d'appels : il voyage par tranches (journal)")
    i = sous.add_parser("import", help="importer un export dans la base de cette instance")
    i.add_argument("fichier")
    j = sous.add_parser("journal", help="le journal d'appels, par tranches de dates")
    jsous = j.add_subparsers(dest="geste_journal", required=True)
    je = jsous.add_parser("export", help="exporter une tranche [depuis, jusqu-a) du journal")
    je.add_argument("--org", type=int, action="append", required=True, dest="orgs")
    je.add_argument("--depuis", required=True, type=instant)
    je.add_argument("--jusqu-a", required=True, type=instant, dest="jusqu_a")
    je.add_argument("--sortie", required=True)
    je.add_argument("--faits-de-run-complets", action="store_true", dest="faits_de_run",
                    help="emporter aussi tous les faits de run antérieurs à --depuis")
    ji = jsous.add_parser("import", help="verser une tranche dans la base de cette instance")
    ji.add_argument("fichier")
    return p


def _jouer(conn, args) -> dict:
    objets = {"stockage": _stockage(), "base_publique": media_store.public_base()}
    if args.geste == "export":
        return _resume(exporter(conn, args.orgs, args.sortie, cle_cible=_cle_cible(),
                                journal=not args.sans_journal, **objets),
                       ("secrets", "partages_omis", "journal"))
    if args.geste == "import":
        return {t: v["lignes"] for t, v in importer(conn, args.fichier, **objets).items()}
    if args.geste_journal == "export":
        return _resume(exporter_tranche(conn, args.orgs, args.sortie, depuis=args.depuis,
                                        jusqu_a=args.jusqu_a, cle_cible=_cle_cible(),
                                        faits_de_run=args.faits_de_run, **objets),
                       ("tranche",))
    return {t: {k: v[k] for k in ("lignes", "inserees", "deja_presentes")}
            for t, v in importer_tranche(conn, args.fichier, **objets).items()}


def main(argv: list[str] | None = None) -> int:
    try:
        args = _analyseur().parse_args(argv)
        with psycopg.connect(_database_url(), row_factory=dict_row) as conn:
            resume = _jouer(conn, args)
    except REFUS as refus:
        print(f"refus ({type(refus).__name__}) : {refus}", file=sys.stderr)
        return 2
    print(json.dumps(resume, ensure_ascii=False, indent=2))
    return 0
