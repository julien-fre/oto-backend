"""Le JOURNAL des révisions de ligne, jalon M1 — écriture fantôme (oto#273).

Tout se juge sur ce que porte la BASE (`datastore_row_revisions`), par une connexion
fraîche : le déclencheur écrit, rien ne lit, donc aucun appel ne pourrait en témoigner.

Ce que ces bancs tiennent :

1. **le diff** — insertion (tout en `apres`), champ modifié, champ ajouté, champ retiré,
   `null` distinct de l'absence, couche modifiée (valeur entière servie des deux côtés) ;
2. **le silence** — écriture sans effet, bail seul (`claimed_*`, `claims`) : aucune
   révision, alors que `rev` avance sur le bail ;
3. **la durée de vie** — les révisions d'une ligne supprimée restent, et la suppression
   en ajoute une (`suppression`, toutes les valeurs en `avant`, estampillée) ; celles
   d'un tableau supprimé partent, sans que la cascade ne lève ;
4. **l'interrupteur** — `OTO_JOURNAL_REVISIONS=off` coupe le journal pour les
   connexions du processus, une valeur illisible lève ;
5. **le boot** — rejouer la pose ne recrée rien ;
6. **jamais bloquant** — un `data` qui n'est pas un objet JSON s'écrit quand même, sans
   révision, avec un `WARNING` qui nomme la ligne.
"""
from __future__ import annotations

import json
import uuid

import pytest

SUB = "sub-journal-273"


def _sql(requete: str, *params):
    from oto_mcp.db._conn import _connect
    with _connect() as conn:
        cur = conn.execute(requete, params or None)
        return cur.fetchall() if cur.description else None


def _table(ligne: dict) -> int:
    from oto_mcp import db
    ns_id = db.create_datastore("user", SUB, "journal-" + uuid.uuid4().hex[:6])
    db.datastore_insert_row(ns_id, "r1", dict(ligne))
    return ns_id


def _revisions(ns_id: int, row_id: str = "r1") -> list[dict]:
    return _sql("SELECT rev, diff, acteur, run_id, source, geste_id, suppression "
                "FROM datastore_row_revisions WHERE ns_id = %s AND row_id = %s "
                "ORDER BY id", ns_id, row_id)


def _ecrire(ns_id: int, data: dict, row_id: str = "r1") -> None:
    _sql("UPDATE datastore_rows SET data = %s::jsonb, updated_at = now() "
         "WHERE ns_id = %s AND row_id = %s", json.dumps(data), ns_id, row_id)


# ── le diff ────────────────────────────────────────────────────────────────────

def test_insertion_porte_tous_ses_champs_en_apres(live):
    ns_id = _table({"nom": "A", "score": 3})
    (rev,) = _revisions(ns_id)
    # L'estampille (M2) a son banc : `test_estampille_273.py`. Ici, sans contexte.
    assert rev.pop("geste_id")
    assert rev == {"rev": 0, "diff": {"nom": {"apres": "A"}, "score": {"apres": 3}},
                   "acteur": None, "run_id": None, "source": "system",
                   "suppression": False}


def test_mise_a_jour_d_un_champ_ne_journalise_que_lui(live):
    ns_id = _table({"nom": "A", "score": 3})
    _ecrire(ns_id, {"nom": "A", "score": 4})
    revs = _revisions(ns_id)
    assert len(revs) == 2
    assert revs[1]["rev"] == 1
    assert revs[1]["diff"] == {"score": {"avant": 3, "apres": 4}}


def test_ajout_retrait_et_null_sont_trois_etats(live):
    ns_id = _table({"garde": 1, "part": "x", "vide": None})
    _ecrire(ns_id, {"garde": 1, "vide": 2, "neuf": []})
    assert _revisions(ns_id)[-1]["diff"] == {
        "part": {"avant": "x"},                 # retiré : pas de clé `apres`
        "vide": {"avant": None, "apres": 2},    # `null` n'est pas l'absence
        "neuf": {"apres": []},                  # ajouté : pas de clé `avant`
    }


def test_couche_modifiee_porte_la_valeur_entiere(live):
    avant = {"value": "ACME", "origine": {"value": "acme"}}
    apres = {"value": "ACME", "origine": {"value": "acme"}, "comment": "vérifié"}
    ns_id = _table({"societe": avant})
    _ecrire(ns_id, {"societe": apres})
    assert _revisions(ns_id)[-1]["diff"] == {
        "societe": {"avant": avant, "apres": apres}}


