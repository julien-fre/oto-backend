---
title: Brancher oto sur Claude Tag (Slack)
description: la procédure complète pour qu'une équipe puisse mentionner @Claude dans Slack et lui donner accès à oto — jeton d'API, plugin, et ce qu'il faut savoir avant d'ouvrir un canal
---

# Brancher oto sur Claude Tag

**Claude Tag**, c'est Claude dans les canaux Slack d'une équipe : n'importe qui l'y mentionne et
lui confie du travail, dans le fil, sous les yeux du canal. Pour qu'il puisse se servir d'oto, il
faut lui donner deux choses — et **les deux sont nécessaires** :

| | Rôle |
| --- | --- |
| Un **jeton d'API oto** | Le laissez-passer. Il est stocké par Anthropic et ajouté aux requêtes à la sortie du réseau ; ni Claude ni son bac à sable ne le voient jamais. |
| Un **plugin** | La déclaration d'existence. Sans lui, Claude ignore qu'un serveur MCP se trouve à cette adresse et ne voit aucun outil oto. |

Ce n'est pas le connecteur oto de claude.ai ou de Claude Code, qui s'ajoute dans l'application avec
une authentification interactive. Dans Slack, cette authentification est impossible : Claude
travaille dans un bac à sable jetable, sans navigateur ni personne devant l'écran. D'où le jeton.

## La procédure

Une fois par organisation, par un *Owner* de l'organisation Claude (le rôle Admin ne suffit pas),
sur un plan Team ou Enterprise, avec Claude Tag déjà relié à Slack.

Tout se passe sur [`claude.ai/admin-settings/claude-tag`](https://claude.ai/admin-settings/claude-tag)
→ **Accès de Claude Tag** → onglet **Slack** → l'espace de travail concerné.



**1. Créer le jeton.** Sur `manage.oto.cx` → **développeurs** (menu du compte, en pied de barre
latérale) → créer un jeton. Il ne s'affiche qu'une fois.

**2. Déclarer la connexion.** Section **Connecteurs** de l'espace de travail → **+** :

| Champ | Valeur |
| --- | --- |
| Nom | `Oto` |
| Type d'identifiant | **Bearer** |
| Jeton | celui de l'étape 1 |
| Hôtes autorisés | `mcp.oto.cx` |

**3. Ajouter le plugin.** Section **Plugins** de l'espace de travail → **+**. Deux voies :
téléverser l'archive `oto-mcp.zip` (le plus simple), ou déclarer un dépôt privé comme source
d'organisation (la synchronisation devient automatique). Les deux donnent le même plugin
`oto-mcp` ; le zip est figé, le dépôt se met à jour seul.

**4. Essayer.** Dans un canal, `/invite @Claude`, puis dans un **fil neuf** :
`@Claude qui suis-je sur oto ?`



Claude doit répondre en nommant le compte et l'organisation du jeton.

## Attacher au bon niveau

Connexions et plugins s'attachent **directement sur l'espace de travail** — c'est la voie simple,
et celle qui couvre tous ses canaux d'un coup.

Les **packs d'accès** sont un niveau facultatif par-dessus : un jeu nommé de connexions,
attachable à plusieurs endroits, qui sert à donner des accès *différents selon le canal*. On n'en
a besoin que pour ça. Un pack créé mais attaché à aucun emplacement ne s'applique nulle part —
c'est la cause la plus fréquente d'une configuration qui semble correcte et ne produit rien.

## Ce qu'il faut dire à l'utilisateur avant

Le jeton porte **le compte qui l'a créé**. Dans les canaux couverts, Claude agira donc avec les
connecteurs de ce compte, sous son organisation par défaut — et **toute personne écrivant dans ces
canaux** pourra s'en servir à travers lui.

C'est le point à soulever spontanément, avant l'installation, pas après. Deux leviers, aucun n'est
le jeton lui-même :

- **le compte** : un compte dédié, avec les seuls accès nécessaires, plutôt qu'un compte personnel
  qui ouvre toute une boîte à outils ;
- **la portée Slack** : un espace de travail ou un canal précis plutôt que l'accès Slack par défaut.

## Quand ça ne marche pas

| Ce que rapporte l'utilisateur | Ce qui se passe | Correctif |
| --- | --- | --- |
| « il faut finaliser une autorisation OAuth » | Le jeton n'atteint pas le serveur : celui-ci répond 401 et le client MCP tente une authentification impossible dans le bac à sable | Vérifier le jeton et l'hôte de la connexion (ci-dessous) |
| « le proxy a refusé le CONNECT » (403) | `mcp.oto.cx` n'est autorisé par aucune connexion appliquée à ce canal | Hôte manquant, ou connexion posée dans un pack d'accès non attaché |
| Claude répond mais ne voit aucun outil oto | Le plugin n'est pas attaché à cette portée | Section Plugins de l'espace de travail |
| Une modification n'a aucun effet | Un fil garde connexions et plugins qu'il avait à son démarrage | Ouvrir un fil neuf |
| Claude répond mais ne trouve aucun connecteur | Le compte derrière le jeton n'en a pas encore | `manage.oto.cx` |

Deux pièges de saisie valent d'être connus : l'hôte autorisé s'écrit `mcp.oto.cx` (ni schéma, ni
chemin), et un en-tête `Authorization` posé à la main doit porter le préfixe `Bearer` — sans lui,
le serveur refuse. Le type **Bearer** natif s'en charge tout seul ; c'est une raison de plus de le
préférer.

## Révoquer

Sur `manage.oto.cx` → **développeurs**. La révocation est immédiate et n'affecte que ce jeton : ni
le compte, ni les autres intégrations. C'est le geste à recommander quand quelqu'un quitte
l'équipe, quand un canal s'ouvre plus largement, ou au moindre doute.
