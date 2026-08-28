## prerequisite — capturer ta session crunchbase (cookie)

Crunchbase n'a pas de clé API publique : oto rejoue ta **session connectée**. capture les cookies de session (+ user-agent) de ton compte [Crunchbase](https://www.crunchbase.com) depuis la page **compte** du dashboard oto.
- sans session configurée, les outils `crunchbase_*` renvoient un message qui pointe vers la page compte

## usage — entreprises, financements et personnes crunchbase

récupère des données d'entreprises, leurs levées de fonds et des profils de personnes.
- « fiche Crunchbase de `anthropic` (effectif, localisation, fondateurs) »
- « liste les tours de financement de cette boîte (date, type, montant, investisseurs) »
- « cherche des entreprises sur `vector database` »
- « profil Crunchbase de cette personne »
