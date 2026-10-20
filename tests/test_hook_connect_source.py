"""BRANCHER une source sur un agent webhook — `oto_trigger op=connect_source`.

Ce que ce fichier tient :

1. **Le porteur ne sort jamais** : ni dans la réponse, ni dans un journal — il ne
   va qu'à la source, comme `secret` de son webhook.
2. **Une inscription refusée ne change rien** : celles déjà faites sont retirées,
   la base n'est pas écrite, l'agent garde sa porte.
3. **Rebrancher = la rotation** : porteur neuf, inscriptions neuves, les anciennes
   retirées chez la source.
4. **Un agent branché garde sa porte** : `rotate_secret`, `rotate_address` et le
   changement de mode sont refusés (`source_connected`).
5. **Débrancher et supprimer retirent chez la source**, et rendent ce qui résiste.
"""
from __future__ import annotations

import asyncio
import logging

import pytest

from oto_mcp import runner_hook
from oto_mcp.capabilities import _source_du_hook as S
from oto_mcp.capabilities import runner_triggers as RT
from oto_mcp.capabilities._types import AuthzDenied, ResolvedCtx

ORG = 77


class _Lemlist:
    """Le client de la source : enregistre ce qu'on lui demande."""

    def __init__(self):
        self.ajouts, self.retraits = [], []
        self.refuse_ajout = self.refuse_retrait = None
        self.sans_id = False

    def add_webhook(self, url, *, type=None, secret=None, campaign_id=None,
                    is_first=None, zap_id=None):
        if self.refuse_ajout == type:
            raise RuntimeError("upstream 400")
        self.ajouts.append({"url": url, "type": type, "secret": secret,
                            "campaign_id": campaign_id, "is_first": is_first})
        return {} if self.sans_id else {"_id": f"hoo_{len(self.ajouts)}"}

    def delete_webhook(self, hook_id):
        if self.refuse_retrait == hook_id:
            raise RuntimeError("upstream 500")
        self.retraits.append(hook_id)
        return {}


@pytest.fixture
def base(monkeypatch):
    monkeypatch.setenv("OTO_MCP_PUBLIC_URL", "https://mcp.example")
    monkeypatch.setattr(RT.db, "comptage_livraisons",
                        lambda t, o: {"recues_24h": 0, "refusees_24h": 0,
                                      "derniere": None})
    monkeypatch.setattr(RT.db, "file_du_declencheur",
                        lambda t, o: {"pending": 0, "held": 0})
    monkeypatch.setattr(RT.db, "comptage_perime",
                        lambda o, t: {"expired_count": 0, "expired_since": None,
                                      "expired_last": None})
    ligne = {"id": 5, "org_id": ORG, "sub": "alexis", "kind": "webhook",
             "procedure": "veille", "tools": ["a"], "enabled": True,
             "payload_mode": "inline", "payload_fields": None,
             "model": "claude-sonnet-5", "hook_auth": "bearer",
             "signing_secret_set": False, "hook_slug": "h_privee", "max_per_day": 20,
             "hook_sources": None}
    etat = {"ligne": ligne, "hash": "h0", "client": _Lemlist(), "supprime": False}

    def _brancher(tid, org, *, hook_auth, secret_hash, sources):
        ligne.update(hook_auth=hook_auth, hook_sources=sources,
                     signing_secret_set=False)
        etat["hash"] = secret_hash
        return True

    monkeypatch.setattr(RT.db, "get_trigger", lambda t, o: dict(ligne))
    monkeypatch.setattr(RT.db, "brancher_la_source", _brancher)
    monkeypatch.setattr(RT.db, "poser_sources_de_hook",
                        lambda t, o, s: ligne.update(hook_sources=s or None) or True)
    monkeypatch.setattr(RT.db, "poser_adresse_de_hook",
                        lambda t, o, slug: ligne.update(hook_slug=slug) or True)
    monkeypatch.setattr(RT.db, "poser_secret_de_hook",
                        lambda t, o, h: etat.update(hash=h) or True)
    monkeypatch.setattr(RT.db, "poser_auth_de_hook",
                        lambda t, o, m, secret_enc=None, effacer_le_secret=False:
                        ligne.update(hook_auth=m) or True)
    monkeypatch.setattr(RT.db, "delete_trigger",
                        lambda t, o: etat.update(supprime=True) or True)
    monkeypatch.setattr(S, "_client", lambda ctx, source: etat["client"])
    return etat


