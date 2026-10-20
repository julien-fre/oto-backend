"""`scripts/instructions_en_anglais` : ne réécrit QUE ce que la plateforme a composé.

Une instruction écrite par quelqu'un est à lui, même en français : le plan la laisse
telle quelle. Une instruction composée en français — égale au caractère près à
l'ancienne composition des champs de SA ligne — reçoit la composition anglaise des
mêmes champs.
"""
from __future__ import annotations

import json
import pathlib
import sys

import pytest

from oto_mcp.capabilities import _instruction

# La racine du dépôt n'est pas dans `sys.path` en CI (pas de `__init__.py`) : `scripts`
# ne s'importait que parce qu'un banc collecté avant l'y avait mise (oto#116).
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from scripts import instructions_en_anglais as S  # noqa: E402


def test_un_declencheur_compose_en_francais_est_reecrit_en_anglais():
    t = {"procedure": "veille", "input": S._derivee_fr("veille")}
    assert S.plan_declencheur(t) == _instruction.derivee("veille")


def test_une_instruction_ecrite_par_quelqu_un_n_est_jamais_touchee():
    ecrite = "Lis la procédure `veille` et applique-la, mais seulement le lundi."
    assert S.plan_declencheur({"procedure": "veille", "input": ecrite}) is None
    assert S.plan_campagne({"procedure": "veille", "namespace": "77",
                            "row_filter": None, "input": ecrite}) is None


def test_une_composition_d_une_AUTRE_procedure_n_est_pas_reecrite():
    """Égale à une composition, mais pas à celle des champs de sa ligne."""
    t = {"procedure": "veille", "input": S._derivee_fr("autre")}
    assert S.plan_declencheur(t) is None


def test_une_campagne_composee_est_reecrite_avec_sa_file_et_son_filtre():
    filtre = {"statut": "a_enrichir"}
    f = {"procedure": "enrichissement", "namespace": "77",
         "row_filter": json.dumps(filtre),       # relu en texte selon le pilote
         "input": S._de_file_fr("enrichissement", "77", filtre)}
    assert S.plan_campagne(f) == _instruction.de_file("enrichissement", "77", filtre)


def test_une_campagne_sans_file_retombe_sur_l_instruction_nue():
    f = {"procedure": "veille", "namespace": None, "row_filter": None,
         "input": S._derivee_fr("veille")}
    assert S.plan_campagne(f) == _instruction.derivee("veille")


def test_la_composition_servie_ne_porte_plus_le_marqueur_francais():
    """Sinon un second passage la reclasserait « à relire »."""
    assert S._MARQUEUR not in _instruction.derivee("veille")
    assert S._MARQUEUR not in _instruction.de_file("veille", "77", {"a": 1})


def test_un_filtre_a_plusieurs_cles_relu_de_jsonb_garde_l_ordre_cite():
    """jsonb rend les clés dans SON ordre ; la composition servie gardait celui reçu."""
    servi = {"statut": "a_enrichir", "pays": "FR"}
    relu = json.dumps({"pays": "FR", "statut": "a_enrichir"})
    f = {"procedure": "enrichissement", "namespace": "77", "row_filter": relu,
         "input": S._de_file_fr("enrichissement", "77", servi)}
    assert S.plan_campagne(f) == _instruction.de_file("enrichissement", "77", servi)


def test_un_filtre_cite_qui_n_egale_pas_celui_de_la_ligne_n_est_pas_reecrit():
    f = {"procedure": "enrichissement", "namespace": "77",
         "row_filter": json.dumps({"statut": "autre"}),
         "input": S._de_file_fr("enrichissement", "77", {"statut": "a_enrichir"})}
    assert S.plan_campagne(f) is None


# ── Sur une vraie base : à blanc n'écrit rien, `--appliquer` n'écrit que le plan ──
#
# ⚠️ Préprod et prod partagent la base : ce que le script fait SANS `--appliquer` doit
# être une lecture, vérifiée ici sur le stockage et non sur un double.

_ORG = 9127


