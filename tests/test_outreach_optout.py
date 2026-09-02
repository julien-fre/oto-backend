"""Le lien de désinscription : le jeton, et la page qui le consomme.

Un lien de désinscription est la seule promesse d'un mail de relance que le
destinataire peut vérifier lui-même. Il doit donc marcher **sans session, sans JS,
sans expiration** — et le prouver ici, parce qu'aucun de ces trois points ne se voit
en relisant le handler.

⚠️ **Le jeton ne périme pas, délibérément.** Le mail qu'on relit six mois plus tard est
précisément celui dont on ne veut plus ; un « ce lien a expiré » à ce moment-là
transforme un refus en corvée. Ce qu'il autorise borne le risque : cesser de recevoir
NOS relances, pour un compte que le porteur devait déjà connaître.
"""
from __future__ import annotations

import asyncio

import pytest
from starlette.requests import Request

from oto_mcp import outreach_optout as O

SUB = "sub-quelqu-un"


@pytest.fixture(autouse=True)
def secret(monkeypatch):
    monkeypatch.setenv("OTO_MCP_OAUTH_STATE_SECRET", "s" * 40)


def test_le_jeton_fait_l_aller_retour():
    assert O.verify(O.sign(SUB)) == SUB


def test_le_jeton_est_STABLE_pour_un_meme_compte():
    """Deux relances ne doivent pas fabriquer deux liens vivants à révoquer un jour."""
    assert O.sign(SUB) == O.sign(SUB)


@pytest.mark.parametrize("bidon", [
    "", "pas-de-point", "a.b", "eyJ0eXAiOiJvcHRvdXQifQ.mauvaise-signature",
])
def test_un_jeton_qui_ne_verifie_pas_rend_None(bidon):
    assert O.verify(bidon) is None


def test_un_jeton_signe_par_un_AUTRE_secret_est_refuse(monkeypatch):
    jeton = O.sign(SUB)
    monkeypatch.setenv("OTO_MCP_OAUTH_STATE_SECRET", "autre" * 8)
    assert O.verify(jeton) is None


def test_un_jeton_d_un_AUTRE_type_ne_passe_pas():
    """`upload_tokens` signe avec le MÊME secret d'instance : sans le contrôle de
    `typ`, un jeton d'upload vaudrait désinscription (et réciproquement)."""
    from oto_mcp import upload_tokens
    jeton, _exp = upload_tokens.sign(SUB, None, {"kind": "x"})
    assert O.verify(jeton) is None


def test_sans_secret_d_instance_on_ne_FABRIQUE_pas_de_lien(monkeypatch):
    """Plutôt qu'un lien mort dans le pied de page de dizaines de mails."""
    monkeypatch.delenv("OTO_MCP_OAUTH_STATE_SECRET", raising=False)
    with pytest.raises(O.OptOutSecretManquant):
        O.lien(SUB)


def test_le_lien_pointe_le_BACKEND_pas_le_dashboard(monkeypatch):
    """La désinscription doit fonctionner sans que le front soit déployé, servi ou
    joignable — c'est l'argument de la page publique d'un doc partagé."""
    monkeypatch.setenv("OTO_MCP_PUBLIC_URL", "https://mcp.exemple.test/")
    assert O.lien(SUB).startswith("https://mcp.exemple.test/o/u/")


# ── la route publique ────────────────────────────────────────────────────────

def _get(token: str):
    from oto_mcp.api import public
    req = Request({"type": "http", "method": "GET", "path": f"/o/u/{token}",
                   "headers": [], "query_string": b"",
                   "path_params": {"token": token}})
    return asyncio.run(public.outreach_unsubscribe(req))


@pytest.fixture
def base(monkeypatch):
    """Un faux store : on veut voir CE QUI EST ÉCRIT, pas une base."""
    from oto_mcp.api import public as _public  # noqa: F401 — force l'import du module
    from oto_mcp.db import outreach as db_outreach
    from oto_mcp import db
    ecrits: list = []
    monkeypatch.setattr(db_outreach, "desinscrire",
                        lambda sub, source="link": ecrits.append((sub, source)))
    monkeypatch.setattr(db, "get_user", lambda sub: {"locale": "en"})
    return ecrits


def test_un_lien_valide_desinscrit_et_confirme(base):
    rep = _get(O.sign(SUB))
    assert rep.status_code == 200
    assert base == [(SUB, "link")]
    assert b"Done" in rep.body, "la page suit la langue déclarée du compte"


def test_recharger_la_page_n_est_pas_une_erreur(base):
    """Idempotent : un préchargeur de webmail, un double-clic, un retour arrière."""
    jeton = O.sign(SUB)
    assert _get(jeton).status_code == 200 and _get(jeton).status_code == 200
    assert base == [(SUB, "link"), (SUB, "link")]


def test_un_lien_trafique_n_ecrit_RIEN(base):
    rep = _get(O.sign(SUB)[:-4] + "aaaa")
    assert rep.status_code == 400
    assert base == [], "un jeton qu'on ne sait pas lire ne désinscrit personne"
    assert "Lien invalide" in rep.body.decode()


def test_la_page_ne_fuite_pas_le_compte(base):
    """La confirmation ne nomme personne : un lien qui circule ne doit pas révéler
    l'adresse ou l'identifiant de celui à qui il a été envoyé."""
    rep = _get(O.sign(SUB))
    assert SUB.encode() not in rep.body


def test_la_page_est_autoportee_sans_JS(base):
    corps = _get(O.sign(SUB)).body.decode()
    assert "<script" not in corps and "fetch(" not in corps


def test_un_compte_inconnu_est_quand_meme_desinscrit(monkeypatch, base):
    """Le refus ne dépend pas de l'existence d'une fiche : la ligne se pose, et la
    page repasse en français faute de préférence lisible."""
    from oto_mcp import db
    monkeypatch.setattr(db, "get_user", lambda sub: None)
    rep = _get(O.sign(SUB))
    assert rep.status_code == 200 and base == [(SUB, "link")]
    assert "C'est noté" in rep.body.decode()


def test_la_route_est_MONTEE_et_sans_auth():
    """La table de routes est le contrat : un handler non monté est un lien mort dans
    des dizaines de mails, et personne ne le découvre avant que quelqu'un clique."""
    from oto_mcp.api import public, routes as api_routes
    montees = {(r.path, tuple(sorted(r.methods or ())))
               for r in api_routes.make_routes(object())
               if getattr(r, "path", "").startswith("/o/u/")}
    assert montees == {("/o/u/{token}", ("GET", "HEAD"))}
    route = next(r for r in api_routes.make_routes(object())
                 if getattr(r, "path", "") == "/o/u/{token}")
    assert route.endpoint is public.outreach_unsubscribe, (
        "montée derrière un `bind(..., verifier=...)`, elle exigerait un jeton — donc "
        "une session, à celui-là même qui ne veut plus rien avoir à faire avec nous.")


def test_sans_secret_la_VERIFICATION_leve_au_lieu_de_dire_lien_invalide(monkeypatch):
    """Le seul endroit où ce module ne se tait pas, et c'est voulu : rendre `None`
    afficherait « lien invalide » à quelqu'un dont le lien est parfaitement valide.
    Son refus serait perdu, et la faute lui serait attribuée — alors que la panne est
    la nôtre. Les autres causes de rejet (signature, forme, `typ`) rendent bien None :
    celles-là ne sont pas de notre fait."""
    jeton = O.sign(SUB)
    monkeypatch.delenv("OTO_MCP_OAUTH_STATE_SECRET", raising=False)
    with pytest.raises(O.OptOutSecretManquant):
        O.verify(jeton)
