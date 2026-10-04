"""Valider le SCHÉMA lui-même — « ce format est-il posable ? » (ADR 0046).

`validate_schema_def` est le seul point d'entrée : elle rend la liste des raisons pour
lesquelles un schéma est refusé À LA POSE, avant qu'une seule ligne ne soit écrite.
Elle juge d'abord le VOCABULAIRE, à chaque niveau (`cles_inconnues.refus`), puis
délègue à deux aides qui portent le gros du texte : `_validate_fields_def` (récursive —
types, bornes, motifs, couches, composites) et `_validate_reserved_def` (les valeurs
des crans `readonly` et `agent_access`).

**La différence avec `validation.py` est le MOMENT, et il change tout** : ici on juge
un format, une fois, à la pose ; là on juge une ligne, à chaque écriture. Un refus
d'ici est cher (il bloque un geste d'administration et il est lu par un humain) ; un
refus de là est chaud (il est lu par un agent, en boucle). C'est pourquoi les deux ne
partagent pas leurs textes.

**La différence avec `declaration.py` est la QUESTION** : `declaration.py` lit un
schéma qu'il suppose valide ; ici on décide s'il l'est. Ce module appelle donc l'autre,
jamais l'inverse.

Ce qu'il ne tient pas :
- **la liste fermée des attributs**, par niveau → `schema_keys.py` ;
- **le refus d'une clé qu'aucun niveau n'admet** → `cles_inconnues.py`, appelé d'ici
  en premier ;
- **le refus d'une ligne** contre le format ainsi validé → `validation.py` ;
- **la validité du périmètre de réservation** → `claimable.erreurs`, appelée d'ici.
"""
from __future__ import annotations

from typing import Optional

from . import acces_agent as aga
from . import claimable
from . import cles_inconnues
from . import reglages
from . import schema_keys

from .couches import LAYER_KEYS, split_layer
from .motifs import PATTERN_MAX_SUBJECT, pattern_refusal
from .declaration import (
    FILE_KEYS,
    COMPOSITE_TYPES,
    DISPLAY_TITLE,
    _fields,
    borne_du_motif,
    max_length_of,
    readonly_fields,
    SCALAR_TYPES,
    status_field,
)
from .cycle_de_vie import LIBELLE_ETAT_MAX, lifecycle_of, terminal_states
from . import formule as _formule

# ── validation de la DÉFINITION du schéma ────────────────────────────────────

