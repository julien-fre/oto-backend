"""Poser un schéma dit désormais ce qu'il EFFACE (#388).

`data_set_schema` pose le schéma ENTIER, sans fusion : tout réglage absent du corps
envoyé disparaît. Le geste qui ne PEUT pas détruire existe depuis le 11/08
(`data_patch_schema`, fusion par clé) — mais `set_schema` reste la bonne façon de
POSER un format, et rien dans sa réponse ne disait ce qu'il venait d'emporter.

⚠️ **Le point qui fait le signal : le mode d'écriture était indétectable côté
appelant.** Sur le même tableau, le même jour, la même session a fait les deux — sa
migration a PRÉSERVÉ 78 notes de champ (elle patchait le schéma relu en mémoire), son
remappage en a DÉTRUIT deux (il rebâtissait la liste). Même méthode, même succès,
réponse identique. Il fallait connaître son propre code pour savoir ce qu'on venait de
perdre, ce qui est hors de portée d'un agent qui exécute une procédure écrite par un
autre. Deux incidents en deux jours par ce mécanisme, dont 52 notes de champ.

C'est la forme exacte du défaut corrigé le 27/08 sur les LIGNES (`valeurs_effacees`) :
une écriture qui efface sans le dire. Ici, la réponse est la seule copie qui reste de
ce qui a disparu — d'où les valeurs, pas seulement les noms.
"""
from __future__ import annotations

import pytest

from oto_mcp import datastore_schema as S


_AVANT = {
    "key": "siren",
    "strict": True,
    "fields": [
        {"key": "siren", "type": "text", "description": "l'identifiant légal"},
        {"key": "segment", "type": "text", "max_length": 250,
         "description": "les catégories éditées", "hint": "séparées par `;`"},
        {"key": "statut", "type": "enum", "options": ["a_faire", "fait"]},
        {"key": "obsolete", "type": "text", "help": "colonne d'un ancien import"},
    ],
}


def _efface(nouveau, annonces=None):
    return S.declarations_effacees(_AVANT, nouveau, annonces)


def test_un_champ_retire_est_nomme():
    apres = {**_AVANT, "fields": [f for f in _AVANT["fields"]
                                  if f["key"] != "obsolete"]}
    perdu = _efface(apres)
    assert [e["champ"] for e in perdu] == ["obsolete"]
    assert perdu[0]["retire"] is True
    assert perdu[0]["declarations"]["help"] == "colonne d'un ancien import"


def test_une_note_de_champ_perdue_est_nommee_AVEC_SA_VALEUR():
    """La réponse est la seule copie qui reste : sans la valeur, il n'y a rien à
    rétablir — c'est la leçon de `valeurs_effacees` (27/08), transposée au format."""
    apres = {**_AVANT, "fields": [
        {"key": f["key"], "type": f.get("type")} for f in _AVANT["fields"]]}
    perdu = {e["champ"]: e["declarations"] for e in _efface(apres)}
    assert perdu["segment"]["description"] == "les catégories éditées"
    assert perdu["segment"]["hint"] == "séparées par `;`"
    assert perdu["segment"]["max_length"] == 250
    assert perdu["statut"]["options"] == ["a_faire", "fait"]
    assert all(not e.get("retire") for e in _efface(apres))


def test_une_cle_de_TETE_perdue_est_nommee():
    """Ce qu'on perd en premier est `schema.key` — la clé métier, qui porte un index
    UNIQUE partiel : la re-poster absente lève la contrainte sans un mot."""
    apres = {"fields": _AVANT["fields"]}
    perdu = [e for e in _efface(apres) if e["champ"] is None]
    assert perdu and perdu[0]["declarations"] == {"key": "siren", "strict": True}


def test_reposer_le_MEME_schema_n_efface_rien():
    assert _efface(dict(_AVANT)) == []


def test_ajouter_un_champ_en_gardant_le_reste_n_efface_rien():
    apres = {**_AVANT, "fields": _AVANT["fields"] + [{"key": "neuf"}]}
    assert _efface(apres) == []


def test_retirer_le_schema_ENTIER_est_un_effacement():
    perdu = _efface(None)
    assert {e["champ"] for e in perdu} >= {"siren", "segment", "statut", "obsolete"}


def test_un_retrait_ANNONCE_ne_fait_pas_de_bruit():
    """`patch_schema` nomme ce qu'il retire : le redire en avertissement serait du
    bruit sur un geste explicite, et un avertissement qui crie à tort est celui qu'on
    apprend à ignorer — donc celui qui ruine les vrais."""
    apres = {**_AVANT, "fields": [f for f in _AVANT["fields"]
                                  if f["key"] != "obsolete"]}
    assert _efface(apres, annonces=["obsolete"]) == []


