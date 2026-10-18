"""Textes servis qui promettaient ce que la réponse ne porte pas (oto#91, lot du 07/09).

Une description d'outil est relue à chaque appel : chaque assertion ici tient UNE phrase
servie contre ce que le code fait, sur la surface que l'agent lit réellement.
"""
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from oto_mcp.capabilities import registry

_GUIDES = Path(__file__).resolve().parents[1] / "oto_mcp" / "guides"


def _outil_datastore(nom: str) -> str:
    from fastmcp import FastMCP
    from oto_mcp.tools import datastore as t_ds

    m = FastMCP("t")
    t_ds.register(m)
    return asyncio.run(m.get_tool(nom)).description or ""


def _capacite(cle: str) -> str:
    import oto_mcp.capabilities  # noqa: F401 — peuple le registre
    return registry.by_key(cle).description


def test_get_schema_ne_fait_pas_de_la_file_une_propriete_du_schema():
    """Retour 757 : lue seule, la phrase « les règles de file vivent sur le champ de
    statut » disait « pas de schéma, pas de file ». `claim_next` ne lit pas le
    `lifecycle` : il réserve sur tout tableau. Un agent a écrit une boucle
    lire-puis-marquer, non atomique, sur un tableau sans schéma."""
    d = _capacite("me.datastore.get_schema")
    assert "needs NOTHING declared" in d
    assert "RESTRICTS" in d
    assert 'work-queue rules live on the `role:"status"` field' not in d


def test_set_schema_ne_designe_plus_la_colonne_par_role():
    """Depuis le 08/09/2026 c'est la colonne qui PORTE le bloc qui est l'état — plus
    d'étiquette `role: "status"` (retirée au code, `definition.py`)."""
    d = _outil_datastore("data_set_schema")
    assert 'lifecycle: on the `role:"status"` field' not in d
    assert "carries the block IS the status column" in d


def test_data_write_dit_qu_une_erreur_au_retour_n_est_pas_un_echec():
    """Retour 734 : un « session expirée » après une écriture ; la ligne était
    commitée. Réessayer n'était sûr que par chance (le tableau portait une clé)."""
    d = _outil_datastore("data_write")
    assert "does NOT mean the write failed" in d
    assert "DUPLICATE" in d


def test_l_audit_de_projet_annonce_ses_quatre_listes():
    """L'audit rend quatre listes (`project_audit.audit_project`) ; la description
    n'en annonçait que trois et taisait les connecteurs irrésolubles."""
    d = _capacite("me.project")
    for liste in ("dead_links", "unbound_slots", "unresolvable_connectors",
                  "inert_procedures"):
        assert liste in d


@pytest.mark.parametrize("guide,faux", [
    ("work-queue.md", "without declared terminal states, nothing is released"),
    ("datastore-semantics.md", "the table has NO queue**: `data_claim_next`"),
])
def test_les_guides_ne_disent_plus_ce_que_le_code_ne_fait_pas(guide, faux):
    assert faux not in (_GUIDES / guide).read_text(encoding="utf-8")


def test_la_liste_des_orgs_dit_ce_que_designe_personal():
    """#1216 : une org nommée « Perso » à la main et l'org perso posée d'office étaient
    confondues. La description servie dit que `personal` désigne la seconde, quel que
    soit son nom — et que le champ est bien dans la réponse déclarée."""
    from oto_mcp.capabilities.orgs.reads import MyOrgEntry
    d = _capacite("org.list")
    assert "`personal: true`" in d and "whatever its name" in d
    assert "personal" in MyOrgEntry.model_fields
