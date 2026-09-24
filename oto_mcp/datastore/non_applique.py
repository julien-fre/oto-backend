"""Ce que ce tableau DÉCLARE et que la plateforme n'applique pas (#319).

Trois faits qu'un schéma laisse croire et que le moteur ne tient pas — dits au moment
où ils comptent, à la pose comme à l'écriture, jamais six semaines plus tard :

- **`options` de COLONNE hors régime strict ne contraint rien.** `validation_active`
  s'arme sur `strict` et sur les exigences (`required`, `required_when`,
  `max_length`, `max_items`) à toute profondeur, mais `options` ne l'arme que DANS un
  sous-record (oto#137) : un tableau qui déclare une liste de choix sur une colonne
  et rien d'autre accepte tout. `options_not_enforced` le dit à la pose,
  `unenforced_options` nomme la valeur hors liste à l'écriture ;

  ⚠️ **Et en régime STRICT, jusqu'au 10/09/2026, `options` ne contraignait que les
  `enum`** (#98) : sur un texte, un json ou une colonne sans type, une valeur hors
  liste s'écrivait sans refus NI signalement — ce module ne la voyait pas non plus,
  puisqu'il se tait dès que la validation est armée. Les deux régimes jugent désormais
  tout type scalaire, avec la même règle (`options_declarees`) ;
- **un champ `type: json` n'est pas interrogeable en profondeur** — stocké et rendu
  tel quel, ni filtrable ni agrégeable au-delà du premier niveau (`json_fields_depth`).

⚠️ **On AVERTIT, on ne refuse pas.** Un tableau non-strict est en régime souple PAR
DÉCLARATION : y refuser changerait son contrat rétroactivement, et transformerait du
jour au lendemain des écritures qui passaient en erreurs, sans que personne l'ait
demandé.

⚠️ **Tout est DÉRIVÉ des fonctions qui décident** (`validation_active`,
`top_level_options`, `lifecycle_of`), jamais d'une copie de leur logique : le jour
où `options` entrera dans `validation_active`, ces avertissements s'éteindront d'
eux-mêmes. Ce module existe précisément parce qu'une liste avait divergé du code.

Ce qu'il ne tient pas :
- **les clés que la plateforme ne lit pas du tout** → `vocabulaire.py` : ici, la clé
  est lue, elle est simplement sans effet dans ce régime ;
- **l'armement de la validation** lui-même → `declaration.validation_active` ;
- **le refus** d'une valeur hors options en régime strict → `validation.py`.
"""
from __future__ import annotations

from typing import Optional

from .declaration import (
    _fields,
    status_field,
    top_level_options,
    validation_active,
    _walk_fields,
)
from .couches import unwrap
from .options_declarees import hors_des_options, montrable
from .cycle_de_vie import (abandon_state_of, claimable_of, lifecycle_of,
                           max_claims_of, terminal_states)

# ── Options déclarées mais non appliquées (#319) ─────────────────────────────
#
# `validation_active` s'arme sur `strict` et sur les exigences à toute profondeur, mais
# **`options` de premier niveau n'y est pas** (dans un sous-record, elle arme : oto#137).
# Un tableau qui déclare
# `options: ["oui","non","inconnu"]` et rien d'autre accepte « Peut-être » sans un mot.
#
# Le défaut a été signalé sur pièce par une mission, et il est aggravé par #316 : cet
# avertissement-là dirige vers `options` (« si tu voulais contraindre les valeurs, la
# clé est `options` ») — donc vers une clé qui, hors strict, ne contraint rien. Le
# correctif précédent avait déplacé le mensonge d'un cran.
#
# ⚠️ **On AVERTIT, on ne refuse pas.** Un tableau non-strict est en régime souple PAR
# DÉCLARATION : y refuser changerait son contrat rétroactivement. Mesuré en production
# le 13/08 — 23 tableaux sur 57 sont dans ce cas, et les 118 valeurs réellement hors
# liste sont TOUTES sur un seul, dont les écritures deviendraient des erreurs du jour
# au lendemain sans qu'il ait rien demandé. Le régime strict, lui, refuse déjà.
#
# ⚠️ **Tout est DÉRIVÉ des fonctions qui décident** (`validation_active`,
# `top_level_options`), jamais d'une copie de leur logique : le jour où `options`
# entrera dans `validation_active`, ces avertissements s'éteindront d'eux-mêmes. Ce
# lot existe précisément parce qu'une liste avait divergé de ce que le code lit.


