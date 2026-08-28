"""Datastore v2 (ADR 0046) — moteur de schéma structuré, module PUR.

Couvre : définition (types imbriqués, lifecycle), activation opt-in, validation
de row (required / required_when / types / imbrication) et cycle de vie (états,
transitions, terminaux dérivés). Le schéma de démo = la fiche « lead PV » de la
genèse (GR) : le guard-rail « pas de qualified sans les 4 livrables ».
"""
import pytest

from oto_mcp.datastore import schema as dsv2


LEAD_SCHEMA = {
    "strict": True,
    "key": "fact_id",
    "fields": [
        {"key": "fact_id", "type": "text", "required": True, "role": "title"},
        {"key": "siren", "type": "text"},
        {"key": "mwh", "type": "number"},
        {"key": "occupant", "type": "object",
         "fields": [{"key": "nom", "type": "text", "required": True},
                    {"key": "naf", "type": "text"}]},
        {"key": "contacts", "type": "list",
         "of": {"fields": [{"key": "nom", "type": "text", "required": True},
                           {"key": "email", "type": "text"}]}},
        {"key": "status", "role": "status",
         "lifecycle": {"states": ["nouveau", "en_cours", "qualified", "ecarte"],
                       "transitions": {"nouveau": ["en_cours"],
                                       "en_cours": ["qualified", "ecarte", "nouveau"]}}},
        {"key": "qualification", "type": "text",
         "required_when": {"status": "qualified"}},
        {"key": "cold_email", "type": "text",
         "required_when": {"status": "qualified"}},
    ],
}


# ── définition ────────────────────────────────────────────────────────────────

def test_flat_0016_schema_still_valid():
    assert dsv2.validate_schema_def(
        {"fields": [{"key": "a", "type": "text"}], "key": "a"}) == []


def test_nested_schema_valid():
    assert dsv2.validate_schema_def(LEAD_SCHEMA) == []


def test_def_rejects_unknown_type_and_malformed_composites():
    errs = dsv2.validate_schema_def({"fields": [
        {"key": "x", "type": "wat"},
        {"key": "o", "type": "object"},              # object sans fields
        {"key": "l", "type": "list"},                # list sans of
    ]})
    assert any("type inconnu" in e for e in errs)
    assert any("exige fields" in e for e in errs)
    assert any("exige of" in e for e in errs)


def test_def_rejects_lifecycle_inconsistencies():
    errs = dsv2.validate_schema_def({"fields": [
        {"key": "status", "role": "status",
         "lifecycle": {"states": ["a"], "transitions": {"a": ["b"]},
                       "terminal": ["c"]}}]})
    assert any("cible inconnu 'b'" in e for e in errs)
    assert any("terminal: état inconnu 'c'" in e for e in errs)


def test_def_rejects_malformed_max_length():
    errs = dsv2.validate_schema_def({"fields": [
        {"key": "a", "type": "text", "max_length": "60"},   # pas un entier
        {"key": "b", "type": "text", "max_length": 0},      # borne vide
        {"key": "c", "type": "list", "of": {"type": "text"},
         "max_length": 30},                                  # composite : borne de quoi ?
    ]})
    assert sum("max_length" in e for e in errs) == 3
    assert any("scalaire" in e for e in errs)
    assert dsv2.validate_schema_def(
        {"fields": [{"key": "a", "type": "text", "max_length": 60}]}) == []


def test_def_rejects_lifecycle_on_non_status_field():
    errs = dsv2.validate_schema_def({"fields": [
        {"key": "etat", "lifecycle": {"states": ["a"]}}]})
    assert any('role="status"' in e for e in errs)


# ── activation opt-in ─────────────────────────────────────────────────────────

def test_validation_inactive_by_default():
    assert not dsv2.validation_active({"fields": [{"key": "a", "type": "number"}]})
    assert not dsv2.validation_active(None)


def test_validation_active_via_strict_or_required():
    assert dsv2.validation_active({"strict": True, "fields": []})
    assert dsv2.validation_active(
        {"fields": [{"key": "a", "required": True}]})
    assert dsv2.validation_active(
        {"fields": [{"key": "a", "required_when": {"s": "x"}}]})


