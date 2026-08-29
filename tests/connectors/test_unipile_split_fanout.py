"""Le fan-out 1→N d'un connecteur SCINDÉ, exercé contre un vrai PostgreSQL.

Un split déplace un connecteur dans le REGISTRE ; les tables de gouvernance, elles,
ne connaissent que l'ancien nom. Trois d'entre elles penchent du mauvais côté quand
un nom leur est inconnu, et c'est ce qui rend le fan-out obligatoire plutôt que
soigneux :

- `connector_availability` — pas de ligne ⟹ **OFF** (deny-by-default). Sans fan-out,
  la messagerie hébergée s'éteint pour TOUT LE MONDE au premier boot du split.
- `connector_acl`          — pas de ligne ⟹ **OUVERT** (ADR 0025). Sans fan-out, une
  org qui avait réservé la messagerie à une équipe l'ouvre à tous.
- `user_selected_connectors` — non-sélectionné ⟹ **MASQUÉ** (ADR 0050). Sans
  fan-out, les membres qui l'avaient installée perdent la surface, sans un mot.

Les trois échouent SILENCIEUSEMENT, dans deux directions opposées : deux ferment ce
qui devait rester ouvert, une ouvre ce qui devait rester fermé. Aucune ne lève.

Le test s'exerce contre un vrai PostgreSQL (fixture `pg_dsn`) parce que ce qui casse
ici est la PK : la sélection est keyée `(sub, org_id, connector)` et le fan-out
INSÈRE — une paire qui porte déjà l'une des cibles doit garder SON état, pas faire
échouer la migration. Un stub qui accepte tout passerait sans rien prouver ; c'est la
leçon déjà payée par `test_connector_selection_rename.py`.
"""
from __future__ import annotations

import pytest

from oto_mcp.connectors import activation as act
from oto_mcp.connectors import selection as sel

CANAUX = ("linkedin_unipile", "whatsapp", "telegram",
          "instagram", "messenger", "twitter")


@pytest.fixture()
def conn(pg_dsn):
    """Connexion sur les tables réelles (schémas de production, PK comprises)."""
    psycopg = pytest.importorskip("psycopg")
    from psycopg.rows import dict_row
    with psycopg.connect(pg_dsn, row_factory=dict_row, autocommit=True) as c:
        for t in ("user_selected_connectors", "connector_selection_seeded",
                  "connector_availability", "connector_acl"):
            c.execute(f"DROP TABLE IF EXISTS {t}")
        sel.init_schema(c)
        act.init_schema(c)
        c.execute("""
            CREATE TABLE connector_acl (
                scope_type TEXT NOT NULL CHECK (scope_type IN ('org', 'group')),
                scope_id TEXT NOT NULL,
                connector TEXT NOT NULL,
                principal_type TEXT NOT NULL CHECK (principal_type IN ('group', 'user')),
                principal_id TEXT NOT NULL,
                granted_by TEXT,
                granted_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                PRIMARY KEY (scope_type, scope_id, connector, principal_type, principal_id)
            )""")
        yield c


def _selection(conn) -> dict:
    return {(r["sub"], r["org_id"], r["connector"]): r["state"]
            for r in conn.execute(
                "SELECT sub, org_id, connector, state FROM user_selected_connectors")}


# --- sélection ----------------------------------------------------------------

def test_la_selection_du_compte_se_propage_aux_six_canaux(conn):
    conn.execute("INSERT INTO user_selected_connectors (sub, org_id, connector, state) "
                 "VALUES ('u1', 1, 'unipile', 'active')")
    assert sel.fanout_selection(conn, "unipile", CANAUX) == 6
    vue = _selection(conn)
    for canal in CANAUX:
        assert vue[("u1", 1, canal)] == "active", canal
    # Le compte SURVIT : il porte toujours la clé. C'est ce qui distingue un split
    # d'un renommage — et pourquoi `rename_selection` ne convenait pas.
    assert vue[("u1", 1, "unipile")] == "active"


def test_letat_est_herite_une_pause_reste_une_pause(conn):
    """Un split n'est pas une occasion d'installer : qui avait mis la messagerie en
    pause ne doit pas la voir revenir dans sa toolbox."""
    conn.execute("INSERT INTO user_selected_connectors (sub, org_id, connector, state) "
                 "VALUES ('u1', 1, 'unipile', 'paused')")
    sel.fanout_selection(conn, "unipile", CANAUX)
    vue = _selection(conn)
    assert all(vue[("u1", 1, c)] == "paused" for c in CANAUX)