def validate_schema_def(schema: Optional[dict],
                        ancien: Optional[dict] = None) -> list[str]:
    """Erreurs de structure de la définition elle-même (posée par data_set_schema).
    Un schéma 0016 plat reste valide tel quel.

    `ancien` = le schéma EN PLACE, quand il y en a un : une clé inconnue qui y figure
    déjà, au même endroit et avec la même valeur, n'est pas posée par ce geste et ne
    se refuse pas (`cles_inconnues.refus`). Sans `ancien`, tout est jugé comme posé."""
    if schema is None:
        return []
    if not isinstance(schema, dict):
        return ["schema doit être un objet {fields:[...]} ou null"]
    # Le vocabulaire d'abord : une clé que son niveau n'admet pas est la faute la plus
    # fréquente, et les refus suivants supposent les clés bien nommées.
    errors: list[str] = cles_inconnues.refus(schema, ancien)
    _validate_fields_def(_fields(schema), "fields", errors)
    errors.extend(_validate_formulas_def(_fields(schema)))
    # Une colonne titre par tableau (#317) : deux candidats, et le nom d'une ligne
    # dépendrait de l'ordre de déclaration — une inférence silencieuse, exactement ce
    # que le retrait des rôles supprime. Zéro conflit en production au moment de la
    # bascule : le refus ne casse personne.
    titres = [str(f.get("key")) for f in _fields(schema)
              if f.get("display") == DISPLAY_TITLE and f.get("key")]
    if len(titres) > 1:
        errors.append(
            f"display=\"title\" déclaré sur {len(titres)} colonnes ({', '.join(titres)}) "
            "— une seule nomme la ligne")
    # Un seul cycle de vie par tableau : le bloc DÉSIGNE la colonne d'état, donc deux
    # blocs feraient dépendre l'état de l'ordre de déclaration — en silence. Même
    # refus que deux `display: "title"`, et pour la même raison.
    files = [str(f.get("key")) for f in _fields(schema)
             if isinstance(f, dict) and isinstance(f.get("lifecycle"), dict)
             and f.get("key")
             and any(k in f["lifecycle"] for k in FILE_KEYS)]
    if len(files) > 1:
        errors.append(
            f"deux colonnes déclarent une FILE de travail ({', '.join(files)}) — "
            f"`claimable`, `max_claims` ou `abandon_state` ne peuvent vivre que sur "
            f"une seule, celle que `data_claim_next` réserve. Plusieurs cycles de vie "
            f"sont permis (une file d'agents et des états humains, par exemple), mais "
            f"une seule file.")
    # Une clé métier n'est JAMAIS un sous-tableau ni un sous-record (oto#22 §4). Elle
    # identifie la ligne : les écritures par lot dédupliquent dessus, et un index
    # d'unicité d'expression la compare. Une liste ne se réduit pas à une valeur —
    # l'unicité porterait sur le TEXTE d'un objet JSON, donc deux listes équivalentes
    # d'ordre différent ne collisionneraient pas. Refusé à la DÉCLARATION plutôt qu'à
    # la première écriture : le tableau serait déjà peuplé de doublons.
    cle = schema.get("key")
    if cle:
        porteur = next((f for f in _fields(schema) if f.get("key") == cle), None)
        if porteur and porteur.get("type") in COMPOSITE_TYPES:
            errors.append(
                f"key=\"{cle}\" désigne un champ de type \"{porteur.get('type')}\" — "
                "une clé métier identifie la ligne, elle doit être une valeur simple "
                "(une liste ne se réduit pas à une valeur, l'unicité serait fausse)")
    # `new_rows: "reject"` DURCIT la clé métier, et l'exige : sans elle, aucune
    # écriture ne peut désigner une ligne autrement que par son identifiant
    # (`reglages.erreurs`, appelé plus bas avec l'autre réglage de tête).
    # #606 (29/08/2026) : la clé figure dans CHAQUE écriture pour désigner la ligne.
    # `readonly` dessus — identique refusé — fermerait toutes les écritures du tableau,
    # et celui qui « complète » la pose dans six mois ne le saurait pas.
    if cle and cle in readonly_fields(schema):
        errors.append(
            f"`{cle}` est la clé métier : elle se protège par `new_rows: \"reject\"`, pas par "
            f"`readonly` — une autre valeur est une autre ligne, et la clé figure dans "
            f"chaque écriture pour désigner la sienne")
    # oto#83, troisième fois la même raison : la clé désigne la ligne, et elle figure
    # dans chaque écriture pour ça. La fermer à l'agent — ou pire, la lui cacher —
    # rendrait le tableau inécrivable ET illisible pour lui, sans qu'aucun refus ne
    # nomme la cause : il verrait des lignes sans identité et des écritures qui visent
    # à côté. Le tableau qu'un agent ne doit pas toucher se ferme par le partage, pas
    # colonne par colonne.
    if cle and aga.acces_declare(schema, cle) not in (None, aga.ECRITURE):
        errors.append(
            f"`{cle}` est la clé métier : elle ne se ferme pas par `{aga.CLE}` — un "
            f"agent la lit pour désigner la ligne qu'il écrit, et sans elle il "
            f"écrirait à côté. Ferme les colonnes de SUIVI, pas celle qui identifie ; "
            f"un tableau entier se ferme en ne le partageant pas")
    errors.extend(reglages.erreurs(schema))
    lc = lifecycle_of(schema)
    if lc is not None:
        states = lc.get("states")
        if not isinstance(states, list) or not states:
            errors.append("lifecycle.states doit être une liste non vide")
        else:
            known = {str(s) for s in states}
        # ⚠️ La FORME de `transitions` se juge AVANT de la parcourir. Elle ne le
        # faisait pas : une chaîne, une liste ou un nombre y produisait un
        # `AttributeError: 'str' object has no attribute 'items'` — une erreur
        # TECHNIQUE, qui n'est pas une `ValueError`, donc que la face REST ne traduit
        # pas : l'appelant recevait un 500 au corps vide sur un schéma qu'il venait
        # d'écrire, et pouvait croire la pose réussie. Même famille que `RowLocked`
        # (#317) : un refus juste qui ne sort pas comme un refus.
        transitions = lc.get("transitions")
        if transitions is not None and not isinstance(transitions, dict):
            errors.append(
                f"lifecycle.transitions doit être un objet "
                f"{{\"état\": [\"états atteignables\"]}} — reçu "
                f"{type(transitions).__name__}. Chaque clé est un état de départ, "
                f"chaque valeur la liste de ceux qu'il peut atteindre.")
            transitions = None
        if isinstance(states, list) and states:
            for frm, tos in (transitions or {}).items():
                if str(frm) not in known:
                    errors.append(f"lifecycle.transitions: état source inconnu {frm!r}")
                for to in tos if isinstance(tos, list) else [tos]:
                    if str(to) not in known:
                        errors.append(f"lifecycle.transitions: état cible inconnu {to!r}")
            for t in lc.get("terminal") or []:
                if str(t) not in known:
                    errors.append(f"lifecycle.terminal: état inconnu {t!r}")
        # Le plafond de reprises (#433) et son état d'abandon vont ENSEMBLE : un
        # plafond sans état où verser la ligne serait une garde qui ne peut pas
        # s'appliquer, et un état non terminal la remettrait dans la file qu'elle
        # vient de quitter. Les deux se refusent à la pose, là où le tableau se
        # déclare — pas au premier claim d'une campagne déjà lancée.
        plafond = lc.get("max_claims")
        if plafond is not None and (isinstance(plafond, bool)
                                    or not isinstance(plafond, int) or plafond < 1):
            errors.append(
                f"lifecycle.max_claims doit être un entier >= 1 (reçu {plafond!r}) — "
                "c'est le nombre de réservations SANS écriture qu'une ligne supporte "
                "avant de quitter la file")
        abandon = lc.get("abandon_state")
        if plafond is not None and abandon is None:
            errors.append(
                "lifecycle.max_claims exige lifecycle.abandon_state — l'état terminal "
                "où verser une ligne réservée N fois sans écriture")
        if abandon is not None and str(abandon) not in terminal_states(schema):
            errors.append(
                f"lifecycle.abandon_state: {abandon!r} n'est pas un état terminal déclaré "
                "(ajoute-le à lifecycle.terminal) — une ligne abandonnée reviendrait "
                "sinon dans la file qu'elle vient de quitter")
        # Le périmètre de réservation (#517) se valide par le moteur de filtre qui le
        # servira — refusé à la pose, comme le plafond : une déclaration illisible
        # au premier claim d'une campagne lancée est le pire moment pour l'apprendre.
        sf = status_field(schema) or {}
        errors.extend(claimable.erreurs(
            lc, declared={f.get("key") for f in _fields(schema)},
            contraignant=reglages.format_contraignant(schema), status_key=sf.get("key"),
            states={str(s) for s in (lc.get("states") or [])}
            if isinstance(lc.get("states"), list) else set()))
    errors.extend(_erreurs_libelles_d_etat(schema))
    # ⚠️ Il y avait ici un refus « lifecycle exige role="status" ». Retiré le
    # 08/09/2026 avec l'étiquette : le bloc DÉSIGNE désormais sa colonne, il n'y a plus
    # de placement à vérifier. Et ce refus n'avait pas protégé — cinq schémas de
    # production portaient un `lifecycle` sur une colonne non étiquetée, stocké, servi
    # et jamais lu, alors qu'il existait. Ce qui les arrête maintenant est plus haut :
    # deux blocs sont refusés, et un bloc seul EST l'état.
    return errors


