"""Rendre sa réponse à celui qui a signalé (#451).

**La boucle d'usage était à sens unique.** Un agent remonte qu'un outil se comporte
mal ; le signal part dans une pile ; et rien ne revient jamais — ni au compte sous
lequel l'agent a écrit, ni à personne. Mesuré le 27/08 : 534 signaux reçus depuis le
19/06, aucun retour à leur auteur, et deux externes en avaient remonté 51 et 53.

Ce banc garde les trois propriétés qui font qu'un retour vaut mieux que pas de retour.

**Groupé, jamais unitaire.** Trois personnes portaient 168 des 204 signaux en attente.
Un mail par signal aurait donc envoyé cinquante mails d'affilée à un partenaire le jour
où l'on vide la pile — la seule chose pire que le silence.

**L'envoi est un ACTE.** Ces mails partent chez des tiers, sous notre marque. L'aperçu
est le défaut et ne touche à rien ; envoyer se demande.

**Un envoi raté reste dû.** Marquer avant d'envoyer ferait disparaître le retour au
premier hoquet du mailer, et personne ne le saurait — le défaut qu'on ferme, déplacé
d'un cran.
"""
import pytest

from oto_mcp.capabilities import usage as cap
from oto_mcp.capabilities._types import ResolvedCtx
from oto_mcp.db import usage as db_usage

CTX = ResolvedCtx(sub="op-1", org_id=1)


def _signal(i, sub, *, status="resolved", email="a@b.c", target="x_tool"):
    return {"id": i, "sub": sub, "email": email, "name": None, "signal": "tool_feedback",
            "kind": "bug", "target": target, "body": "ça casse", "created_at": "2026-08-17",
            "status": status, "resolution": "corrigé", "resolved_at": "2026-08-27"}


@pytest.fixture
def bouchon(monkeypatch):
    """Le mailer et la base, remplacés — on juge le REGROUPEMENT et les gestes."""
    envois, marques = [], []
    monkeypatch.setattr(cap.mailer, "send_signal_digest_email",
                        lambda to, *, items, brand: (envois.append((to, len(items), brand)), True)[1])
    monkeypatch.setattr(cap.db, "mark_signals_notified", lambda ids: marques.append(list(ids)))
    monkeypatch.setattr(cap.config, "front_for", lambda sub: (None, None))
    return {"envois": envois, "marques": marques}


def test_une_personne_un_mail_quel_que_soit_le_nombre(monkeypatch, bouchon):
    """LA propriété du lot. 53 signaux d'une même personne font UN envoi, pas 53."""
    monkeypatch.setattr(cap.db, "pending_signal_notices",
                        lambda: [_signal(i, "sub-a") for i in range(53)])
    out = cap._notify_reporters(CTX, cap.NotifyReportersInput(op="send"))

    assert len(bouchon["envois"]) == 1, "un envoi par personne, pas par signal"
    assert bouchon["envois"][0][1] == 53, "les 53 signaux sont DANS le mail"
    assert out["sent"] == 1 and out["total_signals"] == 53


def test_deux_personnes_deux_mails(monkeypatch, bouchon):
    monkeypatch.setattr(cap.db, "pending_signal_notices",
                        lambda: [_signal(1, "sub-a"), _signal(2, "sub-b", email="b@b.c"),
                                 _signal(3, "sub-a")])
    cap._notify_reporters(CTX, cap.NotifyReportersInput(op="send"))
    assert sorted(e[1] for e in bouchon["envois"]) == [1, 2]


def test_l_apercu_n_envoie_rien_et_ne_marque_rien(monkeypatch, bouchon):
    """Le défaut, c'est l'aperçu : on regarde qui reçoit quoi AVANT que ça parte chez
    des tiers. Un aperçu qui enverrait serait le pire défaut possible de ce lot."""
    monkeypatch.setattr(cap.db, "pending_signal_notices",
                        lambda: [_signal(1, "sub-a"), _signal(2, "sub-b", email="b@b.c")])
    out = cap._notify_reporters(CTX, cap.NotifyReportersInput())

    assert out["op"] == "preview" and out["sent"] == 0
    assert bouchon["envois"] == [] and bouchon["marques"] == []
    assert [r["sent"] for r in out["recipients"]] == [None, None], \
        "en aperçu, `sent` reste indéterminé — ni vrai ni faux"


def test_un_envoi_rate_ne_marque_pas(monkeypatch, bouchon):
    """Sinon un hoquet du mailer fait disparaître le retour, silencieusement."""
    monkeypatch.setattr(cap.mailer, "send_signal_digest_email",
                        lambda to, *, items, brand: False)
    monkeypatch.setattr(cap.db, "pending_signal_notices", lambda: [_signal(1, "sub-a")])
    out = cap._notify_reporters(CTX, cap.NotifyReportersInput(op="send"))

    assert bouchon["marques"] == [], "rien n'est marqué si rien n'est parti"
    assert out["sent"] == 0
    assert out["recipients"][0]["sent"] is False and out["recipients"][0]["reason"]


