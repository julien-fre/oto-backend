## prerequisite — clé api posthog

crée une **clé personnelle** dans PostHog (Settings → Personal API keys — voir la [doc API](https://posthog.com/docs/api)), puis colle-la dans oto.

- ⚠️ **ce n'est PAS la clé du snippet JS.** PostHog met en avant la clé de **projet** `phc_…` (celle de l'installation, de l'ingestion) : l'API de lecture la refuse, avec un `401` impossible à distinguer d'une clé morte. il faut la clé **personnelle**, qui commence par `phx_`. oto refuse une `phc_` à la pose plutôt que de vous laisser chercher
- **scopes** : la clé porte les permissions choisies à sa création. le minimum utile ici est `query:read` + `project:read` ; ajoutez `insight:read`, `person:read`, `event_definition:read`, `property_definition:read`, `cohort:read`, `feature_flag:read`, `session_recording:read`, `annotation:write` selon ce que vous voulez faire. une clé à qui il manque un scope s'authentifie très bien et échoue **au premier appel réel** — le bouton « tester la connexion » exerce donc une vraie requête, pas seulement l'identité
- **région** : `https://us.posthog.com` ou `https://eu.posthog.com` sont deux déploiements **distincts**. une clé de l'un est inconnue de l'autre, et là encore le symptôme est un `401`. choisissez celle de votre projet (ou l'URL de votre instance auto-hébergée)
- **projet** : facultatif. laissé vide, oto le découvre depuis la clé. renseignez-le pour épingler la clé sur un projet précis quand elle en voit plusieurs
- byo-only : pas de clé oto partagée. ce sont vos données produit

## usage — requêtes, personnes, comptes, insights, flags, enregistrements

- « combien d'inscriptions la semaine dernière ? » → `posthog_query(hogql="SELECT count() FROM events WHERE event = 'signup' AND timestamp > now() - INTERVAL 7 DAY")`
- « quels events existent chez nous ? » → `posthog_schema(op="events")` — à faire **avant** d'écrire une requête ; `op="tables"` puis `op="columns", table="events"` pour le schéma
- « notre entonnoir d'activation, mais sur la semaine dernière » → `posthog_insight(op="list")` pour le trouver, puis `posthog_insight(op="run", insight_id=…, date_from="-7d")` — le chiffre rendu est **celui du tableau de bord**, calculé par PostHog
- « un entonnoir qu'on n'a pas encore construit » → `posthog_query(query={"kind": "FunnelsQuery", …})` — surtout **pas** du HogQL écrit à la main pour un entonnoir (voir la note ci-dessous)
- « qui est cet utilisateur ? » → `posthog_person(op="list", search="alice@acme.com")` puis `op="get"`
- « quels clients décrochent ? » → `posthog_group(op="types")` puis `op="list"` — les questions par **compte** ne se répondent pas avec des personnes
- « quels feature flags sont actifs ? » → `posthog_flag(op="list")`
- « montre-moi des sessions où ça coince » → `posthog_recording(op="list", date_from="-7d")`
- « note que la v2.3 est sortie aujourd'hui » → `posthog_project(op="annotate", content="v2.3 en production")`
- « ce chiffre me surprend » → `posthog_project(op="current")` : quel projet, quel compte, quelle région ont répondu — c'est l'explication la plus fréquente

## note — entonnoirs et rétention : ne pas les réécrire en SQL

la sémantique d'entonnoir de PostHog (étapes ordonnées ou non, fenêtre de conversion, étapes d'exclusion, attribution) ne se reconstitue pas fidèlement en HogQL. une requête écrite à la main rendra un nombre **plausible**, et il sera en désaccord avec celui que votre équipe lit dans PostHog — le pire des résultats, parce que rien ne signale l'erreur.

deux voies correctes, dans cet ordre :
1. l'insight existe déjà → `posthog_insight(op="run", insight_id=…)`, éventuellement avec `date_from`/`date_to` pour changer la fenêtre. la définition vient de votre équipe, le calcul de PostHog
2. sinon → `posthog_query(query={"kind": "FunnelsQuery" | "RetentionQuery" | "TrendsQuery", …})`, qui fait calculer PostHog

le HogQL libre reste la bonne voie pour tout le reste : comptages, répartitions, jointures, questions ad hoc.

## note — dialecte hogql

c'est du SQL ClickHouse avec les accesseurs PostHog :
- propriétés : `properties.$browser`, `person.properties.email` — pas de `JSONExtract`. les valeurs sont des **chaînes** : comparer un nombre demande `toFloat(properties.amount) > 10`
- la colonne du nom d'event est `event` (pas `event_name`) ; le temps est `timestamp`, filtré par `timestamp >= now() - INTERVAL 7 DAY`
- utilisateurs uniques = `uniq(person_id)` — **jamais** `count(distinct distinct_id)`, qui compte des appareils
- tables jointes usuelles : `events`, `persons`, `sessions`, `groups`. `posthog_schema` les liste toutes

une requête sans `LIMIT` est bornée à 101 lignes par PostHog, avec `hasMore` à vrai : agrégez dans la requête plutôt que de paginer.

## note — ce que ce connecteur ne fera jamais

créer, modifier ou **basculer** un feature flag, écrire un insight ou une cohorte, supprimer une personne ou un enregistrement, envoyer des events : aucune de ces opérations n'existe dans la librairie sous-jacente. basculer un flag change le produit pour de vrais utilisateurs, et supprimer une personne est irréversible et réglementaire — ce n'est pas une décision d'assistant. faites-les dans PostHog.

la **seule** écriture est l'annotation : un repère daté posé sur vos graphes, purement additif, qui ne modifie aucune mesure.

## note — vérifié en live le 2026-08-22

testé contre un vrai projet PostHog Cloud US : identité, découverte du projet, HogQL, requêtes typées, ré-exécution d'un insight sauvegardé, schéma (156 tables, `events` à 52 colonnes), les 14 familles de ressources et l'écriture d'annotation répondent comme codé. trois formes qui ne se déduisent pas de la doc et qui sont gérées ici : `groups_types` rend une liste **nue** (pas d'enveloppe `results`), `/events/` et `/persons/` ne portent **pas** de `count` (ne jamais annoncer un total depuis une page — passer par une requête), et la réponse brute de `/query/` est à **93 % du diagnostic interne** (SQL généré, modificateurs, clés de cache), réduite ici aux colonnes, types, résultats et à la requête effectivement exécutée.
