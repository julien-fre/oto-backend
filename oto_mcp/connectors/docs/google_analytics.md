## prerequisite — un compte de service google, lecteur de la propriété GA4

l'accès passe par un **compte de service** Google, pas par ton compte Google : il lit GA4 sans que personne connecte son compte, et se coupe en lui retirant l'accès dans GA4. un administrateur le pose une fois pour toute l'org (instance d'org) ; chaque agent de l'org lit alors les mêmes propriétés, sans rien installer.

- dans un projet Google Cloud, **active** « Google Analytics Data API » et « Google Analytics Admin API » ([doc Google](https://developers.google.com/analytics/devguides/reporting/data/v1/quickstart-client-libraries))
- crée un compte de service, puis une **clé JSON** ([doc Google](https://cloud.google.com/iam/docs/keys-create-delete)) : c'est le fichier entier qu'on colle dans `service_account_json`, tel qu'il a été téléchargé
- dans GA4, ajoute l'**email du compte de service** (`…@….iam.gserviceaccount.com`) comme **Lecteur** de chaque propriété à lire ([doc Google](https://support.google.com/analytics/answer/9305788))
- « tester la connexion » appelle la liste des propriétés visibles : zéro propriété est un échec, pas un vert — c'est presque toujours l'étape GA4 qui manque
- pas d'OAuth : le consentement d'un compte Google au scope `analytics.readonly` est bloqué par Google pour cette application

## usage — lire l'audience et les événements d'une propriété GA4

lecture seule, rien n'est jamais écrit dans GA4.

- « quelles propriétés peut-on lire ? » → `ga4_properties()` ; `include_streams=true` ajoute les flux (site, ID de mesure `G-…`)
- « les événements des 30 derniers jours » → `ga4_report(property="123456789", metrics=["eventCount"], dimensions=["eventName"], order_by=["-eventCount"])` — sans dates, la fenêtre est les 30 derniers jours complets (`30daysAgo` → `yesterday`)
- « les sessions par canal en septembre » → `ga4_report(…, metrics=["sessions"], dimensions=["sessionDefaultChannelGroup"], start_date="2026-09-01", end_date="2026-09-30")`
- filtre simple : `dimension_filter={"country": ["France", "Belgique"]}` (texte = égalité, liste = l'une des valeurs, combinés en ET) ; les autres opérateurs passent par une FilterExpression GA4
- « qui est sur le site maintenant ? » → `ga4_realtime(property=…, dimensions=["country"])` — pas de ligne = personne d'actif, pas une erreur
- « quelles dimensions et métriques existent ? » → `ga4_metadata(property=…)`, ou `search="revenue"` pour le détail d'une entrée ; à consulter dès qu'un rapport est refusé pour un nom invalide
- « quelles conversions sont suivies ? » → `ga4_key_events(property=…)`

## note — lire un chiffre GA4 sans se tromper

- les noms sont les **noms d'API** (`activeUsers`, `sessionDefaultChannelGroup`), pas les libellés de l'interface GA4 ; un nom invalide est refusé avec le message de Google qui le nomme
- `metadata` dans la réponse d'un rapport porte les avertissements de GA4 : `samplingMetadatas` (chiffres échantillonnés), `subjectToThresholding` (petits effectifs masqués pour la confidentialité), `dataLossFromOtherRow` (valeurs rares regroupées en « (other) ») — à mentionner avec les chiffres
- `row_count` est le total des lignes qui correspondent ; `next_offset` apparaît quand il en reste
- un refus « n'a pas accès » nomme l'email du compte de service : c'est lui qu'il faut ajouter comme Lecteur de la propriété