def test_max_length_alone_activates_validation():
    """Sinon la borne est INERTE, silencieusement (#383) — y compris posée sur un
    sous-champ, où la profondeur est le cas nominal (contacts[].fonction)."""
    assert dsv2.validation_active(
        {"fields": [{"key": "fonction", "type": "text", "max_length": 60}]})
    assert dsv2.validation_active({"fields": [
        {"key": "contacts", "type": "list",
         "of": {"fields": [{"key": "fonction", "type": "text",
                            "max_length": 60}]}}]})
    # une borne mal formée n'active rien (elle est refusée à la pose)
    assert not dsv2.validation_active(
        {"fields": [{"key": "a", "type": "text", "max_length": "60"}]})


# ── validation de row ─────────────────────────────────────────────────────────

def test_soft_schema_validates_nothing():
    schema = {"fields": [{"key": "mwh", "type": "number"}]}  # 0016 : soft
    assert dsv2.validate_row(schema, {"mwh": "pas-un-nombre"}) == []


def test_required_missing():
    errs = dsv2.validate_row(LEAD_SCHEMA, {"status": "nouveau"})
    assert any("fact_id" in e and "requis" in e for e in errs)


def test_guard_rail_required_when_qualified():
    """LE guard-rail GR : qualified sans livrables = refus ; avec = OK."""
    base = {"fact_id": "f1", "status": "qualified"}
    errs = dsv2.validate_row(LEAD_SCHEMA, base, prev_status="en_cours")
    assert any("qualification" in e for e in errs)
    assert any("cold_email" in e for e in errs)
    ok = dsv2.validate_row(
        LEAD_SCHEMA, {**base, "qualification": "site très intéressant…",
                      "cold_email": "Bonjour…"}, prev_status="en_cours")
    assert ok == []


def test_required_when_inert_on_other_status():
    errs = dsv2.validate_row(LEAD_SCHEMA,
                             {"fact_id": "f1", "status": "en_cours"},
                             prev_status="nouveau")
    assert errs == []


def test_type_conformity_scalars():
    errs = dsv2.validate_row(LEAD_SCHEMA, {"fact_id": "f1", "mwh": "abc"})
    assert any("mwh" in e and "number" in e for e in errs)
    # coercible : l'agent écrit "1200" → accepté
    assert dsv2.validate_row(LEAD_SCHEMA, {"fact_id": "f1", "mwh": "1200"}) == []
    assert dsv2.validate_row(LEAD_SCHEMA, {"fact_id": "f1", "mwh": 1200.5}) == []


def test_nested_object_and_list_validated():
    errs = dsv2.validate_row(LEAD_SCHEMA, {
        "fact_id": "f1",
        "occupant": {"naf": "4711F"},                 # nom requis manquant
        "contacts": [{"email": "a@b.fr"}, "oops"],    # [0] nom manquant, [1] pas un objet
    })
    assert any(e.startswith("occupant.nom") for e in errs)
    assert any(e.startswith("contacts[0].nom") for e in errs)
    assert any("contacts[1]" in e and "object" in e for e in errs)


# ── borne de longueur (#383) ─────────────────────────────────────────────────

BOUNDED = {"fields": [
    {"key": "societe", "type": "text", "required": True},
    {"key": "fonction", "type": "text", "max_length": 60},
    {"key": "notes", "type": "text"},
    {"key": "contacts", "type": "list",
     "of": {"fields": [{"key": "fonction", "type": "text", "max_length": 60}]}},
]}
# Le cas réel : le raisonnement écrit dans la colonne « fonction » (247 car.)
_OVERLONG = ("C.O.O & CFO / Directeur General Operations et Finance. Dans "
             "l'entreprise depuis 1996 et a ce poste depuis septembre 2019, base "
             "a Gennevilliers : c'est le decideur sur le cout charge, et de loin "
             "le contact le mieux etabli de la fiche")


def test_max_length_refuses_and_names_both_numbers():
    errs = dsv2.validate_row(BOUNDED, {"societe": "ACME", "fonction": _OVERLONG})
    assert len(errs) == 1
    assert errs[0] == f"fonction: {len(_OVERLONG)} caractères, maximum 60"
    assert dsv2.validate_row(BOUNDED, {"societe": "ACME", "fonction": "DAF"}) == []


