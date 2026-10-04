"""Le marqueur « (origine inconnue) » ne tient plus lieu de valeur (otomata-tech/oto#116).

Un texte pour l'œil humain, logé à l'emplacement d'une valeur, est indistinguable
d'une valeur : un écran de restitution l'a comparé à la valeur courante et en a conclu
qu'une donnée fournie par un tiers avait été corrigée. Trois moitiés, trois bancs :

1. **plus personne ne l'écrit ni ne le lit** — aucun texte du paquet ne porte la
   chaîne, et le texte servi dit comment l'absence d'origine se lit : par l'absence
   de la couche ;
2. **le juge de la reprise** (`scripts/purger_origine_inconnue.py`), pur : ce qu'il
   retire, ce qu'il laisse, ce qu'il refuse de deviner ;
3. **la reprise sur une vraie base** — à blanc n'écrit rien, `--apply` reprend par
   lots, journalise chaque case sous le service de reprise, et une relance ne trouve
   plus rien. La requête de constat compte comme la passe à blanc.
"""
from __future__ import annotations

import ast
import pathlib
import sys
import uuid

import pytest

# La racine du dépôt n'est pas dans `sys.path` en CI (pas de `__init__.py`) : `scripts`
# ne s'importait que parce qu'un banc collecté avant l'y avait mise (oto#116).
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))
from scripts import purger_origine_inconnue as reprise  # noqa: E402


M = reprise.MARQUEUR
RACINE = pathlib.Path(__file__).resolve().parents[2]


# ── 1. plus personne ne l'écrit ni ne le lit ──────────────────────────────────

def test_aucun_texte_du_paquet_ne_porte_le_marqueur():
    """Ni constante, ni requête, ni phrase servie : le seul endroit où la chaîne vit
    encore est le script qui la retire. Une chaîne qui reviendrait dans `oto_mcp/`
    serait un écrivain, ou un lecteur qui compare — les deux défauts de l'issue."""
    trouves = []
    for chemin in sorted((RACINE / "oto_mcp").rglob("*.py")):
        for n in ast.walk(ast.parse(chemin.read_text(encoding="utf-8"))):
            if isinstance(n, ast.Constant) and isinstance(n.value, str) and M in n.value:
                trouves.append(f"{chemin.relative_to(RACINE)}:{n.lineno}")
    assert not trouves, f"le marqueur est revenu dans le paquet : {trouves}"


def test_le_texte_servi_dit_que_l_absence_de_couche_est_l_absence_d_origine():
    from oto_mcp.datastore import schema as dsv2
    fr = dsv2.description_parametre_origine()
    en = dsv2.description_parametre_origine(en=True)
    assert "sans couche `origine`" in fr and "without an `origine` layer" in en
    # et elle ne promet toujours rien d'inconditionnel (9cd0df19)
    assert "l'origine est conservée" not in fr and "the origin is kept" not in en


def test_la_pose_de_schema_ne_rend_plus_de_compte_de_marqueurs():
    """La clé `origines_capturees` comptait des pertes sous un nom de succès ; son
    écrivain parti, elle part avec lui."""
    source = (RACINE / "oto_mcp/datastore/schema_ops.py").read_text(encoding="utf-8")
    assert "origines_capturees" not in source
    from oto_mcp.db import datastore as dsdb
    assert not hasattr(dsdb, "datastore_capturer_origine")


# ── 2. le juge de la reprise ──────────────────────────────────────────────────

@pytest.mark.parametrize("avant, apres", [
    # plate enveloppée par le balayage : elle redevient plate
    ({"valeur": "ACME", "origine": M}, "ACME"),
    ({"valeur": 0, "origine": M}, 0),
    ({"valeur": ["a", "b"], "origine": M}, ["a", "b"]),
    # d'autres couches : seule l'origine part
    ({"valeur": "ACME", "origine": M, "comment": "registre"},
     {"valeur": "ACME", "comment": "registre"}),
    # le vide assumé a besoin de son enveloppe
    ({"valeur": "", "origine": M, "oto.vide_assume": True},
     {"valeur": "", "oto.vide_assume": True}),
    # une valeur objet reste enveloppée : déballée, elle se lirait comme des couches
    ({"valeur": {"comment": "x"}, "origine": M}, {"valeur": {"comment": "x"}}),
    # un json plat où le balayage avait glissé l'origine : rendu tel qu'avant
    ({"a": 1, "origine": M}, {"a": 1}),
    # la valeur effacée depuis : il ne restait que le marqueur
    ({"origine": M}, None),
])
def test_la_case_perd_sa_couche_et_garde_tout_le_reste(avant, apres):
    data, champs = reprise.sans_le_marqueur({"c": avant, "autre": "intacte"})
    assert champs == ["c"]
    assert data == {"c": apres, "autre": "intacte"}


@pytest.mark.parametrize("cellule", [
    {"valeur": "x", "origine": "ACME"},                   # une vraie origine
    {"valeur": "x", "origine": {"valeur": M}},            # origine en version objet
    {"valeur": M},                                        # le texte EST la valeur
    {"valeur": "x", "comment": M},                        # dans une autre couche
    M,                                                    # une valeur plate
    [{"email": {"valeur": "a@b.invalid", "origine": M}}],  # dans un élément de liste
])
def test_hors_du_motif_du_balayage_rien_n_est_devine(cellule):
    data, champs = reprise.sans_le_marqueur({"c": cellule})
    assert champs == [] and data == {"c": cellule}


