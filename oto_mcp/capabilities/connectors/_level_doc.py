"""Description partagée de l'échelle à cinq crans (oto-backend#775), posée dans
un module NEUTRE plutôt qu'importée d'une capacité vers une autre : un import
`verify.py -> instances.py` accouplerait leur ordre d'IMPORT, donc leur ordre
d'ENREGISTREMENT dans `CAPABILITIES` (chaque module s'enregistre par décorateur à
l'import), donc l'ordre de la table de routes REST — cliquet
`tests/api/test_api_routes_table_frozen.py`. Ce module ne définit AUCUNE
capacité : il ne peut rien décaler.

`tenant` est le compte de plus haut niveau isolé CHEZ LE FOURNISSEUR — donc
AU-DESSUS de l'organisation qui lit, pas en dessous. Ne concerne que ceux qui
accèdent à oto via un partenaire qui le sert sous sa marque (« hébergeur » côté
doc publique, même notion) : ce partenaire peut poser des clés partagées pour
toutes les organisations qu'il héberge. Rien à renommer (cf. rectificatif du
06/09/2026 sur l'issue) — seule la définition manquait.

⚠️ **DEUX textes, parce que les crans ne s'écrivent pas pareil des deux côtés.**
Le cran le plus proche s'appelle `member` sur un `level` (`ConnectorInstance`,
`VerifyResult`, le filtre de `list`) et `user` sur un `InstanceOwner.type` — même
barreau, deux orthographes servies (`instances.py` : `level='member'` est posé
avec `owner={'type': 'user'}`). Un texte unique pour les deux énumérait donc une
valeur qui n'existe pas là où il était servi, et laissait l'autre sans définition.
La définition de `tenant`, elle, reste UNIQUE (`DOC_TENANT`) : c'est elle qu'on ne
veut pas voir diverger.
"""
from __future__ import annotations

DOC_TENANT = (
    "`tenant` désigne le compte de PLUS HAUT NIVEAU, isolé chez le fournisseur — "
    "au-dessus de l'organisation qui lit, pas en dessous : ne concerne que les "
    "comptes qui accèdent à oto via un partenaire qui le sert sous sa propre marque "
    "(appelé « hébergeur » dans la doc publique, même notion), et qui peut poser des "
    "clés partagées pour toutes les organisations qu'il héberge."
)

# Servi sur les champs `level` — dont l'énumération s'ouvre sur `member`.
DOC_LEVEL = (
    "Rang de proximité dans la cascade de résolution : `member` (la clé de "
    "l'appelant lui-même) < `group` (son équipe) < `org` (son organisation) < "
    "`tenant` < `platform` (clé oto par défaut). " + DOC_TENANT
)

# Servi sur `InstanceOwner.type` — même échelle, mais le cran le plus proche s'y
# écrit `user` (la personne), jamais `member`.
DOC_OWNER_TYPE = (
    "Palier qui POSSÈDE l'instance, le long de la même cascade de résolution : "
    "`user` (la personne elle-même, qui porte un sub) < `group` (une équipe) < "
    "`org` (une organisation) < `tenant` < `platform` (clé oto par défaut, sans "
    "id : elle est identifiée par son label, ADR 0044 §F). " + DOC_TENANT
)
