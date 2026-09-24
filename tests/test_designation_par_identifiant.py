"""Désigner un tableau par son IDENTIFIANT, là où seul son NOM voyageait (oto#160).

⚠️ **Le défaut, et pourquoi il ne se répare pas à l'écran.** `resolve_datastore_ns`
résout par nom OU par id, et à nom égal elle préfère le tableau **personnel du
demandeur**. Le demandeur, c'est celui qui appelle : un écran qui résout lui-même un nom
le résout donc avec SON lecteur. Ouvrir un tableau reçu en partage homonyme d'un des
siens peignait les lignes du sien, sous le bon libellé et sans un mot — une réponse
plausible et fausse, pire qu'une erreur.

Deux surfaces ne recevaient qu'un nom, et ce banc tient ce que le serveur y ajoute :

- la **charge utile d'un travail de runner** : la campagne garde l'IDENTIFIANT de son
  tableau, résolu une fois à sa déclaration dans la portée de QUI l'a déclarée (#1067),
  et il part tel quel avec le travail ;
- le **lien de projet vers un tableau** : `target_ref` est tantôt un id (posé par le
  dashboard), tantôt un nom (posé par un agent, #117). `datastore_id` porte toujours le
  même sens, ou n'est pas là — et il est résolu dans la portée du **propriétaire du
  projet**, donc identique pour tous ceux qui ouvrent le projet.

⚠️ Rien ici ne touche la base : ce sont les fonctions pures + un faux résolveur qui
**rejoue l'ORDER BY** de `resolve_datastore_ns`. C'est la reproduction : le même nom,
résolu pour deux demandeurs, ne donne pas le même tableau.
"""
from __future__ import annotations

import pytest

from oto_mcp.capabilities import runner_jobs as cap
from oto_mcp.db.projects import (_apply_tableau_name_ids, _apply_tableau_names,
                                 _portee_du_projet)

# Le monde du banc : deux tableaux qui portent le MÊME nom.
#   41 — personnel du LECTEUR (celui qui regarde l'écran)
#   77 — personnel d'un tiers, partagé nominativement à qui a déclaré la campagne
_MONDE = [
    {"id": 41, "datastore": "clients", "owner_type": "user", "owner_id": "lecteur"},
    {"id": 77, "datastore": "clients", "owner_type": "user", "owner_id": "tiers",
     "grants": {"campagne-sub"}},
]


def _faux_resolve(namespace, *, sub, org_ids, group_ids):
    """L'ORDER BY de `resolve_datastore_ns`, rejoué : nom d'abord, puis perso > org."""
    vus = [t for t in _MONDE
           if (t["datastore"] == namespace or str(t["id"]) == namespace)
           and (t["owner_id"] == sub or sub in t.get("grants", ()))]
    vus.sort(key=lambda t: (t["datastore"] != namespace,
                            0 if t["owner_id"] == sub else 2))
    return vus[0] if vus else None


# ── ① la charge utile d'un travail de runner ─────────────────────────────────

def test_le_nom_seul_ne_designe_pas_le_meme_tableau_selon_qui_demande():
    """LA REPRODUCTION, en deux lignes. C'est tout le défaut : le même nom, deux
    demandeurs, deux tableaux — et rien dans la réponse ne dit lequel."""
    assert _faux_resolve("clients", sub="campagne-sub", org_ids=[], group_ids=[])["id"] == 77
    assert _faux_resolve("clients", sub="lecteur", org_ids=[], group_ids=[])["id"] == 41


def _refuse_toute_resolution(*a, **k):
    raise AssertionError("une résolution par NOM à l'enfilage")


def test_la_charge_utile_EMPORTE_l_identifiant_que_la_campagne_garde(monkeypatch):
    """⚠️ Le banc qui compte : on regarde ce qui est réellement enfilé. Depuis #1067 la
    campagne garde l'IDENTIFIANT de son tableau, résolu à sa déclaration dans la portée
    de son déclarant (`tests/test_campagne_tableau_par_cle_1067.py`) : l'enfilage ne
    résout plus rien par nom, il emporte la clé gardée."""
    enfiles: list[dict] = []
    monkeypatch.setattr(cap.db, "arreter_campagnes_epuisees", lambda org: [])
    monkeypatch.setattr(cap.db, "accuser_arrets_effectifs", lambda org: [])
    monkeypatch.setattr(cap.db, "campagne_a_servir", lambda org, _ordonner: {
        "id": 3, "org_id": 2, "sub": "campagne-sub", "namespace": "77",
        "procedure": "relance", "tools": ["data_rows"], "label": "vivier",
        "input": "traite {namespace}", "project_id": None, "max_steps": 8,
        "max_tokens_per_row": None, "temperature": None, "row_filter": None,
    })
    monkeypatch.setattr(cap.db, "marquer_demarree", lambda fid: None)
    monkeypatch.setattr(cap.db, "enqueue_job",
                        lambda *a, **k: enfiles.append(k.get("payload") or {}))
    monkeypatch.setattr(cap.db, "resolve_datastore_ns", _refuse_toute_resolution)
    monkeypatch.setattr(cap.db, "resolve_datastore_ids_by_name", _refuse_toute_resolution)

    assert cap._produire_pour_une_campagne(2, 60) is None
    assert len(enfiles) == 1
    payload = enfiles[0]
    assert payload["datastore_id"] == 77, "l'identifiant gardé par la campagne"
    assert payload["namespace"] == "77"
    assert payload["input"] == "traite 77", "la consigne cite la clé, pas un nom"


