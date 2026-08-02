## prerequisite — connecte slack via le dashboard

slack se connecte **depuis le dashboard oto** : clique sur **connect** sur le connecteur **slack** et autorise via l'écran oauth de [slack](https://slack.com).
- pas de clé à copier à la main : oto récupère ton **user token** (`xoxp-`)
- les messages partent **en ton nom** (comme l'humain connecté), pas comme un bot
- à défaut, un admin peut te grant la clé plateforme de ton org

## usage — ce que tu peux faire

envoie et lis des messages slack en ton nom depuis claude.
- « envoie un message dans #general » → `slack_post_message`
- « dm jean par email » → `slack_find_user_by_email` puis `slack_open_dm` puis `slack_post_message`
- « lis les derniers messages de ce canal » → `slack_read_history`
- « réagis 👍 à ce message » → `slack_add_reaction`
