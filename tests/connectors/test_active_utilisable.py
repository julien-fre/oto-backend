"""Une clé utilisable installe son connecteur (22/10/2026) — `connectors/usable.py`.

La règle : un connecteur EXPOSÉ que le membre n'a pas encore installé s'installe pour lui
quand une clé l'y sert — la sienne, celle de son équipe active, celle de l'org — ou qu'il
n'en demande aucune. Ce banc tient :

- la table de la règle (`installs`) et le plan (`plan`), purs ;
- l'écriture sur un vrai PostgreSQL : ce qu'elle installe, ce qu'elle laisse (pause,
  retrait, ligne du kit, clé de plateforme) ;
- la marque par (sub, org) : prise avant le calcul, jamais restampée après, effacée par
  une écriture du coffre qui sert le membre ;
- la poignée de main : l'interaction avec le kit, et un échec qui se dit en erreur.
"""
from __future__ import annotations

import os
from datetime import timedelta
from types import SimpleNamespace

import pytest

from oto_mcp.connectors import usable as U

def _con(secret_kind="api_key", namespaces=("x",), spends_keys_of=()):
    return SimpleNamespace(secret_kind=secret_kind, namespaces=namespaces,
                           spends_keys_of=spends_keys_of)


# ── la règle, connecteur par connecteur ────────────────────────────────────────

@pytest.mark.parametrize("mode", ["user", "group", "org"])
def test_la_cle_du_membre_de_son_equipe_ou_de_son_org_installe(mode):
    assert U.installs(_con(), {"mode": mode}) is True


@pytest.mark.parametrize("mode", ["platform", "over_quota", "tenant"])
def test_une_cle_de_plateforme_ou_de_tenant_n_installe_rien(mode):
    """Tranché sur oto-backend#1307 pour le semis : ce que sert la plateforme ou le
    tenant (quota épuisé compris) ne monte pas chez tout le monde."""
    assert U.installs(_con(), {"mode": mode}) is False


def test_une_cle_refusee_installe_quand_meme():
    """Elle est là pour être réparée : l'agent doit voir l'échec, pas perdre l'outil."""
    assert U.installs(_con(), {"mode": "org", "health_ko": True}) is True


def test_rien_ne_repond_n_installe_rien():
    assert U.installs(_con(), {"mode": "forbidden"}) is False


def test_l_open_data_installe_quoi_que_dise_l_instantane():
    assert U.installs(_con("none"), None) is True
    assert U.installs(_con("none"), {"mode": "forbidden"}) is True


def test_un_connecteur_sans_cle_qui_depense_celle_d_un_autre_n_installe_rien():
    """`web` ne demande aucune clé, mais son deuxième palier retombe d'office sur la clé
    serper — celle de la plateforme comprise, débitée et facturée au crédit
    (`tools/web.py::_serper_scrape`). Ce n'est pas gratuit : le membre l'installe."""
    from oto_mcp import providers
    assert U.installs(_con("none", spends_keys_of=("serper",)), None) is False
    web = providers.REGISTRY["web"]
    assert web.secret_kind == "none" and "serper" in web.spends_keys_of
    assert U.installs(web, None) is False
    assert U.installs(providers.REGISTRY["droit"], None) is True


def test_une_cascade_de_fournisseurs_n_installe_rien():
    """Les six waterfalls (`settings`) servent par les clés des fournisseurs de
    l'appelant : ce n'est pas de l'open data (oto-backend#1307)."""
    assert U.installs(_con("settings"), None) is False
    assert U.installs(_con("settings"), {"mode": "org"}) is False


def test_une_etape_en_attente_n_installe_rien():
    """Un canal hébergé dont le compte n'est pas lié : « il reste une étape »."""
    assert U.installs(_con(), {"mode": "org", "pending_action": "Connect"}) is False


def test_pas_de_ligne_dans_l_instantane_n_installe_rien():
    assert U.installs(_con(), None) is False
    assert U.installs(_con(), {"mode": "quelque_chose_de_neuf"}) is False


def test_un_connecteur_sans_outil_n_installe_rien():
    """Une clé de modèle (agents, jamais un outil) : sa sélection ne monte rien."""
    assert U.installs(_con(namespaces=()), {"mode": "org"}) is False
    assert U.installs(None, {"mode": "org"}) is False


# ── le plan ─────────────────────────────────────────────────────────────────────

