"""Les jetons réservés du datastore, et le SEUL endroit qui dit où chacun s'écrit.

Deux jetons voyagent dans les appels : `slot:<nom>` (le tableau bindé par le projet
actif) et `*` (toutes les colonnes). Chacun n'a de sens que dans certains champs — et
jusqu'ici chaque outil décidait dans son coin, ce qui a produit exactement deux familles
de défauts, toutes deux vécues sur une campagne réelle :

1. **Le jeton reconnu, refusé sur le champ voisin.** `slot:` était résolu par les
   opérations de schéma et passé brut par celles de lignes, qui répondaient « datastore
   inconnu ». *Un refus qui dit « inconnu » sur un jeton que la plateforme reconnaît
   envoie chercher une faute de frappe là où il n'y en a pas.*
2. **Le jeton mal placé, accepté en silence** — le cas le plus coûteux, parce qu'il ne
   refuse rien : `_run_id` posé comme colonne grave un identifiant de travail dans le
   fichier d'un client.

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

## Un jeton RETIRÉ, et pourquoi il reste écrit ici (07/09/2026)

`@claimed` — « la ligne que je tiens », posé dans `datastore` et dans `id` — a vécu du
29/08 au 07/09/2026. Il se résolvait par le RUN courant, et c'est ce qui l'a tué : **un
agent est sans état**. Il n'y a pas de « moi » stable auquel accrocher un pronom, et
plusieurs runs d'un même compte coexistent — « la ligne que je tiens » était donc ambigu
par construction, pas par accident d'implémentation. Mesuré avant le retrait : **1 300
refus en 90 jours**, tous des agents qui l'écrivent alors que leur run ne tient rien.

Il reste NOMMÉ ici, et seulement ici, parce qu'un jeton retiré ne disparaît pas du trafic
le jour où on le retire. Sans `JETONS_RETIRES`, la chaîne repartirait telle quelle
jusqu'au stockage, qui répondrait « tableau inconnu » ou « ligne introuvable » sur un mot
que la plateforme reconnaît parfaitement — le refus qui envoie chercher une faute de
frappe dans une chaîne correctement orthographiée. *Un refus qui nomme le geste qui
aboutit est recopié à la lettre dans la minute ; un refus muet fait perdre la ligne deux
fois sur trois.* Cette liste est faite pour se vider le jour où le trafic se sera tu.
"""
from __future__ import annotations

from typing import Optional

SLOT = "slot:"
TOUT = "*"

# Les champs d'ADRESSE — ceux qui désignent où l'on écrit ou ce qu'on lit.
ADRESSE = ("datastore", "id", "fields", "filter", "filters", "group_by", "order_by")

# jeton → (champs qui l'acceptent, ce qu'il désigne)
JETONS: dict[str, tuple[tuple[str, ...], str]] = {
    SLOT: (("datastore",), "le tableau bindé sous ce nom par le projet actif"),
    TOUT: (("fields",), "toutes les colonnes"),
}

# jeton retiré → (pourquoi il est parti, ce qui aboutit à sa place). Un jeton retiré
# n'est plus résolu NULLE PART : il est refusé dans tous les champs d'adresse, et le
# refus porte la conduite — c'est sa seule raison d'être encore écrit.
JETONS_RETIRES: dict[str, tuple[str, str]] = {
    "@claimed": (
        "il se résolvait par le run courant, et un agent sans état n'a pas de « moi » "
        "auquel accrocher un pronom — plusieurs runs coexistent, « la ligne que je "
        "tiens » était ambigu par construction",
        "adresse la ligne explicitement, avec ce que `data_claim_next` t'a rendu au "
        "moment de la réservation : le nom du tableau dans `datastore`, et la ligne par "
        "son `_id` dans `id` (ou par sa clé métier dans `filter`). L'un et l'autre sont "
        "dans la réponse qui t'a réservé la ligne — il n'y a rien à inventer, ni de "
        "faute de frappe à chercher"),
}

# Ce qui n'a AUCUN sens comme donnée : ces noms sont des paramètres d'appel (ADR 0038),
# jamais des colonnes. Posés en clé de ligne, ils gravent un contexte d'exécution dans
# un fichier — et le refus par défaut ne dirait rien de ce qui cloche.
PARAMETRES_D_APPEL = ("_run_id", "_org", "_project", "_group", "_instance")


class JetonMalPlace(ValueError):
    """Jeton reconnu, écrit dans un champ qui ne l'accepte pas.

    Hérite de `ValueError` pour que les deux faces le traduisent en refus actionnable —
    une erreur interne effacerait la seule chose utile : où le jeton s'écrit."""