def test_une_perte_NON_annoncee_traverse_le_geste_sur():
    """…et le filet reste tendu : un retrait annoncé n'excuse que LUI. Une note
    perdue au passage se dit quand même — sinon le geste « sûr » deviendrait un
    angle mort."""
    apres = {**_AVANT, "fields": [{"key": "siren", "type": "text"},
                                  {"key": "segment", "type": "text",
                                   "max_length": 250,
                                   "description": "les catégories éditées",
                                   "hint": "séparées par `;`"},
                                  {"key": "statut", "type": "enum",
                                   "options": ["a_faire", "fait"]}]}
    perdu = _efface(apres, annonces=["obsolete"])
    assert [e["champ"] for e in perdu] == ["siren"]
    assert perdu[0]["declarations"] == {"description": "l'identifiant légal"}


def test_le_releve_descend_dans_un_SOUS_RECORD():
    """Les réglages d'un sous-champ se perdent de la même façon, et une reconstruction
    de la liste les emporte tout autant."""
    avant = {"fields": [{"key": "contacts", "type": "list", "of": {"fields": [
        {"key": "email", "type": "email", "description": "vérifié à la source"}]}}]}
    apres = {"fields": [{"key": "contacts", "type": "list", "of": {"fields": [
        {"key": "email", "type": "email"}]}}]}
    perdu = S.declarations_effacees(avant, apres)
    assert [e["champ"] for e in perdu] == ["contacts[].email"]


# ── le relevé rendu à l'appelant ──

def test_le_releve_dit_QUOI_FAIRE_et_nomme_le_geste_qui_ne_detruit_pas():
    apres = {**_AVANT, "fields": [{"key": f["key"]} for f in _AVANT["fields"]]}
    rapport = S.declarations_effacees_report(_efface(apres))
    assert rapport["declarations_effacees"]
    assert "data_patch_schema" in rapport["declarations_effacees_hint"]


def test_rien_d_efface_aucune_cle_parasite():
    assert S.declarations_effacees_report([]) == {}


def test_un_releve_trop_long_dit_son_total():
    """Nommer 200 pertes noierait la réponse ; en nommer 20 en taisant le reste
    ferait croire qu'il n'y en a que 20."""
    avant = {"fields": [{"key": f"c{i}", "description": "note"} for i in range(60)]}
    apres = {"fields": [{"key": f"c{i}"} for i in range(60)]}
    rapport = S.declarations_effacees_report(
        S.declarations_effacees(avant, apres))
    assert len(rapport["declarations_effacees"]) < 60
    assert "60" in rapport["declarations_effacees_hint"]


def test_une_valeur_enorme_est_rendue_par_sa_TAILLE():
    """Projeter n'est pas tronquer : on ne rend pas un extrait qui ferait croire
    qu'on a lu — on dit la taille de ce qui n'est plus là."""
    avant = {"fields": [{"key": "c", "help": "x" * 5000}]}
    rapport = S.declarations_effacees_report(
        S.declarations_effacees(avant, {"fields": [{"key": "c"}]}))
    rendu = rapport["declarations_effacees"][0]["declarations"]["help"]
    assert "5000 caractères" in rendu


# ── servi par la POSE, pas par le patch ──

@pytest.fixture()
def store(monkeypatch):
    import oto_mcp.datastore as dsm
    from oto_mcp import datastore_schema_ops as ops

    st = dsm.DatastorePg("u", acting_org=35)
    monkeypatch.setattr(st, "_resolve", lambda ns, write=False: 7)
    monkeypatch.setattr(st, "_schema_of", lambda ns_id: _AVANT)
    monkeypatch.setattr(ops.db, "set_datastore_schema", lambda *a: None)
    monkeypatch.setattr(ops.db, "datastore_key_dup_groups", lambda *a: [])
    monkeypatch.setattr(ops.db, "datastore_drop_key_index", lambda *a: None)
    monkeypatch.setattr(ops.db, "datastore_ensure_key_index", lambda *a: None)
    monkeypatch.setattr(ops.db, "datastore_row_keys", lambda ns_id: [])
    monkeypatch.setattr(ops.db, "datastore_overlong_fields", lambda *a, **k: [])
    monkeypatch.setattr(ops.db, "datastore_field_values", lambda *a, **k: {})
    monkeypatch.setattr(ops.db, "datastore_offending_enum_values", lambda *a, **k: [])
    return st


def test_la_pose_rend_ce_qu_elle_vient_de_perdre(store):
    apres = {**_AVANT, "fields": [{"key": f["key"], "type": f.get("type")}
                                  for f in _AVANT["fields"]]}
    out = store.set_schema("viviers", apres)
    perdus = {e["champ"] for e in out["declarations_effacees"]}
    assert perdus == {"siren", "segment", "statut", "obsolete"}
    assert "declarations_effacees_hint" in out


def test_une_pose_qui_ne_perd_rien_ne_porte_pas_la_cle(store):
    out = store.set_schema("viviers", dict(_AVANT))
    assert "declarations_effacees" not in out


def test_le_patch_ne_denonce_pas_le_retrait_qu_il_a_ANNONCE(store, monkeypatch):
    out = store.patch_schema("viviers", remove=["obsolete"])
    assert out["removed"] == ["obsolete"]
    assert "declarations_effacees" not in out
