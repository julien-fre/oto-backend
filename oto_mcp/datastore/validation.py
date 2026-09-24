"""VALIDER une ligne à l'écriture — le chemin chaud, et ses textes de refus.

`validate_row` est le point d'entrée : elle rend les raisons de refuser une ligne
fusionnée contre le format déclaré. Sous elle, `_row_errors` descend la déclaration et
`_type_error` juge chaque valeur — **mutuellement récursives** (un composite redescend
dans `_row_errors`) : c'est pourquoi elles ne se séparent pas.

Le reste est la PROSE du refus, et elle n'est pas décorative : lu par un agent en
boucle, un refus doit dire ce qui est attendu (`_forme_attendue`), pourquoi c'est exigé
maintenant (`_cause_required_when`, `_gated_by`) et sous quelle condition l'exigence
tomberait (`_clause_aiguillage`). Un refus muet fait rejouer le même appel.

Ce qu'il ne tient pas : la validité du FORMAT, jugée une fois à la pose
(`definition.py`) ; les couches exigées (`couches_exigees.py`) et les clés hors
référentiel (`hors_schema.py`), appelés d'ici ; les champs réservés
(`champs_reserves.py`), jugés sur le GESTE — payload + ligne en place — non sur une
ligne seule ; le coût d'un motif (`motifs.py`), qu'on ne fait ici qu'exécuter.
"""
from __future__ import annotations

import re
from datetime import datetime
from typing import Any, Optional

from .couches import (_is_empty, CLES_INTERNES, LAYER_KEYS, VIDE_DELIBERE, layer_value,
                      split_layer, unknown_layers, unwrap, vide_assume)
from . import charge_a_renvoyer as car
from .options_declarees import hors_des_options, montrable
from .motifs import _pattern_re
from .declaration import (_fields, borne_du_motif, cle_d_element, max_length_of,
                          pattern_of, status_field, validation_active)
from . import telephone
from .etats_declares import etats_trahis
from .types_declares import types_trahis
from .cycle_de_vie import lifecycle_of, refus_de_transition
from .hors_schema import _unknown_subkey_refusal, _unknown_subkeys
from .couches_exigees import couches_manquantes, gabarit_de_couche
from .phrases_de_refus import (
    _forme_attendue, _gated_by, _cause_required_when, _clause_aiguillage,
    _clause_un_seul_appel, cle_la_plus_proche, gabarit,
)

_NUM_RE = re.compile(r"^-?\d+(\.\d+)?$")


def _conformite_scalaire(value: Any, ftype: Optional[str], path: str) -> list[str]:
    """La FORME d'une valeur scalaire, sans son appartenance à une liste — que
    `_hors_options` juge ensuite. `json` et l'absence de type n'ont pas de forme à
    tenir : ils rendent `[]`."""
    if ftype == "text":
        return [] if isinstance(value, str) else [f"{path}: attendu text, reçu {type(value).__name__}"]
    if ftype == "number":
        if isinstance(value, bool):
            return [f"{path}: attendu number, reçu bool"]
        if isinstance(value, (int, float)):
            return []
        if isinstance(value, str) and _NUM_RE.match(value.strip()):
            return []  # coercible — l'agent écrit souvent "42"
        return [f"{path}: attendu number, reçu {value!r}"]
    if ftype == "bool":
        return [] if isinstance(value, bool) else [f"{path}: attendu bool, reçu {value!r}"]
    if ftype in ("date", "datetime"):
        if isinstance(value, str):
            try:
                datetime.fromisoformat(value.replace("Z", "+00:00"))
                return []
            except ValueError:
                pass
        return [f"{path}: attendu {ftype} ISO, reçu {value!r}"]
    if ftype == "url":
        if isinstance(value, str) and value.startswith(("http://", "https://")):
            return []
        return [f"{path}: attendu une URL http(s), reçu {value!r}"]
    if ftype == "email":
        if isinstance(value, str) and "@" in value and " " not in value.strip():
            return []
        return [f"{path}: attendu un e-mail, reçu {value!r}"]
    if ftype == "phone":
        # oto#103 : jugé sur la forme COMPACTE (`telephone.normaliser`) — la mise en
        # forme lisible passe, une phrase ou un identifiant ne passe pas.
        if telephone.est_un_numero(value):
            return []
        return [f"{path}: attendu un numéro de téléphone (international `+` et "
                f"indicatif, ou national en chiffres), reçu {value!r}"]
    return []


