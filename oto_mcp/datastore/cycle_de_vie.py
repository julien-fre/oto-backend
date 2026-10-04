"""Le CYCLE DE VIE d'une ligne : états, transitions, sorties (ADR 0046).

Déclaré par `lifecycle: {states, transitions, terminal?, max_claims?, abandon_state?,
claimable?, labels?}` sur le champ `role="status"`, et par lui seul. Ce module tient sa
lecture et sa grammaire :

- l'accès au bloc et à ses crans (`lifecycle_of`, `terminal_states`,
  `is_terminal_status`, `max_claims_of`, `abandon_state_of`, `claimable_of`) ;
- la FORME de `transitions` (`fautes_de_transitions`, `table_des_transitions`) — un
  objet dont chaque valeur est une LISTE d'états, jugée à la pose comme à la lecture ;
- le REFUS d'une transition non déclarée (`refus_de_transition`) — l'unique texte ;
- la FUSION d'un patch de cycle de vie (`merge_transitions`, `merge_lifecycle`), qui
  ajoute sans écraser ;
- l'avertissement de libération de file (`queue_release_warning`).

⚠️ **Les états TERMINAUX sont dérivés quand ils ne sont pas déclarés** (un état sans
transition sortante en est un) : c'est ce qui fait qu'ajouter une transition peut
retirer un terminal, et pourquoi rien ne recopie cette liste ailleurs.

Ce qu'il ne tient pas :
- **la décision de réservation** et sa grammaire de clauses → `claimable.py` ;
- **le plafond de reprises appliqué** à une ligne au pick → `core.py` ;
- **la validation du bloc `lifecycle` à la POSE** (états inconnus, transitions vers
  le vide) → `definition.py` ;
- **le refus d'une ligne** dont l'état sort du cycle → `validation.validate_row`, qui
  appelle `refus_de_transition` d'ici.
"""
from __future__ import annotations

import json
from typing import Any, Optional

from . import claimable

from .couches import unwrap
from .declaration import _fields, status_field

#: Les clés du bloc `lifecycle` qu'une fusion de patch descend ÉTAT PAR ÉTAT : un
#: patch qui nomme un état laisse les autres en place, `null` retire le sien.
#: `transitions` (oto#64) et `labels` (oto#140) ont la même forme — un objet dont
#: chaque clé est un état déclaré.
PAR_ETAT = ("transitions", "labels")

#: La borne d'un libellé d'étape (`lifecycle.labels`, oto#140). C'est le nom d'une
#: étape dans un badge ou une puce, pas une description : au-delà, il ne tient plus.
LIBELLE_ETAT_MAX = 60

def fautes_de_transitions(transitions: Any) -> list[str]:
    """Les fautes de FORME d'une table `transitions` — vide = bien formée, absente
    comprise. L'unique jugement, servi à la pose (`definition.py`) comme à la lecture
    (`table_des_transitions`).

    ⚠️ **Une destination seule s'écrit en LISTE** (oto#63). `{"a": "b"}` était accepté
    à la pose — la boucle de contrôle l'enrobait d'une liste pour le juger — puis
    stocké tel quel. Chaque lecteur le relisait à sa façon : le validateur d'écriture
    le parcourait lettre par lettre (`a → b` passait, `a → fait` était refusé faute
    de `f`, `a`, `i`, `t`), le dashboard appelait `.map` sur une chaîne et l'écran
    levait au rendu. Une forme que chaque consommateur doit deviner n'en est pas une :
    elle se refuse à l'entrée, et le message donne la forme exacte."""
    if transitions is None:
        return []
    if not isinstance(transitions, dict):
        return [f"lifecycle.transitions doit être un objet "
                f"{{\"état\": [\"états atteignables\"]}} — reçu "
                f"{type(transitions).__name__}. Chaque clé est un état de départ, "
                f"chaque valeur la liste de ceux qu'il peut atteindre."]
    fautes = []
    for etat, cibles in transitions.items():
        if isinstance(cibles, list):
            continue
        seule = cibles if isinstance(cibles, (str, int)) and not isinstance(cibles, bool) \
            else "état atteignable"
        attendu = json.dumps({str(etat): [seule]}, ensure_ascii=False)
        fautes.append(
            f"lifecycle.transitions[{str(etat)!r}] doit être une LISTE d'états — reçu "
            f"{type(cibles).__name__} {json.dumps(cibles, ensure_ascii=False)}. "
            f"Forme attendue : {attendu}, même pour une seule destination.")
    return fautes


