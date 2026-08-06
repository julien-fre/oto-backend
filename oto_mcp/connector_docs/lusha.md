## prerequisite — obtenir une clé Lusha

crée une clé API dans le tableau de bord [Lusha](https://www.lusha.com), section paramètres API.
- colle-la dans tes connecteurs oto sur `/account`
- byo-only : pas de clé plateforme partagée, chaque org/personne pose la sienne
- Lusha facture en crédits : une recherche seule (`api_search`) coûte un crédit, et chaque champ révélé (`reveal`) en coûte un de plus PAR contact — surveille `billing.creditsCharged` dans la réponse avant de révéler un gros lot.

## usage — retrouver et révéler des contacts

recherche des contacts et débloque leurs emails/téléphones en un seul appel.
- `lusha_search_and_enrich` — jusqu'à 100 contacts par appel, identifiés par email, URL LinkedIn, ou nom + société. `reveal` contrôle ce qui se débloque (emails, téléphones, ou les deux) ; sans `reveal`, l'appel ne fait que rechercher/matcher, sans débloquer de donnée.
