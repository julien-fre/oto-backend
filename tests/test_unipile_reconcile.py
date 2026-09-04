"""Réconciliation poll-and-bind Unipile (webhook hosted-auth v2 non livré).

Verrouille : on lie le compte le plus RÉCENT, NON déjà lié, du bon provider, créé
APRÈS le pending du sub (le floor évite de rebinder un siège pré-existant)."""
import types
from datetime import datetime, timezone

from oto_mcp import unipile_connect as uc

PEND_TS = datetime(2026, 7, 16, 12, 41, tzinfo=timezone.utc)


def _pend(nonce="N", org=2, provider="LINKEDIN", seat=True, ts=PEND_TS):
    return {"nonce": nonce, "org_id": org, "provider": provider,
            "platform_seat": seat, "created_at": ts}


def _acc(aid, name, provider="linkedin", created="2026-07-16 12:45:00+00"):
    return {"id": aid, "name": name, "provider": provider, "created_at": created}


def _setup(monkeypatch, pendings, accounts, bound=None, dead=None, alive_ids=None):
    monkeypatch.setattr(uc.db, "list_unipile_pending_for_sub", lambda s: pendings)
    monkeypatch.setattr(uc.db, "bound_unipile_account_ids", lambda: set(bound or []))
    # La garde partagée (#559) lit les lignes d'autrui en base ; ici tout est stubé —
    # sans ce stub le fichier ne passe que si un test voisin a laissé DATABASE_URL.
    monkeypatch.setattr(uc.db, "foreign_unipile_account_ids", lambda s: set())
    monkeypatch.setattr(uc.db, "dead_unipile_account_ids_for",
                        lambda s, p="LINKEDIN": set(dead or []))
    monkeypatch.setattr(uc.access, "resolve_credential",
                        lambda *a, **k: types.SimpleNamespace(key="K", is_platform=True, config={}))
    import oto.tools.unipile as core
    # account_alive : par défaut TOUS vivants ; `alive_ids` restreint à ceux-là
    alive = (lambda aid: aid in alive_ids) if alive_ids is not None else (lambda aid: True)
    monkeypatch.setattr(core, "make_unipile_client",
                        lambda **k: types.SimpleNamespace(
                            list_accounts=lambda: accounts, account_alive=alive))
    calls = {"set": [], "resolved": []}
    monkeypatch.setattr(uc.db, "set_unipile_account",
                        lambda *a, **k: calls["set"].append((a, k)))
    monkeypatch.setattr(uc.db, "resolve_unipile_pending",
                        lambda n: calls["resolved"].append(n))
    return calls


def test_binds_newest_after_pending(monkeypatch):
    accounts = [_acc("acc_old", "Seat", created="2026-07-16 11:00:00+00"),
                _acc("acc_new", "Me", created="2026-07-16 12:45:00+00")]
    calls = _setup(monkeypatch, [_pend()], accounts)
    out = uc.reconcile_pending("sub1")
    assert out["bound"] is True
    assert out["accounts"][0]["account_id"] == "acc_new"
    assert calls["set"][0][0][:2] == ("sub1", "acc_new")  # (sub, account_id)
    assert calls["resolved"] == ["N"]


def test_excludes_already_bound(monkeypatch):
    calls = _setup(monkeypatch, [_pend()], [_acc("acc_new", "Me")], bound={"acc_new"})
    out = uc.reconcile_pending("sub1")
    assert out["bound"] is False and calls["set"] == []


def test_skips_dead_session_prefers_alive(monkeypatch):
    # deux candidats après le pending : le plus récent est MORT (401) → on prend le vivant
    accounts = [_acc("acc_alive", "Sain", created="2026-07-16 12:45:00+00"),
                _acc("acc_dead", "MortNé", created="2026-07-16 12:50:00+00")]
    calls = _setup(monkeypatch, [_pend()], accounts, alive_ids={"acc_alive"})
    out = uc.reconcile_pending("sub1")
    assert out["accounts"][0]["account_id"] == "acc_alive"


def test_binds_nothing_when_all_dead(monkeypatch):
    accounts = [_acc("acc_dead", "MortNé", created="2026-07-16 12:50:00+00")]
    calls = _setup(monkeypatch, [_pend()], accounts, alive_ids=set())
    out = uc.reconcile_pending("sub1")
    assert out["bound"] is False and calls["set"] == []


def test_excludes_account_before_floor(monkeypatch):
    # seul compte dispo est ANTÉRIEUR au pending (>5 min) → jamais rebindé (siège tiers)
    calls = _setup(monkeypatch, [_pend()], [_acc("acc_old", "Seat", created="2026-07-16 11:00:00+00")])
    out = uc.reconcile_pending("sub1")
    assert out["bound"] is False


