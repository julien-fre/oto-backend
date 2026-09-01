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
4. le nom obtenu est **déjà pris**, avec une AUTRE valeur → on **REFUSE en nommant les
   deux**. On ne fusionne jamais deux colonnes en silence. Avec la MÊME valeur, il n'y a
   rien à fusionner : c'est notre propre lecture réémise, on la reprend sans rien
   changer (`_absorbe_ou_refuse`).

Le cas 1 passe AVANT le cas 2 : traduire d'abord ferait de `site_web.comment` une
troisième colonne `site_web_comment` au lieu de restaurer l'annotation.

## ⚠️ Une colonne déclarée `json` s'annote — décidé le 2026-09-01 (#728)

Elle ne s'annotait pas, et le refus **mentait sur ce qu'il avait regardé** : « `X` n'est
aucune colonne de ce tableau : ni dans cette écriture, ni sur la ligne visée, ni au
schéma », alors que le geste écrivait `X` deux clés plus haut et que le schéma la
déclare. L'exemption `json` (l'objet métier ne se réinterprète pas) portait aussi sur
l'ADRESSE : l'annotation n'était jamais rangée, restait pointée, et tombait dans le
refus du cas 3 — celui des colonnes qui n'existent nulle part. Asymétrie mesurée le jour
même : `effectif.comment`, colonne scalaire, passait dans la MÊME écriture.

