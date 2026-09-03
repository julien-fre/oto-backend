"""Un message long est DÉCOUPÉ — et l'appelant l'apprend dans la description (#613).

Le 29/08/2026, un digest de ~3 700 caractères est parti en DEUX messages Slack.
La réponse rendait `ok: true` et le texte du DERNIER fragment : de la seule
réponse, impossible de savoir si le message avait été tronqué, découpé, ou
délivré entier. Le client oto-core rend depuis `ts_all` et `split_into`, et
threade les suites sous la première — mais **la description servie ne disait
toujours rien**, et c'est elle que l'agent lit à chaque appel.

⚠️ **Le coût du doute est asymétrique, et c'est ce qui fait ce banc.** Un message
posté se supprime mais ne s'édite pas : celui qui conclut à tort à une troncature
en poste un SECOND. Et la vérification évidente — relire le canal — est
interdite comme source par certaines procédures. La réponse doit donc se suffire.

⚠️ Le dernier test lit le CLIENT amont, donc il porte `exige_pin_oto_core` : la
découpe a été posée dans oto-core le 01/09 (v1.106.0), et un venv resté en deçà
rendrait un rouge qui accuse ce dépôt pour un client qui n'est pas le sien.

Éprouvé rouge le 2026-09-03 : la mention retirée de la docstring ⟹ chaque test
nomme ce que l'appelant ne peut plus savoir.
"""
from __future__ import annotations

import pytest

from oto_mcp.tools import slack as slack_tools


@pytest.fixture(scope="module")
def prose() -> str:
    """La docstring TELLE QUE SERVIE, prise sur le montage réel : un contrat qui
    se lit dans le source sans passer par l'enregistrement se contredirait sans
    que personne le voie."""
    import asyncio

    from fastmcp import FastMCP
    mcp = FastMCP("test")
    slack_tools.register(mcp)
    return asyncio.run(mcp.get_tool("slack_post_message")).description or ""


def test_la_decoupe_est_ANNONCEE(prose):
    assert "SPLIT" in prose or "split" in prose, (
        "l'appelant doit savoir qu'un texte long part en plusieurs messages")


def test_rien_n_est_PERDU_est_dit_explicitement(prose):
    """La question que se pose l'appelant n'est pas « combien de messages » mais
    « ai-je perdu du texte ». Y répondre est ce qui évite le doublon."""
    assert "truncated" in prose and "Nothing is lost" in prose


def test_les_deux_champs_de_la_reponse_sont_NOMMES(prose):
    """Sans leurs noms, la découpe reste indétectable : c'est `split_into` qui
    répond, pas la relecture du canal."""
    assert "ts_all" in prose and "split_into" in prose


def test_le_ts_rendu_est_dit_etre_le_PREMIER(prose):
    """Le piège d'usage : répondre en fil sur le dernier fragment accroche la
    réponse au mauvais message."""
    assert "FIRST" in prose


@pytest.mark.exige_pin_oto_core
def test_le_client_amont_rend_bien_ce_que_la_prose_promet():
    """La garde qui compte : une description peut promettre ce que le code ne
    rend pas. On lit le CLIENT, pas une intention."""
    import inspect
    from oto.tools.slack.client import SlackClient
    src = inspect.getsource(SlackClient.post_message)
    assert "ts_all" in src and "split_into" in src
