"""Denylist de tools par org/équipe (remplace l'ancienne baseline allowlist,
retirée 2026-07-03 commit 3951a57). Gardes de capacité testées par stub (pas de
DB) — même convention que test_group_connector_activation.py.
"""
from types import SimpleNamespace

import pytest

from oto_mcp.capabilities import tools_visibility as cap
from oto_mcp.capabilities._types import AuthzDenied


def _ctx():
    return SimpleNamespace(sub="admin")


@pytest.fixture(autouse=True)
def _org_existe(monkeypatch):
    """Défaut de tous les tests de ce fichier : l'org visée existe. Les trois verbes
    d'org la lisent (404 `unknown_org`, cf. plus bas) et aucun de ces tests-là ne
    porte sur ce cas — celui qui le teste repose son propre stub."""
    monkeypatch.setattr(cap.org_store, "get_org", lambda oid: {"id": oid})


# ── org ──────────────────────────────────────────────────────────────────────

def test_org_list_unknown_org_raises(monkeypatch):
    monkeypatch.setattr(cap.org_store, "get_org", lambda oid: None)
    with pytest.raises(AuthzDenied) as e:
        cap._org_list(_ctx(), cap.OrgHiddenToolsListInput(org_id=42))
    assert e.value.code == "unknown_org"


# --- même fait, même réponse sur les trois verbes du triplet (#293) -----------
#
# `org_list` disait 404 `unknown_org`, `org_hide`/`org_unhide` 403 : elles s'en
# remettaient à la règle d'autz, qui refuse faute de constater l'appartenance. Un
# client recevait donc deux réponses pour le même fait selon le verbe employé. Et
# comme `roles` rend org_admin de TOUTE org à un platform_admin (et que la table n'a
# pas de FK), masquer « pour l'org 999999 » écrivait une ligne orpheline en 200.

def _run(key: str, inp, *, sub: str = "admin"):
    """Rejoue la chaîne de l'adaptateur pour UNE capacité : autz déclarée, puis
    handler. C'est là que vit l'alignement — le code dépend des deux."""
    from oto_mcp.capabilities import registry
    from oto_mcp.capabilities._types import RawCtx

    c = next(x for x in registry.CAPABILITIES if x.key == key)
    return c.handler(c.authz(RawCtx(sub=sub), inp), inp)


_TRIPLET_ORG = (
    ("tools.org_list", lambda: cap.OrgHiddenToolsListInput(org_id=42)),
    ("tools.org_hide", lambda: cap.OrgHiddenToolSetInput(org_id=42, name="attio_record")),
    ("tools.org_unhide", lambda: cap.OrgHiddenToolSetInput(org_id=42, name="attio_record")),
)

_TRIPLET_GROUP = (
    ("tools.group_list", lambda: cap.GroupHiddenToolsListInput(group_id=7)),
    ("tools.group_hide", lambda: cap.GroupHiddenToolSetInput(group_id=7, name="attio_record")),
    ("tools.group_unhide", lambda: cap.GroupHiddenToolSetInput(group_id=7, name="attio_record")),
)


@pytest.mark.parametrize("key,build", _TRIPLET_ORG, ids=[k for k, _ in _TRIPLET_ORG])
def test_org_inconnue_est_un_404_sur_les_trois_verbes(monkeypatch, key, build):
    from oto_mcp.capabilities import _authz
    monkeypatch.setattr(_authz.roles, "is_org_member", lambda sub, oid: True)
    monkeypatch.setattr(_authz.roles, "is_org_admin", lambda sub, oid: True)
    monkeypatch.setattr(_authz.access, "get_user_role", lambda sub: "super_admin")
    monkeypatch.setattr(cap.org_store, "get_org", lambda oid: None)
    monkeypatch.setattr(cap.tool_registry, "boot_tool_names", lambda: ["attio_record"])
    ecrit = []
    monkeypatch.setattr(cap.db, "add_org_disabled_tool", lambda *a, **k: ecrit.append(a))
    monkeypatch.setattr(cap.db, "remove_org_disabled_tool", lambda *a, **k: ecrit.append(a))

    with pytest.raises(AuthzDenied) as e:
        _run(key, build())
    assert (e.value.status, e.value.code) == (404, "unknown_org")
    assert not ecrit, "aucune ligne ne doit être écrite pour une org qui n'existe pas"