def _erreurs_libelles_d_etat(schema: dict) -> list[str]:
    """`lifecycle.labels` (oto#140) : le nom affiché de chaque étape — PRÉSENTATION,
    jamais validation. Aucune écriture de ligne ne le lit ; un front l'affiche à la
    place du code (`a_qualifier` → « À qualifier »).

    Jugé sur CHAQUE colonne qui porte un cycle de vie, pas seulement sur celle de
    file (`status_field`) : un tableau peut porter des états humains à côté de sa
    file, et c'est justement eux qu'un écran nomme.

    ⚠️ **Une clé qui n'est pas un état déclaré est REFUSÉE, et nommée.** Un libellé
    posé sur `a_qualifer` (faute de frappe) serait stocké, servi, et jamais affiché :
    l'auteur croirait l'étape renommée, l'écran continuerait d'afficher le code. Un
    état SANS libellé, lui, est permis — le front dérive alors le sien du code."""
    errs: list[str] = []
    for f in _fields(schema):
        if not isinstance(f, dict) or not isinstance(f.get("lifecycle"), dict):
            continue
        lc = f["lifecycle"]
        if "labels" not in lc:
            continue
        col = f.get("key")
        libelles = lc["labels"]
        if not isinstance(libelles, dict):
            errs.append(
                f"`{col}` : lifecycle.labels doit être un objet {{\"état\": \"libellé\"}} "
                f"— reçu {type(libelles).__name__}. Chaque clé est un état de `states`, "
                f"chaque valeur le nom affiché de cette étape.")
            continue
        etats = lc.get("states")
        connus = [str(e) for e in etats] if isinstance(etats, list) else []
        for etat, libelle in libelles.items():
            if str(etat) not in connus:
                errs.append(
                    f"`{col}` : lifecycle.labels : état inconnu {etat!r} — un libellé "
                    f"nomme un état déclaré dans `states` "
                    f"({', '.join(connus) if connus else 'aucun'})")
            if not isinstance(libelle, str) or not libelle.strip():
                errs.append(
                    f"`{col}` : lifecycle.labels[{etat!r}] doit être une chaîne non vide "
                    f"— reçu {libelle!r}. Pour revenir au libellé dérivé du code, "
                    f"retire la clé.")
            elif len(libelle) > LIBELLE_ETAT_MAX:
                errs.append(
                    f"`{col}` : lifecycle.labels[{etat!r}] fait {len(libelle)} caractères, "
                    f"au plus {LIBELLE_ETAT_MAX} — c'est le nom d'une étape, pas sa "
                    f"description")
    return errs