def _poser(conn):
    from psycopg.types.json import Jsonb
    ecrite = "Lis la procédure `veille` et applique-la, mais seulement le lundi."
    filtre = {"statut": "a_enrichir"}
    t_composee = conn.execute(
        "INSERT INTO runner_triggers (org_id, sub, procedure, tools, input, cron, next_due) "
        "VALUES (%s, 'usr', 'veille', %s, %s, '0 9 * * *', NOW()) RETURNING id",
        (_ORG, Jsonb([]), S._derivee_fr("veille"))).fetchone()["id"]
    t_ecrite = conn.execute(
        "INSERT INTO runner_triggers (org_id, sub, procedure, tools, input, cron, next_due) "
        "VALUES (%s, 'usr', 'veille', %s, %s, '0 9 * * *', NOW()) RETURNING id",
        (_ORG, Jsonb([]), ecrite)).fetchone()["id"]
    f_composee = conn.execute(
        "INSERT INTO runner_fleets (org_id, sub, label, procedure, tools, input, namespace, row_filter) "
        "VALUES (%s, 'usr', 'c', 'enrichissement', %s, %s, '77', %s) RETURNING id",
        (_ORG, Jsonb([]), S._de_file_fr("enrichissement", "77", filtre),
         Jsonb(filtre))).fetchone()["id"]
    return {"t_composee": t_composee, "t_ecrite": t_ecrite, "f_composee": f_composee,
            "ecrite": ecrite, "filtre": filtre}


def _input(conn, table, id_):
    return conn.execute(f"SELECT input FROM {table} WHERE id = %s", (id_,)).fetchone()["input"]


def test_a_blanc_rien_n_est_ecrit_puis_appliquer_reecrit_le_seul_compose(live, capsys):
    from oto_mcp.db._conn import _connect_autocommit
    with _connect_autocommit() as conn:
        conn.execute("DELETE FROM runner_triggers WHERE org_id = %s", (_ORG,))
        conn.execute("DELETE FROM runner_fleets WHERE org_id = %s", (_ORG,))
        ids = _poser(conn)

    # 1. à blanc (le défaut) : le rapport montre avant/après, la base ne bouge pas
    assert S.main([]) == 0
    sortie = capsys.readouterr().out
    assert "à blanc" in sortie and "base : " in sortie
    assert f"réécrire  id={ids['t_composee']}" in sortie
    assert f"relire    id={ids['t_ecrite']}" in sortie
    assert _instruction.derivee("veille") in sortie   # le texte qui SERAIT écrit, en entier
    with _connect_autocommit() as conn:
        assert _input(conn, "runner_triggers", ids["t_composee"]) == S._derivee_fr("veille")
        assert _input(conn, "runner_fleets", ids["f_composee"]) == \
            S._de_file_fr("enrichissement", "77", ids["filtre"])

    # 2. --appliquer : les composées passent en anglais, l'écrite reste à son auteur
    assert S.main(["--appliquer"]) == 0
    assert "2 réécrite(s), 0 laissée(s)" in capsys.readouterr().out
    with _connect_autocommit() as conn:
        assert _input(conn, "runner_triggers", ids["t_composee"]) == _instruction.derivee("veille")
        assert _input(conn, "runner_fleets", ids["f_composee"]) == \
            _instruction.de_file("enrichissement", "77", ids["filtre"])
        assert _input(conn, "runner_triggers", ids["t_ecrite"]) == ids["ecrite"]

    # 3. idempotent : un second passage n'a plus rien à réécrire
    assert S.main(["--appliquer"]) == 0
    sortie = capsys.readouterr().out
    assert "runner_triggers : 0 à réécrire, 1 à relire" in sortie
    assert "runner_fleets : 0 à réécrire, 0 à relire" in sortie
    assert "0 réécrite(s)" in sortie


def test_une_instruction_retouchee_entre_lecture_et_ecriture_n_est_pas_ecrasee(live, capsys):
    from oto_mcp.db._conn import _connect_autocommit
    with _connect_autocommit() as conn:
        conn.execute("DELETE FROM runner_triggers WHERE org_id = %s", (_ORG,))
        conn.execute("DELETE FROM runner_fleets WHERE org_id = %s", (_ORG,))
        ids = _poser(conn)
        rapport = S.inventaire(conn)
        conn.execute("UPDATE runner_triggers SET input = 'retouchée' WHERE id = %s",
                     (ids["t_composee"],))
        ecrites, bougees = S.appliquer(conn, rapport)
        assert (ecrites, bougees) == (1, 1)
        assert _input(conn, "runner_triggers", ids["t_composee"]) == "retouchée"