def test_upsert_et_retrait_de_colonne_par_le_code_du_datastore(live):
    """Pas seulement une requête à la main : l'upsert (`ON CONFLICT DO UPDATE`, qui
    passe par le déclencheur d'UPDATE) et le retrait de colonne (une révision par
    ligne qui la portait, valeur retirée comprise)."""
    from oto_mcp import db
    ns_id = _table({"statut": "a_faire", "note": "x"})
    db.datastore_upsert_row(ns_id, "r1", {"statut": "fait", "note": "x"})
    assert _revisions(ns_id)[-1]["diff"] == {
        "statut": {"avant": "a_faire", "apres": "fait"}}
    db.datastore_drop_column(ns_id, "note")
    assert _revisions(ns_id)[-1]["diff"] == {"note": {"avant": "x"}}


# ── le silence ─────────────────────────────────────────────────────────────────

def test_ecriture_sans_effet_ne_journalise_rien(live):
    ns_id = _table({"a": 1, "b": [1, 2]})
    _ecrire(ns_id, {"b": [1, 2], "a": 1})   # réordonnée : jsonb normalise
    _sql("UPDATE datastore_rows SET updated_at = now(), embed_dirty = TRUE "
         "WHERE ns_id = %s", ns_id)
    assert len(_revisions(ns_id)) == 1


def test_bail_seul_ne_journalise_rien_mais_avance_rev(live):
    ns_id = _table({"a": 1})
    _sql("UPDATE datastore_rows SET claimed_by = 'w', claimed_run = 'run-x', "
         "claimed_until = now() + interval '5 min', claims = claims + 1 "
         "WHERE ns_id = %s", ns_id)
    _sql("UPDATE datastore_rows SET claimed_by = NULL, claimed_run = NULL, "
         "claimed_until = NULL WHERE ns_id = %s", ns_id)
    assert len(_revisions(ns_id)) == 1
    rev = _sql("SELECT rev FROM datastore_rows WHERE ns_id = %s", ns_id)[0]["rev"]
    assert rev == 2, "le bail avance `rev` : le journal l'ignore, pas la révision"
    _ecrire(ns_id, {"a": 2})
    assert _revisions(ns_id)[-1]["rev"] == 3


# ── la durée de vie ────────────────────────────────────────────────────────────

def test_suppression_de_ligne_garde_ses_revisions_et_en_ajoute_une(live):
    from oto_mcp import db, geste
    ns_id = _table({"a": 1, "note": None})
    _ecrire(ns_id, {"a": 2, "note": None})
    with geste.interne("banc-273"):
        assert db.datastore_delete_row(ns_id, "r1") == {"a": 2, "note": None}
    assert _sql("SELECT 1 FROM datastore_rows WHERE ns_id = %s", ns_id) == []
    revs = _revisions(ns_id)
    assert [(r["rev"], r["suppression"]) for r in revs] == [
        (0, False), (1, False), (1, True)], "la suppression recopie la `rev` qui part"
    derniere = revs[-1]
    assert derniere["diff"] == {"a": {"avant": 2}, "note": {"avant": None}}, \
        "toutes les valeurs en `avant`, `null` compris"
    assert (derniere["source"], derniere["acteur"]) == ("system", "service:banc-273")
    assert derniere["geste_id"], "estampillée par le point de passage"


def test_suppression_hors_serveur_et_ligne_vide(live):
    """Une suppression à la main est journalisée aussi (sans estampille), et une ligne
    sans colonne qui part laisse une révision au diff vide."""
    from oto_mcp import db
    ns_id = _table({"a": 1})
    db.datastore_insert_row(ns_id, "vide", {})
    _sql("DELETE FROM datastore_rows WHERE ns_id = %s", ns_id)
    (r1,) = [r for r in _revisions(ns_id) if r["suppression"]]
    (vide,) = [r for r in _revisions(ns_id, "vide") if r["suppression"]]
    assert (r1["diff"], r1["source"], r1["acteur"]) == ({"a": {"avant": 1}}, None, None)
    assert (vide["diff"], vide["rev"]) == ({}, 0)