# Ce qu'une COLONNE seule peut déclarer — donc ce qu'une cible de couche ne peut pas.
# Chacune désigne la colonne en tant que telle : nommer la ligne (`display`), porter
# son statut (`role`), se subdiviser (`fields`/`of`), dire à qui elle est servie
# (`agent_access`). Posées sur une couche, elles ne seraient lues nulle part — la
# forme acceptée-inerte que #347 a fermée.
# DÉRIVÉ de la déclaration unique des attributs (`schema_keys`), jamais recopié : le
# validateur est le PREMIER client de cette liste, l'avertissement sur les clés
# inconnues le second. Une liste parallèle mentirait au premier attribut ajouté.
_COLUMN_ONLY_KEYS = schema_keys.COLONNE_SEULEMENT


def _validate_reserved_def(f: dict, fpath: str, errors: list[str]) -> None:
    """#586/#606 : un cran qui ne peut pas s'appliquer se refuse à la POSE, devant
    celui qui peut corriger — jamais accepté-inerte (#347). `None` passe : c'est
    la forme par laquelle un patch LÈVE le cran sans réécrire le schéma."""
    ro = f.get("readonly")
    if ro is not None and not isinstance(ro, bool):
        errors.append(
            f"{fpath}: readonly doit être true ou false (reçu {ro!r}) — `true` = "
            f"colonne du fichier source, dont la valeur ne change pas par une "
            f"écriture (ses couches `comment`/`link` restent ouvertes)")
    # `origine` n'est plus lue ici depuis le 08/09/2026, et elle est REFUSÉE depuis
    # le 01/10/2026 comme toute clé retirée (`schema_keys.CLES_RETIREES`) — l'origine
    # se déclare à l'import (`donnees_d_origine`), plus par le format.
    #
    # oto#83 — quatrième cran de la famille : à QUI la colonne est servie.
    # ⚠️ La valeur inconnue est REFUSÉE, et c'est le point du cran : une VALEUR que le
    # validateur exécute ne peut pas rester ouverte. `agent_access: "non"` retomberait en
    # silence sur le défaut « write », et le propriétaire croirait sa colonne fermée
    # alors qu'elle est grande ouverte. C'est mot pour mot la plaie de `read_only`
    # écrit pour `readonly`, à ceci près qu'ici on peut la fermer.
    aa = f.get(aga.CLE)
    if aa is not None and aa not in aga.VALEURS:
        errors.append(
            f"{fpath}: {aga.CLE}: valeur inconnue {aa!r} — les seules sont "
            + ", ".join(repr(v) for v in aga.VALEURS)
            + f" ({aga.ECRITURE!r} = le défaut ; {aga.LECTURE!r} = un agent la voit "
            f"mais n'en écrit pas la valeur ; {aga.AUCUN!r} = un agent ne la voit pas "
            f"du tout). Une valeur que la plateforme ne sait pas lire laisserait la "
            f"colonne ouverte sous un réglage qui promet le contraire")
    # Sous un sous-record, `readonly` et `agent_access` ne sont pas admis : c'est le
    # vocabulaire du niveau qui le refuse (`schema_keys.PREMIER_NIVEAU_SEULEMENT`) —
    # d'où un appel au premier niveau seulement.


