"""La SESSION Planity par credential — ce qui la partage, et ce qui ne la garde pas.

Ouvrir une session coûte plusieurs allers-retours ; la rouvrir à chaque appel
rendrait le connecteur inutilisable, et la garder trop bien ferait échouer un mot
de passe corrigé jusqu'à l'expiration du pool. Ces deux erreurs se ressemblent de
l'extérieur — « ça marche pas » — et ne se distinguent que par ces tests.

Le harnais vit dans `_planity_faux.py` ; aucun test ne parle à Planity.
"""
from __future__ import annotations

import asyncio
import types

import pytest
from _planity_faux import _outil, coeur

__all__ = ["coeur"]        # la fixture, importée pour être posée


# ── La session par credential ───────────────────────────────────────────────

def test_le_client_est_construit_avec_le_credential_du_coffre(coeur):
    _outil(coeur, "planity_list_salons")
    asyncio.run(_outil(coeur, "planity_list_salons")())
    appel = coeur.module.PlanityClient.call_args
    assert appel.args[:2] == ("demo@example.com", "s3cret")
    assert appel.args[2] is coeur.module.PlanityEndpoints.return_value, (
        "le client doit recevoir les coordonnées de l'instance — sans elles, "
        "oto-core lève à la construction")


def test_la_session_valide_le_credential_a_l_ouverture(coeur):
    """L'auth est jouée à l'ouverture, pas au premier appel métier : un mot de passe
    faux doit dire « connexion refusée », pas « salon inaccessible » plus tard."""
    asyncio.run(_outil(coeur, "planity_list_salons")())
    coeur.client.auth.get_tokens.assert_awaited()


def test_deux_appels_du_meme_credential_partagent_la_meme_session(coeur):
    """Une session Planity coûte trois allers-retours d'auth puis un WebSocket : la
    rouvrir à chaque appel rendrait le connecteur inutilisable."""
    outil = _outil(coeur, "planity_list_salons")

    async def _deux():
        await outil()
        await outil()

    asyncio.run(_deux())
    assert coeur.module.PlanityClient.call_count == 1


def test_un_autre_credential_ouvre_une_autre_session(coeur, monkeypatch):
    outil = _outil(coeur, "planity_list_salons")
    asyncio.run(outil())

    comptes = iter([{"email": "a@example.com", "password": "p1"},
                    {"email": "b@example.com", "password": "p2"}])
    monkeypatch.setattr("oto_mcp.access.resolve_credential_fields",
                        lambda provider, account=None: next(comptes))

    async def _deux():
        await outil()
        await outil()

    asyncio.run(_deux())
    assert coeur.module.PlanityClient.call_count == 3


def test_une_ouverture_en_echec_n_est_pas_mise_en_cache(coeur):
    """Sinon un mot de passe corrigé continuerait d'échouer jusqu'à l'expiration."""
    from oto_mcp.mcp_errors import McpError
    from oto_mcp.tools import planity_session

    coeur.client.auth.get_tokens.side_effect = RuntimeError("réseau")
    outil = _outil(coeur, "planity_list_salons")
    with pytest.raises(McpError):
        asyncio.run(outil())
    assert planity_session._entrees == {}

    coeur.client.auth.get_tokens.side_effect = None
    asyncio.run(outil())
    assert coeur.module.PlanityClient.call_count == 2


def test_un_credential_incomplet_est_refuse_en_le_disant(coeur, monkeypatch):
    from oto_mcp.mcp_errors import McpError

    monkeypatch.setattr("oto_mcp.access.resolve_credential_fields",
                        lambda provider, account=None: {"email": "demo@example.com",
                                                        "password": ""})
    with pytest.raises(McpError) as e:
        asyncio.run(_outil(coeur, "planity_list_salons")())
    assert "password" in str(e.value)


def test_un_refus_d_auth_de_planity_dit_de_reposer_le_credential(coeur):
    """Un 400 de Firebase veut dire « cet email ou ce mot de passe ne va pas ».
    Rendu brut, il se lit comme une panne de service — donc « réessaie », alors que
    réessayer ne peut pas aboutir."""
    from oto_mcp.mcp_errors import McpError

    refus = RuntimeError("400")
    refus.response = types.SimpleNamespace(status_code=400)
    coeur.client.auth.get_tokens.side_effect = refus

    with pytest.raises(McpError) as e:
        asyncio.run(_outil(coeur, "planity_list_salons")())
    msg = str(e.value)
    assert "password" in msg and "credential" in msg


def test_aucun_refus_ne_recrache_le_credential(coeur):
    """LE credential de ce connecteur est un MOT DE PASSE, et il est passé en
    argument à trois fonctions : n'importe quelle exception construite avec ces
    arguments se retrouverait mot pour mot dans la réponse rendue à l'agent — donc
    dans un transcript, dans le journal d'appels, chez l'utilisateur suivant.

    On exerce le PIRE cas, fabriqué exprès : une exception dont le texte porte
    l'email et le mot de passe. Aucun amont ne fait ça aujourd'hui (httpx met
    l'URL dans ses messages, jamais le corps) — c'est précisément pourquoi la
    porte se ferme maintenant, pendant qu'il n'y a pas d'incident à raconter. Un
    test qui n'exercerait que les exceptions réelles ne garderait rien."""
    from oto_mcp.mcp_errors import McpError

    coeur.client.auth.get_tokens.side_effect = RuntimeError(
        "refus pour demo@example.com / s3cret")
    with pytest.raises(McpError) as e:
        asyncio.run(_outil(coeur, "planity_list_salons")())
    msg = str(e.value)
    assert "s3cret" not in msg and "demo@example.com" not in msg
    assert "RuntimeError" in msg, "le type reste dit — sinon le refus n'aide plus"


def test_le_journal_ne_porte_pas_le_credential(coeur, caplog):
    """Même règle sur l'autre sortie : ce qui n'a pas le droit d'aller à l'agent
    n'a pas plus le droit d'aller au journal, qui vit plus longtemps."""
    coeur.client.auth.get_tokens.side_effect = RuntimeError(
        "refus pour demo@example.com / s3cret")
    with caplog.at_level("DEBUG"):
        with pytest.raises(Exception):
            asyncio.run(_outil(coeur, "planity_list_salons")())
    assert "s3cret" not in caplog.text and "demo@example.com" not in caplog.text