def test_une_cible_deja_posee_garde_son_etat(conn):
    """LE cas qui casse une migration 1→N : la PK est `(sub, org_id, connector)`.

    Un membre qui avait DÉJÀ pausé `whatsapp` (ou l'a installé depuis) ne doit ni
    faire échouer le fan-out, ni se le voir réactiver par-dessus son choix."""
    conn.execute("INSERT INTO user_selected_connectors (sub, org_id, connector, state) "
                 "VALUES ('u1', 1, 'unipile', 'active'), ('u1', 1, 'whatsapp', 'paused')")
    sel.fanout_selection(conn, "unipile", CANAUX)
    vue = _selection(conn)
    assert vue[("u1", 1, "whatsapp")] == "paused"      # SON choix gagne
    assert vue[("u1", 1, "telegram")] == "active"      # les autres suivent le compte


def test_le_rejeu_est_un_no_op(conn):
    """La base est partagée preprod/prod : un boot doit pouvoir rejouer."""
    conn.execute("INSERT INTO user_selected_connectors (sub, org_id, connector, state) "
                 "VALUES ('u1', 1, 'unipile', 'active')")
    sel.fanout_selection(conn, "unipile", CANAUX)
    avant = _selection(conn)
    assert sel.fanout_selection(conn, "unipile", CANAUX) == 0
    assert _selection(conn) == avant


def test_qui_navait_pas_le_compte_ne_recoit_rien(conn):
    """Le fan-out suit une sélection existante — il n'installe personne."""
    conn.execute("INSERT INTO user_selected_connectors (sub, org_id, connector, state) "
                 "VALUES ('u2', 1, 'serper', 'active')")
    assert sel.fanout_selection(conn, "unipile", CANAUX) == 0
    assert set(_selection(conn)) == {("u2", 1, "serper")}


# --- exposition (les trois scopes) -------------------------------------------

def test_lexposition_suit_aux_trois_scopes(conn):
    """Le master, l'override d'org ET la coupure d'équipe.

    Ne recopier que le master laisserait deux décisions au sol : une org qui avait
    coupé unipile verrait six canaux s'allumer, et une coupure d'équipe — monotone,
    elle ne fait que retrancher — se RELÂCHERAIT."""
    conn.execute(
        "INSERT INTO connector_availability (scope_type, scope_id, connector, enabled) "
        "VALUES ('platform', '', 'unipile', TRUE), "
        "       ('org', '7', 'unipile', FALSE), "
        "       ('group', '9', 'unipile', FALSE)")
    assert act.fanout_availability(conn, "unipile", CANAUX) == 18
    rows = {(r["scope_type"], r["scope_id"], r["connector"]): r["enabled"]
            for r in conn.execute("SELECT scope_type, scope_id, connector, enabled "
                                  "FROM connector_availability")}
    for canal in CANAUX:
        assert rows[("platform", "", canal)] is True, canal
        assert rows[("org", "7", canal)] is False, canal      # l'org avait coupé
        assert rows[("group", "9", canal)] is False, canal    # l'équipe aussi


def test_un_reglage_deja_pose_sur_une_cible_gagne(conn):
    """Un admin qui a déjà tranché sur un canal n'est pas écrasé par la migration."""
    conn.execute(
        "INSERT INTO connector_availability (scope_type, scope_id, connector, enabled) "
        "VALUES ('platform', '', 'unipile', TRUE), "
        "       ('platform', '', 'whatsapp', FALSE)")
    act.fanout_availability(conn, "unipile", CANAUX)
    row = conn.execute("SELECT enabled FROM connector_availability WHERE "
                       "scope_type='platform' AND connector='whatsapp'").fetchone()
    assert row["enabled"] is False


# --- ACL ----------------------------------------------------------------------

def test_lacl_suit_sinon_la_restriction_sevapore(conn):
    """L'ACL est deny-by-default À LA PRÉSENCE : une table vide vaut OUVERT.

    C'est l'inverse des deux autres, et c'est ce qui rend l'oubli invisible — rien
    ne casse, tout s'ouvre. Une org qui avait réservé la messagerie à son équipe
    commerciale l'offrirait à tout le monde, sans geste et sans trace."""
    conn.execute(
        "INSERT INTO connector_acl "
        "  (scope_type, scope_id, connector, principal_type, principal_id, granted_by) "
        "VALUES ('org', '7', 'unipile', 'group', '3', 'admin1')")
    assert act.fanout_acl(conn, "unipile", CANAUX) == 6
    rows = list(conn.execute("SELECT connector, principal_id, granted_by "
                             "FROM connector_acl WHERE scope_type='org' AND scope_id='7'"))
    assert {r["connector"] for r in rows} == {"unipile", *CANAUX}
    # L'audit de la décision d'origine voyage : qui a posé la restriction est un
    # fait, la re-dater du geste de migration serait inventer une décision.
    assert all(r["granted_by"] == "admin1" and r["principal_id"] == "3" for r in rows)


