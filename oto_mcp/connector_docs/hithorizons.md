## prerequisite — clé api hithorizons

connecteur **byo** : chacun connecte son propre compte hithorizons.
- crée un compte sur [hithorizons](https://www.hithorizons.com) et abonne-toi à l'api (azure api management)
- récupère ta clé d'abonnement (`Ocp-Apim-Subscription-Key`)
- pose-la sur ton dashboard oto (connecteur `hithorizons`)

## usage — données entreprise européennes

recherche et fiches d'entreprises à l'échelle européenne (pays par défaut FR, surchargeable).
- `hithorizons_search_company(name=…, city=…, country=…)` — recherche par nom + ville/code postal
- `hithorizons_suggestions(query=…)` — autocomplétion sur le nom
- `hithorizons_company(company_id)` — fiche complète à partir d'un id hithorizons