def _hors_options(value: Any, options: Optional[list], path: str,
                  hors: Optional[list]) -> list[str]:
    """L'appartenance à la liste DÉCLARÉE, pour tout type scalaire (#98).

    Jusqu'au 10/09/2026, seule la branche `enum` lisait `options` : sur un tableau
    strict, une liste posée sur un texte, un json ou une colonne sans type ne refusait
    rien, et le signalement du régime souple se taisait aussi dès que la validation
    était armée — personne ne voyait passer la valeur. La règle vient de
    `options_declarees`, la même que le signalement souple et que le relevé de
    l'existant à la pose.

    Le relevé structuré (`hors`) est rempli comme il l'était pour les enums : c'est lui
    qui permet à l'écriture d'ÉCARTER la valeur et d'écrire le reste (#667)."""
    if not hors_des_options(value, options):
        return []
    allowed = [str(o) for o in options]
    if hors is not None:
        hors.append({"champ": path, "valeur": value, "options": allowed})
    return [f"{path}: valeur {montrable(value)!r} hors options ({', '.join(allowed)})"]


def elements_reecrits(apres: Any, en_place: Any, cle: Optional[str]) -> Optional[set]:
    """Les RANGS des éléments de `apres` que le geste a écrits — `None` = tous.

    Ne se distingue que sur une liste fusionnée ÉLÉMENT PAR ÉLÉMENT (`of.key`, cf.
    `columns._merge_items`, dont c'est la lecture) : un élément est écrit s'il est
    neuf, sans identité, ou s'il sort de la fusion différent de celui qui portait son
    identité en place. Un élément renvoyé tel quel — le geste normal : relire la
    liste, corriger UN contact, tout renvoyer — sort identique, il n'est pas écrit.

    `None` (tous écrits) sans `of.key`, sans état antérieur connu (création,
    remplacement), ou quand la liste en place porte une identité en double : la
    fusion l'a alors remplacée en bloc, tout ce qui est là vient du geste."""
    if not cle or not isinstance(apres, list) or not isinstance(en_place, list):
        return None
    index: dict = {}
    for it in en_place:
        if not isinstance(it, dict):
            continue
        v = unwrap(it.get(cle))
        if v in (None, ""):
            continue
        if v in index:
            return None
        index[v] = it
    ecrits: set = set()
    for i, it in enumerate(apres):
        v = unwrap(it.get(cle)) if isinstance(it, dict) else None
        if v in (None, "") or index.get(v) != it:
            ecrits.add(i)
    return ecrits


