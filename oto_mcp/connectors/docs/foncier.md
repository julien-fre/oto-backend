## usage — site, parcelle & immobilier

tout ce qui caractérise un **site** physique en france : géocodage, cadastre, bâti, risques, solaire, prix immobiliers — open data, sans clé.
- `foncier_geocode(adresse)` puis `foncier_site(op="parcelle")(lat, lon)` / `foncier_site(op="bati")(lat, lon)` — coordonnées, parcelle cadastrale, emprise bâtie et CES réel
- `foncier_icpe(siret=… | code_insee=…)` — installations classées (régime, seveso, ied, inspections dreal)
- `foncier_dvf(op="prix_m2")(code_commune)` / `foncier_dvf(op="comparables_adresse")(adresse)` — stats €/m² et ventes comparables dvf
- `foncier_site(op="solaire")(lat, lon, kwc)` / `foncier_conso_elec(annee, dept)` — productible pv et gros consommateurs électriques
