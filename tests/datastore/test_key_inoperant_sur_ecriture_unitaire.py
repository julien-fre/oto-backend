"""`key` sur une écriture UNITAIRE ne faisait rien, et ne le disait pas.

`key` nomme la colonne de dédup du mode LOT (`write_rows`). Sur une écriture unitaire
il n'était passé à rien — ni à `append_row`, ni à `update_row` — donc **silencieusement
ignoré**. Sans `id`, l'appel tombait sur un ajout, qui rapproche sur la clé métier
déclarée du tableau : présente dans le corps, la ligne était mergée ; absente, une ligne
neuve naissait.

**Mesuré le 09/09/2026.** Une campagne a écrit dix fois
`data_write(datastore=…, key="@claimed", row={…})` en croyant viser la ligne qu'elle
tenait. Dix `200`, dix lignes neuves sans clé métier — et **sept lignes réservées qui
n'ont jamais reçu leur écriture**. 172 500 jetons. ⚠️ La face silencieuse est la pire :
la ligne parasite se voit, la ligne réservée restée vide, non.

⚠️ **Et l'avertissement avait été essayé, il ne suffit pas.** Le relevé « ligne créée
sans clé métier » était servi — exact, entier, nommant la colonne et le geste — dix fois
sur dix, dans une réponse de 1 591 caractères non tronquée. Le modèle l'a ignoré dix
fois. *Un avertissement parfaitement délivré n'arrête rien ; seul un refus qui nomme le
geste qui aboutit arrête.* C'est ce banc qui garde ce refus.
"""
from __future__ import annotations

import inspect

import pytest

from oto_mcp.datastore import jetons


def test_le_refus_nomme_les_TROIS_gestes_qui_aboutissent():
    """⚠️ Un refus qui constate l'erreur sans dire quoi faire renvoie l'agent à sa
    perplexité — et il réessaie, en consommant. Les trois issues réelles sont nommées :
    viser par `id`, rapprocher par la clé métier dans le corps, dédoubler un lot."""
    msg = jetons.refus_de_key_sans_lot("siren")
    assert "has no effect" in msg and "BATCH mode" in msg
    assert "nothing was written" in msg.lower(), "l'appelant doit savoir où il en est"
    assert "id=" in msg, "le geste qui VISE une ligne"
    assert "the table's declared key" in msg, "le rapprochement par la clé métier"
    assert "rows=[…]" in msg, "le mode lot, seul endroit où `key` a un sens"


def test_un_jeton_RETIRE_dans_key_est_nomme_comme_tel():
    """⚠️ Priorité au message le plus instruit. « `@claimed` a été retiré, voici ce qui
    aboutit » vaut infiniment mieux que « ce paramètre est inopérant » : le second
    laisserait l'agent croire que son pronom serait bon ailleurs.

    `key` n'est pas un champ d'ADRESSE (`jetons.ADRESSE`), donc rien ne l'inspectait —
    un jeton retiré y passait sans un mot, alors que ce module existe pour ça."""
    with pytest.raises(jetons.JetonRetire) as e:
        jetons.refus_de_key_sans_lot("@claimed")
    msg = str(e.value)
    assert "REMOVED" in msg
    assert "`key`" in msg, "le refus dit OÙ le jeton a été posé"
    assert "data_claim_next" in msg, "et ce que la réservation lui a déjà rendu"


def test_la_garde_vise_l_AXE_et_pas_la_valeur_at_claimed():
    """Ne fermer que `@claimed` corrigerait un cas et laisserait la classe entière : le
    prochain agent écrirait `key="_id"` ou `key="siren"` et repartirait pour dix
    écritures muettes."""
    for valeur in ("siren", "_id", "statut", "", "@claim"):
        msg = jetons.refus_de_key_sans_lot(valeur)
        assert "has no effect" in msg, valeur


def test_le_TOOL_passe_par_la_regle_partagee():
    """⚠️ La garde contre la divergence. Si l'outil se remet à porter son propre texte,
    les deux formulations divergeront — et c'est le refus servi qui aura tort."""
    import oto_mcp.tools.datastore as T
    src = inspect.getsource(T)
    assert "refus_de_key_sans_lot" in src, (
        "`data_write` ne compose plus le refus par la règle partagée")


def test_le_mode_LOT_n_est_PAS_touche():
    """`key` avec `rows` est son emploi LÉGITIME : la dédup d'un lot. Le refus ne se
    déclenche que quand `rows` est absent — sinon on casserait l'usage qu'on documente."""
    import oto_mcp.tools.datastore as T
    src = inspect.getsource(T)
    assert "if key is not None and rows is None:" in src, (
        "la condition doit épargner le mode lot — sans `rows is None`, tout lot "
        "dédoublant sur une colonne serait refusé")