def _type_error(value: Any, ftype: Optional[str], path: str,
                fields: Optional[list] = None, of: Optional[dict] = None,
                options: Optional[list] = None, *,
                closed: bool = False,
                hors: Optional[list] = None,
                ecrits: Optional[set] = None,
                gelees: Optional[list] = None,
                charge: Optional[dict] = None,
                chemin: tuple = car.RACINE,
                decl: Optional[dict] = None) -> list[str]:
    """Erreurs de conformité d'UNE valeur à un type déclaré (récursif).

    `charge`/`chemin` = la charge à renvoyer (oto#135) et l'endroit de cette valeur
    (`charge_a_renvoyer`) : une faute note le gabarit de sa déclaration (`decl`, sinon
    reconstituée des arguments). `charge` absente = rien à noter.

    `ecrits` (liste seulement) = les rangs des éléments que le geste écrit
    (`elements_reecrits`), `None` = tous. Un élément NON écrit n'est pas jugé contre
    le geste : ce qui cloche en lui part dans `gelees`, comme une colonne non écrite
    (oto#137). Sans ça, un ancien contact incomplet bloquerait l'écriture d'un autre,
    et le seul geste possible serait de réparer ce qu'on n'est pas venu toucher.

    `closed` = le référentiel de CE composite est fermé (#544) : un attribut que sa
    déclaration ne nomme pas est refusé, au lieu d'être traversé en silence. Il se
    propage vers le bas — une liste d'objets dans un objet reste fermée.

    `hors` (liste mutable, optionnelle) = le relevé STRUCTURÉ des valeurs hors
    options (#667), rempli en chemin : `{champ, valeur, options}`. Il existe pour
    que l'appelant puisse ÉCARTER la valeur sans reparser le message — un refus
    français relu comme un contrat est un contrat déguisé. Optionnel par
    construction : ce validateur reste pur si personne ne le lui passe."""
    decl = decl or {"type": ftype, "fields": fields, "of": of, "options": options}

    def _faute(errs: list[str]) -> list[str]:
        if errs:
            car.noter(charge, chemin, gabarit(decl))
        return errs

    if ftype == "enum":
        # `options` absentes ⇒ enum libre (le client rend un select vide, pas d'erreur).
        if not isinstance(value, str):
            return _faute([f"{path}: attendu une valeur d'énumération, reçu {value!r}"])
        return _faute(_hors_options(value, options, path, hors))
    if ftype == "object":
        if not isinstance(value, dict):
            return _faute([f"{path}: attendu object, reçu {type(value).__name__}"])
        return _row_errors(fields or [], value, path, closed=closed, hors=hors,
                           charge=charge, chemin=chemin)
    if ftype == "list":
        if not isinstance(value, list):
            return _faute([f"{path}: attendu list, reçu {type(value).__name__}"])
        errors: list[str] = []
        of = of or {}
        sub_fields = of.get("fields")
        cle = of.get("key") if isinstance(of.get("key"), str) else None
        # Un attribut inconnu se nomme UNE fois pour toute la colonne, sur le premier
        # élément qui le porte : les items d'une liste partagent leur déclaration,
        # donc 300 contacts fautifs diraient 300 fois la même chose. Même borne que
        # l'agrégation du relevé `hors_schema` (`clé[].sous_clé`), et même raison :
        # un refus qu'on ne peut pas lire ne vaut pas mieux qu'un silence.
        vus: set = set()
        vus_geles: set = set()
        for i, item in enumerate(value):
            ipath = f"{path}[{i}]"
            ecrit = ecrits is None or i in ecrits
            # Un élément non écrit se juge à part : ni dans le refus, ni dans le relevé
            # `hors` (qui ÉCARTERAIT une valeur que le geste n'a pas posée).
            cible = errors if ecrit else []
            # L'élément SEUL, désigné par son identité quand la liste en déclare une
            # (oto#135) : jamais la liste entière dans la charge.
            ident = unwrap(item.get(cle)) if cle and isinstance(item, dict) else None
            ichemin = car.element(chemin, i, (cle, ident)
                                  if isinstance(ident, (str, int)) and ident != "" else None)
            icharge = charge if ecrit else None
            if isinstance(sub_fields, list):
                if not isinstance(item, dict):
                    cible.append(f"{ipath}: attendu object, reçu {type(item).__name__}")
                    car.noter(icharge, ichemin, gabarit({"type": "object",
                                                         "fields": sub_fields}))
                else:
                    cible.extend(_row_errors(
                        [x for x in sub_fields if isinstance(x, dict)], item, ipath,
                        closed=closed, vus=vus if ecrit else vus_geles,
                        hors=hors if ecrit else None,
                        charge=icharge, chemin=ichemin, cle_d_identite=cle))
            elif of.get("type") or of.get("options"):
                cible.extend(_type_error(item, of.get("type"), ipath,
                                         of.get("fields"), of.get("of"),
                                         of.get("options"), closed=closed,
                                         hors=hors if ecrit else None,
                                         charge=icharge, chemin=ichemin, decl=of))
            if not ecrit and gelees is not None:
                gelees.extend({"champ": e.split(":", 1)[0], "refus": e} for e in cible)
        return errors
    # Tout autre type scalaire — et l'absence de type — passe par la MÊME liste (#98) :
    # d'abord la forme, puis l'appartenance, jamais les deux sur une même valeur. Deux
    # refus pour un seul relevé `hors` feraient refuser la fiche entière, là où
    # l'écriture sait écarter la valeur et écrire le reste (#667).
    return _faute(_conformite_scalaire(value, ftype, path)
                  or _hors_options(value, options, path, hors))


#: oto#204 : ce qu'un refus de requis dit du vide ASSUMÉ. Deux gestes, et le second ferme
#: le défaut du lot : une liste relue au défaut puis renvoyée rend `""` là où la lecture
#: avait un vide assumé — le refus doit dire comment le relire, pas seulement quoi écrire.
_CLAUSE_VIDE_ASSUME = (
    f" — si aucune source ne donne cette valeur, écris `{VIDE_DELIBERE}` ; si tu renvoies "
    f"une ligne lue, relis-la avec `empties=sentinel` : un vide déjà assumé y revient "
    f"`{VIDE_DELIBERE}`, à renvoyer tel quel")


