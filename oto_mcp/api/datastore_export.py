"""`GET /api/datastores/{datastore}/rows/export.csv` — un tableau entier, en CSV.

Écrit à la main pour la raison STRUCTURELLE que `api/billing.py` (le PDF d'une
facture) et `api/projects.py` (l'export ZIP d'un projet) portent déjà : un
handler de capacité rend un `dict` que l'adaptateur emballe en `JSONResponse` —
il ne peut pas servir `text/csv`. Même précédent, même exception, déclarée dans
`tests/test_rest_modules_are_capabilities.py::_KNOWN`.

Diffère des deux par la TAILLE : un tableau peut porter des dizaines de milliers
de lignes, là où une facture ou un export projet tiennent en mémoire d'un bloc.
STREAMER est le point de ce fichier, pas un détail — c'est ce qui remplace la
pagination CLIENT (tulina-app-front, `fetchAllRows` en pages de 500, plafonnée à
`MAX_SELECT_ALL=5000`) par une seule requête dont le serveur ne matérialise
jamais le tableau entier en mémoire.

Trois contraintes du process MONO-BOUCLE (`docs/event-loop-perf.md`) que ce
fichier tient, dans cet ordre d'importance :

1. **chaque lot passe par `run_in_threadpool`** — `cursor_rows` est un appel
   psycopg SYNCHRONE (`db/datastore.py` ne porte aucun curseur serveur, cf.
   l'historique de #987), et l'aplatissement d'une page (`_row_to_dict`) est le
   calcul mesuré à ~10,5 s sur un vivier ENTIER le 17/09/2026 — un seul appel
   direct dans la coroutine gèlerait le process pour TOUT LE MONDE, pas
   seulement pour ce téléchargement ;
2. **une connexion PG par lot, jamais une sur tout le stream** — `cursor_rows`
   ouvre/ferme la sienne à chaque appel (`db._connect()`), donc un téléchargement
   lent ne tient jamais un slot du pool au-delà d'un lot — pertinent contre
   `idle_in_transaction_session_timeout` (60 s par défaut) ;
3. **le premier lot se lit AVANT la réponse** — un tableau introuvable doit
   pouvoir répondre 404, ce qui n'est plus possible une fois les en-têtes 200
   partis. Le générateur ne reprend qu'à partir du second lot.
"""
from __future__ import annotations

import csv
import datetime as dt
import io
import json
from typing import Optional

from fastmcp.server.auth.providers.jwt import JWTVerifier
from starlette.concurrency import run_in_threadpool
from starlette.requests import Request
from starlette.responses import Response

from ..capabilities.datastore.common import ns_not_found
from ..datastore import declaration as dsdecl
from ..datastore.core import DatastoreNotFound, make_store
from .base import _authenticate, _file_stream, _json_error

# Une requête `cursor_rows` par lot. Choisi pour deux raisons opposées : assez
# gros pour qu'un tableau de 50 000 lignes tienne en 50 allers-retours DB plutôt
# que 500 (la borne `limit=500` de la face `/rows`, pensée pour une page
# d'ÉCRAN, n'a pas de raison de s'imposer ici) ; assez petit pour que
# l'aplatissement d'UN lot — le calcul qui a coûté ~10,5 s sur un vivier ENTIER —
# reste une fraction de seconde par tour de la boucle `run_in_threadpool`, ce
# tour étant ce qui tient le pool de threads, pas le total du stream.
_EXPORT_BATCH = 1000

# Garde-fou de boucle FOLLE, PAS une limite produit. `MAX_SELECT_ALL` (front,
# 5 000) existe pour dire « narrow the filter, or ask an agent » à un clic
# humain sur un tableau qui grossit sous ses yeux — cet endpoint existe
# précisément pour ne plus opposer cette limite à un export complet. Celui-ci ne
# mord qu'un curseur qui ne se stabiliserait jamais ; haut exprès, et un CSV n'a
# aucune façon fiable de dire « tronqué » sans se corrompre pour le lecteur qui
# le réimporte — donc le stream s'arrête net, sans marqueur, plutôt que de
# tourner pour toujours sur un process qui sert tout le monde.
_EXPORT_ROW_CEILING = 200_000

_META_COLUMNS = [
    {"key": "_id", "label": "_id"},
    {"key": "_created_at", "label": "_created_at"},
    {"key": "_updated_at", "label": "_updated_at"},
]


def _resolve_columns(schema: Optional[dict], sample_rows: list[dict]) -> list[dict]:
    """Les colonnes du CSV, dans l'ordre : les champs DÉCLARÉS (ordre du schéma,
    label si posé), puis les clés HORS schéma vues dans le PREMIER lot (tableau
    libre, ou colonne écrite sans être déclarée), puis `_id`/`_created_at`/
    `_updated_at` — mêmes trois colonnes que le "Show ID & dates" du front,
    toujours incluses ici : un fichier qui QUITTE l'app n'est pas "ce qui est
    affiché à l'écran" (tulina-app-front, `download-csv-button.tsx`).

    ⚠️ Une colonne hors schéma qui apparaît pour la PREMIÈRE fois après le
    premier lot n'entre pas dans l'en-tête — même limite, assumée pour la même
    raison, que `columnsFor()` côté front (une page ne peut pas savoir qu'une
    colonne existe avant de l'avoir vue). La fermer demanderait un second
    passage sur tout le tableau avant d'écrire la première ligne : le coût que
    ce fichier existe justement pour éviter. Les champs DÉCLARÉS n'ont pas ce
    problème — le schéma les nomme tous, vus ou non dans le premier lot."""
    declared = [{"key": key, "label": (dsdecl.champ_declare(schema, key) or {}).get("label") or key}
                for key in dsdecl.cles_declarees(schema)]
    seen = {c["key"] for c in declared}
    discovered = []
    for row in sample_rows:
        for key in row.keys():
            # Une couche voyage flanquée de sa valeur (`email.origine`) — ce
            # n'est pas une colonne en soi ; `_`-préfixé = plomberie déjà
            # couverte par `_META_COLUMNS`, posée une fois, pas redécouverte ici.
            if key in seen or key.startswith("_") or "." in key:
                continue
            seen.add(key)
            discovered.append({"key": key, "label": key})
    return declared + discovered + _META_COLUMNS


