"""Projection + troncature des listes Unipile (`_slim`) — signal d'usage #281.

`unipile_member_posts(limit=10)` rendait 55-75 Ko (URLs d'images en triple, urns,
jetons de partage) pour un besoin qui est presque toujours « balayer les derniers
posts de X et voir si l'un colle » : le payload basculait en fichier à chaque appel,
donc il fallait un second outil pour trier ce que l'agent venait de demander.
"""
from __future__ import annotations

from oto_mcp.tools.unipile import _slim

PAYLOAD = {
    "items": [
        {"id": "p1", "social_id": "urn:li:activity:1", "text": "x" * 900,
         "images": ["u1", "u2"], "share_token": "tok"},
        {"id": "p2", "social_id": "urn:li:activity:2", "text": "court",
         "images": [], "share_token": "tok2"},
    ],
    "cursor": "next-page",
    "total_count": 2,
}


def test_sans_parametres_le_payload_est_intact():
    """Le comportement par défaut ne change pas — la projection est OPT-IN."""
    assert _slim(dict(PAYLOAD)) == PAYLOAD


def test_projection_garde_de_quoi_enchainer():
    out = _slim(PAYLOAD, ["text"], None)
    it = out["items"][0]
    assert set(it) == {"text", "id", "social_id"}, (
        "`id`/`social_id` sont conservés même non demandés : sans eux l'agent ne peut "
        "plus ouvrir ni commenter le post qu'il vient de repérer")
    assert "images" not in it and "share_token" not in it


def test_troncature_marque_ce_qu_elle_coupe():
    out = _slim(PAYLOAD, None, 100)
    a, b = out["items"]
    assert len(a["text"]) == 101 and a["text"].endswith("…")
    assert a["text_truncated"] is True
    assert b["text"] == "court" and "text_truncated" not in b, (
        "un texte déjà court n'est pas marqué tronqué")


def test_enveloppe_de_pagination_preservee():
    out = _slim(PAYLOAD, ["text"], 50)
    assert out["cursor"] == "next-page" and out["total_count"] == 2, (
        "projeter les items ne doit jamais casser la pagination")


def test_payload_non_liste_passe_tel_quel():
    assert _slim({"object": "UserProfile"}, ["x"], 10) == {"object": "UserProfile"}
    assert _slim(None, ["x"], 10) is None


def test_alias_data_reste_coherent_avec_items():
    """`_norm` (oto-core) aliase `data` et `items` : les deux doivent rester alignés."""
    payload = {**PAYLOAD, "data": PAYLOAD["items"]}
    out = _slim(payload, ["text"], None)
    assert out["data"] == out["items"]