def test_le_plan_n_installe_que_ce_qui_n_a_ni_ligne_ni_retrait():
    registry = {n: _con() for n in ("a", "b", "c", "d", "e", "f")}
    registry["f"] = _con("none")
    snapshot = {n: {"mode": "org"} for n in ("a", "b", "c", "d", "e")}
    snapshot["e"] = {"mode": "forbidden"}
    selection = {"b": "paused", "c": "active"}   # la pause du membre tient
    removed = {"d": "2026-10-22"}                 # son retrait aussi
    exposed = {"a", "b", "c", "d", "e", "f"}
    assert U.plan(snapshot, selection, removed, exposed, registry) == ["a", "f"]


def test_le_plan_ne_touche_pas_ce_que_l_org_n_expose_pas():
    registry = {"a": _con()}
    assert U.plan({"a": {"mode": "org"}}, {}, {}, set(), registry) == []


# ── sur un vrai PostgreSQL ──────────────────────────────────────────────────────

@pytest.fixture(scope="module")
def live(pg_module_dsn):
    pytest.importorskip("psycopg")
    avant = {k: os.environ.get(k) for k in ("DATABASE_URL", "OTO_MCP_MASTER_KEY")}
    os.environ["DATABASE_URL"] = pg_module_dsn
    os.environ["OTO_MCP_MASTER_KEY"] = "4" * 64   # le coffre chiffre : une clé de banc
    try:
        from oto_mcp.db import init_db
        from oto_mcp.connectors import activation
        init_db()
        for nom in ("hunter", "kaspr", "folk", "droit"):
            activation.set_activation(nom, True)
        yield
    finally:
        for k, v in avant.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def _org(nom: str, *membres: str) -> int:
    from oto_mcp import org_store
    oid = org_store.create_org(nom, created_by=membres[0])
    for m in membres:
        org_store.add_org_member(oid, m, "org_member")
    return oid


def _ligne(sub: str, org: int, connecteur: str):
    from oto_mcp import db
    with db._connect() as c:
        return c.execute(
            "SELECT state, origin FROM user_selected_connectors "
            "WHERE sub = %s AND org_id = %s AND connector = %s",
            (sub, org, connecteur)).fetchone()


def _instantane(monkeypatch, providers: dict):
    from oto_mcp import access
    monkeypatch.setattr(access, "status_for",
                        lambda sub, org=None, group=None: {"providers": providers})


def test_installe_et_laisse_le_reste_tel_quel(live, monkeypatch):
    from oto_mcp.connectors import selection as sel
    sub = "membre-reconcilie"
    org = _org("org-reconcilie", sub)
    sel.set_state(sub, "kaspr", sel.ACTIVE, org)       # actif, plus aucune clé : intouché
    sel.set_state(sub, "folk", sel.PAUSED, org)        # mis en pause, mais servi : intouché
    _instantane(monkeypatch, {
        "hunter": {"mode": "org"},
        "kaspr": {"mode": "forbidden"},
        "folk": {"mode": "org"},
    })

    installed = U.reconcile(sub, org, None)

    assert "hunter" in installed and "droit" in installed   # open data : rien à apporter
    assert "kaspr" not in installed and "folk" not in installed
    assert _ligne(sub, org, "hunter") == {"state": "active", "origin": U.ORIGIN}
    assert _ligne(sub, org, "kaspr")["state"] == "active"
    assert _ligne(sub, org, "folk")["state"] == "paused"


def test_un_retrait_du_membre_tient_contre_une_cle(live, monkeypatch):
    from oto_mcp.connectors import selection as sel
    sub = "membre-retrait"
    org = _org("org-retrait", sub)
    sel.set_state(sub, "hunter", sel.ACTIVE, org)
    sel.unselect(sub, "hunter", org)
    _instantane(monkeypatch, {"hunter": {"mode": "org"}})
    assert "hunter" not in U.reconcile(sub, org, None)
    assert _ligne(sub, org, "hunter") is None
    assert "hunter" in sel.list_removed(sub, org)


