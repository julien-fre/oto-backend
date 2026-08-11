## prerequisite — ta clé api spott

il te faut une **clé api spott**.
- dans spott, va dans **settings → api keys** et génère une clé
- colle-la dans tes [clés de connecteurs](https://manage.oto.cx/) (ou laisse ton org partager la sienne)
- doc éditeur : [api-docs.spott.io](https://api-docs.spott.io)

## usage — ce que tu peux faire

pilote spott, l'ats **et** le crm d'un cabinet de recrutement : le candidat d'un côté, l'entreprise cliente de l'autre. un poste = un **job** (`vacancy` dans les urls de l'api), un candidat sur un poste = une **application** qui avance d'**étape** en étape.
- « est-ce qu'on connaît déjà jean dupont ? » → `spott_people` (cherche candidats **et** contacts clients, flou) — à faire avant de créer quoi que ce soit
- « liste les candidats » → `spott_candidate(op="list")`, détail → `spott_candidate` ; par critères → `spott_candidate(op="search")`
- « crée un candidat » → `spott_candidate(op="create")` (`firstName`/`lastName` obligatoires), correction → `spott_candidate(op="update")`
- « quels postes sont ouverts ? » → `spott_job(op="search")` avec le filtre `vacancy.stage.isOpen` ; la liste brute → `spott_job(op="list")`, détail → `spott_job`
- « où en sont les candidatures du poste X ? » → `spott_application(op="list")` (`job_id`, ou `candidate_id` pour l'inverse)
- « fais postuler ce candidat » → `spott_stages` (récupérer l'id d'étape) puis `spott_application(op="create")` ; « passe-le en entretien » → `spott_application(op="move")`
- « note l'appel d'hier » → `spott_note(op="create")` (`links` vers le candidat, `source` phone/inPerson…), relire → `spott_note(op="list")`
- côté crm : `spott_client(op="list")` (liste, ou recherche si tu passes `filters`), `spott_client`, `spott_client(op="contacts")`, et `spott_placements` pour les placements conclus et leurs honoraires
