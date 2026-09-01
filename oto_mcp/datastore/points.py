"""Un nom qui porte un POINT : ce qu'on range, ce qu'on traduit, ce qu'on refuse.

**La règle qui tient ce module, et c'est la seule : ce qu'on SERT doit pouvoir être
réécrit tel quel.**

Une colonne porte des annotations (`origine`, `comment`, `link`). Elles sont STOCKÉES
imbriquées sous la colonne, et SERVIES à plat (`flat_layers`) : une lecture rend
`site_web` **et** `site_web.comment` comme deux clés de premier niveau. Ce n'est pas un
détail de forme — c'est ce qui garantit que le nom nu rend toujours la valeur, sans quoi
`row["email"]` cesserait de rendre un e-mail le jour où quelqu'un pose une source, et
tout consommateur casserait en silence.

⚠️ **Le défaut qu'on ferme ici est né du refus de #685**, qui interdisait TOUTE clé
pointée à l'écriture. Le refus était juste — une colonne littérale `champ.comment` est
invisible au filtre et au tri du même nom — mais il ne distinguait pas la clé fautive de
notre propre lecture réémise. Conséquences mesurées, toutes trois sur des gestes de
première classe :

- un agent qui relit une fiche et la **réémet entière** (le geste dominant, #390)
  renvoie `site_web.comment` — et se faisait refuser ;
- l'export CSV du tableau de bord bâtit ses colonnes sur les clés servies, donc il
  exporte `site_web.comment` ; le **réimport** en faisait une colonne littérale ;
- un en-tête de tableur parfaitement ordinaire (`N.SIREN`, `Tel.mobile`) était refusé
  alors qu'il n'a rien à voir avec nos annotations.

⚠️ **L'alternative a été écartée et il ne faut pas y revenir** : étiqueter les
annotations à la lecture (un objet à part) serait plus propre, mais changerait le
contrat de lecture pour tous les consommateurs actuels. *On ne change pas la forme
servie pendant que quelqu'un écrit dessus.*

## L'ordre de lecture d'un nom — il est unique, et il compte

1. `<colonne>.<annotation>`, annotation connue **et** colonne réelle (du geste, de la
   ligne, ou du schéma) → on **RANGE** dans l'annotation, en forme imbriquée.
   *L'aller-retour se referme.*
2. autre nom pointé venu d'un **EN-TÊTE de fichier** → on **TRADUIT en le disant**. Une
   convention d'adressage interne n'a pas à obliger un tiers à renommer son tableur.
3. autre nom pointé venu d'une **CLÉ d'appel** → on **REFUSE en nommant la forme
   attendue**. Là c'est une adresse fautive, et l'appelant doit l'apprendre.
   ⚠️ Le NDJSON ne traduit rien : il porte des clés, pas des étiquettes.
4. le nom obtenu est **déjà pris** → on **REFUSE en nommant les deux**. On ne fusionne
   jamais deux colonnes en silence.

Le cas 1 passe AVANT le cas 2 : traduire d'abord ferait de `site_web.comment` une
troisième colonne `site_web_comment` au lieu de restaurer l'annotation.
"""
from __future__ import annotations

from typing import Any, Callable, Optional

from . import schema as dsv2
from .errors import RowValidationError


def _socle_accueille_une_couche(socle: Any) -> bool:
    """La valeur déjà nommée pour cette colonne peut-elle recevoir une annotation ?

    Un scalaire, une liste, `None` : oui — ils deviennent la `valeur` de la colonne à
    couches, ce qui est exactement l'aller-retour (la lecture sert la valeur nue à côté
    de ses couches). Un dict fait de couches connues : oui — on lui ajoute la sienne.

    ⚠️ Un dict NON VIDE qui n'est PAS fait de couches est un objet métier (`json`) :
    l'envelopper dans `{"valeur": …}` changerait sa forme stockée sans que personne
    l'ait demandé. On ne range pas — et on le dit avec sa vraie raison, parce que le
    refus du cas 3 dirait « cette colonne n'existe pas », ce qui serait faux ici."""
    return not (isinstance(socle, dict) and socle and not dsv2.names_layers(socle))


