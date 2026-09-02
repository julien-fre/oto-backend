"""Les garde-fous d'un envoi en masse — chacun exercé jusqu'au refus.

Ce fichier ne teste pas l'envoi : il teste **ce qui l'empêche**. Un envoi qui part
trop tôt ne se rattrape pas, donc chaque barrière doit être vue rouge au moins une
fois. Quatre d'entre elles vivent dans le handler, la cinquième (l'exclusion
partenaire) dans le SQL — `tests/test_outreach_audience_db.py`.

Le mailer est remplacé par un mouchard : aucun email ne part d'ici. Chaque test qui
attend un refus vérifie EN PLUS que rien n'a été envoyé — un refus qui arrive après
le premier mail n'est pas un refus.
"""
from __future__ import annotations

import pytest

from oto_mcp.capabilities import outreach
from oto_mcp.capabilities._types import AuthzDenied, ResolvedCtx

CAMPAGNE = "relance-2026-09"
OPERATEUR = "sub-operateur"


@pytest.fixture
def banc(monkeypatch):
    """Une audience de trois comptes (deux FR, un EN déclaré), zéro envoi réel."""
    envois: list = []
    journal: list = []
    banc_total = {"n": 3}

    def _audience(*, campaign, statut="jamais_actif", silence_days=30, cap=200):
        return [
            {"sub": "s1", "email": "un@exemple.test", "name": None, "locale": None,
             "created_at": None, "appels": 0, "last_seen_at": None,
             "relances_deja_recues": 0},
            {"sub": "s2", "email": "deux@exemple.test", "name": None, "locale": "en",
             "created_at": None, "appels": 0, "last_seen_at": None,
             "relances_deja_recues": 0},
            {"sub": "s3", "email": "trois@exemple.test", "name": None, "locale": "fr",
             "created_at": None, "appels": 0, "last_seen_at": None,
             "relances_deja_recues": 0},
        ][:cap]

    def _enregistre(*, campaign, sub, to_email, locale, fingerprint, kind="send",
                    sent_by=None):
        cle = (campaign, sub, kind)
        if kind == "send" and cle in {(c, s, k) for c, s, k, *_ in journal}:
            return False
        journal.append((campaign, sub, kind, locale, fingerprint))
        return True

    def _locales(*, campaign, fingerprint):
        return {lc for c, _s, k, lc, fp in journal
                if k == "test" and c == campaign and fp == fingerprint}

    monkeypatch.setattr(outreach.db_outreach, "audience", _audience)
    # Le compte RÉEL de l'audience — celui qu'un envoi annonce. Sans plafond : c'est
    # justement l'écart entre lui et la page servie que le plafond doit refuser.
    monkeypatch.setattr(outreach.db_outreach, "taille_audience",
                        lambda **kw: banc_total["n"])
    monkeypatch.setattr(outreach.db_outreach, "enregistre_envoi", _enregistre)
    monkeypatch.setattr(outreach.db_outreach, "locales_essayees", _locales)
    monkeypatch.setattr(outreach.db_outreach, "annule_envoi",
                        lambda **kw: journal.remove(
                            next(l for l in journal
                                 if l[0] == kw["campaign"] and l[1] == kw["sub"]
                                 and l[2] == "send")))
    monkeypatch.setattr(outreach.db, "get_user",
                        lambda sub: {"email": "moi@exemple.test", "locale": "fr"})
    monkeypatch.setattr(outreach.outreach_optout, "lien",
                        lambda sub: f"https://mcp.exemple.test/o/u/{sub}")
    monkeypatch.setattr(outreach.mailer, "send_composed_email",
                        lambda to, subject, body, **kw: envois.append(
                            {"to": to, "subject": subject, "locale": kw.get("locale"),
                             "unsub": kw.get("unsubscribe_url")}) or True)
    return {"envois": envois, "journal": journal, "total": banc_total}


def _appel(**champs):
    inp = outreach.OutreachInput(campaign=CAMPAGNE, **champs)
    return outreach._outreach(ResolvedCtx(sub=OPERATEUR), inp)


_TEXTE = dict(subject_fr="objet", body_fr="bonjour\n\nvoilà.",
              subject_en="subject", body_en="hello\n\nthere.")


# ── ① rien ne part sans essai reçu ───────────────────────────────────────────

def test_envoyer_sans_essai_est_REFUSE(banc):
    with pytest.raises(AuthzDenied) as e:
        _appel(op="send", confirm=3, **_TEXTE)
    assert e.value.code == "test_send_required"
    assert banc["envois"] == [], "un refus qui arrive après le premier mail n'en est pas un"


