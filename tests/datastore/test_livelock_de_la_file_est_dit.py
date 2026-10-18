"""La file sert la plus ancienne — et rien ne disait ce que ça coûte (#101).

`data_claim_next` sert la ligne la plus ancienne éligible, ordre figé. **Relâcher une
ligne la remet donc en tête si elle correspond toujours au filtre.** Si le filtre porte
sur une colonne que le traitement ne modifie pas, les mêmes lignes reviennent
indéfiniment.

**Mesuré le 07/09/2026 sur un tableau de 3 766 lignes** : trois workers, filtre sur une
colonne A, écriture dans une colonne B. **834 lignes fraîches jamais atteintes**, un
worker a réservé SEPT fois la même ligne. Chaque appel réussissait, aucune erreur —
c'est un livelock, pas une panne, et on ne le voit pas de l'intérieur.

⚠️ **Le fait était déjà servi (« picks the oldest row »), sa CONSÉQUENCE non.** Un texte
qui énonce un mécanisme sans dire ce qu'il produit chez son lecteur ne l'aide pas :
l'agent lit « la plus ancienne », n'y voit rien d'anormal, et boucle.

**Pourquoi pas un `order_by` sur la réservation** — la question s'est posée et la réponse
est mesurée : si le filtre ne porte pas sur une colonne écrite, **aucun ordre ne
progresse**. Un autre ordre déplacerait le livelock, il ne le lèverait pas. Et un
paramètre offert sera réglé : il donnerait l'illusion d'un remède là où la cause est le
filtre. Ce qui manquait est la condition, pas un réglage.

⚠️ **L'ordre a changé depuis (oto#101, second lot)** : la file sert la ligne servie le
moins récemment, les jamais servies d'abord — ce n'est pas un `order_by` offert, c'est
la mémoire de service de la plateforme. Les lignes fraîches sont donc atteintes ; mais
la condition reste la seule qui VIDE la file : sans elle, les lignes traitées reviennent
tour après tour. Le texte servi dit le nouvel ordre ET la condition.
"""
from __future__ import annotations

import inspect

import oto_mcp.tools.datastore as T
from pathlib import Path

GUIDE = Path(inspect.getfile(T)).parent.parent / "guides" / "work-queue.md"


def _bloc_claim_next() -> str:
    src = inspect.getsource(T)
    i = src.find("def data_claim_next")
    j = src.find("def ", i + 40)
    return src[i:j if j > 0 else len(src)]


def test_la_description_SERVIE_dit_la_condition_qui_fait_avancer():
    """⚠️ La condition, pas seulement le mécanisme : le filtre doit nommer une colonne
    que le traitement ÉCRIT. C'est ce qui manquait, et c'est ce qui évite la boucle."""
    b = _bloc_claim_next()
    assert "MUST name a column" in b and "WRITES" in b
    assert "least recently served" in b, "l'ordre doit être dit avec l'effet"
    assert "never empties" in b, "et ce que coûte un filtre qui n'avance pas"


def test_elle_donne_le_moyen_de_le_VOIR_de_l_interieur():
    """Un livelock ne lève rien : sans signature nommée, l'agent ne peut pas le
    distinguer d'un travail qui avance. `_claims` au-dessus de 1 EST la signature."""
    b = _bloc_claim_next()
    assert "_claims" in b, "le compteur servi sur la ligne doit être nommé ici"
    assert "claiming again will not help" in b, "et la conduite doit suivre"


def test_le_GUIDE_porte_la_meme_condition():
    """⚠️ Les deux surfaces, parce qu'elles ne sont pas lues par le même lecteur au même
    moment : la description au moment d'appeler, le guide au moment de concevoir la
    passe. Une seule des deux laisserait la moitié des agents dans le noir."""
    g = GUIDE.read_text()
    assert "that your processing WRITES" in g
    assert "834 fresh rows" in g, "le fait mesuré, pas une mise en garde vague"
    assert "_claims" in g, "et la signature observable"


def test_le_cout_de_l_abandon_est_dit_sur_les_DEUX_surfaces():
    """⚠️ Ce que le plafond de reprises fait vraiment : il BORNE le livelock en
    éjectant les lignes — mesuré, les 17 tableaux à file du parc déclarent tous un
    plafond. Donc la boucle s'arrête… en perdant des lignes qui n'avaient rien de
    fautif. Le dire évite qu'on prenne le plafond pour une protection suffisante."""
    b, g = _bloc_claim_next(), GUIDE.read_text()
    assert "without being at fault" in b
    assert "without being at fault" in g
