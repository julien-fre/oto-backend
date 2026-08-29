"""Budget du handshake : ce que les jetons `_*` coûtent, agrégé sur toute la surface.

Le poids d'un schéma d'outil ne se juge pas au fichier — il se juge MULTIPLIÉ. Les six
jetons de contexte sont recopiés dans le schéma de chaque outil concerné : une phrase
ajoutée ici est payée ~400 fois, à chaque tour, par chaque agent branché.

Mesuré le 10/08 sur la mission Audiens (9 outils, function calling Mistral) : la reprise
des schémas tels quels coûtait 94 $ sur le fichier complet, et la boîte entière ne tenait
pas dans une fenêtre de 128 000 tokens. Re-mesuré le 14/08 : les axes pesaient encore
424 744 c., soit 48,2 % des 880 520 c. servis — la coupe de l'issue #277 avait raccourci
les paragraphes sans fermer la multiplication.

Une règle écrite (« garde ces descriptions courtes ») ne tient pas : elle se contourne
sans le vouloir, une phrase à la fois. Un budget, si — rallonger devient un choix visible,
qui casse un test et se discute.
"""
import asyncio
import json

import pytest
from fastmcp import FastMCP

from oto_mcp import call_axes

# ⚠️ **Le budget est PAR OUTIL, pas global — et c'est la correction du 21/08.**
#
# Le plafond absolu (290 000 c.) a sauté quand le catalogue est passé de 410 à 466
# outils : six connecteurs contribués en une nuit, et le test rouge. Or rien n'avait
# régressé — mesuré, le coût des axes vaut **632 caractères par outil** avant comme
# après. Le test punissait la CROISSANCE au lieu de la dérive, ce qui en fait un test
# qu'on finit par relever machinalement à chaque ajout : à la troisième fois, plus
# personne ne se demande si le chiffre a une raison.
#
# La propriété qu'on garde n'a jamais été « le catalogue ne grossit pas » — c'est
# « une phrase écrite dans call_axes n'est pas payée par tous ». Elle se mesure par
# outil, et par la PART du handshake. Les deux sont invariants à l'échelle.
_BUDGET_PAR_OUTIL = 800     # mesuré 766 c./outil le 29/08 (558 outils, après #547)
_BUDGET_PART = 0.40         # part des axes dans le total servi
_BUDGET_DESCRIPTION = 110   # caractères, par description d'axe

# ⚠️ **Exceptions NOMMÉES au plafond par description.** Un plafond qu'on relève pour
# tout le monde n'est plus un plafond : la dérive qu'on veut voir est celle qui arrive
# axe par axe, sans qu'on la décide. Une exception se demande ici, pour UN axe, avec sa
# raison et son coût — et les cinq autres restent tenus à 110 c.
#
# `_run_id` (#547, 29/08/2026) : sa description tenait en 55 c. (« run_id d'un
# `run_start` — le run ACTIF s'applique déjà. ») et cette concision était le bug. Elle
# disait au modèle qu'il pouvait omettre le jeton ; mesuré sur une campagne, `_run_id`
# était passé sur 140/140 réservations puis omis à l'écriture, et **31 écritures sur
# 100 étaient refusées**, toutes sur la ligne que l'appelant tenait lui-même. Trois
# faits qu'un lecteur de schéma ne peut pas déduire se paient donc ici : obligatoire,
# ce que coûte l'omission, l'exception nommée. Coût assumé : +135 c. sur 548 des 558
# outils, ~74 k c. de handshake — contre 31 lignes perdues sur 100.
_EXCEPTIONS_DESCRIPTION = {"_run_id": 200}


