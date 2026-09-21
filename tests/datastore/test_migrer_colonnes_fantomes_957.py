"""L'outil de migration des colonnes fantômes (oto-backend#957) — contre un VRAI
PostgreSQL, schéma réel (`live`) : ce qui compte, ce sont les LIGNES réellement
écrites, sautées ou laissées, pas le code retour.

Épreuves rouges jouées contre la version d'origine du script (e5ac0e2a) : la course
lecture/écriture (`test_une_ecriture_concurrente_n_est_pas_ecrasee`) et la colonne
`json` métier (`test_json_declare_*`, `test_objet_metier_non_declare_*`) y échouent —
la première réécrivait `effectif` depuis une lecture périmée, la seconde glissait
`comment` DANS l'objet du client."""
from __future__ import annotations

import json
import pathlib
import sys
import threading
import uuid

import psycopg
import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))
import scripts.migrer_colonnes_fantomes as outil  # noqa: E402
from scripts.migrer_colonnes_fantomes import Refus, executer, restaurer  # noqa: E402


@pytest.fixture()
def pg(live, pg_module_dsn):
    with psycopg.connect(pg_module_dsn, autocommit=True) as c:
        yield c


def _tableau(c, schema=None) -> int:
    return c.execute(
        "INSERT INTO user_datastores (owner_type, owner_id, namespace, schema) "
        "VALUES ('user', 'banc-957', %s, %s::jsonb) RETURNING id",
        (f"t-{uuid.uuid4().hex[:8]}", json.dumps(schema) if schema else None),
    ).fetchone()[0]


def _ins(c, ns, rid, data):
    c.execute("INSERT INTO datastore_rows (ns_id, row_id, data, updated_at) "
              "VALUES (%s, %s, %s::jsonb, NOW() - interval '1 day')",
              (ns, rid, json.dumps(data)))


def _data(c, ns, rid):
    return c.execute("SELECT data FROM datastore_rows WHERE ns_id=%s AND row_id=%s",
                     (ns, rid)).fetchone()[0]


def _meta(c, ns, rid):
    return c.execute("SELECT rev, updated_at FROM datastore_rows "
                     "WHERE ns_id=%s AND row_id=%s", (ns, rid)).fetchone()


def _apply(ns, champ, couche, tmp_path, **kw):
    return executer(ns, champ, couche, apply=True, sauvegarde=str(tmp_path), **kw)


def test_a_blanc_ne_touche_a_rien_mais_classe(pg):
    ns = _tableau(pg)
    _ins(pg, ns, "a", {"effectif": 42, "effectif_comment": "registre 2023"})
    r = executer(ns, "effectif", "comment")
    assert r["pop1"] == ["a"] and r["migrees"] == []
    assert _data(pg, ns, "a") == {"effectif": 42, "effectif_comment": "registre 2023"}


def test_population_1_migree_et_updated_at_avance(pg, tmp_path):
    ns = _tableau(pg)
    _ins(pg, ns, "a", {"effectif": 42, "effectif_origine": "registre — tranche 01"})
    rev0, maj0 = _meta(pg, ns, "a")
    r = _apply(ns, "effectif", "origine", tmp_path)
    assert r["migrees"] == ["a"] and r["restantes"] == []
    assert _data(pg, ns, "a") == {"effectif": {"valeur": 42,
                                               "origine": "registre — tranche 01"}}
    rev1, maj1 = _meta(pg, ns, "a")
    assert rev1 > rev0 and maj1 > maj0


def test_le_fantome_garde_son_type(pg, tmp_path):
    ns = _tableau(pg)
    _ins(pg, ns, "a", {"effectif": 42, "effectif_origine": 40})
    _apply(ns, "effectif", "origine", tmp_path)
    assert _data(pg, ns, "a")["effectif"] == {"valeur": 42, "origine": 40}


def test_socle_deja_a_couches_garde_les_autres(pg, tmp_path):
    ns = _tableau(pg)
    _ins(pg, ns, "a", {"effectif": {"valeur": 42, "comment": "INSEE"},
                       "effectif_origine": "DUPONT"})
    r = _apply(ns, "effectif", "origine", tmp_path)
    assert r["migrees"] == ["a"]
    assert _data(pg, ns, "a") == {"effectif": {"valeur": 42, "comment": "INSEE",
                                               "origine": "DUPONT"}}


def test_population_2_conflit_jamais_touchee(pg, tmp_path):
    ns = _tableau(pg)
    avant = {"effectif": {"valeur": 42, "origine": "INSEE"},
             "effectif_origine": "registre"}
    _ins(pg, ns, "a", avant)
    r = _apply(ns, "effectif", "origine", tmp_path, purger_vides=True)
    assert r["pop2_conflit"] == ["a"] and r["migrees"] == r["fantomes_retires"] == []
    assert r["conflits"] == [{"row_id": "a", "couche": "INSEE", "fantome": "registre"}]
    assert _data(pg, ns, "a") == avant


