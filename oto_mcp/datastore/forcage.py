"""Forcer une colonne verrouillée — **sur l'appel**, jamais par un état (#658).

Une colonne `readonly: true` protège la valeur remise par le client contre son
écrasement par un agent (#606). Elle était fermée à TOUT LE MONDE : celui à qui la
donnée appartient ne pouvait plus la corriger, et la seule sortie nommée était le
schéma — lever le cran, écrire, le remettre.

⚠️ **Cette manœuvre-là est le défaut qu'on ferme, pas la sortie qu'on offre.** Mesurée
sur l'autre verrou de la plateforme, celui qui a la forme d'un ÉTAT (`key_required`,
#668) : le 01/09/2026 un agent refusé la retrouve seul et la rejoue deux fois sur deux
tableaux — il a bien refermé ; le lendemain un autre passage ne la retrouve pas et
s'arrête. *Il suffit qu'une exécution s'interrompe entre « lever » et « remettre » pour
que le verrou reste ouvert sans que personne le sache* — et une colonne déverrouillée
ne produit aucun signal. Le forçage vaut donc pour CET APPEL et rien d'autre : rien à
refermer, rien à oublier.

Trois choses le tiennent, et il en faut trois :

- **le palier** (`core.DatastorePg._forcage_readonly`) — le PROPRIÉTAIRE du tableau
  (owner-match : lui, son org, son équipe) OU celui qui le GOUVERNE
  (`ownership.can_govern` : gérant, admin d'org, admin plateforme). L'un des deux
  suffit. Un accès en écriture PARTAGÉ (`data_share`) ne suffit PAS : un verrou que
  quiconque peut écrire peut lever ne protège de personne ;
- **le refus qui nomme le geste** (`arbitrer`) — qui peut forcer, et comment. Un refus
  qui dit seulement « colonne verrouillée » renvoie l'appelant chercher une manœuvre :
  c'est exactement ce qui a produit le contournement ci-dessus ;
- **la trace** — chaque substitution est relevée (ligne, colonne, valeur remplacée) et
  versée aux arguments du journal des appels (`server._TRACED_ARGS`), à côté du `sub`
  que le journal stampe déjà. ⚠️ Tranché le 02/09/2026 **en connaissance de cause** :
  le journal ne remonte qu'à ~35 jours, donc la trace disparaîtra alors que la valeur
  forcée restera. Pas de colonne de plus sur la ligne — la question a été posée et
  fermée.

Module PUR (aucun I/O, aucun import du paquet) : `schema.py` l'importe en tête, le
store lui remet un `Forcage` déjà tranché.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

# Le nom du paramètre, dans les DEUX faces (MCP `data_write`, REST `?…=true`). Cité par
# chaque refus, et par la description servie : une capacité qu'aucun texte n'annonce
# n'existe pas pour un agent — il retombe sur la manœuvre qu'on cherche à supprimer.
PARAMETRE = "readonly_override"

# Ce que le journal garde d'un forçage, borné. Un lot force autant de lignes qu'il en
# porte ; la ligne de journal, elle, doit rester lisible et insérable.
MAX_RELEVE = 25
MAX_VALEUR = 120

# La phrase du palier, une seule fois : les deux refus la citent, la description servie
# aussi. Deux formulations divergeraient le jour où le palier bouge.
PALIER = ("le PROPRIÉTAIRE du tableau (toi, ton org ou ton équipe) ou celui qui le "
          "GOUVERNE")


def _borne(valeur: Any) -> Any:
    """La valeur telle que le journal la gardera : les scalaires JSON tels quels, tout
    le reste stringifié et coupé. Le journal doit dire CE QUI a été remplacé, pas
    reporter une fiche entière dans une colonne d'audit."""
    if valeur is None or isinstance(valeur, (bool, int, float)):
        return valeur
    texte = valeur if isinstance(valeur, str) else str(valeur)
    return texte[:MAX_VALEUR]


