"""Révoquer un jeton laisse une trace (#523).

⚠️ **Constaté le 28/08** en coupant un jeton non porté qui ouvrait toute l'org :
révoquer était un `DELETE` de la ligne. Après coup, on ne savait plus qui le
détenait, ni quand ni pourquoi il avait été coupé — précisément le cas où la trace
compte (un jeton trop large stocké chez un tiers). Les partages et les instances de
connecteur gardaient, eux, leur date de révocation.

Ce qu'on vérifie contre la VRAIE base : la ligne reste, le jeton ne s'authentifie
plus, la liste ne le montre que sur demande, et la trace porte qui/quand/pourquoi.
"""
from __future__ import annotations

import uuid

import pytest


@pytest.fixture()
def sub(live):
    from oto_mcp import db
    s = "usr_revoc_" + uuid.uuid4().hex[:6]
    db.upsert_user(s, email=f"{s}@revoc.invalid", name=s)
    return s


def _id(sub: str, label: str) -> int:
    from oto_mcp import db
    return next(t["id"] for t in db.list_api_tokens(sub, include_revoked=True)
                if t["label"] == label)


def test_un_jeton_revoque_ne_s_authentifie_plus_mais_sa_ligne_reste(sub):
    """⚠️ LE test : c'est la trace qui manquait."""
    from oto_mcp import db

    secret = db.create_api_token(sub, label="trop large")
    assert db.verify_api_token(secret) is not None
    tid = _id(sub, "trop large")

    assert db.revoke_api_token(sub, tid, revoked_by="usr_admin",
                               reason="stocké chez un tiers") is True

    assert db.verify_api_token(secret) is None, "un jeton révoqué s'authentifie encore"
    [trace] = [t for t in db.list_api_tokens(sub, include_revoked=True) if t["id"] == tid]
    assert trace["revoked_at"] is not None
    assert trace["revoked_by"] == "usr_admin"
    assert trace["revoked_reason"] == "stocké chez un tiers"


def test_la_liste_ne_montre_les_revoques_que_sur_demande(sub):
    from oto_mcp import db

    db.create_api_token(sub, label="actif")
    db.create_api_token(sub, label="coupé")
    db.revoke_api_token(sub, _id(sub, "coupé"), revoked_by=sub, reason=None)

    assert [t["label"] for t in db.list_api_tokens(sub)] == ["actif"]
    assert sorted(t["label"] for t in db.list_api_tokens(sub, include_revoked=True)) \
        == ["actif", "coupé"]


def test_une_seconde_revocation_n_ecrase_pas_la_premiere(sub):
    from oto_mcp import db

    db.create_api_token(sub, label="deux fois")
    tid = _id(sub, "deux fois")
    assert db.revoke_api_token(sub, tid, revoked_by="premier", reason="fuite")
    assert db.revoke_api_token(sub, tid, revoked_by="second", reason="autre") is False
    [trace] = [t for t in db.list_api_tokens(sub, include_revoked=True) if t["id"] == tid]
    assert (trace["revoked_by"], trace["revoked_reason"]) == ("premier", "fuite")


def test_on_ne_revoque_pas_le_jeton_d_un_autre(sub):
    from oto_mcp import db

    secret = db.create_api_token(sub, label="le mien")
    assert db.revoke_api_token("usr_autre", _id(sub, "le mien"),
                               revoked_by="usr_autre", reason=None) is False
    assert db.verify_api_token(secret) is not None
