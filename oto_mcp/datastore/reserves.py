"""Les champs que l'appelant n'écrit pas — le geste du store (#586, #606).

Deux crans de schéma, UNE garde. Ce qu'ils protègent est la donnée remise par le
client, contre deux gestes mesurés sur la même campagne (29/08/2026) :

- **écraser** une colonne source (#606) : quatorze valeurs sur douze fiches par cent,
  à l'exact — l'agent « complète » l'adresse avec ce que dit le registre, et la valeur
  remise n'existe plus nulle part. Cran `readonly: true` (+ `report_to`) : une
  écriture qui CHANGE la valeur en place est refusée en nommant la colonne, la raison
  et où va la divergence ;
- **détruire la copie de secours** (#586) : la couche `<champ>.origine` censée garder
  la valeur remise était écrite par l'agent, donc réécrite par lui — une fois sur
  quarante et une, et c'était l'unique copie. Cran `origine: "system"` : la plateforme
  la pose elle-même, à la première écriture qui change la valeur, une seule fois ; la
  couche est fermée à l'appelant.

La DÉCISION vit dans `schema.py` (`reserved_refusals`, à côté des autres
déclarations, et sondée par `enforced_keys`) ; ce module en fait le geste : refuser en
levant, et poser. Il est appelé aux cinq chemins d'écriture du store — création (ligne
seule, lot, upload signé), fusion sous verrou, patch par identifiant, remplacement.

⚠️ **Le cran borne TOUT LE MONDE, faces humaine et REST comprises.** Le store ne sait
pas distinguer un agent d'un humain (il connaît un sub et une org ; le run n'est pas
obligatoire sur toute écriture), et une exemption par défaut serait un trou. La sortie
du propriétaire est le schéma — `data_patch_schema(fields=[{key, readonly: false}])`,
deux gestes délibérés, comme le bail (#317) et `key_required` (#516). Il n'y a pas de
« forcer » sur `data_write`, et le refus ne l'enseigne pas.

⚠️ Pas dans le registre des jetons (#602) : celui-ci juge AVANT la résolution, sans
schéma ; un champ réservé est une propriété du TABLEAU, il se juge là où le schéma est
connu. Les deux se complètent — jeton mal placé : « il s'écrit dans tel champ » ;
champ réservé : « il ne s'écrit pas, voici où va la chose ».
"""
from __future__ import annotations

from typing import Optional

from . import schema as dsv2
from .columns import _existing_layers
from .errors import RowValidationError


def refuser_champs_reserves(schema: Optional[dict], payload: Optional[dict], *,
                            avant: Optional[dict] = None,
                            apres: Optional[dict] = None) -> None:
    """Refuse ce que l'appelant n'écrit pas — en nommant le champ, la raison et où
    va la chose. `RowValidationError`, donc `row_invalid` côté REST (avec
    `details.expected_column`, #545) et INVALID_PARAMS côté MCP : le code ne
    change pas, c'est le texte qui enseigne."""
    errors, details = dsv2.reserved_refusals(schema, payload, avant, apres)
    if errors:
        raise RowValidationError(errors, details=details)


def poser_origine_systeme(schema: Optional[dict], avant: Optional[dict],
                          apres: dict, cles) -> list[str]:
    """Pose `<champ>.origine` = la valeur d'AVANT sur les colonnes `origine: "system"`
    que le geste vient de MODIFIER — une seule fois, jamais réécrite. Rend les
    colonnes posées ; `apres` est modifiée en place.

    Trois règles, chacune fermant une porte du défaut :

    - une origine DÉJÀ là (posée par le système, ou écrite par un agent avant le
      cran) n'est jamais touchée — les 40 fiches de la campagne restent lues telles
      quelles ;
    - une valeur INCHANGÉE ne pose rien : relire → repousser n'est pas une
      modification, et une colonne plate reste plate ;
    - un champ VIDE au départ reçoit `""` — le marqueur « rien n'avait été remis ».
      Sans lui, la deuxième écriture capturerait la première valeur de l'agent comme
      si elle venait du client. `flat_layers` ne sert pas une couche vide : à la
      lecture, « vide à l'origine » et « jamais modifié » se confondent, et c'est
      juste — dans les deux cas il n'y a rien à rétablir.

    La capture est PARESSEUSE (à la première écriture, pas à la pose du schéma) et
    rend la même valeur : entre la pose et la première modification, rien n'a bougé.
    Un format ne vaut que pour l'avenir — le poser ne réécrit aucune ligne."""
    posees: list[str] = []
    for cle in sorted(dsv2.system_origin_fields(schema) & set(cles)):
        col_avant = (avant or {}).get(cle)
        if dsv2.layer_value(col_avant, dsv2.ORIGIN_LAYER) is not None:
            continue
        val_avant, val_apres = dsv2.unwrap(col_avant), dsv2.unwrap(apres.get(cle))
        if val_avant == val_apres:
            continue
        couches = _existing_layers(apres.get(cle))
        couches[dsv2.ORIGIN_LAYER] = val_avant if val_avant is not None else ""
        apres[cle] = couches
        posees.append(cle)
    return posees
