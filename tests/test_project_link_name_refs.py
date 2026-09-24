"""Un lien projet `tableau` créé par NOM (#117) ne « vit » que s'il RÉSOUT dans la
portée du propriétaire du projet (#365).

Jusqu'au 24/09/2026, le libellé `datastore` se posait dès qu'un tableau de ce nom
existait N'IMPORTE OÙ sur la plateforme : un lien d'une org vivait grâce au « vivier »
d'une autre, l'audit ne le voyait pas mort, et `slot:` rendait ce nom à résoudre chez
l'appelant. `_apply_tableau_name_ids` pose désormais le libellé ET l'identifiant
ensemble, sur un nom résolu dans la portée — ou `datastore_ambigu` — ou rien."""
from __future__ import annotations

from oto_mcp.db import projects as P


def test_un_nom_resolu_dans_la_portee_recoit_son_libelle_et_son_identifiant():
    links = [{"target_type": "tableau", "target_ref": "vivier-pmi"}]
    P._apply_tableau_name_ids(links, {"vivier-pmi": 41, "autre": 42})
    assert links[0]["datastore"] == "vivier-pmi" and links[0]["datastore_id"] == 41


def test_un_nom_qui_n_existe_QU_AILLEURS_reste_non_resolu():
    """La simple existence du nom sur la plateforme ne suffit plus : hors portée, le
    lien est mort pour ce projet — l'audit le dit, `slot:` le refuse."""
    links = [{"target_type": "tableau", "target_ref": "vivier"}]
    P._apply_tableau_name_ids(links, {})
    assert "datastore" not in links[0] and "datastore_id" not in links[0]


def test_un_nom_AMBIGU_ne_recoit_ni_libelle_ni_identifiant():
    links = [{"target_type": "tableau", "target_ref": "vivier"}]
    P._apply_tableau_name_ids(links, {}, {"vivier"})
    assert links[0]["datastore_ambigu"] is True
    assert "datastore" not in links[0] and "datastore_id" not in links[0]


def test_id_resolved_link_untouched():
    links = [{"target_type": "tableau", "target_ref": "109",
              "datastore": "vivier-pmi", "datastore_id": 109}]
    P._apply_tableau_name_ids(links, {"109": 7}, {"109"})
    assert links[0] == {"target_type": "tableau", "target_ref": "109",
                        "datastore": "vivier-pmi", "datastore_id": 109}


def test_non_tableau_untouched():
    links = [{"target_type": "connecteur", "target_ref": "folk"}]
    P._apply_tableau_name_ids(links, {"folk": 3}, {"folk"})
    assert links[0] == {"target_type": "connecteur", "target_ref": "folk"}