def test_max_length_applies_inside_sub_records():
    errs = dsv2.validate_row(BOUNDED, {"societe": "ACME",
                                       "contacts": [{"fonction": _OVERLONG}]})
    assert any(e.startswith("contacts[0].fonction") and "maximum 60" in e
               for e in errs)


def test_max_length_only_judges_the_keys_actually_written():
    """Une valeur trop longue DÉJÀ en base ne doit pas bloquer un patch qui porte
    sur un autre champ (#383) — mais la réécrire trop longue, si."""
    merged = {"societe": "ACME", "fonction": _OVERLONG, "notes": "rappelé"}
    assert dsv2.validate_row(BOUNDED, merged, written={"notes"}) == []
    errs = dsv2.validate_row(BOUNDED, merged, written={"fonction"})
    assert any("fonction" in e and "maximum 60" in e for e in errs)
    # written=None (insert / remplacement intégral) = tout est jugé
    assert dsv2.validate_row(BOUNDED, merged) != []


def test_max_length_unbounded_field_untouched():
    assert dsv2.validate_row(BOUNDED, {"societe": "ACME",
                                       "notes": _OVERLONG}) == []


def test_lifecycle_unknown_state_and_forbidden_transition():
    errs = dsv2.validate_row(LEAD_SCHEMA, {"fact_id": "f1", "status": "wat"})
    assert any("état inconnu" in e for e in errs)
    errs = dsv2.validate_row(LEAD_SCHEMA,
                             {"fact_id": "f1", "status": "qualified"},
                             prev_status="nouveau")  # nouveau → qualified interdit
    assert any("transition" in e and "interdite" in e for e in errs)


def test_lifecycle_same_state_write_is_free():
    assert dsv2.validate_row(LEAD_SCHEMA,
                             {"fact_id": "f1", "status": "en_cours"},
                             prev_status="en_cours") == []


def test_terminal_states_derived_and_explicit():
    # dérivés : qualified/ecarte n'ont pas de transition sortante
    assert dsv2.terminal_states(LEAD_SCHEMA) == {"qualified", "ecarte"}
    assert dsv2.is_terminal_status(LEAD_SCHEMA, "qualified")
    assert not dsv2.is_terminal_status(LEAD_SCHEMA, "en_cours")
    explicit = {"fields": [{"key": "s", "role": "status",
                            "lifecycle": {"states": ["a", "b"], "terminal": ["b"]}}]}
    assert dsv2.terminal_states(explicit) == {"b"}


# ── #347 : required_when à LISTE — la déclaration qui élargit ne désarme plus ──

def test_une_liste_de_valeurs_mord_sur_chacune():
    """`required_when: {status: [a, b]}` = requis quand la valeur ∈ liste. Avant
    #347 la liste était acceptée, stockée, et la contrainte devenait INERTE pour
    TOUTES les valeurs — y compris celle qui mordait en scalaire : la déclaration
    qui semblait élargir la garde la supprimait sans un mot."""
    schema = {"fields": [
        {"key": "status"},
        {"key": "motif", "required_when": {"status": ["hors_perimetre", "eteinte"]}},
    ]}
    for statut in ("hors_perimetre", "eteinte"):
        errs = dsv2.validate_row(schema, {"status": statut})
        assert errs and "motif" in errs[0], f"la contrainte doit mordre sur {statut}"
    assert dsv2.validate_row(schema, {"status": "active"}) == [], \
        "hors liste, rien n'est requis"
    assert dsv2.validate_row(
        schema, {"status": "eteinte", "motif": "radiation BODACC"}) == []


def test_le_scalaire_continue_de_mordre():
    schema = {"fields": [{"key": "s"}, {"key": "m", "required_when": {"s": "x"}}]}
    assert dsv2.validate_row(schema, {"s": "x"})
    assert dsv2.validate_row(schema, {"s": "y"}) == []


def test_une_forme_non_interpretee_est_refusee_a_la_pose():
    """La règle de la famille #329/#331 : ce qu'une surface ne sait pas
    interpréter, elle le REFUSE en le nommant — jamais stocké-inerte."""
    for mauvaise in ({"s": {"nested": 1}},        # dict en condition
                     {"s": [["a"]]},              # liste imbriquée
                     {"s": []},                   # liste vide : rien à matcher
                     {"s": None}):                # None : condition indicible
        errs = dsv2.validate_schema_def(
            {"fields": [{"key": "m", "required_when": mauvaise}]})
        assert errs and "required_when" in errs[0], f"{mauvaise!r} doit être refusé"


