#!/usr/bin/env python3
"""Archive puis purge le journal d'appels au-delà de la fenêtre de rétention.

**Pourquoi ce script existe.** `tool_calls` n'avait AUCUNE rétention : 47 % de la base
le 2026-08-27, et une croissance passée de 9 600 à ~90 000 lignes/jour en deux semaines
(×10, tiré par une campagne de runner). La lecture, elle, a été réparée le même jour par
un index — mais une table qui ne fait que croître finit toujours par coûter ailleurs
(sauvegardes, vacuum, restauration). D'où : on garde 90 jours en ligne, le reste part en
froid sur l'Object Storage avant d'être effacé.

**Pourquoi ce n'est PAS une simple purge de logs.** Cette table est à double emploi :
c'est le journal d'observabilité, ET la source de vérité des exécutions — un run n'est
pas stocké, il est RECONSTRUIT depuis ses faits, et ces faits sont deux lignes d'ici
(`run_start` / `run_finish`, cf. `db/usage.py::_runs_from_journal`). Les effacer
effacerait l'historique des runs, pas seulement du log. Ils sont donc **exemptés** :
ils pèsent ~3 % du volume, les garder indéfiniment est bon marché.
⚠️ Conséquence assumée : un run dont les appels ordinaires ont été archivés garde son
ouverture, sa clôture et son issue, mais son « dernier signe de vie » retombe sur sa date
d'ouverture (`last_seen_at` se dérive du dernier appel rattaché). Sans effet sur un run
clos ; un run resté ouvert et vieux de 90 jours est de toute façon lu comme silencieux.

**Où il tourne, et pourquoi pas dans le backend.** Sur la box, en travail planifié — pas
dans le processus MCP. Celui-ci est mono-boucle : y loger un export de plusieurs
centaines de Mo et une suppression par lots reviendrait à réinstaller la panne que ce
même journal a causée (cf. `docs/event-loop-perf.md`). Un verrou consultatif protège de
toute façon contre deux exécutions simultanées — prod et preprod partagent la base.

Usage :
    archive_tool_calls.py [--dry-run] [--retention-days N]

L'environnement est celui du backend (`/opt/oto-mcp/.env`) : `DATABASE_URL` et
`OTO_MCP_S3_{ENDPOINT,REGION,BUCKET,ACCESS_KEY,SECRET_KEY}`.
"""
from __future__ import annotations

import argparse
import csv
import gzip
import io
import logging
import os
import shutil
import sys
import tempfile
import time
from datetime import datetime, timezone

import boto3
import psycopg

# Les faits qui RECONSTRUISENT un run : jamais archivés, jamais supprimés. Toucher à
# cette liste, c'est décider d'oublier des exécutions — pas d'alléger un log.
RUN_FACTS = ("run_start", "run_finish")

# Préfixe S3. Déposé SANS ACL publique (le bucket porte les deux régimes : `upload_image`
# pose `public-read`, les blobs durables non — cf. `media_store.py`). Un journal porte des
# identifiants de compte et des arguments d'appel : il suit le régime privé.
S3_PREFIX = "journal/tool_calls"

# Suppression par lots : un DELETE de plusieurs millions de lignes tiendrait un lock long
# et gonflerait la table de tuples morts. La pause laisse respirer l'autovacuum.
DELETE_BATCH = 20_000
DELETE_PAUSE_S = 0.2

# Verrou consultatif : deux exécutions concurrentes exporteraient le même mois deux fois,
# et la seconde supprimerait ce que la première est en train de lire.
LOCK_KEY = 0x07C11  # constante arbitraire, stable — ne la change pas sans raison

log = logging.getLogger("archive_tool_calls")


def _env(name: str, default: str | None = None) -> str:
    val = os.environ.get(name, default)
    if not val:
        raise SystemExit(f"variable d'environnement requise absente : {name}")
    return val


def _s3_client():
    return boto3.client(
        "s3",
        endpoint_url=_env("OTO_MCP_S3_ENDPOINT"),
        region_name=os.environ.get("OTO_MCP_S3_REGION", "fr-par"),
        aws_access_key_id=_env("OTO_MCP_S3_ACCESS_KEY"),
        aws_secret_access_key=_env("OTO_MCP_S3_SECRET_KEY"),
    )