def _options_already_enforced(schema: Optional[dict]) -> set:
    """Les champs dont les valeurs sont DÉJÀ contraintes autrement que par `options`.

    ⚠️ Aujourd'hui il n'y en a qu'un : le champ `role="status"` porteur d'un
    `lifecycle`, dont les états sont refusés hors liste MÊME quand `validation_active`
    est faux (vérifié : un état inconnu lève, sans `strict`). L'avertir serait un FAUX
    POSITIF — et un avertissement qui crie à tort est celui qu'on apprend à ignorer,
    donc celui qui ruine les deux autres.

    Dérivé de `lifecycle_of`/`status_field`, jamais d'un nom en dur : le mécanisme de
    cycle de vie est en cours de retrait (#317) et cette exclusion s'éteindra d'
    elle-même le jour où il partira."""
    if lifecycle_of(schema) is None:
        return set()
    sf = status_field(schema) or {}
    key = sf.get("key")
    return {str(key)} if key else set()


def unenforced_options(schema: Optional[dict], data: dict) -> dict:
    """`{champ: valeur hors liste}` — et SEULEMENT quand rien ne les fait respecter.

    Vide dès que la validation est armée : là, une valeur hors options est REFUSÉE, et
    signaler en plus serait un doublon bavard sur un chemin qui ne peut pas passer.
    """
    if validation_active(schema) or not isinstance(data, dict):
        return {}
    deja = _options_already_enforced(schema)
    out: dict = {}
    for champ, opts in top_level_options(schema).items():
        if champ in deja:
            continue
        # ⚠️ **Déballer avant de comparer**, comme partout ailleurs où une valeur est
        # jugée. Une cellule vaut `{"valeur": …, "comment": …}` dès qu'on la justifie
        # en couches — geste NORMAL des agents. Comparée à sa liste SANS déballage,
        # elle est fatalement « hors options » : le repr d'un dict n'est jamais une
        # option. L'avertissement criait donc à tort sur une valeur légitime, en
        # citant la structure Python au lieu de la valeur.
        #
        # Mesuré sur la production le 09/09/2026 avant de corriger : **0** cellule en
        # couches sur les 22 331 cellules pleines des 151 tableaux souples à options.
        # Le trou n'était atteint par rien — le correctif est donc GRATUIT, et c'est
        # tout son intérêt : il ferme la porte avant qu'on la pousse. Le chemin de
        # REFUS (régime strict) déballait déjà ; seul l'avertissement ne le faisait pas.
        v = unwrap(data.get(champ))
        # ⚠️ **Le VIDE n'est pas une valeur hors liste.** Une cellule vide n'a pas de
        # valeur fautive à corriger, et « valeur hors des options déclarées : `status`
        # = '' — elle est ÉCRITE quand même » envoie chercher ce qui n'existe pas.
        # Ce qu'un geste vide est déjà dit, et mieux, par `off_erased`/`off_ignored`.
        #
        # Mesuré, lui, sur du vivant : **25 écritures** l'ont déclenché à tort, sur 4
        # tableaux et 3 propriétaires (`account-management-reengagement/status` ×12,
        # `prospection_unicoop_firenze_20260814/email_source` ×10). Même règle que
        # `etats_trahis`, qui ignore `None` et `""` depuis sa première ligne.
        if v is None or (isinstance(v, str) and not v.strip()):
            continue
        if hors_des_options(v, opts):
            out[champ] = montrable(v)
    return out


