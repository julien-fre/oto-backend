"""Rédaction de champs : **quasi rien par défaut** + templates applicables en 1 clic.

Décision (2026-06-22) : on ne redacte **AUCUN** connecteur par défaut. La rédaction
est *disponible* partout (le middleware `FieldRedactionMiddleware` l'applique dès qu'une
org pose une politique pour un service), mais elle ne s'active que sur décision explicite
de l'org. Raison : la PII n'est pas toujours un risque — sur un CRM/inbox/annuaire
légal, c'est le but. Et le matching par clé est aveugle au contexte (faux positifs).

**Amendement du 2026-09-17 — `SERVER_DEFAULTS` n'est plus vide** (signal d'usage
#1063). La décision de 2026-06-22 tenait tant qu'un connecteur pouvait, en plus, RETIRER
en dur ce qu'il jugeait trop lourd : c'est ce que faisait `payfit`, et ça revenait à
décider à la place de l'entreprise qui possède ses données. On a inversé les deux
leviers : le connecteur sert **tout** ce que l'API expose, et ce qui protège est un
défaut serveur **liftable**, ici. Ce qui n'a pas changé : la rédaction reste une
POLITIQUE, pas un retrait — l'org_admin la lève connecteur par connecteur, et sa
politique est autoritaire (`access.resolve_field_filter`).

Un défaut ne se pose donc ici qu'à trois conditions : la donnée est sensible **par
nature** (RGPD art. 9, identifiant national, coordonnée bancaire), le connecteur la
sert par construction, et le **nom de feuille est sans ambiguïté** — `FieldFilter`
matche par nom de clé, à toute profondeur, sans savoir sous quel parent il est. Un
champ dont le nom est partagé (`type`, `name`, `id`) ne peut PAS recevoir de défaut :
il faut que le connecteur le serve sous un nom qui n'appartient qu'à lui (cf.
`absence_type` dans `tools/payfit_socle.py`), sinon la règle abîme ses homonymes.

Pour ne pas re-saisir des règles utiles, on expose aussi des **TEMPLATES** nommés
(jeux de règles prêts) que l'UI applique en un clic — ex. « anonymisation candidat »
pour le recrutement. Appliquer un template = poser une politique d'org normale (rien
de magique).

Forme d'un bloc / template = `{ "salt": str?, "rules": [ {fields, action, ...} ] }`.
`FieldFilter` (oto-core) matche par **nom de clé feuille**, récursivement et insensible
à la casse → un même jeu couvre les variantes de nommage (snake/camel/kebab).
"""
from __future__ import annotations

# Anonymisation d'un profil/candidat (use-case recrutement) : on masque l'identité
# avant que l'agent voie le profil — pseudonyme cohérent pour les noms (analyse
# possible sans ré-identification), masque format-préservant pour les coordonnées,
# suppression des ré-identifiants directs (photo, URL/ids publics), drop de la date de
# naissance. La localisation/headline sont **gardés** (utiles au scoring).
#
# ⚠️ Calé sur la FORME RÉELLE observée (`linkedin_unipile_profile` : `contact_info.emails`/
# `phones`, `profile_picture_url` + `_large`, `provider_id`, `birthdate`…). On NE
# pseudonymise PAS la clé générique `name` : le moteur matche par clé feuille à toute
# profondeur, et `name` désigne aussi `skills[].name`/`languages[].name` → ça les
# corromprait. Le nom de la personne passe par `first_name`/`last_name` (et `full_name`/
# `display_name`, sans ambiguïté). Un connecteur dont la personne vit sous `name` se
# règle via le dry-run (schéma réel), pas par un défaut aveugle.
_CANDIDATE_PII: list[dict] = [
    {"fields": ["first_name", "firstName", "first-name", "prenom", "given_name", "givenName"],
     "action": "pseudonym", "kind": "first_name"},
    {"fields": ["last_name", "lastName", "last-name", "nom", "family_name", "familyName", "surname"],
     "action": "pseudonym", "kind": "last_name"},
    {"fields": ["full_name", "fullName", "full-name", "display_name", "displayName"],
     "action": "pseudonym", "kind": "name"},
    {"fields": ["email", "emails", "email_address", "emailAddress"],
     "action": "mask", "preserve": "email"},
    {"fields": ["phone", "phones", "phone_number", "phoneNumber", "mobile", "telephone"],
     "action": "mask", "preserve": "phone"},
    {"fields": ["photo_url", "photoUrl", "picture_url", "pictureUrl", "profile_picture_url",
                "profile_picture_url_large", "profilePicture", "avatar_url", "avatarUrl", "image_url"],
     "action": "drop"},
    {"fields": ["public_profile_url", "publicProfileUrl", "profile_url", "profileUrl",
                "public_identifier", "publicIdentifier", "permalink", "profile_link",
                "provider_id", "member_urn"],
     "action": "drop"},
    {"fields": ["birthdate", "birth_date", "date_of_birth", "dob", "dateNaissance"],
     "action": "drop"},
]

