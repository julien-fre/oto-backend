"""Les connecteurs OAuth fédérés ont enfin une entrée dans `me.providers`.

`access.status_for` remplissait `me.providers` par TROIS boucles — keyés, à champs, à
session navigateur. atlassian, folkmcp et google ne sont dans aucune (`keyed=False`,
`secret_fields=0`, `secret_kind='oauth'`), donc ils n'avaient **aucune entrée**. Personne
ne l'avait vu, et ça expliquait quatre choses d'un coup :

- un hook `status_hints` sur eux aurait été inatteignable (la décoration itère les
  entrées EXISTANTES) ;
- `health_ko` idem ;
- le verdict de leur fiche n'avait rien à lire ;
- et le dashboard interrogeait `/api/<nom>/oauth/status` — un nom de connecteur dans une
  URL — faute d'état dans `/api/me`.

Ce fichier verrouille la fermeture du trou ET la forme émise : c'est un contrat
cross-repo (le dashboard lit ces clés) que rien d'autre ne mécanise.
"""
from __future__ import annotations

import pytest

from oto_mcp import providers
from oto_mcp.connectors import link as connector_link

# L'import est CE qui déclare : ces modules s'enregistrent au niveau module.
from oto_mcp import atlassian_oauth, folk_oauth, google_oauth  # noqa: F401,E402


def _federated() -> set[str]:
    return {n for n, c in providers.REGISTRY.items() if c.secret_kind == "oauth"}


def test_tout_connecteur_oauth_federe_declare_sa_lecture():
    """TOTALITÉ. Sans déclaration, le connecteur retombe silencieusement dans le trou —
    exactement l'état d'avant. Un oubli doit casser la CI, pas disparaître."""
    manquants = sorted(_federated() - set(connector_link.entries()))
    assert not manquants, (
        f"{manquants} : credential OAuth fédéré sans lecture d'état déclarée "
        "(`connector_link.register`) → aucune entrée dans me.providers, donc pas de "
        "verdict, pas de pending_action, pas de health_ko.")


def test_le_perimetre_est_celui_quon_croit():
    # Memento a été décommissionné le 30/07 : il était le quatrième.
    assert _federated() == {"atlassian", "folkmcp", "google"}


# --- la forme émise, contrat lu par le dashboard -------------------------------

_CLES_ATTENDUES = {
    "mode", "user_key_configured", "session_set_at", "group_secret_configured",
    "org_secret_configured", "platform_key_label", "quota_used_today", "quota_daily",
}


@pytest.mark.parametrize("linked", [True, False])
def test_la_traduction_vers_provider_status_est_complete(monkeypatch, linked):
    """Le module rend `LinkState` (son vocabulaire) ; `status_for` doit le TRADUIRE en
    `ProviderStatus` (celui du dashboard). Une clé manquante ici, et la carte lit
    `undefined` sans que rien ne le signale."""
    from oto_mcp import access

    monkeypatch.setattr(connector_link, "state",
                        lambda name, sub: connector_link.LinkState(
                            linked=linked, set_at="2026-07-31 10:00:00",
                            accounts=1 if linked else 0))
    entry = _entry_for(access, "atlassian")
    assert set(entry) >= _CLES_ATTENDUES, f"clés manquantes : {_CLES_ATTENDUES - set(entry)}"
    assert entry["user_key_configured"] is linked
    # `forbidden` = « aucune clé ne résout » (état par défaut d'un BYO pas connecté),
    # PAS un refus RBAC — la carte s'appuie dessus pour dire « à connecter ».
    assert entry["mode"] == ("user" if linked else "forbidden")


def test_une_lecture_en_echec_ne_casse_pas_api_me(monkeypatch):
    """Fail-open : un fournisseur tiers qui tousse ne doit pas faire tomber /api/me. On
    retombe sur l'absence d'entrée — l'état d'avant, donc une dégradation, pas une
    régression."""
    from oto_mcp import access

    def _boom(sub):
        raise RuntimeError("fournisseur indisponible")

    monkeypatch.setitem(connector_link._READERS, "atlassian", _boom)
    assert connector_link.state("atlassian", "sub-x") is None
    assert _entry_for(access, "atlassian") is None


def _entry_for(access, name: str):
    """Exécute la 4e boucle isolément (pas de DB) et rend l'entrée produite."""
    out = {"providers": {}}
    for c in providers.REGISTRY.values():
        if c.name in out["providers"] or c.secret_kind != "oauth":
            continue
        link = connector_link.state(c.name, "sub-x")
        if link is None:
            continue
        out["providers"][c.name] = {
            "mode": "user" if link.linked else "forbidden",
            "user_key_configured": link.linked,
            "session_set_at": link.set_at,
            "group_secret_configured": False,
            "org_secret_configured": False,
            "platform_key_label": None,
            "quota_used_today": 0,
            "quota_daily": None,
        }
    return out["providers"].get(name)


def test_la_boucle_du_test_est_bien_celle_du_code():
    """Garde-fou du garde-fou : le helper ci-dessus DUPLIQUE la 4e boucle (le chemin
    réel demande une DB). Si le code diverge, ce test ne prouve plus rien — on compare
    donc les clés produites à celles du source."""
    import inspect
    from oto_mcp import access
    src = inspect.getsource(access.status_for)
    bloc = src[src.index("secret_kind != \"oauth\""):]
    for cle in _CLES_ATTENDUES:
        assert f'"{cle}"' in bloc, f"la 4e boucle du code n'émet plus « {cle} »"
