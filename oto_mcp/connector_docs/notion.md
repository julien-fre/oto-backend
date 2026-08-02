## prerequisite — ton token d'intégration notion

notion s'ouvre via une **intégration interne**. crée-la sur [notion.so/my-integrations](https://www.notion.so/my-integrations), récupère l'**internal integration token**.
- **partage les pages/databases voulues avec ton intégration** dans notion (menu `...` → connexions) — sinon elle ne voit rien
- colle le token dans oto sur ton compte (`/account`), connecteur **notion**

## usage — ce que tu peux faire

lis et écris pages, databases et blocs notion partagés avec ton intégration.
- « retrouve la page roadmap » → `notion_search`
- « liste les lignes de cette base où statut = à faire » → `notion_query_database` (avec filtre)
- « crée une page sous ce projet » → `notion_create_page`
- « ajoute ce paragraphe à la page » → `notion_append_blocks`
