## prerequisite — ta clé api teamtailor

il te faut une clé **api teamtailor**.
- dans teamtailor, va dans **settings → integrations → API keys** et génère une clé
- colle-la dans tes [clés de connecteurs](https://manage.oto.cx/) (ou laisse ton org partager la sienne)
- doc éditeur : [teamtailor.com](https://www.teamtailor.com)

## usage — ce que tu peux faire

pilote ton ats teamtailor : candidats, jobs, candidatures.
- « liste les candidats » → `teamtailor_candidates` (filtre `email`), détail d'un candidat → `teamtailor_candidate`
- « crée un candidat jean dupont » → `teamtailor_create_candidate` (attributs `first-name`, `last-name`, `email`, `phone`, `pitch`, `tags`…)
- « quels jobs sont ouverts ? » → `teamtailor_jobs` (`status` open/draft/archived/unlisted)
- « montre les candidatures sur le job 99 » → `teamtailor_job_applications` (filtre `job_id`)