def _ctx(sub="alexis"):
    return ResolvedCtx(sub=sub, org_id=ORG)


def _appel(**kw):
    return asyncio.run(RT._triggers(_ctx(), RT.TriggerInput(trigger_id=5, **kw)))


def _brancher(**kw):
    kw.setdefault("events", ["emailsReplied", "linkedinReplied"])
    return _appel(op="connect_source", source="lemlist", **kw)


# ── 1. le porteur ne sort jamais ──────────────────────────────────────────────

def test_brancher_inscrit_chez_la_source_avec_le_porteur_de_l_agent(base):
    out = _brancher(campaign_id="cam_1")
    c = base["client"]
    assert [a["type"] for a in c.ajouts] == ["emailsReplied", "linkedinReplied"]
    porteur = c.ajouts[0]["secret"]
    assert porteur.startswith(runner_hook.HOOK_SECRET_PREFIX)
    assert base["hash"] == runner_hook.hacher(porteur), "la source détient LE porteur"
    assert {a["secret"] for a in c.ajouts} == {porteur}
    assert c.ajouts[0]["url"] == "https://mcp.example/api/hooks/h_privee"
    assert c.ajouts[0]["campaign_id"] == "cam_1"
    assert base["ligne"]["hook_auth"] == runner_hook.BODY_SECRET
    assert [x["remote_id"] for x in out["connected"]] == ["hoo_1", "hoo_2"]
    assert out["trigger"]["hook_sources"] == base["ligne"]["hook_sources"]


def test_le_porteur_n_est_NI_rendu_NI_journalise(base, caplog):
    caplog.set_level(logging.DEBUG)
    out = _brancher()
    porteur = base["client"].ajouts[0]["secret"]
    assert porteur not in repr(out) and not out.get("hook_secret")
    assert porteur not in caplog.text


def test_un_agent_SANS_adresse_privee_en_recoit_une_avant_l_inscription(base):
    base["ligne"]["hook_slug"] = None
    _brancher()
    slug = base["ligne"]["hook_slug"]
    assert slug.startswith(runner_hook.ADRESSE_PREFIX)
    assert base["client"].ajouts[0]["url"].endswith("/api/hooks/" + slug)


@pytest.mark.parametrize("kw, code", [
    ({"events": []}, "events_required"),
    ({"events": ["  "]}, "events_required"),
    ({"events": [f"e{i}" for i in range(11)]}, "too_many_events"),
])
def test_les_evenements_sont_EXIGES_et_bornes(base, kw, code):
    with pytest.raises(AuthzDenied) as e:
        _brancher(**kw)
    assert e.value.code == code and not base["client"].ajouts


def test_sans_source_le_branchement_est_refuse(base):
    with pytest.raises(AuthzDenied) as e:
        _appel(op="connect_source", events=["emailsReplied"])
    assert e.value.code == "unknown_source"


def test_un_agent_PROGRAMME_ne_se_branche_pas(base):
    base["ligne"]["kind"] = "schedule"
    with pytest.raises(AuthzDenied) as e:
        _brancher()
    assert e.value.code == "trigger_not_found"


def test_brancher_l_agent_d_un_AUTRE_est_refuse(base, monkeypatch):
    monkeypatch.setattr(RT.roles, "is_org_admin", lambda s, o: False)
    monkeypatch.setattr(RT._acces_agent, "niveau", lambda s, o, a: "editor")
    with pytest.raises(AuthzDenied) as e:
        asyncio.run(RT._triggers(_ctx("bob"), RT.TriggerInput(
            op="connect_source", trigger_id=5, source="lemlist",
            events=["emailsReplied"])))
    assert e.value.code == "trigger_owner_or_admin_required"


# ── 2. une inscription refusée ne change rien ─────────────────────────────────

