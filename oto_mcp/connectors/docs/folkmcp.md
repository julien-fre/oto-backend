## prerequisite — connecter ton compte Folk (OAuth)

connecteur **fédéré** : au premier usage, tu es redirigé vers [Folk](https://folk.app) pour autoriser oto à agir sur ton workspace (OAuth, pas de clé à copier). tu agis alors **en ton nom**, avec tes propres droits Folk.
- distinct du connecteur `folk` natif (clé API partagée de l'org) : ici chaque personne connecte **son** compte
- url de callback : `{{callback:/api/folkmcp/oauth/callback}}`

## usage — piloter ton CRM Folk

le MCP officiel de Folk (outils fédérés `folkmcp_*`) : cherche, crée et mets à jour contacts, sociétés, deals et notes en langage naturel.
- « trouve la société Acme dans mon workspace »
- « crée un contact et rattache-le au groupe Fundraising »
- « fais avancer ce deal dans le pipeline »