def _row_errors(fields: list, data: dict, path: str,
                written: Optional[set] = None, *,
                strict: bool = False, closed: bool = False,
                vus: Optional[set] = None,
                details: Optional[dict] = None,
                hors: Optional[list] = None,
                gelees: Optional[list] = None,
                en_place: Optional[dict] = None,
                charge: Optional[dict] = None,
                chemin: tuple = car.RACINE,
                cle_d_identite: Optional[str] = None) -> list[str]:
    """Erreurs d'un (sous-)record. `written` = clés effectivement RÉÉCRITES par ce
    geste (None = toutes) : la borne de longueur, le motif, la fermeture d'un
    composite **et le TYPE** s'y restreignent — eux seuls, cf. `validate_row`. La
    récursion dans un sous-record repart à None — remplacer une clé de premier niveau
    réécrit tout ce qu'elle contient.

    `gelees` = liste OUT (patron `hors`) où part le type qui échoue sur une colonne
    que le geste **n'écrit pas**. Elle ne refuse plus : elle se DIT.

    `en_place` (premier niveau) = la ligne EN PLACE avant la fusion, quand il y en a une.
    Elle ne sert qu'aux listes fusionnées par élément (`of.key`) : seuls les éléments
    que le geste écrit y sont jugés (`elements_reecrits`, oto#137).

    `charge`/`chemin` = la charge à renvoyer et l'endroit de ce (sous-)record (oto#135,
    `charge_a_renvoyer`) : chaque faute y note le gabarit du champ à corriger. Propagée
    aux sous-records, à la différence de `details` : un chemin structuré n'est pas
    ambigu. `cle_d_identite` = le `of.key` de la liste dont ce record est un élément —
    `@empty` n'y est pas une alternative.

    `strict` = le tableau déclare `strict: true`. Il n'interdit rien ICI (une clé
    inconnue au premier niveau crée une colonne libre, droit du contrat 0016 : elle
    est SIGNALÉE par `hors_schema`, jamais refusée — arbitrage #294) ; il FERME les
    composites déclarés d'un cran plus bas (#544). `closed` porte cette fermeture.

    Pourquoi l'asymétrie, alors que « strict s'applique récursivement » : au premier
    niveau, un nom inconnu crée une vraie colonne, que l'interface affiche et qu'on
    peut déclarer après coup — c'est ce qui permet d'explorer un tableau avant de le
    typer. Dans un composite déclaré, il n'existe pas de « sous-colonne libre » :
    `of.fields` EST le seul référentiel, et l'attribut serait stocké là où rien ne le
    lit. Le geste qu'on protège en haut n'existe pas en bas.

    `vus` = les attributs déjà nommés pour la colonne-liste courante (borne du
    refus, cf. `_type_error`).

    `details` (dict mutable, optionnel) = le refus STRUCTURÉ que l'appelant récupère,
    aujourd'hui `expected_column` (#545). Renseigné au PREMIER cas rencontré et jamais
    écrasé : un refus en porte une, pas une liste — le message, lui, les dit toutes.
    Non propagé aux sous-records : « la colonne attendue » d'un sous-champ imbriqué
    serait ambiguë côté client, et un pointeur ambigu ne vaut pas mieux qu'aucun."""
    errors: list[str] = []
    if closed:
        for cle in _unknown_subkeys(fields, data):
            if vus is not None:
                if cle in vus:
                    continue
                vus.add(cle)
            errors.append(_unknown_subkey_refusal(
                f"{path}.{cle}" if path else cle, fields))
            # La charge nomme la clé DÉCLARÉE la plus proche, avec son gabarit : la
            # valeur envoyée sous le mauvais nom n'est pas recopiée.
            par_cle = {str(x["key"]): x for x in fields if x.get("key")}
            proche = cle_la_plus_proche(cle, list(par_cle))
            if proche is not None:
                car.noter(charge, car.champ(chemin, proche), gabarit(par_cle[proche]))
    # Les colonnes-AIGUILLAGE de ce niveau, et ce qu'elles rendent requis.
    portes = _gated_by(fields)
    for f in fields:
        key = f.get("key")
        if not key:
            continue
        fpath = f"{path}.{key}" if path else key
        # Le marqueur du vide assumé (oto#204) est une clé INTERNE, pas un sous-champ.
        inconnues = [k for k in unknown_layers(data.get(key)) if k not in CLES_INTERNES]
        if inconnues:
            proches = [cle_la_plus_proche(k, list(LAYER_KEYS)) for k in inconnues]
            dites = [f"`{p}` pour {k!r}" for k, p in zip(inconnues, proches) if p]
            errors.append(
                f"{fpath}: sous-champ(s) inconnu(s) {', '.join(repr(k) for k in inconnues)}"
                f" — disponibles : {', '.join(LAYER_KEYS)}"
                + (f" (le plus proche : {', '.join(dites)})" if dites else "")
                + ". Une couche stockée sans être lue donnerait l'illusion d'une "
                "provenance renseignée.")
            for p in proches:
                if p:
                    car.noter(charge, car.champ(car.champ(chemin, str(key)), p),
                              gabarit_de_couche(p))
        # Déballer avant de juger : c'est la VALEUR qui doit respecter le type, la
        # borne et les options — pas son enveloppe. Sans ça un schéma strict refuse
        # toute écriture en couches, donc la primitive est inutilisable là où elle
        # sert le plus.
        #
        # Une clé POINTÉE (#377) désigne une couche : `qualification.comment` est la
        # justification portée par la colonne `qualification`, pas une colonne du
        # même nom. Le contrôle lisait `data["qualification.comment"]` — un nom que
        # `_refuse_dotted_names` interdit précisément d'ÉCRIRE : la contrainte ne
        # pouvait donc jamais être satisfaite, et refusait jusqu'aux écritures qui
        # portaient bien le commentaire. Accepté à la pose, bloquant à l'écriture :
        # un cran plus grave qu'inerte, parce que la déclaration avait l'air d'avoir
        # pris.
        base, layer = split_layer(key)
        value = (layer_value(data.get(base), layer) if layer
                 else unwrap(data.get(key)))
        # Ce que le geste POSE — hissé ici parce que la fermeture d'un composite s'y
        # restreint, exactement comme la borne et le motif : la validation porte sur
        # le MERGÉ, donc juger un composite que le geste ne réécrit pas rendrait
        # inécritable toute ligne portant déjà un attribut hors format, y compris
        # pour un patch sans rapport (les 23 lignes gelées d'oto-backend#284).
        pose = written is None or key in written
        # Où ce champ vit dans la charge à renvoyer (oto#135). Une cible de couche
        # (`qualification.comment`) s'y écrit comme elle s'écrit : sous sa colonne.
        fchemin = car.champ(chemin, base)
        if layer:
            fchemin = car.champ(fchemin, layer)
        fcharge = charge if pose else None
        required = bool(f.get("required"))
        rw = f.get("required_when")
        if not required and isinstance(rw, dict) and rw:
            # Une condition en LISTE = requis quand la valeur ∈ liste (#347).
            # Avant, str(liste) ne matchait jamais : la déclaration qui semblait
            # ÉLARGIR la garde la rendait inerte, sans un mot.
            # ⚠️ La valeur de condition se DÉBALLE (unwrap) comme toute valeur
            # jugée — une qualification écrite en couches ({"valeur": …}) est un
            # dict brut qui ne matche rien : la garde était désarmée par le
            # geste NORMAL des agents (justifier en couches), et par tout merge
            # sur une ligne portant déjà une couche (prouvé en re-validation :
            # 5 fiches écartées sans motif, aucun refus).
            required = all(
                str(unwrap(data.get(k))) in {str(x) for x in v}
                if isinstance(v, (list, tuple))
                else str(unwrap(data.get(k))) == str(v)
                for k, v in rw.items())
        if _is_empty(value):
            # oto#204 : le vide ASSUMÉ (marqueur posé par la résolution de `@empty`)
            # satisfait l'obligation ; une chaîne vide ordinaire, non.
            if required and not (not layer and vide_assume(data.get(key))):
                cause = (_cause_required_when(rw)
                         if not f.get("required") and rw else "")
                # #545 : la colonne était déjà nommée ; ce qui manquait, c'est sa
                # FORME — et la prévention du geste suivant. Sans elle, l'agent
                # corrige en écrivant le motif DANS l'aiguillage, se fait refuser une
                # seconde fois, et paie deux allers-retours pour une seule ligne :
                # exactement la séquence mesurée (35 refus sur 105, 27 rattrapés au
                # coup d'après).
                # #649 : la clause « un seul appel » n'a de sens que sur un PATCH qui
                # cible ce champ SANS toucher à l'aiguillage. À la création
                # (written=None) il n'y a pas d'état antérieur à quitter ; et quand
                # CE geste écrit déjà une clé de la condition, lui conseiller de
                # « mettre les deux dans le même appel » est faux — il vient de le
                # faire, c'est la valeur qu'il a choisie qui ARME la garde, et le
                # refus juste est alors « le motif manque », rien de plus.
                seul_appel = (written is not None and pose
                              and isinstance(rw, dict) and rw
                              and not (set(rw) & written))
                errors.append(f"{fpath}: champ requis manquant{cause} — "
                              f"elle attend {_forme_attendue(f)}"
                              + _clause_aiguillage(fields, rw)
                              + (_clause_un_seul_appel(rw) if seul_appel else "")
                              + _CLAUSE_VIDE_ASSUME)
                if details is not None:
                    details.setdefault("expected_column", str(key))
                attendu = gabarit(f)
                car.noter(charge, fchemin,
                          car.avec_vide(attendu)
                          if not layer and car.vide_permis(chemin, str(key), cle_d_identite)
                          else attendu)
            continue
        # `options` sans type compte aussi (#98) : la liste est déclarée, la valeur doit
        # y être — le type absent dit seulement qu'il n'y a pas de FORME à tenir.
        if f.get("type") or f.get("options"):
            ecrits = (elements_reecrits(value, unwrap((en_place or {}).get(key)),
                                        cle_d_element(f))
                      if pose and en_place is not None else None)
            errs_type = _type_error(value, f.get("type"), fpath,
                                    f.get("fields"), f.get("of"), f.get("options"),
                                    closed=closed or (strict and pose),
                                    hors=hors, ecrits=ecrits, gelees=gelees,
                                    charge=fcharge, chemin=fchemin, decl=f)
            # #545 : la colonne qui vient de refuser est-elle un AIGUILLAGE dont une
            # autre colonne dépend ? Alors la chaîne libre qu'on y a écrite a une
            # destination déclarée, et le refus doit la donner — c'est le cas
            # majoritaire des 35 : « le motif va dans `retraitement_motif`, pas dans
            # `retraitement` ». La condition est stricte (énuméré + options déclarées
            # + valeur texte hors liste) : hors de là, le pointeur serait une
            # devinette.
            gardees = portes.get(str(key)) or []
            if (errs_type and gardees and f.get("type") == "enum"
                    and isinstance(value, str)
                    and value not in [str(o) for o in (f.get("options") or [])]):
                cible = gardees[0]
                errs_type[-1] += (
                    f" — cette valeur va dans `{cible.get('key')}` "
                    f"({_forme_attendue(cible)}), pas dans `{key}`")
                if details is not None:
                    details.setdefault("expected_column", str(cible.get("key")))
                car.noter(fcharge, car.champ(chemin, str(cible.get("key"))),
                          gabarit(cible))
                # #667 : cette valeur-là a une DESTINATION déclarée — elle est mal
                # rangée, pas indésirable. L'écarter écrirait une fiche qui prétend
                # ne pas avoir été retraitée, et l'agent verrait un succès : la
                # corruption silencieuse, en pire que la perte. Le relevé porte donc
                # la destination, et l'écartement s'y refuse.
                if hors and hors[-1].get("champ") == fpath:
                    hors[-1]["destination"] = str(cible.get("key"))
            if errs_type and not pose:
                # La colonne n'est pas écrite par ce geste. La refuser rendrait la
                # ligne INÉCRITABLE pour toujours, sur n'importe quel champ — c'est
                # exactement ce que la restriction de la borne et du motif a corrigé
                # (23 lignes gelées chez un client), et le type y avait été oublié.
                #
                # ⚠️ On ne se tait pas pour autant : la valeur en base ne passe plus
                # le format déclaré, et l'agent qui écrit à côté est le mieux placé
                # pour le savoir. Refuser gèle, taire cache — on DIT.
                if gelees is not None:
                    gelees.append({"champ": fpath, "refus": errs_type[0]})
            else:
                errors.extend(errs_type)
        mi = f.get("max_items")
        if (isinstance(mi, int) and not isinstance(mi, bool) and mi > 0
                and isinstance(value, list) and len(value) > mi):
            # Même forme que la borne de longueur : le CONSTATÉ autant que la borne,
            # sinon le refus fait deviner de combien on dépasse.
            #
            # Et même RESTRICTION qu'elle, alignée le 07/09/2026 : c'est une propriété
            # de la valeur qu'on POSE. Sur une colonne que le geste n'écrit pas, une
            # liste déjà trop longue rendrait la ligne inécritable pour toujours, y
            # compris sur un champ sans rapport — le défaut que le type venait de
            # quitter, laissé sur son voisin immédiat.
            trop = f"{fpath}: {len(value)} éléments, maximum {mi}"
            # Pas de charge : dire QUELS éléments retirer demanderait de recopier la
            # liste, et c'est à l'agent de choisir.
            if pose:
                errors.append(trop)
            elif gelees is not None:
                gelees.append({"champ": fpath, "refus": trop})
        ml = max_length_of(f)
        trop_long = False
        if ml and pose:
            n = len(value) if isinstance(value, str) else len(str(value))
            if n > ml:
                # La longueur CONSTATÉE autant que la borne : un refus qui ne dit
                # pas de combien on dépasse fait deviner (signal #383).
                errors.append(f"{fpath}: {n} caractères, maximum {ml}")
                car.noter(fcharge, fchemin, gabarit(f))
                trop_long = True
        # #387 : la FORME, là où la taille ne sépare rien. Restreint aux clés que le
        # geste ÉCRIT, comme la borne et pour la même raison : la validation porte
        # sur le résultat MERGÉ, donc sans cette restriction une ligne déjà non
        # conforme deviendrait inécritable pour n'importe quel patch, y compris sur
        # un champ sans rapport (23 lignes gelées chez un client, oto-backend#284).
        # Sauté quand la valeur dépasse déjà la borne : c'est ELLE qui garantit que
        # le motif s'exécute sur un sujet de taille connue — et le refus est déjà
        # posé, l'ajouter en double ne dirait rien de plus.
        motif = pattern_of(f)
        if motif and pose and not trop_long:
            texte = value if isinstance(value, str) else str(value)
            # oto#103 : un motif SANS `max_length` s'applique ; le coût en a été majoré
            # contre `borne_du_motif`, et c'est cette borne qui garantit qu'il ne
            # s'exécute que sur un sujet de taille connue. Au-delà : refus, sans
            # exécuter le motif.
            if len(texte) > borne_du_motif(f):
                errors.append(
                    f"{fpath}: {len(texte)} caractères — une colonne à motif se lit sur "
                    f"{borne_du_motif(f)} caractères au plus")
                car.noter(fcharge, fchemin, gabarit(f))
            elif not _pattern_re(motif).search(texte):
                # La valeur CONSTATÉE autant que le motif attendu : sans le motif, le
                # refus ne laisse rien à corriger ; sans la valeur, il fait relire la
                # ligne pour savoir ce qui coince.
                errors.append(
                    f"{fpath}: {texte!r} ne suit pas le motif `{motif}`")
                car.noter(fcharge, fchemin, gabarit(f))
    return errors


