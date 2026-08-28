"""La règle de TVA d'un abonnement — ce que le PAYS du payeur fait au montant (#486).

Module **pur** : aucune base, aucun réseau, aucune horloge. Il ne connaît qu'une
identité de facturation (pays + n° de TVA) et un montant HT, et il en déduit trois
choses — le régime, le taux, et le montant réellement débité.

## Pourquoi ce module existe

Jusqu'au 28/08/2026 le montant passé au PSP était le prix HT du palier : un client
« à 19 € » était débité de 19,00 € alors que la TVA française de 20 % est due par
Otomata quoi qu'il arrive. Une facture correcte était donc impossible à émettre sur
l'encaissement réel. Le calcul vit ici, une fois, et les DEUX chemins de débit
(souscription `billing.subscribe` et échéance `billing_runner`) l'appellent — un
seul calcul, sinon le renouvellement et la souscription divergent en silence.

## La règle (cadre Alexis, 28/08/2026)

| client | régime | taux | mention portée sur la facture |
| --- | --- | --- | --- |
| **France** | `fr_ttc` | 20 % | — |
| **UE hors FR, n° de TVA intracom** | `reverse_charge` | 0 % | autoliquidation, art. 196 dir. 2006/112/CE |
| **UE hors FR, SANS n° de TVA** | *refusé* | — | guichet OSS non en place |
| **hors UE** | `export` | 0 % | hors champ de la TVA française |

Le refus du particulier UE hors France est un **choix assumé**, pas un trou : le
guichet OSS impose de collecter la TVA du pays du client, de la déclarer et de la
reverser. Tant qu'il n'est pas en place, encaisser serait une TVA due et non
collectée. On refuse donc de souscrire plutôt que de facturer faux.

⚠️ **La forme du numéro n'est PAS sa validité.** Ce module contrôle le PRÉFIXE et le
FORMAT par pays ; il ne dit pas que le numéro existe ni qu'il est actif. La
vérification en ligne (VIES, `ec.europa.eu/taxation_customs/vies`) est un appel
réseau tiers, hors de ce lot — **TODO #486**. D'ici là, un numéro bien formé mais
inexistant fait passer un client en autoliquidation à tort, et la régularisation est
manuelle. C'est la limite connue, elle est écrite ici et dans `docs/billing.md`.

⚠️ **Point de droit resté ouvert** (à trancher avec le conseil, pas en code) : le
« hors UE = 0 % » du cadre ne distingue pas le professionnel du particulier, alors
que les services électroniques rendus à un particulier peuvent relever de règles
propres au pays de consommation. La règle appliquée est celle du cadre.
"""
from __future__ import annotations

import re
from decimal import ROUND_HALF_UP, Decimal
from typing import Optional

# ── régimes de TVA (valeurs journalisées telles quelles dans billing_payments) ──

SCHEME_FR = "fr_ttc"                 # TVA française collectée, 20 %
SCHEME_REVERSE_CHARGE = "reverse_charge"   # autoliquidation intracom, 0 %
SCHEME_EXPORT = "export"             # hors champ de la TVA française, 0 %

SCHEMES = (SCHEME_FR, SCHEME_REVERSE_CHARGE, SCHEME_EXPORT)

# Le taux est en POINTS DE BASE (2000 = 20,00 %), jamais en flottant : un taux
# flottant se propage dans un calcul de montant, et `0.2` n'est pas représentable en
# binaire — `19,00 € × 0.2` donnerait 3,8000000000000003 € avant arrondi. Les
# montants étant déjà en centimes entiers, le taux l'est aussi, et le calcul reste
# exact de bout en bout.
FR_VAT_RATE_BPS = 2000
ZERO_RATE_BPS = 0

MENTION_REVERSE_CHARGE = (
    "Autoliquidation — TVA due par le preneur (article 196 de la directive "
    "2006/112/CE)."
)
MENTION_EXPORT = (
    "TVA non applicable — prestation de services fournie hors de l'Union "
    "européenne (article 259-1 du CGI)."
)

