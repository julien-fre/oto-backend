"""Un `ok` de sonde doit dire QUELLE instance a répondu.

En niveau `auto`, la sonde teste le credential qui résout par la cascade — clé perso,
puis équipe, puis org, puis plateforme. Un `ok: true` nu est donc ambigu : impossible de
distinguer « ma clé perso marche » de « ma clé perso a échoué et c'est celle de l'org qui
répond ». C'est précisément le cas où la confirmation compte, puisque la perso gagne en
proximité et masque les autres.

Signalé le 03/08 en inspectant deux instances Salesforce d'une même org : les deux
passaient le verify, sans qu'on puisse dire laquelle avait été jointe.
"""
from __future__ import annotations

import pytest

from oto_mcp.capabilities import connectors_verify as cv


class _Ctx:
    sub, org_id = "sub-x", 2


class _Inp:
    provider, level = "salesforce", "auto"


class _RC:
    """Substitut de `ResolvedCredential` — seuls l'entité gagnante et le mode comptent."""

    def __init__(self, mode, entity_type, entity_id):
        self.mode, self.entity_type, self.entity_id = mode, entity_type, entity_id
        self.fields, self.config = {"client_id": "ci"}, {}


# --- le nommage de l'instance --------------------------------------------------

@pytest.mark.parametrize("mode,etype,eid,attendu", [
    ("user", "member", "2:sub-x", "member:2:sub-x:salesforce"),
    ("group", "group", "7", "group:7:salesforce"),
    ("org", "org", "2", "org:2:salesforce"),
    # Un grant plateforme n'a PAS de ligne de coffre — il faut quand même le nommer,
    # sinon le cas le plus ambigu de la cascade est justement celui qu'on ne voit pas.
    ("platform", None, None, "platform:salesforce"),
])
def test_la_sonde_nomme_linstance_jointe(monkeypatch, mode, etype, eid, attendu):
    from oto_mcp import access
    monkeypatch.setattr(access, "resolve_credential",
                        lambda *a, **k: _RC(mode, etype, eid))
    _, _, _, instance = cv._fields_config_scope(_Ctx(), _Inp())
    assert instance == {"level": mode, "ref": attendu}


def test_le_niveau_org_se_nomme_sans_passer_par_la_cascade(monkeypatch):
    """`level='org'` vise la clé de l'org SPÉCIFIQUEMENT (une clé perso la masquerait) :
    le ref rendu doit donc désigner l'org, pas ce que la cascade aurait choisi."""
    from oto_mcp import credentials_store
    monkeypatch.setattr(credentials_store, "get_credential_with_meta",
                        lambda *a, **k: {"secret": "s", "meta": {}})
    monkeypatch.setattr(credentials_store, "unpack_secret", lambda *a: {"client_id": "ci"})
    monkeypatch.setattr(credentials_store, "public_meta", lambda m: {})

    class _InpOrg:
        provider, level = "salesforce", "org"

    _, _, _, instance = cv._fields_config_scope(_Ctx(), _InpOrg())
    assert instance == {"level": "org", "ref": "org:2:salesforce"}


# --- la remontée jusqu'à la réponse --------------------------------------------

def test_le_ref_arrive_dans_la_reponse():
    """TRIPWIRE. Nommer l'instance en interne ne sert à rien si la réponse la perd —
    c'est exactement ce qui se passait : la résolution CONNAISSAIT l'entité gagnante et
    la jetait."""
    import inspect
    src = inspect.getsource(cv._verify)
    assert src.count("**instance") >= 2, (
        "l'instance sondée n'est pas jointe aux DEUX sorties (nominale et `pending`)")


def test_un_credential_incomplet_nomme_aussi_son_instance():
    """Le cas « il reste le consentement » renvoie tôt : sans l'instance, on saurait
    qu'il manque une étape mais pas SUR QUELLE clé."""
    import inspect
    src = inspect.getsource(cv._verify)
    bloc = src[src.index('"pending": True'):src.index("started = time.monotonic()")]
    assert "**instance" in bloc