def test_une_org_sans_acl_reste_ouverte(conn):
    """Le fan-out ne FABRIQUE pas de restriction : sans ligne source, rien ne
    naît — sinon le split fermerait la messagerie chez qui ne l'avait jamais
    restreinte."""
    assert act.fanout_acl(conn, "unipile", CANAUX) == 0
    assert not list(conn.execute("SELECT 1 FROM connector_acl"))


# --- la sentinelle : un déménagement est vrai UNE fois -------------------------

def _post_split(conn):
    """L'état que le fan-out du 2026-08-28 a produit : le compte + ses six canaux."""
    for name in ("unipile", *CANAUX):
        conn.execute("INSERT INTO user_selected_connectors (sub, org_id, connector, state) "
                     "VALUES ('u1', 1, %s, 'active')", (name,))


def _noms(conn) -> set:
    return {r["connector"] for r in conn.execute(
        "SELECT connector FROM user_selected_connectors WHERE sub = 'u1'")}


def test_sans_sentinelle_un_canal_retire_reviendrait_au_boot(conn):
    """Le défaut que la sentinelle corrige (#543) — reproduit, pas raconté.

    `ON CONFLICT DO NOTHING` ne protège que les lignes PRÉSENTES. Retirer un canal
    SUPPRIME la sienne, donc le rejeu du fan-out la réinstalle : la garde ne couvrait
    pas le seul cas où elle comptait. Ce test appelle `fanout_selection` NU, comme le
    boot le faisait entre le 28 et le 29/08 — il documente pourquoi l'appel est
    désormais sous `split_fanout_pending`, et rougirait si on l'en ressortait."""
    _post_split(conn)
    conn.execute("DELETE FROM user_selected_connectors "
                 "WHERE sub = 'u1' AND connector = 'whatsapp'")
    sel.fanout_selection(conn, "unipile", CANAUX)
    assert "whatsapp" in _noms(conn)


def test_une_base_deja_migree_est_marquee_sans_reecrire(conn):
    """Le cas de la PROD : elle a reçu le déménagement AVANT que la sentinelle existe.

    Poser le marqueur sans regarder l'aurait fait tourner une dernière fois — donc
    réinstaller une dernière fois ce que les gens venaient de retirer. Le témoin est
    une sélection portant l'un des six canaux : présente ⟹ on marque, on n'écrit pas."""
    _post_split(conn)
    conn.execute("DELETE FROM user_selected_connectors "
                 "WHERE sub = 'u1' AND connector = 'whatsapp'")
    assert sel.split_fanout_pending(conn, CANAUX) is False
    assert "whatsapp" not in _noms(conn), "la sonde a réécrit ce qu'elle devait constater"
    assert conn.execute("SELECT 1 FROM connector_selection_seeded WHERE sub = %s",
                        (sel._SPLIT_MARK,)).fetchone(), "sentinelle non posée"


def test_une_base_neuve_recoit_le_fanout_puis_plus_jamais(conn):
    """L'autre moitié : sans le fan-out, une base neuve perdrait la messagerie. Il
    doit donc tourner UNE fois — et le boot suivant ne doit plus rien réinstaller."""
    conn.execute("INSERT INTO user_selected_connectors (sub, org_id, connector, state) "
                 "VALUES ('u1', 1, 'unipile', 'active')")
    assert sel.split_fanout_pending(conn, CANAUX) is True
    sel.fanout_selection(conn, "unipile", CANAUX)
    sel.mark_split_fanout(conn)
    assert _noms(conn) == {"unipile", *CANAUX}

    conn.execute("DELETE FROM user_selected_connectors "
                 "WHERE sub = 'u1' AND connector = 'whatsapp'")
    assert sel.split_fanout_pending(conn, CANAUX) is False
    assert "whatsapp" not in _noms(conn)


def test_langle_mort_de_la_sonde_est_connu_et_borne(conn):
    """Le témoin est la SÉLECTION seule : une base migrée dont plus aucune ligne ne
    porte l'un des six canaux se relit « pas encore migrée » et rejouerait une
    dernière fois. Vérifié le 2026-08-29 : ne concerne pas la prod (les six y sont
    sélectionnés). Ce test FIGE la limite — si on la referme un jour, il rougit et
    dit où."""
    _post_split(conn)
    for canal in CANAUX:
        conn.execute("DELETE FROM user_selected_connectors "
                     "WHERE sub = 'u1' AND connector = %s", (canal,))
    assert sel.split_fanout_pending(conn, CANAUX) is True
