## usage — site, parcelle & immobilier

tout ce qui caractérise un **site** physique en france : géocodage, cadastre, bâti, risques, solaire, prix immobiliers — open data, sans clé.
- `foncier_geocode(adresse)` puis `foncier_parcelle(lat, lon)` / `foncier_bati(lat, lon)` — coordonnées, parcelle cadastrale, emprise bâtie et CES réel
- `foncier_icpe(siret=… | code_insee=…)` — installations classées (régime, seveso, ied, inspections dreal)
- `foncier_prix_m2(code_commune)` / `foncier_comparables_adresse(adresse)` — stats €/m² et ventes comparables dvf
- `foncier_productible_solaire(lat, lon, kwc)` / `foncier_conso_elec(annee, dept)` — productible pv et gros consommateurs électriques
