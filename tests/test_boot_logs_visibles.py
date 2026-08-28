"""Les lignes `boot:` doivent ATTERRIR quelque part — vécu le 2026-08-28.

Le lot 0 de l'ADR 0065 a instrumenté chaque étape du démarrage (`boot: <étape> <n> ms`)
parce que l'ADR demande de décider sur mesure. Déployé, il n'en a produit **aucune** :
`logging.basicConfig` vivait dans `server.main`, or le premier tiers du démarrage se
passe **à l'import** d'`oto_mcp.server` (l'instance anonyme est construite au niveau
module, et prépare la base). Un `logger.info` émis avant tout handler est jeté — le
`lastResort` de la stdlib n'émet qu'à partir de WARNING.

**Une instrumentation qui ne journalise pas est pire que pas d'instrumentation** : on la
croit posée, et on mesure en croyant avoir mesuré. Le journal de la box a montré zéro
ligne `boot:` là où le code en émettait une par étape.

⚠️ Ce test tourne en **sous-processus**, et c'est nécessaire : `caplog` et `capsys`
installent eux-mêmes un handler sur le root logger, donc reproduisent l'inverse du bug.
Le seul instrument fidèle est un interpréteur nu.
"""
from __future__ import annotations

import subprocess
import sys
import textwrap


def _python(code: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, "-c", textwrap.dedent(code)],
                          capture_output=True, text=True, timeout=60)


def test_un_info_sans_configuration_est_bien_PERDU():
    """La cause, reproduite — sinon le test suivant ne prouverait rien.

    Si un jour la stdlib se mettait à émettre les INFO non configurés, ce test
    tomberait et dirait que le garde-fou d'à côté n'a plus d'objet."""
    r = _python("""
        import logging
        log = logging.getLogger("oto_mcp")
        log.info("boot: init_db 930 ms")
        log.warning("un warning, lui, passe par lastResort")
    """)
    assert "boot: init_db" not in r.stderr, (
        "un INFO non configuré est arrivé quelque part — la prémisse de ce fichier "
        "n'est plus vraie, relire le garde-fou d'à côté")
    assert "lastResort" in r.stderr


def test_le_journal_est_configure_avant_que_le_demarrage_ne_parle():
    """Le garde-fou : après `_configurer_le_journal`, un INFO du démarrage atterrit.

    On n'importe PAS `oto_mcp.server` ici (il construirait une instance MCP complète et
    voudrait une base) : ce qu'on vérifie est la propriété qui manquait — que les
    handlers soient posés AVANT, quelle que soit la suite."""
    r = _python("""
        from oto_mcp.cli import _configurer_le_journal
        _configurer_le_journal()
        import logging
        logging.getLogger("oto_mcp").info("boot: init_db 930 ms")
    """)
    assert "boot: init_db 930 ms" in r.stderr, (
        f"la ligne de démarrage s'est perdue — stderr : {r.stderr!r}")


def test_la_configuration_precede_l_import_du_serveur():
    """L'ORDRE, pas seulement la présence.

    Poser les handlers après l'import laisserait passer les deux tests précédents et
    reproduirait exactement le bug de production. On remplace donc `oto_mcp.server`
    par un module espion qui note, à SON import, si le root logger avait déjà un
    handler — c'est le moment précis où `_prepare_database` journalise."""
    r = _python("""
        import sys, types, logging

        # PEP 562 : le `__getattr__` d'un module s'exécute au moment où
        # `from .server import main` résout le nom — c'est-à-dire À L'IMPORT, l'instant
        # exact où le vrai module construit son instance et journalise ses durées.
        vu = {}

        def _getattr(nom):
            vu.setdefault("handlers", bool(logging.getLogger().handlers))
            if nom == "main":
                return lambda: print("CONFIGURE" if vu["handlers"] else "MUET")
            raise AttributeError(nom)

        espion = types.ModuleType("oto_mcp.server")
        espion.__getattr__ = _getattr
        import oto_mcp
        sys.modules["oto_mcp.server"] = espion
        oto_mcp.server = espion

        sys.argv = ["oto-mcp"]
        from oto_mcp.cli import main
        main()
    """)
    assert "CONFIGURE" in r.stdout, (
        "au moment où `oto_mcp.server` est importé — donc où le démarrage prépare la "
        "base et journalise ses durées — le root logger n'avait aucun handler. C'est "
        f"le bug du 2026-08-28. stdout={r.stdout!r} stderr={r.stderr[-400:]!r}")