def unenforced_options_warning(hors: dict) -> Optional[str]:
    """La phrase qui accompagne le relevé — elle dit la CONSÉQUENCE avant le remède.

    Sans ça on lit « valeur inhabituelle » là où il faut lire « ce champ n'est pas la
    liste fermée que le schéma laisse croire »."""
    if not hors:
        return None
    detail = ", ".join(f"`{k}` = {v!r}" for k, v in sorted(hors.items()))
    return (f"valeur hors des options déclarées : {detail} — elle est ÉCRITE quand "
            "même. Ce tableau n'étant pas en format strict, les `options` de son "
            "schéma décrivent des choix proposés, elles ne les imposent pas. Pour "
            "qu'elles contraignent vraiment, pose `strict: true` sur le tableau "
            "(`data_set_schema`) — les écritures hors liste seront alors refusées.")


def options_not_enforced(schema: Optional[dict]) -> list[str]:
    """Les champs dont les `options` sont déclarées mais inertes — à la POSE.

    Pendant de #316, au moment qui compte : quand on écrit le schéma, pas six semaines
    plus tard en constatant les valeurs libres."""
    if validation_active(schema):
        return []
    deja = _options_already_enforced(schema)
    return sorted(c for c in top_level_options(schema) if c not in deja)


def options_not_enforced_warning(champs: list[str]) -> Optional[str]:
    if not champs:
        return None
    noms = ", ".join(f"`{c}`" for c in champs)
    return (f"options déclarées mais NON appliquées : {noms} — ce tableau n'est pas "
            "en format strict, donc ces listes sont indicatives : une valeur hors "
            "liste sera acceptée. Ajoute `strict: true` au schéma pour qu'elles "
            "contraignent.")


def json_fields_depth(schema: Optional[dict]) -> list[str]:
    """Les champs `type: json` — dont le contenu n'est pas interrogeable en profondeur.

    Le fait est documenté, mais invisible AU MOMENT où on déclare le champ : une
    mission y a mis toute sa traçabilité par champ avant de découvrir qu'elle n'était
    ni filtrable ni agrégeable."""
    return sorted(str(f.get("key")) for f in _walk_fields(_fields(schema))
                  if f.get("type") == "json" and f.get("key"))


def json_depth_warning(champs: list[str]) -> Optional[str]:
    """⚠️ Énonce le FAIT, sans prescrire de contournement : la provenance native est
    en cours de conception, et recommander une structure aujourd'hui reviendrait à
    conseiller ce qui sera obsolète demain."""
    if not champs:
        return None
    noms = ", ".join(f"`{c}`" for c in champs)
    return (f"champ(s) `json` : {noms} — leur contenu est stocké et rendu tel quel, "
            "mais il n'est ni filtrable ni agrégeable au-delà du premier niveau : "
            "`data_rows` ne sait pas interroger une clé imbriquée, et l'export ne la "
            "déplie pas.")


# ── Un cycle de vie posé hors du champ de statut (07/09/2026) ────────────────
#
# Le troisième fait, et il coûte plus cher que les deux autres : `lifecycle_of` ne
# cherche le cycle de vie QUE sur le champ portant `role: "status"`. Un `lifecycle`
# déclaré sur n'importe quel autre champ est stocké, servi dans le schéma, et **jamais
# lu** — donc aucun état terminal, aucun plafond de reprises, aucun état d'abandon,
# aucun périmètre de réservation. La file tourne sans garde, et le schéma affiche le
# contraire.
#
# ⚠️ **La pose est déjà refusée** (`_validate_reserved_def` : « lifecycle exige
# role="status" »). Ça ne suffit pas, et c'est tout l'objet de ce relevé : **un schéma
# déjà en base ne se repose jamais.** Le refus ne parle qu'à celui qui écrit un schéma
# neuf ; celui qui a posé le sien avant la garde ne l'entendra jamais.
#
# Cas mesuré le 07/09/2026 sur un tableau de production d'une campagne vivante : la
# colonne d'état portait un `lifecycle` complet avec `role: "badge"`, pendant qu'une
# autre colonne portait `role: "status"` sans cycle de vie. Six semaines que la file
# de ce tableau n'était gardée par rien, sans un mot. Découvert par un tiers, en
# comparant un schéma avant et après un retrait d'attribut — pas par la plateforme.
#
# C'est le même incident que `required_layers` (posé, servi, sans lecteur) et que les
# clés non interprétées (#316) : une déclaration que le moteur ignore en silence est
# pire qu'une déclaration absente, parce que son auteur croit la garde armée.

