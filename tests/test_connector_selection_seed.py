"""Socle curé + backfill de transition (ADR 0050).

Logique pure + SQL simulé par un faux conn (convention du repo : le chemin PG
réel est prouvé au déploiement) : le seed d'un nouveau (sub, org) reçoit le
SOCLE `default_active`, le backfill one-shot reconstitue le VISIBLE d'avant
(exposé − ex-default_hidden) et ne rejoue jamais (sentinelle).
"""
from oto_mcp import connector_selection, providers

# Tripwire de curation : le socle est un choix PRODUIT explicite — décision du
# 25/08 (remplace le socle VIDE du 16/07) : tout connecteur SANS credential
# démarre installé. La liste est écrite en dur ICI alors que le registre la
# DÉRIVE, et c'est exprès : le tripwire n'a de valeur que s'il casse quand la
# dérivation change de résultat. Un connecteur open data ajouté demain fera
# échouer ce test — l'assumer ici est le geste attendu, pas un accident.
# `web` en est ABSENT à dessein : sans credential côté client, mais ses barreaux
# hauts brûlent serper + Browserbase (clés Oto) — cf. l'exclusion explicite dans
# providers.py.
_SOCLE: set[str] = {
    "culture", "droit", "foncier", "frenchtech", "gr",
    "infosec", "justicelibre", "osm", "sante", "urba",
}


def test_default_active_socle_is_the_curated_set():
    assert set(providers.DEFAULT_ACTIVE_CONNECTORS) == _SOCLE


def test_socle_guidance_is_injected():
    """Le socle n'est viable que si l'agent sait ce qu'il a ET ce qu'il n'a pas : le
    bloc A doit porter le mode d'emploi (installer via oto_connector op=select, pont
    oto_call) et dire que le départ se limite à l'open data — sinon l'agent conclut
    d'une toolbox courte que la capacité n'existe pas.

    Garde-fou de cohérence : la promesse « sans configuration » ne doit plus englober
    le free tier (serper/hunter), qui n'est PAS pré-installé depuis le 25/08."""
    from oto_mcp import instructions
    surface = instructions.render()
    assert "Le compte démarre sur l'open data" in surface
    assert "oto_connector(op='select'" in surface
    assert "Seules les capacités SANS credential (open data) sont installées d'office" in surface
    assert "Le compte démarre nu" not in surface


# ── backfill one-shot (faux conn : rejoue le contrat SQL sans PG) ──────────────

class _FakeConn:
    """Simule le `conn` psycopg (rows = dicts, comme _str_dict_row) pour le
    backfill : activation + pairs fournis, écritures capturées."""

    def __init__(self, *, sentinel_present=False, activation=(), pairs=()):
        self.sentinel_present = sentinel_present
        self.activation = [dict(r) for r in activation]
        self.pairs = [dict(r) for r in pairs]
        self.selected: list[tuple] = []      # (sub, org_id, connector)
        self.seeded: list[tuple] = []        # (sub, org_id)

    def execute(self, sql, params=()):
        self._last = (sql, params)
        if sql.startswith("SELECT 1 FROM connector_selection_seeded"):
            return _Cur(one={"?": 1} if self.sentinel_present else None)
        # Table unifiée `connector_availability` (chantier ACL, cadrage 10/07).
        if sql.startswith("SELECT scope_type, scope_id, connector, enabled"):
            return _Cur(all_=self.activation)
        if sql.startswith("SELECT sub, org_id FROM org_members"):
            return _Cur(all_=self.pairs)
        if sql.startswith("INSERT INTO user_selected_connectors"):
            self.selected.append(params[:3])
            return _Cur()
        if sql.startswith("INSERT INTO connector_selection_seeded"):
            self.seeded.append(params)
            return _Cur()
        raise AssertionError(f"SQL inattendu: {sql}")


class _Cur:
    def __init__(self, one=None, all_=None):
        self._one, self._all = one, all_ or []

    def fetchone(self):
        return self._one

    def fetchall(self):
        return self._all


def test_backfill_noop_when_sentinel_present():
    conn = _FakeConn(sentinel_present=True)
    connector_selection.backfill_preexisting(conn)
    assert conn.selected == [] and conn.seeded == []


