"""Lot L2 (ADR 0052) — registre d'émetteurs et qualification du sub.

Ce que le lot promet, dans l'ordre de ce qui coûterait le plus cher à rater :

1. **Le tenant `oto` garde un sub NU.** L'AAD du coffre dérive du sub
   (`credentials_store._aad`) : qualifier le sub du tenant `oto` rendrait TOUS les
   credentials de production indéchiffrables. Le test qui compte porte donc sur le
   coffre, pas sur une égalité de chaînes — et il montre aussi le contraire (avec
   un sub qualifié, le déchiffrement échoue), pour que la raison de l'invariant
   soit dans le test et pas seulement dans un commentaire.
2. **Non-collision entre tenants** : un sub qualifié ne peut jamais désigner la
   ligne d'un sub nu, ni celle d'un autre tenant.
3. **Le drain est absorbé** : `LOGTO_ENDPOINT_ALT` devient une entrée du registre
   sur le tenant `oto`, pas un mécanisme parallèle.
4. **L'env gagne toujours sur la base** : une ligne `tenants` qui réclamerait
   l'émetteur primaire re-tenanterait les comptes existants — donc changerait leur
   sub, donc leur AAD. Refusée.

Le chemin SQL (lecture des tenants) n'est pas exerçable ici — pas de PostgreSQL sur
le poste : `tenancy.build` est une fonction PURE, on lui passe les lignes.
"""
from __future__ import annotations

import asyncio

import pytest

from oto_mcp import credentials_store, crypto, server, tenancy

_PRIMARY = "https://auth.oto.ninja/oidc"
_DRAIN = "https://auth.oto.zone/oidc"
_TIERS = "https://auth.tulina.ai/oidc"


def _registry(*tenant_rows, drains=()):
    return tenancy.IssuerRegistry(
        tenancy.build(_PRIMARY, drain_issuers=drains, tenants=tenant_rows))


# --- 1. L'invariant qui commande tout : le coffre du tenant `oto` ne bouge pas ---

@pytest.fixture
def master_key(monkeypatch):
    monkeypatch.setenv("OTO_MCP_MASTER_KEY", "0" * 64)  # 32 octets, hex


def test_un_credential_existant_reste_dechiffrable(master_key):
    """Le test qui compte (issue #272) : une ligne posée AVANT le lot se relit APRÈS.

    On rejoue la chaîne complète — un jeton du tenant `oto` produit un sub, le sub
    produit un `entity_id` membre, l'`entity_id` produit l'AAD, l'AAD ouvre le
    ciphertext. Rien ne doit avoir bougé d'un octet.
    """
    sub_pose = "abc123"                      # le sub du jour où la clé a été posée
    aad_pose = credentials_store._aad(
        credentials_store.MEMBER, credentials_store.member_id(12, sub_pose), "folk")
    scelle = crypto.encrypt("secret-du-client", aad_pose)

    # Le même utilisateur revient, après le lot : son jeton vient de l'émetteur
    # primaire, donc du tenant `oto`.
    registre = _registry({"slug": "tulina", "issuer": _TIERS})
    sub_apres = registre.qualify_claims({"sub": sub_pose, "iss": _PRIMARY})

    assert sub_apres == sub_pose, "le sub du tenant `oto` doit rester NU"
    aad_apres = credentials_store._aad(
        credentials_store.MEMBER, credentials_store.member_id(12, sub_apres), "folk")
    assert aad_apres == aad_pose
    assert crypto.decrypt(scelle, aad_apres) == "secret-du-client"


def test_qualifier_le_sub_du_tenant_oto_casserait_le_coffre(master_key):
    """Le pendant du test précédent — la raison pour laquelle `oto` reste nu.

    S'il venait à l'idée de qualifier uniformément (« c'est plus régulier »), voilà
    ce que ça produit en production : `InvalidTag`, traduit en erreur métier."""
    aad_pose = credentials_store._aad(
        credentials_store.MEMBER, credentials_store.member_id(12, "abc123"), "folk")
    scelle = crypto.encrypt("secret-du-client", aad_pose)
    aad_si_qualifie = credentials_store._aad(
        credentials_store.MEMBER,
        credentials_store.member_id(12, tenancy.qualify("oto-bis", "abc123")), "folk")
    with pytest.raises(RuntimeError, match="indéchiffrable"):
        crypto.decrypt(scelle, aad_si_qualifie)


# --- 2. Non-collision -------------------------------------------------------