def test_une_inscription_REFUSEE_defait_les_autres_et_n_ecrit_rien(base):
    base["client"].refuse_ajout = "linkedinReplied"
    with pytest.raises(AuthzDenied) as e:
        _brancher()
    assert e.value.code == "source_registration_failed"
    assert base["client"].retraits == ["hoo_1"], "la première est retirée"
    assert base["ligne"]["hook_auth"] == "bearer" and base["hash"] == "h0"
    assert not base["ligne"]["hook_sources"]
    assert "upstream 400" not in e.value.message, "jamais le message brut du tiers"


def test_une_inscription_SANS_identifiant_est_refusee(base):
    base["client"].sans_id = True
    with pytest.raises(AuthzDenied) as e:
        _brancher()
    assert e.value.code == "source_registration_failed"
    assert base["hash"] == "h0"


# ── 3. rebrancher = la rotation ───────────────────────────────────────────────

def test_REBRANCHER_renouvelle_le_porteur_et_retire_les_anciennes(base):
    _brancher()
    premier = base["hash"]
    out = _brancher(events=["emailsReplied"])
    assert base["hash"] != premier
    assert base["client"].retraits == ["hoo_1", "hoo_2"]
    assert [x["remote_id"] for x in base["ligne"]["hook_sources"]] == ["hoo_3"]
    assert out["remote_cleanup_failed"] == []


def test_un_ancien_webhook_qui_RESISTE_est_rendu(base):
    _brancher()
    base["client"].refuse_retrait = "hoo_2"
    out = _brancher()
    assert [x["remote_id"] for x in out["remote_cleanup_failed"]] == ["hoo_2"]


# ── 4. un agent branché garde sa porte ────────────────────────────────────────

@pytest.mark.parametrize("op", ["rotate_secret", "rotate_address"])
def test_un_agent_BRANCHE_refuse_les_rotations(base, op):
    _brancher()
    with pytest.raises(AuthzDenied) as e:
        _appel(op=op)
    assert e.value.code == "source_connected"


def test_un_agent_BRANCHE_refuse_le_changement_de_mode(base):
    _brancher()
    with pytest.raises(AuthzDenied) as e:
        asyncio.run(RT._hook_auth(_ctx(), RT.HookAuthInput(trigger_id=5,
                                                          hook_auth="bearer")))
    assert e.value.code == "source_connected"


# ── 5. débrancher, supprimer ──────────────────────────────────────────────────

def test_DEBRANCHER_retire_chez_la_source_et_garde_le_mode(base):
    _brancher()
    out = _appel(op="disconnect_source")
    assert base["client"].retraits == ["hoo_1", "hoo_2"]
    assert not base["ligne"]["hook_sources"]
    assert base["ligne"]["hook_auth"] == runner_hook.BODY_SECRET
    assert [x["remote_id"] for x in out["disconnected"]] == ["hoo_1", "hoo_2"]
    _appel(op="rotate_secret")   # débranché : la porte se change de nouveau


def test_debrancher_GARDE_ce_qui_resiste_pour_le_retirer_plus_tard(base):
    _brancher()
    base["client"].refuse_retrait = "hoo_1"
    out = _appel(op="disconnect_source")
    assert [x["remote_id"] for x in base["ligne"]["hook_sources"]] == ["hoo_1"]
    assert [x["remote_id"] for x in out["remote_cleanup_failed"]] == ["hoo_1"]


def test_SUPPRIMER_un_agent_branche_retire_chez_la_source(base):
    _brancher()
    out = _appel(op="delete")
    assert base["supprime"] and base["client"].retraits == ["hoo_1", "hoo_2"]
    assert out["ok"] is True and out["remote_cleanup_failed"] == []


def test_supprimer_SANS_cle_de_la_source_supprime_quand_meme_et_le_dit(base, monkeypatch):
    _brancher()

    def _sans_cle(ctx, source):
        raise AuthzDenied(400, "credential_unavailable", "pas de clé")

    monkeypatch.setattr(S, "_client", _sans_cle)
    out = _appel(op="delete")
    assert base["supprime"]
    assert {x["error"] for x in out["remote_cleanup_failed"]} == {"credential_unavailable"}