def test_backfill_seeds_previously_visible_and_marks_sentinel():
    activation = [
        {"connector": "serper", "scope_type": "platform", "scope_id": "", "enabled": True},
        {"connector": "aiark", "scope_type": "platform", "scope_id": "", "enabled": True},
        {"connector": "attio", "scope_type": "platform", "scope_id": "", "enabled": True},   # ex-default_hidden
        {"connector": "zoho", "scope_type": "platform", "scope_id": "", "enabled": False},
        {"connector": "aiark", "scope_type": "org", "scope_id": "7", "enabled": False},      # override org 7
    ]
    pairs = [{"sub": "u1", "org_id": 7}, {"sub": "u2", "org_id": 0}]
    conn = _FakeConn(activation=activation, pairs=pairs)
    connector_selection.backfill_preexisting(conn)
    got = {(s, o): set() for s, o, _ in conn.selected}
    for s, o, name in conn.selected:
        got[(s, o)].add(name)
    # u1 (org 7) : aiark coupé par l'override, attio jamais visible (ex-hidden)
    assert got[("u1", 7)] == {"serper"}
    # u2 (perso/global) : l'exposé master moins l'ex-hidden
    assert got[("u2", 0)] == {"serper", "aiark"}
    # chaque pair marqué seedé + la sentinelle en dernier
    assert ("u1", 7) in conn.seeded and ("u2", 0) in conn.seeded
    assert conn.seeded[-1] == (connector_selection._BACKFILL_MARK,)


def test_backfill_hidden_set_is_the_frozen_history():
    # Fait HISTORIQUE figé au moment du retrait du flag — ne doit plus bouger
    # (le registre n'a plus de default_hidden ; cette liste appartient à la
    # migration, pas au produit).
    assert connector_selection._BACKFILL_HIDDEN == {
        "attio", "brevoauto", "pennylaneged", "resend", "scaleway", "http", "bridge"}


# ── backfill du socle « sans credential » (25/08) ──────────────────────────────

def _socle_conn(**kw):
    """Faux conn dont l'exposé plateforme couvre tout le socle + un intrus keyé."""
    act = [{"scope_type": "platform", "scope_id": 0, "connector": n, "enabled": True}
           for n in sorted(providers.DEFAULT_ACTIVE_CONNECTORS | {"hubspot"})]
    return _FakeConn(activation=act, **kw)


def test_socle_backfill_installs_the_socle_for_existing_pairs():
    """Le geste ATTENDU : un membre déjà seedé (donc hors du seed lazy) reçoit
    quand même le socle — c'est tout l'objet de la passe."""
    conn = _socle_conn(pairs=[{"sub": "u1", "org_id": 7}])
    connector_selection.backfill_no_credential_socle(conn)
    got = {c for (_s, _o, c) in conn.selected}
    assert got == set(providers.DEFAULT_ACTIVE_CONNECTORS)
    assert all(s == "u1" and o == 7 for (s, o, _c) in conn.selected)


def test_socle_backfill_never_installs_outside_the_socle():
    """L'exposé de l'org est plus large que le socle (hubspot y est) : la passe ne
    doit installer QUE le socle, jamais « tout ce qui est exposé » — la confusion
    exacte que faisait le backfill 0050, dont le contrat était l'inverse."""
    conn = _socle_conn(pairs=[{"sub": "u1", "org_id": 7}])
    connector_selection.backfill_no_credential_socle(conn)
    assert "hubspot" not in {c for (_s, _o, c) in conn.selected}


def test_socle_backfill_respects_the_org_exposure_ceiling():
    """Un connecteur du socle coupé au niveau ORG ne doit pas entrer par la bande."""
    act = [{"scope_type": "platform", "scope_id": 0, "connector": n, "enabled": True}
           for n in sorted(providers.DEFAULT_ACTIVE_CONNECTORS)]
    act.append({"scope_type": "org", "scope_id": 7, "connector": "osm", "enabled": False})
    conn = _FakeConn(activation=act, pairs=[{"sub": "u1", "org_id": 7}])
    connector_selection.backfill_no_credential_socle(conn)
    assert "osm" not in {c for (_s, _o, c) in conn.selected}
    assert "culture" in {c for (_s, _o, c) in conn.selected}


def test_socle_backfill_is_one_shot():
    """Sentinelle : sans elle, un membre qui désinstalle se le verrait réinstaller
    à CHAQUE boot — le backfill deviendrait une politique permanente."""
    conn = _socle_conn(sentinel_present=True, pairs=[{"sub": "u1", "org_id": 7}])
    connector_selection.backfill_no_credential_socle(conn)
    assert conn.selected == []


def test_socle_backfill_marks_its_own_sentinel():
    conn = _socle_conn(pairs=[{"sub": "u1", "org_id": 7}])
    connector_selection.backfill_no_credential_socle(conn)
    # `VALUES (%s, 0)` : l'org_id est en dur dans le SQL, un seul param lié.
    assert (connector_selection._SOCLE_MARK,) in conn.seeded


def test_socle_backfill_uses_a_distinct_sentinel_from_adr0050():
    """Deux passes indépendantes : partager la sentinelle ferait sauter la seconde
    sur toute base ayant déjà vu la première — c'est-à-dire la prod."""
    assert connector_selection._SOCLE_MARK != connector_selection._BACKFILL_MARK
