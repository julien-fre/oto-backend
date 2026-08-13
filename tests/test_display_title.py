"""La colonne qui NOMME une ligne devient une présentation, pas un rôle (#317 étape A).

Décision d'Alexis (« voie Notion ») : une colonne désignée au schéma nomme chaque
ligne. Le logement s'est décidé sur une mesure — sur les 57 titres de production,
**six ne sont pas du texte** (cinq `url`, une `date`). Une valeur de `type` aurait
donc forcé à choisir entre ce que la valeur EST et ce qu'elle SERT à l'écran : un
titre qui est une URL aurait cessé d'être rendu en lien.

D'où `display`, orthogonal à `type`.

⚠️ Le repli sur `role="title"` est TRANSITOIRE : il vit le temps de la conversion des
schémas en base et meurt avec les rôles (étape C). Un repli qui survit à sa raison
devient le canal par lequel ce qu'on retire revient — l'étape C porte le test qui le
vérifie.
"""
from __future__ import annotations

import pytest

from oto_mcp import datastore_schema as dsv2


def test_display_names_the_row_without_touching_its_type():
    """Le fait qui a tranché l'implémentation : un titre peut être une URL."""
    schema = {"fields": [{"key": "profil", "type": "url", "display": "title"}]}
    f = dsv2.title_field(schema)

    assert f["key"] == "profil"
    assert f["type"] == "url", "le type de données survit à la désignation"


@pytest.mark.parametrize("type_", ["text", "url", "date"])
def test_every_real_world_title_type_survives(type_):
    """Les trois types portés par les titres de production, un par un."""
    schema = {"fields": [{"key": "k", "type": type_, "display": "title"}]}
    assert dsv2.title_field(schema)["type"] == type_


def test_the_legacy_role_alone_no_longer_names_a_row():
    """⚠️ **Le repli est MORT** (#317 étape C). Il a vécu le temps de la conversion ;
    le garder ferait qu'un schéma neuf déclarant un rôle continuerait de marcher — et
    le rôle ne serait jamais parti. Un repli qui survit à sa raison devient le canal
    par lequel ce qu'on retire revient.

    La conversion au boot est passée sur les 57 tableaux : plus aucun schéma vivant
    n'est dans ce cas. Celui qui le serait retombe sur la clé métier puis
    l'identifiant, comme un tableau sans titre."""
    schema = {"fields": [{"key": "raison_sociale", "type": "text", "role": "title"}]}
    assert dsv2.title_field(schema) is None


def test_display_wins_over_the_legacy_role():
    """Un schéma converti a les deux (la conversion est ADDITIVE) : c'est `display`
    qui décide, sinon la conversion ne changerait rien."""
    schema = {"fields": [
        {"key": "ancien", "type": "text", "role": "title"},
        {"key": "neuf", "type": "text", "display": "title"},
    ]}
    assert dsv2.title_field(schema)["key"] == "neuf"


def test_no_title_at_all_is_a_normal_state():
    assert dsv2.title_field({"fields": [{"key": "x", "type": "text"}]}) is None
    assert dsv2.title_field(None) is None


def test_two_title_columns_are_refused_at_pose_time():
    """Deux candidats, et le nom d'une ligne dépendrait de l'ordre de déclaration —
    une inférence silencieuse, exactement ce que le retrait des rôles supprime. Aucun
    conflit en production au moment de la bascule : le refus ne casse personne."""
    errors = dsv2.validate_schema_def({"fields": [
        {"key": "a", "type": "text", "display": "title"},
        {"key": "b", "type": "text", "display": "title"}]})

    assert errors and "une seule nomme la ligne" in errors[0]
    assert "a, b" in errors[0], "l'erreur nomme les candidats"


def test_one_title_column_passes():
    assert dsv2.validate_schema_def(
        {"fields": [{"key": "a", "type": "text", "display": "title"}]}) == []


# ── le vocabulaire dérivé doit ramasser `display` tout seul ──────────────────

def test_display_is_seen_as_interpreted_without_editing_any_list():
    """⚠️ Exigence du superviseur, et c'est un contrôle sur la DÉRIVATION elle-même :
    `display` étant désormais lu par le code, la détection des clés non interprétées
    (#316) doit le voir sans qu'on touche à une liste. Si ce test échoue, la
    dérivation était une copie déguisée — un finding, pas une ligne à éditer."""
    assert "display" in dsv2.interpreted_keys()

    # Corollaire : un schéma qui déclare `display` ne doit plus être averti.
    assert dsv2.unknown_declaration_keys(
        {"fields": [{"key": "a", "type": "text", "display": "title"}]}) == []


