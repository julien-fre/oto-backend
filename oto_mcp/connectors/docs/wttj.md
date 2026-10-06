## prerequisite — your welcome to the jungle api token

you need an **api token** for the welcome to the jungle ats (ex-welcome kit).
- it cannot be generated in the interface: request it from wttj via [help.welcometothejungle.com](https://help.welcometothejungle.com/) describing your use
- ask for the scopes `me_r`, `organizations_r`, `jobs_r`, `candidates_rw` (or `candidates_r` for read-only), `comments_w`, `moves_r`
- paste it into your [connector keys](https://manage.oto.cx/) (or let your org share its own)
- vendor docs: [developers.welcomekit.co](https://developers.welcomekit.co)

## usage — what you can do

drive your wttj ats: everything starts from an **organization**, a job offer is a **job**, its **stages** are read on the job, a **candidate** belongs to a job.
- "which organizations do I see?" → `wttj_organization` (gives the `organization_reference`s)
- "which jobs are published?" → `wttj_job(op="list", status="published")`; a job's stages → `wttj_job(op="get")`
- "who is at the interview stage for this role?" → `wttj_candidate(op="list")` with `job_reference` and `job_stage_id`; detail → `wttj_candidate(op="get")`
- "add this candidate" → `wttj_candidate(op="create")`; "move them to the next stage" → `wttj_candidate(op="update", job_stage_id=…)`; archive → `archived=true`
- "log yesterday's conversation" → `wttj_comment` (write-only: the api does not read comments back)
- "what moved this week?" → `wttj_moves`
