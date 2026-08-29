"""La lentille de la fenêtre L7 contre un VRAI PostgreSQL — le 500 du 2026-08-29.

**Ce que la suite statique ne pouvait pas voir.** Les tests de la PR 1 stubbaient la
base : ils prouvaient la logique de classement, jamais la FORME des lignes qui
remontent. Or le pool a un row factory maison — `db._conn._str_dict_row` — qui
normalise **tout `datetime`/`date` en chaîne ISO** avant que la ligne n'atteigne un
appelant. La lentille rappelait `.isoformat()` par-dessus, ce qui ne peut rendre qu'une
chose : `AttributeError: 'str' object has no attribute 'isoformat'`, c'est-à-dire
« Erreur interne du serveur » pour l'admin qui lit sa fenêtre.

Le déploiement était sain, la table était là, le compteur écrivait — **seule la lecture
cassait**. C'est le mode de panne le plus coûteux à diagnostiquer, et le moins cher à
empêcher : une lecture réelle, sur une vraie base, jusqu'à la validation du modèle SERVI.

Ce test est donc écrit pour rougir sur le code d'avant, et il porte l'invariant maison
plutôt que la ligne fautive : **une ligne rendue par `db.*` ne contient jamais de
datetime**. Y ajouter une conversion, c'est ajouter un bug.

`pg_dsn` (conftest) : `OTO_TEST_PG_DSN`, sinon un conteneur jetable, sinon skip.
"""
from __future__ import annotations

import uuid

import pytest


@pytest.fixture(scope="module")
def live(pg_dsn):
    """Une base JETABLE, le VRAI `init_db()`, le VRAI pool — c'est le pool qui porte le
    row factory, donc s'en passer effacerait précisément ce qu'on vient prouver."""
    import os

    psycopg = pytest.importorskip("psycopg")
    from oto_mcp.db import _conn as dbconn

    name = "oto_l7_" + uuid.uuid4().hex[:8]
    root = psycopg.connect(pg_dsn, autocommit=True)
    root.execute(f'CREATE DATABASE "{name}"')
    dsn = pg_dsn.rsplit("/", 1)[0] + "/" + name

    previous_url, previous_pool = os.environ.get("DATABASE_URL"), dbconn._pool
    os.environ["DATABASE_URL"] = dsn
    dbconn._pool = None
    try:
        from oto_mcp.db import init_db
        init_db()
        yield dsn
    finally:
        if dbconn._pool is not None:
            dbconn._pool.close()
        dbconn._pool = previous_pool
        if previous_url is None:
            os.environ.pop("DATABASE_URL", None)
        else:
            os.environ["DATABASE_URL"] = previous_url
        root.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
        root.close()


def test_le_row_factory_rend_les_horodatages_en_CHAINES(live):
    """L'invariant, énoncé sur la table du lot. Il n'est pas propre à L7 : tout le
    package `db` le respecte, et c'est pour ça qu'aucun autre appelant ne convertit."""
    from oto_mcp.db import access_shadow as db_shadow

    db_shadow.bump_shadow("serper", 7, "accord", 3)
    ligne = db_shadow.read_shadow(days=1)[0]
    for champ in ("day", "first_at", "last_at"):
        assert isinstance(ligne[champ], str), (
            f"`{champ}` remonte en {type(ligne[champ]).__name__} — le row factory du "
            "pool (`db._conn._str_dict_row`) est censé rendre des chaînes ISO. Si "
            "cet invariant change, ce sont TOUS les appelants de `db.*` qu'il faut "
            "relire, pas seulement cette lentille.")


def test_la_lentille_rend_la_fenetre_sans_500(live):
    """Le rejeu de l'incident, bout en bout : on écrit dans le compteur, puis on lit
    par le handler SERVI, et on valide la réponse par le modèle déclaré — c'est ce
    dernier pas qui reproduit ce que voit l'appelant, pas un `dict` de plus."""
    from oto_mcp.capabilities import access_shadow_admin as lentille
    from oto_mcp.db import access_shadow as db_shadow

    db_shadow.bump_shadow("hunter", 0, "restriction_acl", 1,
                          {"sub_h": "abcd1234", "ancien": "aucun",
                           "chaine": "org/org"})
    # `origine="toutes"` : ce test porte sur la FORME de la réponse, pas sur le
    # périmètre du verdict — la ligne qu'il vient d'écrire doit lui revenir quelle que
    # soit l'origine que le process se donne dans le banc complet.
    out = lentille._read(None, lentille.AccessShadowInput(op="read", days=1,
                                                          origine="toutes"))
    servi = lentille.ShadowOut(**out)          # la validation de la face servie

    ligne = [l for l in servi.lignes if l.connector == "hunter"][0]
    assert ligne.classe == "restriction_acl" and ligne.n == 1
    assert ligne.sample["sub_h"] == "abcd1234"
    # Les horodatages sortent tels quels — des chaînes ISO, pas des objets ni None.
    assert ligne.first_at and ligne.first_at.startswith(ligne.day)
    assert ligne.last_at and ligne.last_at.startswith(ligne.day)


def test_le_verdict_refuse_la_porte_sur_une_fenetre_MUETTE(live):
    """La garde du lot, vérifiée sur une vraie base : zéro observation ne doit JAMAIS
    se lire « porte ouverte ». Une fenêtre muette et une fenêtre concluante rendent
    toutes deux zéro `inconnu` — les distinguer est tout l'intérêt du bloc."""
    from oto_mcp.capabilities import access_shadow_admin as lentille

    vide = lentille._verdict([])
    assert vide["observations"] == 0 and vide["porte_ouverte"] is False

    plein = lentille._verdict([{"classe": "accord", "n": 12}])
    assert plein["porte_ouverte"] is True

    avec_inconnu = lentille._verdict([{"classe": "accord", "n": 12},
                                      {"classe": "inconnu", "n": 1}])
    assert avec_inconnu["inconnus"] == 1 and avec_inconnu["porte_ouverte"] is False
