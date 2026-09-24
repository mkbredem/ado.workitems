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