def test_l_essai_part_chez_l_APPELANT_et_dans_chaque_langue_servie(banc):
    out = _appel(op="test", **_TEXTE)
    assert {e["to"] for e in banc["envois"]} == {"moi@exemple.test"}
    assert sorted(e["locale"] for e in banc["envois"]) == ["en", "fr"]
    assert sorted(out["tested_locales"]) == ["en", "fr"]


def test_retoucher_le_texte_INVALIDE_l_essai(banc):
    _appel(op="test", **_TEXTE)
    banc["envois"].clear()
    retouche = dict(_TEXTE, body_fr="bonjour\n\nvoilà !")   # une ponctuation
    with pytest.raises(AuthzDenied) as e:
        _appel(op="send", confirm=3, **retouche)
    assert e.value.code == "test_send_required"
    assert banc["envois"] == []


def test_un_essai_dans_UNE_seule_langue_ne_debloque_pas_l_autre(banc):
    """L'essai est exigé par langue SERVIE : voir la version française ne dit rien de
    l'anglaise, qui partira pourtant chez de vraies personnes."""
    _appel(op="test", **_TEXTE)
    banc["journal"][:] = [l for l in banc["journal"] if l[3] != "en"]
    banc["envois"].clear()
    with pytest.raises(AuthzDenied) as e:
        _appel(op="send", confirm=3, **_TEXTE)
    assert e.value.code == "test_send_required" and "en" in str(e.value)
    assert banc["envois"] == []


# ── ② le nombre est annoncé avant de partir ──────────────────────────────────

def test_envoyer_sans_confirm_ANNONCE_le_nombre_et_ne_part_pas(banc):
    _appel(op="test", **_TEXTE)
    banc["envois"].clear()
    with pytest.raises(AuthzDenied) as e:
        _appel(op="send", **_TEXTE)
    assert e.value.code == "confirmation_required"
    assert "3 personnes" in str(e.value), "le refus doit DIRE combien de gens sont visés"
    assert banc["envois"] == []


def test_un_confirm_qui_ne_colle_pas_est_REFUSE(banc):
    _appel(op="test", **_TEXTE)
    banc["envois"].clear()
    with pytest.raises(AuthzDenied) as e:
        _appel(op="send", confirm=2, **_TEXTE)
    assert e.value.code == "confirmation_mismatch"
    assert banc["envois"] == []


def test_le_plafond_se_juge_sur_l_audience_ENTIERE_pas_sur_la_page_servie(banc):
    """Le piège que ce test ferme : la lecture TRONQUE déjà à `MAX_ENVOI`. Un plafond
    comparé à la liste servie serait donc inatteignable par construction — vert pour
    toujours, et l'opérateur enverrait à 200 personnes en croyant en couvrir 3 000."""
    banc["total"]["n"] = 5000
    _appel(op="test", **_TEXTE)
    banc["envois"].clear()
    with pytest.raises(AuthzDenied) as e:
        _appel(op="send", confirm=3, **_TEXTE)
    assert e.value.code == "audience_too_large" and "5000" in str(e.value)
    assert banc["envois"] == []


def test_une_audience_tronquee_le_DIT(banc):
    banc["total"]["n"] = 5000
    out = _appel(op="audience")
    assert out["total"] == 5000 and out["selected"] == 3 and out["truncated"] is True


# ── ③ l'envoi nominal, une fois les barrières levées ─────────────────────────

def test_l_envoi_sert_la_langue_DECLAREE_et_le_defaut_pour_le_reste(banc):
    _appel(op="test", **_TEXTE)
    banc["envois"].clear()
    out = _appel(op="send", confirm=3, default_locale="fr", **_TEXTE)
    assert out["sent"] == 3
    par_dest = {e["to"]: e for e in banc["envois"]}
    assert par_dest["deux@exemple.test"]["locale"] == "en", "préférence déclarée"
    assert par_dest["trois@exemple.test"]["locale"] == "fr", "préférence déclarée"
    assert par_dest["un@exemple.test"]["locale"] == "fr", "aucune préférence ⟹ le défaut"
    assert out["with_declared_locale"] == 2 and out["with_default_locale"] == 1


def test_chaque_mail_porte_SON_lien_de_desinscription(banc):
    _appel(op="test", **_TEXTE)
    banc["envois"].clear()
    _appel(op="send", confirm=3, **_TEXTE)
    liens = {e["to"]: e["unsub"] for e in banc["envois"]}
    assert len(set(liens.values())) == 3, (
        "un lien par personne : un lien partagé désinscrirait quelqu'un d'autre")
    assert all(l for l in liens.values())