def table_des_transitions(colonne: str, lc: dict) -> Optional[dict]:
    """La table `{état: [états atteignables]}` d'un bloc, ou None = aucune table
    déclarée (toute transition permise). LÈVE `ValueError` sur un bloc STOCKÉ hors
    forme : la pose le refuse depuis oto#63, il ne peut venir que d'avant —
    `scripts/transitions_en_liste.py` le convertit. Le deviner ici serait la
    tolérance qu'on vient de retirer à la pose ; le message dit le geste qui répare.

    Même parti que `max_claims_of` : une valeur présente mais illisible LÈVE."""
    transitions = lc.get("transitions")
    fautes = fautes_de_transitions(transitions)
    if fautes:
        raise ValueError(
            f"{colonne}: cycle de vie stocké hors forme — {' ; '.join(fautes)} "
            f"Corrige-le par `data_patch_schema(datastore=…, fields=[{{\"key\": "
            f"\"{colonne}\", \"lifecycle\": {{\"transitions\": …}}}}])` avec la forme "
            f"attendue : la fusion ne touche que les états nommés.")
    if transitions is None:
        return None
    return {str(k): [str(t) for t in v] for k, v in transitions.items()}


def lifecycle_of(schema: Optional[dict]) -> Optional[dict]:
    sf = status_field(schema)
    lc = (sf or {}).get("lifecycle")
    return lc if isinstance(lc, dict) else None


def terminal_states(schema: Optional[dict]) -> set:
    """États terminaux du cycle de vie : `lifecycle.terminal` explicite, sinon
    dérivés = états sans transition sortante déclarée. Vide si pas de lifecycle."""
    lc = lifecycle_of(schema)
    if not lc:
        return set()
    explicit = lc.get("terminal")
    if isinstance(explicit, list):
        return {str(s) for s in explicit}
    states = {str(s) for s in lc.get("states") or []}
    transitions = lc.get("transitions") or {}
    outgoing = {str(k) for k, v in transitions.items() if v}
    return states - outgoing if states else set()


def is_terminal_status(schema: Optional[dict], value: Any) -> bool:
    """⚠️ Déballe, comme tout ce qui juge une valeur (#586). Une colonne d'état
    déclarée `origine: "system"` arrive enveloppée — et l'état terminal cessait
    alors d'être reconnu **en silence** : plus d'avertissement « la libération
    automatique est retirée », donc un agent qui écrit son verdict garde sa ligne
    sans que rien ne le lui dise. Corrigé ICI et pas chez les appelants : la source
    unique vaut mieux que trois déballages qui peuvent diverger."""
    value = unwrap(value)
    return value is not None and str(value) in terminal_states(schema)


def max_claims_of(schema: Optional[dict]) -> Optional[int]:
    """Plafond de réservations SANS écriture (`lifecycle.max_claims`), ou None =
    garde inactive — le comportement historique, qu'aucune déclaration n'arme.

    Une valeur présente mais inutilisable LÈVE : la déclaration est refusée à la
    pose (`validate_schema_def`), donc un plafond illisible ici ne peut venir que
    d'une écriture hors surface. L'ignorer rendrait la garde inerte en silence —
    exactement le défaut que ce plafond existe pour fermer."""
    lc = lifecycle_of(schema) or {}
    if "max_claims" not in lc:
        return None
    v = lc.get("max_claims")
    if isinstance(v, bool) or not isinstance(v, int) or v < 1:
        raise ValueError(f"lifecycle.max_claims doit être un entier >= 1 (déclaré : {v!r})")
    return v


def abandon_state_of(schema: Optional[dict]) -> Optional[str]:
    """L'état où verser une ligne qui a atteint son plafond de reprises
    (`lifecycle.abandon_state`). None = non déclaré."""
    v = (lifecycle_of(schema) or {}).get("abandon_state")
    return str(v) if v is not None else None


def claimable_of(schema: Optional[dict],
                 ns_id: Optional[int] = None) -> Optional[dict]:
    """Le périmètre de réservation déclaré (`lifecycle.claimable`, #517), ou None.
    La décision et sa grammaire vivent dans `claimable.py` ; ici, l'accès depuis un
    schéma — à côté de `max_claims_of`, avec le même parti sur une valeur illisible
    (elle LÈVE, elle n'ouvre pas le tableau en silence)."""
    return claimable.perimetre_of(lifecycle_of(schema), ns_id)


