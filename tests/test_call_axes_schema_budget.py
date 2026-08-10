"""La prose d'un jeton de contexte est recopiée ~350 fois : elle a un budget (issue #277).

`inject_schema` recopie `CallAxis.schema` — description comprise — dans le schéma d'entrée
de CHAQUE tool auquel l'axe s'applique. Une phrase ajoutée ici n'est donc jamais une
phrase : c'est ~350 phrases, payées par tout client qui charge `tools/list`.

Mesuré sur `mcp.oto.cx` le 10/08/2026, avant la coupe : les six axes pesaient 1 845
caractères par tool, soit **561 207 des 914 045 caractères de schémas** exposés — 61 % de
la boîte, six paragraphes répétés à l'identique. Un agent Mistral (fenêtre 128 k) ne
pouvait pas charger la boîte du tout ; un client qui injecte lui-même ces jetons les payait
à chaque tour sans jamais les lire.

Le *pourquoi* de ces jetons vit **une fois**, dans le bloc A des instructions serveur
(`instructions.py`, « Porte ton contexte DANS l'appel ») — injecté au handshake. Ce qui
reste dans le schéma : ce que l'axe fait, et où trouver sa valeur.

Ce garde-fou est mécanique parce que la discipline ne tient pas : chaque incident donne
envie d'ajouter « ⚠️ … » à l'axe concerné, et c'est exactement comme ça qu'on est arrivé à
433 caractères sur `_account`. Un test qui casse dit où écrire à la place.
"""
import pytest

from oto_mcp import call_axes, instructions

# Plafond par axe. Deux phrases tiennent largement dedans ; un paragraphe, non.
MAX_CHARS = 200
# Plafond cumulé = ce que TOUT tool de connecteur paie (les six axes s'y appliquent).
MAX_TOTAL = 1_000


@pytest.mark.parametrize("axis", call_axes.AXES, ids=lambda a: a.param)
def test_axis_description_stays_short(axis):
    desc = axis.schema.get("description", "")
    assert desc, f"{axis.param} doit dire ce qu'il fait"
    assert len(desc) <= MAX_CHARS, (
        f"`{axis.param}` : {len(desc)} caractères (max {MAX_CHARS}). Cette prose est "
        f"recopiée dans ~350 schémas. Le contexte long va dans le bloc A "
        f"(`instructions._SECRET_SAUCE`), pas ici."
    )


def test_total_axis_weight_stays_bounded():
    total = sum(len(a.schema.get("description", "")) for a in call_axes.AXES)
    assert total <= MAX_TOTAL, (
        f"{total} caractères de prose d'axe par tool de connecteur (max {MAX_TOTAL}) — "
        f"soit ~{total * 356 // 1000} k caractères sur la surface exposée."
    )


def test_every_axis_says_where_to_get_its_value():
    """Couper le pourquoi, oui ; couper le comment, non. Un jeton dont on ne sait pas
    fabriquer la valeur est pire que pas de jeton : l'agent devine un id."""
    # `_run_id` fait exception : sa valeur vient de `run_start`, cité dans sa description.
    for axis in call_axes.AXES:
        desc = axis.schema["description"]
        assert any(k in desc for k in ("oto_project", "oto_identity", "oto_instance",
                                       "run_start", "(id)")), (
            f"`{axis.param}` ne dit pas d'où vient sa valeur : {desc!r}")


def test_the_long_form_lives_in_the_server_instructions():
    """Le contrepoids du test précédent : si on coupe ici, le texte doit exister LÀ-BAS.
    Sinon la coupe n'est pas une déduplication, c'est une perte."""
    block = instructions._SECRET_SAUCE
    for axis in call_axes.AXES:
        assert axis.param in block, (
            f"`{axis.param}` n'est décrit nulle part : absent du bloc A, et réduit à une "
            f"ligne dans les schémas. Documente-le dans `instructions._SECRET_SAUCE`.")
