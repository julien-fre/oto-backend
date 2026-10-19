"""Lire les `units` qu'un outil note au relevé d'appel (L8, `access/reglement.py`).

Un outil ne débite plus la clé plateforme lui-même : il note `units`, et le bord de
l'appel règle. Les tests qui observaient l'ancien débit observent donc la note.
"""
from __future__ import annotations


def capter_units(monkeypatch, provider: str, recevoir) -> None:
    """Chaque `units` > 0 noté au relevé arrive à `recevoir(provider, units)` : c'est ce
    que le règlement débiterait sous la clé plateforme (`units=0` ne débite rien). Les
    autres clés du relevé continuent vers le `note_call_trace` en place — celui du
    module, ou le double que le test a posé AVANT d'appeler ceci."""
    from oto_mcp import session_org
    suivant = session_org.note_call_trace

    def _note(**valeurs):
        unites = valeurs.pop("units", None)
        if unites:
            recevoir(provider, unites)
        if valeurs:
            suivant(**valeurs)
    monkeypatch.setattr(session_org, "note_call_trace", _note)
