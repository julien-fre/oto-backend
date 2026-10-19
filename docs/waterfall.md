---
title: Les waterfalls natifs — email, téléphone, société, personne
type: explanation
description: >-
  Quatre connecteurs (`waterfall_email`, `waterfall_phone`, `waterfall_company`,
  `waterfall_person`), un outil chacun, servis par un seul module : une cascade de
  fournisseurs DÉJÀ connectés, arrêtée au premier trouvé, où chaque étape est un appel
  ordinaire de l'outil du fournisseur (`meta.executer_cible`). D'où vient l'ordre (appel,
  réglage `steps` de la carte, défaut) et pourquoi les clés de l'appelant passent devant ;
  ce que la cascade garde de chaque étape (garde, clé, facturation, rédaction) ; l'étape
  asynchrone (lemlist) ; ce qu'elle refuse d'être. À lire avant de toucher
  `oto_mcp/tools/waterfall.py`.
---

# Les waterfalls natifs

## Le problème

Trouver l'email d'une personne, c'est essayer Hunter, puis Apollo, puis Kaspr… jusqu'au
premier qui répond. Un agent le fait à la main : un appel par fournisseur, une lecture
de chaque réponse dans son contexte, une décision « trouvé ou pas » à chaque fois — et il
paie des jetons pour un travail sans jugement. FullEnrich vend cette cascade, mais chez
lui, sur ses fournisseurs, à son prix ; l'org qui a déjà ses propres clés n'en profite pas.

## Le principe

Quatre connecteurs, un outil `<ns>` chacun (lecteurs seuls, donc pas de `<ns>_act` :
convention du 17/10), une table d'étapes chacun (`EMAIL_STEPS`, `PHONE_STEPS`,
`COMPANY_STEPS`, `PERSON_STEPS`), un seul module (`Connector.modules=("waterfall",)`,
que `register_all` ne charge qu'une fois). Une étape = un outil de connecteur existant,
les entrées qu'il exige, la façon de construire ses arguments et de lire sa réponse. La
cascade s'arrête au **premier** trouvé (choix produit : le moins cher, pas le plus sûr —
l'email n'est pas vérifié ici).

⚠️ **Un nom de connecteur multi-token qui ne sert que `<ns>`** : `namespace_of` excluait
le nom ENTIER des candidats, si bien que `waterfall_email` se résolvait à `waterfall` —
hors de tout connecteur, donc hors gate d'activation, en silence (et
`test_convention_act` l'aurait laissé passer : il écarte les outils sans connecteur). Le
nom entier est désormais un candidat ; aucun outil existant ne change de namespace.

**Chaque étape passe par `meta.executer_cible`**, la couture d'`oto_call` et des
recettes. Elle garde donc tout ce qu'un appel direct a : la garde d'activation du
connecteur pour l'org et l'équipe de l'appel, la cascade de clé, le niveau d'accès, la
liste d'outils d'un travail hébergé, **sa** ligne dans `tool_calls` (donc sa `quantity`
et son `key_mode` : la facturation reste sur la ligne du fournisseur, jamais sur celle
du waterfall), et la rédaction de **son** namespace. Le waterfall n'a ni clé ni
facturation propres.

## D'où vient l'ordre

Trois couches, la plus spécifique gagne :

