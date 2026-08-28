"""Dire ce que cette version EXÉCUTE, plutôt que faire deviner ce qu'elle ignore (#389).

Quatrième signal du même jour sur `data_set_schema`, et celui qui rendait les trois
autres dangereux. Il ne demandait pas une contrainte de plus — il demandait de savoir
lesquelles MORDENT.

Deux cas vécus, et le second est le vrai sujet : l'écart n'était pas dans le
vocabulaire mais dans le DÉPLOIEMENT. `max_length: 60` posé sur quatre colonnes d'un
tableau de production, code de validation écrit le jour même, version déployée qui ne
l'exécutait pas encore. Vérifié empiriquement à l'époque : un PATCH idempotent rendait
200 ; avec le code à jour, 75 lignes sur 600 devenaient inécritables. Profil de panne :
effet DIFFÉRÉ au prochain déploiement, MASSIF et SIMULTANÉ, cause vieille de plusieurs
semaines — personne ne relie « les agents n'écrivent plus » à « quelqu'un a posé une
borne un mardi ».

D'où `enforced` : la liste des clés de validation que CETTE version applique, servie à
la pose ET à la lecture du schéma. Un client — humain ou agent — peut alors vérifier
que ce qu'il pose sera exécuté, contre le serveur qui répond, pas contre une doc.

⚠️ Le point qui fait la valeur du relevé : il est établi **en faisant tourner le
validateur**, jamais en recopiant une liste. Une liste parallèle diverge le jour où
quelqu'un exécute une clé de plus (ou cesse d'en exécuter une), et elle se met alors à
mentir dans les deux sens — exactement ce que le signal reproche au silence. C'est ce
que fige `test_le_releve_TOMBE_quand_la_regle_tombe`.
"""
from __future__ import annotations

import pytest

from oto_mcp.datastore import schema as S


@pytest.fixture(autouse=True)
def _cache_propre():
    """Le relevé est mémorisé (il s'exécute une fois) : chaque test repart de zéro."""
    S.reset_enforced_keys()
    yield
    S.reset_enforced_keys()


@pytest.mark.parametrize("cle", [
    "strict", "required", "required_when", "type", "options",
    "max_length", "max_items", "pattern", "lifecycle",
])
def test_les_regles_de_cette_version_sont_annoncees(cle):
    assert cle in S.enforced_keys()


def test_une_cle_de_PRESENTATION_n_y_est_pas():
    """`width`, `hidden`, `label`, `role` sont lues par le client, jamais par oto —
    les annoncer comme exécutées serait la même faute, dans l'autre sens."""
    annoncees = S.enforced_keys()
    for cle in ("width", "hidden", "label", "role", "description", "help"):
        assert cle not in annoncees


def test_le_releve_TOMBE_quand_la_regle_tombe(monkeypatch):
    """La preuve qu'il est DÉRIVÉ et non listé : on désarme la borne dans le code,
    le relevé cesse de l'annoncer. Une liste écrite à la main continuerait, elle, de
    promettre une contrainte que plus rien n'exécute — c'est le défaut du signal."""
    assert "max_length" in S.enforced_keys()
    S.reset_enforced_keys()
    monkeypatch.setattr(S, "max_length_of", lambda field: None)
    assert "max_length" not in S.enforced_keys()
    # …et les autres tiennent : le relevé mesure clé par clé, il ne s'effondre pas.
    assert "required" in S.enforced_keys()


def test_le_releve_est_stable_et_trie():
    assert S.enforced_keys() == sorted(S.enforced_keys())
    assert S.enforced_keys() == S.enforced_keys()


# ── servi par les DEUX faces du schéma ──

def test_la_pose_annonce_ce_qu_elle_fera_respecter(monkeypatch):
    from oto_mcp.datastore import core as dsm
    from oto_mcp.datastore import schema_ops as ops

    st = dsm.DatastorePg("u", acting_org=35)
    monkeypatch.setattr(st, "_resolve", lambda ns, write=False: 7)
    monkeypatch.setattr(st, "_ns_of", lambda ns_id: {})
    monkeypatch.setattr(ops.db, "set_datastore_schema", lambda *a: None)
    monkeypatch.setattr(ops.db, "datastore_key_dup_groups", lambda *a: [])
    monkeypatch.setattr(ops.db, "datastore_drop_key_index", lambda *a: None)
    monkeypatch.setattr(ops.db, "datastore_row_keys", lambda ns_id: [])
    monkeypatch.setattr(ops.db, "datastore_overlong_fields", lambda *a, **k: [])
    monkeypatch.setattr(ops.db, "datastore_offending_enum_values", lambda *a, **k: [])
    out = st.set_schema("viviers", {"fields": [{"key": "x", "type": "text"}]})
    assert out["enforced"] == S.enforced_keys()


def test_la_lecture_l_annonce_aussi(monkeypatch):
    """Sans ça il faudrait ÉCRIRE un schéma pour savoir ce que le serveur exécute —
    un effet de bord pour poser une question."""
    from oto_mcp.capabilities import datastore_schema as CAP
    from oto_mcp.capabilities._types import ResolvedCtx

    monkeypatch.setattr(CAP, "make_store",
                        lambda sub: type("S", (), {
                            "get_schema": staticmethod(lambda ns: {"fields": []})})())
    out = CAP._get_schema(ResolvedCtx(sub="u"), CAP.GetSchemaInput(namespace="v"))
    assert out["enforced"] == S.enforced_keys()