def refus_de_transition(colonne: str, depuis: str, vers: str,
                        autorisees: list) -> str:
    """Le refus d'une transition de cycle de vie — et la PORTE, nommée (oto#64).

    ⚠️ Il ne disait que ce qui est fermé. Mesuré le 05/09/2026 : la sortie d'un état
    terminal **est déjà déclarable** — il suffit de la poser dans
    `lifecycle.transitions`. L'appelant, lui, lisait « (état terminal) » et en
    concluait qu'il n'y avait pas de porte : le signal 726 est un agent qui a préféré
    ne rien écrire du tout et rendre la main à un humain, sur un tableau qu'une ligne
    de schéma aurait rouvert.

    ⚠️ **Ce message n'a pu être écrit qu'APRÈS avoir rendu le geste sûr.** Jusqu'au
    même jour, poser cette transition en ne nommant qu'elle effaçait toutes les autres
    (`merge_lifecycle` remplaçait `transitions` en bloc) : enseigner ce geste-là aurait
    envoyé casser le cycle de vie qu'on cherche à rouvrir. Un message qui apprend un
    geste attend que le geste soit sûr.

    Il TUTOIE, comme les autres refus d'écriture de ce chemin (« reprends le lot à la
    ligne 4 ») : c'est un agent qui le lit, au milieu d'un lot.

    ⚠️ **Le patch proposé porte les destinations DÉJÀ autorisées, plus la nouvelle** —
    et pas la nouvelle seule. La fusion descend par état, mais la LISTE d'un état se
    remplace : conseiller `{"recorded": ["pushed"]}` à qui a déjà `['drafted',
    'excluded']` lui ferait perdre les deux. Le défaut qu'on vient de fermer un cran
    plus haut se reformerait ici, dans le message écrit pour l'éviter."""
    quoi = (f" (autorisées: {autorisees})" if autorisees else " (état terminal)")
    # Les sorties de CET état après le patch : celles qui existent + celle qu'on ajoute.
    cibles = list(autorisees) + [vers]
    patch = ('{"key": "%s", "lifecycle": {"transitions": {"%s": %s}}}'
             % (colonne, depuis,
                "[" + ", ".join(f'"{c}"' for c in cibles) + "]"))
    return (
        f"{colonne}: transition {depuis!r} → {vers!r} interdite{quoi}. Si cette "
        f"transition est légitime, elle se DÉCLARE : "
        f"`data_patch_schema(datastore=…, fields=[{patch}])` — la fusion ne touche que "
        f"l'état nommé, le reste du cycle de vie ne bouge pas.")


def merge_transitions(current: dict, patch: dict) -> dict:
    """Fusion PAR ÉTAT des transitions — `null` retire l'état de la table (oto#64).
    Sert aussi `labels` (oto#140) : même forme, même geste de retrait.

    ⚠️ La liste de destinations d'un état, elle, se REMPLACE : c'est l'ensemble des
    sorties de cet état, et une fusion de listes rendrait le retrait d'UNE destination
    impossible sans une grammaire de plus.
    """
    out = dict(current)
    for etat, cibles in patch.items():
        if cibles is None:
            out.pop(etat, None)
        else:
            out[etat] = cibles
    return out


def merge_lifecycle(current: dict, patch: dict) -> dict:
    """Fusion PAR CLÉ du cycle de vie — `null` LÈVE une clé (#517).

    Jusqu'au 29/08/2026, un patch qui nommait `lifecycle` le REMPLAÇAIT en bloc :
    poser un périmètre de réservation exigeait de recopier `states`, `transitions`,
    `terminal`, `max_claims` et `abandon_state` — et en oublier un les faisait
    disparaître sans un mot, la promesse inverse de `data_patch_schema`. La fusion
    descend d'un cran ; `null` est le geste de retrait (même parti que
    `new_rows="create"` : un patch qui ne peut qu'ajouter rend le retrait
    impossible).

    ⚠️ **Et elle descend d'un cran de PLUS dans `transitions` (oto#64, 05/09/2026)** —
    le même défaut, une couche plus bas, trouvé en mesurant : ajouter une seule
    transition en ne nommant qu'elle effaçait toutes les autres, sans un mot. Le geste
    qui le déclenchait était celui de la RÉPARATION : l'agent enfermé dans un état
    terminal apprend qu'une sortie se déclare, la déclare, et casse le cycle de vie
    qu'il voulait assouplir.

    Le retrait d'un état devient donc explicite : `transitions: {"perdu": null}` retire
    ses sorties, `transitions: null` retire la table entière. On ne préavise pas la fin
    d'une destruction silencieuse — ce que ce changement retire à l'appelant, c'est le
    droit d'effacer sans le savoir.

    ⚠️ **`labels` descend de la même façon (oto#140, 25/09/2026)** : poser le libellé
    d'UNE étape par `lifecycle: {labels: {"perdu": "Perdu"}}` ne doit pas effacer ceux
    des autres — le défaut d'oto#64 se serait reformé sur la première clé ajoutée au
    bloc depuis. `labels: {"perdu": null}` retire ce libellé, `labels: null` tous.

    ⚠️ `claimable` NE descend pas : c'est un périmètre de réservation, un filtre entier
    dont le remplacement en bloc est le geste voulu. Une fusion par colonne y rendrait
    impossible de restreindre une file en une fois."""
    out = dict(current)
    for k, v in patch.items():
        if v is None:
            out.pop(k, None)
        elif (k in PAR_ETAT and isinstance(v, dict)
                and isinstance(out.get(k), dict)):
            out[k] = merge_transitions(out[k], v)
        else:
            out[k] = v
    return out


