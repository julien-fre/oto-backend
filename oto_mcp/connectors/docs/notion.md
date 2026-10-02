## prerequisite — connecter notion

deux voies, le même résultat :
- **« connecter avec notion »** (quand le bouton est proposé) : un clic, l'écran notion où tu **choisis les pages et bases à partager**, un clic. rien à copier, le partage est fait au consentement
- **jeton d'intégration interne** : crée-la sur [notion.so/my-integrations](https://www.notion.so/my-integrations), récupère l'**internal integration token**, colle-le sur la carte du connecteur **notion** — puis **partage les pages/databases voulues avec ton intégration** dans notion (menu `...` → connexions), sinon elle ne voit rien

pour donner accès à d'autres pages plus tard : partage-les avec l'intégration dans notion (menu `...` → connexions), ou reconnecte et coche-les à l'écran de consentement.

## usage — ce que tu peux faire

lis et écris pages, databases et blocs notion partagés avec l'intégration.
- « retrouve la page roadmap » → `notion_search` — un zéro peut vouloir dire « rien n'est partagé avec l'intégration » : la réponse porte alors un `warning` qui dit comment trancher (relancer avec `query=""`). une réponse rend au plus 100 objets : `has_more: true` → repasse son `next_cursor` en `cursor` pour la page suivante
- « qu'est-ce qui a changé hier dans notion ? » → `notion_search` avec `query=""` et `edited_on="AAAA-MM-JJ"` (jour UTC) : tous les objets édités ce jour-là, en une réponse
- « liste les lignes de cette base où statut = à faire » → `notion_query_database` (avec filtre)
- « crée une page sous ce projet » → `notion_create_page`
- « ajoute ce paragraphe à la page » → `notion_append_blocks`