def _validate_fields_def(fields: list, path: str, errors: list[str]) -> None:
    for f in fields:
        key = f.get("key")
        fpath = f"{path}.{key or '?'}"
        if not isinstance(key, str) or not key:
            errors.append(f"{fpath}: key manquante")
            continue
        # Cible de COUCHE (#377) : `qualification.comment` contraint la couche du
        # même nom SUR la colonne `qualification` — ce n'est pas une colonne de
        # plus. Toute autre forme pointée se refuse ICI plutôt que d'être stockée :
        # elle ne désignerait rien, et une contrainte qui ne désigne rien n'est pas
        # inerte, elle est INSATISFIABLE — c'est le défaut de #377, où la pose
        # passait et toute écriture déclenchante était ensuite refusée, y compris
        # celle qui portait bien la justification.
        base, layer = split_layer(key)
        if layer:
            if not any(str(x.get("key") or "") == base
                       for x in fields if isinstance(x, dict)):
                errors.append(
                    f"{fpath}: la couche `{layer}` porte sur la colonne `{base}`, "
                    f"qui n'est pas déclarée ici — déclare-la, ou corrige le nom. "
                    f"Une contrainte sur une colonne absente ne pourrait jamais "
                    f"être satisfaite.")
            interdites = [k for k in _COLUMN_ONLY_KEYS if k in f]
            if interdites:
                errors.append(
                    f"{fpath}: {', '.join(repr(k) for k in interdites)} ne se "
                    f"déclare que sur une COLONNE, pas sur une couche — une couche "
                    f"ne nomme pas la ligne, ne porte pas son statut et ne se "
                    f"subdivise pas. Sur `{key}` ces clés ne seraient lues nulle "
                    f"part : déplace-les sur `{base}`.")
        elif "." in key:
            errors.append(
                f"{fpath}: `{key}` n'est pas un nom de colonne — un point ne "
                f"désigne qu'une couche, et les couches sont "
                f"{', '.join(LAYER_KEYS)}. La valeur, elle, se désigne par le nom "
                f"NU (`{key.rpartition('.')[0]}`). Une colonne littérale portant un "
                f"point serait invisible au filtre et au tri du même nom.")
        ftype = f.get("type")
        if ftype is not None and ftype not in SCALAR_TYPES + COMPOSITE_TYPES:
            errors.append(f"{fpath}: type inconnu {ftype!r}")
        # oto#137 : des sous-champs sous une colonne SANS type. La validation ne
        # descend que dans un composite déclaré comme tel (`type`) : ses sous-champs,
        # et leurs exigences, ne seraient jamais lus. Une garde acceptée et inerte est
        # pire qu'une garde absente — on cesse de la chercher. Mesuré le 24/09/2026 :
        # aucun schéma de production ne porte cette forme, le refus ne casse personne.
        if ftype is None and ("fields" in f or "of" in f):
            porte = "fields" if "fields" in f else "of"
            forme = ('"type": "object", "fields": [...]' if porte == "fields"
                     else '"type": "list", "of": {...}')
            errors.append(
                f"{fpath}: `{porte}` déclaré sans `type` — les sous-champs ne sont lus "
                f"que sous une colonne composite typée, et ceux-ci ne seraient jamais "
                f"validés : leurs exigences resteraient inertes. Déclare "
                f"{{\"key\": \"{key}\", {forme}}}.")
        if ftype == "object":
            sub = f.get("fields")
            if not isinstance(sub, list) or not sub:
                errors.append(f"{fpath}: type=object exige fields:[...]")
            else:
                _validate_fields_def([x for x in sub if isinstance(x, dict)],
                                     fpath, errors)
        if ftype == "list":
            of = f.get("of")
            if of is None:
                errors.append(f"{fpath}: type=list exige of:<field-def>")
            elif isinstance(of, dict):
                # `max_items` posé dans `of` (oto#137) est refusé par le vocabulaire
                # de l'élément, avec la forme correcte (`cles_inconnues.phrase`).
                if isinstance(of.get("fields"), list):
                    _validate_fields_def(
                        [x for x in of["fields"] if isinstance(x, dict)], fpath, errors)
                elif of.get("type") is not None and \
                        of["type"] not in SCALAR_TYPES + COMPOSITE_TYPES:
                    errors.append(f"{fpath}.of: type inconnu {of.get('type')!r}")
            else:
                errors.append(f"{fpath}: of doit être un objet field-def")
        rw = f.get("required_when")
        if rw is not None and (not isinstance(rw, dict) or not rw):
            errors.append(f"{fpath}: required_when doit être un objet {{champ: valeur}}")
        elif isinstance(rw, dict):
            # La règle de la famille #329/#331 : une forme non interprétée se
            # REFUSE à la pose en nommant l'attendu — jamais stockée-inerte
            # (vécu #347 : une condition en liste était acceptée et désarmait
            # la contrainte pour TOUTES les valeurs, scalaires comprises).
            for ck, cv in rw.items():
                ok_scalaire = isinstance(cv, (str, int, float, bool))
                ok_liste = (isinstance(cv, (list, tuple)) and len(cv) > 0
                            and all(isinstance(x, (str, int, float, bool)) for x in cv))
                if not (ok_scalaire or ok_liste):
                    errors.append(
                        f"{fpath}: required_when — la condition de `{ck}` doit être "
                        f"une valeur ou une liste non vide de valeurs (requis quand "
                        f"la valeur du champ est / est parmi) ; reçu {cv!r}")
        # oto#75 : MÊME règle de famille (#329/#331/#347) — une forme non
        # interprétée se REFUSE devant celui qui la pose. Une couche mal
        # orthographiée (`commentaire`) serait stockée sans rien exiger, et
        # l'attribut a déjà vécu trois schémas de production dans cet état.
        # ⚠️ **La liste VIDE est ACCEPTÉE, et ce n'est pas une tolérance.** À
        # l'écriture, `required_layers_of` lit déjà `[]` comme « aucune couche
        # exigée » — la refuser ICI serait une asymétrie entre les deux moitiés du
        # même attribut. Elle a un coût mesuré : quatre tableaux VIVANTS portent
        # `required_layers: []`, dont deux chez une organisation cliente ; les
        # refuser rendrait leur schéma inpatchable — y compris pour un patch portant
        # sur une TOUT AUTRE colonne, puisque `patch_schema` repasse le schéma
        # FUSIONNÉ par `set_schema` (`schema_ops.py`). Un geste qui marchait aurait
        # cessé de marcher sur un schéma que personne n'a modifié.
        rl = f.get("required_layers")
        if rl is not None and not (
                isinstance(rl, (list, tuple))
                and all(isinstance(c, str) and c in LAYER_KEYS for c in rl)):
            errors.append(
                f"{fpath}: required_layers doit être une liste de couches parmi "
                f"{', '.join(repr(c) for c in LAYER_KEYS)} — la liste vide "
                f"n'exige rien et reste acceptée ; reçu {rl!r} — une couche que la "
                f"plateforme ne connaît pas n'exigerait rien, et son auteur croirait "
                f"la provenance exigée")
        ml = f.get("max_length")
        if ml is not None:
            if isinstance(ml, bool) or not isinstance(ml, int) or ml <= 0:
                errors.append(f"{fpath}: max_length doit être un entier > 0, reçu {ml!r}")
            elif ftype in COMPOSITE_TYPES:
                errors.append(
                    f"{fpath}: max_length ne borne qu'un champ scalaire "
                    f"(type={ftype} — borne le sous-champ concerné)")
        # #387 : le motif se refuse ICI, devant celui qui le pose — jamais à
        # l'écriture d'une ligne trois semaines plus tard. Un motif fautif accepté
        # puis inerte est le pire des deux mondes : son auteur croit avoir posé un
        # contrat. Trois refus, chacun nommant sa raison.
        motif = f.get("pattern")
        if motif is not None:
            # oto#103 (24/09/2026) : un motif SEUL s'accepte. Son coût se majore contre
            # la borne de lecture — `max_length`, sinon `PATTERN_MAX_SUBJECT`.
            bornes = borne_du_motif(f)
            if not isinstance(motif, str) or not motif:
                errors.append(
                    f"{fpath}: pattern doit être une expression régulière (une "
                    f"chaîne non vide), reçu {motif!r}")
            elif ftype in COMPOSITE_TYPES:
                errors.append(
                    f"{fpath}: pattern ne contraint qu'un champ scalaire "
                    f"(type={ftype} — pose-le sur le sous-champ concerné)")
            elif bornes > PATTERN_MAX_SUBJECT:
                errors.append(
                    f"{fpath}: pattern sur un champ borné à {bornes} caractères — "
                    f"maximum {PATTERN_MAX_SUBJECT} : au-delà, contraindre la FORME "
                    f"d'une valeur n'a plus de sens et son coût n'est plus majorable")
            else:
                raison = pattern_refusal(motif, bornes)
                if raison:
                    # Sans borne déclarée, le coût se majore sur la borne par défaut :
                    # le dire, sinon l'auteur ne sait pas que borner le champ peut
                    # suffire à faire passer le même motif.
                    sans_borne = ("" if max_length_of(f) else
                                  f" (sans `max_length`, le motif se majore sur "
                                  f"{PATTERN_MAX_SUBJECT} caractères : borner le "
                                  f"champ peut suffire)")
                    errors.append(f"{fpath}: pattern {motif!r} refusé — {raison}"
                                  f"{sans_borne}")
        # #586/#606 : les champs que l'appelant n'écrit pas. Sur une cible de couche,
        # `_COLUMN_ONLY_KEYS` a déjà parlé.
        if not layer and path == "fields":
            _validate_reserved_def(f, fpath, errors)


