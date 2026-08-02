## prerequisite — clé api topograph

connecteur **byo** facturé à la requête : chacun connecte son propre compte (pas de clé plateforme).
- crée un compte et génère ta clé sur [topograph](https://www.topograph.co) ([doc api](https://docs.topograph.co))
- pose-la sur ton dashboard oto (connecteur `topograph`)

## usage — kyb registres européens

données et documents kyb normalisés depuis les registres publics européens (FR, GB, DE…).
- `topograph_search(query=…, country=…)` — trouve une entreprise par nom ou numéro d'immatriculation
- `topograph_company(country=…, registration_number=…)` — données normalisées, `mode="onboarding"` (rapide) ou `"verification"` (kyb rigoureux)