@pytest.mark.parametrize("key,build", _TRIPLET_GROUP, ids=[k for k, _ in _TRIPLET_GROUP])
def test_equipe_inconnue_est_refusee_sur_les_trois_verbes(monkeypatch, key, build):
    """Au palier équipe l'alignement est porté un cran plus haut, par les règles
    d'autz elles-mêmes — d'où le handler sans garde d'existence.

    ⚠️ Le CODE a changé le 12/08 (#300) : c'était 404 `unknown_group`, c'est
    désormais 403 — l'autorisation est testée avant l'existence, donc un non-membre
    n'apprend plus si l'équipe #N existe. Ce que ce test garde est inchangé : une
    équipe inconnue ne passe pas, et le handler n'a pas besoin de sa propre garde.
    """
    from oto_mcp.capabilities import _authz
    # Stubber `roles` et pas seulement `_authz.group_store` : depuis l'inversion, ce
    # sont les fonctions de rôle qui interrogent la base EN PREMIER — un stub posé
    # sur le seul module d'autz laissait le test ouvrir une vraie connexion.
    monkeypatch.setattr(_authz.roles, "can_read_group", lambda sub, gid: False)
    monkeypatch.setattr(_authz.roles, "can_admin_group", lambda sub, gid: False)
    monkeypatch.setattr(_authz.group_store, "get_group", lambda gid: None)

    with pytest.raises(AuthzDenied) as e:
        _run(key, build())
    assert (e.value.status, e.value.code) == (403, "forbidden")


def test_org_list_returns_stored_names(monkeypatch):
    monkeypatch.setattr(cap.org_store, "get_org", lambda oid: {"id": oid})
    monkeypatch.setattr(cap.db, "list_org_disabled_tools", lambda oid: ["attio_record"])
    out = cap._org_list(_ctx(), cap.OrgHiddenToolsListInput(org_id=42))
    assert out == {"org_id": 42, "disabled_tools": ["attio_record"]}


def test_org_hide_rejects_unknown_tool(monkeypatch):
    monkeypatch.setattr(cap.tool_registry, "boot_tool_names", lambda: ["attio_record"])
    with pytest.raises(AuthzDenied) as e:
        cap._org_hide(_ctx(), cap.OrgHiddenToolSetInput(org_id=42, name="not_a_real_tool"))
    assert e.value.code == "unknown_tool"


def test_org_hide_stores_with_setter(monkeypatch):
    monkeypatch.setattr(cap.tool_registry, "boot_tool_names", lambda: ["attio_record"])
    calls = []
    monkeypatch.setattr(cap.db, "add_org_disabled_tool",
                        lambda org_id, name, disabled_by=None: calls.append((org_id, name, disabled_by)))
    out = cap._org_hide(_ctx(), cap.OrgHiddenToolSetInput(org_id=42, name="attio_record"))
    assert out == {"org_id": 42, "tool": "attio_record", "hidden": True}
    assert calls == [(42, "attio_record", "admin")]


def test_org_unhide_removes(monkeypatch):
    calls = []
    monkeypatch.setattr(cap.db, "remove_org_disabled_tool",
                        lambda org_id, name: calls.append((org_id, name)))
    out = cap._org_unhide(_ctx(), cap.OrgHiddenToolSetInput(org_id=42, name="attio_record"))
    assert out == {"org_id": 42, "tool": "attio_record", "hidden": False}
    assert calls == [(42, "attio_record")]


# ── équipe ───────────────────────────────────────────────────────────────────

# (« équipe inconnue » n'est plus testée sur le handler : la garde vit dans les règles
# `GROUP_*_OF`, qui la portent pour les trois verbes — cf.
# `test_equipe_inconnue_est_un_404_sur_les_trois_verbes`.)


def test_group_hide_rejects_unknown_tool(monkeypatch):
    monkeypatch.setattr(cap.tool_registry, "boot_tool_names", lambda: ["attio_record"])
    with pytest.raises(AuthzDenied) as e:
        cap._group_hide(_ctx(), cap.GroupHiddenToolSetInput(group_id=7, name="not_a_real_tool"))
    assert e.value.code == "unknown_tool"


def test_group_hide_stores_with_setter(monkeypatch):
    monkeypatch.setattr(cap.tool_registry, "boot_tool_names", lambda: ["attio_record"])
    calls = []
    monkeypatch.setattr(cap.db, "add_group_disabled_tool",
                        lambda group_id, name, disabled_by=None: calls.append((group_id, name, disabled_by)))
    out = cap._group_hide(_ctx(), cap.GroupHiddenToolSetInput(group_id=7, name="attio_record"))
    assert out == {"group_id": 7, "tool": "attio_record", "hidden": True}
    assert calls == [(7, "attio_record", "admin")]


