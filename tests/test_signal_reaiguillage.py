"""Ré-aiguiller un signal déposé sur la mauvaise organisation (signal #471).

Le cas vécu le 16/08 : un signal ÉCRIT au sujet d'une org, mais DÉPOSÉ sur une autre —
un espace de prospection sans rapport — parce qu'un appel avait omis son jeton d'org.
Il y est resté. `feedback` écrit et ne relit rien ; la console d'arbitrage sait poser un
état, pas corriger une adresse. Le rapporteur le résume ainsi : oto sait mettre une
chose dans un espace, il ne sait pas l'en sortir.

**Ré-aiguiller, pas supprimer** — la décision de ce lot, et pourquoi :

- **un signal est un FAIT.** L'agent a réellement buté sur ce manque ; c'est la matière
  même de la boucle d'usage (ADR 0017), et la ligne en est l'unique copie. Le défaut
  n'est pas que le signal existe, c'est qu'il est ADRESSÉ au mauvais espace : on répare
  l'adresse, on ne détruit pas la preuve ;
- **la mesure survit.** Les deux lentilles d'org — les manques agrégés et la liste des
  signaux — comptent par `org_id`. Déplacer la ligne retire le signal de l'espace qui
  n'aurait jamais dû le voir ET le rend à celui qui aurait dû ; une suppression ferait
  la première moitié et perdrait la seconde ;
- **la pile refuse déjà d'être un endroit où les choses disparaissent.** Refuser un
  signal EXIGE un motif, précisément pour qu'« un refus soit distinguable d'un oubli ».
  Une suppression rouvrirait cette porte sous un autre nom.

Reste `platform_admin`, comme tout le reste de la console d'arbitrage : l'espace de
destination est arbitraire, et laisser un responsable d'org pousser une ligne chez un
autre serait une écriture croisée sans personne pour la recevoir.
"""
from __future__ import annotations

import pytest

from oto_mcp.capabilities import usage as U
from oto_mcp.capabilities._types import AuthzDenied, ResolvedCtx

CTX = ResolvedCtx(sub="admin", org_id=1, role="super_admin")

SIGNAL = {"id": 468, "signal": "gap", "kind": "missing_tool",
          "target": "retirer un membre d'une org", "status": "open",
          "org_id": 246, "resolved_at": None, "resolved_by": None, "resolution": None}


@pytest.fixture
def store(monkeypatch):
    etat = {"row": dict(SIGNAL), "orgs": {2, 246}}

    def _reroute(signal_id, *, org_id):
        """Le MÊME contrat que `db.reroute_usage_signal` : la row à jour, plus l'org
        d'avant. Un stub qui l'oublierait ferait passer un lot qui la perd."""
        if int(signal_id) != 468:
            return None
        avant = etat["row"]["org_id"]
        etat["row"]["org_id"] = org_id
        return dict(etat["row"], previous_org_id=avant)

    monkeypatch.setattr(U.db, "reroute_usage_signal", _reroute)
    monkeypatch.setattr(U.db, "count_usage_signals_by_status",
                        lambda: {"open": 1, "acknowledged": 0, "declined": 0,
                                 "resolved": 0, "pending": 1})
    monkeypatch.setattr(U.org_store, "get_org", lambda oid: (
        {"id": oid, "name": f"org {oid}"} if oid in etat["orgs"] else None))
    return etat


# ── Le geste ─────────────────────────────────────────────────────────────────────────

def test_un_signal_mal_route_change_d_organisation(store):
    out = U._reroute_signal(CTX, U.RerouteSignalInput(signal_id=468, org_id=2))
    assert out["ok"] is True
    assert out["signal"]["org_id"] == 2
    assert out["previous_org_id"] == 246


def test_le_signal_lui_meme_est_intact(store):
    """Ce qui a été observé ne change pas : ré-aiguiller déplace une ADRESSE, pas un
    contenu. Un ré-aiguillage qui réécrirait le corps serait une réécriture de
    l'histoire, et c'est très exactement ce qu'on refuse à la suppression."""
    out = U._reroute_signal(CTX, U.RerouteSignalInput(signal_id=468, org_id=2))
    for champ in ("signal", "kind", "target", "status"):
        assert out["signal"][champ] == SIGNAL[champ]


def test_un_signal_peut_remonter_au_niveau_plateforme(store):
    """`org_id=null` est un ré-aiguillage légitime, pas un effacement déguisé : un
    signal qui ne concerne aucun espace client appartient à la plateforme."""
    out = U._reroute_signal(CTX, U.RerouteSignalInput(signal_id=468, org_id=None))
    assert out["ok"] is True and out["signal"]["org_id"] is None


# ── Les refus, chacun NOMMANT ce qui manque ─────────────────────────────────────────