def test_rebinds_own_dead_account_despite_floor(monkeypatch):
    # reconnexion : Unipile RÉUTILISE le compte (antérieur au pending) — la ligne
    # soft-déconnectée du sub est la preuve de propriété → rebind déterministe,
    # même si l'account_id figure dans bound (les morts y sont, anti-tiers).
    calls = _setup(monkeypatch, [_pend()],
                   [_acc("acc_mine", "Moi", created="2026-07-16 11:00:00+00")],
                   bound={"acc_mine"}, dead={"acc_mine"})
    out = uc.reconcile_pending("sub1")
    assert out["bound"] is True
    assert out["accounts"][0]["account_id"] == "acc_mine"


def test_never_rebinds_dead_account_of_third_party(monkeypatch):
    # ligne morte d'un TIERS (dans bound, pas dans MES morts) + antérieure → intouchable
    calls = _setup(monkeypatch, [_pend()],
                   [_acc("acc_tiers", "Autre", created="2026-07-16 11:00:00+00")],
                   bound={"acc_tiers"}, dead=set())
    assert uc.reconcile_pending("sub1")["bound"] is False


def test_provider_mismatch_ignored(monkeypatch):
    calls = _setup(monkeypatch, [_pend(provider="LINKEDIN")],
                   [_acc("acc_wa", "WA", provider="whatsapp")])
    out = uc.reconcile_pending("sub1")
    assert out["bound"] is False


def test_no_pending_is_noop(monkeypatch):
    """No-op = rien de lié ET aucun appel au fournisseur.

    ⚠️ Ce test comparait la réponse ENTIÈRE à `{bound: False, accounts: []}`. Cette
    forme exacte était le MOYEN de vérifier le no-op, pas son objet — et elle a cassé
    dès que la réponse a gagné la raison du refus (#689). Réécrit sur ce que son nom
    annonce : le `set_unipile_account` non appelé et la liste des comptes jamais
    demandée sont ce qui fait qu'il ne se passe rien."""
    calls = _setup(monkeypatch, [], [])
    vus = []
    import oto.tools.unipile as core
    monkeypatch.setattr(core, "make_unipile_client",
                        lambda **k: vus.append("client") or types.SimpleNamespace(
                            list_accounts=lambda: [], account_alive=lambda a: True))
    out = uc.reconcile_pending("sub1")
    assert out["bound"] is False and out["accounts"] == []
    assert calls["set"] == [] and calls["resolved"] == []
    assert vus == [], "sans pending, le fournisseur ne doit même pas être contacté"


# ── #689 : une réconciliation qui ne lie rien DIT pourquoi ────────────────────
#
# Vécu le 03/09 : un utilisateur suit le parcours hosted-auth DEUX fois, la seconde
# jusqu'à la redirection finale, attend plusieurs minutes — et lit `connected:false`.
# Rien, nulle part, ne lui disait ce qui avait manqué. Les six sorties de cette
# fonction rendaient toutes le même `{bound: False}`.
#
# C'est la doctrine que ce module écrit en tête, pour `BindOutcome`, et que sa voisine
# immédiate n'appliquait pas : « un refus muet est un refus que personne ne saura
# avoir eu ».

def test_sans_pending_la_raison_est_dite(monkeypatch):
    _setup(monkeypatch, [], [])
    out = uc.reconcile_pending("u1")
    assert out["bound"] is False and out["reason"] == "no_pending"
    # Et le message dit le GESTE, pas seulement l'état.
    assert "op=connect" in out["detail"]


def test_aucun_candidat_nomme_les_trois_causes_possibles(monkeypatch):
    """LE cas du signalement : le parcours s'est terminé côté fournisseur et pourtant
    aucun compte n'est éligible. Trois causes indiscernables jusqu'ici — compte
    jamais créé, compte antérieur au pending, compte appartenant à un tiers."""
    _setup(monkeypatch, [_pend()], [_acc("A", "vieux", created="2026-07-01 09:00:00+00")])
    out = uc.reconcile_pending("u1")
    assert out["bound"] is False and out["reason"] == "no_candidate"
    assert "plus ancien que le pending" in out["detail"]
    assert "quelqu'un d'autre" in out["detail"]
    # Le compte-rendu porte AUSSI le nonce : deux demandes en attente ne se
    # confondent pas dans une seule phrase.
    assert out["pendings"][0]["nonce"] == "N"


def test_candidats_tous_morts_dit_quoi_refaire(monkeypatch):
    """Un wizard avorté produit un compte que le fournisseur n'authentifie plus.
    L'utilisateur doit apprendre qu'il faut refaire le parcours, pas attendre."""
    _setup(monkeypatch, [_pend()], [_acc("A", "mort")], alive_ids=set())
    out = uc.reconcile_pending("u1")
    assert out["reason"] == "candidates_dead"
    assert "redirection finale" in out["detail"]


def test_une_liaison_REUSSIE_ne_porte_aucune_raison(monkeypatch):
    """Pas d'écart, pas de bruit : le succès ne s'encombre pas d'un champ d'échec."""
    _setup(monkeypatch, [_pend()], [_acc("A", "bon")])
    monkeypatch.setattr(uc.db, "resolve_unipile_pending", lambda n: None)
    out = uc.reconcile_pending("u1")
    assert out["bound"] is True
    assert "reason" not in out and "detail" not in out