# ── les 27 de l'Union ────────────────────────────────────────────────────────
#
# État au 2026-08-28 : 27 États membres (Croatie entrée le 2013-07-01, Royaume-Uni
# sorti le 2020-01-31). Liste EN DUR et datée — une liste dérivée d'une API ferait
# dépendre le calcul d'un montant débité d'un appel réseau, et un élargissement de
# l'Union est un événement rare qui mérite un commit, pas un silence.
EU_COUNTRIES = frozenset(
    "AT BE BG CY CZ DE DK EE ES FI FR GR HR HU IE IT LT LU LV MT NL PL PT RO SE "
    "SI SK".split()
)

HOME_COUNTRY = "FR"

# ⚠️ **Le code ISO d'un pays n'est pas toujours son préfixe de TVA.** La Grèce est
# `GR` en ISO-3166-1 et `EL` en TVA intracommunautaire — un numéro grec valide
# commence par EL et serait refusé par un contrôle naïf « le numéro commence par le
# code pays ». C'est la seule divergence parmi les 27.
VAT_PREFIXES = {"GR": "EL"}

# Formats de n° de TVA intracommunautaire, PAR PAYS, hors préfixe (source : format
# officiel VIES / Commission européenne). C'est un contrôle de FORME : il attrape la
# faute de frappe et le numéro d'un autre pays, pas un numéro inventé.
VAT_FORMATS: dict[str, str] = {
    "AT": r"U\d{8}",
    "BE": r"[01]\d{9}",
    "BG": r"\d{9,10}",
    "CY": r"\d{8}[A-Z]",
    "CZ": r"\d{8,10}",
    "DE": r"\d{9}",
    "DK": r"\d{8}",
    "EE": r"\d{9}",
    "ES": r"[A-Z0-9]\d{7}[A-Z0-9]",
    "FI": r"\d{8}",
    "FR": r"[A-Z0-9]{2}\d{9}",
    "GR": r"\d{9}",
    "HR": r"\d{11}",
    "HU": r"\d{8}",
    "IE": r"(\d{7}[A-W]|\d[A-W+*]\d{5}[A-W]|\d{7}[A-W][A-I])",
    "IT": r"\d{11}",
    "LT": r"(\d{9}|\d{12})",
    "LU": r"\d{8}",
    "LV": r"\d{11}",
    "MT": r"\d{8}",
    "NL": r"\d{9}B\d{2}",
    "PL": r"\d{10}",
    "PT": r"\d{9}",
    "RO": r"\d{2,10}",
    "SE": r"\d{12}",
    "SI": r"\d{8}",
    "SK": r"\d{10}",
}

# Codes ISO-3166-1 alpha-2 officiellement assignés. Le pays n'est pas contrôlé pour
# le plaisir de la nomenclature : sans lui, une faute de frappe sur « FR » sort de
# l'Union et fait passer un client français en export à 0 % — un manque à gagner
# fiscal SILENCIEUX, du bon côté du doute pour personne.
ISO_COUNTRIES = frozenset(
    "AD AE AF AG AI AL AM AO AQ AR AS AT AU AW AX AZ BA BB BD BE BF BG BH BI BJ "
    "BL BM BN BO BQ BR BS BT BV BW BY BZ CA CC CD CF CG CH CI CK CL CM CN CO CR "
    "CU CV CW CX CY CZ DE DJ DK DM DO DZ EC EE EG EH ER ES ET FI FJ FK FM FO FR "
    "GA GB GD GE GF GG GH GI GL GM GN GP GQ GR GS GT GU GW GY HK HM HN HR HT HU "
    "ID IE IL IM IN IO IQ IR IS IT JE JM JO JP KE KG KH KI KM KN KP KR KW KY KZ "
    "LA LB LC LI LK LR LS LT LU LV LY MA MC MD ME MF MG MH MK ML MM MN MO MP MQ "
    "MR MS MT MU MV MW MX MY MZ NA NC NE NF NG NI NL NO NP NR NU NZ OM PA PE PF "
    "PG PH PK PL PM PN PR PS PT PW PY QA RE RO RS RU RW SA SB SC SD SE SG SH SI "
    "SJ SK SL SM SN SO SR SS ST SV SX SY SZ TC TD TF TG TH TJ TK TL TM TN TO TR "
    "TT TV TW TZ UA UG UM US UY UZ VA VC VE VG VI VN VU WF WS YE YT ZA ZM ZW".split()
)

