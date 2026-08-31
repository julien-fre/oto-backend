"""Mount fédéré SANS auth — verrouille le chemin no-auth.

Un mount `kind="mount"` avec `auth_modes` VIDE (endpoint hébergé public) doit
forwarder SANS token per-user : ni `resolve_mount_token`, ni header
`Authorization`, ni exigence d'un sub courant. Contraste avec un mount byo_user
(atlassian) dont la factory lève hors requête. On exerce le vrai chemin (pas de
réseau : la Client n'ouvre la connexion qu'à l'entrée du context manager).

⚠️ Depuis le retrait du connecteur `justicelibre` (2026-08-21), AUCUN mount
no-auth n'est déclaré au registre : la branche est générique et sans consommateur
vivant. Le fixture est donc SYNTHÉTIQUE (un mount déclaré, dérivé sans auth) —
c'est bien la branche `not connector.auth_modes` de `tools/mount.py` qui est
exercée, pas un connecteur du catalogue.
"""
import asyncio
import dataclasses

import pytest

from oto_mcp import providers
from oto_mcp.tools import mount


def _mount(name):
    c = providers.REGISTRY.get(name)
    assert c is not None and c.kind == "mount", f"{name} doit être un mount déclaré"
    return c


def _noauth_mount():
    """Mount no-auth synthétique : un mount déclaré, privé de tout mode d'auth."""
    return dataclasses.replace(
        _mount("planity"),
        name="noauth_probe",
        namespaces=("noauth_probe",),
        auth_modes=frozenset(),
        keyed=False,
        secret_kind="none",
    )


def test_noauth_mount_shape():
    c = _noauth_mount()
    assert not c.auth_modes, "un mount no-auth a des auth_modes vides"
    assert c.kind == "mount" and c.mount_url


def test_noauth_factory_needs_no_token_no_sub():
    """La factory no-auth construit un Client SANS résoudre de token ni exiger un
    sub — même hors contexte de requête (là où la factory atlassian lèverait)."""
    called = {"resolve": False}

    def _boom(_name):  # ne doit JAMAIS être appelé pour un mount no-auth
        called["resolve"] = True
        raise AssertionError("resolve_mount_token appelé pour un mount no-auth")

    orig = mount.access.resolve_mount_token
    mount.access.resolve_mount_token = _boom
    try:
        factory = mount._make_factory(_noauth_mount())
        client = asyncio.run(factory())
    finally:
        mount.access.resolve_mount_token = orig

    assert client is not None
    assert called["resolve"] is False


def test_no_noauth_mount_in_registry():
    """Garde-fou : si un mount no-auth revient au registre, ce test tombe — c'est
    le signal d'ajouter sa couverture dédiée (et de mettre docs/federation.md à
    jour) plutôt que de s'appuyer sur le fixture synthétique ci-dessus."""
    noauth = [c.name for c in providers.REGISTRY.values()
              if c.kind == "mount" and not c.auth_modes]
    assert noauth == [], f"mount(s) no-auth déclaré(s) : {noauth}"


def test_byo_mount_factory_still_gates():
    """Contraste : un mount byo_user (atlassian) lève bien hors requête (aucun sub)."""
    from oto_mcp.mcp_errors import McpError
    factory = mount._make_factory(_mount("atlassian"))
    with pytest.raises(McpError):
        asyncio.run(factory())