def _months_to_archive(conn: psycopg.Connection, retention_days: int) -> list[tuple[str, int]]:
    """Les mois CALENDAIRES entièrement sortis de la fenêtre, qui portent encore des
    lignes archivables.

    Le mois entier doit être derrière la borne : archiver un mois à cheval déposerait un
    fichier incomplet, que la prochaine passe ne saurait pas compléter (l'objet existe
    déjà) — on aurait effacé des lignes qui ne sont dans aucune archive."""
    rows = conn.execute(
        """
        SELECT to_char(date_trunc('month', created_at), 'YYYY-MM') AS mois,
               count(*) AS lignes
          FROM tool_calls
         WHERE coalesce(tool, '') <> ALL(%(facts)s)
           AND (date_trunc('month', created_at) + interval '1 month')
               <= now() - %(retention)s * interval '1 day'
         GROUP BY 1 ORDER BY 1
        """,
        {"facts": list(RUN_FACTS), "retention": retention_days},
    ).fetchall()
    for mois, lignes in rows:
        log.info("mois archivable : %s (%s lignes)", mois, f"{lignes:_}".replace("_", " "))
    return [(r[0], r[1]) for r in rows]


def _export_month(conn: psycopg.Connection, s3, bucket: str, mois: str) -> str:
    """Exporte un mois en CSV gzip et le dépose sur l'Object Storage.

    Passe par un fichier temporaire plutôt qu'un flux en mémoire : un mois pèse ~1 Go
    brut. ⚠️ La box a un `/` étroit (incident disque du 2026-07-24) — l'espace est
    vérifié avant, et le temporaire est retiré quoi qu'il arrive."""
    key = f"{S3_PREFIX}/{mois}.csv.gz"
    free = shutil.disk_usage(tempfile.gettempdir()).free
    if free < 2 * 1024**3:
        raise SystemExit(
            f"moins de 2 Go libres sur {tempfile.gettempdir()} ({free // 1024**2} Mo) — "
            "export refusé plutôt que remplir le disque de la box")

    fd, path = tempfile.mkstemp(prefix=f"tool_calls-{mois}-", suffix=".csv.gz")
    os.close(fd)
    try:
        # ⚠️ Ne PAS compter les enregistrements en comptant les sauts de ligne du flux :
        # `args` et `error` en contiennent, et le CSV les porte entre guillemets. Mesuré
        # le 2026-08-27 : 12 830 « lignes » annoncées pour 12 459 enregistrements réels.
        # Le seul compte juste est celui de la base — un compteur qui ment dans un log
        # d'archivage est pire qu'absent, c'est lui qu'on interrogera pour vérifier.
        with gzip.open(path, "wb") as gz:
            with conn.cursor().copy(
                """
                COPY (SELECT * FROM tool_calls
                       WHERE coalesce(tool, '') <> ALL(%(facts)s)
                         AND to_char(date_trunc('month', created_at), 'YYYY-MM') = %(mois)s
                       ORDER BY id)
                TO STDOUT WITH (FORMAT csv, HEADER true)
                """,
                {"facts": list(RUN_FACTS), "mois": mois},
            ) as copy:
                for chunk in copy:
                    gz.write(chunk)
        taille = os.path.getsize(path)
        log.info("export %s : %s Mo compressés", mois, round(taille / 1024**2, 1))
        s3.upload_file(path, bucket, key)  # pas d'ACL : objet privé
    finally:
        os.unlink(path)

    meta = s3.head_object(Bucket=bucket, Key=key)
    if meta["ContentLength"] != taille:
        raise SystemExit(
            f"archive {key} déposée incomplète ({meta['ContentLength']} != {taille} "
            "octets) — AUCUNE suppression effectuée")
    log.info("archive déposée : s3://%s/%s (%s octets)", bucket, key, meta["ContentLength"])
    return key


