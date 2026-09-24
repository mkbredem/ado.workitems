#!/usr/bin/env bash
# Start the mock Azure DevOps server, run the smoke and check-mode playbooks, stop the server.
set -euo pipefail
here="$(cd "$(dirname "$0")" && pwd)"
port="${MOCK_ADO_PORT:-8765}"
mkdir -p "$here/output"
python3 "$here/mock/mock_ado_server.py" --port "$port" > "$here/output/mock.log" 2>&1 &
mock_pid=$!
trap 'kill $mock_pid 2>/dev/null || true' EXIT
sleep 1
export ADO_PAT=mock-pat
export ANSIBLE_COLLECTIONS_PATH="$(cd "$here/../../../.." && pwd)"
url="http://127.0.0.1:$port/mockorg"
ansible-playbook "$here/playbooks/smoke.yml" -e "auth_organization_url=$url" "$@"
ansible-playbook "$here/playbooks/check_mode.yml" --check -e "auth_organization_url=$url" "$@"
ADO_PAT= AZURE_TENANT=mocktenant AZURE_CLIENT_ID=mock-client AZURE_SECRET=mock-secret AZURE_SUBSCRIPTION_ID=00000000-0000-0000-0000-000000000000 \
  ansible-playbook "$here/playbooks/service_principal.yml" \
  -e "auth_organization_url=$url" -e "auth_authority_host=http://127.0.0.1:$port" "$@"
echo "incident log line" > "$here/output/incident.log"
for pass in first second; do
  ansible-playbook "$here/../playbooks/incident_lifecycle.yml" -e "auth_organization_url=$url" \
    -e ado_workitems_project=Ops -e incident_lifecycle_reference=INC0012345 \
    -e incident_lifecycle_host=web01 -e "incident_lifecycle_log=$here/output/incident.log" "$@" \
    | tee "$here/output/incident_$pass.log" | tail -3
done
grep -q "changed=0" "$here/output/incident_second.log" && echo "incident_lifecycle second run: no changes"

# Ticket playbooks (playbooks/tickets). Organization-level vars go in one file.
t="$here/../playbooks/tickets"
common=(-e "auth_organization_url=$url" -e ado_workitems_project=Tickets)
run() { ansible-playbook "$@" "${common[@]}" | tee -a "$here/output/tickets.log" | grep -E "^localhost" ; }
run "$t/create_ticket.yml" -e ticket_type=Bug -e '{"ticket_title": "Disk full on db01"}' -e ticket_priority=1 \
  -e ticket_tags="aap, storage" -e ticket_reference=INC0099
run "$t/create_ticket.yml" -e ticket_type=Bug -e '{"ticket_title": "Disk full on db01"}' -e ticket_priority=1 \
  -e ticket_tags="aap, storage" -e ticket_reference=INC0099
tid=$(python3 -c "import json,urllib.request,base64;r=urllib.request.Request('$url/Tickets/_apis/wit/wiql?api-version=7.1',data=json.dumps({'query':\"SELECT [System.Id] FROM WorkItems WHERE [System.Tags] CONTAINS 'INC0099'\"}).encode(),headers={'Authorization':'Basic '+base64.b64encode(b':mock-pat').decode(),'Content-Type':'application/json'});print(json.load(urllib.request.urlopen(r))['workItems'][0]['id'])")
run "$t/update_ticket.yml" -e ticket_id=$tid -e ticket_priority=2 -e '{"ticket_note": "Lowered priority"}'
run "$t/comment_ticket.yml" -e ticket_id=$tid -e '{"ticket_comment": "Cleared /var/log"}'
run "$t/assign_ticket.yml" -e ticket_id=$tid -e ticket_assigned_to=dba@example.com
run "$t/transition_ticket.yml" -e ticket_id=$tid -e ticket_state=Resolved --check
run "$t/transition_ticket.yml" -e ticket_id=$tid -e ticket_state=Resolved
run "$t/create_ticket.yml" -e ticket_type=Task -e '{"ticket_title": "Grow db01 volume"}' -e ticket_reference=TASK0001
run "$t/link_tickets.yml" -e ticket_id=$((tid+1)) -e ticket_link_type=parent -e ticket_target_id=$tid
run "$t/link_tickets.yml" -e ticket_id=$((tid+1)) -e ticket_link_type=parent -e ticket_target_id=$tid
run "$t/query_tickets.yml" -e ticket_filter_tag=aap
run "$t/open_ticket_on_failure.yml" -e awx_workflow_job_id=4242 -e '{"awx_workflow_job_name": "Patch web tier"}' -e awx_job_id=4250
run "$t/open_ticket_on_failure.yml" -e awx_workflow_job_id=4242 -e '{"awx_workflow_job_name": "Patch web tier"}' -e awx_job_id=4251
run "$t/delete_ticket.yml" -e ticket_id=$tid -e ticket_confirm_id=$((tid+5)) || echo "delete refused as expected"
run "$t/delete_ticket.yml" -e ticket_id=$tid -e ticket_confirm_id=$tid
