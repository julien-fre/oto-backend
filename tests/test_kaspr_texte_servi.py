"""Le TEXTE que le serveur sert pour Kaspr : l'exemple de `data_to_get`, et ce
qu'affirme le message d'un 500 amont.

**L'incident.** La docstring de `kaspr_enrich_linkedin` donnait en exemple
`["emails", "phones", "company"]`. Aucun de ces trois noms n'existe chez Kaspr —
les seuls acceptés sont `workEmail`, `personalEmail`, `phone`. Un nom inconnu ne
rend pas un 400 lisible : le parser amont plante et rend un **500**
(`TypeError: Cannot read properties of undefined (reading 'push')`, reproduit sur
un profil sentinelle sans crédit). La chaîne est écrite dans le journal d'un vrai
run : schéma d'outil lu à 13:27:18, `["emails","phones"]` envoyé à 13:28:58 —
l'agent a lu notre texte et l'a appliqué. 10 contacts × 2 passes = 20 échecs, puis
abandon. Le mauvais exemple datait du jour de création du tool (2026-05-22).

Et le message qui accueillait ce 500 disait « **ce n'est pas ton entrée** » : faux,
et faux précisément dans le cas le plus atteignable. Kaspr rend 500 sur au moins
deux fautes d'entrée connues — une URL complète au lieu du slug nu (relevé le
15/06) et un `dataToGet` inconnu. L'affirmation fermait la seule piste correcte.

**Pourquoi le contrôle porte sur le texte SERVI et non sur la docstring.** Le
harnais fastmcp ne sert pas la docstring telle quelle : il en découpe le bloc
`Args:` pour en faire les `description` du schéma. C'est ce schéma-là que le modèle
relit à chaque appel, donc c'est lui qu'on vérifie — mesuré ici sur le montage
réel, pas sur `kaspr.__doc__`.
"""
from __future__ import annotations

import asyncio
import re
from unittest.mock import patch

import pytest
from fastmcp import FastMCP


class _Resp500:
    status_code = 500


class _Boom500(Exception):
    """Réplique de forme d'une `requests.HTTPError` 5xx (le tool lit
    `e.response.status_code`)."""
    response = _Resp500()


def _schema_servi() -> dict:
    from oto_mcp.tools import kaspr

    m = FastMCP("t")
    kaspr.register(m)

    async def go():
        t = await m.get_tool("kaspr_enrich_linkedin")
        return t.parameters

    return asyncio.run(go())


def _message_du_500_kaspr() -> str:
    from oto_mcp.mcp_errors import McpError
    from oto_mcp.tools import kaspr

    class _Stub:
        def __init__(self, *a, **k):
            pass

        def enrich_linkedin(self, **k):
            raise _Boom500()

    with patch("oto.tools.kaspr.client.KasprClient", _Stub), \
            patch("oto_mcp.access.resolve_api_key", return_value=("k", False)):
        m = FastMCP("t")
        kaspr.register(m)
        fn = asyncio.run(m.get_tool("kaspr_enrich_linkedin")).fn
        with pytest.raises(McpError) as e:
            fn(linkedin_id="jane-doe")
        return e.value.error.message


def _message_du_500_aiark() -> str:
    from oto_mcp.mcp_errors import McpError
    from oto_mcp.tools import aiark

    class _Stub:
        def __init__(self, *a, **k):
            pass

        def export_person(self, **k):
            raise _Boom500()

    with patch("oto.tools.aiark.client.AiArkClient", _Stub), \
            patch("oto_mcp.access.resolve_api_key", return_value=("k", False)):
        m = FastMCP("t")
        aiark.register(m)
        fn = asyncio.run(m.get_tool("linkedin_aiark_person")).fn
        with pytest.raises(McpError) as e:
            fn(op="export", url="https://www.linkedin.com/in/jane-doe/")
        return e.value.error.message


