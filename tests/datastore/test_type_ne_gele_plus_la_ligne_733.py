"""Une valeur déjà en base qui ne passe plus le TYPE ne gèle plus la ligne (#733).

Le cas signalé par un tenant : un tableau déclare qu'une colonne porte une adresse
web. Des lignes de socle, créées avant cette déclaration, y portent autre chose
(`prior-contact://…`). Écrire une NOTE sur une de ces lignes — rien à voir avec
l'adresse — était refusé, parce que la validation juge la ligne telle qu'elle sera,
pas ce que l'appel écrit. La ligne devenait inécritable pour toujours, sur n'importe
quel champ.

⚠️ **Ce n'était pas un choix, c'était un oubli.** Trois contrôles voisins — la borne
de longueur, le motif, la fermeture d'un composite — s'étaient DÉJÀ vu restreindre
aux clés écrites, pour exactement ce défaut : leur commentaire cite « 23 lignes
gelées chez un client ». Le type produisait le même effet et n'avait pas suivi.

Ce que ce banc fige, et c'est le triangle entier :

1. écrire AILLEURS passe — la ligne n'est plus gelée par son passé ;
2. la colonne fautive est SIGNALÉE, pas tue — refuser gèle, taire laisse pourrir ;
3. écrire la colonne fautive refuse TOUJOURS — la garde n'est pas affaiblie, elle
   est ramenée sur ce que le geste pose.
"""
from __future__ import annotations

from oto_mcp.datastore import schema as dsv2

SCHEMA = {
    "unknown_columns": "report",
    "fields": [
        {"key": "linkedin_url", "type": "url"},
        {"key": "notes", "type": "text"},
    ],
}

#: La ligne de socle : semée avant que le format ne soit déclaré.
EN_BASE = {"linkedin_url": "prior-contact://orgil-lazard", "notes": "ancienne"}


def test_ecrire_ailleurs_ne_se_fait_plus_refuser():
    """LE cas du signalement : la note passe, l'adresse fautive ne bloque plus."""
    merge = {**EN_BASE, "notes": "note du jour"}

    assert dsv2.validate_row(SCHEMA, merge, written={"notes"}) == []


def test_la_colonne_fautive_est_signalee_et_pas_tue():
    """Ne pas refuser n'est pas se taire : l'agent qui passe par cette ligne est le
    seul à pouvoir l'apprendre, et le relevé garde le refus qu'on aurait rendu — donc
    ce qu'il faut y écrire pour la remettre en règle."""
    merge = {**EN_BASE, "notes": "note du jour"}
    gelees: list = []

    dsv2.validate_row(SCHEMA, merge, written={"notes"}, gelees=gelees)

    assert [g["champ"] for g in gelees] == ["linkedin_url"]
    assert "attendu une URL" in gelees[0]["refus"]

    phrase = dsv2.types_geles_warning(gelees)
    assert "ton écriture est passée" in phrase
    assert "`linkedin_url`" in phrase


def test_ecrire_LA_colonne_fautive_refuse_toujours():
    """La garde n'est pas affaiblie : elle est ramenée sur ce que le geste POSE.
    Sans ce cas, le correctif se lirait comme un désarmement."""
    merge = {**EN_BASE, "linkedin_url": "toujours-pas-une-url"}

    errors = dsv2.validate_row(SCHEMA, merge, written={"linkedin_url"})

    assert errors and "attendu une URL" in errors[0]


def test_une_creation_juge_TOUT_comme_avant():
    """`written=None` = la row entière (insert ou remplacement) : il n'y a pas de
    « déjà en base » à épargner, et rien ne change."""
    errors = dsv2.validate_row(SCHEMA, EN_BASE, written=None)

    assert errors and "attendu une URL" in errors[0]


def test_une_ligne_conforme_ne_signale_rien():
    """Le relevé est le SIGNAL, son absence est le cas normal — pas de clé parasite
    dans la réponse d'une écriture ordinaire."""
    saine = {"linkedin_url": "https://www.linkedin.com/in/x", "notes": "ok"}
    gelees: list = []

    assert dsv2.validate_row(SCHEMA, saine, written={"notes"}, gelees=gelees) == []
    assert gelees == []
    assert dsv2.types_geles_warning(gelees) is None