def test_an_unknown_display_value_is_not_silently_swallowed():
    """`display` est interprété, mais toutes ses valeurs ne le sont pas : une
    présentation inconnue ne nomme rien, et ne doit pas faire croire le contraire."""
    schema = {"fields": [{"key": "a", "type": "text", "display": "vignette"}]}
    assert dsv2.title_field(schema) is None


# ── la conversion des schémas en base (PostgreSQL réel) ─────────────────────

@pytest.fixture(scope="module")
def live(pg_dsn):
    import os
    import uuid

    psycopg = pytest.importorskip("psycopg")
    from oto_mcp.db import _conn as dbconn

    name = "oto_disp_" + uuid.uuid4().hex[:8]
    root = psycopg.connect(pg_dsn, autocommit=True)
    root.execute(f'CREATE DATABASE "{name}"')
    dsn = pg_dsn.rsplit("/", 1)[0] + "/" + name

    prev_url, prev_pool = os.environ.get("DATABASE_URL"), dbconn._pool
    os.environ["DATABASE_URL"] = dsn
    dbconn._pool = None
    try:
        from oto_mcp.db import init_db
        init_db()
        yield init_db
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


def _schema_of(ns_id: int) -> dict:
    from oto_mcp.db._conn import _connect
    with _connect() as conn:
        return conn.execute("SELECT schema FROM user_datastores WHERE id = %s",
                            (ns_id,)).fetchone()["schema"]


def test_the_boot_converts_legacy_titles_additively(live):
    """La conversion est ADDITIVE : `display` s'ajoute, `role` reste. Les lecteurs ont
    déjà basculé, donc le retrait du rôle (étape C) n'aura plus de lecteur à casser —
    le remplacement précède la perte, comme pour la libération par run."""
    import uuid

    from oto_mcp import db
    from oto_mcp.db._conn import _connect

    ns = db.create_datastore_namespace("user", "sub-test", "t-" + uuid.uuid4().hex[:6])
    with _connect() as conn:                  # un schéma d'AVANT la bascule
        conn.execute(
            "UPDATE user_datastores SET schema = %s::jsonb WHERE id = %s",
            ('{"fields": [{"key": "raison_sociale", "type": "text", "role": "title"},'
             ' {"key": "siren", "type": "text"}]}', ns))

    live()                                    # un boot

    champs = _schema_of(ns)["fields"]
    titre = next(f for f in champs if f["key"] == "raison_sociale")
    assert titre["display"] == "title"
    assert titre["role"] == "title", "additif : le rôle reste jusqu'à l'étape C"
    assert titre["type"] == "text", "le type n'est pas touché"
    assert champs[1]["key"] == "siren", "l'ordre des champs est préservé"
    assert "display" not in champs[1], "les autres champs ne sont pas touchés"


def test_the_conversion_is_idempotent(live):
    """Rejouable : un second boot ne réécrit rien (le prédicat exclut ce qui a déjà
    son `display`)."""
    import uuid

    from oto_mcp import db
    from oto_mcp.db._conn import _connect

    ns = db.create_datastore_namespace("user", "sub-test", "t-" + uuid.uuid4().hex[:6])
    with _connect() as conn:
        conn.execute("UPDATE user_datastores SET schema = %s::jsonb WHERE id = %s",
                     ('{"fields": [{"key": "nom", "type": "url", "role": "title"}]}', ns))

    live()
    apres_un = _schema_of(ns)
    live()
    assert _schema_of(ns) == apres_un


def test_a_schema_without_any_title_is_left_alone(live):
    """La conversion ne doit toucher que ce qu'elle vise — un schéma sans titre sort
    du boot rigoureusement identique."""
    import uuid

    from oto_mcp import db
    from oto_mcp.db._conn import _connect

    ns = db.create_datastore_namespace("user", "sub-test", "t-" + uuid.uuid4().hex[:6])
    avant = '{"fields": [{"key": "a", "type": "text"}, {"key": "b", "type": "enum"}]}'
    with _connect() as conn:
        conn.execute("UPDATE user_datastores SET schema = %s::jsonb WHERE id = %s",
                     (avant, ns))

    live()

    assert _schema_of(ns) == {"fields": [{"key": "a", "type": "text"},
                                         {"key": "b", "type": "enum"}]}
