"""Lot L5 (blueprint ADR 0053) — la clé plateforme entre dans le modèle d'accès.

Ce que ces tests garantissent, dans l'ordre de ce qui coûterait cher :

1. **Le repli est identique.** Un bénéficiaire que la chaîne ne connaît pas passe par
   l'ancien chemin, au caractère près. C'est ce qui rend la fenêtre de double lecture
   déployable sans qu'une seule personne perde un accès le jour même.
2. **La révocation coupe.** Une arête archivée fait dire NON à la chaîne, qui ne
   retombe donc PAS sur l'ancien chemin — sans quoi révoquer serait un no-op sur une
   clé free-tier, et « l'accès se retire » serait faux. Aucun cache à invalider :
   la lecture suivante voit `revoked_at`.
3. **Les neuf autres ne bougent pas d'un octet.** Pas une branche, pas une requête —
   vérifié en rendant toute lecture de chaîne EXPLOSIVE et en constatant que leur
   résolution ne la touche jamais.
4. **Accorder n'enlève plus rien à personne.** L'écriture d'un grant ne touche plus la
   ligne du coffre : c'est l'incident du 31/07 rendu structurellement impossible, et
   c'est vérifié en rendant l'accès au coffre explosif sur le chemin d'écriture.

Convention du repo : logique pure et gardes par stub ici ; le SQL s'exerce contre un
vrai PostgreSQL dans `test_grants_l5_migration_live.py`.
"""
from __future__ import annotations

import pytest

from oto_mcp import access, credentials_store, grants_chain, providers
from oto_mcp.db import grants as db_grants

# L'instance fullenrich TELLE QU'ELLE EST EN PROD (relevé du 12/08/2026) : free-tier
# ouverte (`share_mode='open'`, `share_down=[]`), quota par défaut 5, deux grants
# nominatifs dans `rate_limit_by`. Les tests jouent contre CETTE forme, pas contre une
# forme idéale — c'est elle que la migration doit reproduire.
PROD_INSTANCE = {
    "label": "env", "share_mode": "open", "share_down": [], "share_side": [],
    "meta": {"rate_limit": 5, "rate_limit_by": {"user:granted-sub": 200,
                                                "user:other-sub": 5}},
}
REF = "platform:fullenrich:env"


def _edge(grantee_kind="user", grantee_id="granted-sub", quota=200, revoked=None,
          edge_id=1):
    return {"id": edge_id, "resource_kind": "connector_instance", "resource_id": REF,
            "grantor_kind": "platform", "grantor_id": "platform",
            "grantee_kind": grantee_kind, "grantee_id": grantee_id,
            "constraints": {"quota": quota} if quota else {}, "parent_id": None,
            "source": "manual", "created_by": "migration:l5",
            "created_at": "2026-08-12 00:00:00", "revoked_at": revoked}


@pytest.fixture
def vault(monkeypatch):
    """La ligne du coffre, sans base."""
    monkeypatch.setattr(credentials_store, "list_platform_instances",
                        lambda p: [dict(PROD_INSTANCE)] if p == "fullenrich" else [])


def _edges(monkeypatch, rows):
    monkeypatch.setattr(db_grants, "edges_for", lambda ref, grantees: list(rows))


# ── 1. Les trois états de la chaîne ────────────────────────────────────────────

def test_granted_beneficiary_resolves_exactly_as_before(vault, monkeypatch):
    """Arête vivante ⟹ la chaîne résout, avec LA MÊME clé et LE MÊME quota que
    l'ancien chemin. C'est la condition de la bascule : migrer ne change rien de ce
    que voit celui qui était déjà servi."""
    monkeypatch.setattr(access, "_platform_instance_usable", lambda *a: True)
    legacy = access._legacy_platform_grant_meta("granted-sub", "fullenrich", None)
    _edges(monkeypatch, [_edge()])
    chained = access._platform_grant_meta("granted-sub", "fullenrich", None)
    assert chained == legacy == {"label": "env", "daily_quota": 200}


def test_unknown_beneficiary_falls_back_identically(vault, monkeypatch):
    """Aucune arête n'a JAMAIS visé cet appelant ⟹ la chaîne est muette et l'ancien
    chemin répond seul. Le repli n'est pas « équivalent », il est le MÊME appel : on
    compare au résultat de `_legacy_platform_grant_meta` lui-même."""
    _edges(monkeypatch, [])
    assert (access._platform_grant_meta("nobody", "fullenrich", None)
            == access._legacy_platform_grant_meta("nobody", "fullenrich", None)
            == {"label": "env", "daily_quota": 5})


