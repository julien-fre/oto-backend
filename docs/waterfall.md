---
title: Les waterfalls natifs — email, téléphone, société, personne, gens d'une société
type: explanation
description: >-
  Cinq connecteurs (`waterfall_email`, `waterfall_phone`, `waterfall_company`,
  `waterfall_person`, `waterfall_people`), un outil chacun, servis par un seul module : une cascade de
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

Cinq connecteurs, un outil `<ns>` chacun (lecteurs seuls, donc pas de `<ns>_act` :
convention du 17/10), une table d'étapes chacun (`EMAIL_STEPS`, `PHONE_STEPS`,
`COMPANY_STEPS`, `PERSON_STEPS`, `PEOPLE_STEPS`), un seul module (`Connector.modules=("waterfall",)`,
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

## Ce que l'ordre par défaut optimise

Gratuit → clé de l'appelant (Tulina ne facture rien — même si le fournisseur facture
l'org) → clé de Tulina la moins chère → la plus chère → les asynchrones (lentes, et
facturées même quand elles finissent `pending`). On ne demande jamais à une étape plus
d'unités facturées qu'elle n'en garde (AI Ark : `size` = la limite ; TheirStack : une
société ; une recherche AI Ark par domaine : une), et le plan sert les PLAFONDS
(`max_units`, `per_item`) et le coût typique d'une exécution Apify (`typical_usd`) pour qu'un écran ne montre jamais
un prix unitaire là où l'étape peut en facturer plusieurs.

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
non plus. ⚠️ **Une recherche rend toujours quelque chose** : le registre par nom, AI Ark par nom,
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

- **Email** : hunter → aiark (◎ 1 — sur une vraie liste, les mêmes emails qu'Apollo, ◎ 2,
  qui facture sa correspondance même sans email) → apollo → kaspr → lusha → dropcontact → lemlist → fullenrich →
  forager (les asynchrones vers la fin ; Forager en dernier : sur la clé de Tulina, une
  recherche d'email y coûte ◎ 11, deux fois un email FullEnrich).
- **Téléphone** : kaspr → lusha → apollo (`apollo_reveal_phone`, clé propre : 0 crédit
  Tulina) → forager (◎ 33 sur la clé de Tulina) → lemlist → dropcontact → fullenrich (◎ 60
  par numéro trouvé). Pas d'AI Ark. ⚠️ Le seul waterfall où le défaut peut dépenser ◎ 93 sur
  un non-trouvé (Forager + FullEnrich).
- **Société** : `fr` (le registre français, UNE ligne à deux entrées : `fr_get` par SIREN,
  sinon `fr_search` par nom, France seulement, gratuit) → aiark
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

## Les étapes de complétion : la page LinkedIn d'une société

**Une ligne, plusieurs entrées** (`Step.variants`, `_one_row`) : le registre (SIREN, sinon
nom) et la page LinkedIn (site, sinon nom) sont UNE étape chacun — un écran les montre une
fois, un réglage les nomme une fois ; les variantes tournent dans l'ordre, chacune quand
ses entrées sont là, et le reçu dit laquelle a répondu (`via`, `tried`).

Une étape `Step(fills="linkedin_url")` ne concourt pas au trouvé : après la cascade, elle
ne tourne que si la valeur trouvée n'a PAS ce champ, et y fusionne ce qu'elle trouve
(sans écraser). Une page trouvée alors que rien d'autre n'avait identifié la société fait
encore un trouvé, mince (`linkedin_url`, et ce que la recherche rend). Une ligne `linkedin`,
deux variantes, deux acteurs Apify, chacun une exécution bornée par `apify_run_sync` avec `timeout_secs` (25 s,
côté Apify) et un plafond de coût — un acteur coupé rend encore ses éléments partiels :

- `linkedin_site` (`vdrmota/contact-info-scraper`) : les liens LinkedIn du site de la
  société. ⚠️ Le site d'un grand groupe lie souvent la page du GROUPE (`linkedin_source`
  le dit : `website`).
- `linkedin_search` (`harvestapi/linkedin-company`, `searches` — pas `companies`, qui
  attend des URL) : le premier résultat d'une recherche par nom est souvent un homonyme ;
  gardé seulement si son site est le domaine, ou, sans domaine, si son nom est celui
  demandé.

Appris sur une vraie liste (un salon professionnel, octobre 2026) : ces deux étapes ont
retrouvé les pages que les fournisseurs de données n'avaient pas, pour quelques centimes.

## Les gens d'une société (`waterfall_people`)

⚠️ **Des noms masqués ne stoppent pas la cascade** (`Step.weak`) : la recherche d'Apollo
est gratuite mais rend des noms de famille masqués ; elle est gardée comme REPLI, la
cascade continue (google → aiark → employés LinkedIn), et ce repli n'est rendu que si rien
de mieux ne vient. Le reçu marque l'étape `weak`.