def _refus_de_collision(base: str, couche: str, cle: str) -> RowValidationError:
    return RowValidationError([
        f"`{cle}` et `{base}.{couche}` (écrit en forme imbriquée dans `{base}`) "
        f"désignent la MÊME annotation, avec deux valeurs. Rien n'a été écrit : on ne "
        f"fusionne jamais deux écritures d'un même champ en silence. Garde l'une des "
        f"deux formes."])


def _ranger_une_fiche(fiche: dict) -> dict:
    """Un item de colonne-tableau est une FICHE : ses attributs suivent la règle du
    premier niveau, un cran plus bas.

    `_served_item` sert `item["email.origine"]` à plat, exactement comme
    `row["email.origine"]` — donc il doit se réécrire tel quel, exactement pareil. Ici
    la colonne « réelle » ne peut venir que du GESTE : un item n'a ni schéma ni
    identité, il n'y a rien d'autre à consulter."""
    out = {k: _ranger_les_items(v) for k, v in fiche.items()}
    for cle in list(out):
        adresse = dsv2.layer_address(cle)
        if adresse is None or adresse[0] not in out:
            continue
        base, couche = adresse
        socle = out[base]
        if not _socle_accueille_une_couche(socle):
            continue
        if dsv2.names_layers(socle) and couche in socle:
            raise _refus_de_collision(base, couche, cle)
        val = out.pop(cle)
        out[base] = ({**socle, couche: val} if dsv2.names_layers(socle)
                     else {dsv2.VALUE_LAYER: socle, couche: val})
    return out


def _ranger_les_items(valeur: Any) -> Any:
    """Les fiches d'une colonne-tableau, rangées. Le reste traverse intact.

    On ne descend QUE dans les listes, parce que c'est exactement là que
    `served_value` aplatit : un dict ordinaire est servi tel quel, donc il n'y a rien
    à défaire dedans — et y toucher réécrirait des données `json` que personne n'a
    demandé de changer."""
    if isinstance(valeur, list):
        return [_ranger_une_fiche(x) if isinstance(x, dict) else _ranger_les_items(x)
                for x in valeur]
    return valeur


def _ranger_la_colonne(valeur: Any) -> Any:
    if dsv2.names_layers(valeur) and isinstance(valeur.get(dsv2.VALUE_LAYER), list):
        return {**valeur,
                dsv2.VALUE_LAYER: _ranger_les_items(valeur[dsv2.VALUE_LAYER])}
    return _ranger_les_items(valeur)


