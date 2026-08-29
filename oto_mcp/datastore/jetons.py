"""Les jetons réservés du datastore, et le SEUL endroit qui dit où chacun s'écrit.

Trois jetons voyagent dans les appels : `@claimed` (la réservation du run), `slot:<nom>`
(le tableau bindé par le projet actif) et `*` (toutes les colonnes). Chacun n'a de sens
que dans certains champs — et jusqu'ici chaque outil décidait dans son coin, ce qui a
produit exactement deux familles de défauts, toutes deux vécues sur une campagne réelle :

1. **Le jeton reconnu, refusé sur le champ voisin.** `@claimed` accepté à l'écriture et
   « namespace inconnu » à la lecture (#517) ; `slot:` résolu par les opérations de
   schéma et passé brut par celles de lignes. *Un refus qui dit « inconnu » sur un jeton
   que la plateforme reconnaît envoie chercher une faute de frappe là où il n'y en a pas.*
2. **Le jeton mal placé, accepté en silence** — le cas le plus coûteux, parce qu'il ne
   refuse rien : `@claimed` écrit dans le CONTENU d'une ligne finit en clair dans un
   fichier client, et `_run_id` posé comme colonne y grave un identifiant de travail.

D'où **trois** issues, et jamais une quatrième :

| ce qu'on lit | ce qui se passe |
|---|---|
| jeton **accepté** par ce champ | résolu, comme aujourd'hui |
| jeton **reconnu mais mal placé** | refus qui NOMME le champ où il s'écrit |
| jeton **inconnu** ici | rien — la valeur part telle quelle |

⚠️ La troisième issue est ce qui empêche cette couture de devenir une grammaire à
deviner. Une chaîne qui commence par `slot:` **dans une valeur de ligne** est une donnée
parfaitement légitime (« slot: machine à café ») : seuls les jetons qui n'ont AUCUN sens
comme donnée sont refusés dans le contenu.
"""
from __future__ import annotations

from typing import Optional

CLAIMED = "@claimed"
SLOT = "slot:"
TOUT = "*"

# Les champs d'ADRESSE — ceux qui désignent où l'on écrit ou ce qu'on lit.
ADRESSE = ("namespace", "id", "fields", "filter", "filters", "group_by", "order_by")

# jeton → (champs qui l'acceptent, ce qu'il désigne)
JETONS: dict[str, tuple[tuple[str, ...], str]] = {
    CLAIMED: (("namespace", "id"), "la ligne que ton run réserve (et son tableau)"),
    SLOT: (("namespace",), "le tableau bindé sous ce nom par le projet actif"),
    TOUT: (("fields",), "toutes les colonnes"),
}

# Ce qui n'a AUCUN sens comme donnée : ces noms sont des paramètres d'appel (ADR 0038),
# jamais des colonnes. Posés en clé de ligne, ils gravent un contexte d'exécution dans
# un fichier — et le refus par défaut ne dirait rien de ce qui cloche.
PARAMETRES_D_APPEL = ("_run_id", "_org", "_project", "_group", "_instance")


class JetonMalPlace(ValueError):
    """Jeton reconnu, écrit dans un champ qui ne l'accepte pas.

    Hérite de `ValueError` pour que les deux faces le traduisent en refus actionnable —
    une erreur interne effacerait la seule chose utile : où le jeton s'écrit."""


def jeton_de(valeur: object) -> Optional[str]:
    """Le jeton que porte cette valeur, ou `None` — la reconnaissance est EXACTE.

    `@claim`, `@claimed-2`, `slots:x` ne sont pas des jetons : ils partent tels quels et
    échouent comme avant. *Un alias qui pardonne remplace une chaîne à recopier par une
    grammaire à deviner — la même faute, un cran plus haut.*"""
    if not isinstance(valeur, str):
        return None
    if valeur == CLAIMED:
        return CLAIMED
    if valeur == TOUT:
        return TOUT
    if valeur.startswith(SLOT):
        return SLOT
    return None


def accepte(champ: str, jeton: str) -> bool:
    champs, _ = JETONS.get(jeton, ((), ""))
    return champ in champs