def queue_release_warning(schema: Optional[dict]) -> Optional[str]:
    """Le datastore se donne un STATUT qui n'en est pas un, ou un cycle de vie sans
    état TERMINAL : dit-le, sinon le silence se paie en file de travail (signal #360).

    ⚠️ **Ce texte enseignait deux choses fausses jusqu'au 29/09/2026** (oto#91) : que
    déclarer un état terminal ferait relâcher le bail au verdict — la libération
    automatique est retirée depuis #317 — et qu'un tableau sans `lifecycle` n'a pas de
    file, alors que `claim_next` n'en lit rien et y sert la ligne libre dans le même
    appel que l'avertissement. Il dit maintenant ce que le code fait : la réservation
    marche sans rien déclarer ; ce qu'un `lifecycle` absent ou sans terminal retire,
    ce sont les gardes (transitions, plafond de reprises, périmètre, état d'abandon).
    None = rien à signaler (pas de statut, ou terminaux dérivables)."""
    sf = status_field(schema)
    if sf is None:
        # ⚠️ **Le cas de l'incident #360, sous sa forme d'après le 08/09/2026.**
        # La colonne d'état est désormais CELLE QUI PORTE le `lifecycle` : une colonne
        # qui n'en porte pas n'est pas un état — aucune garde du cycle de vie ne s'y
        # applique. La RÉSERVATION, elle, n'en dépend pas : `claim_next` sert la ligne
        # libre avec ou sans `lifecycle` (ce texte affirmait le contraire jusqu'au
        # 29/09/2026, oto#91 — l'avertissement contredisait la ligne servie avec lui).
        # L'auteur qui voit une colonne d'états avec ses options croit tenir des gardes
        # qu'il n'a pas : on garde donc le dire, sur le fait qui est vrai.
        candidates = [str(f.get("key")) for f in _fields(schema)
                      if isinstance(f, dict) and f.get("key")
                      and f.get("options")]
        if not candidates:
            return None
        noms = ", ".join(f"`{c}`" for c in candidates[:3])
        return (f"aucune colonne ne porte de `lifecycle` : {noms} ressemble(nt) à un "
                f"état (`options` déclarées) sans en être un — **c'est le bloc "
                f"`lifecycle` qui fait l'état**. Aucune transition n'y est donc "
                f"gardée, et ni plafond de reprises, ni état d'abandon, ni périmètre "
                f"de réservation ne s'appliquent. La file (`data_claim_next`) "
                f"fonctionne quand même, sans rien déclarer : filtre sur une colonne "
                f"que ton traitement ÉCRIT, et rends chaque ligne (`data_release`, ou "
                f"`run_finish`). Pour armer ces gardes, déclare `lifecycle: {{states: "
                f"[...], terminal: [...]}}` sur la colonne qui porte l'avancement. "
                f"Cf. guide `work-queue`.")
    if terminal_states(schema):
        return None
    key = sf.get("key") or "status"
    return (f"champ `{key}` : un `lifecycle` sans état terminal dérivable (tout état a "
            "une transition sortante) → aucun `abandon_state` n'y est déclarable (il "
            "doit être terminal), et le suivi d'une campagne ne peut rien compter comme "
            f"conclu. Déclare `terminal: [...]` sur le `lifecycle` de `{key}`. Sans "
            "effet sur les baux : écrire un verdict, terminal ou non, ne libère jamais "
            "une ligne — `data_release` après chaque verdict, ou `run_finish`. "
            "Cf. guide `work-queue`.")