def lifecycle_hors_statut(schema: Optional[dict]) -> list[str]:
    """Les champs portant un `lifecycle` que la plateforme ne lira jamais.

    DÉRIVÉ de `status_field`, jamais d'une copie de sa règle : le jour où le cycle de
    vie se lira ailleurs, ce relevé s'éteindra de lui-même."""
    ancre = status_field(schema)
    cle_ancre = str((ancre or {}).get("key") or "")
    return sorted(
        str(f.get("key")) for f in _walk_fields(_fields(schema))
        if isinstance(f.get("lifecycle"), dict) and f.get("key")
        and str(f.get("key")) != cle_ancre)


def lifecycle_hors_statut_warning(champs: list[str],
                                  schema: Optional[dict] = None) -> Optional[str]:
    """La phrase dit la CONSÉQUENCE avant la correction — mais **seulement celle
    qu'elle a constatée**.

    ⚠️ **La première version affirmait « ce tableau n'a AUCUN état terminal, AUCUN
    plafond, AUCUN état d'abandon, AUCUN périmètre ».** C'était faux, et signalé dans
    l'heure par une campagne qui l'a vérifié avant de me le dire : sur son tableau, la
    colonne d'ancrage portait bien des états terminaux — ce sont le plafond, l'abandon
    et le périmètre qui manquaient. Le message décrivait le cycle de vie ORPHELIN et
    concluait sur le tableau ENTIER.

    Un lecteur pressé serait allé poser un état terminal qui existait déjà. **Un
    avertissement qui déborde de son constat coûte plus qu'il ne rapporte** : il fait
    agir sur ce qui va bien, et il perd la confiance qu'il faut pour être suivi sur ce
    qui ne va pas.

    Les manques sont donc MESURÉS un par un, sur ce que la file lit réellement.

    ⚠️ Et le conseil ne suppose plus que l'ancre n'a pas de cycle de vie : quand les
    DEUX colonnes en portent un — le cas rencontré —, « déplace le rôle sur la colonne
    qui porte le cycle de vie » ne désigne rien.
    """
    if not champs:
        return None
    noms = ", ".join(f"`{c}`" for c in champs)
    ancre = status_field(schema)
    cle = (ancre or {}).get("key")

    if not cle:
        return (f"cycle de vie NON LU : {noms} — oto ne lit le `lifecycle` que sur le "
                "champ déclaré `role: \"status\"`, et **aucune colonne ne porte ce "
                "rôle sur ce tableau**. La file n'a donc aucune ancre : rien de ce "
                "cycle de vie n'est appliqué. Déclare `role: \"status\"` sur la "
                "colonne qui porte l'état de travail — jamais pendant qu'une vague "
                "tourne.")

    # Ce que la file lit VRAIMENT, cran par cran. Dérivé des fonctions qui décident,
    # jamais d'une hypothèse sur ce que l'ancre contient.
    # ⚠️ **Un diagnostic ne lève JAMAIS sur ce qu'il diagnostique.**
    # `claimable_of` LÈVE quand le périmètre est déclaré sous une forme illisible —
    # c'est juste sur le chemin de la file, où ignorer rouvrirait le tableau en
    # silence. Mais ICI, cette levée faisait échouer la LECTURE ENTIÈRE du schéma :
    # un tableau au périmètre mal formé devenait illisible, et mon avertissement —
    # écrit pour signaler des déclarations inertes — cassait sur exactement le genre
    # de déclaration qu'il existe pour signaler. Trouvé avant livraison, sur le
    # schéma réel d'un banc de flotte.
    def _perimetre_utilisable() -> bool:
        try:
            return claimable_of(schema) is not None
        # noqa: SILENT — une forme illisible est signalée à part, ligne suivante
        except Exception:
            return False

    manques = [nom for nom, present in (
        ("état terminal", bool(terminal_states(schema))),
        ("plafond de reprises", max_claims_of(schema) is not None),
        ("état d'abandon", abandon_state_of(schema) is not None),
        ("périmètre de réservation", _perimetre_utilisable()),
    ) if not present]

    lc_ancre = lifecycle_of(schema)
    if not lc_ancre:
        etat = (f"C'est `{cle}` qui porte `role: \"status\"`, et **il n'a aucun cycle "
                "de vie** : rien n'est appliqué.")
        conseil = (f"Déplace `role: \"status\"` sur la colonne qui porte le cycle de "
                   f"vie, ou déplace le cycle de vie sur `{cle}`.")
    else:
        etat = (f"C'est `{cle}` qui porte `role: \"status\"`, et **son cycle de vie "
                "est le seul appliqué**.")
        conseil = (f"Les deux colonnes portent un cycle de vie : seul celui de `{cle}` "
                   f"compte. Reporte sur `{cle}` ce que tu veux voir appliqué, ou "
                   f"déplace le rôle si c'est {noms} qui décrit le vrai état de "
                   "travail.")

    if manques:
        # « pas d'état », « pas de plafond » : l'élision se calcule, elle ne se
        # devine pas — une phrase servie à un agent est lue par un humain derrière.
        liste = ", ".join(("pas d'" if m[0] in "aeiouéè" else "pas de ") + m
                          for m in manques)
        consequence = "Ce tableau n'a donc " + liste + " : "
        consequence += ("une ligne réservée n'est pas relâchée quand elle se termine, "
                        "et la file peut tourner à vide indéfiniment."
                        if "état terminal" in manques
                        else "la file ne s'arrête pas d'elle-même sur ces crans.")
    else:
        consequence = ("Tous les crans de la file sont par ailleurs déclarés sur "
                       f"`{cle}` : cette déclaration-ci n'a aucun effet POUR OTO.")

    return (f"cycle de vie NON LU : {noms} — oto ne lit le `lifecycle` que sur le "
            f"champ déclaré `role: \"status\"`. {etat} {consequence} {conseil} "
            "Jamais pendant qu'une vague tourne. ⚠️ « Non lu par oto » ne veut pas dire "
            "« lu par personne » : un consommateur en aval peut parfaitement s'en "
            "servir, et la plateforme ne sait pas qui lit quoi. Ne le retire pas sur "
            "la seule foi de ce message — demande à qui affiche ce tableau.")