# ── colonnes calculées (oto-backend#1008) ───────────────────────────────────

def _validate_formulas_def(fields: list) -> list[str]:
    """Une colonne `type: "formula"` DOIT porter un texte `formula` qui PARSE
    (fonction du sous-ensemble fermé, grammaire correcte), ne référence QUE des
    colonnes déclarées au premier niveau, et ne chaîne PAS sur une autre colonne
    formule (v1). Refusé À LA POSE, jamais stocké-invalide."""
    errors: list[str] = []
    top_level = [f for f in fields if isinstance(f, dict)]
    colonnes = {f["key"] for f in top_level
                if isinstance(f.get("key"), str) and f["key"]}
    formules = {f["key"] for f in top_level
                if f.get("type") == "formula" and isinstance(f.get("key"), str)
                and f["key"]}
    for f in top_level:
        if f.get("type") != "formula":
            continue
        key = f.get("key")
        fpath = f"fields.{key or '?'}"
        texte = f.get("formula")
        if not isinstance(texte, str) or not texte.strip():
            errors.append(
                f"{fpath}: type=\"formula\" exige `formula` (texte OpenFormula "
                f"non vide)")
            continue
        try:
            _formule.valider(texte, colonnes, formules - {key}, champs_def=top_level)
        except _formule.FormulaError as e:
            errors.append(f"{fpath}: formule refusée — {e}")
    return errors
