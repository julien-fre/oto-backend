"""Une liste d'identités vide ne décrit pas la santé du connecteur (#850).

Mesuré le 10/09/2026 : `oto_identity(op=list, connector=slack)` rendait
`identities: []` et « rien n'est connecté » pendant que, **dans la même minute**, le
même appelant rejoignait un canal et lisait 37 messages sur l'espace de travail visé.

⚠️ **Ce qui rend ce défaut coûteux n'est pas la réponse vide, c'est son usage.** La
veille, tous les appels échouaient vraiment — et cette même lecture répondait `[]`
**aussi**. Elle a donc servi de corroboration à une conclusion fausse (« un humain
doit reconnecter l'espace de travail »), alors que personne n'a rien reconnecté et
que tout remarchait le lendemain. *Une lecture qui rend la même réponse quand tout
va bien et quand tout est cassé ne corrobore rien : elle confirme ce que son lecteur
croit déjà.* C'est pire qu'une absence de lecture, comme le signal le dit lui-même.

⚠️ **La nuance était déjà dans le code, sans être servie** : l'état rendu ici
signifie « les couches sont bonnes, il reste un geste ». Autrement dit la clé
résout. L'information existait, elle ne sortait pas.

⚠️ **Et l'avertissement va dans un champ SÉPARÉ, pas dans le geste.** Le geste du
connecteur est relayé tel quel — deux surfaces qui le reformulent racontent deux
histoires, et son banc garde cette égalité exacte. C'est ce banc voisin qui a
rattrapé ma première version, qui concaténait.

Éprouvé rouge le 2026-09-10 : `layers_ok` retiré ⟹ le premier test constate qu'une
liste vide reste indistinguable d'un connecteur mort.
"""
from __future__ import annotations

import pytest

from oto_mcp import access
from oto_mcp.capabilities.connectors import identities as I
from oto_mcp.connectors import readiness as R


class _Ctx:
    sub = "u1"
    org_id = 7


@pytest.fixture()
def couches_bonnes(monkeypatch):
    """Le connecteur résout tout et attend un geste — l'état du cas mesuré."""
    monkeypatch.setattr(R, "diagnose",
                        lambda *a, **k: R.Diagnosis(reason=R.PENDING_STEP,
                                                    next_step="Connecte un canal"))
    monkeypatch.setattr(access, "current_group", lambda sub: None)
    return _Ctx()


def test_une_liste_vide_DIT_que_les_couches_resolvent(couches_bonnes):
    out = I._why_empty(couches_bonnes, "slack", "canal")
    assert out["layers_ok"] is True, (
        "sans ce fait, la liste vide reste indistinguable d'un connecteur mort")


def test_elle_dit_de_NE_PAS_s_en_servir_pour_expliquer_un_echec(couches_bonnes):
    """Le cœur du signal : c'est l'usage en corroboration qui a coûté une journée."""
    note = I._why_empty(couches_bonnes, "slack", "canal")["scope_note"]
    assert "do not conclude that a failing call fails for this reason" in note
    assert "identity registry" in note


def test_elle_nomme_la_lecture_qui_TRANCHE_vraiment(couches_bonnes):
    """Dire « ne conclus pas » sans dire quoi faire laisse l'appelant où il était."""
    note = I._why_empty(couches_bonnes, "slack", "canal")["scope_note"]
    assert "op='verify'" in note and "slack" in note


def test_le_GESTE_du_connecteur_reste_relaye_TEL_QUEL(couches_bonnes):
    """La doctrine que ma première version violait : `next_step` est au connecteur,
    la mise en garde a sa propre clé."""
    out = I._why_empty(couches_bonnes, "slack", "canal")
    assert out["next_step"] == "Connecte un canal"


def test_sans_diagnostic_lisible_on_n_affirme_PAS_que_les_couches_vont_bien(monkeypatch):
    """Quand le diagnostic ne se lit pas, on ne sait rien — et un `layers_ok` posé
    là serait une affirmation inventée, exactement le défaut qu'on corrige."""
    monkeypatch.setattr(R, "diagnose", lambda *a, **k: None)
    monkeypatch.setattr(access, "current_group", lambda sub: None)
    out = I._why_empty(_Ctx(), "slack", "canal")
    assert "layers_ok" not in out and "scope_note" not in out
    assert out["reason"] == "no_identity_connected" and out["next_step"]


def test_une_couche_qui_MANQUE_vraiment_garde_son_propre_motif(monkeypatch):
    """La contre-épreuve : quand une couche manque, le motif n'est pas « rien de
    lié » et le lot ne doit pas l'écraser."""
    monkeypatch.setattr(R, "diagnose",
                        lambda *a, **k: R.Diagnosis(reason="no_credential",
                                                    next_step="Pose une clé"))
    monkeypatch.setattr(access, "current_group", lambda sub: None)
    out = I._why_empty(_Ctx(), "slack", "canal")
    assert out["reason"] == "no_credential" and "layers_ok" not in out