def _ou_il_s_ecrit(jeton: str) -> str:
    champs, quoi = JETONS[jeton]
    ou = " ou ".join(f"`{c}`" for c in champs)
    return f"`{jeton if jeton != SLOT else 'slot:<nom>'}` = {quoi} ; il s'écrit dans {ou}"


def verifier_adresse(champ: str, valeur: object) -> None:
    """Refuse un jeton reconnu posé dans un champ d'adresse qui ne l'accepte pas.

    Une valeur qui ne porte aucun jeton connu passe SANS RIEN DIRE : cette couture
    n'invente pas de garde sur les noms littéraux."""
    jeton = jeton_de(valeur)
    if jeton is None or accepte(champ, jeton):
        return
    raise JetonMalPlace(
        f"`{valeur}` n'est pas accepté dans `{champ}` — {_ou_il_s_ecrit(jeton)}.")


def verifier_contenu(contenu: object) -> None:
    """Refuse ce qui n'a aucun sens comme DONNÉE : l'alias en valeur, un paramètre
    d'appel en nom de colonne.

    ⚠️ Volontairement plus étroit que `verifier_adresse` : `slot:` et `*` sont des
    chaînes qu'une ligne peut légitimement porter, et les refuser ici casserait des
    écritures justes pour se protéger d'une faute qu'on ne sait même pas distinguer."""
    if isinstance(contenu, str):
        if contenu == CLAIMED:
            raise JetonMalPlace(
                f"`{CLAIMED}` est une ADRESSE, pas une donnée : il s'écrit dans `id` "
                "(ou dans `namespace`), jamais dans le contenu de la ligne — écrit "
                "ici, il finirait en clair dans le fichier.")
        return
    if isinstance(contenu, dict):
        for cle, valeur in contenu.items():
            if cle in PARAMETRES_D_APPEL:
                raise JetonMalPlace(
                    f"`{cle}` est un PARAMÈTRE de l'appel, pas une colonne : il se pose "
                    f"à côté de `row`, jamais dedans — écrit ici, il grave un contexte "
                    f"d'exécution dans le fichier.")
            verifier_contenu(valeur)
        return
    if isinstance(contenu, list):
        for v in contenu:
            verifier_contenu(v)


def verifier_champs(*, namespace=None, id=None, fields=None,
                    filter=None, filters=None) -> None:
    """Le point d'entrée des deux faces : tous les champs d'adresse d'un appel.

    Les clés d'un filtre sont des NOMS DE COLONNE — un jeton y est aussi mal placé que
    dans `id`, et le refus par défaut y dirait « colonne inconnue »."""
    if namespace is not None:
        verifier_adresse("namespace", namespace)
    if id is not None:
        verifier_adresse("id", id)
    for f in fields or ():
        verifier_adresse("fields", f)
    for cle in (filter or {}):
        verifier_adresse("filter", cle)
    for clause in filters or ():
        if isinstance(clause, dict):
            for cle in clause.get("fields", ()) or ():
                verifier_adresse("filter", cle)


def resoudre(store, namespace, id=None, *, worker=None, ligne: bool = True,
             resoudre_slot):
    """Vérifie PUIS résout les champs d'adresse — **le geste des deux faces**.

    C'est ici que la couture cesse d'être une garde et devient un seam : la face MCP et
    la face REST n'ont plus chacune leur idée de ce qu'un jeton signifie. Elles ont
    divergé exactement une fois, et en silence — `slot:` était résolu par les opérations
    de schéma et passé brut par celles de lignes, qui répondaient « namespace inconnu ».

    `resoudre_slot` est injecté (la résolution d'un slot lit le projet actif, qui vit
    dans la couche d'accès) : cette couche-ci ne connaît que les jetons.

    `ligne=False` pour les verbes qui n'adressent qu'un TABLEAU — y résoudre une ligne
    n'aurait aucun sens."""
    verifier_champs(namespace=namespace, id=id)
    if namespace == CLAIMED:
        table, reservee = store.resolve_claimed_target(worker=worker)
        if ligne and (id is None or id == CLAIMED):
            return table, reservee
        return table, (id if ligne else None)
    namespace = resoudre_slot(namespace)
    if ligne and id == CLAIMED:
        id = store.resolve_claimed_ref(namespace, worker=worker)
    return namespace, id
