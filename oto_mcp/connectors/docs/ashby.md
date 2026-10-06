## prerequisite — your ashby api key

you need an **ashby api** key.
- in ashby, go to **admin → integrations → API** and create a key
- paste it into your [connector keys](https://manage.oto.cx/) (or let your org share its own)
- vendor docs: [ashbyhq.com](https://www.ashbyhq.com)

## usage — what you can do

drive your ashby ats: candidates, jobs, applications, notes.
- "find the candidate whose email is x@y.com" → `ashby_search_candidates` (by `email` and/or `name`)
- "list the candidates" → `ashby_candidates`, then the detail → `ashby_candidate`
- "add a note on this candidate" → `ashby_add_note`
- "which jobs are open?" → `ashby_jobs` (`status` Open/Closed/Draft/Archived); applications → `ashby_applications` (`job_id` filter)