def test_un_retrait_fait_pendant_le_calcul_tient_aussi(live, monkeypatch):
    """L'écriture relit les retraits dans la même instruction : un retrait posé entre la
    lecture et l'écriture n'est pas défait."""
    from oto_mcp.connectors import selection as sel
    sub = "membre-retrait-course"
    org = _org("org-retrait-course", sub)
    sel.set_state(sub, "hunter", sel.ACTIVE, org)
    sel.unselect(sub, "hunter", org)
    _instantane(monkeypatch, {"hunter": {"mode": "org"}})
    monkeypatch.setattr(sel, "list_removed", lambda *a: {})   # lu avant le retrait
    assert "hunter" not in U.reconcile(sub, org, None)
    assert _ligne(sub, org, "hunter") is None


def test_une_cle_de_plateforme_n_installe_rien_en_base(live, monkeypatch):
    sub = "membre-plateforme"
    org = _org("org-plateforme", sub)
    _instantane(monkeypatch, {"hunter": {"mode": "platform"}, "kaspr": {"mode": "over_quota"}})
    installed = U.reconcile(sub, org, None)
    assert "hunter" not in installed and "kaspr" not in installed
    assert _ligne(sub, org, "hunter") is None


def test_la_marque_est_prise_avant_le_calcul_jamais_restampee(live, monkeypatch):
    """Une clé posée PENDANT le calcul efface la marque ; le calcul ne la repose pas :
    la session suivante voit la clé, au lieu d'attendre le TTL."""
    from oto_mcp import credentials_store
    sub = "membre-course"
    org = _org("org-course", sub)

    def _instantane_puis_cle(sub_, org=None, group=None):
        credentials_store.set_credential(credentials_store.MEMBER,
                                         credentials_store.member_id(org, sub_),
                                         "hunter", "k-test")
        return {"providers": {}}

    from oto_mcp import access
    monkeypatch.setattr(access, "status_for", _instantane_puis_cle)
    assert U.run_if_due(sub, org, None) is not None
    assert U.due(sub, org)


def test_la_marque_vieillit_et_une_cle_du_membre_l_efface(live, monkeypatch):
    from oto_mcp import credentials_store, db
    sub = "membre-marque"
    org = _org("org-marque", sub)
    _instantane(monkeypatch, {})
    assert U.run_if_due(sub, org, None) is not None
    assert not U.due(sub, org)

    with db._connect() as c:
        c.execute("UPDATE connector_selection_reconciled SET reconciled_at = NOW() - %s "
                  "WHERE sub = %s AND org_id = %s", (U.TTL + timedelta(minutes=1), sub, org))
    assert U.due(sub, org)

    assert U.run_if_due(sub, org, None) is not None
    assert not U.due(sub, org)
    credentials_store.set_credential(credentials_store.MEMBER,
                                     credentials_store.member_id(org, sub), "hunter", "k-test")
    assert U.due(sub, org)


def test_une_cle_d_org_efface_la_marque_de_tous_ses_membres(live, monkeypatch):
    from oto_mcp import credentials_store
    a, b = "membre-org-a", "membre-org-b"
    org = _org("org-cle-partagee", a, b)
    _instantane(monkeypatch, {})
    U.run_if_due(a, org, None)
    U.run_if_due(b, org, None)
    credentials_store.set_credential(credentials_store.ORG, str(org), "hunter", "k-org")
    assert U.due(a, org) and U.due(b, org)


def test_une_cle_d_equipe_n_efface_que_la_marque_de_ses_membres(live, monkeypatch):
    from oto_mcp import credentials_store, group_store
    dedans, dehors = "membre-equipe-dedans", "membre-equipe-dehors"
    org = _org("org-cle-equipe", dedans, dehors)
    gid = group_store.create_group(org, "equipe-cle", created_by=dedans)
    group_store.add_group_member(gid, dedans)
    _instantane(monkeypatch, {})
    U.run_if_due(dedans, org, None)
    U.run_if_due(dehors, org, None)
    credentials_store.set_credential("group", str(gid), "hunter", "k-equipe")
    assert U.due(dedans, org)
    assert not U.due(dehors, org)


def test_une_cle_de_plateforme_n_efface_aucune_marque(live, monkeypatch):
    """Elle n'installe rien : effacer toutes les marques ferait recalculer chaque
    membre pour rien."""
    from oto_mcp import credentials_store
    sub = "membre-cle-plateforme"
    org = _org("org-cle-plateforme", sub)
    _instantane(monkeypatch, {})
    U.run_if_due(sub, org, None)
    credentials_store.set_platform_key("cle-banc-usable", "hunter", "k-plateforme")
    assert not U.due(sub, org)