def test_une_campagne_sans_cible_n_emporte_aucun_identifiant():
    from oto_mcp.capabilities import _lignes_reservables
    assert _lignes_reservables.cle_de_campagne({"id": 3, "namespace": None}) is None
    assert _lignes_reservables.cle_de_campagne({"id": 3, "namespace": "clients"}) is None


# ── ② le lien de projet vers un tableau ──────────────────────────────────────

def test_le_lien_pose_par_le_dashboard_sert_son_identifiant():
    """`target_ref` EST l'id — mais le lecteur n'a pas à savoir quand c'est le cas."""
    liens = [{"target_type": "tableau", "target_ref": "77"}]
    _apply_tableau_names(liens, {77: "clients"})
    assert liens[0] == {"target_type": "tableau", "target_ref": "77",
                        "datastore": "clients", "datastore_id": 77}


def test_un_tableau_DISPARU_ne_pose_toujours_aucune_cle():
    """Le lien mort se lit sur l'ABSENCE des clés — y compris de l'identifiant, sinon
    un lien mort passerait pour vivant."""
    liens = [{"target_type": "tableau", "target_ref": "999"}]
    _apply_tableau_names(liens, {})
    assert "datastore" not in liens[0] and "datastore_id" not in liens[0]


def test_le_lien_pose_par_un_agent_recoit_l_identifiant_resolu():
    """Le chemin #117 : `target_ref` est un NOM. C'est celui qui portait le défaut —
    l'écran résolvait ce nom, donc le résolvait chez son lecteur."""
    liens = [{"target_type": "tableau", "target_ref": "clients", "datastore": "clients"}]
    _apply_tableau_name_ids(liens, {"clients": 77})
    assert liens[0]["datastore_id"] == 77


def test_un_nom_hors_de_portee_du_projet_ne_recoit_PAS_d_identifiant():
    """Le serveur ne devine pas. Sans identifiant, l'écran n'ouvre rien — il le dit."""
    liens = [{"target_type": "tableau", "target_ref": "clients", "datastore": "clients"}]
    _apply_tableau_name_ids(liens, {})
    assert "datastore_id" not in liens[0]


def test_un_lien_deja_identifie_n_est_pas_reecrit():
    """Le chemin par id passe en premier ; celui par nom ne repasse pas dessus."""
    liens = [{"target_type": "tableau", "target_ref": "77",
              "datastore": "clients", "datastore_id": 77}]
    _apply_tableau_name_ids(liens, {"77": 41})
    assert liens[0]["datastore_id"] == 77


# ⚠️ Chaque projet du banc porte un `created_by` — le membre qui l'a créé. Sans lui,
# une portée qui retomberait sur ce membre rendrait la MÊME chose que la portée d'org
# (chaîne vide contre chaîne vide) et ce banc resterait vert sur le défaut qu'il existe
# pour attraper : un lien d'org résolu au nom d'une personne.
@pytest.mark.parametrize("proprio,attendu", [
    ({"owner_type": "user", "owner_id": "sub-a", "context_org_id": None,
      "created_by": "sub-a"},
     {"sub": "sub-a", "org_ids": [], "group_ids": []}),
    # Un projet perso ouvert DANS une org y lie couramment un tableau d'org.
    ({"owner_type": "user", "owner_id": "sub-a", "context_org_id": 5,
      "created_by": "sub-a"},
     {"sub": "sub-a", "org_ids": [5], "group_ids": []}),
    # ⚠️ Un projet d'org résout comme L'ORG, jamais comme un membre : sinon deux
    # membres verraient deux tableaux derrière le même lien — le défaut qu'on ferme.
    ({"owner_type": "org", "owner_id": "5", "context_org_id": None,
      "created_by": "un-membre"},
     {"sub": "", "org_ids": [5], "group_ids": []}),
    ({"owner_type": "group", "owner_id": "9", "context_org_id": None,
      "created_by": "un-membre"},
     {"sub": "", "org_ids": [], "group_ids": [9]}),
    # Une équipe range couramment ses tableaux dans son org PARENTE (#365) : la même
    # règle qu'à la pose du lien. L'équipe passe devant à nom égal (rang, db).
    ({"owner_type": "group", "owner_id": "9", "context_org_id": None,
      "group_org_id": 5, "created_by": "un-membre"},
     {"sub": "", "org_ids": [5], "group_ids": [9]}),
])
def test_la_portee_est_celle_du_PROPRIETAIRE_du_projet(proprio, attendu):
    portee = _portee_du_projet(proprio)
    assert portee == attendu
    if proprio["owner_type"] != "user":
        assert portee["sub"] != proprio["created_by"], "aucune personne dans la portée"