def _message_du_500_cognism() -> str:
    from oto_mcp.mcp_errors import McpError
    from oto_mcp.tools import cognism

    class _Stub:
        def __init__(self, *a, **k):
            pass

        def enrich_contact(self, **k):
            raise _Boom500()

    with patch("oto.tools.cognism.client.CognismClient", _Stub), \
            patch("oto_mcp.access.resolve_api_key", return_value=("k", False)):
        m = FastMCP("t")
        cognism.register(m)
        fn = asyncio.run(m.get_tool("cognism_enrich_contact")).fn
        with pytest.raises(McpError) as e:
            fn(linkedin_url="https://www.linkedin.com/in/jane-doe/")
        return e.value.error.message


# --- ① le schéma servi nomme les champs que Kaspr accepte VRAIMENT -------------

def test_le_schema_servi_nomme_les_trois_champs_kaspr():
    """Les trois de l'enum publié par Kaspr — pas ceux de notre ancienne docstring.
    C'est une docstring non vérifiée qui a produit l'incident : la source du texte
    servi est désormais le contrat du fournisseur."""
    desc = _schema_servi()["properties"]["data_to_get"]["description"]
    for champ in ("workEmail", "directEmail", "phone"):
        assert champ in desc, f"`{champ}` absent du texte servi : {desc!r}"


def test_le_schema_servi_ne_prescrit_aucun_champ_inexistant():
    """Les trois noms de l'exemple d'origine — ceux qui font planter Kaspr."""
    desc = _schema_servi()["properties"]["data_to_get"]["description"]
    for faux in ("emails", "phones", "company"):
        assert not re.search(rf"\b{faux}\b", desc), \
            f"le texte servi prescrit encore `{faux}`, que Kaspr refuse par un 500"


def test_le_schema_servi_annonce_le_defaut_reel_pas_tous_les_champs():
    """Omis, `data_to_get` ne vaut PAS « tous les champs » : le client applique
    `["workEmail", "phone"]`."""
    desc = _schema_servi()["properties"]["data_to_get"]["description"]
    assert "Defaults to all" not in desc
    assert "workEmail" in desc and "phone" in desc


# --- ③ un 500 amont n'innocente pas l'entrée ----------------------------------

def test_le_500_kaspr_naffirme_plus_que_lentree_est_hors_de_cause():
    msg = _message_du_500_kaspr()
    assert "ce n'est pas ton entrée" not in msg.lower()


def test_le_500_kaspr_nomme_les_deux_fautes_dentree_atteignables():
    """Slug et noms de champs : les deux entrées dont on SAIT qu'elles font 500."""
    msg = _message_du_500_kaspr()
    assert "slug" in msg.lower(), msg
    assert "data_to_get" in msg, msg
    for champ in ("workEmail", "directEmail", "phone"):
        assert champ in msg, msg


def test_le_500_kaspr_borne_la_reprise():
    """« Réessaie » sans borne a produit 20 tentatives puis un abandon : la reprise
    est explicitement d'UNE fois."""
    msg = _message_du_500_kaspr().lower()
    assert "une seule" in msg, msg


def test_le_500_aiark_naffirme_plus_que_lentree_est_hors_de_cause():
    """La même phrase vivait à l'identique dans le connecteur AI Ark."""
    msg = _message_du_500_aiark()
    assert "ce n'est pas ton entrée" not in msg.lower()


def test_le_500_aiark_borne_la_reprise():
    msg = _message_du_500_aiark().lower()
    assert "une seule" in msg, msg


def test_le_500_cognism_naffirme_plus_que_lentree_est_hors_de_cause():
    """Troisième copie de la même phrase, trouvée en la retirant des deux autres.
    Rien ne l'avait recopiée sciemment : elle a été dupliquée d'un connecteur à
    l'autre, avec sa certitude. Elle est fausse partout où on ne peut pas savoir."""
    msg = _message_du_500_cognism()
    assert "ce n'est pas ton entrée" not in msg.lower()


def test_le_500_cognism_borne_la_reprise():
    assert "une seule" in _message_du_500_cognism().lower()