class JetonRetire(JetonMalPlace):
    """Jeton qui a existé et n'est plus résolu nulle part (`JETONS_RETIRES`).

    Sous-classe de `JetonMalPlace` par CONTRAT, pas par commodité : les deux faces
    traduisent déjà cette famille en refus actionnable (`INVALID_PARAMS` côté agent,
    `400 jeton_mal_place` côté REST — documenté dans `docs/rest-api.md`), et un jeton
    retiré EST un jeton reconnu qu'aucun champ n'accepte. Lui donner un troisième code
    d'erreur changerait un contrat public sans rien apprendre à l'agent, qui lit le
    message et pas le code. La classe existe pour que la prochaine lecture de ce module
    sache pourquoi une chaîne retirée y est encore écrite."""


def jeton_de(valeur: object) -> Optional[str]:
    """Le jeton que porte cette valeur, ou `None` — la reconnaissance est EXACTE.

    `slots:x` n'est pas un jeton : il part tel quel et échoue comme avant. *Un alias qui
    pardonne remplace une chaîne à recopier par une grammaire à deviner — la même faute,
    un cran plus haut.*"""
    if not isinstance(valeur, str):
        return None
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


def verifier_retire(champ: str, valeur: object) -> None:
    """Refuse un jeton RETIRÉ, dans n'importe quel champ d'adresse.

    Passe AVANT `jeton_de` : un jeton retiré n'est plus un jeton, il tomberait donc dans
    l'issue « inconnu » (la valeur part telle quelle) et le stockage répondrait « tableau
    inconnu » — le seul refus qu'on ne veut pas ici, parce qu'il envoie chercher une
    faute de frappe dans une chaîne juste. Le refus NOMME le geste qui aboutit ; il ne se
    contente pas de constater la disparition."""
    if not isinstance(valeur, str):
        return
    retire = JETONS_RETIRES.get(valeur)
    if retire is None:
        return
    pourquoi, conduite = retire
    raise JetonRetire(
        f"`{valeur}` a été RETIRÉ et n'est plus résolu : {pourquoi}. Tu l'as posé dans "
        f"`{champ}` — {conduite}.")


def key_unitaire_redondant(key: object, declaree: Optional[str], row: object,
                           id: object) -> bool:
    """Vrai quand un `key=` posé sur une écriture UNITAIRE ne fait que redire ce qu'elle
    fait déjà : il nomme la clé métier DÉCLARÉE du tableau, la ligne en porte la valeur,
    et aucun `id=` ne vise de ligne. L'écriture unitaire rapproche d'elle-même sur cette
    clé (`append_row` : upsert sur `schema.key`) — le paramètre est alors RÉGLÉ, pas
    ignoré, et le refuser coûtait un aller-retour à chaque procédure qui l'écrit
    (signaux 986, 1125, 1135, 1154 : l'idiome « upsert sur la clé métier » s'écrit
    naturellement ainsi, et une ligne de journal de plusieurs milliers de caractères
    était renvoyée entière).

    ⚠️ Tout le reste reste refusé (`refus_de_key_sans_lot`) : une autre colonne que la
    clé déclarée ne rapproche RIEN sur ce chemin, un `id=` vise déjà sa ligne, et une
    clé sans valeur dans `row` créerait une ligne sans clé — l'incident du 09/09."""
    if not (isinstance(key, str) and key and isinstance(declaree, str) and declaree):
        return False
    if key != declaree or id is not None or not isinstance(row, dict):
        return False
    return row.get(key) is not None