def test_revoked_edge_cuts_access_without_falling_back(vault, monkeypatch):
    """⚠️ LE test du lot. Des arêtes existent mais toutes révoquées ⟹ la chaîne dit
    NON, et ne retombe PAS sur l'ancien chemin. Sans cette règle, révoquer un grant
    sur une clé free-tier (ouverte à tous) ne couperait rien du tout.

    Et rien n'est à invalider : la révocation est vue à la lecture SUIVANTE — c'est
    l'argument du banc L0 contre le cache, ici sous forme de test."""
    assert access._legacy_platform_grant_meta("granted-sub", "fullenrich", None), (
        "prémisse du test : l'ancien chemin accorde (clé free-tier ouverte)")
    _edges(monkeypatch, [_edge(revoked="2026-08-12 10:00:00")])
    assert access._platform_grant_meta("granted-sub", "fullenrich", None) is None


def test_divergence_is_journalled_not_raised(vault, monkeypatch, caplog):
    """Un écart entre les deux voies ne dégrade jamais le service : il se journalise
    en WARN. C'est la matière du verdict de fin de fenêtre."""
    _edges(monkeypatch, [_edge(revoked="2026-08-12 10:00:00")])
    with caplog.at_level("WARNING"):
        access._platform_grant_meta("granted-sub", "fullenrich", None)
    assert "ÉCART d'accès" in caplog.text
    assert all(r.levelname == "WARNING" for r in caplog.records), caplog.text


def test_quota_divergence_is_journalled(vault, monkeypatch, caplog):
    """Les deux accordent mais pas le même plafond ⟹ la migration a mal reproduit
    `rate_limit_by`, ou un admin a écrit d'un seul côté. WARN, pas d'erreur."""
    _edges(monkeypatch, [_edge(quota=999)])
    with caplog.at_level("WARNING"):
        access._platform_grant_meta("granted-sub", "fullenrich", None)
    assert "ÉCART de quota" in caplog.text


# ── 2. Le quota : la règle vient de l'arête, le refus reste le même ────────────

def test_edge_quota_produces_the_same_refusal_as_before(vault, monkeypatch):
    """Quota épuisé ⟹ le MÊME refus qu'avant, mot pour mot : c'est le plafond porté
    par l'arête qui alimente le message historique, pas un nouveau chemin d'erreur."""
    from mcp.shared.exceptions import McpError

    monkeypatch.setattr(access, "require_connector_access", lambda *a, **k: None)
    monkeypatch.setattr(access.session_org, "current_call_instance", lambda: None)
    monkeypatch.setattr(access, "project_pinned_instance", lambda p, *a: None)
    monkeypatch.setattr(access, "current_org", lambda sub: None)
    monkeypatch.setattr(access, "current_group", lambda sub: None)
    monkeypatch.setattr(access.db, "get_usage_today", lambda sub, p: 7)
    monkeypatch.setattr(credentials_store, "get_credential", lambda *a, **k: "SECRET")
    _edges(monkeypatch, [_edge(quota=7)])
    with pytest.raises(McpError) as e:
        access._resolve_credential_impl("fullenrich", "auto", "granted-sub")
    assert "Quota plateforme fullenrich dépassé aujourd'hui (7/7)" in str(e.value)
    assert "la clé `env`" in str(e.value)


def test_user_edge_beats_org_edge(vault, monkeypatch):
    """Plusieurs voies vers la même instance ⟹ la plus FAVORABLE (0053-D5), et
    l'ordre de spécificité est celui de l'ancien chemin : `user:` prime sur l'org
    active — miroir exact d'`_platform_grantee_scope`."""
    _edges(monkeypatch, [_edge(quota=10, edge_id=2),
                         _edge("org", "42", quota=1000, edge_id=3)])
    assert access._platform_grant_meta("granted-sub", "fullenrich", 42)["daily_quota"] == 10


def test_org_scope_is_the_ACTIVE_org_never_membership():
    """Un grant d'org est métré per-contexte-d'org : un membre de X actif dans Y n'en
    profite pas. La migration doit reproduire ça, donc la liste des scopes aussi."""
    assert grants_chain.grantee_scopes("s", 7) == [("user", "s"), ("org", "7")]
    assert grants_chain.grantee_scopes("s", None) == [("user", "s")]


# ── 3. Le périmètre : qui est basculé, qui ne l'est pas ────────────────────────

