"""Désigner un tableau par son IDENTIFIANT, là où seul son NOM voyageait (oto#160).

⚠️ **Le défaut, et pourquoi il ne se répare pas à l'écran.** `resolve_datastore_ns`
résout par nom OU par id, et à nom égal elle préfère le tableau **personnel du
demandeur**. Le demandeur, c'est celui qui appelle : un écran qui résout lui-même un nom
le résout donc avec SON lecteur. Ouvrir un tableau reçu en partage homonyme d'un des
siens peignait les lignes du sien, sous le bon libellé et sans un mot — une réponse
plausible et fausse, pire qu'une erreur.

Deux surfaces ne recevaient qu'un nom, et ce banc tient ce que le serveur y ajoute :

- la **charge utile d'un travail de runner** : le passage ne stocke qu'un nom
  (`runner_fleets.namespace`). Il est résolu au nom de QUI A DÉCLARÉ la campagne — le
  sub sous lequel l'agent travaillera, donc la même priorité que le store qu'il
  utilisera — et l'identifiant part avec le travail ;
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


def test_l_identifiant_est_resolu_au_nom_de_QUI_A_DECLARE_la_campagne(monkeypatch):
    """…et pas au nom de qui lit. C'est ce qui rend la réponse la même pour tous."""
    monkeypatch.setattr(cap.db, "resolve_datastore_ns", _faux_resolve)
    monkeypatch.setattr("oto_mcp.group_store.list_groups_for_user",
                        lambda sub, org: [])
    campagne = {"id": 3, "org_id": 2, "sub": "campagne-sub", "namespace": "clients"}
    assert cap._id_du_tableau_vise(campagne) == 77


def test_un_nom_qui_ne_resout_plus_ne_bloque_PAS_la_campagne(monkeypatch):
    """Fail-open : tableau supprimé, renommé, sorti de portée → pas d'identifiant, et
    le travail part quand même. L'écran montrera le nom sans prétendre l'ouvrir."""
    monkeypatch.setattr(cap.db, "resolve_datastore_ns", _faux_resolve)
    monkeypatch.setattr("oto_mcp.group_store.list_groups_for_user",
                        lambda sub, org: [])
    absente = {"id": 3, "org_id": 2, "sub": "campagne-sub", "namespace": "disparu"}
    assert cap._id_du_tableau_vise(absente) is None
    # Un passage SANS cible (une campagne peut n'en avoir aucune) : aucune requête.
    assert cap._id_du_tableau_vise({"id": 3, "org_id": 2, "sub": "s"}) is None


def test_une_resolution_qui_LEVE_ne_casse_pas_le_sondage(monkeypatch):
    """Ce chemin tourne à chaque sondage de chaque worker. Une panne de résolution ne
    doit pas arrêter les automatisations de toute l'org — elle coûte un lien, pas un
    service."""
    def boum(*a, **k):
        raise RuntimeError("base indisponible")
    monkeypatch.setattr(cap.db, "resolve_datastore_ns", boum)
    monkeypatch.setattr("oto_mcp.group_store.list_groups_for_user",
                        lambda sub, org: [])
    assert cap._id_du_tableau_vise(
        {"id": 3, "org_id": 2, "sub": "campagne-sub", "namespace": "clients"}) is None


def test_la_charge_utile_EMPORTE_l_identifiant(monkeypatch):
    """⚠️ Le banc qui compte : une fonction qui résout bien ne sert à rien si la clé
    n'entre pas dans le payload. On regarde ce qui est réellement enfilé."""
    enfiles: list[dict] = []
    monkeypatch.setattr(cap.db, "arreter_campagnes_epuisees", lambda org: [])
    monkeypatch.setattr(cap.db, "accuser_arrets_effectifs", lambda org: [])
    monkeypatch.setattr(cap.db, "campagne_a_servir", lambda org, _ordonner: {
        "id": 3, "org_id": 2, "sub": "campagne-sub", "namespace": "clients",
        "procedure": "relance", "tools": ["data_rows"], "label": "vivier",
        "input": "traite {namespace}", "project_id": None, "max_steps": 8,
        "max_tokens_per_row": None, "temperature": None, "row_filter": None,
    })
    monkeypatch.setattr(cap.db, "marquer_demarree", lambda fid: None)
    monkeypatch.setattr(cap.db, "enqueue_job",
                        lambda *a, **k: enfiles.append(k.get("payload") or {}))
    monkeypatch.setattr(cap.db, "resolve_datastore_ns", _faux_resolve)
    monkeypatch.setattr("oto_mcp.group_store.list_groups_for_user",
                        lambda sub, org: [])

    assert cap._produire_pour_une_campagne(2, 60) is None
    assert len(enfiles) == 1
    payload = enfiles[0]
    assert payload["datastore_id"] == 77, "l'identifiant, résolu pour la campagne"
    # ⚠️ Le NOM reste : c'est le libellé que l'écran affiche, et le worker s'en sert
    # encore. Les deux voyagent — l'un désigne, l'autre se lit.
    assert payload["namespace"] == "clients"


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
])
def test_la_portee_est_celle_du_PROPRIETAIRE_du_projet(proprio, attendu):
    portee = _portee_du_projet(proprio)
    assert portee == attendu
    if proprio["owner_type"] != "user":
        assert portee["sub"] != proprio["created_by"], "aucune personne dans la portée"