# ── l'identité de facturation : le contrat de champs ─────────────────────────
#
# Une SEULE liste, lue par la capacité qui écrit l'identité comme par `subscribe`
# qui refuse sans elle : c'est ce qui garantit que le refus nomme exactement les
# champs que le formulaire demande. Deux listes divergeraient au premier ajout.
REQUIRED_IDENTITY_FIELDS = (
    "legal_name", "country_code", "address_line", "postal_code", "city",
)


def missing_identity_fields(identity: Optional[dict]) -> list[str]:
    """Les champs requis qui manquent, dans l'ordre du formulaire.

    Une identité absente rend la liste ENTIÈRE (et non « identity »), pour que le
    refus dise la même chose dans les deux cas : voilà ce qu'il faut fournir."""
    if not identity:
        return list(REQUIRED_IDENTITY_FIELDS)
    return [f for f in REQUIRED_IDENTITY_FIELDS
            if not str(identity.get(f) or "").strip()]


# ── normalisation & contrôle de forme ────────────────────────────────────────

def normalize_country(raw: Optional[str]) -> str:
    """Code pays ISO-3166-1 alpha-2, en majuscules. Refuse ce qui n'est pas un code
    assigné — un pays inconnu ne peut pas être classé, et le classer par défaut
    reviendrait à choisir un taux au hasard."""
    code = str(raw or "").strip().upper()
    if code not in ISO_COUNTRIES:
        raise ValueError(
            f"country_invalid: code pays {raw!r} inconnu — attendu un code "
            "ISO-3166-1 alpha-2 (« FR », « BE », « US »…)")
    return code


def vat_prefix(country_code: str) -> str:
    """Le préfixe que porte le n° de TVA de ce pays (`GR` → `EL`, sinon le code)."""
    return VAT_PREFIXES.get(country_code, country_code)


def normalize_vat_number(raw: Optional[str]) -> Optional[str]:
    """Numéro sans espaces, points ni tirets, en majuscules. `None`/vide reste
    `None` : l'absence de numéro est une information (le client n'est pas
    assujetti), pas une chaîne vide à trimballer."""
    if raw is None:
        return None
    cleaned = re.sub(r"[\s.\-/]", "", str(raw)).upper()
    return cleaned or None


def check_vat_number(country_code: str, vat_number: Optional[str]) -> Optional[str]:
    """Contrôle de FORME (jamais d'existence — VIES est un TODO #486) et renvoie le
    numéro normalisé, préfixe compris.

    Refuse : un pays hors UE (un numéro intracom n'y a pas de sens), un préfixe qui
    ne correspond pas au pays déclaré (la faute la plus fréquente : coller un numéro
    belge sur une identité française), et un format hors de la grammaire du pays."""
    number = normalize_vat_number(vat_number)
    if number is None:
        return None
    if country_code not in EU_COUNTRIES:
        raise ValueError(
            f"vat_number_unexpected: {country_code} n'est pas un État membre de "
            "l'Union — un numéro de TVA intracommunautaire n'y a pas de sens")
    prefix = vat_prefix(country_code)
    if not number.startswith(prefix):
        raise ValueError(
            f"vat_number_invalid: un numéro {country_code} commence par "
            f"« {prefix} » (reçu : {number})")
    corps = number[len(prefix):]
    if not re.fullmatch(VAT_FORMATS[country_code], corps):
        raise ValueError(
            f"vat_number_invalid: format inattendu pour {country_code} — attendu "
            f"« {prefix} » suivi de {_forme_lisible(country_code)} (reçu : {number})")
    return number


