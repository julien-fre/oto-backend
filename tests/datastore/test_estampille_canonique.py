"""Les estampilles posées par la PLATEFORME ont une seule forme (#859).

Alexis, en relisant le lot sur le tri : *« cette date est déterministe, elle
n'est pas renseignée par l'agent, c'est pas un champ writable »*. C'est exact —
une colonne déclarée système est refusée à l'écriture et la plateforme y pose sa
valeur. Et c'est justement ce qui rend le défaut gênant : **c'est nous qui
écrivions deux formes.**

Mesuré le 03/09 en appelant les deux sources :

    write.at         2026-09-03T11:22:19+00:00           ← seconde
    run.started_at   2026-09-03T11:22:19.619406+00:00    ← microseconde

Deux colonnes déterministes du même tableau, deux écritures. Un troisième format
vu en production (`…T00:00:00.000Z`) ne vient d'aucune des deux : il précède ce
cran.

⚠️ **Le tri est ce qui paie** : `Z` et `+00:00` désignent le même décalage et ne
se rangent pas pareil dans l'alphabet. Le tri caste désormais en horodatage, donc
il absorbe l'existant — mais *corriger la lecture d'une donnée qu'on écrit
soi-même de travers, c'est réparer autour de la source*. Ce banc garde la source.

⚠️ **Il garde la CLASSE, pas les deux cas** : le dernier test compare les deux
sources entre elles plutôt que chacune à une constante. Une troisième source
ajoutée demain avec sa propre forme tomberait ici — vérifier deux littéraux ne
l'aurait pas vue.

Éprouvé rouge le 2026-09-03 : `depart.isoformat()` rétabli à la place de la
forme canonique ⟹ le test des deux sources nomme la divergence.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from oto_mcp.datastore.core import _now_iso
from oto_mcp.datastore.reserves import iso_utc

_PARIS = timezone(timedelta(hours=2))
_INSTANT = datetime(2026, 9, 3, 13, 22, 19, 619406, tzinfo=_PARIS)


def test_un_decalage_est_ramene_en_UTC():
    """13 h 22 à Paris, c'est 11 h 22 UTC. Sans ce ramené, deux estampilles du
    même instant portent deux heures différentes selon le fuseau de la source."""
    assert iso_utc(_INSTANT) == "2026-09-03T11:22:19+00:00"


def test_la_fraction_de_seconde_est_ECARTEE():
    """On date un travail, pas une mesure physique. La précision perdue n'a aucun
    usage ; l'uniformité, elle, se voit à chaque tri."""
    assert "." not in iso_utc(_INSTANT)


def test_la_notation_du_decalage_est_UNE():
    """`Z` et `+00:00` sont le même décalage et se rangent différemment dans
    l'alphabet — c'est exactement ce qui inversait deux instants identiques."""
    rendu = iso_utc(_INSTANT)
    assert rendu.endswith("+00:00") and not rendu.endswith("Z")


def test_les_DEUX_sources_systeme_rendent_la_meme_forme(monkeypatch):
    """Le garde-fou qui compte, et il vise l'AXE : on compare les deux sources
    entre elles, pas à un littéral. Une troisième déclarée demain avec sa propre
    forme tomberait ici.

    ⚠️ Il passe par le VRAI chemin de la seconde source — la lecture de
    l'ouverture du passage, telle que la base la rend, avec sa fraction de
    seconde et son fuseau. Ma première rédaction appelait la fonction canonique
    des deux côtés : elle comparait une chose à elle-même et restait verte le
    correctif retiré. Une garde qui affirme au lieu de vérifier est exactement ce
    que ce chantier corrige ailleurs.
    """
    from oto_mcp import db
    from oto_mcp.datastore import reserves

    reserves._OUVERTURE_DU_RUN.clear()
    brut = datetime(2026, 9, 3, 13, 22, 19, 619406, tzinfo=_PARIS)
    monkeypatch.setattr(db, "get_run_head", lambda _r: {"started_at": brut})

    valeurs = reserves.valeurs_systeme(
        {"fields": [{"key": "ouvert_a", "type": "datetime",
                     "system": "run.started_at"},
                    {"key": "ecrit_a", "type": "datetime",
                     "system": "write.at"}]},
        run="run-test", maintenant=_now_iso())
    reserves._OUVERTURE_DU_RUN.clear()

    ouverture, ecriture = valeurs["ouvert_a"], valeurs["ecrit_a"]
    assert len(ouverture) == len(ecriture), (
        f"deux formes posées par la plateforme : {ouverture!r} vs {ecriture!r}")
    assert ouverture[-6:] == ecriture[-6:] == "+00:00"
    assert "." not in ouverture and "." not in ecriture


def test_une_valeur_qui_n_est_PAS_un_instant_traverse_sans_etre_inventee():
    """Une source qui rendrait autre chose qu'une date ne doit pas se voir
    fabriquer un horodatage plausible : un champ vide se voit, une valeur fausse
    se croit."""
    assert iso_utc("pas une date") == "pas une date"
    assert iso_utc(None) == "None"