@dataclass
class Forcage:
    """Le forçage demandé sur CET appel, et s'il est tenu.

    `autorise` est tranché par le store AVANT toute transaction : le palier coûte une
    lecture d'ownership, et l'évaluer dans le `_apply` du verrou de ligne ouvrirait une
    seconde connexion pendant qu'on tient un `FOR UPDATE` — la forme exacte du gel de
    prod du 02/09/2026. Ici, c'est déjà un booléen.

    Un SEUL objet par appel : un lot force plusieurs lignes sous le même geste, et le
    relevé qui part au journal est celui de l'appel entier."""

    demande: bool = False
    autorise: bool = False
    forcees: list = field(default_factory=list)

    @property
    def actif(self) -> bool:
        return self.demande and self.autorise

    def relever(self, colonne: str, avant: Any, apres: Any) -> None:
        """Note une substitution. La LIGNE est agrafée après coup (`rattacher`) : la
        décision se prend sur le payload et la ligne en place, sans connaître son
        identifiant.

        Une entrée encore SANS ligne et de même colonne est remplacée, pas ajoutée :
        `datastore_merge_row_locked` documente que son `_apply` peut être rejoué, et
        deux entrées pour un seul remplacement se liraient comme deux forçages."""
        if any(self._reprendre(e, colonne, avant, apres) for e in self.forcees):
            return
        if len(self.forcees) >= MAX_RELEVE:
            return
        self.forcees.append({"row": None, "col": colonne,
                             "was": _borne(avant), "now": _borne(apres)})

    @staticmethod
    def _reprendre(entree: dict, colonne: str, avant: Any, apres: Any) -> bool:
        if entree.get("row") is not None or entree.get("col") != colonne:
            return False
        entree["was"], entree["now"] = _borne(avant), _borne(apres)
        return True

    def rattacher(self, row_id: Optional[str]) -> None:
        """Agrafe la ligne aux substitutions relevées depuis la précédente. Appelée par
        le store dès qu'une écriture aboutit — donc jamais sur un geste refusé."""
        for entree in self.forcees:
            if entree.get("row") is None:
                entree["row"] = None if row_id is None else str(row_id)

    def releve(self) -> list:
        """Ce qui part au journal — les seules entrées rattachées à une ligne écrite."""
        return [e for e in self.forcees if e.get("row") is not None]


def arbitrer(forcage: Optional[Forcage], colonne: str,
             avant: Any, apres: Any) -> Optional[str]:
    """Cette écriture sur colonne verrouillée passe-t-elle ? — `None` = elle passe (et
    elle est relevée), sinon le refus, qui NOMME le geste.

    Trois sorties, et les trois disent où va la chose ET qui peut forcer. Un refus
    exact mais sans issue fait deviner exactement comme un refus muet (#668)."""
    ou_va = (f"Ce que dit une autre source va dans `{colonne}.comment` "
             f"({{\"{colonne}\": {{\"comment\": …}}}})")
    if forcage is not None and forcage.actif:
        forcage.relever(colonne, avant, apres)
        return None
    if forcage is not None and forcage.demande:
        # Le palier n'est pas tenu. Ne pas répéter « passe le paramètre » : il est
        # passé, et le redire enverrait chercher une manœuvre pour l'obtenir.
        return (
            f"`{colonne}` est une colonne du fichier source, non modifiable "
            f"(`readonly`) — rien n'a été écrit, et `{PARAMETRE}` n'y change rien "
            f"ici : forcer est réservé à {PALIER}, jamais à un simple accès en "
            f"écriture partagé — sinon le verrou ne protégerait de personne. "
            f"{ou_va}, ou demande la correction à qui possède le tableau.")
    return (
        f"`{colonne}` est une colonne du fichier source, non modifiable "
        f"(`readonly`) — rien n'a été écrit. {ou_va} ; la valeur reste celle du "
        f"fichier. Pour la REMPLACER malgré le verrou : `{PARAMETRE}=true` sur CET "
        f"appel, ouvert à {PALIER}. Il ne vaut que pour cet appel — il n'y a rien à "
        f"rouvrir dans le schéma, donc rien à refermer.")