def test_suppression_puis_recreation_sous_le_meme_id(live):
    """Supprimée puis recréée : l'historique se lit dans l'ordre des `id`, la
    suppression sépare les deux vies."""
    from oto_mcp import db
    ns_id = _table({"a": 1})
    db.datastore_delete_row(ns_id, "r1")
    db.datastore_insert_row(ns_id, "r1", {"a": 9})
    assert [(r["rev"], r["suppression"], r["diff"]) for r in _revisions(ns_id)] == [
        (0, False, {"a": {"apres": 1}}),
        (0, True, {"a": {"avant": 1}}),
        (0, False, {"a": {"apres": 9}})]


def test_suppression_de_tableau_emporte_son_historique(live):
    """La cascade supprime les lignes, et le déclencheur de suppression de chaque ligne
    s'exécute alors que le tableau n'existe déjà plus : il n'écrit rien (sinon la clé
    étrangère lèverait, et la suppression du tableau avec)."""
    from oto_mcp import db
    ns_id = _table({"a": 1})
    db.datastore_insert_row(ns_id, "r2", {"a": 2})
    voisin = _table({"b": 1})
    _ecrire(ns_id, {"a": 2})
    assert db.delete_datastore_by_id(ns_id)
    assert _sql("SELECT count(*) AS n FROM datastore_row_revisions "
                "WHERE ns_id = %s", ns_id)[0]["n"] == 0
    assert len(_revisions(voisin)) == 1, "le voisin garde le sien"
    # Par une requête à la main aussi, sans passer par le code du tableau.
    autre = _table({"c": 1})
    _sql("DELETE FROM user_datastores WHERE id = %s", autre)
    assert _sql("SELECT count(*) AS n FROM datastore_row_revisions "
                "WHERE ns_id = %s", autre)[0]["n"] == 0


# ── l'interrupteur ─────────────────────────────────────────────────────────────

def test_interrupteur_coupe_le_journal_des_connexions_du_processus(live, monkeypatch):
    import psycopg

    from oto_mcp.db import _conn
    ns_id = _table({"a": 1})
    monkeypatch.setenv("OTO_JOURNAL_REVISIONS", "off")
    options = _conn._connect_options()
    assert "-c oto.journal_revisions=off" in options
    with psycopg.connect(_conn._database_url(), options=options) as conn:
        conn.execute("UPDATE datastore_rows SET data = '{\"a\": 2}'::jsonb "
                     "WHERE ns_id = %s", (ns_id,))
        conn.execute("INSERT INTO datastore_rows (ns_id, row_id, data) "
                     "VALUES (%s, 'r2', '{\"z\": 1}'::jsonb)", (ns_id,))
        conn.execute("DELETE FROM datastore_rows WHERE ns_id = %s AND row_id = 'r2'",
                     (ns_id,))
    assert len(_revisions(ns_id)) == 1
    assert _revisions(ns_id, "r2") == []
    monkeypatch.setenv("OTO_JOURNAL_REVISIONS", "ON")
    assert "journal_revisions" not in _conn._connect_options()


@pytest.mark.parametrize("valeur", ["0", "false", "désactivé", ""])
def test_interrupteur_illisible_leve(monkeypatch, valeur):
    from oto_mcp.db import _conn
    monkeypatch.setenv("OTO_JOURNAL_REVISIONS", valeur)
    with pytest.raises(ValueError, match="OTO_JOURNAL_REVISIONS"):
        _conn._connect_options()


# ── le boot ────────────────────────────────────────────────────────────────────

def test_rejouer_la_pose_ne_recree_rien(live):
    from oto_mcp.db import journal_revisions
    from oto_mcp.db._conn import _connect
    with _connect() as conn:
        assert journal_revisions.poser_journal_des_revisions(conn) == []
    noms = {r["tgname"] for r in _sql(
        "SELECT tgname FROM pg_trigger WHERE tgrelid = 'datastore_rows'::regclass "
        "AND NOT tgisinternal")}
    assert {journal_revisions.DECLENCHEUR_INSERT,
            journal_revisions.DECLENCHEUR_UPDATE,
            journal_revisions.DECLENCHEUR_DELETE} <= noms


# ── jamais bloquant ────────────────────────────────────────────────────────────

