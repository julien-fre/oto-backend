## prerequisite — ta clé api ashby

il te faut une clé **api ashby**.
- dans ashby, va dans **admin → integrations → API** et crée une clé
- colle-la dans tes [clés de connecteurs](https://manage.oto.cx/) (ou laisse ton org partager la sienne)
- doc éditeur : [ashbyhq.com](https://www.ashbyhq.com)

## usage — ce que tu peux faire

pilote ton ats ashby : candidats, jobs, candidatures, notes.
- « trouve le candidat dont l'email est x@y.com » → `ashby_search_candidates` (par `email` et/ou `name`)
- « liste les candidats » → `ashby_candidates`, puis le détail → `ashby_candidate`
- « ajoute une note sur ce candidat » → `ashby_add_note`
- « quels jobs sont ouverts ? » → `ashby_jobs` (`status` Open/Closed/Draft/Archived) ; les candidatures → `ashby_applications` (filtre `job_id`)