def test_une_adresse_inconnue_se_voit_au_lieu_de_se_perdre(monkeypatch, bouchon):
    """Un compte supprimé depuis le signalement n'a plus d'adresse. Le taire ferait
    croire l'envoi complet ; on le montre, et ses signaux restent dus."""
    monkeypatch.setattr(cap.db, "pending_signal_notices",
                        lambda: [_signal(1, "sub-a", email=None)])
    out = cap._notify_reporters(CTX, cap.NotifyReportersInput(op="send"))

    assert bouchon["envois"] == [] and bouchon["marques"] == []
    assert out["recipients"][0]["sent"] is False
    assert "adresse" in out["recipients"][0]["reason"]


def test_only_restreint_l_envoi(monkeypatch, bouchon):
    """Sortir par paliers plutôt que d'un coup sur des tiers."""
    monkeypatch.setattr(cap.db, "pending_signal_notices",
                        lambda: [_signal(1, "sub-a", email="a@b.c"),
                                 _signal(2, "sub-b", email="b@b.c")])
    out = cap._notify_reporters(CTX, cap.NotifyReportersInput(op="send", only=["B@B.C"]))

    assert len(bouchon["envois"]) == 1 and bouchon["envois"][0][0] == "b@b.c", \
        "la restriction matche l'email SANS respecter la casse"
    assert out["total_signals"] == 1


def test_la_marque_est_celle_du_destinataire(monkeypatch, bouchon):
    """Écrire « oto » à l'utilisateur d'un partenaire est un faux, même quand tout le
    reste est juste — le destinataire ne nous connaît pas sous ce nom."""
    monkeypatch.setattr(cap.config, "front_for", lambda sub: ("https://x", "Tulina"))
    monkeypatch.setattr(cap.db, "pending_signal_notices", lambda: [_signal(1, "sub-a")])
    cap._notify_reporters(CTX, cap.NotifyReportersInput(op="send"))
    assert bouchon["envois"][0][2] == "Tulina"


# ── ce que la base doit garantir ──────────────────────────────────────────────

def test_seuls_les_etats_terminaux_font_un_retour():
    """« On l'a lu » n'est pas une réponse : l'annoncer userait le canal avant d'avoir
    rien dit."""
    assert set(db_usage.SIGNAL_TERMINAL) == {"resolved", "declined"}


def test_ré_arbitrer_efface_l_annonce():
    """Un « traité » corrigé en « refusé » doit être re-annoncé, sinon la personne
    reste sur une réponse qui n'a plus cours."""
    import inspect
    src = inspect.getsource(db_usage.set_usage_signal_status)
    assert src.count("notified_at = NULL") == 2, \
        "les DEUX branches (retour à open, et tout autre état) doivent effacer l'annonce"


def test_le_mail_parle_des_AGENTS_pas_de_la_personne():
    """Ces retours sont écrits par des agents en session, sous le compte de quelqu'un
    qui n'a le plus souvent jamais su qu'ils existaient. Lui écrire « votre
    signalement » lui attribuerait des mots qu'elle n'a pas écrits."""
    from oto_mcp import email as mailer
    envoyes = {}
    mailer._send = lambda to, subject, html, *a, **k: envoyes.update(
        {"subject": subject, "html": html}) or True
    mailer.send_signal_digest_email("a@b.c", items=[_signal(1, "sub-a")], brand="oto")

    assert "vos agents" in envoyes["html"]
    assert "votre signalement" not in envoyes["html"]
    assert "vos agents" in envoyes["subject"] or "de vos agents" in envoyes["subject"]


def test_un_refus_ne_se_dit_pas_refuse_a_celui_qui_a_signale():
    """Le mot interne est `declined` ; le mot qu'on écrit est « non retenu ». Le
    rapporteur a rendu service en signalant — le mot qui blesse est celui qu'on
    retient, et on veut qu'il signale encore."""
    from oto_mcp import email as mailer
    envoyes = {}
    mailer._send = lambda to, subject, html, *a, **k: envoyes.update({"html": html}) or True
    mailer.send_signal_digest_email(
        "a@b.c", items=[_signal(1, "sub-a", status="declined")], brand="oto")

    assert "non retenu" in envoyes["html"]
    assert "refusé" not in envoyes["html"]


def test_le_rattrapage_historique_est_borne_dans_le_temps():
    """Le défaut le plus coûteux de ce lot, et il n'a rien cassé de visible : le
    backfill « les anciens arbitrages sont réputés annoncés » n'avait pas de borne,
    donc il RE-TOURNAIT à chaque démarrage et marquait « déjà annoncé » tout ce qui
    venait d'être arbitré sans avoir encore été envoyé. Il a avalé les 62 premiers
    retours réels, entre l'arbitrage et l'envoi, sans un mot.

    Un backfill est un geste UNIQUE : il doit se reconnaître à ce qu'il rattrape —
    une population fermée — et jamais à l'état dans lequel il met les lignes, sinon
    il devient une purge permanente déguisée en migration."""
    import pathlib
    src = pathlib.Path(__file__).resolve().parent.parent / "oto_mcp" / "db" / "_init.py"
    txt = src.read_text(encoding="utf-8")
    i = txt.index("SET notified_at = resolved_at")
    fenetre = txt[i:i + 400]
    assert "resolved_at <" in fenetre and "2026-08-20" in fenetre, (
        "le rattrapage de `notified_at` doit être BORNÉ à la population historique. "
        "Sans borne il se rejoue à chaque boot et mange les retours en attente.")