def test_une_organisation_inconnue_est_REFUSEE_et_non_ecrite(store):
    """Sans ce contrôle, une faute de frappe enterre le signal dans un espace qui
    n'existe pas — le même défaut qu'on répare, en pire : plus personne ne le voit,
    et rien ne le dit."""
    with pytest.raises(AuthzDenied) as e:
        U._reroute_signal(CTX, U.RerouteSignalInput(signal_id=468, org_id=999))
    assert e.value.code == "unknown_org"
    assert "999" in str(e.value.message)
    assert store["row"]["org_id"] == 246, "rien ne doit avoir été écrit"


def test_un_signal_inconnu_le_dit(store):
    out = U._reroute_signal(CTX, U.RerouteSignalInput(signal_id=4040, org_id=2))
    assert out == {"ok": False, "error": "not_found", "id": 4040}


def test_l_arbitrage_reste_reserve_a_la_plateforme():
    """L'espace de destination est arbitraire : un responsable d'org qui pourrait
    pousser une ligne chez un autre écrirait chez quelqu'un qui n'a rien demandé."""
    from oto_mcp.capabilities.registry import CAPABILITIES
    from oto_mcp.capabilities._authz import PLATFORM_ADMIN
    capa = next(c for c in CAPABILITIES if c.key == "usage.reroute_signal")
    assert capa.authz is PLATFORM_ADMIN


def test_la_surface_nomme_sa_cible_dans_le_chemin():
    """Règle de la maison : ce qu'une écriture vise se lit dans l'URL."""
    from oto_mcp.capabilities.registry import CAPABILITIES
    capa = next(c for c in CAPABILITIES if c.key == "usage.reroute_signal")
    [b] = capa.rest_bindings()
    assert (b.verb, b.path) == ("POST", "/api/admin/usage/signals/{signal_id}/org")


# ── La console MCP : le geste doit être atteignable depuis un agent ─────────────────
#
# La route REST a été livrée seule (`e21e0ad`). Un signal mal aiguillé se corrige donc
# depuis le dashboard, et l'agent qui vient de constater l'erreur — souvent celui-là même
# dont l'appel a omis son jeton d'org — n'a rien pour la réparer. La plateforme sait faire
# le geste, l'agent ne l'atteint pas : c'est le même trou que la visibilité admin, une
# surface plus bas.


def _console(**kw):
    from oto_mcp.capabilities import admin_console as AC
    return AC._signal(CTX, AC.SignalAdminInput(**kw))


def test_le_reaiguillage_est_atteignable_depuis_la_console(store):
    """Le geste littéral : « ce signal parle de l'espace 2, il est posé sur le 246 »."""
    out = _console(op="reroute", signal_id=468, to_org="2")
    assert out["ok"] is True
    assert out["signal"]["org_id"] == 2
    assert out["previous_org_id"] == 246


def test_la_console_sait_remonter_un_signal_a_la_plateforme(store):
    """`platform` en toutes lettres : sur une console, tous les champs sont optionnels,
    donc un `org_id` absent et un `org_id: null` sont INDISTINGUABLES (vérifié : fastmcp
    remplit les défauts avant d'appeler le handler, `model_fields_set` ne tranche rien).
    Le mot dit la destination au lieu de la laisser tomber d'un défaut."""
    out = _console(op="reroute", signal_id=468, to_org="platform")
    assert out["ok"] is True
    assert out["signal"]["org_id"] is None
    assert out["previous_org_id"] == 246


def test_une_destination_non_dite_est_REFUSEE(store):
    """Le cœur de l'invariant que porte `RerouteSignalInput` : `None` n'est pas « ne rien
    changer », c'est la plateforme. Sur une console où tout est optionnel, le seul moyen de
    tenir cette exigence est de refuser la destination muette — sinon un `reroute` étourdi
    sortirait le signal de son espace sans que personne ne l'ait demandé."""
    with pytest.raises(AuthzDenied) as refus:
        _console(op="reroute", signal_id=468)
    assert refus.value.status == 400
    assert "to_org" in refus.value.message


def test_une_destination_qui_n_est_pas_un_espace_est_REFUSEE(store):
    """Un mot de travers ne doit pas se faire lire comme « la plateforme »."""
    with pytest.raises(AuthzDenied) as refus:
        _console(op="reroute", signal_id=468, to_org="chez moi")
    assert refus.value.status == 400
    assert "chez moi" in refus.value.message


def test_la_console_verifie_l_espace_de_destination(store):
    """La garde du handler traverse la console : un id inexistant enterrerait le signal."""
    with pytest.raises(AuthzDenied) as refus:
        _console(op="reroute", signal_id=468, to_org="999")
    assert refus.value.status == 404