# Les connecteurs à `platform_key_open` — fait de PRODUIT, inchangé par la vague 2 :
# basculer un connecteur sur la chaîne n'éteint pas son free-tier (celle décision-là
# se prend connecteur par connecteur, mesure de rayon en main — fullenrich seul l'a eue).
FREE_TIER_OTHERS = ("serper", "hunter", "reddit", "sirene", "kaspr",
                    "unipile", "apollo", "serpapi", "searchapi",
                    "tavily")  # 26/08 : socle recherche web, ouvert sans quota (GO Julien)

# Vague 2 (23/08) : chaînés SANS toucher leur flag — arêtes semées au boot, révocation
# vraie, metering d'arête ; un appelant sans arête retombe sur le chemin ouvert.
WAVE2 = ("serper", "hunter", "apollo", "serpapi", "kaspr", "reddit")

# Jamais chaînés, et pourquoi : unipile (mode plateforme gouverné par option comp +
# comptes opérés, pas par share_down), searchapi/sirene (pas de clé plateforme posée).
NEVER_CHAINED = ("unipile", "searchapi", "sirene", "tavily")


def test_unchained_connectors_never_touch_the_chain(monkeypatch):
    """Test DIFFÉRENTIEL : toute lecture de chaîne explose. Les connecteurs hors
    périmètre résolvent quand même — donc aucun d'eux ne passe par une ligne de ce
    lot."""
    def boom(*a, **k):  # pragma: no cover - doit ne jamais être appelé
        raise AssertionError("un connecteur non basculé a consulté la chaîne")

    monkeypatch.setattr(db_grants, "edges_for", boom)
    monkeypatch.setattr(credentials_store, "list_platform_instances",
                        lambda p: [{"label": "env", "share_mode": "open",
                                    "share_down": [], "share_side": [],
                                    "meta": {"rate_limit": 42}}])
    for provider in NEVER_CHAINED:
        assert access._platform_grant_meta("s", provider, None) == {
            "label": "env", "daily_quota": 42}, provider


def test_wave2_open_key_without_edges_resolves_like_legacy(monkeypatch):
    """Le rayon de la vague 2 est NUL pour qui n'a pas d'arête : un connecteur
    chaîné mais à clé OUVERTE rend, pour un appelant que la chaîne ne connaît pas,
    exactement ce que rendait l'ancien chemin (état MUET → repli identique)."""
    monkeypatch.setattr(db_grants, "edges_for", lambda ref, grantees: [])
    monkeypatch.setattr(credentials_store, "list_platform_instances",
                        lambda p: [{"label": "env", "share_mode": "open",
                                    "share_down": [], "share_side": [],
                                    "meta": {"rate_limit": 42}}])
    for provider in WAVE2:
        assert access._platform_grant_meta("s", provider, None) == \
            access._legacy_platform_grant_meta("s", provider, None), provider
        assert access._platform_grant_meta("s", provider, None) == {
            "label": "env", "daily_quota": 42}, provider


def test_wave2_revocation_cuts_access_even_on_open_key(monkeypatch):
    """Ce que la vague 2 CHANGE : une arête révoquée coupe l'accès plateforme d'un
    connecteur chaîné, même à clé ouverte — sans elle, révoquer un grant serait un
    no-op (le free-tier re-accorderait aussitôt)."""
    monkeypatch.setattr(credentials_store, "list_platform_instances",
                        lambda p: [{"label": "env", "share_mode": "open",
                                    "share_down": [], "share_side": [],
                                    "meta": {"rate_limit": 42}}])
    for provider in WAVE2:
        ref = f"platform:{provider}:env"
        monkeypatch.setattr(db_grants, "edges_for", lambda r, g, _ref=ref: [
            {"id": 1, "resource_kind": "connector_instance", "resource_id": _ref,
             "grantor_kind": "platform", "grantor_id": "platform",
             "grantee_kind": "user", "grantee_id": "s",
             "constraints": {}, "parent_id": None, "source": "manual",
             "created_by": "t", "created_at": "2026-08-23 00:00:00",
             "revoked_at": "2026-08-23 01:00:00"}])
        assert access._platform_grant_meta("s", provider, None) is None, provider


def test_only_fullenrich_lost_its_free_tier_flag():
    """`platform_key_open` s'éteint pour fullenrich seulement — vague 2 comprise :
    chaîner un connecteur n'éteint PAS son free-tier. Le flag survivant des neuf
    autres est un fait de produit — l'éteindre en passant serait couper un free-tier
    sans le décider (le rayon de fullenrich était mesuré nul, pas les leurs)."""
    assert providers.REGISTRY["fullenrich"].platform_key_open is False
    still_open = {n for n, c in providers.REGISTRY.items() if c.platform_key_open}
    assert still_open == set(FREE_TIER_OTHERS), sorted(still_open)


