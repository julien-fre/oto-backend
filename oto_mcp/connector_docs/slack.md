## prerequisite — il te faut une app Slack installée sur ton workspace

oto n'a pas encore d'app Slack publiée (« connecter en un clic ») : tu crées **ta** app dans ton workspace et tu colles ses tokens ici. Une manœuvre unique par workspace, ~5 minutes.
- **bot token** (`xoxb-`) : lire les canaux, poster sous l'identité de l'app. C'est le token nominal.
- **user token** (`xoxp-`) : poster **en ton nom**, et chercher (`search:read` n'existe qu'en user token). Optionnel.
- l'un des deux suffit ; les deux ensemble = lecture par le bot + post en ton nom
- à défaut, un admin peut te grant la clé plateforme de ton org

## setup — créer l'app en collant un manifeste (le plus court)

1. va sur [api.slack.com/apps](https://api.slack.com/apps) → **Create New App** → choisis **From a manifest** (pas « AI agent » ni « Starter app » : ce sont des gabarits d'app conversationnelle, sans rapport)
2. choisis le workspace. L'éditeur qui s'ouvre est **déjà rempli** d'un exemple (`display_information: name: Demo App`) : **sélectionne tout et remplace-le** — coller par-dessus sans vider soude les deux manifestes et Slack refuse (« Nested mappings are not allowed in compact mappings »). Le manifeste à mettre à la place, qui déclare déjà tous les scopes dont les outils oto ont besoin :

```yaml
display_information:
  name: Oto
  description: Lit et écrit dans Slack pour votre agent Oto
features:
  bot_user:
    display_name: Oto
    always_online: false
oauth_config:
  scopes:
    bot:
      - channels:read
      - channels:history
      - groups:read
      - groups:history
      - im:read
      - im:history
      - im:write
      - mpim:read
      - mpim:history
      - users:read
      - users:read.email
      - chat:write
      - reactions:write
      - files:read
    user:
      - search:read
      - chat:write
settings:
  org_deploy_enabled: false
  socket_mode_enabled: false
  token_rotation_enabled: false
```

3. **Create**, puis **Install to Workspace** et autorise
4. dans **OAuth & Permissions**, copie le **Bot User OAuth Token** (`xoxb-`) et, si tu veux poster en ton nom, le **User OAuth Token** (`xoxp-`)
5. colle-les ici — rien d'autre à configurer

*(sans manifeste : **Blank app**, puis déclare les mêmes scopes à la main dans OAuth & Permissions avant d'installer. Le manifeste évite exactement cette étape.)*

⚠️ **un scope ne remplace pas l'appartenance au canal** : pour lire un canal privé — et un canal public où il n'est pas — le bot doit y être **invité** (`/invite @Oto`). Sinon Slack répond `not_in_channel`, ce qui ressemble à tort à un problème de token.

référence Slack : [créer une app depuis un manifeste](https://api.slack.com/reference/manifests) · [installer avec oauth v2](https://api.slack.com/authentication/oauth-v2)

## setup — plusieurs workspaces, un compte par workspace

un token Slack est émis **par installation** : deux workspaces = deux jeux de tokens indépendants. Refais l'installation dans le second workspace (le même manifeste), puis pose ses tokens comme un **second compte nommé** du connecteur (un nom par workspace, ex. `otomata`, `client-x`).
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
