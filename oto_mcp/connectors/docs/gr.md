## usage — entreprises grèce

identifie une entité grecque dans les registres publics (gemi + vies), sans clé.
- `gr_lookup(query=…)` accepte un **nom**, un **n° gemi** ou un **n° de tva** grec (ΑΦΜ, avec ou sans préfixe `EL`)
- renvoie les entreprises correspondantes (nom, n° gemi, tva, statut actif/inactif)
- pour un résultat unique : ajoute l'adresse et la validité du n° de tva via vies
