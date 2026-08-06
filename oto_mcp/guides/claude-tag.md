---
title: Brancher oto sur Claude Tag (Slack)
description: la procédure complète pour qu'un utilisateur puisse mentionner @Claude dans Slack et lui donner accès à oto — jeton d'API, plugin, pack d'accès, et ce qu'il faut savoir avant d'ouvrir un canal
---

# Brancher oto sur Claude Tag

**Claude Tag**, c'est Claude dans les canaux Slack d'une équipe : n'importe qui l'y mentionne et
lui confie du travail, dans le fil, sous les yeux du canal. Pour qu'il puisse se servir d'oto, il
faut lui donner deux choses — et **les deux sont nécessaires** :

| | Rôle |
| --- | --- |
| Un **jeton d'API oto** | Le laissez-passer. Il est stocké par Anthropic et ajouté aux requêtes à la sortie du réseau ; ni Claude ni son bac à sable ne le voient jamais. |
| Un **plugin** | La déclaration d'existence. Sans lui, Claude ignore qu'un serveur MCP se trouve à cette adresse et ne voit aucun outil oto. |

Ce n'est pas la même chose que le connecteur oto de claude.ai ou de Claude Code, qui s'ajoute
directement dans l'application avec une authentification interactive. Dans Slack, cette
authentification est impossible : Claude travaille dans un bac à sable jetable, sans navigateur ni
personne devant l'écran.

## La procédure

Elle se fait **une fois par organisation**, par quelqu'un qui est *Owner* de son organisation
Claude (le rôle Admin ne suffit pas), sur un plan Team ou Enterprise, avec Claude Tag déjà relié à
Slack.

1. **Copier le template.** Le dépôt <https://github.com/otomata-tech/oto-claude-tag-template> →
   *Use this template* → **Private**. Claude Tag n'accepte comme source de plugins que des dépôts
   privés ou internes : c'est pourquoi chacun a sa copie. Vérifier ensuite que la *Claude GitHub
   App* a accès à ce dépôt, sinon il n'apparaîtra pas dans la liste.
2. **Créer le jeton.** Sur `manage.oto.cx` → **développeurs** → créer un jeton. Il ne s'affiche
   qu'une fois.
3. **Déclarer la connexion.** Réglages d'administration → Claude Tag → pack d'accès → onglet
   *Identifiants* → *Connecter une autre application* : type **Bearer**, le jeton, et
   `mcp.oto.cx` en **sites web autorisés**.
4. **Activer le plugin.** Onglet *Plugins* du pack d'accès → *Gérer les plugins de l'organisation*
   → ajouter le dépôt de l'étape 1 comme source → revenir au pack et activer `oto-mcp`.
5. **Attacher le pack** à un canal (ou au scope par défaut) — non attaché, il ne s'applique nulle
   part — puis, dans un **fil neuf** : `/invite @Claude` et `@Claude qui suis-je sur oto ?`.

## Ce qu'il faut dire à l'utilisateur avant

Le jeton porte **le compte qui l'a créé**. Dans les canaux couverts, Claude agira donc avec les
connecteurs de ce compte, sous son organisation par défaut — et **toute personne écrivant dans ces
canaux** pourra s'en servir à travers lui.

C'est le point à soulever spontanément, avant l'installation, pas après. Deux leviers, aucun n'est
le jeton lui-même :

- **le compte** : un compte dédié, avec les seuls accès nécessaires, plutôt qu'un compte personnel
  qui ouvre toute une boîte à outils ;
- **le scope Slack** : un pack d'accès attaché à un canal précis plutôt qu'à « Accès Slack par
  défaut ».

## Quand ça ne marche pas

| Ce que rapporte l'utilisateur | Ce qui se passe | Correctif |
| --- | --- | --- |
| Claude dit qu'il lui faut une authentification interactive | Le jeton n'atteint pas le serveur, qui répond 401 ; le client MCP tente alors une authentification impossible dans le bac à sable | Vérifier que `mcp.oto.cx` est bien dans les sites web autorisés de la connexion |
| Claude dit qu'il ne peut pas joindre l'hôte | Même cause : un hôte non listé est bloqué avant l'envoi | Idem |
| Claude répond mais ne voit aucun outil oto | Plugin non activé sur le pack, ou pack attaché nulle part | Étapes 4 et 5 |
| Une modification n'a aucun effet | Un fil garde les connexions et plugins qu'il avait à son démarrage | Ouvrir un fil neuf |
| Claude répond mais ne trouve aucun connecteur | Le compte derrière le jeton n'en a pas encore | `manage.oto.cx` |

## Révoquer

Sur `manage.oto.cx` → **développeurs**. La révocation est immédiate et n'affecte que ce jeton : ni
le compte, ni les autres intégrations. C'est le geste à recommander quand quelqu'un quitte
l'équipe, quand un canal est ouvert plus largement, ou au moindre doute.
