"""La description servie nomme le PROPRIÉTAIRE que le code donne vraiment.

Histoire courte, deux réparations le même jour (04/09/2026).
`data_create_namespace` annonçait à l'agent, dans le texte relu à chaque appel, un
identifiant « unique **per user** » — pendant que le code posait le tableau chez l'**org
active**, lisible de tous ses membres. Le geste le plus banal du produit créait du
contenu d'org sous une instruction qui promettait un espace à soi.

Le texte a été corrigé d'abord (`ab6d0eff`), puis le COMPORTEMENT (ADR 0068 : le
tableau naît personnel), ce qui a rendu le premier correctif obsolète en une journée.
C'est exactement le scénario que ce fichier existe pour rendre bruyant.

Ce banc ne relit donc pas une phrase : il lit le DÉFAUT réel dans le code, puis exige
que la description servie nomme ce défaut-là. Écrit dans l'autre sens, il se
périmerait au premier changement de comportement — **en restant vert**, ce qui est le
pire des cas. Il a d'ailleurs fait son travail : il a refusé de virer au vert quand le
défaut est passé à `user`, avant que le texte ne bouge.

⚠️ La description est lue sur le MONTAGE (`register` → `tools/list`), jamais sur le
`__doc__` de la fonction : c'est FastMCP qui décide de ce qui part sur le fil, et un
banc posé sur le docstring dirait que le texte est bien écrit sans rien dire de ce que
le modèle reçoit (même piège que `test_param_description_servie.py`).
"""
from __future__ import annotations

import asyncio
import inspect

from fastmcp import FastMCP


def _descriptions_servies() -> dict:
    from oto_mcp.tools import datastore as surface
    mcp = FastMCP("sonde")
    surface.register(mcp)

    async def _lire():
        # ⚠️ Espaces NORMALISÉS : la description est du texte enroulé, et une phrase
        # y traverse les retours à la ligne (« not\nthe other members »). Chercher un
        # bout de phrase dans la chaîne brute échoue pour une raison qui n'a rien à
        # voir avec ce qu'on teste — et se « corrige » en découpant l'assertion en
        # morceaux plus courts, jusqu'à ne plus rien vérifier.
        return {t.name: " ".join((t.description or "").split())
                for t in await mcp.list_tools(run_middleware=False)}

    return asyncio.run(_lire())


def test_le_defaut_de_propriete_du_datastore_est_la_PERSONNE():
    """Le fait, lu dans le code et non supposé — si ce banc tombe, c'est le
    COMPORTEMENT qui a changé, et la description du suivant doit changer avec lui."""
    from oto_mcp.datastore import core

    src = inspect.getsource(core.DatastorePg._default_owner)
    assert 'return ("user", self.sub)' in src, (
        "le défaut de propriété d'un tableau n'est plus la personne : mets à jour la "
        "description servie de `data_create_namespace` AVANT de rendre ce banc vert.")


def test_data_create_namespace_DIT_que_le_tableau_est_PRIVE():
    """Ce que l'agent lit doit être ce qui se passe. Et le dire au bon niveau de
    détail : « private » seul laisserait croire qu'un admin d'org y accède quand même
    — c'est précisément la question qu'on se pose devant un contenu sensible."""
    d = _descriptions_servies()["data_create_namespace"]
    assert "PRIVATE" in d, "le propriétaire réel doit être nommé, en clair"
    assert "not the other members" in d and "not its admins" in d, (
        "« privé » sans dire QUI est exclu se relit comme « privé, sauf les admins »")
    assert "per user" not in d, "la formule qui mentait ne doit pas revenir"


def test_le_texte_dit_aussi_comment_PARTAGER():
    """Un défaut privé sans issue rend le produit inutilisable à deux. La phrase nomme
    le geste inverse — c'est ce qui distingue « privé par défaut » de « fermé »."""
    d = _descriptions_servies()["data_create_namespace"]
    assert 'owner: {type: "org"|"group", id: N}' in d


def test_le_texte_dit_que_l_EXISTANT_ne_bouge_pas():
    """La bascule se voit le jour même. Un agent qui lit « privé » et retrouve un
    tableau d'org conclurait à un bug, ou pire, croirait privé ce qui ne l'est pas."""
    d = _descriptions_servies()["data_create_namespace"]
    assert "keep the owner they have" in d