def test_un_sub_qualifie_ne_peut_pas_designer_une_ligne_du_tenant_oto():
    """Les subs Logto ne contiennent pas de `:` : un sub qualifié est donc en dehors
    de l'espace des subs nus, quelle que soit la valeur du sub d'origine."""
    for sub in ("abc123", "1", "u-42", "0123456789abcdef0123"):
        qualifie = tenancy.qualify("tulina", sub)
        assert qualifie != sub
        assert ":" in qualifie and ":" not in sub
        # …et pas davantage la ligne d'un AUTRE tenant.
        assert qualifie != tenancy.qualify("acme", sub)


def test_deux_emetteurs_ne_produisent_pas_le_meme_sub():
    """Le même sub Logto chez deux tenants = deux identités distinctes. C'est
    exactement ce qui rendrait un coffre lisible par le mauvais compte."""
    registre = _registry({"slug": "tulina", "issuer": _TIERS})
    chez_oto = registre.qualify_claims({"sub": "abc123", "iss": _PRIMARY})
    chez_tiers = registre.qualify_claims({"sub": "abc123", "iss": _TIERS})
    assert chez_oto == "abc123"
    assert chez_tiers == "tulina:abc123"
    assert chez_oto != chez_tiers


def test_un_slug_qui_rendrait_la_qualification_ambigue_est_refuse():
    """Le slug entre DANS le sub : un `:` dedans rendrait indécidable où finit le
    tenant et où commence le sub. Rien n'est rattrapé au passage (` tulina` est
    refusé, pas trimé) — sinon l'identité en base et l'identité en vol diffèrent."""
    for slug in ("tu:lina", "", "Tulina", "tulina/x", " tulina"):
        registre = _registry({"slug": slug, "issuer": _TIERS})
        assert registre.get(_TIERS) is None, f"slug {slug!r} accepté à tort"
        assert registre.qualify_claims({"sub": "abc", "iss": _TIERS}) == "abc"


# --- 3. Le drain est absorbé par le registre --------------------------------

def test_le_drain_est_une_entree_du_registre_sur_le_tenant_oto():
    """`LOGTO_ENDPOINT_ALT` était « une fenêtre, puis on retire l'env ». C'est le
    cas particulier de « un tenant, deux émetteurs » — donc une entrée, pas un
    mécanisme à côté. Et un sub qui arrive par le drain reste NU."""
    registre = _registry(drains=[_DRAIN])
    assert registre.slug_for(_PRIMARY) == tenancy.PRIMARY_SLUG
    assert registre.slug_for(_DRAIN) == tenancy.PRIMARY_SLUG
    assert registre.qualify_claims({"sub": "abc123", "iss": _DRAIN}) == "abc123"


def test_le_jwks_est_derive_faute_de_declaration():
    registre = _registry({"slug": "tulina", "issuer": _TIERS},
                         {"slug": "acme", "issuer": "https://id.acme.test/oidc",
                          "jwks_uri": "https://keys.acme.test/jwks.json"})
    assert registre.get(_TIERS).jwks_uri == f"{_TIERS}/jwks"
    assert registre.get("https://id.acme.test/oidc").jwks_uri == \
        "https://keys.acme.test/jwks.json"


def test_le_slash_final_ne_fait_pas_deux_emetteurs():
    registre = _registry({"slug": "tulina", "issuer": _TIERS + "/"})
    assert registre.slug_for(_TIERS) == "tulina"
    assert registre.slug_for(_TIERS + "/") == "tulina"


# --- 4. L'env gagne sur la base ---------------------------------------------

def test_une_ligne_qui_reclame_lemetteur_primaire_est_ignoree():
    """Sinon un `UPDATE tenants` suffirait à re-tenanter tous les comptes existants
    — donc à changer leur sub, donc l'AAD de leurs credentials."""
    registre = _registry({"slug": "pirate", "issuer": _PRIMARY})
    assert registre.slug_for(_PRIMARY) == tenancy.PRIMARY_SLUG
    assert registre.qualify_claims({"sub": "abc123", "iss": _PRIMARY}) == "abc123"


def test_une_ligne_qui_reclame_un_drain_est_ignoree():
    registre = _registry({"slug": "pirate", "issuer": _DRAIN}, drains=[_DRAIN])
    assert registre.slug_for(_DRAIN) == tenancy.PRIMARY_SLUG


def test_un_emetteur_deja_tenu_ne_se_reprend_pas():
    """Premier arrivé (ordre `tenants.id`) : la sélection par `iss` ne doit jamais
    dépendre de l'ordre de lecture pour décider QUI est l'appelant."""
    registre = _registry({"slug": "tulina", "issuer": _TIERS},
                         {"slug": "acme", "issuer": _TIERS})
    assert registre.slug_for(_TIERS) == "tulina"


