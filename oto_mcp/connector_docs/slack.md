## prerequisite — pose tes tokens slack depuis le dashboard

slack se connecte **depuis le dashboard oto**, en collant les tokens d'une app slack installée sur ton workspace (il n'y a **pas** d'écran oauth « connect » côté oto).
- **bot token** (`xoxb-`) : lecture des canaux + messages postés sous l'identité de l'app
- **user token** (`xoxp-`) : messages postés **en ton nom**, et recherche (`search:read` est un scope user token)
- l'un des deux suffit ; les deux ensemble donnent la lecture bot + le post en ton nom
- scopes minimum pour lire : `channels:read`, `groups:read`, `channels:history`, `groups:history`
- à défaut, un admin peut te grant la clé plateforme de ton org

## plusieurs workspaces — un compte par workspace

un token slack est émis **par installation** de l'app dans un workspace : deux workspaces = deux jeux de tokens, indépendants.
- pose-les comme **deux comptes nommés** du connecteur (un nom par workspace, ex. `otomata`, `client-x`)
- choisis-en un à l'appel avec `_account=<nom>`, ou fixe le compte par défaut (`oto_identity op=set`)
- avec un seul compte posé, rien à préciser : il est servi automatiquement

## usage — ce que tu peux faire

envoie et lis des messages slack en ton nom depuis claude.
- « envoie un message dans #general » → `slack_post_message`
- « dm jean par email » → `slack_find_user_by_email` puis `slack_open_dm` puis `slack_post_message`
- « lis les derniers messages de ce canal » → `slack_read_history`
- « réagis 👍 à ce message » → `slack_add_reaction`
