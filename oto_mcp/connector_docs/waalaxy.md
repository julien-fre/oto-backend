## prerequisite — clé API waalaxy

génère une clé API dans Waalaxy (app → Settings → [CRM Sync](https://app.waalaxy.com/settings/crm-sync) → Generate API key — voir la [doc API](https://docs.waalaxy.com/introduction)), puis colle-la dans oto. Elle n'est affichée qu'une fois (révoquer + régénérer au même endroit).
- plan Advanced ou Business requis — l'API n'existe pas sur les plans inférieurs
- byo-only : pas de clé oto partagée — une clé = UN siège Waalaxy, donc UN compte LinkedIn

## usage — pousser des prospects dans waalaxy

l'API Waalaxy est **import-only** : elle sert à alimenter Waalaxy, pas à le lire. 3 tools :
- « quelles listes ai-je ? » → `waalaxy_prospect_list()` — **toujours commencer là** : le `_id` rendu est le `prospect_list_id` requis par l'import
- « quelles campagnes tournent ? » → `waalaxy_campaign()` — seules les campagnes running/paused sont visibles ; leur `_id` est le `campaign_id`
- « ajoute ce profil LinkedIn à la liste X » → `waalaxy_prospect(op="add", prospect_list_id="...", prospect={"url": "https://www.linkedin.com/in/jane-doe"})`
- « importe ces 40 leads dans la liste X et lance-les dans la campagne Y » → `waalaxy_prospect(op="add", prospect_list_id="...", campaign_id="...", prospects=[{"url": "...", "customProfile": {"firstName": "Jane", "lastName": "Doe", "email": "jane@acme.com", "company": {"name": "Acme"}}, "customVariables": [{"label": "pain", "value": "…"}]}, ...])` — max 100 par appel, un seul appel HTTP
- « montre-moi ce qui partirait sans l'envoyer » → même appel avec `dry_run=true` : rend le payload exact, zéro appel Waalaxy
- pattern typique : sourcing ailleurs (Apollo, Pharow, un datastore Tulina…) → `waalaxy_prospect(op="add")` → Waalaxy déroule invitations/messages tout seul

## ⚠️ notes

- **Waalaxy répond 200 même si TOUS les prospects ont échoué** : lire `failed` dans le reçu (`{total, imported, enrolled, failed: [{index, url, code, message}], result}`), jamais le seul statut HTTP. Codes fréquents : `duplicated_prospect` (déjà dans une autre liste — passer `move_duplicates_to_other_list=true` ou `can_create_duplicates=true`, ce dernier exige la permission import_duplicates du compte), `max_limit_crm` (quota CRM du plan atteint), `already_in_campaign`, `cant_add_prospect_campaign_is_archived`
- `customProfile` ne remplit que les champs vides d'un prospect existant, sauf `should_overwrite_custom_profile_data=true`
- `customVariables[].value` ≤ 1000 caractères (refusé avant l'envoi)
- aucune lecture de prospects, aucune suppression, aucun accès à l'inbox ni aux stats de campagne par l'API — tout ça reste dans l'app ; les retours (réponses, connexions) remontent par les webhooks « CRM Sync » configurés DANS une campagne Waalaxy, pas par ce connecteur
- **non testé en live** (pas de clé au moment du build, 2026-08-26) : construit depuis la référence officielle docs.waalaxy.com/api ; l'URL de base réelle est developers.waalaxy.com (api.waalaxy.com, citée dans la prose de la doc, ne résout pas)