def validate_row(schema: Optional[dict], merged: dict, *,
                 prev_status: Any = None,
                 written: Optional[set] = None,
                 details: Optional[dict] = None,
                 hors: Optional[list] = None,
                 gelees: Optional[list] = None,
                 en_place: Optional[dict] = None,
                 pose: Optional[dict] = None) -> list[str]:
    """Erreurs d'une row TELLE QU'ELLE SERA ÉCRITE (le résultat mergé, pas le
    patch) : required / required_when / types / structure imbriquée — si la
    validation est active — plus le cycle de vie (états + transitions) dès qu'un
    `lifecycle` est déclaré, même hors mode strict. Liste vide = OK.

    Sur un tableau `strict`, un composite DÉCLARÉ est en plus un référentiel FERMÉ
    (#544) : un attribut absent de `of.fields` / `fields` est refusé. La fermeture
    ne descend que dans les composites que le geste RÉÉCRIT — même restriction que
    `max_length`, et même raison.

    `written` = les clés que ce geste réécrit (None = la row entière, cas d'un
    insert ou d'un remplacement). **Quatre** contrôles s'y restreignent, et eux
    seuls : la borne `max_length`, le motif `pattern`, la fermeture d'un composite
    et le **TYPE** — ce sont des propriétés de la valeur qu'on POSE, pas de l'état
    final. Sans ça, une valeur trop longue (ou hors format, ou hors type) déjà en
    base ferait échouer tout patch ultérieur de la ligne, même portant sur un champ
    sans rapport (signal #383, et les 23 lignes gelées d'oto-backend#284). Le reste
    continue de se juger sur le mergé : un requis manquant est un défaut de la row,
    quel que soit le geste qui l'y laisse.

    ⚠️ **Le type est arrivé le 07/09/2026, et son absence était un OUBLI, pas un
    choix.** La restriction des trois premiers cite en toutes lettres le défaut
    qu'elle ferme — « une valeur déjà en base ferait échouer tout patch ultérieur,
    même sur un champ sans rapport » — et le type produisait exactement ça, signalé
    par un tenant sur des lignes de socle devenues inécritables. Il ne se TAIT pas
    pour autant : ce qu'il ne refuse plus part dans `gelees`.

    `gelees` = liste OUT : les colonnes dont la valeur EN BASE ne passe plus le type
    déclaré, alors que ce geste ne les écrit pas. Ni refus ni silence — l'agent qui
    écrit à côté est le mieux placé pour l'apprendre.

    `details` (dict mutable, optionnel) = le refus STRUCTURÉ, rempli en chemin —
    aujourd'hui `expected_column`, la colonne où la valeur aurait dû atterrir (#545).
    Optionnel par construction : un validateur PUR ne doit pas exiger un accumulateur
    de ses appelants pour rendre ses erreurs.

    `en_place` = la ligne EN PLACE, sur les chemins qui fusionnent (patch, clé métier) :
    dans une liste fusionnée par élément (`of.key`), un élément que le geste n'écrit
    pas n'est pas jugé contre lui — ce qui y cloche part dans `gelees` (oto#137).
    Absente (création, remplacement), tout ce qui est posé vient du geste.

    ⚠️ **`details` porte aussi la CHARGE À RENVOYER** (oto#135) : `a_renvoyer`, un
    fragment de `row` qui ne contient que les champs à corriger, marqués d'un gabarit,
    et `a_renvoyer_elements` quand ce sont des éléments de liste
    (`charge_a_renvoyer.rendre`). Quatre familles la notent : requis manquant,
    type ou format, sous-champ inconnu, couche exigée.

    `pose` = ce que CE geste a nommé par colonne, AVANT fusion, sur les chemins qui
    fusionnent. Seul `required_layers` le lit (oto#75, complément du 11/09/2026) : une
    écriture qui ne pose que des couches ne juge pas la valeur EN PLACE."""
    errors: list[str] = []
    charge: Optional[dict] = {} if details is not None else None
    if validation_active(schema):
        # required_when se juge sur la row finale (le statut mergé, pas l'ancien)
        errors.extend(_row_errors(_fields(schema), merged, "", written,
                                  strict=bool(schema.get("strict")),
                                  details=details, hors=hors, gelees=gelees,
                                  en_place=en_place, charge=charge))
    # oto#75 barreau 1 : HORS du garde `validation_active`, comme le cycle de vie
    # ci-dessous — la déclaration `required_layers` s'arme elle-même.
    errors.extend(couches_manquantes(schema, merged, written=written, charge=charge,
                                     pose=pose))
    # 08/09/2026 — même raison, même place : un `type` déclaré s'arme lui-même. Le
    # contrôle existait sous `validation_active` et n'y voyait rien passer (0 violation
    # sur 88 tableaux) pendant que 248 tableaux sans validation en portaient 118.
    errors.extend(types_trahis(schema, merged, written=written, gelees=gelees,
                               charge=charge))
    # 09/09/2026 — le cran suivant de la même famille : une colonne SECONDAIRE qui
    # déclare ses états les fait respecter, elle aussi. Seule la file était vérifiée ;
    # 131 colonnes du parc déclaraient une liste que personne n'appliquait. Mesuré
    # avant de brancher : zéro valeur hors liste sur 8 646 cellules — la garde ne
    # refuse rien d'existant, elle ferme la porte avant qu'on la pousse.
    errors.extend(etats_trahis(schema, merged, written=written, gelees=gelees))
    lc = lifecycle_of(schema)
    if lc:
        sf = status_field(schema)
        key = sf.get("key") if sf else None
        # ⚠️ La valeur se DÉBALLE, comme partout ailleurs dans cette validation
        # (#586, 29/08). Trois lignes plus haut dans le chemin d'écriture, la
        # plateforme pose elle-même `<champ>.origine` sur une colonne déclarée
        # `origine: "system"` — la colonne d'état devient
        # `{'valeur': 'enrichi', 'origine': 'a_enrichir'}` — et ce contrôle la lisait
        # BRUTE : il refusait « état inconnu » sur la ligne que la plateforme venait
        # elle-même de compléter. Zéro fiche écrite sur cent, campagne arrêtée.
        # *Deux gestes voisins qui lisent la même colonne doivent la lire pareil* —
        # les contrôles de champ déballaient déjà, chacun après un défaut du même
        # genre (#329 les couches, #347 `required_when`).
        new = unwrap(merged.get(key)) if key else None
        if new is not None:
            states = {str(s) for s in lc.get("states") or []}
            # L'état est-il POSÉ par ce geste ? Même partage que le type et ses
            # quatre voisins (07/09/2026) : « cet état est-il permis » juge une
            # VALEUR, donc ce que l'appel écrit. Un état stocké devenu invalide —
            # parce qu'on l'a retiré de la liste déclarée depuis — gelait sinon la
            # ligne entière, y compris pour une écriture sans rapport, et pour
            # toujours. C'était le SIXIÈME contrôle de la famille, et le seul que
            # personne n'avait rapporté : il ne sortait pas d'un signalement mais
            # d'une vérification faite en cherchant autre chose.
            #
            # La TRANSITION, elle, n'a pas besoin d'être gardée ici : elle ne se
            # juge que si l'état change, donc que si le geste l'écrit.
            pose_etat = written is None or key in written
            if states and str(new) not in states:
                inconnu = f"{key}: état inconnu {new!r} (états: {sorted(states)})"
                if pose_etat:
                    errors.append(inconnu)
                elif gelees is not None:
                    gelees.append({"champ": str(key), "refus": inconnu})
            # L'état PRÉCÉDENT se déballe aussi : dès la deuxième écriture la ligne
            # porte des couches, donc le cas normal est un objet, pas un mot.
            elif prev_status is not None and str(unwrap(prev_status)) != str(new):
                transitions = lc.get("transitions")
                if isinstance(transitions, dict):
                    allowed = {str(t)
                               for t in transitions.get(str(unwrap(prev_status))) or []}
                    if str(new) not in allowed:
                        errors.append(refus_de_transition(
                            str(key), str(unwrap(prev_status)), str(new),
                            sorted(allowed)))
    if errors and charge:
        details.update(car.rendre(charge))
    return errors