**L'exemption protège le CONTENU de l'objet, pas le droit d'annoter la colonne.** Le
LECTEUR ne l'a jamais exemptée — `flat_layers` sert `X.comment` pour toute colonne,
`json` comprise — donc l'écriture doit reprendre ce qu'il sert, sinon l'aller-retour
reste ouvert précisément là. Restent exempts : le contenu de l'objet
(`_ranger_les_items`, l'écriture le traverse intact) et la garde des couches mixtes
(`_refuse_mixed_layers`, #329).

La déclaration est ce qui autorise l'enveloppe : `{"valeur": <objet>, "comment": …}` se
relit `<objet>` au nom nu (`unwrap`), donc envelopper une colonne DÉCLARÉE objet ne
change rien à ce qu'on en sert. Sans déclaration on ne sait pas ce qu'est ce dict, et on
continue de refuser en demandant la forme explicite.
"""
from __future__ import annotations

from typing import Any, Callable, Optional

from . import schema as dsv2
from .columns import _refuse_flat_writes
from .errors import RowValidationError


def _socle_accueille_une_couche(socle: Any) -> bool:
    """La valeur déjà nommée pour cette colonne peut-elle recevoir une annotation ?

    Un scalaire, une liste, `None` : oui — ils deviennent la `valeur` de la colonne à
    couches, ce qui est exactement l'aller-retour (la lecture sert la valeur nue à côté
    de ses couches). Un dict fait de couches connues : oui — on lui ajoute la sienne.

    ⚠️ Un dict NON VIDE qui n'est PAS fait de couches est un objet métier :
    l'envelopper dans `{"valeur": …}` changerait sa forme stockée sans que personne
    l'ait demandé. On ne range pas — et on le dit avec sa vraie raison, parce que le
    refus du cas 3 dirait « cette colonne n'existe pas », ce qui serait faux ici.

    ⚠️ **Ne se consulte plus sur une colonne DÉCLARÉE `json`** (#728) : là, l'auteur du
    schéma a dit que le nu est un objet, et `unwrap` le rend nu quoi qu'il arrive —
    l'enveloppe ne change donc rien à ce qu'on sert. C'est l'absence de déclaration qui
    rend le dict indécidable, pas le dict."""
    return not (isinstance(socle, dict) and socle and not dsv2.names_layers(socle))


def _refus_de_collision(base: str, couche: str, cle: str) -> RowValidationError:
    return RowValidationError([
        f"`{cle}` et `{base}.{couche}` (écrit en forme imbriquée dans `{base}`) "
        f"désignent la MÊME annotation, avec deux valeurs. Rien n'a été écrit : on ne "
        f"fusionne jamais deux écritures d'un même champ en silence. Garde l'une des "
        f"deux formes."])


def _absorbe_ou_refuse(out: dict, cle: str, base: str, couche: str) -> bool:
    """Le socle NOMME déjà cette couche : la clé pointée en est-elle l'ÉCHO ?

    `flat_layers` ne regarde que le NOM. Une colonne à couches et un objet métier dont
    un champ s'appelle `comment` sont donc SERVIS pareil — `champ` puis `champ.comment`
    — et une réémission renvoie les deux formes sans que le payload dise laquelle des
    deux formes stockées elle vient de lire. On tranche sur la VALEUR, jamais sur la
    forme :

    - **identique** ⟹ c'est notre propre lecture. On reprend la clé sans rien changer :
      la ranger envelopperait l'objet dans `valeur`, c'est-à-dire changerait sa forme
      stockée alors que personne n'a rien demandé. Précédent exact dans `_merge_column`
      — une valeur nue identique à celle en place est un NO-OP, pas une réécriture ;
    - **différente** ⟹ cas 4 : deux écritures d'un même champ servi, dont l'une
      écraserait l'autre selon l'ordre des clés. On refuse en nommant les deux.

    Rend True quand la clé a été absorbée. UN seul juge pour le premier niveau et pour
    les attributs d'un item : deux copies divergeraient un jour sur un cas limite, et
    c'est exactement le défaut que ce module passe son temps à fermer."""
    socle = out.get(base)
    if not (isinstance(socle, dict) and couche in socle):
        return False
    if not dsv2.same_value(socle[couche], out[cle]):
        raise _refus_de_collision(base, couche, cle)
    out.pop(cle)
    return True


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
        if _absorbe_ou_refuse(out, cle, base, couche):
            continue
        socle = out[base]
        if not _socle_accueille_une_couche(socle):
            continue
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
    On délègue donc à `_refuse_flat_writes`, qui le nomme mieux que nous — mais on
    l'APPELLE (#728) au lieu de le supposer posé : `append_row` et le lot ne le posent
    pas, et là où il manquait c'est le refus des noms pointés qui parlait, en annonçant
    « n'est aucune colonne : ni dans cette écriture » d'un nom que le geste porte.

    ⚠️ **Une colonne déclarée `json` est une colonne comme une autre pour l'ADRESSE**
    (#728) : son exemption ne couvre que le CONTENU de l'objet (ci-dessous, `out`), pas
    le droit de l'annoter. La couvrir aussi laissait l'annotation pointée jusqu'à
    `_refuse_dotted_names`, qui annonçait alors « n'est aucune colonne » d'une colonne
    que le schéma déclare — un refus qui ment sur ce qu'il a regardé, et qui envoie
    créer ce qui existe déjà. Genèse en tête de module.

    Lève sur COLLISION (cas 4) : deux écritures d'une même annotation, avec deux
    valeurs, dans un seul geste. Tout ce qu'on ne range pas reste pointé, et
    `_refuse_dotted_names` tranche — sa phrase à trois sources n'est vraie que parce
    qu'on ne lui laisse ici QUE des noms dont la base est introuvable partout.
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
        if adresse is None:
            continue
        if (dsv2.resolve_flat_name(schema, adresse[0]) is not None
                or dsv2.resolve_flat_name(schema, cle) is not None):
            # Nom PROJETÉ (oto#22 §6) : servi en lecture, jamais stocké — son
            # annotation n'a nulle part où aller. On appelle ICI le refus qui sait le
            # dire, au lieu de laisser filer la clé : `_refuse_flat_writes` ne passe
            # pas à toutes les portes (ni `append_row`, ni le lot), et là où il manque
            # c'est `_refuse_dotted_names` qui parlait — en annonçant « ni dans cette
            # écriture » d'un nom que le geste porte littéralement.
            _refuse_flat_writes(schema, {cle: out[cle],
                                         adresse[0]: out.get(adresse[0])})
            continue
        adresses.append((cle, adresse))
    if not adresses:
        return out
    reelles = set(out) | {f.get("key") for f in dsv2._fields(schema) if f.get("key")}
    if colonnes_en_place is not None and any(b not in reelles for _, (b, _) in adresses):
        reelles |= set(colonnes_en_place())
    for cle, (base, couche) in adresses:
        if base not in reelles:
            continue                        # cas 3 : `_refuse_dotted_names` tranche
        if _absorbe_ou_refuse(out, cle, base, couche):
            continue                        # cas 4, ou notre propre lecture réémise
        socle = out.get(base)
        if (base in out and base not in exemptes
                and not _socle_accueille_une_couche(socle)):
            # La colonne EXISTE — le refus du cas 3 mentirait en disant le contraire.
            # Ce qui bloque est ailleurs : ce geste y écrit un dict que RIEN ne déclare
            # objet, et poser une annotation à côté l'envelopperait dans `valeur`. Une
            # colonne déclarée `json`, elle, ne passe pas ici : la déclaration lève
            # l'indécision, et `unwrap` la rendra nue de toute façon (#728).
            raise RowValidationError([
                f"`{cle}` veut annoter `{base}`, mais ce geste écrit dans `{base}` un "
                f"objet ({', '.join(sorted(map(str, socle))[:3])}…) qui n'est pas fait "
                f"de couches : lui ajouter `{couche}` changerait sa forme stockée. "
                f"Rien n'a été écrit. Écris la colonne en couches, explicitement — "
                f'{{"{base}": {{"valeur": {{…}}, "{couche}": …}}}} — ou déclare-la '
                f"`json` (data_set_schema) si c'est un objet métier."])
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
    colonne n'existe pas » ne se corrigent pas du tout de la même façon.

    ⚠️ **Ce message AFFIRME trois lectures qu'il ne fait pas** — il ne reçoit que le
    payload ; ce sont celles de `ranger_les_couches`. Il n'est donc vrai que tant que
    rien n'arrive ici avec une base connue, et le 2026-09-01 (#728) DEUX chemins y
    arrivaient : une colonne déclarée `json` (exemptée de l'adressage) et un nom
    projeté aux portes qui ne posent pas `_refuse_flat_writes`. Les deux se refusent
    désormais en amont, avec leur vraie cause. **Si un troisième s'ouvre un jour, c'est
    en amont qu'il se ferme — jamais en affadissant cette phrase, qui est ce qui rend
    le refus actionnable.**"""
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
