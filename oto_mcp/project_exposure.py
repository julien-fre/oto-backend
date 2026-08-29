"""Opt-ins d'exposition d'un projet publié — LA garde, lue par les DEUX faces.

Un projet publié sans login (`mcp_access ∈ {anonymous, secret}`) est servi par la
**même URL** sur deux faces, séparées par le seul `Accept:` de la requête
(`subdomain_project.HostDispatch._http`) :

- le **serveur MCP** (ce qu'un agent branche), qui décide de ce qu'il expose par
  `AnonContext` ;
- l'**UI web navigable** (`share_ui.build_page`), qu'un navigateur atteint sans rien
  installer.

Le consentement du propriétaire est un fait unique : il vit ici, et les deux faces le
lisent au même endroit. Fonctions **pures** (entrée = la ligne `projects`, sortie =
bool, aucun I/O) — appelables depuis le dispatch ASGI comme depuis le rendu HTML en
threadpool, et testables isolément.

⚠️ **#557 (2026-08-29, sévérité haute)** — pourquoi ce module existe. La face MCP
exigeait `secret` **et** l'opt-in explicite `mcp_expose_docs` avant de servir les
pages d'un projet ; la face web les listait et rendait leur corps entier **sans
consulter aucun des deux flags**. Un endpoint publié était donc annoncé par
l'annuaire public, ouvrable dans un navigateur, et livrait des notes internes que
son propriétaire croyait gardées — il avait lu la garde, côté MCP. Un test gravait
même la divergence. Deux décisions écrites à deux endroits ne restent pas d'accord :
celle qui diverge ne le montre jamais.
"""
from __future__ import annotations

from typing import Mapping


def docs_exposed(project: Mapping) -> bool:
    """Les PAGES du projet (`docs`) sont-elles exposées au destinataire ?

    Opt-in EXPLICITE (`mcp_expose_docs`), et `secret` seulement. Les pages d'un projet
    portent typiquement des notes internes (arbitrages, contacts, gotchas) — les
    exposer par défaut serait une fuite par surprise. Régime INVERSE du datastore
    ci-dessous, et c'est voulu : le datastore est le plus souvent le livrable qu'on
    partage, les pages sont de la doc interne.

    Vaut pour **toute** lecture de ces pages : l'outil `oto_doc` de la face MCP comme
    la vue humaine de la face web (index `/` et page `/docs/<id>`).
    """
    return (project.get("mcp_access") == "secret"
            and bool(project.get("mcp_expose_docs")))


def datastore_exposed(project: Mapping) -> bool:
    """Le DATASTORE lié au projet est-il exposé en LECTURE au destinataire ?

    Opt-in `mcp_expose_datastore`, `secret` seulement (défaut posé à la publication —
    #193 —, refermable). Un endpoint `anonymous` est public (annuaire) ; un endpoint
    `org` a déjà un membre authentifié qui résout `data_*` nativement.

    Gate les tools `data_*` de la face MCP **et** les tableaux navigables de la face
    web (index `/` et page `/data/<id>`).
    """
    return (project.get("mcp_access") == "secret"
            and bool(project.get("mcp_expose_datastore")))


def datastore_writable(project: Mapping) -> bool:
    """Opt-in ADDITIONNEL (#193) : l'ÉCRITURE du datastore. Sans objet si la lecture
    n'est pas exposée. Ne concerne que la face MCP — la face web est en lecture seule."""
    return (datastore_exposed(project)
            and bool(project.get("mcp_expose_datastore_write")))