Une société en entrée (domaine, nom, page LinkedIn), des `roles` facultatifs, une
`limit` (≤ 25) ; la première source qui rend au moins une personne gagne. Une personne
n'est gardée que si elle a un nom et si son titre correspond à l'un des `roles` (quand il
y en a) : apollo (recherche gratuite ; les noms y sont masqués — on révèle par
`apollo_id`, JAMAIS par nom + société, qui facturerait une fiche vide) → aiark (domaine ;
pas de filtre de titre côté AI Ark : il est appliqué ici) → `linkedin_employees`
(`harvestapi/linkedin-company-employees`, les employés de la page LinkedIn — titres
actuels, souvent plus frais que les fournisseurs ; ⚠️ une page tire des gens dont le
poste ACTUEL est ailleurs, et des faux profils : gardés seulement si
`currentPosition[0].companyUniversalName` est la page demandée) → google (`serper_search`
sur `linkedin.com/in`, un titre « Nom – Poste – Société | LinkedIn », gardé seulement s'il
nomme la société).

## Essai réel (19/10/2026, exposants d'un salon professionnel)

Ce que les vraies réponses ont appris, et qui est désormais figé par des tests
(`test_reel_*`, réponses anonymisées) :

- ⚠️ **Apify refuse une exécution dont le plafond est sous 0,50 \$**
  (`max-total-charge-usd-below-minimum`). Décision (19/10) : **aucun plafond de coût par
  défaut** — une exécution est bornée par sa TAILLE (pages, résultats, profils = la
  `limit` de l'appel) et son DÉLAI (`timeout_secs`, côté Apify), bornes figées par les
  tests ; un écran montre le coût TYPIQUE (`typical_usd`).
- AI Ark écrit le statut d'un email en capitales (`VALID`).
- Apollo trouve des personnes sans email (`email_status: unavailable`) — facturées quand
  même — et rend leur URL LinkedIn encodée en pourcents : la personne rendue garde l'URL
  de l'appelant.
- Google : le `subtitle` (« Lieu · Titre · Société ») est fiable, le `title` ne l'est pas ;
  un extrait qui MENTIONNE les mots de la société n'en fait pas un employé. Sur un nom peu
  distinctif, la recherche ne rend que du bruit — d'où la société exigée dans le résultat.
- Une recherche LinkedIn par nom rend d'abord un homonyme (vérifié : une cimenterie pour
  une société d'IA) : la garde par domaine est indispensable.
- Les sources rendent les gens dans leur ordre (un ingénieur avant le CTO) : la cascade
  les range par séniorité avant d'appliquer la limite.
- lemlist a répondu en ~25 s (dans la fenêtre), FullEnrich en ~80 s (donc `pending`).
- Votre LinkedIn non connecté : l'étape gratuite est `skipped / not_connected`, pas un échec.

## Limites : aucune par défaut, l'agent en fixe une s'il le faut

Décision du 19/10/2026 : la cascade ne plafonne rien de son propre chef. Deux paramètres
optionnels, sur chacun des 5 outils, pour l'agent qui veut une limite :

- `max_paid_steps` — au plus N appels PAYANTS par recherche (les étapes gratuites ne
  comptent pas) ; les suivantes sont `skipped / max_paid_steps`.
- `apify_max_usd` — un plafond de coût par exécution Apify (≥ 0,50 \$, le minimum
  d'Apify), transmis tel quel.

Restent les bornes de forme, pas de dépense : la réponse (`limit` des gens, jusqu'à 100),
le délai de la cascade (40 s, la route REST coupe à 45 s) et celui d'une exécution Apify.

## Qualifier par Jev avant de dépenser

`waterfall_company`, `waterfall_person` et `waterfall_people` prennent `qualify` (une
rubrique Jev, la forme des `questions` de `jev_ask`) et `qualify_min`. Après la cascade,
UN appel `jev_items` (via `executer_cible` : gardes, facturation au coût réel) juge ce qui a
été trouvé : chaque résultat porte `qualification: {answers, model}` ; avec `qualify_min`,
les gens sous le seuil sont écartés (`dropped`), une société ou une personne reste avec
`passed`. Le but : qu'un agent ne lise pas 25 profils pour en garder 3, et qu'on ne paie
de révélation ou d'enrichissement que pour ceux qui passent.

- ⚠️ Jev est un tiers (OpenRouter puis TypeSafe) : seuls partent les champs d'un jugement
  (`_QUALIFY_FIELDS` — titre, séniorité, lieu, secteur, taille…), jamais un nom, un email,
  un téléphone ni une URL de profil. Figé par un test.
- Ça n'échoue jamais la recherche : pas de clé Jev (clé partagée du tenant ou de la
  plateforme), refus ou moins de 5 s restantes → résultats non qualifiés, et pourquoi.
- Ne compte pas dans `max_paid_steps`.
- Gens avec `qualify_min` (décision du 19/10) : qualifiés étape par étape ; un fournisseur
  dont personne ne passe ne clôt pas la cascade, le suivant est essayé. Un écarté retrouvé
  plus loin n'est pas rejugé. Les écartés reviennent TOUJOURS, en compact (`unqualified` :
  nom, titre, URL, réponses) — vérifier la rubrique ne repaie jamais une cascade ;
  `show_unqualified=true` rend les fiches entières. Limite connue : quand la société n'a
  simplement personne qui convienne, les fournisseurs suivants retrouvent les mêmes gens
  et sont payés pour rien — `max_paid_steps` est le frein.
- Moins de qualifiés que `limit` (décision du 19/10) : la cascade S'ARRÊTE et
  `continue_with` nomme les fournisseurs pas encore essayés — l'agent appelant décide s'ils
  valent d'être payés.
- Une société ou une personne est jugée une fois, à la fin (`passed`) : le fournisseur
  suivant ne trouverait que la même.

## Sur un tableau : `waterfall_<x>_act(op="update_rows")`

Un tableau s'enrichissait un appel par ligne : l'agent lisait la ligne, appelait la
cascade, relisait la trouvaille et la retapait — chaque ligne deux fois dans un contexte
de modèle, et chaque tour renvoyait la conversation. Le mode tableau
(`tools/waterfall_lignes.py`) prend les LIGNES (`row_ids` ou `filter`) et les COLONNES
(`inputs`, `output`, `status_column`) une fois ; le serveur lit, lance la MÊME cascade
(`_run_cascade`), écrit en retour, et ne rend que des comptes et des codes — le contrat
de `datastore/par_reference` (`docs/donnees-par-reference.md`), comme `lemlist_push_rows`.

- **Écrire, c'est `<ns>_act`** (la règle `<ns>`/`<ns>_act`, `tests/test_convention_act.py`) :
  op unique `update_rows`, annotations d'acte.
- **Une ligne se paie une fois** : seules les lignes à `status_column` vide sont prises ;
  chacune reçoit `found` (le fournisseur en commentaire), `no_match`, `unqualified` (gens
  sous `qualify_min`) ou `failed` (`invalid_input`). Vider l'état remet la ligne en jeu.
  Une ligne sans entrée exploitable n'est pas marquée : elle attend qu'on la remplisse.
- **Mêmes entrées, une recherche** : les lignes dont les entrées normalisées coïncident
  partagent UNE cascade, écrite sur chacune (`unique_lookups`, `rows_sharing_a_lookup`).
- **En parallèle** (`PARALLELE` = 8), dans la borne REST : aucune cascade ne PART après
  `DEPART_S`, et leurs étapes cessent d'être tentées à `CASCADE_S` ; ce qui n'est pas parti
  reste en attente (`stopped: "time_budget"`).
- **Les colonnes se déclarent avant de s'écrire** (une colonne non déclarée est refusée) :
  état en texte, listes (`all`, `people`) en json, le reste en texte ; une valeur va au
  TYPE de sa colonne (un nombre dans `number`, une liste jointe dans `text`), une valeur
  qui ne s'y range pas reste vide. `output_fields_never_found` nomme un champ qui n'a
  rien écrit (une faute de frappe).
- ⚠️ **Les étapes asynchrones sont laissées de côté** (lemlist, FullEnrich, Dropcontact,
  la révélation de téléphone Apollo) : payées maintenant, elles répondraient après
  l'écriture de la ligne. Le reçu les nomme (`left_out_async_steps`). Suite naturelle :
  écrire la poignée de collecte sur la ligne et une op `collect_rows`.
- **Pas encore de travail de fond** : un appel traite ce qui tient dans sa fenêtre
  (jusqu'à `batch_size` lignes, 50 au plus) ; l'agent rappelle avec les mêmes arguments.
  Un travail de fond sur le modèle de `jev_rows(background=true)` se justifiera si la
  mesure des lignes par appel ne suffit pas.
- `qualify` (company, person, people) juge par recherche — un appel Jev par recherche.

## Ajouter une étape

Une ligne `Step(...)` dans la table : la clé, l'outil (déjà monté — vérifié par
`tests/test_waterfall.py`), les groupes d'entrées exigées, `build`, `extract` (qui lit le
résultat SERVI, après rédaction), une note de coût, et `collect` si le fournisseur répond
plus tard. Un test de son extraction sur une réponse réelle du fournisseur, anonymisée.
La clé devient aussitôt utilisable dans le réglage `steps` de la carte.
