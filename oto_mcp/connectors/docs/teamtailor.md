## prerequisite — your teamtailor api key

you need a teamtailor **api** key.
- in teamtailor, go to **settings → integrations → API keys** and generate a key
- paste it into your [connector keys](https://manage.oto.cx/) (or let your org share its own)
- vendor docs: [teamtailor.com](https://www.teamtailor.com)

## usage — what you can do

drive your teamtailor ats: candidates, jobs, applications.
- "list the candidates" → `teamtailor_candidates` (`email` filter), one candidate's detail → `teamtailor_candidate`
- "create a candidate john doe" → `teamtailor_create_candidate` (attributes `first-name`, `last-name`, `email`, `phone`, `pitch`, `tags`…)
- "which jobs are open?" → `teamtailor_jobs` (`status` open/draft/archived/unlisted)
- "show the applications on job 99" → `teamtailor_job_applications` (`job_id` filter)