def _verify_archive(s3, bucket: str, key: str, attendu: int) -> None:
    """Relit l'archive DEPUIS l'Object Storage et recompte ses enregistrements.

    ⚠️ C'est la garantie qui autorise la suppression, et elle ne se déduit d'aucune autre.
    Comparer la taille du dépôt à celle du fichier local prouve que l'upload n'a rien
    perdu — pas que l'export contenait tout, ni qu'il se relit. La seule preuve est de
    refaire le chemin du jour où on en aura besoin : télécharger, décompresser, parser,
    compter. Le surcoût d'un travail MENSUEL est sans commune mesure avec une suppression
    définitive fondée sur une supposition.

    Lu en flux : une archive pèse plusieurs centaines de Mo, la charger entière en
    mémoire sur la box la mettrait en difficulté."""
    csv.field_size_limit(10_000_000)  # un `args` peut être gros ; le défaut coupe à 128 ko
    body = s3.get_object(Bucket=bucket, Key=key)["Body"]
    with gzip.GzipFile(fileobj=body) as gz:
        lecteur = csv.reader(io.TextIOWrapper(gz, encoding="utf-8"))
        entete = next(lecteur, None)
        if not entete:
            raise SystemExit(f"archive {key} vide ou illisible — AUCUNE suppression effectuée")
        relus = sum(1 for _ in lecteur)
    if relus != attendu:
        raise SystemExit(
            f"archive {key} : {relus} enregistrements relus pour {attendu} attendus — "
            "AUCUNE suppression effectuée")
    log.info("archive vérifiée à la relecture : %s enregistrements, %s colonnes",
             relus, len(entete))


def _delete_month(conn: psycopg.Connection, mois: str) -> int:
    """Supprime les lignes archivées, par lots bornés."""
    total = 0
    while True:
        deleted = conn.execute(
            """
            DELETE FROM tool_calls WHERE id IN (
                SELECT id FROM tool_calls
                 WHERE coalesce(tool, '') <> ALL(%(facts)s)
                   AND to_char(date_trunc('month', created_at), 'YYYY-MM') = %(mois)s
                 ORDER BY id LIMIT %(batch)s)
            """,
            {"facts": list(RUN_FACTS), "mois": mois, "batch": DELETE_BATCH},
        ).rowcount
        conn.commit()
        total += deleted
        if deleted == 0:
            break
        log.info("  supprimé %s lignes (cumul %s)", deleted, total)
        time.sleep(DELETE_PAUSE_S)
    return total


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true",
                        help="n'exporte rien et ne supprime rien : dit seulement ce qui partirait")
    parser.add_argument("--export-only", action="store_true",
                        help="dépose et vérifie l'archive, mais NE SUPPRIME RIEN. C'est ce qui "
                             "permet d'exercer le chemin réel — export, upload, vérification — "
                             "sans engager la moitié irréversible ; à blanc on ne prouve rien.")
    parser.add_argument("--retention-days", type=int,
                        default=int(os.environ.get("OTO_JOURNAL_RETENTION_DAYS", "90")),
                        help="fenêtre gardée en ligne (défaut 90, ou OTO_JOURNAL_RETENTION_DAYS)")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    log.info("=== archivage du journal d'appels — rétention %s j%s ===",
             args.retention_days, " (À BLANC)" if args.dry_run else "")

    bucket = _env("OTO_MCP_S3_BUCKET")
    with psycopg.connect(_env("DATABASE_URL"), autocommit=True) as conn:
        if not conn.execute("SELECT pg_try_advisory_lock(%s)", (LOCK_KEY,)).fetchone()[0]:
            log.warning("une autre exécution tient le verrou — abandon (rien n'a été touché)")
            return 0
        try:
            mois_list = _months_to_archive(conn, args.retention_days)
            if not mois_list:
                log.info("rien à archiver : aucun mois entièrement sorti de la fenêtre.")
                return 0
            if args.dry_run:
                log.info("à blanc : %s mois partiraient (%s)", len(mois_list),
                         ", ".join(m for m, _ in mois_list))
                return 0

            s3 = _s3_client()
            for mois, lignes in mois_list:
                key = _export_month(conn, s3, bucket, mois)
                _verify_archive(s3, bucket, key, lignes)
                if args.export_only:
                    log.info("%s : %s lignes archivées dans %s — suppression NON demandée",
                             mois, lignes, key)
                    continue
                supprimees = _delete_month(conn, mois)
                log.info("%s : %s lignes archivées dans %s, %s supprimées de la base",
                         mois, lignes, key, supprimees)
        finally:
            conn.execute("SELECT pg_advisory_unlock(%s)", (LOCK_KEY,))

    log.info("=== terminé à %s ===", datetime.now(timezone.utc).isoformat(timespec="seconds"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