# ── Deux gardes qui ont l'air de mordre, et qui ne mordent pas là (08/09/2026) ─
#
# Signalées par une campagne qui les avait posées en croyant fermer une porte, et
# mesurées par elle avant de me le dire. Les deux sont exactes dans ce qu'elles font
# et trompeuses dans ce que leur nom laisse croire — la pire forme de garde, parce
# qu'elle produit une confiance qu'elle ne soutient pas.
#
# **Le contexte qui donne leur poids.** Cette campagne a mesuré que 44 de ses 122
# travaux d'une passe **n'avaient appelé aucun outil de recherche et avaient écrit une
# note qui en décrivait cinq** — le modèle narre au passé des appels qu'il n'a pas
# faits. La parade décidée est d'exiger un jeton de preuve dans la note (`[serper:12]`)
# et de REFUSER sans lui. C'est `pattern` qui porte cette parade ; le trou ci-dessous
# la vide de la moitié de son effet, puisqu'un agent qui n'a rien cherché est aussi
# celui qui peut ne rien écrire.


def motif_sans_obligation(schema: Optional[dict]) -> list[str]:
    """Les colonnes qui déclarent un `pattern` sans rien qui oblige à les remplir.

    ⚠️ **Un motif contraint ce qui est ÉCRIT, jamais le fait d'écrire** — la phrase
    est de la campagne qui l'a mesuré, et elle mérite d'être citée telle quelle. Un
    champ absent ne passe par aucune vérification de forme ; un champ vide non plus.

    Le remède existe et n'est pas nouveau : `required` ferme le trou mais refuse aussi
    les lignes qui n'ont pas encore atteint l'étape, `required_when` le ferme en
    laissant passer les autres. C'est la seconde qui convient à une file de travail où
    les lignes avancent par paliers.
    """
    # ⚠️ Les couches DÉJÀ exigées par leur colonne parente sont exclues, et ce
    # n'est pas un détail : `{"key": "sourcee.comment", "pattern": …}` sur une
    # colonne qui déclare `required_layers: ["comment"]` est **exactement la forme
    # qu'on recommande** deux relevés plus bas. La signaler ferait crier
    # l'avertissement sur le remède qu'il préconise — et un avertissement qui crie à
    # tort est celui qu'on apprend à ignorer, donc celui qui ruine les vrais.
    exigees = set()
    for f in _walk_fields(_fields(schema)):
        if not isinstance(f, dict) or not f.get("key"):
            continue
        for couche in (f.get("required_layers") or []):
            exigees.add(f"{f['key']}.{couche}")

    dehors = []
    for f in _walk_fields(_fields(schema)):
        if not isinstance(f, dict) or not f.get("key"):
            continue
        if not f.get("pattern"):
            continue
        if f.get("required") is True or f.get("required_when"):
            continue
        if str(f["key"]) in exigees:
            continue
        dehors.append(str(f["key"]))
    return sorted(dehors)