def test_group_unhide_removes(monkeypatch):
    calls = []
    monkeypatch.setattr(cap.db, "remove_group_disabled_tool",
                        lambda group_id, name: calls.append((group_id, name)))
    out = cap._group_unhide(_ctx(), cap.GroupHiddenToolSetInput(group_id=7, name="attio_record"))
    assert out == {"group_id": 7, "tool": "attio_record", "hidden": False}
    assert calls == [(7, "attio_record")]


# --- démasquer accepte n'importe quel nom : échappatoire ASSUMÉE (#293) -------
#
# Masquer valide (404 inconnu, 400 protégé), démasquer non. C'est ce qui permet de
# nettoyer une ligne dont le tool a été renommé ou retiré — rien d'autre ne le fait.
# Une purge automatique n'a pas de référentiel fiable : `boot_tool_names()` ne liste
# que ce qui a été MONTÉ (un module dont une dép manque est désactivé en silence, et
# le registre non réchauffé rend `[]`), donc elle effacerait de la gouvernance vivante
# au premier import raté. Ces deux tests figent la porte ouverte ET sa mention dans le
# contrat publié : la refermer sans écrire la purge doit les casser.

def test_demasquer_accepte_un_nom_que_masquer_refuserait(monkeypatch):
    monkeypatch.setattr(cap.tool_registry, "boot_tool_names", lambda: ["attio_record"])
    retires = []
    monkeypatch.setattr(cap.db, "remove_org_disabled_tool",
                        lambda oid, name: retires.append((oid, name)))
    monkeypatch.setattr(cap.db, "remove_group_disabled_tool",
                        lambda gid, name: retires.append((gid, name)))

    # `tool_disparu` : inconnu du registre — le cas du tool renommé/retiré.
    # `oto_whoami` : protégé — masquer le refuse, démasquer doit rester possible.
    assert cap._org_unhide(
        _ctx(), cap.OrgHiddenToolSetInput(org_id=42, name="tool_disparu"))["hidden"] is False
    assert cap._group_unhide(
        _ctx(), cap.GroupHiddenToolSetInput(group_id=7, name="oto_whoami"))["hidden"] is False
    assert retires == [(42, "tool_disparu"), (7, "oto_whoami")]


def test_le_contrat_publie_annonce_lechappatoire():
    """Un effet de bord non écrit est une dette ; écrit dans la `description=`, c'est
    une décision que l'intégrateur lit dans `/api/openapi.json` et le schéma MCP."""
    from oto_mcp.capabilities import registry

    for key in ("tools.org_unhide", "tools.group_unhide"):
        d = next(c for c in registry.CAPABILITIES if c.key == key).description or ""
        assert "the hide side would refuse" in d and "stale row" in d, (
            f"{key} ne documente plus qu'il accepte un nom inconnu : soit tu le "
            "redis, soit tu valides des deux côtés ET tu écris la purge.")


# --- outils protégés : refus à l'ÉCRITURE, pas seulement à la lecture ---------
#
# `is_tool_visible` ignore déjà le denylist sur un tool protégé — donc sans refus
# ici, l'admin recevrait `hidden: true` sur un masquage qui ne masque rien. Les
# deux autres faces du geste refusent (`oto_disable_tool`, `POST /api/me/tools/
# {name}` → 400 protected_tool) ; ces deux tests figent l'alignement.

def test_org_hide_refuses_protected_tool(monkeypatch):
    monkeypatch.setattr(cap.tool_registry, "boot_tool_names", lambda: ["oto_whoami"])
    stored = []
    monkeypatch.setattr(cap.db, "add_org_disabled_tool",
                        lambda *a, **k: stored.append(a))

    with pytest.raises(AuthzDenied) as e:
        cap._org_hide(_ctx(), cap.OrgHiddenToolSetInput(org_id=2, name="oto_whoami"))

    assert e.value.code == "protected_tool"
    assert not stored, "aucune ligne ne doit être écrite pour un tool protégé"


def test_group_hide_refuses_protected_tool(monkeypatch):
    monkeypatch.setattr(cap.tool_registry, "boot_tool_names", lambda: ["oto_call"])
    stored = []
    monkeypatch.setattr(cap.db, "add_group_disabled_tool",
                        lambda *a, **k: stored.append(a))

    with pytest.raises(AuthzDenied) as e:
        cap._group_hide(_ctx(), cap.GroupHiddenToolSetInput(group_id=7, name="oto_call"))

    assert e.value.code == "protected_tool"
    assert not stored
