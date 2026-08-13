"""Un run muet cesse de s'annoncer « en cours ».

En production, 16 runs s'affichaient ouverts ; **15 n'avaient plus donné signe de vie
depuis 1 jour à 1 mois** (#309). Ce ne sont pas des travaux en cours : ce sont des
conversations terminées sans clôture déclarée. L'affichage mentait — dashboard,
lentilles, et le bloc injecté que lisent tous les agents au handshake.

Ce que ces tests figent :

- le silence est **dérivé**, jamais stocké (pas de démon, pas de colonne d'état qui
  pourrait mentir à son tour) ;
- le seuil vient de la mesure : aucun run silencieux entre 1 jour et 1 mois, donc 48 h
  ne marque aucune population à tort ;
- le vocabulaire de clôture a **une seule source**, et cette source est celle de l'ADR.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from oto_mcp import run_status

MAINTENANT = datetime(2026, 8, 13, 12, 0, tzinfo=timezone.utc)


def _il_y_a(**kw) -> datetime:
    return MAINTENANT - timedelta(**kw)


# --- le seuil, exercé de part et d'autre ---------------------------------------

@pytest.mark.parametrize("heures, muet", [
    (0, False), (1, False), (24, False),
    (47, False),        # la veille de la bascule : encore « en cours »
    (48, False),        # pile au seuil : pas encore (strictement supérieur)
    (49, True),         # au-delà : muet
    (24 * 30, True),    # le cas réel le plus ancien du recensement
])
def test_le_silence_bascule_a_48h(heures, muet):
    assert run_status.is_stale(None, _il_y_a(hours=heures), now=MAINTENANT) is muet


def test_un_run_CLOS_nest_jamais_muet():
    """L'issue est un fait DÉCLARÉ : elle prime sur toute inférence. Un run clos il y a
    six mois reste clos, il ne devient pas « sans nouvelles »."""
    for issue in run_status.OUTCOMES:
        assert not run_status.is_stale(issue, _il_y_a(days=180), now=MAINTENANT)


def test_sans_date_lisible_on_naffirme_rien():
    """L'ignorance ne doit pas se transformer en affirmation : sans dernier signe de
    vie, on ne déclare pas le silence — on garde « en cours »."""
    for valeur in (None, "", "pas une date", 42, object()):
        assert run_status.is_stale(None, valeur, now=MAINTENANT) is False


def test_une_date_en_chaine_est_acceptee():
    """Les lectures de ce dépôt rendent parfois les dates en chaîne (le row factory
    normalise pour les réponses JSON). Refuser cette forme ferait marcher la dérivation
    sur une surface et pas sur l'autre — le défaut même qu'on corrige."""
    assert run_status.is_stale(None, "2026-08-01 09:00:00", now=MAINTENANT)
    assert not run_status.is_stale(None, "2026-08-13 09:00:00", now=MAINTENANT)


def test_une_date_sans_fuseau_est_lue_comme_utc():
    """Postgres rend du `timestamptz`, mais un test ou un appelant peut passer du naïf.
    Le comparer à un aware lèverait `TypeError` — donc un plantage au rendu d'une
    lentille, pour une question d'annotation."""
    naif = datetime(2026, 8, 1, 9, 0)
    assert run_status.is_stale(None, naif, now=MAINTENANT)


# --- ce qui s'écrit à côté du run ----------------------------------------------

def test_un_run_clos_porte_son_issue():
    assert run_status.describe({"outcome": "done"}, now=MAINTENANT) == "→ done"


def test_un_run_vivant_reste_en_cours():
    run = {"outcome": None, "last_seen_at": _il_y_a(hours=2)}
    assert run_status.describe(run, now=MAINTENANT) == "(en cours)"


def test_un_run_muet_porte_la_DATE_de_son_dernier_signe():
    """Une date, pas une durée : « depuis le 01/08 » se vérifie d'un coup d'œil dans le
    journal, « depuis 12 jours » oblige à compter."""
    run = {"outcome": None, "last_seen_at": datetime(2026, 8, 1, 9, 0, tzinfo=timezone.utc)}
    assert run_status.describe(run, now=MAINTENANT) == "(sans nouvelles depuis le 01/08)"


def test_un_run_sans_dernier_signe_reste_en_cours():
    assert run_status.describe({"outcome": None}, now=MAINTENANT) == "(en cours)"


# --- le vocabulaire : une seule source, et c'est celle de l'ADR ----------------

def test_le_vocabulaire_est_celui_de_ladr():
    """ADR 0058-D5 porte `done` · `failed` · `blocked` (plus `running`/`waiting_human`,
    qui sont des ÉTATS, pas des issues déclarables).

    ⚠️ `failed` reste bien qu'il n'ait JAMAIS servi (#309) : la mesure dit qu'un retrait
    serait indolore, pas qu'il serait juste. On ne retire pas un mot du vocabulaire
    d'une décision d'architecture au motif qu'il n'a pas encore servi — un run peut
    échouer sans être bloqué, et l'absence du cas dans l'histoire dit surtout que la
    plateforme est jeune. `abandoned`, lui, ne figure PAS dans D5 : c'est ce qui a
    permis de le retirer, pas son absence d'usage."""
    assert run_status.OUTCOMES == ("done", "failed", "blocked")
    assert "abandoned" not in run_status.OUTCOMES
    assert "stale" not in run_status.OUTCOMES, (
        "`stale` se DÉRIVE, il ne se déclare pas — l'exposer comme issue rouvrirait "
        "la porte à un état écrit qui peut mentir")


def test_le_tool_de_cloture_ne_redeclare_pas_le_vocabulaire():
    """TRIPWIRE — la liste a divergé de la prose du bloc A pendant des mois, ce qui est
    la façon la plus discrète de mentir à un agent : le schéma dit une chose,
    l'instruction une autre. Une seule source, partagée."""
    from oto_mcp.tools import doctrine_run
    assert doctrine_run._OUTCOMES is run_status.OUTCOMES


def test_la_prose_injectee_ne_propose_plus_le_mot_mort():
    """Le bloc A prescrit le geste de clôture. Y laisser une valeur que le serveur
    refuse ferait échouer l'agent qui obéit à la consigne."""
    from oto_mcp import instructions
    assert "abandoned" not in instructions._SECRET_SAUCE
    for mot in run_status.OUTCOMES:
        assert mot in instructions._SECRET_SAUCE, f"« {mot} » absent de la prose"


def test_le_tool_reellement_monte_refuse_une_issue_inconnue():
    """⚠️ Le banc charge ce que charge le BOOT (`register_all`), pas le module seul :
    quatre garde-fous de cette semaine mentaient parce que leur banc reproduisait une
    partie du démarrage et promettait le tout."""
    import asyncio

    from fastmcp import FastMCP
    from mcp.shared.exceptions import McpError

    from oto_mcp.tools import register_all
    mcp = FastMCP("run-status-probe")
    register_all(mcp)
    outil = asyncio.run(mcp.get_tool("run_finish"))
    # On appelle la fonction RÉELLEMENT montée. Son `ctx` est une dépendance de
    # session, indisponible hors serveur — mais la validation de l'issue précède tout
    # usage du contexte, donc un refus prouve exactement ce qu'on veut prouver, et un
    # `ctx` factice suffirait à masquer un jour où ce ne serait plus vrai.
    with pytest.raises(McpError) as err:
        asyncio.run(outil.fn(ctx=None, run_id="r-1", outcome="abandoned"))
    message = str(err.value)
    assert "outcome" in message
    for valide in run_status.OUTCOMES:
        assert valide in message, (
            "le refus doit LISTER les valeurs valides — sinon l'agent retente au hasard")
