"""Un retour déposé DEUX FOIS à l'identique ne crée pas deux lignes (#684/#685).

Le 03/09/2026 à 08:42, le même agent a déposé le même signalement **deux fois, à
onze secondes d'écart** — corps identique au caractère près, sur des fiches créées
en double dans le CRM d'un client. L'insertion était nue : deux dépôts, deux
lignes, aucun signe. La pile d'arbitrage a donc compté deux occurrences là où il
n'y en avait qu'une, et un lecteur pouvait croire que le défaut s'était reproduit.

⚠️ **C'est notre propre boucle de retour qui se faisait le défaut qu'elle sert à
remonter** : une information produite — « tu m'as déjà dit exactement ça » —
que personne ne rendait. Elle n'a droit à aucun traitement de faveur : c'est même
le meilleur cas de démonstration de la classe.

⚠️ **On ne refuse pas, on rend l'existant et on le DIT.** Refuser ferait perdre
son retour à un agent qui redépose de bonne foi ; se taire gonflerait la pile de
faux volume. Le second appel reçoit le même identifiant, et sait que c'est le même.

Éprouvé rouge le 2026-09-03 : la recherche de rejeu retirée ⟹ le premier test
constate deux identifiants distincts pour un seul fait.
"""
from __future__ import annotations

import re
from contextlib import contextmanager

import pytest

from oto_mcp.db import _schema, usage

_CORPS = ("op=bulk_create a rendu success:true pour deux fiches qui étaient des "
          "doublons exacts de fiches existantes sur le même compte.")


@pytest.fixture()
def live_signals(pg_module_dsn, monkeypatch):
    psycopg = pytest.importorskip("psycopg")
    from psycopg.rows import dict_row

    def _ddl(table: str) -> str:
        m = re.search(rf"^CREATE TABLE IF NOT EXISTS {table} \(.*?^\);",
                      _schema._SCHEMA, re.S | re.M)
        assert m, f"DDL de `{table}` introuvable"
        return m.group(0)

    with psycopg.connect(pg_module_dsn, row_factory=dict_row, autocommit=True) as c:
        c.execute("DROP TABLE IF EXISTS usage_signal_occurrences")
        c.execute("DROP TABLE IF EXISTS usage_signals")
        c.execute(_ddl("usage_signals"))
        c.execute(_ddl("usage_signal_occurrences"))

        @contextmanager
        def _connect_test():
            yield c

        monkeypatch.setattr(usage, "_connect", _connect_test)
        yield c
        c.execute("DROP TABLE IF EXISTS usage_signal_occurrences")
        c.execute("DROP TABLE IF EXISTS usage_signals")


def _depose(**kw):
    base = dict(sub="agent-1", org_id=196, signal="tool_feedback",
                kind="wrong_result", target="salesforce_record", body=_CORPS,
                session_id="s1")
    base.update(kw)
    return usage.insert_usage_signal(**base)


def test_le_meme_retour_depose_deux_fois_rend_le_MEME_identifiant(live_signals):
    """Le cas mesuré, à onze secondes d'écart."""
    premier = _depose()
    second = _depose(session_id="s2")     # même agent, session relancée
    assert premier.deja is False and second.deja is True
    assert second.id == premier.id, "un rejeu ne doit pas fabriquer une seconde ligne"
    assert second.occurrences == 1, "un rejeu n'est pas une occurrence"


def test_une_seule_ligne_existe_en_base(live_signals):
    """Le compte est ce qui compte : la pile d'arbitrage ne doit pas gonfler d'un
    faux volume, sinon on croit qu'un défaut se reproduit."""
    _depose()
    _depose()
    n = live_signals.execute("SELECT COUNT(*) AS n FROM usage_signals").fetchone()
    assert n["n"] == 1


def test_un_corps_DIFFERENT_est_une_seconde_OCCURRENCE(live_signals):
    """Deux retours sur le même sujet sont normaux et fréquents. C'est le texte à
    l'identique qui trahit le rejeu ; un autre texte est une vraie seconde occurrence —
    rattachée au signal en attente, son texte gardé, pas un second sujet dans la pile."""
    premier = _depose()
    second = _depose(body=_CORPS + " Deuxième occurrence, autre compte.")
    assert second.deja is False and second.rattache is True
    assert second.id == premier.id and second.occurrences == 2
    o = live_signals.execute("SELECT body FROM usage_signal_occurrences").fetchone()
    assert o["body"].endswith("autre compte."), "le texte de l'occurrence est gardé"


def test_un_AUTRE_agent_n_est_jamais_un_rejeu(live_signals):
    """Deux agents qui butent sur le même défaut, c'est le signal le plus fort
    qu'on puisse recevoir : le second est une OCCURRENCE qui garde son auteur, jamais
    un rejeu effacé."""
    premier = _depose()
    second = _depose(sub="agent-2")
    assert second.deja is False and second.rattache is True
    assert second.id == premier.id and second.occurrences == 2
    o = live_signals.execute("SELECT sub FROM usage_signal_occurrences").fetchone()
    assert o["sub"] == "agent-2", "l'auteur de l'occurrence est gardé"


def test_le_meme_outil_avec_un_AUTRE_sujet_passe(live_signals):
    premier = _depose()
    second = _depose(target="salesforce_query")
    assert second.deja is False and second.rattache is False
    assert second.id != premier.id


def test_la_surface_DIT_le_rejeu_au_lieu_de_le_taire(live_signals, monkeypatch):
    """Le témoin doit atteindre l'appelant : sans lui, l'agent croit avoir déposé
    un second retour et ne saura jamais que non."""
    from oto_mcp.capabilities import usage as cap

    monkeypatch.setattr(cap, "_correlation", lambda: ("agent", "s1"))
    monkeypatch.setattr(cap, "_active_org", lambda _s: 196)

    class _Ctx:
        sub, org_id = "agent-1", 196

    class _In:
        signal, kind, target, text = "tool_feedback", "wrong_result", "x", _CORPS

    premier = cap._feedback(_Ctx(), _In())
    second = cap._feedback(_Ctx(), _In())
    assert "deja_signale" not in premier
    assert second["deja_signale"] is True and second["id"] == premier["id"]
    assert "ce qui a changé" in second["deja_signale_hint"], (
        "le témoin doit dire quoi faire si le défaut s'est VRAIMENT reproduit")


def test_une_AUTRE_organisation_n_est_jamais_un_rejeu(live_signals):
    """La borne la plus importante, et je l'avais manquée.

    Les deux dépôts qui ont motivé ce lot n'étaient PAS un rejeu : l'auteur avait
    adressé le premier à la mauvaise organisation et l'a redéposé, texte
    identique, sur la bonne — il l'écrit dans le second corps, que je n'avais pas
    lu jusqu'au bout. Sans cette clause, mon propre correctif aurait fusionné le
    second dans le premier et laissé le signalement classé au mauvais endroit,
    **définitivement**.

    ⚠️ C'est la forme la plus coûteuse d'une garde : celle qui détruit la donnée
    qu'elle prétend ranger, en croyant reconnaître un défaut qu'elle n'a pas
    vérifié. Un rapport de terrain est une mesure ; l'interprétation qu'on en
    fait n'en est pas une.
    """
    premier = _depose(org_id=249)
    second = _depose(org_id=178)
    assert second.deja is False and second.rattache is False
    assert second.id != premier.id, (
        "le même texte sur DEUX organisations est une correction d'adresse, "
        "pas un rejeu — les fusionner perd le classement")
