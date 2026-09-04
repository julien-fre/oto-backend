"""Un jeton de délégation n'apparaît pas dans les jetons de l'utilisateur.

⚠️ **Constaté à l'écran le 04/09** : la page « cli & api tokens » — qui annonce
*« long-lived tokens for the oto cli and ci environments »* — listait
`runner job 11936`, `runner job 11935`, `runner job 11934`… Des jetons de douze
minutes, émis automatiquement, un par travail exécuté.

Trois défauts d'un coup :

1. **l'écran ment** sur ce qu'il montre ;
2. **ils ne disparaissent jamais** — rien ne purgeait les expirés, et
   l'accumulation est mécanique ;
3. **le bouton « révoquer » est actif dessus** : quelqu'un qui fait le ménage
   dans ses jetons peut couper un accès en cours d'usage, sans rien pour le
   prévenir de ce qu'il casse.

C'est la famille documentée le 04/09 : **un objet qui se présente comme un
autre**. Deux choses de nature différente dans une même liste, rendues à
l'identique.
"""
from __future__ import annotations

import uuid

import pytest


@pytest.fixture()
def live(pg_dsn):
    import os

    psycopg = pytest.importorskip("psycopg")
    from oto_mcp.db import _conn as dbconn

    nom = "oto_jetons_" + uuid.uuid4().hex[:8]
    root = psycopg.connect(pg_dsn, autocommit=True)
    root.execute(f'CREATE DATABASE "{nom}"')
    dsn = pg_dsn.rsplit("/", 1)[0] + "/" + nom
    url_avant, pool_avant = os.environ.get("DATABASE_URL"), dbconn._pool
    os.environ["DATABASE_URL"] = dsn
    dbconn._pool = None
    try:
        from oto_mcp.db import init_db
        init_db()
        yield dsn
    finally:
        if dbconn._pool is not None:
            dbconn._pool.close()
        dbconn._pool = pool_avant
        if url_avant is None:
            os.environ.pop("DATABASE_URL", None)
        else:
            os.environ["DATABASE_URL"] = url_avant
        root.execute(f'DROP DATABASE IF EXISTS "{nom}" WITH (FORCE)')
        root.close()


@pytest.fixture()
def sub(live):
    from oto_mcp import db
    s = "usr_jetons_" + uuid.uuid4().hex[:6]
    db.upsert_user(s, email=f"{s}@jetons.invalid", name=s)
    return s


def test_la_liste_de_l_utilisateur_ignore_les_delegations(live, sub):
    """⚠️ LE test : c'est ce que montrait la capture d'écran."""
    from oto_mcp import db

    db.create_api_token(sub, label="mon jeton de CLI")
    for i in (11934, 11935, 11936):
        db.create_api_token(sub, label=f"runner job {i}", ttl_seconds=720,
                            kind="delegation")

    libelles = [t["label"] for t in db.list_api_tokens(sub)]
    assert libelles == ["mon jeton de CLI"], (
        f"des jetons d'exécution s'affichent parmi ceux de l'utilisateur : {libelles}")


def test_un_libelle_ne_suffit_PAS_a_distinguer(live, sub):
    """⚠️ Le contrôle qui justifie la colonne. Un utilisateur peut nommer SON
    jeton « runner job 42 » — un filtre sur le libellé le ferait disparaître de
    sa propre liste. `label` est du texte libre ; filtrer dessus n'est pas une
    garantie, c'est une convention qu'on espère."""
    from oto_mcp import db

    db.create_api_token(sub, label="runner job 42")   # c'est le SIEN
    libelles = [t["label"] for t in db.list_api_tokens(sub)]
    assert libelles == ["runner job 42"], (
        "le jeton de l'utilisateur a disparu : le tri se fait sur le libellé, "
        "pas sur l'origine")


def test_les_delegations_EXPIREES_sont_purgees(live, sub):
    """Un jeton mort est inutilisable ; l'accumulation est mécanique — un par
    travail exécuté."""
    from oto_mcp import db

    db.create_api_token(sub, label="runner job 1", ttl_seconds=1, kind="delegation")
    with db._conn._connect() as c:      # on force l'échéance dans le passé
        c.execute("UPDATE user_api_tokens SET expires_at = NOW() - interval '1 hour' "
                  "WHERE sub = %s", (sub,))
    assert db.purger_delegations_expirees(sub) == 1
    assert db.purger_delegations_expirees(sub) == 0, "la purge doit être idempotente"


def test_un_jeton_d_UTILISATEUR_expire_n_est_PAS_purge(live, sub):
    """⚠️ Le contrôle symétrique. Le sien lui appartient : il doit pouvoir
    constater qu'il a expiré. Le faire disparaître en silence serait une
    surprise — et on ne purge jamais ce que quelqu'un a créé de sa main."""
    from oto_mcp import db

    db.create_api_token(sub, label="mon jeton expiré", ttl_seconds=1)
    with db._conn._connect() as c:
        c.execute("UPDATE user_api_tokens SET expires_at = NOW() - interval '1 hour' "
                  "WHERE sub = %s", (sub,))
    assert db.purger_delegations_expirees(sub) == 0
    assert [t["label"] for t in db.list_api_tokens(sub)] == ["mon jeton expiré"]


def test_un_jeton_de_delegation_VIVANT_reste_utilisable(live, sub):
    """⚠️ Sans ça, on aurait rendu invisible ET inopérant : le worker doit
    continuer de s'en servir, c'est tout le mécanisme."""
    from oto_mcp import db

    jeton = db.create_api_token(sub, label="runner job 7", ttl_seconds=720,
                                kind="delegation")
    vu = db.verify_api_token(jeton)
    assert vu is not None and vu["sub"] == sub