def test_data_qui_n_est_pas_un_objet_n_empeche_pas_l_ecriture(live):
    """Aucune face d'écriture du produit ne pose un `data` non objet ; une requête à la
    main le peut. La ligne s'écrit, le journal se tait et le dit (`WARNING`)."""
    import psycopg

    from oto_mcp.db import _conn
    ns_id = _table({"a": 1})
    avertissements: list[str] = []
    with psycopg.connect(_conn._database_url(),
                         options=_conn._connect_options()) as conn:
        conn.add_notice_handler(
            lambda d: avertissements.append(f"{d.severity_nonlocalized} {d.message_primary}"))
        # objet → tableau (UPDATE), tableau → tableau, puis INSERT d'un scalaire
        conn.execute("UPDATE datastore_rows SET data = '[1, 2]'::jsonb "
                     "WHERE ns_id = %s AND row_id = 'r1'", (ns_id,))
        conn.execute("UPDATE datastore_rows SET data = '[3]'::jsonb "
                     "WHERE ns_id = %s AND row_id = 'r1'", (ns_id,))
        conn.execute("INSERT INTO datastore_rows (ns_id, row_id, data) "
                     "VALUES (%s, 'r2', '\"x\"'::jsonb)", (ns_id,))
    assert _sql("SELECT row_id, data FROM datastore_rows WHERE ns_id = %s "
                "ORDER BY row_id", ns_id) == [
        {"row_id": "r1", "data": [3]}, {"row_id": "r2", "data": "x"}]
    assert len(_revisions(ns_id)) == 1, "seule l'insertion objet est journalisée"
    assert _revisions(ns_id, "r2") == []
    # La suppression d'un `data` non objet : la ligne part, le journal se tait et le dit.
    with psycopg.connect(_conn._database_url(),
                         options=_conn._connect_options()) as conn:
        conn.add_notice_handler(
            lambda d: avertissements.append(f"{d.severity_nonlocalized} {d.message_primary}"))
        conn.execute("DELETE FROM datastore_rows WHERE ns_id = %s AND row_id = 'r2'",
                     (ns_id,))
    assert _sql("SELECT 1 FROM datastore_rows WHERE ns_id = %s AND row_id = 'r2'",
                ns_id) == []
    assert _revisions(ns_id, "r2") == []
    assert "suppression non journalisée" in avertissements.pop()
    assert len(avertissements) == 3, avertissements
    assert all(a.startswith("WARNING ") and f"ns_id={ns_id}" in a
               for a in avertissements), avertissements
    assert "row_id=r1" in avertissements[0] and "row_id=r2" in avertissements[2]
    # Retour à un objet : le journal reprend (l'avant n'est pas un objet → rien).
    _ecrire(ns_id, {"a": 5})
    assert len(_revisions(ns_id)) == 1
    _ecrire(ns_id, {"a": 6})
    assert _revisions(ns_id)[-1]["diff"] == {"a": {"avant": 5, "apres": 6}}


# ── la révision Alembic ────────────────────────────────────────────────────────

def _alembic():
    from pathlib import Path

    from alembic.config import Config
    racine = Path(__file__).resolve().parent.parent.parent
    cfg = Config(str(racine / "alembic.ini"))
    cfg.set_main_option("script_location", str(racine / "oto_mcp" / "db" / "migrations"))
    return cfg


def _pose(dsn: str) -> dict:
    """Ce que porte la base : la table, les fonctions, les déclencheurs."""
    import psycopg

    from oto_mcp.db import journal_revisions as j
    with psycopg.connect(dsn) as c:
        table = c.execute("SELECT to_regclass(%s) IS NOT NULL", (j.TABLE,)).fetchone()[0]
        fonctions = c.execute("SELECT count(*) FROM pg_proc WHERE proname = ANY(%s)",
                              ([j.NOM_FONCTION, j.NOM_FONCTION_SUPPRESSION],)).fetchone()[0]
        declencheurs = {r[0] for r in c.execute(
            "SELECT tgname FROM pg_trigger WHERE tgrelid = 'datastore_rows'::regclass "
            "AND tgname = ANY(%s)", ([j.DECLENCHEUR_INSERT, j.DECLENCHEUR_UPDATE,
                                      j.DECLENCHEUR_DELETE],))}
        colonne = table and c.execute(
            "SELECT count(*) FROM information_schema.columns "
            "WHERE table_name = %s AND column_name = %s",
            (j.TABLE, j.COLONNE_SUPPRESSION)).fetchone()[0] == 1
    return {"table": table, "colonne": colonne, "fonctions": fonctions,
            "declencheurs": len(declencheurs)}


