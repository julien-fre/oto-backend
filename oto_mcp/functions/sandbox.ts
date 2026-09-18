// Le bac à sable d'une fonction (ADR 0073 §D4) : Pyodide sous Deno.
//
// Lancé par `executor.py`, UN processus par exécution, avec pour seules permissions la
// LECTURE du répertoire de Pyodide et des roues de la liste blanche. Ni réseau, ni
// écriture, ni variables d'environnement, ni sous-processus : Deno les refuse lui-même,
// y compris au code Python qui passerait par le module `js`.
//
// Protocole. La tâche arrive en JSON sur l'entrée standard :
//   {pyodide, wheels: [chemins], nonce, harness, job: {sources, entrypoint, mode, input}}
// La réponse part sur la sortie standard, sur UNE ligne préfixée par `nonce`. Le code de
// la fonction peut écrire sur la sortie standard (par `js.Deno`) : c'est pourquoi le
// nonce ne vit que dans cette fermeture, hors de `globalThis`, où Python ne le lit pas.
// Une réponse sans nonce est un échec, jamais un résultat.

// Pyodide 0.26 tourne sous Deno par la compatibilité Node, que Deno n'active que pour un
// paquet `npm:`. Le paquet est résolu dans le cache npm du bac à sable, posé et épinglé
// par `scripts/installer_bac_a_sable.py` ; l'exécuteur passe `--cached-only` : rien ne
// se télécharge au moment d'exécuter.
import { loadPyodide } from "npm:pyodide@0.26.4";

const brut = await new Response(Deno.stdin.readable).text();
const tache = JSON.parse(brut);
const nonce: string = tache.nonce;
const ecrire = (objet: unknown) =>
  Deno.stdout.writeSync(new TextEncoder().encode(nonce + JSON.stringify(objet) + "\n"));

let journal = "";
const MAX_JOURNAL = 20_000;
const noter = (ligne: string) => {
  if (journal.length < MAX_JOURNAL) journal += ligne + "\n";
};

try {
  const py = await loadPyodide({
    indexURL: tache.pyodide + "/",
    stdout: noter,
    stderr: noter,
  });
  for (const roue of tache.wheels) {
    py.unpackArchive(Deno.readFileSync(roue), "wheel");
  }
  py.globals.set("_tache_json", JSON.stringify(tache.job));
  const sortie = py.runPython(tache.harness);
  ecrire({ ok: true, sortie: JSON.parse(sortie), journal });
} catch (e) {
  ecrire({ ok: false, erreur: String(e).slice(0, 4000), journal });
}
