## prerequisite — il te faut une app Slack installée sur ton workspace

oto ne fait pas d'écran oauth Slack : tu **colles les tokens** d'une app installée sur ton workspace. C'est une manœuvre unique par workspace, côté Slack.
- **bot token** (`xoxb-`) : lire les canaux, poster sous l'identité de l'app
- **user token** (`xoxp-`) : poster **en ton nom**, et chercher (`search:read` n'existe qu'en user token)
- l'un des deux suffit ; les deux ensemble = lecture par le bot + post en ton nom
- à défaut, un admin peut te grant la clé plateforme de ton org

## setup — où prendre les deux tokens

1. crée une app sur [api.slack.com/apps](https://api.slack.com/apps) (« from scratch »), en choisissant le workspace visé
2. dans la section des permissions oauth, déclare les scopes selon l'usage :
   - lire et poster comme l'app (**bot**) : `channels:read`, `groups:read`, `channels:history`, `groups:history`, `chat:write`, `users:read.email`, `im:write`
   - poster en ton nom (**user**) : les mêmes en version user, plus `search:read` si tu veux la recherche
3. installe l'app sur le workspace : Slack affiche alors le **Bot User OAuth Token** (`xoxb-`) et, si tu as demandé des scopes user, le **User OAuth Token** (`xoxp-`)
4. colle-les sur la fiche du connecteur slack dans oto — rien d'autre à configurer

⚠️ **un scope ne remplace pas l'appartenance au canal** : pour lire un canal privé (et un canal public dont il n'est pas membre), le bot doit y être invité. Sinon Slack répond `not_in_channel`, qui n'est pas un problème de token.

référence Slack : [installer une app avec oauth v2](https://api.slack.com/authentication/oauth-v2) · [choisir ses scopes](https://api.slack.com/scopes)

## setup — plusieurs workspaces, un compte par workspace

un token Slack est émis **par installation** : deux workspaces = deux jeux de tokens indépendants. Refais les étapes ci-dessus dans le second workspace, puis pose-les comme un **second compte nommé** du connecteur (un nom par workspace, ex. `otomata`, `client-x`).
- le premier compte n'a pas besoin de nom ; à partir du deuxième, chacun porte le sien
- viser l'un d'eux à l'appel : `_account="<nom>"` sur l'outil (`oto_identity(op='list')` pour les lister)
- en fixer un par défaut : `oto_identity(op='set', connector='slack', identity_id='<nom>')`
- avec un seul compte posé, rien à préciser — il est servi automatiquement

## usage — ce que tu peux faire

envoie et lis des messages slack en ton nom depuis claude.
- « envoie un message dans #general » → `slack_post_message`
- « dm jean par email » → `slack_find_user_by_email` puis `slack_open_dm` puis `slack_post_message`
- « lis les derniers messages de ce canal » → `slack_read_history`
- « réagis 👍 à ce message » → `slack_add_reaction`
