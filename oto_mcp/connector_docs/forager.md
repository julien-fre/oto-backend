## prerequisite — clé api forager

crée une clé API sur [app.forager.ai](https://app.forager.ai) (gestion des clés — création/suppression — **dashboard uniquement**, pas depuis oto), puis colle-la dans oto.
- byo-only : pas de clé oto partagée
- un second champ facultatif `account_id` existe si ta clé a accès à **plusieurs** comptes Forager — laisse-le vide sinon, il est résolu automatiquement

## note — ⚠️ payant au crédit, chaque recherche/lookup consomme le solde du compte

Contrairement à la plupart des connecteurs oto, **chaque appel de recherche ou de lookup facture un crédit** sur l'abonnement Forager (`forager_account(op="me")` montre le solde). Seuls `forager_account` et `forager_autocomplete` sont gratuits.
- utilise `op="totals"` (job posts, organisations, recherche de rôles) pour connaître un volume **avant** de payer la page de résultats complète
- `forager_account(op="balance_log"|"balance_totals")` retrace la consommation

## note — ⚠️ les filtres par ID ne prennent pas de texte libre

`locations`, `industries`/`industries_exclude`, `keywords`/`organization_keywords`, `web_technologies`/`organization_web_technologies`, `person_skills` attendent des **identifiants entiers**, jamais une chaîne. Passer `locations=["Paris"]` ne matche rien. Résous d'abord via `forager_autocomplete` :
- « des offres d'emploi remote à Paris » → `forager_autocomplete(op="locations", q="Paris")` → récupère l'id → `forager_job_post(op="search", filters={"locations": [<id>], "is_remote": true})`

## usage — job posts, organisations, personnes, feedback

Forager croise offres d'emploi, données d'entreprise et enrichissement contact, en 6 tools :
- « des offres d'emploi ingénieur chez telle entreprise » → `forager_job_post(op="search", filters={"title": "engineer", "organization_ids": [...]})`
- « combien d'offres remote en ce moment, avant de payer la liste complète » → `forager_job_post(op="totals", filters={"is_remote": true})`
- « les entreprises SaaS entre 50 et 200 salariés » → `forager_organization(op="search", filters={"industries": [<id SaaS>], "employees_start": 50, "employees_end": 200})`
- « le stack technique du site acme.com » → `forager_organization(op="website", domain="acme.com")`
- « la fiche complète de cette personne (LinkedIn, expériences, formations…) » → `forager_person(op="detail", linkedin_public_identifier="janedoe")`
- « son email pro / perso / téléphone » → `forager_person(op="work_emails"|"personal_emails"|"phone_numbers", person_id=...)` (`personal_emails` ne renvoie que des adresses grand public — Gmail, Outlook… — jamais un domaine d'entreprise)
- « qui est cette adresse email / ce numéro » → `forager_person(op="reverse_by_email", email="...")` / `op="reverse_by_phone"`
- « les personnes qui occupent tel poste dans telle industrie » → `forager_person(op="role_search", filters={"role_title": "...", "organization_industries": [...]})`
- « cet email ne correspondait pas à la bonne personne » → `forager_feedback(op="work_email", email="...", contact_status="invalid", is_correct_person=false)` — chaque appel crée une NOUVELLE ligne de feedback, jamais un upsert

## note — pas de gestion des clés API ici

`GET/POST /api/api_keys/` et `GET/DELETE /api/api_keys/{prefix}/` existent côté Forager mais ne sont **volontairement** pas exposés en tool : créer une clé exposerait un secret dans le contexte agent, et en supprimer une pourrait casser une autre intégration silencieusement. Gestion exclusivement sur [app.forager.ai](https://app.forager.ai).

## note — pas de résultat ≠ pas de résultat

`forager_person(op="work_emails"|"personal_emails"|"phone_numbers")` rend `[]` quand rien n'est trouvé (pas facturé). `op="reverse_by_email"|"reverse_by_phone"` **lève une erreur** dans le même cas (Forager répond 404) — les deux familles de lookup ne signalent pas « rien trouvé » de la même façon, confirmé en live.

## note — testé en live le 2026-08-21

Construit à partir du spec OpenAPI officiel de Forager (contrairement à Grain/Fireflies, un vrai spec machine-readable existe), puis testé en live sur une vraie clé Trial (50 crédits, 36 dépensés — un appel par op des 6 tools). Tout fonctionne de bout en bout. Deux découvertes réelles au passage :
- `forager_autocomplete` rend des `id` en **chaîne** (`"9839"`), à caster en entier avant de les repasser dans un `filters` — cf. note ci-dessus sur les filtres par ID
- `forager_job_post(op="totals")` et `op="search")` ont rendu des `total_search_results` différents sur le même filtre (500 vs 949) lors du test — pas d'explication trouvée côté client, à garder en tête si un chiffre surprend

Restent non confirmés (pas testés pour économiser le solde) : le comportement exact sur un filtre mal typé, et la forme de `test_scores` sur `person_detail_lookup`.