def test_la_liste_valide_est_acceptee_a_la_pose():
    assert dsv2.validate_schema_def(
        {"fields": [{"key": "m",
                     "required_when": {"s": ["a", "b"]}}]}) == []


def test_la_condition_deballe_les_couches_liste_et_scalaire():
    """Le trou RÉEL derrière « la garde ne mord pas » (re-validation, 15/08) :
    la condition lisait la valeur BRUTE — une qualification écrite en couches
    ({"valeur": …}, le geste NORMAL des agents, et le résultat de tout merge
    sur une ligne portant une couche) est un dict qui ne matche rien. Liste ET
    scalaire étaient désarmés pareil ; « tout ce qui juge une valeur déballe
    d'abord » vaut aussi pour les CONDITIONS."""
    for cond in ({"s": ["x", "y"]}, {"s": "x"}):
        schema = {"fields": [{"key": "s"}, {"key": "m", "required_when": cond}]}
        errs = dsv2.validate_row(schema, {"s": {"valeur": "x", "comment": "j"}})
        assert errs and "m" in errs[0], \
            f"condition {cond} désarmée par une valeur en couches"
        assert dsv2.validate_row(
            schema, {"s": {"valeur": "x"}, "m": "motif"}) == []


def test_la_condition_mord_sur_le_resultat_fusionne():
    """Le cas le plus fréquent en campagne (lignes reprises) : la ligne porte
    déjà la qualification EN COUCHES, l'agent réécrit une valeur NUE — le merge
    reconstruit les couches (l'origine survit), et c'est le RÉSULTAT FUSIONNÉ
    que la condition doit juger. Si elle ne déballait que l'écriture entrante,
    le trou se rouvrirait exactement là."""
    from oto_mcp.datastore.columns import _merge_column

    schema = {"fields": [
        {"key": "qualification"},
        {"key": "motif_ecartement",
         "required_when": {"qualification": ["hors_perimetre", "dormante_ou_introuvable"]}},
    ]}
    existant = {"valeur": "en_activite", "origine": "fichier-client"}
    merged = _merge_column(existant, "hors_perimetre")
    assert isinstance(merged, dict) and merged.get("valeur") == "hors_perimetre", \
        "le merge préserve l'origine : la colonne reste en couches"
    errs = dsv2.validate_row(schema, {"qualification": merged})
    assert errs and "motif_ecartement" in errs[0], \
        "la condition juge le résultat fusionné, couches comprises"


# ── #377 : la cible d'un required_when peut être une COUCHE ───────────────────
#
# Le cas réel de campagne : exiger la JUSTIFICATION quand la qualification prend
# certaines valeurs. La justification n'est pas une colonne sœur — c'est la couche
# `comment` de la qualification elle-même, celle que l'agent écrit du même geste.
#
# Avant #377 cette déclaration était ACCEPTÉE à la pose puis refusait toute
# écriture déclenchante, **y compris celle qui portait le commentaire** : le
# contrôle cherchait une colonne littérale `qualification.comment`, que
# `_refuse_dotted_names` interdit précisément d'écrire. Une contrainte
# insatisfiable par construction — pire qu'inerte, puisque la pose avait l'air
# d'avoir marché.

QUALIF_SCHEMA = {"fields": [
    {"key": "siren", "type": "text"},
    {"key": "qualification", "type": "text"},
    {"key": "qualification.comment", "type": "text", "label": "Justification",
     "required_when": {"qualification": ["hors_perimetre", "dormante_ou_introuvable"]}},
]}


def test_la_cible_de_couche_est_acceptee_a_la_pose():
    assert dsv2.validate_schema_def(QUALIF_SCHEMA) == []


def test_la_couche_requise_mord_quand_elle_manque():
    for q in ("hors_perimetre", "dormante_ou_introuvable"):
        errs = dsv2.validate_row(QUALIF_SCHEMA, {"siren": "1", "qualification": q})
        assert errs and "qualification.comment" in errs[0], \
            f"la justification doit être exigée sur {q}"