def refus_de_key_sans_lot(key: object, declaree: Optional[str] = None) -> str:
    """Le refus d'un `key=` posé sur une écriture UNITAIRE — il ne sert qu'au lot.

    ⚠️ **Il lève d'abord si `key` porte un jeton RETIRÉ** : « `@claimed` a été retiré,
    voici ce qui aboutit » est infiniment plus utile que « ce paramètre est inopérant ».
    `key` n'est pas un champ d'ADRESSE, donc rien ne l'inspectait — un jeton retiré y
    passait sans un mot, alors que ce module existe précisément pour ça.

    **Pourquoi un refus et pas un avertissement.** Le paramètre était *silencieusement
    ignoré* : ni `append_row` ni `update_row` ne le reçoivent. Mesuré le 09/09/2026 —
    une campagne a écrit dix fois `data_write(key="@claimed", row={…})` en croyant viser
    la ligne réservée ; dix `200`, dix lignes neuves orphelines, et **sept lignes
    réservées qui n'ont jamais reçu leur écriture**. 172 500 jetons.

    ⚠️ **Et l'avertissement avait été essayé.** Le relevé « ligne créée sans clé métier »
    était servi, exact, en entier, nommant la colonne et le geste — dix fois. Le modèle
    l'a ignoré dix fois. *Un refus qui nomme le geste qui aboutit est la seule forme qui
    arrête ; un avertissement parfaitement délivré n'arrête rien.*

    Le refus vise l'AXE — tout `key` inopérant sur ce chemin — et non la valeur
    `@claimed` : ne fermer qu'elle corrigerait un cas et laisserait la classe entière.
    """
    verifier_retire("key", key)
    clause = (f" La clé métier déclarée de ce tableau est `{declaree}` : une écriture "
              f"unitaire accepte `key={declaree!r}` quand `row` en porte la valeur, et "
              f"sans `id=`." if isinstance(declaree, str) and declaree else
              " Ce tableau ne déclare pas de clé métier.")
    return (
        f"`key={key!r}` n'a aucun effet sur une écriture unitaire — il ne sert qu'au "
        f"mode LOT, où il nomme la colonne de dédup de `rows`. Rien n'a été écrit."
        f"{clause}\n"
        f"• pour VISER une ligne existante : `data_write(datastore=…, id=\"<le _id "
        f"rendu par data_claim_next ou data_rows>\", row={{…}})` ;\n"
        f"• pour la retrouver par sa CLÉ MÉTIER : mets la valeur dans `row` — "
        f"l'écriture unitaire rapproche d'elle-même sur la clé déclarée du tableau ;\n"
        f"• pour DÉDOUBLER sur `{key}`, même une seule ligne : "
        f"`data_write(datastore=…, rows=[{{…}}], key={key!r})` — la même ligne "
        f"enveloppée dans `rows=[…]`.")


def verifier_adresse(champ: str, valeur: object) -> None:
    """Refuse un jeton retiré, ou un jeton reconnu posé dans un champ d'adresse qui ne
    l'accepte pas.

    Une valeur qui ne porte aucun jeton connu passe SANS RIEN DIRE : cette couture
    n'invente pas de garde sur les noms littéraux."""
    verifier_retire(champ, valeur)
    jeton = jeton_de(valeur)
    if jeton is None or accepte(champ, jeton):
        return
    raise JetonMalPlace(
        f"`{valeur}` n'est pas accepté dans `{champ}` — {_ou_il_s_ecrit(jeton)}.")


def verifier_contenu(contenu: object) -> None:
    """Refuse ce qui n'a aucun sens comme DONNÉE : un paramètre d'appel en nom de
    colonne.

    ⚠️ Volontairement plus étroit que `verifier_adresse` : `slot:` et `*` sont des
    chaînes qu'une ligne peut légitimement porter, et les refuser ici casserait des
    écritures justes pour se protéger d'une faute qu'on ne sait même pas distinguer.
    Un jeton RETIRÉ ne se refuse pas non plus ici : retiré des adresses, il n'est plus
    qu'une chaîne — et une chaîne qu'aucune description ne propose plus n'arrive pas
    dans une valeur de ligne par imitation."""
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


def verifier_champs(*, datastore=None, id=None, fields=None,
                    filter=None, filters=None) -> None:
    """Le point d'entrée des deux faces : tous les champs d'adresse d'un appel.

    Les clés d'un filtre sont des NOMS DE COLONNE — un jeton y est aussi mal placé que
    dans `id`, et le refus par défaut y dirait « colonne inconnue »."""
    if datastore is not None:
        verifier_adresse("datastore", datastore)
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


def resoudre(datastore, id=None, *, resoudre_slot):
    """Vérifie PUIS résout les champs d'adresse — **le geste des deux faces**.

    C'est ici que la couture cesse d'être une garde et devient un seam : la face MCP et
    la face REST n'ont plus chacune leur idée de ce qu'un jeton signifie. Elles ont
    divergé exactement une fois, et en silence — `slot:` était résolu par les opérations
    de schéma et passé brut par celles de lignes, qui répondaient « datastore inconnu ».

    `resoudre_slot` est injecté (la résolution d'un slot lit le projet actif, qui vit
    dans la couche d'accès) : cette couche-ci ne connaît que les jetons.

    ⚠️ **`id` n'est plus résolu, il est seulement VÉRIFIÉ.** Tant que `@claimed` a vécu,
    cette fonction lisait le store pour transformer un pronom en identifiant, et prenait
    donc un `store` et un `worker` ; le pronom retiré (07/09/2026), il ne reste aucun
    jeton qui s'écrive dans `id` — la vérification suffit, et le seul travail restant est
    la résolution du tableau."""
    verifier_champs(datastore=datastore, id=id)
    return resoudre_slot(datastore), id