def test_la_console_annonce_EXACTEMENT_ce_qu_elle_sait_faire():
    """Les deux sens, et le second est le plus coûteux.

    Un op livré mais tu est simplement invisible. Un op ANNONCÉ mais absent est pire :
    l'agent qui suit la description se fait refuser par le schéma du tool lui-même, sans
    rien pour comprendre que c'est la description qui ment. Vécu ici — `4b5355c` a ajouté
    `notify_preview`/`notify_send` à la description de `oto_admin_signal` en oubliant le
    `Literal` et l'aiguillage : deux gestes promis à l'agent, aucun atteignable.
    """
    import re
    import typing

    from oto_mcp.capabilities.registry import CAPABILITIES

    capa = next(c for c in CAPABILITIES if c.key == "admin.signal")
    servis = set(typing.get_args(capa.Input.model_fields["op"].annotation))
    annonces = set(re.findall(r"op=([a-z_]+)", capa.description))
    # La description énumère aussi à la barre (« op=list … / set_status (…) »).
    annonces |= {m for m in re.findall(r"/ ([a-z_]+) \(", capa.description)
                 if m in servis or m.startswith("notify")}

    assert not (servis - annonces), (
        f"{sorted(servis - annonces)} : servis par oto_admin_signal, tus par sa "
        "description — donc invisibles pour l'agent qui la lit.")
    assert not (annonces - servis), (
        f"{sorted(annonces - servis)} : promis par la description de oto_admin_signal, "
        "absents du `Literal` — l'agent qui suit la description se fait refuser par le "
        "schéma, et rien ne lui dit que c'est la description qui a tort.")


# ── Ce qu'on n'a PAS fait, et qui doit le rester ────────────────────────────────────

def test_aucune_suppression_de_signal_n_existe():
    """Le garde-fou de la décision. Le jour où quelqu'un ajoutera un `delete` sur la
    pile, ce test tombera et l'obligera à rouvrir l'arbitrage plutôt qu'à le contourner
    : un signal supprimé emporte une mesure, un signal ré-aiguillé la conserve."""
    from oto_mcp.capabilities.registry import CAPABILITIES
    from oto_mcp import db

    cles = {c.key for c in CAPABILITIES if c.key.startswith("usage.")}
    assert not {k for k in cles if "delete" in k or "purge" in k}
    assert not [n for n in dir(db)
                if "usage_signal" in n and ("delete" in n or "purge" in n)]


# ── La requête elle-même, contre un vrai PostgreSQL ─────────────────────────────────
#
# Le test du dessus stube `db.reroute_usage_signal` : il décrit la surface, pas le SQL.
# Or ce SQL est inhabituel — il rend la valeur d'AVANT et celle d'APRÈS dans la même
# instruction (sinon le ré-aiguillage ne se relit pas, et ne se défait pas). Un
# `UPDATE … FROM (SELECT …)` mal écrit passe la relecture et tombe en prod.

import re
from contextlib import contextmanager

from oto_mcp.db import _schema, usage


@pytest.fixture()
def live_signals(pg_dsn, monkeypatch):
    psycopg = pytest.importorskip("psycopg")
    from psycopg.rows import dict_row
    def _ddl(table: str) -> str:
        m = re.search(rf"^CREATE TABLE IF NOT EXISTS {table} \(.*?^\);",
                      _schema._SCHEMA, re.S | re.M)
        assert m, f"DDL de `{table}` introuvable dans _schema.py"
        return m.group(0)

    with psycopg.connect(pg_dsn, row_factory=dict_row, autocommit=True) as c:
        c.execute("DROP TABLE IF EXISTS usage_signals")
        c.execute("DROP TABLE IF EXISTS users CASCADE")
        c.execute(_ddl("usage_signals"))
        # `list_usage_signals` joint le rapporteur : sans la table, la lentille d'org
        # ne s'exerce pas — et c'est justement ce qu'on vient vérifier.
        c.execute(_ddl("users"))

        @contextmanager
        def _connect_test():
            yield c

        monkeypatch.setattr(usage, "_connect", _connect_test)
        yield c


def test_le_deplacement_rend_l_avant_ET_l_apres(live_signals):
    """Les deux valeurs, en une instruction. Les lire en deux temps les laisserait
    diverger entre les deux."""
    sid = usage.insert_usage_signal(
        sub="u1", org_id=246, signal="gap", kind="missing_tool",
        target="retirer un membre", body="déposé sur le mauvais espace",
        session_id=None)

    row = usage.reroute_usage_signal(sid, org_id=2)
    assert row["org_id"] == 2 and row["previous_org_id"] == 246
    assert row["body"] == "déposé sur le mauvais espace"    # le fait est intact

    # Et la ligne a bien changé d'espace pour les lentilles d'org, dans les deux sens.
    assert [r["id"] for r in usage.list_usage_signals(org_id=2)] == [sid]
    assert usage.list_usage_signals(org_id=246) == []


def test_remonter_a_la_plateforme_puis_redescendre(live_signals):
    sid = usage.insert_usage_signal(sub="u1", org_id=246, signal="gap", kind="other",
                                    target="x", body=None, session_id=None)
    assert usage.reroute_usage_signal(sid, org_id=None)["org_id"] is None
    assert usage.reroute_usage_signal(sid, org_id=246)["previous_org_id"] is None


def test_un_id_inconnu_ne_rend_rien_et_n_ecrit_rien(live_signals):
    assert usage.reroute_usage_signal(999_999, org_id=2) is None