def test_un_coffre_inconnu_de_la_regle_leve():
    with pytest.raises(ValueError, match="inconnu|unknown"):
        U.invalidate(SimpleNamespace(execute=lambda *a: None), "quelque_chose", "x")


def test_une_rafale_ne_calcule_qu_une_fois(live, monkeypatch):
    """Premier déploiement, ou une clé posée : la flotte d'une même personne ouvre ses
    sessions ensemble — le tour est PRIS avant le calcul, un seul le gagne."""
    sub = "membre-rafale"
    org = _org("org-rafale", sub)
    assert U.claim(sub, org) is True
    assert U.claim(sub, org) is False
    assert not U.due(sub, org)


def test_un_echec_rend_le_tour(live, monkeypatch):
    from oto_mcp import access
    sub = "membre-echec"
    org = _org("org-echec", sub)

    def _boum(*a, **k):
        raise RuntimeError("instantané illisible")

    monkeypatch.setattr(access, "status_for", _boum)
    with pytest.raises(RuntimeError):
        U.run_if_due(sub, org, None)
    assert U.due(sub, org)                      # la session suivante réessaie
    _instantane(monkeypatch, {})
    assert U.run_if_due(sub, org, None) is not None
    assert U.run_if_due(sub, org, None) is None  # pas son tour


# ── à la poignée de main ────────────────────────────────────────────────────────

class _Ctx:
    def __init__(self, *noms):
        self.fastmcp = SimpleNamespace(list_tools=self._list_tools)
        self._noms = noms

    async def _list_tools(self, run_middleware=False):
        return [SimpleNamespace(name=n) for n in self._noms]


def test_un_echec_a_la_session_se_dit_en_erreur_et_la_session_tient(monkeypatch):
    """La boîte monte ce que la table porte ; l'échec ne se perd pas dans un warning :
    il part en ERREUR, trace comprise (`logger.exception`)."""
    import asyncio
    from oto_mcp import session_visibility as SV

    def _boum(*a, **k):
        raise RuntimeError("panne")

    erreurs = []
    # Sans base : la première moitié du calcul (toggles perso, rôle) lit PG et, sur
    # erreur, retombe sur « aucune org » — `run_if_due` n'est alors jamais joué. Le test
    # ne doit pas dépendre d'un `DATABASE_URL` laissé par la fixture `live` du module :
    # sous `--dist loadgroup` (CI), il tourne seul sur son worker, hors de ce groupe.
    monkeypatch.setattr(SV.db, "list_user_disabled_tools", lambda *a: [])
    monkeypatch.setattr(SV.db, "list_user_enabled_tools", lambda *a: [])
    monkeypatch.setattr(SV.access, "get_user_role", lambda *a: "member")
    monkeypatch.setattr(SV.access, "current_group", lambda *a: None)
    monkeypatch.setattr(SV.connector_usable, "run_if_due", _boum)
    monkeypatch.setattr(SV.connector_selection, "is_seeded", lambda *a: True)
    monkeypatch.setattr(SV.connector_selection, "list_selection",
                        lambda *a: {"hunter": "active"})
    monkeypatch.setattr(SV.logger, "exception",
                        lambda msg, *a, **k: erreurs.append(msg % a))

    caches = asyncio.run(SV.compute_hidden_tools(_Ctx("hunter_x", "kaspr_x"), "s", org=7))
    assert "kaspr_x" in caches
    assert any("selection reconcile failed" in m for m in erreurs), erreurs


def test_a_la_session_le_kit_sans_cle_reste_et_l_open_data_s_installe(live, monkeypatch):
    """L'interaction avec le kit, par la vraie poignée de main : le kit installe `kaspr`
    (aucune clé) — il reste tel quel ; `droit` (open data) s'installe sans geste."""
    import asyncio
    from oto_mcp import org_store, session_visibility as SV
    sub = "membre-kit"
    org = _org("org-kit", sub)
    org_store.set_org_default_connectors(org, ["kaspr"])
    _instantane(monkeypatch, {"kaspr": {"mode": "forbidden"}})

    caches = asyncio.run(SV.compute_hidden_tools(_Ctx("kaspr_x", "droit_x"), sub, org=org))
    assert _ligne(sub, org, "kaspr") == {"state": "active", "origin": "kit"}
    assert _ligne(sub, org, "droit") == {"state": "active", "origin": U.ORIGIN}
    assert "kaspr_x" not in caches and "droit_x" not in caches