def test_un_envoi_qui_ECHOUE_ne_condamne_pas_son_destinataire(banc, monkeypatch):
    """Sans le retrait de la trace, un hoquet du mailer sortirait la personne de
    toute audience future alors qu'elle n'a jamais rien reçu."""
    _appel(op="test", **_TEXTE)
    banc["envois"].clear()
    monkeypatch.setattr(outreach.mailer, "send_composed_email",
                        lambda to, subject, body, **kw: to != "deux@exemple.test")
    out = _appel(op="send", confirm=3, **_TEXTE)
    assert out["sent"] == 2
    rate = next(r for r in out["recipients"] if r["email"] == "deux@exemple.test")
    assert rate["sent"] is False and "reste à relancer" in rate["reason"]
    assert not [l for l in banc["journal"] if l[1] == "s2" and l[2] == "send"], (
        "la trace d'un envoi qui n'est pas parti doit disparaître")


# ── ④ l'aperçu ne touche à rien ──────────────────────────────────────────────

def test_l_apercu_n_envoie_RIEN_et_rend_les_deux_langues(banc):
    out = _appel(op="preview", **_TEXTE)
    assert banc["envois"] == [] and banc["journal"] == []
    assert sorted(out["preview_html"]) == ["en", "fr"]
    assert "bonjour" in out["preview_html"]["fr"] and "hello" in out["preview_html"]["en"]


def test_l_audience_sans_campagne_est_REFUSEE(banc):
    with pytest.raises(AuthzDenied) as e:
        outreach._outreach(ResolvedCtx(sub=OPERATEUR), outreach.OutreachInput(op="audience"))
    assert e.value.code == "campaign_required"


def test_une_langue_servie_sans_texte_est_REFUSEE(banc):
    """`s2` a déclaré l'anglais : partir sans version anglaise lui enverrait du
    français, ou rien."""
    with pytest.raises(AuthzDenied) as e:
        _appel(op="preview", subject_fr="objet", body_fr="bonjour")
    assert e.value.code == "content_required" and "_en" in str(e.value)


# ── ⑤ l'autz : qui peut faire partir un mail ─────────────────────────────────

_OPS = set(outreach.OutreachInput.model_fields["op"].annotation.__args__)
_OPS_QUI_ENVOIENT = {"test", "send", "optout_clear"}


@pytest.mark.parametrize("op", sorted(_OPS))
def test_un_admin_NON_super_ne_fait_partir_aucun_mail(op, monkeypatch):
    """Lire l'audience est une lentille de supervision ; faire partir un mail sous
    notre marque — ou lever le refus de quelqu'un — est un acte de plateforme.

    La règle est EXERCÉE, pas introspectée : un test qui lirait la table du
    combinateur resterait vert si la règle cessait d'être appliquée. Et il est
    paramétré sur l'énuméré `op`, donc **une op ajoutée sans gate arrive ici toute
    seule** au lieu d'attendre qu'on y pense."""
    from oto_mcp.capabilities import _authz, registry
    cap = next(c for c in registry.CAPABILITIES if c.key == "admin.outreach")
    monkeypatch.setattr(_authz.access, "is_platform_operator", lambda sub: True)
    monkeypatch.setattr(_authz.access, "is_super_admin", lambda sub: False)
    monkeypatch.setattr(_authz.access, "current_org", lambda sub: None)
    monkeypatch.setattr(_authz.access, "get_user_role", lambda sub: "admin")
    autorise = _authz.capacite_autorise("admin.outreach", "sub-operateur", op=op)
    assert autorise is (op not in _OPS_QUI_ENVOIENT), (
        f"op={op!r} : un admin plateforme NON super " +
        ("ne devrait pas pouvoir déclencher un envoi" if op in _OPS_QUI_ENVOIENT
         else "devrait pouvoir lire cette lentille"))
    assert cap.authz is not None


@pytest.mark.parametrize("op", sorted(_OPS))
def test_aucune_op_n_arrive_sans_gate(op, monkeypatch):
    """Un op hors table du combinateur lève `unsupported_op` — donc `False` ici pour
    TOUT le monde, super admin compris. C'est ce qui attrape l'op qu'on ajoute à
    l'énuméré en oubliant sa règle."""
    from oto_mcp.capabilities import _authz
    monkeypatch.setattr(_authz.access, "is_platform_operator", lambda sub: True)
    monkeypatch.setattr(_authz.access, "is_super_admin", lambda sub: True)
    monkeypatch.setattr(_authz.access, "current_org", lambda sub: None)
    monkeypatch.setattr(_authz.access, "get_user_role", lambda sub: "super_admin")
    assert _authz.capacite_autorise("admin.outreach", "sub-op", op=op), (
        f"op={op!r} n'est couvert par aucune branche du combinateur : il refuse tout "
        "le monde en `unsupported_op`, ce qui ressemble à un droit manquant.")
