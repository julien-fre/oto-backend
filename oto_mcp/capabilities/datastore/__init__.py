"""Les capacités du datastore (ADR 0046) : tableaux, lignes, schéma, colonnes,
partage, file de travail, journal.

Package sans surface propre — `capabilities/__init__.py` importe chaque module pour
son effet de DÉCLARATION. `common` porte ce que ces capacités partagent (le 404 qui
dit OÙ vit un tableau, l'horodatage), aucun descripteur.
"""