_TOUT = {"table": True, "colonne": True, "fonctions": 2, "declencheurs": 3}
_RIEN = {"table": False, "colonne": False, "fonctions": 0, "declencheurs": 0}


def test_la_revision_pose_la_table_se_defait_et_le_boot_repose_tout(live, pg_module_dsn):
    """`0011` crée la table (le boot aussi) ; son retour arrière retire d'abord
    déclencheurs et fonction — sinon la prochaine écriture de ligne lèverait sur une
    table absente — puis la table. Le boot suivant repose tout."""
    import psycopg
    from alembic import command

    from oto_mcp.db import init_db
    from oto_mcp.db import journal_revisions as j
    assert _pose(pg_module_dsn) == _TOUT, "une base NEUVE reçoit tout du démarrage"
    with psycopg.connect(pg_module_dsn, autocommit=True) as c:
        for declencheur in (j.DECLENCHEUR_INSERT, j.DECLENCHEUR_UPDATE,
                            j.DECLENCHEUR_DELETE):
            c.execute(f"DROP TRIGGER {declencheur} ON datastore_rows")
        for fonction in (j.NOM_FONCTION, j.NOM_FONCTION_SUPPRESSION):
            c.execute(f"DROP FUNCTION {fonction}()")
        c.execute(f"DROP TABLE {j.TABLE}")
    cfg = _alembic()
    command.stamp(cfg, "0010_tool_calls_result_shape")
    command.upgrade(cfg, "0011_journal_revisions_ligne")
    assert _pose(pg_module_dsn)["table"], "la révision n'a rien écrit"
    command.downgrade(cfg, "0010_tool_calls_result_shape")
    assert _pose(pg_module_dsn) == _RIEN, "le retour arrière a laissé quelque chose"
    ns_id = _table({"a": 1})                # une écriture de ligne passe sans journal
    _ecrire(ns_id, {"a": 2})
    command.upgrade(cfg, "head")
    init_db()
    assert _pose(pg_module_dsn) == _TOUT
    _ecrire(ns_id, {"a": 3})
    assert _revisions(ns_id) == [{"rev": 2, "diff": {"a": {"avant": 2, "apres": 3}},
                                  "acteur": None, "run_id": None, "source": None,
                                  "geste_id": None, "suppression": False}], \
        "le journal n'écrit plus"


def test_la_revision_0016_pose_la_colonne_se_defait_et_le_boot_repose_tout(
        live, pg_module_dsn):
    """`0016` ajoute `suppression` à une table qui ne l'a pas ; son retour arrière
    retire d'abord le déclencheur et la fonction de suppression — sinon chaque
    suppression de ligne lèverait sur une colonne absente — puis la colonne. Le boot
    suivant repose la colonne AVANT la fonction qui l'écrit."""
    from alembic import command

    from oto_mcp.db import init_db
    cfg = _alembic()
    command.stamp(cfg, "head")
    command.downgrade(cfg, "0015_droits_valeur_obligatoire")
    assert _pose(pg_module_dsn) == {"table": True, "colonne": False, "fonctions": 1,
                                    "declencheurs": 2}
    ns_id = _table({"a": 1})
    _sql("DELETE FROM datastore_rows WHERE ns_id = %s", ns_id)   # passe, sans journal
    assert len(_revisions_brutes(pg_module_dsn, ns_id)) == 1
    command.upgrade(cfg, "0016_journal_suppression")
    assert _pose(pg_module_dsn)["colonne"]
    assert _sql("SELECT suppression FROM datastore_row_revisions WHERE ns_id = %s",
                ns_id) == [{"suppression": False}], "l'existant prend le défaut"
    command.downgrade(cfg, "0015_droits_valeur_obligatoire")
    init_db()                             # le boot rattrape une base sans la révision
    assert _pose(pg_module_dsn) == _TOUT
    command.stamp(cfg, "head")
    db_ns = _table({"b": 1})
    _sql("DELETE FROM datastore_rows WHERE ns_id = %s", db_ns)
    assert [r["suppression"] for r in _revisions(db_ns)] == [False, True]


def _revisions_brutes(dsn: str, ns_id: int) -> list:
    """Sans nommer `suppression`, que la base n'a peut-être pas."""
    import psycopg
    with psycopg.connect(dsn) as c:
        return c.execute("SELECT rev FROM datastore_row_revisions WHERE ns_id = %s",
                         (ns_id,)).fetchall()
