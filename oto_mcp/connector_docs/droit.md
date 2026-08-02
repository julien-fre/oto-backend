## usage — conventions collectives (kali)

le droit de la branche en texte intégral (stock kali/dila complet, ~290k articles) : minima, congés, primes, classifications. filtre idcc natif.
- `ccn_conventions(idcc=… | query=…)` — résoudre une convention (« quelle est la 3090 ? », « conventions du spectacle »)
- `ccn_search(query=…, idcc=…)` — recherche plein-texte dans les articles d'une branche (ou toutes)
- `ccn_get(kali_id)` — texte intégral consolidé d'un article + lien légifrance vérifiable
- complément côté entreprise (connecteur sirene) : `fr_accords_search(idcc=…)` — les accords d'**entreprise** de la branche (qui a négocié quoi, quand), puis `fr_accords_text(acco_id)` pour lire l'accord

## usage — codes consolidés (legi)

les 22 codes français avec versions historiques : citer la loi exacte, à la bonne date, avec lien légifrance.
- `loi_article(code="CT", num="L1242-2", date=…)` — le texte en vigueur à la date demandée (défaut aujourd'hui)
- `loi_versions(code, num)` — la timeline des rédactions d'un article
- `loi_search(query=…, code=…)` — retrouver l'article quand on connaît le concept, pas le numéro
- `loi_codes()` — les alias couverts (CT, CC, CP, CSS, CGI…)

## usage — jurisprudence (6 fonds dila + cedh/cjue)

comment les juges tranchent : cassation (publiés + inédits), cours d'appel, CE/CAA/TA, conseil constitutionnel, cnil, cedh, cjue. tri pertinence × autorité.
- `juris_search(query=…, fond=…, juridiction=…, date_min=…)` — recherche unifiée plein-texte
- `juris_get(decision_id)` — texte intégral d'une décision + lien légifrance
- workflow type : `juris_search` → repérer l'arrêt de principe → `juris_get` → citer avec `loi_article` (les textes visés, à la date de la décision)