def test_the_lot_covers_pilot_plus_wave2():
    """« Un connecteur à quota d'abord, les 9 autres ensuite » : vague 2 du 23/08
    (GO Alexis). Élargir encore reste une décision, pas un ajout de nom."""
    assert grants_chain.CHAIN_CONNECTORS == frozenset({"fullenrich", *WAVE2})


# ── 4. L'écriture : accorder n'enlève plus rien ────────────────────────────────

def test_granting_never_touches_the_vault_row(monkeypatch):
    """L'incident du 31/07 : poser un grant individuel avait basculé la clé partagée
    `open`→`closed` avec une allowlist d'une personne, la fermant POUR TOUS. Ici,
    l'accès au coffre est rendu explosif sur le chemin d'écriture : accorder passe
    quand même. Il n'y a plus de geste qui, en accordant à l'un, retire à l'autre."""
    def boom(*a, **k):  # pragma: no cover
        raise AssertionError("le chemin de grant a touché la ligne du coffre")

    posed = []
    monkeypatch.setattr(credentials_store, "_connect", boom)
    monkeypatch.setattr(grants_chain, "grant",
                        lambda *a, **k: posed.append((a, k)))
    credentials_store.platform_grant("fullenrich", "user:x", daily_quota=200)
    assert posed == [(("fullenrich", "user:x"), {"daily_quota": 200, "label": None})]


def test_granting_a_non_migrated_connector_still_uses_the_old_write_path(monkeypatch):
    """Le pendant du test précédent : pour un connecteur HORS chaîne, l'écriture
    reste exactement celle d'avant (elle passe bien par le coffre)."""
    touched = []
    monkeypatch.setattr(credentials_store, "_connect",
                        lambda: (_ for _ in ()).throw(RuntimeError("coffre touché")))
    monkeypatch.setattr(grants_chain, "grant",
                        lambda *a, **k: touched.append(a))
    with pytest.raises(RuntimeError, match="coffre touché"):
        credentials_store.platform_grant("unipile", "user:x", daily_quota=200)
    assert touched == []


def test_revoking_archives_the_edge_for_a_migrated_connector(monkeypatch):
    revoked = []
    monkeypatch.setattr(credentials_store, "_connect",
                        lambda: (_ for _ in ()).throw(AssertionError("coffre touché")))
    monkeypatch.setattr(grants_chain, "revoke", lambda *a, **k: revoked.append(a))
    credentials_store.platform_revoke("fullenrich", "user:x")
    assert revoked == [("fullenrich", "user:x")]


def test_regrant_archives_the_previous_edge(monkeypatch):
    """Un grant re-posé REMPLACE le précédent (0053-D6, le remplacé est archivé).
    Sans ça, deux arêtes vivantes coexisteraient et la plus favorable gagnerait —
    donc BAISSER un quota n'aurait aucun effet."""
    calls = []
    monkeypatch.setattr(credentials_store, "list_platform_instances",
                        lambda p: [dict(PROD_INSTANCE)])
    monkeypatch.setattr(db_grants, "revoke_edges",
                        lambda *a: calls.append(("revoke",) + a) or 1)
    monkeypatch.setattr(db_grants, "insert_grant",
                        lambda **k: calls.append(("insert", k["constraints"])) or 9)
    grants_chain.grant("fullenrich", "user:x", daily_quota=50)
    assert calls == [("revoke", REF, "user", "x"), ("insert", {"quota": 50})]


# ── 5. La désignation de l'instance ────────────────────────────────────────────

def test_resource_id_is_the_canonical_instance_ref():
    """`grants.resource_id` n'invente pas une désignation : c'est le ref canonique
    d'`instance_refs` (domicile unique du format, ADR 0038 §B). Deux gains : un label
    contenant `:` (`zoho` porte `editor:eu` en prod) est percent-encodé au lieu de
    rendre le découpage ambigu, et le jour de L6 le ref `inst:{id}` sera accepté par
    le même parseur — pas de migration de forme."""
    from oto_mcp import instance_refs

    ref = grants_chain.instance_ref("editor:eu", "zoho")
    assert ref == instance_refs.make_platform_ref("zoho", "editor:eu")
    assert grants_chain.parse_instance_ref(ref) == ("editor:eu", "zoho")
    # Un ref d'un AUTRE niveau, ou malformé, n'est jamais pris pour une clé plateforme.
    assert grants_chain.parse_instance_ref("org:2:salesforce") is None
    assert grants_chain.parse_instance_ref("platform") is None