1. `providers=[…]` sur l'appel — validé : une clé inconnue est la faute de l'appelant ;
2. le réglage **`steps`** de la carte du connecteur — un `CredentialField(secret=False)`
   sur une carte `secret_kind="settings"` (19/10/2026 : « cette carte ne porte que des
   réglages FACULTATIFS, elle marche sans rien » — même formulaire que `fields`, méthode
   d'auth `secret`, mais un front y lit « inclus, rien à connecter » au lieu de « non
   connecté »)
   (un réglage, jamais un secret), posé par un admin d'org (défaut de l'org) ou par un
   membre pour lui-même (sa surcharge), résolu par la même cascade qu'une clé
   (`access.resolve_credential(ns, want="byo", emit_on_failure=False)` : sans réglage,
   aucun événement de monitoring n'est émis). Rendu par le formulaire générique des
   champs de connecteur : aucun travail de front. Une clé inconnue y est IGNORÉE et
   nommée (`ignored_steps`) : celui qui appelle ne l'a pas écrite ;
3. la table par défaut.

Sur l'ordre PAR DÉFAUT seulement, **les fournisseurs pour lesquels l'appelant détient SA
clé passent devant**, avec les étapes GRATUITES (`Step.free` : registre français, session
LinkedIn de l'appelant — que `credential_mode_for` dit `platform` et qu'un tri par clé
aurait placées derrière une clé payante)
(utilisateur, équipe, org, tenant — `access.credential_mode_for`, lu une fois par
porteur et par appel, AVANT toute dépense), les clés partagées de la plateforme ensuite,
chaque groupe dans l'ordre de la table (tri stable). Un ordre ÉCRIT — celui de l'appel ou
du réglage — est gardé tel quel : quelqu'un l'a choisi. Un fournisseur sans clé utilisable
(`forbidden`, `over_quota`) est sauté sans être appelé. ⚠️ Ce mode est une lecture
proche de la résolution réelle, pas exacte (il ignore un `_instance` épinglé, un compte
nommé, l'option payante) : il ORDONNE, il ne garde rien — la garde reste l'appel de
l'étape.

## Ce qu'une étape qui échoue fait : rien d'autre que passer la main

Une garde qui refuse (`McpError` levée par `executer_cible` : connecteur coupé, hors
liste du travail), un échec du fournisseur (`IssueCible(ok=False)` : clé refusée —
classée `invalid_input`, il n'existe pas de code « non connecté » —, compte vide, limite
de débit, panne), un résultat retenu par la rédaction, une réponse sans valeur : tout est
écrit dans `attempts` (statut, code, message, mode de clé) et l'étape suivante est
essayée. Un `not_found` (404) est un non-trouvé, pas un échec. Seules les fautes de
l'appelant lèvent : aucune étape possible avec ces entrées, une clé de `providers`
inconnue, un SIREN malformé. Le tout tient en **40 s** (`DEADLINE_S`) : sous les 45 s du
chemin REST d'invocation (`capabilities/tools_me.py`, la borne la plus serrée qu'un
appelant rencontre — MCP : 120 s) ; une étape qui partirait après est sautée
(`time_budget`).

⚠️ **Un trouvé se juge à la FORME de la valeur** : une adresse qui a la forme d'un email,
un numéro d'au moins six chiffres, un profil qui nomme un titre ou une société. Un
fournisseur qui renvoie l'entrée qu'on lui a donnée n'a rien trouvé ; un `person._stub`
d'Apollo (fiche vide, pourtant facturée) non plus ; une ligne Lusha porteuse d'`error`
non plus. ⚠️ **Une recherche rend toujours quelque chose** : `fr_search`, AI Ark par nom,
TheirStack, Cognism ne comptent que si le domaine correspond ou si le nom demandé est
contenu dans celui du résultat — un homonyme arrêtant la cascade cacherait la bonne
réponse plus bas.

## Les étapes asynchrones : lemlist, Dropcontact, FullEnrich, la révélation Apollo

`lemlist_enrich` / `dropcontact_enrich` / `fullenrich_enrich_linkedin` /
`apollo_reveal_phone` soumettent et rendent un identifiant ; leur outil de relève
(gratuit) lit le résultat. ⚠️ FullEnrich met d'ordinaire 30 s à 4 min : dans un appel,
il finit le plus souvent `pending`. L'étape soumet, attend ~8 s, relève toutes les ~6 s pendant au plus 25 s
(`POLL_*`). Fini à temps → lu comme une étape ordinaire ; un `done` à digest vide qui
porte un `warning` n'est pas final (lemlist demande une relève de plus). Pas fini → l'étape
est `pending`, la réponse donne l'appel exact à faire plus tard (`pending[]`), et la
cascade continue — les crédits lemlist de cette soumission sont dépensés quoi qu'il arrive.
lemlist est BYO seulement : sans clé de l'appelant, l'étape est sautée sans appel.

## Ce que le waterfall refuse d'être

- **Un appel d'outil arbitraire** : `providers` et `steps` ne nomment que des clés de la
  table, jamais un outil. C'est ce qui tient une écriture (ou un waterfall lui-même) hors
  d'une cascade.
- **Un lot** : une personne par appel. Une liste passe par une recette (`oto_recipe`,
  `for_each`) — d'où la déclaration `LECTURE` des outils : ils ne changent rien chez
  aucun tiers, même si leurs étapes dépensent des crédits. ⚠️ La recette ne juge que le
  waterfall ; ses étapes, appelées par `executer_cible`, ne sont pas rejugées « en
  lecture » — ce sont toutes des lectures d'enrichissement (`lemlist_enrich` écrit une
  demande d'enrichissement chez lemlist, rien qu'un tiers voit), et c'est la table fermée
  qui le garantit.
- **Hors `BETA_TOOLS`** : quatre noms neufs, mais des connecteurs fermés par défaut
  (`default_active=False`) — l'activation par la plateforme fait office de population.

## Les étapes

- **Email** : hunter → apollo → aiark → kaspr → forager → lusha → dropcontact → lemlist →
  fullenrich (les asynchrones en dernier).
- **Téléphone** : kaspr → forager → lusha → apollo (`apollo_reveal_phone`, clé propre) →
  lemlist → dropcontact → fullenrich (pas d'AI Ark ; les asynchrones en dernier).
- **Société** : `fr_get` (SIREN) → `fr_search` (nom, France, gratuit) → aiark
  (`linkedin_aiark_search op=companies`, domaine ou nom) → apollo (domaine) → theirstack
  (domaine) → cognism (domaine ou nom).
- **Personne** : linkedin (`linkedin_unipile_profile`, le compte LinkedIn connecté de
  l'appelant, gratuit) → aiark (export) → apollo → forager (`op=detail`) → cognism →
  lusha (recherche sans `reveal`, le mode bon marché) → lemlist (`linkedin_enrichment`).
- **Lus par des noms de clés connus, faute de forme figée par un test ici** : Kaspr,
  Forager, Lusha (email/téléphone par extraction profonde), et pour la personne et la
  société Forager, Cognism, Lusha, Unipile, le profil LinkedIn brut de lemlist, le domaine
  d'une société AI Ark. À confirmer sur une réponse réelle avant de s'y fier — et à figer
  alors par un test de son extraction.
- **Absents, et pourquoi** : HitHorizons (deux appels par société, aucune forme connue),
  Topograph et Crunchbase (un « introuvable » ne s'y distingue pas d'une panne :
  `internal`).

## Ajouter une étape

Une ligne `Step(...)` dans la table : la clé, l'outil (déjà monté — vérifié par
`tests/test_waterfall.py`), les groupes d'entrées exigées, `build`, `extract` (qui lit le
résultat SERVI, après rédaction), une note de coût, et `collect` si le fournisseur répond
plus tard. Un test de son extraction sur une réponse réelle du fournisseur, anonymisée.
La clé devient aussitôt utilisable dans le réglage `steps` de la carte.
