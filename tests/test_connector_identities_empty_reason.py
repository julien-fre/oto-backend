"""Une liste d'identités VIDE dit pourquoi (signal #504, org 244, 14/08/2026).

Ce que le signal affirmait : « `oto_identity(op=list, connector=unipile)` renvoie
`identities:[]` alors que le compte LinkedIn est connecté et opérationnel ».

Ce que la prod montre (vérifié le 28/08/2026) : les trois lectures `oto_identity`
de ce compte datent du 14/08 à **14:00:44, 14:00:55 et 14:02:09** — le compte, lui,
a été lié à **14:03:30**. Rejouée aujourd'hui sur le même sub, la liste rend bien le
compte (`acc_01m008…`, « Rachel Hourlier »). Elle était vide parce qu'il n'y avait
rien à lister, et le listing des comptes hébergés existait déjà depuis le 08/07
(feedback #132, commit 5b24a72).

Le défaut RÉEL n'est donc pas le contenu de la liste, c'est son SILENCE : `[]` ne
disait pas s'il n'y avait aucun compte, aucune clé, ou une clé qui ne voit rien —
et c'est ce silence qui a fait conclure au bug pendant quatre jours.

Même famille que #476, et donc MÊME seam (`connectors/readiness.py`) : une surface
qui n'énonce pas ce qu'elle sait laisse l'appelant inventer la cause, et il invente
la mauvaise. Une seconde formulation du verdict rouvrirait la divergence.
"""
import asyncio

from oto_mcp import access, providers, status_hints
from oto_mcp.capabilities.connectors import identities as CI
from oto_mcp.capabilities._types import ResolvedCtx


def _list(monkeypatch, *, identities, mode="platform", pending=None, option_ok=True,
          broken=False):
    """Seams de domaine stubés — aucun accès DB (convention du repo)."""
    monkeypatch.setattr(CI.connector_identities, "list_identities",
                        lambda sub, name, scope="member": list(identities))
    monkeypatch.setattr(CI.connector_identities, "supports", lambda name: True)
    monkeypatch.setattr(access, "credential_mode_for",
                        lambda sub, name, org=None, group=None, probe=None: mode)
    monkeypatch.setattr(access, "option_open", lambda sub, name, org=None: option_ok)
    def _opt(name):
        if broken:
            raise RuntimeError("cascade indisponible")
        return "unipile" if name == "unipile" else None
    monkeypatch.setattr(access, "paid_option_for", _opt)
    monkeypatch.setattr(access, "current_group", lambda sub: None)
    monkeypatch.setattr(access, "account_noun", lambda name: "compte")
    monkeypatch.setattr(status_hints, "pending_action",
                        lambda name, sub, org, group, entry: pending)
    assert "unipile" in providers.REGISTRY      # le slug doit rester connu du registre
    return asyncio.run(CI._list(ResolvedCtx(sub="u1", org_id=244),
                                CI.IdentitiesInput(connector="unipile")))


def test_liste_pleine_nexplique_rien(monkeypatch):
    """Pas de bruit quand il n'y a rien à expliquer."""
    out = _list(monkeypatch, identities=[{"id": "acc_1", "is_default": True}])
    assert out["identities"] and "reason" not in out and "next_step" not in out


def test_vide_sans_cle_le_dit(monkeypatch):
    """Couche 2 absente : aucune clé ne résout. Ce n'est PAS « aucun compte »."""
    out = _list(monkeypatch, identities=[], mode="forbidden")
    assert out["identities"] == []
    assert out["reason"] == "no_credential"
    assert out["next_step"]


def test_vide_option_fermee_nomme_la_couche_3(monkeypatch):
    """L'option gatée fermée vide la liste elle aussi — et ce n'est encore pas
    « aucun compte » : la reformuler ainsi enverrait connecter un canal qu'on n'a
    pas le droit de connecter."""
    out = _list(monkeypatch, identities=[], option_ok=False)
    assert out["reason"] == "paid_option_off" and out["next_step"]


def test_vide_avec_cle_mais_aucun_compte_lie_le_dit(monkeypatch):
    """Le cas exact de #504 : la clé résout, il n'y a simplement encore aucun
    compte connecté. L'étape manquante vient du seam générique `status_hints`,
    déjà déclaré par le module du connecteur — relayée telle quelle, jamais
    reformulée (deux surfaces qui reformulent racontent deux histoires)."""
    out = _list(monkeypatch, identities=[], pending="Connecte un canal")
    assert out["identities"] == []
    assert out["reason"] == "no_identity_connected"
    assert out["next_step"] == "Connecte un canal"


def test_vide_sans_etape_declaree_dit_quand_meme_letat(monkeypatch):
    """Un connecteur sans hook `status_hints` ne doit pas retomber dans le silence :
    la clé résout, rien n'est connecté, et ça se dit."""
    out = _list(monkeypatch, identities=[], pending=None)
    assert out["reason"] == "no_identity_connected" and out["next_step"]


def test_diagnostic_illisible_se_dit_au_lieu_de_se_taire(monkeypatch):
    """Fail-VISIBLE, pas fail-open : si les couches ne se lisent pas, on rend
    `unknown` — retomber sur un `[]` muet serait rejouer le défaut qu'on répare."""
    out = _list(monkeypatch, identities=[], broken=True)
    assert out["reason"] == "unknown" and out["next_step"]