def ranger_les_couches(schema: Optional[dict], user_data: Optional[dict], *,
                       colonnes_en_place: Optional[Callable[[], set]] = None) -> dict:
    """CAS 1 — `<colonne>.<annotation>` retourne dans son annotation, imbriquée.

    Rend un payload NEUF (l'appelant réassigne), jamais le sien : les relevés d'écriture
    (`arbitrer_les_vides`, `refuser_champs_reserves`, `_check_row`) doivent tous voir la
    forme rangée, sinon ils jugeraient une adresse au lieu d'une colonne.

    **« Colonne réelle » a trois sources**, et l'ordre entre elles n'a pas d'importance
    puisqu'on ne fait que les réunir :

    - **du geste** — le payload nomme aussi `site_web`. C'est le cas dominant : une
      lecture réémise porte toujours la colonne à côté de son annotation ;
    - **du schéma** — la colonne est déclarée, même absente de ce payload-ci ;
    - **de la ligne** — la colonne existe déjà sur la ligne visée. Lue PARESSEUSEMENT
      (`colonnes_en_place`) : seul un nom pointé encore irrésolu la demande, donc le
      chemin nominal — l'immense majorité des écritures, dont les lots de 8 000 lignes
      — ne paie aucun aller-retour SQL supplémentaire.

    ⚠️ **Un nom PROJETÉ n'est pas une colonne réelle.** `contact1_email` est servi en
    lecture pendant une migration et n'est jamais stocké (oto#22 §6) : le ranger
    fabriquerait la colonne libre que `_refuse_flat_writes` passe son temps à interdire.
    On le laisse pointé — le refus qui le nomme est meilleur que le nôtre.

    Lève sur COLLISION (cas 4) : deux écritures d'une même annotation dans un seul
    geste. Tout ce qu'on ne range pas reste pointé, et `_refuse_dotted_names` tranche.
    """
    if not user_data:
        return dict(user_data or {})
    exemptes = {f.get("key") for f in dsv2._fields(schema)
                if f.get("type") == "json" and f.get("key")}
    out = {k: (v if k in exemptes else _ranger_la_colonne(v))
           for k, v in user_data.items()}
    adresses = []
    for cle in list(out):
        adresse = dsv2.layer_address(cle)
        if adresse is None or adresse[0] in exemptes:
            continue
        if (dsv2.resolve_flat_name(schema, adresse[0]) is not None
                or dsv2.resolve_flat_name(schema, cle) is not None):
            continue                        # nom projeté : `_refuse_flat_writes` parle
        adresses.append((cle, adresse))
    if not adresses:
        return out
    reelles = set(out) | {f.get("key") for f in dsv2._fields(schema) if f.get("key")}
    if colonnes_en_place is not None and any(b not in reelles for _, (b, _) in adresses):
        reelles |= set(colonnes_en_place())
    for cle, (base, couche) in adresses:
        if base not in reelles:
            continue                        # cas 3 : `_refuse_dotted_names` tranche
        socle = out.get(base)
        if base in out and not _socle_accueille_une_couche(socle):
            # La colonne EXISTE — le refus du cas 3 mentirait en disant le contraire.
            # Ce qui bloque est ailleurs : ce geste y écrit un objet métier, et poser
            # une annotation à côté l'envelopperait dans `valeur`.
            raise RowValidationError([
                f"`{cle}` veut annoter `{base}`, mais ce geste écrit dans `{base}` un "
                f"objet ({', '.join(sorted(map(str, socle))[:3])}…) qui n'est pas fait "
                f"de couches : lui ajouter `{couche}` changerait sa forme stockée. "
                f"Rien n'a été écrit. Écris la colonne en couches, explicitement — "
                f'{{"{base}": {{"valeur": {{…}}, "{couche}": …}}}}.'])
        if dsv2.names_layers(socle) and couche in socle:
            raise _refus_de_collision(base, couche, cle)
        val = out.pop(cle)
        if base not in out:
            # Le socle n'est pas dans le geste : on pose l'annotation SEULE. C'est le
            # geste nominal du rattrapage (#326) — `_merge_column` la dépose sur la
            # valeur en place sans y toucher.
            out[base] = {couche: val}
        else:
            out[base] = ({**socle, couche: val} if dsv2.names_layers(socle)
                         else {dsv2.VALUE_LAYER: socle, couche: val})
    return out


