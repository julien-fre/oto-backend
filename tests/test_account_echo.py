"""L'agent doit savoir SOUS QUEL compte son appel est parti.

Avec deux workspaces Slack, un agent en visait un — défaut posé, épinglage de projet
ou `_account=` — sans que rien ne le lui confirme : l'identité effective ne vivait que
dans le journal, qu'il ne lit pas. Un message envoyé sous la mauvaise identité ne se
rattrape pas.

L'écho est volontairement DISCRET : il n'apparaît que là où il y a un choix (un compte
NOMMÉ), et jamais pour un compte auxiliaire résolu par un outil composite.
"""
from __future__ import annotations

import pytest

from oto_mcp import middleware, redaction, session_org


class _Result:
    """Ce que rend un tool FastMCP, réduit aux deux canaux qui portent la donnée."""

    def __init__(self, payload, is_error: bool = False):
        self.structured_content = payload
        self.content = []
        self.is_error = is_error


@pytest.fixture()
def trace():
    tok = session_org.set_call_trace({})
    yield session_org.current_call_trace()
    session_org.reset_call_trace(tok)


def _payload(result):
    return redaction.extract_payload(result)


def test_le_compte_nomme_est_annonce(trace):
    session_org.note_call_trace(resolved_connector="slack", resolved_account="client-x")
    out = middleware._echo_account(_Result({"ok": True, "ts": "1.2"}), "slack_post_message")
    assert _payload(out) == {"ok": True, "ts": "1.2", "_account": "client-x"}


def test_mono_compte_aucun_echo(trace):
    """La ligne du coffre est anonyme tant qu'il n'y a qu'un compte : rien à choisir,
    donc rien à annoncer — 99 % des réponses ne changent pas d'un octet."""
    session_org.note_call_trace(resolved_connector="slack", resolved_account="")
    out = middleware._echo_account(_Result({"ok": True}), "slack_post_message")
    assert _payload(out) == {"ok": True}


def test_un_credential_auxiliaire_n_est_pas_annonce(trace):
    """Un outil composite peut résoudre un AUTRE connecteur en chemin. Annoncer ce
    compte-là ferait croire que l'appel est parti sous lui."""
    session_org.note_call_trace(resolved_connector="google", resolved_account="perso")
    out = middleware._echo_account(_Result({"ok": True}), "slack_post_message")
    assert _payload(out) == {"ok": True}


def test_une_erreur_est_rendue_telle_quelle(trace):
    session_org.note_call_trace(resolved_connector="slack", resolved_account="client-x")
    err = _Result({"error": "channel_not_found"}, is_error=True)
    assert middleware._echo_account(err, "slack_post_message") is err


def test_un_payload_non_dict_est_rendu_tel_quel(trace):
    session_org.note_call_trace(resolved_connector="slack", resolved_account="client-x")
    liste = _Result([{"id": 1}, {"id": 2}])
    assert middleware._echo_account(liste, "slack_read_history") is liste


def test_hors_appel_mcp_aucun_relevé():
    """Le relevé n'existe pas en REST/stdio : l'écho doit être inerte, pas lever."""
    out = middleware._echo_account(_Result({"ok": True}), "slack_post_message")
    assert _payload(out) == {"ok": True}


def test_un_champ_metier_du_meme_nom_n_est_jamais_ecrase(trace):
    session_org.note_call_trace(resolved_connector="slack", resolved_account="client-x")
    out = middleware._echo_account(_Result({"_account": "à moi"}), "slack_post_message")
    assert _payload(out) == {"_account": "à moi"}