# PayFit (paie et RH) — le PLANCHER d'un logiciel de paie, posé le 2026-09-17 quand le
# connecteur a cessé de retirer en dur (cf. `tools/payfit.py`). Trois familles, et rien
# d'autre : tout le reste de la paie (rémunérations, bulletins, coût employeur,
# contrats, temps de travail, mutuelle, coordonnées, naissance) sort SANS masque —
# c'est ce que l'entreprise vient chercher.
#
# ⚠️ Chaque nom ci-dessous a été vérifié SANS HOMONYME dans les réponses PayFit : le
# moteur matche la feuille à toute profondeur, donc un nom partagé abîmerait ses
# voisins. C'est exactement pourquoi le type d'une absence est servi en
# `absence_type` et pas en `type` (qui désigne aussi le type d'un e-mail, d'un
# téléphone, d'une adresse, d'un code analytique et d'un document).
_PAYFIT_PII: list[dict] = [
    # NIR, et le NTT qui en tient lieu avant immatriculation. Masque TOTAL : les
    # premiers chiffres d'un NIR portent sexe, année et département de naissance —
    # un `keep_first` en ferait fuiter la partie la plus signifiante.
    {"fields": ["socialSecurityNumber", "numeroSecuriteSociale",
                "temporaryTechnicalNumber", "numeroTechniqueTemporaire"],
     "action": "mask"},
    # Coordonnées bancaires du salarié. `preserve: iban` garde de quoi reconnaître un
    # compte sans pouvoir l'utiliser.
    {"fields": ["iban"], "action": "mask", "preserve": "iban"},
    {"fields": ["bic"], "action": "mask"},
    # Motif d'une absence : maladie, accident du travail, maternité/paternité sont des
    # données de SANTÉ (RGPD art. 9). Le moteur ne sait pas masquer « seulement les
    # motifs médicaux » — il n'a aucune règle conditionnée à la valeur — donc le champ
    # part en entier. `absence_category` (ordinary_leave | restricted), lui, n'est pas
    # masqué : il suffit à planifier une charge sans lire un motif.
    {"fields": ["absence_type"], "action": "mask"},
]

# Défauts serveur, par service. Repli quand l'org n'a pas posé SA politique ; sa
# politique, elle, est autoritaire et peut tout lever (`access.resolve_field_filter`).
SERVER_DEFAULTS: dict[str, dict] = {
    "payfit": {"rules": _PAYFIT_PII},
}

# Templates appliquables en 1 clic depuis le dashboard (≠ défaut : pas auto-appliqués).
TEMPLATES: dict[str, dict] = {
    "candidate": {
        "label": "anonymisation candidat",
        "hint": "masque l'identité d'un profil/CV (nom→pseudonyme, contact masqué, "
                "photo/URL/ids retirés) — pour analyser un candidat sans le ré-identifier.",
        "rules": _CANDIDATE_PII,
    },
    "bank_details": {
        "label": "coordonnées bancaires",
        "hint": "masque IBAN/BIC/RIB (garde les 4 derniers).",
        "rules": [{"fields": ["iban", "bic", "rib"], "action": "mask", "keep_last": 4}],
    },
}
