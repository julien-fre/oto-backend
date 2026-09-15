---
title: Charger un gros volume (réseau, export, enrichissement de masse)
description: déléguer à un sous-agent, garder les gros payloads hors contexte, ne remonter qu'un reçu léger
---

# Charger un gros volume sans saturer le contexte

À lire **avant** toute tâche qui va tirer beaucoup de données via oto : copier un
réseau LinkedIn complet (`linkedin_unipile_network(op="relations")`), exporter des milliers de lignes,
enrichir une longue liste, boucler sur de la pagination profonde. Ces opérations
renvoient des payloads qui **dépassent le plafond de tokens** d'un résultat d'outil
(ex. `linkedin_unipile_network(op="relations")` ≈ 70 Ko/page) et, traitées dans le contexte principal,
le polluent et coûtent cher.

## Le principe : délègue à un sous-agent, ne remonte qu'un reçu

Ne boucle **pas** un gros volume dans ta conversation. Lance un **sous-agent dédié**
qui :

1. **boucle** la récupération (pagination) et garde chaque gros payload **chez lui**,
   hors de ton contexte ;
2. **dédoublonne** par une clé stable (ex. `member_id`, `siren`, une URL) ;
3. **écrit** le résultat dans un tableau (datastore), pas dans sa réponse ;
4. te renvoie seulement un **reçu léger** — p.ex.
   `{distinct, doublons, pages, couverture, 5 exemples}`.

Bénéfice : ton contexte reste propre, le coût est borné, le run long ne te bloque pas.

## Ponter les outils : `oto_call`

⚠️ Un sous-agent que tu lances **hérite du registre d'outils figé** de ta session : si
un connecteur a été activé en cours de session, ses outils ne sont montés ni chez toi
ni chez lui. Le sous-agent doit donc les appeler via **`oto_call(name="…", arguments={…})}`**
(le pont universel), exactement comme toi. Pour un appel direct des nouveaux outils sans
`oto_call`, il faut une **session neuve**.

## Paginer proprement : le curseur

Pour relire un tableau volumineux, utilise le **curseur** de `data_rows` plutôt que de
tout tirer d'un coup : passe `limit`, lis `next_cursor` dans la réponse, et rappelle
`data_rows(cursor=<next_cursor>)` jusqu'à ce que `next_cursor` soit nul. Le curseur est
**stable** (les lignes écrites entre-temps ne décalent pas la pagination).

Côté source (ex. `linkedin_unipile_network(op="relations")`), pagine de même page par page ; n'accumule jamais
toutes les pages dans un seul message.

## Écrire en masse

- Beaucoup de lignes d'un coup : `data_write(datastore, rows=[…], key="<clé métier>")`
  en **lots** — la `key` dédoublonne (ré-écrire la même clé met à jour, ne duplique pas).
- Très gros volume / contenu lourd : demande une **URL d'upload** (`oto_upload_url`) et
  laisse le sous-agent y pousser le fichier côté serveur, sans faire transiter les octets
  par le contexte.

## Savoir quand tu as fini (convergence)

Ne conclus pas « fini » après une seule passe. Sur une source paginée dont l'ordre n'est
pas garanti (ex. relations LinkedIn), **itère les décalages** (grilles d'offset) jusqu'à
**deux passes consécutives sans aucune nouvelle ligne** — une seule passe en rate souvent
5–15 %. Récupère si possible la **cible autoritaire** (p.ex. `connections_count` du profil)
pour **mesurer ta couverture** et détecter un plateau réel vs un arrêt prématuré.

## Côté client : le succès ne se présume pas

Un chemin qui peut rendre un succès sans avoir agi est un piège — dans ton script
autant que dans la plateforme. Sept règles pour toute passe en masse :

1. **Une réponse perdue LÈVE.** Un transport peut toujours faillir (timeout, connexion
   coupée) : une charge vide ou non parsable n'est jamais un succès — c'est « rien ne
   permet de savoir si l'appel a eu lieu ». (Exception légitime : une notification
   JSON-RPC sans `id` n'attend pas de réponse.)
2. **Une lecture interrompue LÈVE.** Un `break` qui imprime un avertissement puis rend
   l'inventaire partiel au reste du programme fabrique un « complet » qui n'existe pas.
   Suis `next_cursor` jusqu'à null ou échoue franchement.
3. **Vérifie l'EFFET, jamais la forme du retour.** `isinstance(dict)` ne dit pas qu'une
   écriture a eu lieu ; relire la donnée en base (ou comparer `_updated_at`) le dit. Sur
   un lot : recompter côté serveur, avec le MÊME critère que l'écriture.
4. **Journalise la passe ENTIÈRE dans un fichier.** Une passe qui écrit des milliers de
   lignes et ne sort qu'à l'écran perd son diagnostic avec la fenêtre.
5. **Compte les lignes avant et après toute passe.** Un invariant de population se
   vérifie en UNE seconde, sans dépendre d'aucune consigne ni d'aucune contrainte de
   schéma — et il lève l'alerte à la première minute là où la relecture la trouve six
   heures plus tard. (Vécu : 6 lignes créées dans le fichier d'une cliente — 4 fantômes
   d'API + 2 entreprises jamais confiées — détectables par un simple `count` avant/après.)
6. **Aucune correction de consigne ne part en volume sans avoir vu tourner UN agent.** La
   preuve la plus nette possible : la 2e famille de défauts de la nuit est NÉE de la
   correction de la 1re — « recopie le numéro d'identification dans la fiche » (posée à
   3 h du matin, jamais éprouvée) sans dire LEQUEL : un agent qui vient d'établir que le
   numéro fourni est faux recopie le sien en toute bonne foi, et comme c'est la clé du
   tableau, il crée une seconde ligne au lieu de corriger. Une correction est un
   déploiement : elle a son essai à une ligne avant son volume.
7. **Le complément du compte de lignes : l'empreinte des champs d'IMPORT.** Le compte ne
   voit que la création ; l'empreinte voit la déformation. Relève avant chaque vague un
   hash des champs que l'import a posés et qu'AUCUN agent n'a de raison de toucher
   (identifiants légaux, raison sociale, code d'activité, date de création, forme
   juridique) : une écriture qui atterrit sur la mauvaise ligne en déforme forcément un.
   Les deux témoins ensemble ne dépendent d'aucune consigne ni d'aucun schéma, et
   coûtent une lecture chacun.

## Reçu type à remonter

> Réseau chargé : **2942** distinct sur ~3010 annoncés (couverture 98 %), 4 pages ×
> 4 grilles d'offset, 63 doublons écartés. Écrit dans `linkedin-reseau-n1`. Exemples :
> …
