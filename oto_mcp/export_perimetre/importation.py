"""L'import d'un export de périmètre dans une base NÉE PAR `init_db`, JAMAIS DÉMARRÉE.

La cible est une base vierge qu'`init_db` a montée pour l'instance qui la sert (#969) :
même schéma, version Alembic posée, tenant primaire (ligne 1) semé depuis
`OTO_TENANT_PRIMAIRE_SLUG`. L'app n'y a pas encore démarré : son premier démarrage y
sème ses propres lignes, que l'import heurterait (#1161). L'import y verse le fichier en
UNE transaction, et ne la valide qu'après s'être relu.

Refus, tous AVANT la première écriture et chacun nommé (`ImportRefuse`) : fichier dont
l'empreinte ou les comptes ne sont pas ceux du manifeste, version de schéma différente
de la source ou colonnes qui n'y sont pas les mêmes (comparées par NOM, l'ordre ne compte
pas : `_ecarts_de_colonnes`), base cible qui n'est pas vierge (`controler_vierge` : une
table que l'import écrit porte des lignes que sa naissance n'y a pas semées), tenant
primaire de la cible dont le slug ou le NOM (semé depuis `OTO_BRAND_NAME`) n'est pas
celui du tenant exporté, secrets ou objets chiffrés sous une autre clé que celle de CETTE
instance, archive des objets absente ou modifiée.

L'import ne connaît que la clé de son instance : les secrets arrivent déjà rechiffrés
pour elle (`rechiffrement`, fait à l'export). Il vérifie seulement, en mémoire, que
chacun se déchiffre sous sa clé et l'AAD de la ligne qu'il écrit.

Ce qui change en chemin est `transformation.Transformation`, la fonction même dont
l'export s'est servi pour les AAD : le tenant devient la ligne 1 (la ligne semée prend
ses valeurs), toute clé vers `tenants(id)` vaut 1, les comptes perdent leur préfixe, et
les URL de notre stockage public deviennent celles du stockage de la cible.

Les objets (`objets`) se versent de l'archive dans le stockage de la cible, après la
relecture et avant la validation : un objet qui ne se verse pas annule tout, et un
nouvel essai saute ceux qui sont déjà là. La relecture refuse s'il subsiste la moindre
URL de notre stockage dans le périmètre.

L'écriture se fait par LOTS (`TAILLE_LOT` lignes par aller-retour) : un journal
d'appels complet se compte en centaines de milliers de lignes.

Un export SANS journal (`manifeste["journal"]["inclus"]` faux) se relit avec le même
classement que l'export (`classement.sans_journal`) : la cible peut déjà porter des
tranches du journal, poussées la veille (`journal`), que la relecture ne compte pas. Les
séquences ne font jamais que monter (`avancer_sequence`) : celle du journal, déjà
avancée par une tranche, ne recule pas.

La vérification finale relit le périmètre SUR LA CIBLE, par la même lecture que
l'export (`extraction.ouvrir`), et exige par table le même nombre de lignes que le
manifeste et la même empreinte que les lignes écrites. L'empreinte est une somme de
hachés (indépendante de l'ordre) de la forme canonique de chaque ligne
(`json.dumps(sort_keys=True)`) — la seule comparaison qui survive aux remappages.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import psycopg

from ..crypto import _load_master_key
from .classement import CLASSEMENT, EXPORTEES, Table, sans_journal
from .decouverte import lire_schema, verifier_classement
from .extraction import FORMAT, Lecture, _colonnes, ouvrir
from .objets import ObjetsRefuses, Stockage, controler_archive
from .objets import verser as verser_objets
from .rechiffrement import AAD, empreinte_cle, lisible
from .transformation import Transformation

_MODULE = 2 ** 256
TAILLE_LOT = 500


class ImportRefuse(RuntimeError):
    """L'import ne part pas : la cible ou le fichier ne sont pas ce qu'ils doivent être."""


class VerificationEchouee(ImportRefuse):
    """Relue, la cible ne porte pas ce que l'import y a écrit : tout est annulé."""