def test_un_emetteur_inconnu_retombe_sur_le_primaire():
    """Il sera rejeté par le verifier primaire (`iss` différent du sien) : fermé,
    jamais accepté sous une identité qualifiée par défaut."""
    registre = _registry({"slug": "tulina", "issuer": _TIERS})
    assert registre.slug_for("https://ailleurs.example/oidc") == tenancy.PRIMARY_SLUG
    assert registre.slug_for(None) == tenancy.PRIMARY_SLUG


def test_une_base_indisponible_ne_coupe_pas_lauth_canonique(monkeypatch):
    """L'émetteur primaire vient de l'env : un hoquet de base rend les tenants tiers
    injoignables (leurs jetons routent vers le primaire, qui refuse) sans jamais
    couper la plateforme."""
    class _Boom:
        def list_tenant_issuers(self):
            raise RuntimeError("pool épuisé")

    monkeypatch.setitem(__import__("sys").modules, "oto_mcp.db", _Boom())
    assert tenancy.load_tenants() == []


# --- Le sub sort du verifier, une seule fois --------------------------------

class _StubVerifier:
    """Verifier d'un tenant tiers — on n'exerce pas la crypto JWT ici, seulement le
    routage et la qualification."""

    def __init__(self, result):
        self.result = result
        self.calls = 0

    async def verify_token(self, token):
        self.calls += 1
        return self.result


class _Verifier(server._IatGatedVerifier):
    """Instance nue (le parent exigerait un JWKS joignable)."""

    def __init__(self, by_issuer):
        self._by_issuer = by_issuer
        self._min_iat = 0
        self._expected_audience = None
        self._alt_audiences = frozenset()


def _jwt(iss: str) -> str:
    """Jeton de FORME valide (3 segments) : seul le claim `iss` est lu ici, et il
    ne sert qu'à router — la vérification, c'est le verifier retenu qui la fait."""
    import base64
    import json
    payload = base64.urlsafe_b64encode(
        json.dumps({"iss": iss, "sub": "abc123"}).encode()).decode().rstrip("=")
    return f"entete.{payload}.signature"


def _access_token():
    from fastmcp.server.auth.auth import AccessToken
    return AccessToken(token="t", client_id="c", scopes=[], subject="abc123",
                       claims={"sub": "abc123", "iss": _TIERS})


def test_le_verifier_qualifie_le_sub_dun_tenant_tiers(monkeypatch):
    monkeypatch.setattr(server.db, "verify_api_token", lambda t: None)
    stub = _StubVerifier(_access_token())
    v = _Verifier({_TIERS: ("tulina", stub)})
    out = asyncio.run(v.verify_token(_jwt(_TIERS)))
    assert stub.calls == 1, "le jeton doit être routé vers le verifier du tenant"
    assert out.claims["sub"] == "tulina:abc123"
    assert out.subject == "tulina:abc123"


def test_le_verifier_rend_le_sub_du_tenant_oto_tel_quel(monkeypatch):
    """Byte pour byte : c'est l'objet même du lot."""
    monkeypatch.setattr(server.db, "verify_api_token", lambda t: None)
    jeton = _access_token()

    async def _super(self, token):
        return jeton

    monkeypatch.setattr(server.JWTVerifier, "verify_token", _super)
    v = _Verifier({_PRIMARY: (tenancy.PRIMARY_SLUG, None)})
    out = asyncio.run(v.verify_token(_jwt(_PRIMARY)))
    assert out is jeton, "aucune copie, aucune retouche pour le tenant `oto`"
    assert out.claims["sub"] == "abc123"


def test_un_jeton_recale_ne_produit_aucun_sub(monkeypatch):
    """La qualification est le DERNIER geste : un jeton refusé par l'audience ou par
    l'iat-gate ne doit jamais avoir fabriqué d'identité."""
    monkeypatch.setattr(server.db, "verify_api_token", lambda t: None)
    v = _Verifier({_TIERS: ("tulina", _StubVerifier(_access_token()))})
    v._min_iat = 99_999_999_999
    assert asyncio.run(v.verify_token(_jwt(_TIERS))) is None


def test_un_jeton_dapi_nest_pas_requalifie(monkeypatch):
    """Son sub sort de `users`, où il a été écrit DÉJÀ qualifié : le repréfixer
    fabriquerait `tulina:tulina:abc123`, c'est-à-dire un compte fantôme."""
    monkeypatch.setattr(server.db, "verify_api_token",
                        lambda t: {"sub": "tulina:abc123", "scopes": None})
    v = _Verifier({_TIERS: ("tulina", _StubVerifier(_access_token()))})
    out = asyncio.run(v.verify_token("oto_zzz"))
    assert out.claims["sub"] == "tulina:abc123"
    assert out.subject == "tulina:abc123"
