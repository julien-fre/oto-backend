"""La file du runner, LUE PAR SA ROUTE — un relevé tronqué doit le dire (#469).

Le défaut mesuré le 28/08 : `POST /api/me/runner/jobs {op: list, limit: 1000}` rend
200 lignes, sans total ni curseur. Un poste de flotte qui fait le bilan d'une vague
de 150+ jobs lit donc un compte FAUX, et rien dans la réponse ne le lui apprend —
c'est la classe de défaut qui rassure quand il ne faut pas : un relevé plafonné
sous-déclare, il ne sur-déclare jamais.

Le banc part de la table de routes réelle (`make_routes` + adaptateur de capacités)
contre un vrai PostgreSQL : c'est le seul niveau qui prouve ce que la face SERT.
Appeler `db.list_jobs` à la main prouverait le store, pas la route — or c'est la
route que le worker consomme.
"""
from __future__ import annotations

import uuid

import pytest
from starlette.applications import Starlette
from starlette.testclient import TestClient

# Au-delà du plafond de la page (200), pour que la troncature soit un FAIT du banc
# et non une hypothèse : 205 jobs = une page pleine, puis un reste que rien ne
# montrait.
_JOBS = 205


class _Claims:
    def __init__(self, sub: str):
        self.claims = {"sub": sub, "email": f"{sub}@runner-page.invalid", "name": sub}


class _Verifier:
    async def verify_token(self, token: str):
        return _Claims(token)


def _h(sub: str) -> dict:
    return {"Authorization": f"Bearer {sub}"}


@pytest.fixture(scope="module")
def live(pg_dsn):
    """Base JETABLE + vrai `init_db()` (jamais la base partagée du conteneur)."""
    import os

    psycopg = pytest.importorskip("psycopg")
    from oto_mcp.db import _conn as dbconn

    name = "oto_rjpage_" + uuid.uuid4().hex[:8]
    root = psycopg.connect(pg_dsn, autocommit=True)
    root.execute(f'CREATE DATABASE "{name}"')
    dsn = pg_dsn.rsplit("/", 1)[0] + "/" + name
    prev_url, prev_pool = os.environ.get("DATABASE_URL"), dbconn._pool
    os.environ["DATABASE_URL"] = dsn
    dbconn._pool = None
    try:
        from oto_mcp.db import init_db
        init_db()
        yield
    finally:
        if dbconn._pool is not None:
            dbconn._pool.close()
        dbconn._pool = prev_pool
        if prev_url is None:
            os.environ.pop("DATABASE_URL", None)
        else:
            os.environ["DATABASE_URL"] = prev_url
        root.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
        root.close()


@pytest.fixture(scope="module")
def client(live):
    from oto_mcp.api import routes as api_routes
    return TestClient(Starlette(routes=api_routes.make_routes(_Verifier(), mcp_instance=None)))


@pytest.fixture(scope="module")
def flotte(live):
    """Un worker, son org active, et une vague plus grande que la page."""
    from oto_mcp import db, org_store

    worker = "usr_worker_page"
    db.upsert_user(worker, email=f"{worker}@runner-page.invalid", name=worker)
    oid = org_store.create_org("Org de la vague", created_by=worker)
    org_store.add_org_member(oid, worker, "org_admin")
    org_store.set_active_org(worker, oid)

    # Une org VOISINE, enfilée elle aussi : le total servi ne doit jamais compter
    # la file de quelqu'un d'autre.
    voisin = "usr_worker_voisin"
    db.upsert_user(voisin, email=f"{voisin}@runner-page.invalid", name=voisin)
    autre = org_store.create_org("Org voisine", created_by=voisin)
    org_store.add_org_member(autre, voisin, "org_admin")
    db.enqueue_job(autre, "start", payload={"procedure": "pas-la-notre"})

    attendus = [db.enqueue_job(oid, "start", payload={"procedure": f"p{i}"})["id"]
                for i in range(_JOBS)]
    return {"sub": worker, "org_id": oid, "ids": attendus}


def _liste(client, sub: str, **corps) -> dict:
    r = client.post("/api/me/runner/jobs", headers=_h(sub),
                    json={"op": "list", **corps})
    assert r.status_code == 200, r.text
    return r.json()


# ── la vérité du plafond ──────────────────────────────────────────────────────

def test_une_page_tronquee_le_DIT_par_son_total_et_son_curseur(client, flotte):
    """Demander plus que le plafond ne rend pas plus — et la réponse doit dire
    qu'il reste des lignes. Sans ça, `len(jobs)` se lit comme le compte de la file,
    et un bilan de vague sous-déclare en silence."""
    page = _liste(client, flotte["sub"], limit=1000)

    assert len(page["jobs"]) == 200, \
        "le plafond de page reste 200 — c'est sa DISSIMULATION qui est le défaut"
    assert page.get("total") == _JOBS, (
        "la réponse doit porter le total de la file (filtre appliqué) : c'est le "
        f"chiffre qu'un bilan vient chercher, {page.get('total')!r} ne le dit pas")
    assert page.get("next_cursor"), \
        "une page pleine alors qu'il reste des jobs DOIT rendre un curseur"


def test_le_total_est_scope_a_lorg_de_lappelant(client, flotte):
    """Un total qui déborderait sur la file d'une autre org serait pire qu'aucun
    total : le scope de la liste est déjà l'org, le compte suit le même scope."""
    page = _liste(client, flotte["sub"], limit=1)
    assert page["total"] == _JOBS, page


def test_le_curseur_parcourt_TOUTE_la_file_sans_doublon_ni_trou(client, flotte):
    """La pagination n'est utile que si elle referme : marcher de page en page rend
    chaque job une fois et exactement une."""
    vus: list[int] = []
    curseur = None
    for _ in range(10):                       # borne de sécurité, jamais atteinte
        corps = {"limit": 50}
        if curseur:
            corps["cursor"] = curseur
        page = _liste(client, flotte["sub"], **corps)
        vus += [j["id"] for j in page["jobs"]]
        curseur = page.get("next_cursor")
        if not curseur:
            break
    assert curseur is None or curseur == "", \
        "la marche doit se terminer : le dernier curseur est nul"
    assert len(vus) == len(set(vus)) == _JOBS, \
        f"{len(vus)} lignes parcourues, {len(set(vus))} distinctes, {_JOBS} attendues"
    assert set(vus) == set(flotte["ids"])


def test_le_filtre_de_statut_se_retrouve_dans_le_total(client, flotte):
    """Le total décrit la MÊME population que la page : filtre appliqué, pas la
    file entière — sinon « 3 sur 12 000 » sur un écran qui en montre 3 sur 3."""
    page = _liste(client, flotte["sub"], status="done", limit=10)
    assert page["jobs"] == [] and page["total"] == 0, page
    assert page.get("next_cursor") is None


def test_un_curseur_illisible_est_un_refus_nomme_pas_un_500(client, flotte):
    """Un curseur abîmé se refuse en le disant. Le repli muet (repartir du début)
    reservirait la première page en boucle sans que personne ne le voie."""
    r = client.post("/api/me/runner/jobs", headers=_h(flotte["sub"]),
                    json={"op": "list", "cursor": "pas-un-curseur"})
    assert r.status_code == 400, r.text
    assert r.json().get("error") == "invalid_cursor", r.text