def test_la_couche_requise_passe_quand_elle_est_ecrite():
    """LE défaut de #377 : cette écriture-là était refusée. La justification est
    écrite EN COUCHES sur la colonne qualifiée — le geste nominal d'un agent."""
    assert dsv2.validate_row(QUALIF_SCHEMA, {
        "siren": "1",
        "qualification": {"valeur": "hors_perimetre",
                          "comment": "NAF 68.20A — hors périmètre santé"},
    }) == []


def test_hors_condition_la_couche_n_est_pas_exigee():
    assert dsv2.validate_row(
        QUALIF_SCHEMA, {"siren": "1", "qualification": "en_activite"}) == []


def test_une_colonne_nue_ne_porte_aucune_couche():
    """La colonne écrite en scalaire n'a pas de couche `comment` : la contrainte
    mord. Sans quoi il suffirait d'écrire la valeur nue pour échapper au motif."""
    errs = dsv2.validate_row(
        QUALIF_SCHEMA, {"siren": "1", "qualification": "hors_perimetre"})
    assert errs and "qualification.comment" in errs[0]


def test_la_couche_d_une_colonne_inconnue_est_refusee_nommement():
    """Sans base déclarée, la cible ne désigne rien — et la contrainte serait
    insatisfiable en silence, le défaut même qu'on ferme."""
    errs = dsv2.validate_schema_def({"fields": [
        {"key": "qualification", "type": "text"},
        {"key": "inexistante.comment", "required_when": {"qualification": "x"}}]})
    assert errs and "inexistante" in errs[0], errs


def test_un_suffixe_qui_n_est_pas_une_couche_est_refuse():
    """`qualification.commnet` (faute de frappe) et `qualification.valeur` (la
    valeur se désigne par le nom NU) ne sont pas des cibles : refus nommé plutôt
    qu'une colonne littérale fantôme."""
    for mauvaise in ("qualification.commnet", "qualification.valeur"):
        errs = dsv2.validate_schema_def({"fields": [
            {"key": "qualification", "type": "text"},
            {"key": mauvaise, "required_when": {"qualification": "x"}}]})
        assert errs and "couche" in errs[0].lower(), f"{mauvaise} doit être refusé"


def test_une_cible_de_couche_ne_declare_pas_de_colonne():
    """Une couche n'est pas une colonne : elle ne nomme pas la ligne, ne porte pas
    son statut, ne se subdivise pas. Ces clés seraient LUES NULLE PART — la forme
    acceptée-inerte que #347 a fermée."""
    for cle, valeur in (("role", "status"), ("display", "title"),
                        ("fields", [{"key": "x"}]), ("flat_alias", "q{n}_{attr}")):
        errs = dsv2.validate_schema_def({"fields": [
            {"key": "qualification", "type": "text"},
            {"key": "qualification.comment", cle: valeur}]})
        assert errs and cle in errs[0], f"`{cle}` sur une couche doit être refusé"


def test_le_type_et_la_borne_portent_sur_la_valeur_de_la_couche():
    """Ce qui juge, déballe — et ce qui est jugé ici est le TEXTE du commentaire,
    pas l'enveloppe de la colonne."""
    schema = {"fields": [
        {"key": "q", "type": "text"},
        {"key": "q.comment", "type": "text", "max_length": 20, "required": True}]}
    assert dsv2.validate_row(schema, {"q": {"valeur": "x", "comment": "court"}}) == []
    errs = dsv2.validate_row(
        schema, {"q": {"valeur": "x", "comment": "beaucoup trop long pour la borne"}})
    assert errs and "20" in errs[0], errs


def test_la_cible_de_couche_active_la_validation():
    """Elle déclare un requis : sans activation, la contrainte serait posée pour rien."""
    assert dsv2.validation_active(
        {"fields": [{"key": "q"}, {"key": "q.comment", "required_when": {"q": "x"}}]})


def test_une_couche_n_est_pas_annoncee_comme_colonne_mesurable():
    """`top_level_bounds` sert une requête `data->>clé` — un chemin de couche n'en
    est pas un. L'y laisser ferait mesurer une colonne littérale qui n'existe pas,
    donc rendre « aucune ligne hors borne » sur un tableau non vérifié."""
    assert dsv2.top_level_bounds({"fields": [
        {"key": "q", "type": "text"},
        {"key": "q.comment", "type": "text", "max_length": 20}]}) == {}