def motif_sans_obligation_warning(champs: list[str]) -> Optional[str]:
    """La phrase dit ce que la garde NE FAIT PAS, puis le geste qui la complète.

    ⚠️ Elle ne dit pas « ton motif est mal posé » : il est bien posé, et il fait
    exactement ce qu'un motif fait. Ce qui manque est à côté, et une phrase qui
    accuserait la déclaration ferait chercher au mauvais endroit."""
    if not champs:
        return None
    noms = ", ".join(f"`{c}`" for c in champs)
    return (f"motif posé sans obligation de remplir : {noms} — un motif contraint ce "
            "qui est ÉCRIT, jamais le fait d'écrire. Un champ absent, ou vide, passe "
            "sans être vérifié. Si le motif sert de PREUVE (une source citée, un jeton "
            "d'outil), ajoute `required_when: {<colonne>: [<valeur>]}` pour l'exiger à "
            "l'étape où il compte — `required: true` l'exigerait aussi sur les lignes "
            "qui n'y sont pas encore arrivées.")


def couche_exigee_sans_forme(schema: Optional[dict]) -> list[str]:
    """Les colonnes qui exigent une couche sans rien exiger de son CONTENU.

    ⚠️ `required_layers` garde qu'une couche EXISTE, jamais ce qu'elle contient. « vu
    quelque part » satisfait une exigence de provenance aussi bien qu'une référence de
    registre. La garde a l'air de mordre et ne mord que sur la forme.

    Mesuré le 08/09/2026 sur un tableau de production : quatre colonnes exigeaient la
    provenance, aucune n'exigeait qu'elle dise quoi que ce soit.

    Le remède, trouvé par la campagne elle-même : une couche se contraint en la
    déclarant comme un champ à part entière — `{"key": "actualite.comment", "type":
    "text", "max_length": 600, "pattern": …}`. Le vocabulaire existait déjà ; c'est son
    emploi sur une couche qui n'était écrit nulle part.
    """
    formes = {str(f.get("key")) for f in _walk_fields(_fields(schema))
              if isinstance(f, dict) and f.get("key")
              and (f.get("pattern") or f.get("max_length") or f.get("options"))}
    dehors = []
    for f in _walk_fields(_fields(schema)):
        if not isinstance(f, dict) or not f.get("key"):
            continue
        exigees = f.get("required_layers")
        if not isinstance(exigees, list) or not exigees:
            continue
        cle = str(f["key"])
        for couche in exigees:
            if f"{cle}.{couche}" not in formes:
                dehors.append(f"{cle}.{couche}")
    return sorted(dehors)


def couche_exigee_sans_forme_warning(champs: list[str]) -> Optional[str]:
    if not champs:
        return None
    noms = ", ".join(f"`{c}`" for c in champs)
    return (f"couche exigée sans forme : {noms} — `required_layers` garde qu'une "
            "couche EXISTE, jamais ce qu'elle contient : « vu quelque part » y passe "
            "aussi bien qu'une référence de registre. Pour contraindre le contenu, "
            "déclare la couche comme une colonne à part entière — "
            '`{"key": "<colonne>.comment", "type": "text", "max_length": …, '
            '"pattern": …}` — et le refus nommera alors le champ et la valeur.')