def traduire_les_entetes(schema: Optional[dict], entetes: list) -> dict:
    """CAS 2 — un EN-TÊTE de fichier qui porte un point devient un nom de colonne.

    ⚠️ **La distinction qui tient ce lot, et sans laquelle quelqu'un étendra ceci à
    l'appel programmatique :** un en-tête de tableur est une **étiquette** — une chaîne
    humaine écrite par un tiers, à traduire, comme on traduit déjà ses types, ses vides
    et son encodage. Une clé d'appel est une **adresse** : `{"champ.comment": …}`
    DÉSIGNE une couche, et se tromper d'adresse est un bug qui doit lever. *Le refus
    protège le magasin, la traduction protège l'ingestion — ce ne sont pas les mêmes
    portes.*

    **Le cas 1 passe d'abord** : un en-tête qui est une adresse d'annotation valide
    n'est PAS traduit, il est laissé au store qui le rangera. Le traduire ferait de
    `site_web.comment` une troisième colonne `site_web_comment`, à côté de `site_web` et
    de son annotation — c'est-à-dire précisément la corruption qu'on ferme.

    Rend `{en-tête: colonne}`, limité à ce qui est effectivement traduit. Trois
    conditions, aucune décorative : **dite** (l'appelant reçoit ce dict et le rend),
    **sûre en collision** (cas 4, on refuse en nommant les deux), et **déterministe** —
    la traduction ne lit que les en-têtes et le schéma, jamais les lignes en place, donc
    un fichier rechargé chaque mois vise toujours les mêmes colonnes. Sans quoi il
    fabriquerait un doublon par colonne et par passage."""
    presents = [e for e in entetes if e]
    reelles = set(presents) | {f.get("key") for f in dsv2._fields(schema)
                               if f.get("key")}
    traduits: dict = {}
    cibles: dict = {}
    for entete in presents:
        if "." not in entete:
            continue
        adresse = dsv2.layer_address(entete)
        if (adresse is not None and adresse[0] in reelles
                and dsv2.resolve_flat_name(schema, adresse[0]) is None):
            continue                                  # cas 1 : le store la rangera
        cible = entete.replace(".", "_")
        if cible in reelles or cible in cibles:
            autre = (f"un autre en-tête du fichier, `{cibles[cible]}`"
                     if cible in cibles else
                     "un autre en-tête du fichier, ou une colonne du tableau")
            raise RowValidationError([
                f"L'en-tête `{entete}` ne peut pas devenir la colonne `{cible}` : ce "
                f"nom est déjà pris par {autre}. Deux colonnes distinctes seraient "
                f"fusionnées et l'une écraserait l'autre. Rien n'a été importé — "
                f"renomme `{entete}` dans le fichier source, puis recharge."])
        traduits[entete] = cible
        cibles[cible] = entete
    return traduits


def _refuse_dotted_names(user_data: Optional[dict]) -> None:
    """CAS 3 — ce qui reste pointé après le rangement est une adresse FAUTIVE.

    #329 volet 2, rétréci par le présent lot : `data_write` avec `"champ.comment"` en
    clé fabriquait une colonne littérale fantôme — acceptée, persistée, et invisible à
    l'adresse qui la nomme (le filtre et le tri lisent la COUCHE
    `data->'champ'->>'comment'`, jamais la colonne littérale), avec collision
    silencieuse en lecture.

    ⚠️ **Ce refus ne se lit qu'APRÈS `ranger_les_couches`.** Seul reste ici ce qui n'est
    ni une adresse d'annotation valide, ni un en-tête traduisible — et le message
    change selon la raison, parce que « le suffixe n'est pas une couche » et « la
    colonne n'existe pas » ne se corrigent pas du tout de la même façon."""
    for cle in user_data or {}:
        if "." not in cle:
            continue
        adresse = dsv2.layer_address(cle)
        if adresse is not None:
            base, couche = adresse
            raise RowValidationError([
                f"`{cle}` désigne l'annotation `{couche}` de la colonne `{base}`, "
                f"mais `{base}` n'est aucune colonne de ce tableau : ni dans cette "
                f"écriture, ni sur la ligne visée, ni au schéma. Rien n'a été écrit. "
                f'Écris la colonne dans le même geste, ou nomme-la en forme imbriquée '
                f'— {{"{base}": {{"{couche}": …}}}} — si tu veux l\'annoter seule.'])
        base = cle.split("[")[0].split(".")[0]
        raise RowValidationError([
            f"`{cle}` n'est pas un nom de colonne — les points désignent des "
            f"couches ou des attributs, qui s'écrivent en forme imbriquée : "
            f'{{"{base}": {{…}}}}. Les annotations connues sont '
            f"{', '.join('`' + k + '`' for k in dsv2.LAYER_KEYS)}. Une colonne "
            f"littérale nommée `{cle}` serait invisible au filtre et au tri du même "
            "nom. Rien n'a été écrit."])
