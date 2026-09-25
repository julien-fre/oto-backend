## prerequisite — clé api o*net web services

crée un compte développeur gratuit sur [services.onetcenter.org](https://services.onetcenter.org) (Sign up), génère une clé dans My Account, puis colle-la dans oto.
- byo-only : pas de clé oto partagée — la clé est nominative, ses conditions d'usage sont acceptées par son titulaire
- pas de plafond dur annoncé, mais un 429 quand le service est saturé : réessayer après un court délai

## usage — trouver un métier us et lire sa fiche

le référentiel des métiers du Department of Labor, en un tool :
- « quel est le code du métier de data engineer ? » → `onet_occupation(op="search", keyword="data engineer")` — les plus proches d'abord ; un code, même partiel, se cherche aussi (`keyword="15-12"`)
- « que fait un ingénieur civil ? » → `onet_occupation(op="get", code="17-2051.00")` — titre, description, intitulés de poste réellement rencontrés (`sample_of_reported_titles`), tâches (`tasks`, `limit` en élargit le nombre), métiers détaillés rattachés (`also_see`)

## note — ⚠️ codes et limites

- un code O\*NET-SOC a 8 chiffres (`15-1299.08`) ; ses 6 premiers (`15-1299`) sont le code SOC sous lequel les statistiques de salaires américaines sont publiées — plusieurs métiers O\*NET partagent donc les mêmes chiffres de salaire
- tous les métiers ne portent pas tous les champs : un métier sans tâches rend `tasks: []`, ce n'est pas une erreur
- `full=True` rend en plus les liens de navigation de l'API (inutiles à un agent, retirés par défaut)
- **écrit d'après le manuel de référence v2.0, pas encore exercé en live** (aucune clé disponible à l'écriture) : au premier usage réel, signaler tout écart de forme