def _served() -> list[tuple[str, int, int]]:
    """(nom, poids servi, poids des axes) pour chaque outil du montage RÉEL.

    On monte ce que monte le boot — `register_all` — et on réinjecte les axes comme le
    fait le middleware : un banc qui mesure `t.parameters` nu mesurerait un document que
    personne ne reçoit.
    """
    from oto_mcp.tools import register_all

    m = FastMCP("budget")
    try:
        register_all(m)
    except Exception as e:  # deps optionnelles absentes en CI minimal
        pytest.skip(f"register_all indisponible: {e}")
    tools = asyncio.run(m.list_tools(run_middleware=False))
    out = []
    for t in tools:
        axes = call_axes.axes_for(t.name)
        served = {"name": t.name, "description": t.description or "",
                  "inputSchema": call_axes.inject_schema(t.parameters, axes)}
        cost = sum(len(json.dumps({a.param: a.schema}, ensure_ascii=False)) for a in axes)
        out.append((t.name, len(json.dumps(served, ensure_ascii=False)), cost))
    return out


def test_chaque_description_d_axe_reste_une_ligne():
    trop_longues = {
        a.param: len(a.schema.get("description", ""))
        for a in call_axes.AXES
        if len(a.schema.get("description", ""))
        > _EXCEPTIONS_DESCRIPTION.get(a.param, _BUDGET_DESCRIPTION)}
    assert not trop_longues, (
        f"description d'axe au-delà de {_BUDGET_DESCRIPTION} c. : {trop_longues}. "
        "Elle est recopiée dans ~400 schémas — le *pourquoi*, la marche à suivre et les "
        "clauses « omets pour… » vivent UNE fois, dans le bloc A (instructions.py). "
        "Ici : ce que l'axe fait, et l'outil qui liste ses valeurs. Si un axe DOIT être "
        "plus long, ajoute-le à `_EXCEPTIONS_DESCRIPTION` avec sa raison et son coût — "
        "l'exception se discute, le plafond général ne se relève pas."
    )


def test_les_exceptions_de_description_restent_l_exception():
    """Un dictionnaire d'exceptions qui grossit est un plafond qu'on a supprimé sans le
    dire. Deux garde-fous : il ne vise que des axes RÉELS, et il reste minoritaire."""
    params = {a.param for a in call_axes.AXES}
    inconnus = set(_EXCEPTIONS_DESCRIPTION) - params
    assert not inconnus, f"exception sur un axe qui n'existe pas : {inconnus}"
    assert len(_EXCEPTIONS_DESCRIPTION) <= len(params) // 3, (
        f"{len(_EXCEPTIONS_DESCRIPTION)} axes sur {len(params)} échappent au plafond — "
        "ce n'est plus une exception, c'est le régime.")


def test_les_jetons_de_contexte_tiennent_dans_leur_budget():
    rows = _served()
    axes = sum(c for _, _, c in rows)
    total = sum(t for _, t, _ in rows)
    par_outil = axes / max(len(rows), 1)
    assert par_outil <= _BUDGET_PAR_OUTIL, (
        f"les jetons `_*` coûtent {par_outil:.0f} c. PAR OUTIL (budget "
        f"{_BUDGET_PAR_OUTIL}) — {axes:,} c. sur {len(rows)} outils. Ce chiffre ne "
        "bouge pas quand le catalogue grandit : s'il monte, c'est que la prose des "
        "descriptions d'axe est revenue."
    )
    assert axes / total <= _BUDGET_PART, (
        f"les jetons `_*` sont {100 * axes / total:.1f} % du handshake "
        f"(budget {100 * _BUDGET_PART:.0f} %) : {axes:,} c. sur {total:,}."
    )


def test_un_axe_ne_s_advertise_pas_partout_par_defaut():
    # `applies` existe pour que l'axe n'apparaisse QUE là où il a un sens. Un prédicat
    # qui devient vrai partout (ou un axe neuf sans prédicat) multiplie le coût par la
    # surface entière — la régression est silencieuse, d'où la vérification.
    rows = _served()
    n_outils = len(rows)
    porteurs = {a.param: sum(1 for name, _, _ in rows if a in call_axes.axes_for(name))
                for a in call_axes.AXES}
    assert porteurs["_account"] < n_outils / 2, (
        "`_account` ne concerne que les connecteurs multi-compte : le voir sur la moitié "
        f"de la surface signale un prédicat trop large ({porteurs['_account']}/{n_outils})."
    )
