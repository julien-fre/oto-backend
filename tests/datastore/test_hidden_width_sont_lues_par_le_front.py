"""`hidden` et `width` sont prescrites par le produit — les dénoncer était une faute.

La description de `data_set_schema` les PRESCRIT, mot pour mot : « `width`… **declare
it** to keep a stable layout », « `hidden: true`… **Use it** for opaque ids and technical
fields ». L'avertissement des clés non interprétées les dénonçait pourtant à chaque
lecture de schéma. **Le produit disait « déclare-la », puis criait sur la déclaration.**

⚠️ **Ce que ça coûtait n'est pas le bruit, c'est le SIGNAL qu'il couvrait.** Mesuré le
10/09/2026 sur les 363 tableaux à schéma du parc :

    portaient un avertissement à la lecture   224   (61 %)
      dont à cause de `hidden`                186
      dont à cause de `width`                 141
    après ce correctif                         30   ( 8 %)

**194 tableaux rendus silencieux, et 87 % du bruit venait d'une seule cause.** Les 26
tableaux portant `enum` là où `options` fait foi — la clé même qui a laissé passer 504
valeurs libres — étaient noyés dedans.

⚠️ **Vérifié dans `oto-dashboard` AVANT de les déclarer**, parce qu'une clé « front »
que personne ne lirait serait la même faute dans l'autre sens : `hidden` filtre les
cartes et pilote la sauvegarde de vue, `width` est lu par le tiroir de fiche. Elles sont
donc dans le cas de `label` — interprétées par le consommateur auquel elles s'adressent,
jamais par le validateur.
"""
from __future__ import annotations

from oto_mcp.datastore import cles_inconnues
from oto_mcp.datastore import schema as dsv2
from oto_mcp.datastore import schema_keys as sk

PRESCRITES = ("hidden", "width")


def test_elles_sont_declarees_FRONT_et_pas_validateur():
    """⚠️ La nuance porte tout : le serveur ne les applique pas, et le prétendre serait
    le défaut inverse. Elles sont lues en aval, comme `label`."""
    for cle in PRESCRITES:
        assert cle in sk.ADMISES["champ"], f"`{cle}` est prescrite par le produit"
        assert cle in sk.LUES_PAR_LE_FRONT, f"`{cle}` est lue par le front"
        assert cle not in sk.LUES_PAR_LE_VALIDATEUR, (
            f"`{cle}` ne contraint RIEN côté serveur — la déclarer appliquée serait "
            f"la même faute, dans l'autre sens")


def test_un_schema_qui_les_porte_est_admis_et_ne_dit_rien():
    """Le cas des 194 tableaux : ils suivaient la consigne et recevaient un reproche.
    Depuis la fermeture du vocabulaire (01/10/2026), le reproche serait un REFUS."""
    S = {"fields": [{"key": "ident", "type": "text", "hidden": True, "width": "half"}]}
    assert dsv2.validate_schema_def(S) == []
    assert cles_inconnues.residus_warning(S) is None


def test_le_SIGNAL_survit_a_la_desaturation():
    """⚠️ L'autre moitié : `enum` à côté d'un `options` qui fait foi reste dénoncé —
    refusé s'il est posé, dit à la lecture s'il est déjà stocké, avec la clé qui
    décide."""
    S = {"fields": [{"key": "statut", "type": "enum",
                     "enum": ["a", "b"], "options": ["a", "b", "c"],
                     "hidden": True, "width": "full"}]}
    errs = dsv2.validate_schema_def(S)
    assert len(errs) == 1 and "voulais-tu `options` ?" in errs[0], errs
    w = cles_inconnues.residus_warning(S)
    assert w and "`fields.statut.enum`" in w and "Ce qui fait foi : `options`" in w


def test_ce_qui_reste_denonce_ne_contient_PAS_les_prescrites():
    """Le canal reste lisible : ce qui parle est ce qui mérite d'être lu."""
    S = {"fields": [{"key": "x", "hidden": True, "width": "full", "zorglub": 1}]}
    errs = dsv2.validate_schema_def(S)
    assert len(errs) == 1 and "`zorglub`" in errs[0], errs
    assert "hidden" not in errs[0].split(" — ")[0] and "width" not in errs[0].split(" — ")[0]


#: Le cas inverse (oto#34) : déclarées « front » sans qu'aucun front ne les lise.
#: Le dashboard ne les rend pas — `schema-keys-check.mjs` les comptait en dette
#: « servie-non-lue » depuis la remesure du 01/10/2026.
LUES_PAR_LE_SEUL_VALIDATEUR = ("agent_access", "formula", "required_layers")


def test_les_reglages_de_colonne_ne_promettent_PAS_de_lecteur_front():
    """Une clé « front » que personne ne lit est la faute de `hidden`/`width` à
    l'envers : la plateforme promet un lecteur qui n'existe pas."""
    servie = {e["key"]: e for e in sk.servie()["keys"]}
    for cle in LUES_PAR_LE_SEUL_VALIDATEUR:
        assert cle in sk.LUES_PAR_LE_VALIDATEUR, f"`{cle}` contraint le serveur"
        assert cle not in sk.LUES_PAR_LE_FRONT, f"`{cle}` n'est lue par aucun front"
        assert "front" not in servie[cle]["readers"], servie[cle]