def test_population_2_egale_le_fantome_se_retire_sans_perte(pg, tmp_path):
    ns = _tableau(pg)
    _ins(pg, ns, "a", {"effectif": {"valeur": 42, "comment": "INSEE"},
                       "effectif_comment": "INSEE"})
    _ins(pg, ns, "b", {"effectif": {"valeur": 42, "comment": 7},
                       "effectif_comment": "7"})        # au type près : conflit
    r = _apply(ns, "effectif", "comment", tmp_path)
    assert r["pop2_egale"] == ["a"] and r["fantomes_retires"] == ["a"]
    assert r["pop2_conflit"] == ["b"]
    assert _data(pg, ns, "a") == {"effectif": {"valeur": 42, "comment": "INSEE"}}
    assert "effectif_comment" in _data(pg, ns, "b")


def test_population_3_retiree_seulement_avec_purger_vides(pg, tmp_path):
    ns = _tableau(pg)
    _ins(pg, ns, "a", {"effectif": 42, "effectif_origine": ""})
    r = _apply(ns, "effectif", "origine", tmp_path)
    assert r["pop3"] == ["a"] and r["fantomes_retires"] == []
    assert "effectif_origine" in _data(pg, ns, "a")
    r = _apply(ns, "effectif", "origine", tmp_path, purger_vides=True)
    assert r["fantomes_retires"] == ["a"]
    assert _data(pg, ns, "a") == {"effectif": 42}


def test_objet_metier_non_declare_n_est_jamais_pollue(pg, tmp_path):
    """`{"a":1,"origine":"x"}` n'est PAS une cellule à couches (`names_layers`)."""
    ns = _tableau(pg)
    avant = {"meta": {"a": 1, "origine": "x"}, "meta_comment": "note"}
    _ins(pg, ns, "r", avant)
    r = _apply(ns, "meta", "comment", tmp_path, assumer_provenance_non_datee=True)
    assert _data(pg, ns, "r") == avant
    assert r["socle_opaque"] == ["r"] and r["migrees"] == []


def test_json_declare_est_enveloppe_objet_intact(pg, tmp_path):
    ns = _tableau(pg, {"fields": [{"key": "meta", "type": "json"}]})
    _ins(pg, ns, "r", {"meta": {"a": 1, "origine": "x"}, "meta_comment": "note"})
    r = _apply(ns, "meta", "comment", tmp_path, assumer_provenance_non_datee=True)
    assert _data(pg, ns, "r") == {"meta": {"valeur": {"a": 1, "origine": "x"},
                                           "comment": "note"}}
    assert r["migrees"] == ["r"]


def test_une_ecriture_concurrente_n_est_pas_ecrasee(pg, tmp_path, monkeypatch):
    """Un agent écrit `effectif` ENTRE l'inventaire et l'UPDATE : sa valeur reste."""
    ns = _tableau(pg)
    _ins(pg, ns, "a", {"effectif": 42, "effectif_origine": "registre"})
    _ins(pg, ns, "b", {"effectif": 10, "effectif_origine": "registre"})
    vrai, fait = outil._inventaire, []

    def inventaire_puis_agent(*a, **kw):
        rows = vrai(*a, **kw)
        if not fait:
            fait.append(1)
            pg.execute("UPDATE datastore_rows SET data = data || '{\"effectif\": 99}' "
                       "WHERE ns_id = %s AND row_id = 'a'", (ns,))
        return rows
    monkeypatch.setattr(outil, "_inventaire", inventaire_puis_agent)
    r = _apply(ns, "effectif", "origine", tmp_path)
    assert _data(pg, ns, "a") == {"effectif": 99, "effectif_origine": "registre"}
    assert _data(pg, ns, "b") == {"effectif": {"valeur": 10, "origine": "registre"}}
    assert r["sautees_bougees"] == ["a"] and r["migrees"] == ["b"]


def test_une_ligne_verrouillee_est_sautee_pas_attendue(pg, pg_module_dsn, tmp_path):
    ns = _tableau(pg)
    _ins(pg, ns, "a", {"effectif": 42, "effectif_origine": "registre"})
    tient, relache = threading.Event(), threading.Event()

    def agent():
        with psycopg.connect(pg_module_dsn) as c:
            c.execute("SELECT 1 FROM datastore_rows WHERE ns_id=%s AND row_id='a' "
                      "FOR UPDATE", (ns,))
            tient.set()
            relache.wait(30)
    t = threading.Thread(target=agent)
    t.start()
    tient.wait(10)
    try:
        r = _apply(ns, "effectif", "origine", tmp_path)
    finally:
        relache.set()
        t.join()
    assert r["sautees_verrou"] == ["a"] and r["migrees"] == []
    assert _data(pg, ns, "a") == {"effectif": 42, "effectif_origine": "registre"}