def test_une_ligne_qui_n_est_pas_un_objet_est_rendue_telle_quelle():
    assert reprise.sans_le_marqueur(["x"]) == (["x"], [])


# ── 3. la reprise sur une vraie base ──────────────────────────────────────────

SUB = "sub-origine-inconnue-116"


def _sql(requete: str, *params):
    from oto_mcp.db._conn import _connect
    with _connect() as conn:
        cur = conn.execute(requete, params or None)
        return cur.fetchall() if cur.description else None


@pytest.fixture(scope="module")
def base(live):
    """Deux tableaux, des lignes posées À LA MAIN comme le balayage les laissait —
    aucun chemin du serveur ne sait plus en écrire."""
    from oto_mcp import db
    from psycopg.types.json import Jsonb
    db.upsert_user(SUB, email=f"{SUB}@exemple.invalid", name=SUB)
    ns = [db.create_datastore("user", SUB, f"oi116-{uuid.uuid4().hex[:6]}")
          for _ in range(2)]
    lignes = {
        (ns[0], "a"): {"nom": {"valeur": "A", "origine": M}, "ville": "Lyon"},
        (ns[0], "b"): {"nom": {"valeur": "B", "origine": M, "comment": "registre"},
                       "effectif": {"valeur": 3, "origine": M}},
        (ns[0], "c"): {"nom": {"valeur": "C", "origine": "C d'import"}},
        (ns[0], "d"): {"note": {"valeur": M}},
        (ns[1], "e"): {"nom": {"origine": M}},
        (ns[1], "f"): {"nom": "F"},
    }
    for (n, rid), data in lignes.items():
        _sql("INSERT INTO datastore_rows (ns_id, row_id, data) VALUES (%s, %s, %s)",
             n, rid, Jsonb(data))
    return ns


def _data(ns_id: int, row_id: str):
    return _sql("SELECT data, rev FROM datastore_rows WHERE ns_id = %s AND row_id = %s",
                ns_id, row_id)[0]


def _constat(ns: list[int]) -> dict:
    return {r["ns_id"]: (r["lignes_a_reprendre"], r["hors_motif"])
            for r in _sql(reprise.CONSTAT_SQL) if r["ns_id"] in ns}


def test_a_blanc_rien_n_est_ecrit_et_le_constat_compte_pareil(base):
    avant = {rid: _data(base[0], rid) for rid in "abcd"}
    bilan = reprise.executer(apply=False, taille=2, dire=lambda _: None)
    assert {rid: _data(base[0], rid) for rid in "abcd"} == avant
    assert bilan["par_tableau"] == {base[0]: 2, base[1]: 1}
    assert bilan["cellules"] == 4
    assert (base[0], "d") in bilan["hors_motif"]
    assert _constat(base) == {base[0]: (2, 1), base[1]: (1, 0)}


def test_apply_reprend_par_lots_journalise_et_ne_trouve_plus_rien_ensuite(base):
    rev_avant = _data(base[0], "b")["rev"]
    bilan = reprise.executer(apply=True, taille=2, dire=lambda _: None)
    assert bilan["lignes"] == 3 and bilan["cellules"] == 4

    assert _data(base[0], "a")["data"] == {"nom": "A", "ville": "Lyon"}
    b = _data(base[0], "b")
    assert b["data"] == {"nom": {"valeur": "B", "comment": "registre"}, "effectif": 3}
    assert b["rev"] > rev_avant
    assert _data(base[1], "e")["data"] == {"nom": None}
    # ce qui n'est pas l'œuvre du balayage n'a pas bougé
    assert _data(base[0], "c")["data"] == {"nom": {"valeur": "C", "origine": "C d'import"}}
    assert _data(base[0], "d")["data"] == {"note": {"valeur": M}}
    assert _data(base[1], "f")["data"] == {"nom": "F"}

    # le journal des révisions dit qui a repris, et garde l'état d'avant
    revs = _sql("SELECT row_id, source, acteur, geste_id, diff "
                "FROM datastore_row_revisions WHERE ns_id = ANY(%s) "
                "AND acteur = %s ORDER BY id", base, f"service:{reprise.SERVICE}")
    assert sorted(r["row_id"] for r in revs) == ["a", "b", "e"]
    assert {r["source"] for r in revs} == {"system"}
    assert len({r["geste_id"] for r in revs}) == 1, "UNE reprise, UN geste"
    diff_a = next(r["diff"] for r in revs if r["row_id"] == "a")
    assert diff_a == {"nom": {"avant": {"valeur": "A", "origine": M}, "apres": "A"}}

    # idempotent : la relance ne trouve plus rien à reprendre
    encore = reprise.executer(apply=True, taille=2, dire=lambda _: None)
    assert encore["lignes"] == 0
    assert _constat(base) == {base[0]: (0, 1)}
    # et la commande entière, telle que le lanceur la joue, sort proprement
    assert reprise.main(["--taille-lot", "2"]) == 0


def test_les_parametres_invalides_sont_refuses_sans_rien_lire():
    assert reprise.main(["--depuis", "pas-une-cle"]) == 2
    assert reprise.main(["--taille-lot", "0"]) == 2