def _forme_lisible(country_code: str) -> str:
    """La grammaire d'un pays, dite en français plutôt qu'en expression régulière :
    le message de refus s'adresse à quelqu'un qui recopie un numéro, pas à un
    développeur."""
    return {
        "AT": "« U » puis 8 chiffres", "BE": "10 chiffres commençant par 0 ou 1",
        "BG": "9 ou 10 chiffres", "CY": "8 chiffres puis une lettre",
        "CZ": "8 à 10 chiffres", "DE": "9 chiffres", "DK": "8 chiffres",
        "EE": "9 chiffres", "ES": "9 caractères (lettre ou chiffre aux extrémités)",
        "FI": "8 chiffres", "FR": "2 caractères de clé puis 9 chiffres",
        "GR": "9 chiffres", "HR": "11 chiffres", "HU": "8 chiffres",
        "IE": "7 chiffres et 1 à 2 lettres", "IT": "11 chiffres",
        "LT": "9 ou 12 chiffres", "LU": "8 chiffres", "LV": "11 chiffres",
        "MT": "8 chiffres", "NL": "9 chiffres, « B », puis 2 chiffres",
        "PL": "10 chiffres", "PT": "9 chiffres", "RO": "2 à 10 chiffres",
        "SE": "12 chiffres", "SI": "8 chiffres", "SK": "10 chiffres",
    }[country_code]


# ── la règle ─────────────────────────────────────────────────────────────────

def scheme_for(country_code: str, vat_number: Optional[str]) -> tuple[str, int]:
    """Le régime et le taux (en points de base) pour ce client.

    Lève `vat_consumer_unsupported` pour un client de l'Union hors France sans
    numéro de TVA : c'est le guichet OSS qu'il faudrait, et il n'est pas en place.
    Le refus est le comportement VOULU — encaisser une TVA qu'on ne sait ni
    déclarer ni reverser serait pire qu'un client perdu."""
    if country_code == HOME_COUNTRY:
        return SCHEME_FR, FR_VAT_RATE_BPS
    if country_code in EU_COUNTRIES:
        if not vat_number:
            raise ValueError(
                "vat_consumer_unsupported: un client de l'Union hors France sans "
                f"numéro de TVA intracommunautaire ({country_code}) relève du "
                "guichet OSS, qui n'est pas en place — la souscription en ligne "
                "n'est pas ouverte à ce pays. Fournir un numéro de TVA "
                "intracommunautaire, ou nous contacter.")
        return SCHEME_REVERSE_CHARGE, ZERO_RATE_BPS
    return SCHEME_EXPORT, ZERO_RATE_BPS


def mention_for(scheme: str) -> Optional[str]:
    """La mention légale à porter sur la facture (#488). `None` en régime français :
    une facture avec TVA n'a rien de particulier à justifier."""
    return {SCHEME_REVERSE_CHARGE: MENTION_REVERSE_CHARGE,
            SCHEME_EXPORT: MENTION_EXPORT}.get(scheme)


def vat_amount(amount_ht: int, rate_bps: int) -> int:
    """TVA en centimes, arrondie au centime (demi vers le haut).

    `Decimal` et non un flottant : à 24 900 centimes × 20 %, le flottant rend
    4979.999999999999 et `int()` tronquerait à 4979 — un centime perdu par facture,
    qui ne se voit qu'au rapprochement comptable."""
    exact = (Decimal(amount_ht) * Decimal(rate_bps)) / Decimal(10000)
    return int(exact.quantize(Decimal(1), rounding=ROUND_HALF_UP))