def lire_manifeste(chemin: Path, format_attendu: str = FORMAT) -> dict:
    derniere = None
    with chemin.open(encoding="utf-8") as f:
        for derniere in f:
            pass
    if derniere is None:
        raise ImportRefuse(f"{chemin} est vide")
    manifeste = json.loads(derniere).get("manifeste")
    if not manifeste or manifeste.get("format") != format_attendu:
        raise ImportRefuse(f"{chemin} n'est pas un export au format {format_attendu} "
                           f"(il se dit {manifeste and manifeste.get('format')!r})")
    return manifeste


def classement_du(manifeste: dict) -> dict[str, Table]:
    """Le classement sous lequel l'export a été lu : la relecture lit le même."""
    return CLASSEMENT if manifeste["journal"]["inclus"] else sans_journal(CLASSEMENT)


def _lignes_du_fichier(chemin: Path):
    with chemin.open(encoding="utf-8") as f:
        for texte in f:
            if texte.startswith('{"manifeste"'):
                return
            yield texte


def controler_fichier(chemin: Path, manifeste: dict) -> None:
    """Empreinte et comptes du fichier = ceux du manifeste, avant toute écriture."""
    empreinte, comptes = hashlib.sha256(), {}
    for texte in _lignes_du_fichier(chemin):
        empreinte.update(texte.encode())
        t = json.loads(texte)["t"]
        comptes[t] = comptes.get(t, 0) + 1
    if empreinte.hexdigest() != manifeste["empreinte"]:
        raise ImportRefuse("l'empreinte du fichier n'est pas celle de son manifeste : "
                           "fichier tronqué ou modifié")
    attendus = {t: v["lignes"] for t, v in manifeste["tables"].items() if v.get("lignes")}
    if comptes != attendus:
        raise ImportRefuse(f"comptes du fichier {comptes} ≠ manifeste {attendus}")