def _cell_text(value: object) -> str:
    """Une cellule CSV, dans le MÊME dialecte que l'export déjà existant côté
    front (tulina-app-front, `download-csv.ts`, et l'export du tableau de bord,
    `lib/csv.ts` — cf. `docs/datastore.md` §« un CSV ne sait pas dire null ») :
    vide pour `None`/`""` (jamais la chaîne `"None"`), une liste jointe sur
    `"; "`, un objet en JSON. Diverger silencieusement créerait un second
    dialecte CSV pour le même produit."""
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (list, tuple)):
        return "; ".join(_cell_text(v) for v in value)
    if isinstance(value, dict):
        return json.dumps(value, ensure_ascii=False)
    return str(value)


def _encode_rows(rows: list[list[str]]) -> bytes:
    """Un lot de lignes CSV, encodées une seule fois. `csv.writer` tient déjà
    RFC 4180 (guillemets, `\\r\\n`) — rien à réimplémenter ici."""
    buf = io.StringIO()
    csv.writer(buf).writerows(rows)
    return buf.getvalue().encode("utf-8")


def _json_query_param(request: Request, name: str, code: str, *, expect):
    """Décode un paramètre JSON de query string, avec le refus NOMMÉ de la
    route — jumelle locale de `capabilities/datastore/rows.py::_json_param`,
    non partagée : importer une fonction du paquet `capabilities` dans une
    route écrite à la main referait le couplage que cette route évite déjà en
    n'entrant pas dans l'adaptateur."""
    raw = request.query_params.get(name)
    if not raw:
        return None, None
    try:
        value = json.loads(raw)
    except ValueError:
        return None, code
    if expect is not None and not isinstance(value, expect):
        return None, code
    return value, None


async def export_csv(request: Request, *, verifier: JWTVerifier) -> Response:
    """Le tableau entier (ou le jeu filtré/trié demandé), en CSV — voir le
    docstring du module pour les trois contraintes mono-boucle.

    Mêmes paramètres de query que `GET …/rows` (`q`, `order_by`, `order_dir`,
    `filter`, `filters`) — cette route n'existe pas pour dire « exporte tout, y
    compris ce que la vue ignore » ; elle dit « le même jeu, sans plafond de
    lignes ». Absents = le tableau entier, dans son ordre de création."""
    sub, err = await _authenticate(request, verifier)
    if err:
        return err

    ref = request.path_params["datastore"]
    q = request.query_params.get("q") or None
    order_by = request.query_params.get("order_by") or None
    order_dir = request.query_params.get("order_dir") or "desc"
    if order_dir not in ("asc", "desc"):
        order_dir = "desc"
    filter_eq, filter_refus = _json_query_param(request, "filter", "invalid_filter", expect=dict)
    if filter_refus:
        return _json_error(request, 400, filter_refus)
    filters, filters_refus = _json_query_param(request, "filters", "invalid_filters", expect=list)
    if filters_refus:
        return _json_error(request, 400, filters_refus)

    store = make_store(sub)
    kw = dict(order_by=order_by, order_dir=order_dir, q=q, filter=filter_eq, filters=filters)
    try:
        # Le SCHÉMA et le PREMIER lot se lisent ici, avant toute réponse : un
        # tableau introuvable doit encore pouvoir répondre 404 (§3 du docstring
        # de module) — impossible une fois les en-têtes 200 partis.
        schema = await run_in_threadpool(store.get_schema, ref)
        first_page = await run_in_threadpool(store.cursor_rows, ref, limit=_EXPORT_BATCH, **kw)
    except DatastoreNotFound:
        d = ns_not_found(sub, ref)
        return _json_error(request, d.status, d.code, d.message or None)
    except ValueError as e:
        # Même refus que la face `/rows` pour un filtre sémantiquement invalide
        # (opérateur inconnu, `null` sur `eq`…) — le message du store arrive
        # jusqu'au client plutôt qu'un `invalid_filters` nu.
        return _json_error(request, 400, "invalid_filters", str(e))

    columns = _resolve_columns(schema, first_page["rows"])
    table_name = (store.dernier_tableau or {}).get("datastore") or ref
    filename = f"{table_name}-{dt.date.today().isoformat()}.csv"

    async def body():
        yield _encode_rows([[c["label"] for c in columns]])
        page = first_page
        served = 0
        while True:
            rows = page["rows"]
            if rows:
                yield _encode_rows([[_cell_text(row.get(c["key"])) for c in columns]
                                    for row in rows])
            served += len(rows)
            cursor = page.get("next_cursor")
            if not cursor or served >= _EXPORT_ROW_CEILING:
                break
            page = await run_in_threadpool(
                store.cursor_rows, ref, limit=_EXPORT_BATCH, cursor=cursor, **kw)

    return _file_stream(request, body(), media_type="text/csv; charset=utf-8",
                        filename=filename)