def tax_for(amount_ht: int, country_code: str, vat_number: Optional[str]) -> dict:
    """Le calcul complet, seule porte d'entrée des appelants.

    Rend tout ce qui doit être journalisé sur la tentative de paiement ET servi au
    client : le HT, le taux, la TVA, le TTC réellement débité, le régime et sa
    mention. Le pays et le numéro sont re-normalisés ici — l'identité vient de la
    base, et une donnée relue n'est jamais présumée propre."""
    country = normalize_country(country_code)
    number = check_vat_number(country, vat_number)
    scheme, rate_bps = scheme_for(country, number)
    vat = vat_amount(amount_ht, rate_bps)
    return {
        "amount_ht": amount_ht,
        "vat_rate_bps": rate_bps,
        "vat_amount": vat,
        "amount_ttc": amount_ht + vat,
        "country_code": country,
        "vat_number": number,
        "vat_scheme": scheme,
        "vat_mention": mention_for(scheme),
    }


def tax_for_identity(amount_ht: int, identity: Optional[dict]) -> dict:
    """`tax_for` à partir d'une ligne `billing_identities` — le seam qu'appellent
    la souscription ET l'échéance du runner, pour qu'un renouvellement ne puisse pas
    calculer autrement qu'un premier paiement.

    Reste PUR (l'identité arrive en argument, la lecture est à l'appelant) : chaque
    branche de la règle se teste sans base.

    Le refus NOMME les champs qui manquent, parce qu'il est servi à quelqu'un qui a
    un formulaire sous les yeux — « identité incomplète » l'obligerait à deviner."""
    manquants = missing_identity_fields(identity)
    if manquants:
        raise ValueError(
            "billing_identity_required: l'identité de facturation de l'org est "
            f"incomplète — champs à renseigner : {', '.join(manquants)}. Le pays "
            "décide du montant réellement débité (TVA), et la facture ne peut pas "
            "s'émettre sans la raison sociale ni l'adresse.")
    return tax_for(amount_ht, identity["country_code"], identity.get("vat_number"))


# ── deux lectures, qui ne refusent jamais ────────────────────────────────────

BLANK_PREVIEW = {"vat_rate_bps": None, "vat_amount": None, "amount_ttc": None,
                 "vat_scheme": None, "vat_blocked": None}


def tax_preview(amount_ht: Optional[int], identity: Optional[dict]) -> dict:
    """La même règle, pour une LECTURE (l'état d'abonnement, le formulaire
    d'identité) — qui ne lève jamais.

    Un écran doit rester lisible quand l'identité manque ou n'ouvre pas droit à la
    souscription en ligne : on rend alors les champs à `None`, et `vat_blocked` DIT
    lequel des deux refus s'appliquerait. C'est le seul endroit où l'absence de
    calcul est SERVIE au lieu d'être levée — parce qu'ici rien n'est débité. Tout
    chemin qui débite passe par `tax_for_identity`, qui refuse."""
    if amount_ht is None:
        return dict(BLANK_PREVIEW)
    try:
        tax = tax_for_identity(amount_ht, identity)
    except ValueError as e:
        return {**BLANK_PREVIEW, "vat_blocked": str(e).split(":", 1)[0].strip()}
    return {"vat_rate_bps": tax["vat_rate_bps"], "vat_amount": tax["vat_amount"],
            "amount_ttc": tax["amount_ttc"], "vat_scheme": tax["vat_scheme"],
            "vat_blocked": None}


def tax_view(row: dict) -> dict:
    """Ce qu'une ligne de journal (`billing_payments`) dit du montant qu'elle a
    réellement pris.

    ⚠️ `amount_ht` peut être `None` : les encaissements antérieurs au 28/08/2026 ont
    été débités du HT sans TVA et ne sont **pas** réécrits. Leur servir un HT égal au
    montant serait une reconstitution, pas une lecture — on rend `null`, qui est la
    vérité, et c'est ce `null` qui les distingue."""
    return {"amount": row.get("amount"), "amount_ht": row.get("amount_ht"),
            "vat_rate_bps": row.get("vat_rate_bps"),
            "vat_amount": row.get("vat_amount"),
            "vat_scheme": row.get("vat_scheme")}
