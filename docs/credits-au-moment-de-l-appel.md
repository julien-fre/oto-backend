# Crédits comptés au moment de l'appel

Le chantier : compter ce qu'un appel consomme sur une clé partagée (plateforme ou tenant)
AU MOMENT de l'appel, dans la base du cœur, et non plus après coup en relisant le journal
`tool_calls`, qui est best-effort par conception (`docs/monitoring.md`). Doctrine
inchangée : **le commerce écrit, le cœur relit**. Le cœur ne tient ni prix en monnaie, ni
devise, ni facture, ni moyen de paiement (#1104) : seulement des unités.

Trois étapes :

1. **L8 — le règlement unique** (fait) : un seul point débite, au bord de l'appel.
2. **R6 — l'autorité du refus** (fait) : sous une arête `user` d'un connecteur chaîné
   (`grants_chain.CHAIN_CONNECTORS`), le refus lit le compteur d'arête (`grant_counters`).
   La fenêtre de double lecture est close là où la chaîne a un avis.
3. **Le budget d'unités** : un barème posé par le commerce, des mouvements idempotents
   versés à un payeur, une réservation au pire cas avant l'appel et un règlement au réel
   après. Il suppose les deux formes de `cost` du format des connecteurs (pire cas par
   élément de lot, quantité relue dans la réponse) et le délai de relève d'un travail
   asynchrone. Modes `off | report | enforce`, `off` par défaut.

## L8 : le règlement d'un appel (`access/reglement.py`)

Un outil ne débite plus rien lui-même. Trois rôles, trois endroits :

| qui | quoi | où |
| --- | --- | --- |
| l'outil | ce qu'il consomme : `units` au relevé d'appel | `session_org.note_call_trace(units=N)` |
| le résolveur | sous quelle clé l'appel part : `platform_carrier` (clé plateforme, hors connexion), `tenant_grant_ids` (arête de tenant) | `access/resolve.py`, `access/tenant_budget.py` |
| le bord | règle une fois, au SUCCÈS, sous le contexte de l'appel | `regler_appel` |

Les trois bords sont ceux qui posent un relevé d'appel : `middleware/call_context.py`
(appel MCP direct), `tools/meta.executer_cible` (cible d'`oto_call` et des recettes, avec
son propre relevé ; l'enveloppe ne reçoit jamais `CLES_REGLEMENT`) et `jev_jobs_worker.py`
(une tranche de fond). Une garde de test interdit tout autre appelant du débit.

- **`units` n'est pas `quantity`.** `quantity` est l'axe de facturation (les crédits du
  fournisseur, posés aussi sous la clé propre du client) ; `units` est l'axe du quota de la
  clé partagée. Les deux diffèrent volontairement sur plusieurs outils (le match de
  personne Apollo : quota 1, quantité = crédits ; la recherche AI Ark : quota 1,
  quantité = lignes rendues).
- **Débit constant.** L8 débite ce qui était débité avant, au même montant (un plancher à 1
  là où l'ancien compteur en avait un). Les outils qui ne débitaient rien sont déclarés
  dans `SANS_DEBIT` : passer l'un d'eux au débit est une décision, avec ses `units`.
- **Succès seulement.** Un appel qui lève ne règle rien.
- **Rien en silence.** Une clé plateforme servie sans `units`, par un outil absent de
  `SANS_DEBIT`, est journalisée (`units_absentes`, avec l'outil et le connecteur) et rien
  n'est débité. `tests/test_reglement_appel.py` tient chaque outil d'un connecteur à clé
  plateforme dans l'un des deux cas.
- **Un règlement ne défait pas un appel servi.** Un débit qui échoue est journalisé avec ce
  qu'il faut pour rattraper le compte à la main.
- **L'arête de tenant** est vérifiée à la résolution et débitée au succès, au même point.
  Hors relevé d'appel (face REST, maintenance), il n'y a pas de bord : elle est débitée à
  la résolution, comme avant.

## R6 : le compte que lit le refus (`quotas.platform_used_today`)

| l'arête qui sert | le compte du refus |
| --- | --- |
| `user` (connecteur chaîné) | son compteur d'arête, arêtes archivées comprises |
| `org` ou `group` | `usage(sub, tool, day)`, par personne |
| aucune (chaîne muette, connecteur non chaîné) | `usage(sub, tool, day)`, par personne |

Le refus, la sonde (`platform_quota_hint`), le mode affiché et l'instantané `/api/me`
lisent ce même nombre. Là où la chaîne accorde ou refuse, l'ancien chemin n'est plus lu,
et le journal d'écart (accès, quota, compteur) disparaît avec lui. Le compteur d'une
arête d'org continue d'être débité : c'est la consommation de l'org entière, lue par
l'administration des arêtes.

## Limites assumées

- **Sous une arête d'org ou de groupe, le refus reste lu dans `usage`**, compté par
  personne, comme avant : un compteur d'arête compte pour tous ses bénéficiaires, et le
  plafond d'une arête d'org est un plafond par personne. `grant_counters` n'a pas de
  colonne de bénéficiaire (`sub`) et n'en reçoit pas : le pot commun des crédits (étape 3)
  remplacera ce compte.

## Écarts connus, laissés pour l'étape 3

- **tavily** porte un plafond quotidien (100) que rien ne débite : ses outils sont dans
  `SANS_DEBIT`, le plafond n'arrête donc jamais rien. À trancher avec le barème.
- **Les comptes de `cognism_entitlement`, `cognism_filter_values` et
  `linkedin_aiark_credits`** : des lectures gratuites chez le fournisseur, qui débitent 1
  comme avant parce qu'elles passent par le même `_run`.
- **Une page vide de TheirStack** débite 1 sur le quota, alors que le fournisseur ne
  facture rien : l'ancien compteur plafonnait tout débit à 1 au minimum. Le montant est
  gardé tel quel.
- **Un outil composite** qui résout deux clés plateforme n'est réglé que sur la dernière
  (`platform_carrier` est une clé du relevé, pas une liste).
