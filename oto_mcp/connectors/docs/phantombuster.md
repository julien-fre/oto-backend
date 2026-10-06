## prerequisite — your phantombuster api key

- from [phantombuster.com](https://phantombuster.com), open your organization settings then the api key section
- copy your api key
- paste it into your oto connector keys under `phantombuster`

## usage — launch agents and fetch their results

trigger an agent (phantom), then follow its run and fetch its results.
- `phantombuster_get_agent` an agent's configuration and status
- `phantombuster_launch_agent` starts a run (⚠️ consumes credits and acts on third-party accounts), returns the `containerId`
- `phantombuster_list_containers` / `phantombuster_get_container` list and follow runs
- `phantombuster_container_results` fetches the json results of a finished run, `phantombuster_container_output` its logs