def test_la_borne_de_liste_suit_la_meme_regle():
    """Alignée le 07/09/2026, et pour la même raison : c'est une propriété de la
    valeur qu'on POSE. La laisser sur le mergé aurait gardé le défaut que le type
    venait de quitter, sur son voisin immédiat — deux contrôles de la même famille
    se comportant différemment, ce que personne n'aurait pu deviner."""
    schema = {"unknown_columns": "report", "fields": [
        {"key": "contacts", "type": "list", "of": {"type": "text"}, "max_items": 2},
        {"key": "notes", "type": "text"},
    ]}
    en_base = {"contacts": ["a", "b", "c", "d"], "notes": "ancienne"}
    gelees: list = []

    # écrire ailleurs : ça passe, et la liste trop longue est signalée
    assert dsv2.validate_row(schema, {**en_base, "notes": "neuve"},
                             written={"notes"}, gelees=gelees) == []
    assert [g["champ"] for g in gelees] == ["contacts"]
    assert "4 éléments, maximum 2" in gelees[0]["refus"]

    # écrire LA liste : refusé, comme avant
    errors = dsv2.validate_row(schema, {**en_base, "contacts": ["a", "b", "c"]},
                               written={"contacts"})
    assert errors and "3 éléments, maximum 2" in errors[0]


def test_le_requis_continue_de_se_juger_sur_la_ligne_ENTIERE():
    """La ligne de partage que le correctif suit : ce qui juge la VALEUR QU'ON POSE
    se restreint au geste (type, longueur, motif, fermeture) ; ce qui juge l'ÉTAT DE
    LA LIGNE reste sur le mergé. Un requis manquant est un défaut de la ligne, quel
    que soit le geste qui l'y laisse — sans quoi on pourrait laisser une ligne
    incomplète en écrivant toujours à côté."""
    schema = {"unknown_columns": "report", "fields": [
        {"key": "siren", "type": "text", "required": True},
        {"key": "notes", "type": "text"},
    ]}

    errors = dsv2.validate_row(schema, {"notes": "posée"}, written={"notes"})

    assert errors and "champ requis manquant" in errors[0]


def test_un_etat_devenu_invalide_ne_gele_plus_la_ligne():
    """Le SIXIÈME contrôle de la famille, et le seul que personne n'avait rapporté :
    il est sorti d'une vérification faite en cherchant autre chose.

    Une colonne d'état porte une valeur qu'on a depuis retirée de la liste déclarée.
    Toute écriture sur la ligne était refusée « état inconnu », y compris une note
    sans rapport — et pour toujours, puisque corriger l'état demandait d'écrire, ce
    qui était justement refusé.

    Même partage que ses cinq voisins : « cet état est-il permis » juge une VALEUR,
    donc ce que l'appel écrit. La TRANSITION, elle, n'a pas besoin d'être gardée :
    elle ne se juge que si l'état change, donc que si le geste l'écrit."""
    schema = {"fields": [
        {"key": "statut", "type": "enum", "role": "status",
         "options": ["a_faire", "traite"],
         "lifecycle": {"states": ["a_faire", "traite"],
                       "transitions": {"a_faire": ["traite"]}}},
        {"key": "notes", "type": "text"},
    ]}
    en_base = {"statut": "en_pause", "notes": "ancienne"}   # état retiré depuis
    gelees: list = []

    # écrire la note : passe, et l'état devenu invalide est signalé
    assert dsv2.validate_row(schema, {**en_base, "notes": "neuve"},
                             prev_status="en_pause", written={"notes"},
                             gelees=gelees) == []
    assert [g["champ"] for g in gelees] == ["statut"]
    assert "état inconnu" in gelees[0]["refus"]

    # écrire un état invalide refuse toujours
    errs = dsv2.validate_row(schema, {**en_base, "statut": "n_importe_quoi"},
                             prev_status="en_pause", written={"statut"})
    assert errs and "état inconnu" in errs[0]

    # et une transition interdite refuse toujours
    errs = dsv2.validate_row(schema, {"statut": "a_faire", "notes": "x"},
                             prev_status="traite", written={"statut"})
    assert errs and "transition" in errs[0]