def test_colonne_suffixee_declaree_n_est_pas_un_fantome(pg, tmp_path):
    ns = _tableau(pg, {"fields": [{"key": "effectif"},
                                  {"key": "effectif_comment"}]})
    avant = {"effectif": 42, "effectif_comment": "vraie colonne"}
    _ins(pg, ns, "a", avant)
    with pytest.raises(Refus, match="DÉCLARÉE"):
        _apply(ns, "effectif", "comment", tmp_path, assumer_provenance_non_datee=True)
    assert _data(pg, ns, "a") == avant
    assert list(tmp_path.iterdir()) == []


def test_ligne_sans_champ_mise_a_part(pg, tmp_path):
    """`x_comment` sans `x` : peut venir de la traduction d'un en-tête `x.comment`."""
    ns = _tableau(pg)
    avant = {"site_comment": "colonne importée"}
    _ins(pg, ns, "a", avant)
    r = _apply(ns, "site", "comment", tmp_path, assumer_provenance_non_datee=True,
               purger_vides=True)
    assert r["sans_champ"] == ["a"] and r["migrees"] == []
    assert _data(pg, ns, "a") == avant


def test_couche_liee_a_la_valeur_mise_a_part_sans_levee_explicite(pg, tmp_path):
    ns = _tableau(pg)
    _ins(pg, ns, "a", {"effectif": 42, "effectif_comment": "registre"})
    r = _apply(ns, "effectif", "comment", tmp_path)
    assert r["provenance_non_datee"] == ["a"] and r["migrees"] == []
    assert "effectif_comment" in _data(pg, ns, "a")
    r = _apply(ns, "effectif", "comment", tmp_path, assumer_provenance_non_datee=True)
    assert r["migrees"] == ["a"]
    assert _data(pg, ns, "a") == {"effectif": {"valeur": 42, "comment": "registre"}}


def test_apply_sans_sauvegarde_refuse(pg):
    ns = _tableau(pg)
    _ins(pg, ns, "a", {"effectif": 42, "effectif_origine": "registre"})
    with pytest.raises(Refus, match="sauvegarde"):
        executer(ns, "effectif", "origine", apply=True)
    assert outil.main([str(ns), "effectif", "origine", "--apply"]) == 2
    assert _data(pg, ns, "a") == {"effectif": 42, "effectif_origine": "registre"}


def test_sauvegarde_puis_restauration_aller_retour(pg, tmp_path):
    ns = _tableau(pg)
    lignes = {"p1": {"effectif": 42, "x": 1, "effectif_origine": "registre"},
              "egal": {"effectif": {"valeur": 5, "origine": "A"},
                       "effectif_origine": "A"},
              "vide": {"effectif": 7, "effectif_origine": ""},
              "touchee": {"effectif": 8, "effectif_origine": "B"}}
    for rid, d in lignes.items():
        _ins(pg, ns, rid, d)
    r = _apply(ns, "effectif", "origine", tmp_path, purger_vides=True)
    assert r["migrees"] == ["p1", "touchee"]
    assert r["fantomes_retires"] == ["egal", "vide"]
    sauvegarde = pathlib.Path(r["sauvegarde"])
    assert sauvegarde.parent == tmp_path
    assert len(sauvegarde.read_text().splitlines()) == 4
    # un agent réécrit `touchee` après la migration : la restauration ne l'écrase pas
    pg.execute("UPDATE datastore_rows SET data = data || '{\"effectif\": 9}' "
               "WHERE ns_id = %s AND row_id = 'touchee'", (ns,))
    blanc = restaurer(str(sauvegarde))
    assert blanc["a_restaurer"] == ["p1", "egal", "vide"] and blanc["restaurees"] == []
    assert _data(pg, ns, "p1") == {"effectif": {"valeur": 42, "origine": "registre"},
                                   "x": 1}
    fait = restaurer(str(sauvegarde), apply=True)
    assert fait["restaurees"] == ["p1", "egal", "vide"]
    assert fait["modifiees_depuis"] == ["touchee"]
    for rid in ("p1", "egal", "vide"):
        assert _data(pg, ns, rid) == lignes[rid]
    assert _data(pg, ns, "touchee") == {"effectif": 9}


def test_idempotent_et_borne_au_tableau(pg, tmp_path):
    ns, autre = _tableau(pg), _tableau(pg)
    _ins(pg, ns, "a", {"effectif": 10, "effectif_origine": "registre"})
    _ins(pg, autre, "a", {"effectif": 10, "effectif_origine": "registre"})
    assert _apply(ns, "effectif", "origine", tmp_path)["migrees"] == ["a"]
    second = _apply(ns, "effectif", "origine", tmp_path)
    assert second["inventaire"] == 0 and second["sauvegarde"] is None
    assert _data(pg, autre, "a") == {"effectif": 10, "effectif_origine": "registre"}


def test_couche_inconnue_refusee(pg):
    ns = _tableau(pg)
    with pytest.raises(Refus, match="inconnue"):
        executer(ns, "effectif", "pas_une_couche")
