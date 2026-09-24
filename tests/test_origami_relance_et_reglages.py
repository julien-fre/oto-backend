"""Origami : ce que le texte servi promettait, et que le fournisseur ne tient pas.

La garde `aucune_action` refuse un déroulé terminé sans action (v1.272.0) et elle mord :
les signaux d'usage qui suivent la citent. Restaient deux phrases fausses, qui pilotent
l'agent :

- « relaunch » tout court. Mesuré le 14/09 (signal 982) : un déroulé refusé ainsi a
  pu laisser dans l'interface Origami un brouillon « Ready to launch » qu'aucun verbe du
  connecteur ne voit ; relancer en ajoute un à chaque fois — et pendant une panne du
  fournisseur (signal 984), huit relances de suite n'ont rien créé.
- « Settings (persisted on the campaign) ». Mesuré les 13 et 15/09 (signaux 939, 1017) :
  `block_prior_contacts=False` redemandé, `True` stocké, sur une campagne créée par
  l'appel lui-même.

Pas de relance automatique côté connecteur : elle multiplierait les brouillons fantômes.
Le texte dit donc de relancer UNE fois, de faire vérifier par un humain, et de relire
les réglages.
"""
from __future__ import annotations

import asyncio

import pytest

from oto_mcp.mcp_errors import McpError
from oto_mcp.tools import origami as O


def _doc(name: str) -> str:
    from fastmcp import FastMCP
    m = FastMCP("t")
    O.register(m)
    return " ".join((asyncio.run(m.get_tool(name)).fn.__doc__ or "").split())


def test_le_refus_dit_de_relancer_UNE_fois_et_de_faire_verifier():
    with pytest.raises(McpError) as e:
        O._refuse_si_rien_n_a_ete_fait({"id": "run-x", "status": "completed", "actions": []})
    msg = e.value.error.message
    assert "relance origami_campaign_create" in msg
    assert "UNE fois" in msg and "l'API ne voit pas" in msg
    assert "ne relance pas en boucle" in msg


def test_la_creation_ne_promet_plus_des_reglages_persistes():
    doc = _doc("origami_campaign_create")
    assert "persisted on the campaign" not in doc
    assert "REQUESTED, not guaranteed" in doc
    assert 'origami_campaigns(op="get")' in doc and "settings" in doc
    assert "relaunch ONCE" in doc


def test_le_suivi_du_deroule_dit_la_meme_prudence():
    doc = _doc("origami_run_get")
    assert "relaunch `origami_campaign_create` ONCE" in doc
    assert "visible only in the Origami interface" in doc
