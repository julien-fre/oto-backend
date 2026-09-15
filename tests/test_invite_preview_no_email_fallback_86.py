"""oto#86 fuite 3 — repli d'invitation vers l'email de l'invitant, sur une route
ANONYME. LA PLUS URGENTE des trois fuites de l'issue.

`_PREVIEW_SELECT` (org_store/invitations.py) projetait `COALESCE(u.name, u.email)
AS inviter` : nommer l'invitant est intentionnel et documenté (accompagner
l'accueil avant la création de compte, sur `GET /api/invitations/{token}` et
`GET /api/invitations/code/{code}`, SANS authentification — le jeton/code EST
le secret). Le repli ne l'était pas : un compte invitant fraîchement créé, sans
nom encore déclaré, servait son ADRESSE DE COURRIEL à un anonyme.

Vrai PostgreSQL, base neuve pour ce module : ce banc n'a de valeur que sur la
vraie requête de projection — une doublure qui la recopierait ne prouverait
qu'un graphe d'appels, pas l'absence du repli dans le SQL réel.
"""
from __future__ import annotations

import os

import pytest


@pytest.fixture(scope="module")
def live(pg_module_dsn):
    pytest.importorskip("psycopg")
    url_avant = os.environ.get("DATABASE_URL")
    os.environ["DATABASE_URL"] = pg_module_dsn
    try:
        from oto_mcp.db import init_db
        init_db()
        yield
    finally:
        if url_avant is None:
            os.environ.pop("DATABASE_URL", None)
        else:
            os.environ["DATABASE_URL"] = url_avant


def test_invitant_sans_nom_ne_sert_pas_son_email(live):
    """Compte fraîchement créé (signup email+code, jamais de `name` déclaré) qui
    émet une invitation : l'aperçu ANONYME ne doit servir ni son adresse telle
    quelle, ni la faire apparaître dans le champ `inviter` sous quelque forme."""
    from oto_mcp import org_store
    from oto_mcp.db import upsert_user

    emetteur = "u-86-emetteur-sans-nom"
    email_emetteur = f"{emetteur}@x.tld"
    upsert_user(emetteur, email=email_emetteur)  # compte frais, AUCUN nom déclaré

    oid = org_store.create_org("Org 86", created_by=emetteur)
    _id, token, code = org_store.create_invitation(
        oid, "invite-86@x.tld", "org_member", emetteur)

    for apercu in (org_store.preview_invitation(token),
                   org_store.preview_invitation_by_code(code)):
        assert apercu is not None
        assert apercu["inviter"] != email_emetteur, (
            f"l'aperçu ANONYME sert l'email de l'invitant faute de nom — "
            f"oto#86 ({apercu!r})")
        assert email_emetteur not in (apercu["inviter"] or ""), (
            f"l'adresse de l'invitant fuite dans le libellé — oto#86 ({apercu!r})")


def test_invitant_avec_nom_reste_servi(live):
    """Non-régression : la fonctionnalité INTENTIONNELLE (nommer l'invitant quand
    son profil a un nom) ne doit pas disparaître avec le repli."""
    from oto_mcp import org_store
    from oto_mcp.db import upsert_user

    emetteur = "u-86-emetteur-avec-nom"
    upsert_user(emetteur, email=f"{emetteur}@x.tld", name="Jane Doe")

    oid = org_store.create_org("Org 86 bis", created_by=emetteur)
    _id, token, _code = org_store.create_invitation(
        oid, "invite-86-bis@x.tld", "org_member", emetteur)

    apercu = org_store.preview_invitation(token)
    assert apercu is not None
    assert apercu["inviter"] == "Jane Doe"