def _canonique(ligne: dict) -> int:
    texte = json.dumps(ligne, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return int.from_bytes(hashlib.sha256(texte.encode()).digest(), "big")


def importer(conn: psycopg.Connection, chemin: Path | str, *,
             stockage: Stockage | None = None, base_publique: str | None = None) -> dict:
    """Verse l'export `chemin` dans la base de `conn` (à `dict_row`, hors transaction)
    et rend, par table, le nombre de lignes et l'empreinte relue sur la cible.

    S'il désigne des objets : `stockage` est le stockage objet de CETTE instance (ses
    identifiants), où l'archive se verse, et `base_publique` la base publique qu'elle
    déclare (`media_store.public_base`), vers laquelle les URL sont réécrites."""
    chemin = Path(chemin)
    manifeste = lire_manifeste(chemin)
    controler_fichier(chemin, manifeste)
    archive, bases = preparer_objets(chemin, manifeste, stockage, base_publique)
    cle = cle_de_l_instance(manifeste)
    with conn.transaction():
        schema = _controler_cible(conn, manifeste)
        # L'import REPRODUIT un état, il ne rejoue pas des gestes : les déclencheurs de
        # la cible (journal des révisions d'une ligne, vecteur de recherche) écriraient
        # une seconde fois ce que le fichier apporte déjà. Suspendus le temps de la
        # transaction — `USER` seulement : les clés étrangères restent vérifiées.
        tables = ["tenants", *manifeste["ordre"]]
        for t in tables:
            conn.execute(f"ALTER TABLE {t} DISABLE TRIGGER USER")
        attendu = _verser(conn, chemin, manifeste, schema, cle, bases)
        for t in tables:
            conn.execute(f"ALTER TABLE {t} ENABLE TRIGGER USER")
        recaler_sequences(conn, manifeste)
        relu = relire(conn, ouvrir(conn, manifeste["perimetre"]["orgs_declarees"],
                                   classement_du(manifeste)), bases)
        comparer(attendu, relu)
        verser_archive(archive, manifeste, stockage, cle)
    return {t: {"lignes": n, "empreinte": f"{h:064x}"} for t, (n, h) in relu.items()}


def preparer_objets(chemin: Path, manifeste: dict, stockage, base_publique):
    """L'archive des objets, vérifiée avant toute écriture, et les bases (source, cible)
    des URL à réécrire — `(None, None)` si le fichier ne désigne aucun objet."""
    objets = manifeste["objets"]
    if not objets["liste"]:
        return None, None
    if stockage is None or not base_publique:
        raise ImportRefuse(f"le fichier désigne {len(objets['liste'])} objet(s) : il "
                           "faut le stockage objet de cette instance et sa base publique")
    archive = chemin.with_name(objets["archive"])
    try:
        controler_archive(archive, objets["empreinte"])
    except ObjetsRefuses as e:
        raise ImportRefuse(str(e)) from e
    return archive, (objets["base_publique"], base_publique)


def comparer(attendu: dict, relu: dict) -> None:
    if relu != attendu:
        ecarts = sorted(t for t in set(attendu) | set(relu) if attendu.get(t) != relu.get(t))
        raise VerificationEchouee(f"la cible relue diffère de ce qui a été écrit : {ecarts}")


def verser_archive(archive: Path | None, manifeste: dict, stockage, cle) -> None:
    """Dans la transaction, APRÈS la relecture : un objet qui ne se verse pas annule tout ;
    ceux déjà versés sont sautés au prochain essai."""
    if archive is None:
        return
    try:
        verser_objets(archive, manifeste["objets"]["liste"], stockage, cle)
    except ObjetsRefuses as e:
        raise ImportRefuse(str(e)) from e


def cle_de_l_instance(manifeste: dict) -> bytes | None:
    """La clé de CETTE instance, si le fichier porte des secrets ou des objets — et c'est
    la leur."""
    if not manifeste["secrets"] and not manifeste["objets"]["liste"]:
        return None
    cle = _load_master_key()
    if cle is None:
        raise ImportRefuse(f"le fichier porte des secrets {manifeste['secrets']} ou des "
                           "objets, et cette instance n'a pas de clé maîtresse "
                           "(OTO_MCP_MASTER_KEY)")
    if empreinte_cle(cle) != manifeste["cle_cible"]:
        raise ImportRefuse("les secrets et objets du fichier sont chiffrés sous une autre "
                           "clé que "
                           "celle de cette instance (empreinte "
                           f"{manifeste['cle_cible'][:12]}… ≠ {empreinte_cle(cle)[:12]}…) : "
                           "refaire l'export avec la clé de CETTE instance")
    return cle


def _controler_cible(conn, manifeste: dict):
    schema = controler_schema(conn, manifeste)
    controler_vierge(conn, schema, manifeste)
    controler_tenant(conn, manifeste)
    return schema


def controler_vierge(conn, schema, manifeste: dict) -> None:
    """Chaque table que l'import écrit est vide sur la cible, hors les lignes que la
    naissance de l'instance y sème (`Table.naissance`) — sinon refus, avant la première
    écriture, qui nomme chaque table et son nombre de lignes.

    Les tables sont celles du classement sous lequel l'export a été lu
    (`classement_du`) : sans journal, le journal n'en est pas, et les tranches poussées
    la veille ne comptent pas. Sans ce contrôle, une cible où l'app a déjà démarré
    passait le contrôle de schéma et tombait tard sur une violation d'unicité
    (`nodes_pkey` : les guides plateforme semés au démarrage portent les identifiants
    que l'import préserve). Aucune option ne le contourne."""
    deja = lignes_deja_la(conn, verifier_classement(schema, classement_du(manifeste)))
    if deja:
        raise ImportRefuse(
            "la base cible n'est pas vierge : des tables que l'import écrit portent déjà "
            "des lignes — " + ", ".join(f"{t} ({n})" for t, n in deja.items())
            + ". L'import vise une base NEUVE, née par `init_db` et jamais démarrée : "
            "repartir d'une base neuve, ou importer AVANT le premier démarrage de l'app")


def lignes_deja_la(conn, classement: dict[str, Table]) -> dict[str, int]:
    """Par table exportée du `classement` (indexé par table physique), le nombre de
    lignes que la cible porte hors celles de sa naissance ; les tables vides n'y sont pas."""
    deja = {}
    for t, entree in sorted(classement.items()):
        if entree.classe not in EXPORTEES:
            continue
        sql, params = f"SELECT count(*) AS n FROM {t}", ()
        if entree.naissance:
            sql += f" WHERE {entree.naissance[0]}::text IS DISTINCT FROM %s"
            params = (entree.naissance[1],)
        n = conn.execute(sql, params).fetchone()["n"]
        if n:
            deja[t] = n
    return deja


def controler_schema(conn, manifeste: dict):
    """Le schéma de la cible est celui de la source : classement, version, colonnes."""
    schema = lire_schema(conn)
    verifier_classement(schema, CLASSEMENT)
    version = [r["version_num"] for r in conn.execute("SELECT version_num FROM alembic_version")]
    if version != manifeste["version_schema"]:
        raise ImportRefuse(f"version de schéma cible {version} ≠ source "
                           f"{manifeste['version_schema']} : les deux instances doivent "
                           "servir le même tronc")
    ecarts = _ecarts_de_colonnes(schema, manifeste)
    if ecarts:
        raise ImportRefuse("colonnes différentes de la source : " + " ; ".join(ecarts))
    return schema


def controler_tenant(conn, manifeste: dict) -> None:
    """Le tenant primaire de la cible est le tenant exporté : même slug, même nom."""
    primaire = conn.execute("SELECT slug, name FROM tenants WHERE id = 1").fetchone()
    exporte = manifeste["tenant"]
    if primaire is None or primaire["slug"] != exporte["slug"]:
        raise ImportRefuse(f"la cible déclare le tenant primaire "
                           f"{primaire and primaire['slug']!r}, l'export est celui de "
                           f"{exporte['slug']!r} (OTO_TENANT_PRIMAIRE_SLUG)")
    if primaire["name"] != exporte["nom"]:
        raise ImportRefuse(f"le tenant primaire de la cible s'appelle {primaire['name']!r} "
                           f"(OTO_BRAND_NAME), le tenant exporté {exporte['nom']!r} : "
                           "l'instance déclare son nom, l'import ne l'écrase pas")


def _ecarts_de_colonnes(schema, manifeste: dict) -> list[str]:
    """Par table exportée, les colonnes présentes d'un seul côté, nommées.

    Comparées par NOM, pas par position : une base servie depuis longtemps porte en fin
    de table les colonnes venues par `ALTER TABLE … ADD COLUMN`, une base née par
    `init_db` les a à leur place de création. Rien ne dépend de l'ordre : l'écriture
    passe par `json_populate_record`, qui associe par nom, et l'empreinte de relecture
    trie les clés (`_canonique`)."""
    ecarts = []
    for t in manifeste["ordre"]:
        source, cible = set(manifeste["colonnes"][t]), set(_colonnes(schema, t))
        cotes = [f"{cote} : {', '.join(sorted(seules))}"
                 for cote, seules in (("source seule", source - cible),
                                      ("cible seule", cible - source)) if seules]
        if cotes:
            ecarts.append(f"{t} ({' ; '.join(cotes)})")
    return ecarts


def lignes_cibles(chemin: Path, manifeste: dict, schema, cle, bases, attendu: dict):
    """Chaque ligne du fichier telle que la cible l'écrit (`Transformation`), son secret
    vérifié lisible sous la clé de l'instance, comptée et hachée dans `attendu`."""
    transformation = Transformation.depuis(schema, manifeste["comptes"], bases)
    for texte in _lignes_du_fichier(chemin):
        brut = json.loads(texte)
        t = brut["t"]
        ligne = transformation.appliquer(t, brut["l"])
        if t in AAD and not lisible(t, ligne, cle):
            raise ImportRefuse(f"{t} : un secret ne se déchiffre pas sous la clé de cette "
                               "instance et l'AAD de sa ligne")
        n, h = attendu.get(t, (0, 0))
        attendu[t] = (n + 1, (h + _canonique(ligne)) % _MODULE)
        yield t, ligne


def par_lots(lignes):
    """`(table, ligne)` regroupées en lots d'une même table, `TAILLE_LOT` au plus."""
    lot: list[dict] = []
    table = None
    for t, ligne in lignes:
        if lot and (t != table or len(lot) >= TAILLE_LOT):
            yield table, lot
            lot = []
        table = t
        lot.append(ligne)
    if lot:
        yield table, lot


def _verser(conn, chemin: Path, manifeste: dict, schema, cle,
            bases) -> dict[str, tuple[int, int]]:
    auto = {t: [k.colonnes[0] for k in schema.cles_de(t) if k.cible == t]
            for t in manifeste["ordre"]}
    attendu: dict[str, tuple[int, int]] = {}
    differes: list[tuple[str, dict]] = []

    def a_inserer():
        for t, ligne in lignes_cibles(chemin, manifeste, schema, cle, bases, attendu):
            if t == "tenants":
                _ecrire_tenant_primaire(conn, ligne)
                continue
            if any(ligne.get(c) is not None for c in auto[t]):
                differes.append((t, {c: ligne[c] for c in auto[t]} | _cle(schema, t, ligne)))
                ligne = {**ligne, **{c: None for c in auto[t]}}
            yield t, ligne

    for t, lot in par_lots(a_inserer()):
        _inserer(conn, t, [json.dumps(x) for x in lot])
    # Les auto-références (une page sous une page) se posent quand toutes les lignes
    # de la table sont là : leur ordre d'insertion n'a pas à connaître l'arbre.
    for t, v in differes:
        cle_ligne = _cle(schema, t, v)
        poses = [c for c in v if c not in cle_ligne]
        conn.execute(f"UPDATE {t} SET " + ", ".join(f"{c} = %({c})s" for c in poses)
                     + " WHERE " + " AND ".join(f"{c} = %({c})s" for c in cle_ligne), v)
    return attendu


def _inserer(conn, t: str, lot: list[str]) -> None:
    with conn.cursor() as cur:
        cur.executemany(f"INSERT INTO {t} SELECT * FROM json_populate_record(NULL::{t}, "
                        "%s::json)", [(x,) for x in lot])


def _cle(schema, t: str, ligne: dict) -> dict:
    return {c: ligne[c] for c in schema.primaires[t]}


def _ecrire_tenant_primaire(conn, ligne: dict) -> None:
    """La ligne 1 semée prend les valeurs du tenant exporté (slug et nom déjà égaux)."""
    cols = [c for c in ligne if c != "id"]
    conn.execute(f"UPDATE tenants SET ({', '.join(cols)}) = (SELECT {', '.join(cols)} "
                 "FROM json_populate_record(NULL::tenants, %s::json)) WHERE id = 1",
                 (json.dumps(ligne),))


def recaler_sequences(conn, manifeste: dict) -> None:
    for cle, maximum in manifeste["sequences"].items():
        avancer_sequence(conn, *cle.split("."), maximum)


def avancer_sequence(conn, t: str, c: str, maximum: int) -> None:
    """Porte la séquence de `t.c` au-delà de `maximum` et des lignes de la table, et ne
    la fait JAMAIS reculer : celle du journal a pu être avancée par une tranche poussée
    avant l'import principal, et la cible servie écrit ses propres appels."""
    conn.execute(f"SELECT setval(s.seq, v.m) FROM "
                 f"(SELECT pg_get_serial_sequence('{t}', '{c}')::regclass AS seq) s, "
                 f"LATERAL (SELECT GREATEST(%s::bigint, (SELECT max({c}) FROM {t})) AS m) v "
                 "WHERE v.m > COALESCE(pg_sequence_last_value(s.seq), 0)", (maximum,))


def relire(conn, lu: Lecture, bases) -> dict[str, tuple[int, int]]:
    """Relit, sur la cible, ce que la lecture `lu` désigne ; refuse s'il y subsiste une
    URL de NOTRE stockage public — la réécriture ne doit rien avoir laissé derrière elle."""
    relu: dict[str, tuple[int, int]] = {}
    restes: dict[str, int] = {}
    for t in lu.ordre:
        for texte in lu.lignes(conn, t):
            n, h = relu.get(t, (0, 0))
            relu[t] = (n + 1, (h + _canonique(json.loads(texte))) % _MODULE)
            if bases and f"{bases[0]}/" in texte:
                restes[t] = restes.get(t, 0) + 1
    if restes:
        raise VerificationEchouee(f"des URL de l'ancien stockage public subsistent : {restes}")
    return relu