def test_quota_zero_means_unlimited_like_before():
    """Convention conservée de l'ancien chemin : 0 (ou absent) = illimité."""
    assert grants_chain.quota_of(_edge(quota=None)) is None
    assert grants_chain.quota_of({"constraints": {"quota": 0}}) == 0
    assert grants_chain.quota_of({"constraints": {}}) is None


# ── 6. L'empreinte de résolution : SOUS QUELLE CLÉ l'appel est passé ───────────

def _resolved_ref(monkeypatch, rung):
    """Fait résoudre un credential et rend ce que le relevé de l'appel a retenu."""
    from oto_mcp import session_org

    monkeypatch.setattr(access, "require_connector_access", lambda *a, **k: None)
    monkeypatch.setattr(access, "_resolve_credential_impl", lambda *a, **k: rung)
    holder: dict = {}
    token = session_org.set_call_trace(holder)
    try:
        access.resolve_credential("fullenrich", sub="s")
    finally:
        session_org.reset_call_trace(token)
    return holder.get("instance")


def test_the_trace_says_which_key_actually_served(monkeypatch):
    """Le journal disait quel outil et quelle org ; jamais SOUS QUELLE CLÉ. C'est
    pourtant la question d'une bascule d'accès — « l'appel est-il passé par l'arête
    ou par l'ancien chemin ? » — et celle de tout incident de credential."""
    from oto_mcp import instance_refs

    platform = access.ResolvedCredential(
        "fullenrich", "SECRET", True, "platform", "platform", "env")
    assert _resolved_ref(monkeypatch, platform) == "platform:fullenrich:env"

    member = access.ResolvedCredential(
        "fullenrich", "SECRET", False, "user", "member", "42:sub-x", account="a@b.c")
    assert _resolved_ref(monkeypatch, member) == instance_refs.make_member_ref(
        42, "sub-x", "fullenrich", "a@b.c")


def test_the_trace_never_breaks_a_resolution(monkeypatch):
    """Best-effort, et il faut que ce soit vrai : un relevé qui lève ferait échouer
    un appel qui a pourtant résolu sa clé."""
    monkeypatch.setattr(access.session_org, "note_call_trace",
                        lambda **k: (_ for _ in ()).throw(RuntimeError("journal HS")))
    weird = access.ResolvedCredential("fullenrich", "SECRET", False, "user",
                                      "member", "pas-un-id")
    monkeypatch.setattr(access, "require_connector_access", lambda *a, **k: None)
    monkeypatch.setattr(access, "_resolve_credential_impl", lambda *a, **k: weird)
    assert access.resolve_credential("fullenrich", sub="s") is weird


def test_instance_is_in_the_journal_allowlist():
    """Le relevé n'atteint la ligne de journal que si l'allowlist le laisse passer —
    une valeur consignée hors allowlist serait un travail invisible."""
    from oto_mcp import server

    assert "instance" in server._TRACED_ARGS


# ── 7. Le chemin chaud : un bulk ne se compte pas en N requêtes ────────────────

def test_a_bulk_debits_the_edge_once(monkeypatch):
    """`fullenrich` facture au contact et métrait donc en boucle : jusqu'à 100
    incréments par job. L'historique garde sa cadence (sa signature n'accepte pas de
    pas), mais la chaîne débite en UNE fois — sinon la fenêtre multiplierait par cinq
    le trafic DB d'un bulk, sur le chemin chaud d'un serveur mono-loop."""
    legacy, edge = [], []
    monkeypatch.setattr(access, "current_user_sub_from_token", lambda: "s")
    monkeypatch.setattr(access, "current_org", lambda sub: None)
    monkeypatch.setattr(access.db, "increment_usage",
                        lambda sub, p: legacy.append(p))
    monkeypatch.setattr(grants_chain, "record_usage",
                        lambda sub, p, org, calls: edge.append(calls))
    access.record_platform_usage("fullenrich", 100)
    assert len(legacy) == 100, "le compteur historique garde EXACTEMENT sa cadence"
    assert edge == [100], "l'arête est débitée une fois, du bon montant"


def test_a_non_migrated_connector_never_reaches_the_chain_counter(monkeypatch):
    monkeypatch.setattr(access, "current_user_sub_from_token", lambda: "s")
    monkeypatch.setattr(access.db, "increment_usage", lambda sub, p: None)
    monkeypatch.setattr(grants_chain, "record_usage",
                        lambda *a, **k: pytest.fail("connecteur non basculé compté"))
    access.record_platform_usage("unipile", 3)
