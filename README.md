# ado.workitems

> **Illustrative example only. Not tested or verified against Azure DevOps.**
> This collection was created to illustrate how you could build roles that abstract the Azure DevOps REST API behind a single `api` role, in the way the `servicenow.itsm` collection wraps the ServiceNow API. It has not been tested or verified against a live Azure DevOps organization, Microsoft Entra ID, or an automation controller. The only testing done is against the mock server in `tests/mock/`, which imitates the Azure DevOps endpoints as documented and cannot prove real-world behavior. The collection is not a Red Hat product, is not supported by Red Hat, and is not Red Hat Certified Content. Review, adapt and test it in a non-production Azure DevOps organization before relying on it.

Azure DevOps Boards work item management for Red Hat Ansible Automation Platform, built as roles on top of one REST abstraction role. The collection gives the record-management capability of the `servicenow.itsm` collection to teams whose system of record is Azure Boards.

- Azure DevOps Services (`dev.azure.com`) only. Azure DevOps Server (on-premises) is not supported, because Entra ID tokens do not work against Azure DevOps Server.
- Requires ansible-core 2.16 or later. Baseline: containerized Ansible Automation Platform 2.7.
- No Python dependencies beyond ansible-core. Everything runs through `ansible.builtin.uri`, so the supported execution environment works as is.

## Roles

| Role | What the role does | `servicenow.itsm` equivalent |
|---|---|---|
| `ado.workitems.auth` | Resolves an Authorization header from a service principal, a managed identity or a personal access token | Connection options on every module |
| `ado.workitems.api` | Calls any Azure DevOps REST endpoint: URL building, authentication on first use, token refresh, retries for HTTP 429 and 5xx, readable errors, check mode handling | `api`, `api_info` |
| `ado.workitems.work_item` | Creates, updates or deletes one work item with an idempotent JSON Patch diff and a revision guard | `incident`, `problem`, `change_request`, `change_request_task`, `problem_task` |
| `ado.workitems.work_item_info` | Reads work items by ID list, saved query, WIQL or field filter, in batches of 200 | `incident_info`, `problem_info`, `change_request_info`, task `_info` modules |
| `ado.workitems.comment` | Adds (idempotent on text), edits, deletes and lists discussion comments | `comments` and `work_notes` options |
| `ado.workitems.attachment` | Uploads and links a file (idempotent on name and SHA-256), unlinks, downloads | `attachment`, `attachment_upload`, `attachment_info` |
| `ado.workitems.link` | Adds or removes parent, child, related, dependency, duplicate, test and hyperlink relations | Parent reference on task modules, `configuration_item_relations` for work-item-to-work-item links |
| `ado.workitems.batch` | Applies a list of work item definitions and collects the results | `configuration_item_batch` |

### Where parity stops

- **Configuration items.** Azure Boards has no configuration management database. The `configuration_item*` modules have no equivalent. Tags, area paths or a custom field can carry a configuration item reference.
- **Inventory plugin.** `servicenow.itsm.now` builds inventory from the configuration management database. There is no Azure Boards source to build inventory from, so this collection has no inventory plugin.
- **Record types.** ServiceNow has fixed tables. Azure Boards has work item types that depend on the process template. Map the ServiceNow record types to your types:

| ServiceNow record | Agile process | Scrum process | CMMI process | Basic process |
|---|---|---|---|---|
| Incident | Bug or Issue | Bug or Impediment | Bug or Issue | Issue |
| Problem | Bug | Bug | Bug | Issue |
| Change request | User Story | Product Backlog Item | Change Request | Issue |
| Change and problem tasks | Task | Task | Task | Task |

Custom work item types work the same way. Set `work_item_type` to the type name.

## Authentication

The `auth` role resolves the method automatically (`auth_method: auto`), in this order:

1. **Service principal.** Attach the native [Microsoft Azure Resource Manager credential type](https://docs.redhat.com/en/documentation/red_hat_ansible_automation_platform/2.7/secure-ref_controller_credential_azure_resource) with Client ID, Client Secret and Tenant ID. The credential injects `AZURE_CLIENT_ID`, `AZURE_SECRET` and `AZURE_TENANT`, and the role exchanges them for an Entra ID token for the Azure DevOps resource (`499b84ac-1321-427f-aa17-267ca6975798`).
2. **Personal access token.** Set in the Azure DevOps Organization custom credential type. The personal access token needs the Work Items (Read, write and manage) scope.
3. **Managed identity.** Used when neither of the above is present. The role asks the Azure Instance Metadata Service on the execution node for a token. For a user-assigned identity, set `auth_managed_identity_client_id`.

Set `auth_method` to `service_principal`, `managed_identity` or `pat` to skip detection.

### Azure DevOps Organization credential type

The Microsoft Azure Resource Manager credential carries no Azure DevOps organization, so the collection ships a custom credential type in `docs/credential_types/azure_devops_organization.yml`. The custom credential type injects `ADO_ORGANIZATION`, `ADO_PROJECT` and an optional `ADO_PAT`. Register the custom credential type with `playbooks/configure_credential_type.yml` (requires the `ansible.controller` collection) or paste the inputs and injectors into Automation Execution > Infrastructure > Credential Types.

Attach to each job template:

| Method | Credentials on the job template |
|---|---|
| Service principal | Azure DevOps Organization (no personal access token) and Microsoft Azure Resource Manager |
| Personal access token | Azure DevOps Organization with a personal access token |
| Managed identity | Azure DevOps Organization (no personal access token), on an instance group whose execution nodes run in Azure |

### Azure DevOps prerequisites for service principals and managed identities

- Add the identity to the organization under Organization settings > Users. Adding the identity to an Entra ID group alone is not enough. Without this step Azure DevOps answers with error TF401444, and the `auth` role says so.
- Give the identity an access level of Basic (or Stakeholder for work-item-only use) and membership in each project it manages.
- Managed identity works only when the execution node is an Azure virtual machine or scale set with the identity assigned, and the execution environment container can reach `169.254.169.254`. Execution nodes on Azure Kubernetes Service need workload identity federation, which this release does not implement.

### Token handling

- The Authorization header lives in the `auth_ado_headers` fact. Every task that touches the header sets `no_log`, and `set_fact` is not cacheable, so the header never reaches job output or the fact cache.
- Entra ID tokens expire. The `api` role checks the expiry before every call and gets a new token five minutes early (`api_token_refresh_margin`), so long workflow jobs do not fail halfway.

## Common variables

| Variable | Purpose |
|---|---|
| `ado_workitems_project` | Default project for every role. Falls back to `ADO_PROJECT`. |
| `auth_organization` | Organization name. Falls back to `ADO_ORGANIZATION`. |
| `auth_organization_url` | Full organization URL, for testing against the mock server. |

Every role documents its options in `meta/argument_specs.yml`, and Ansible validates the options when the role is included.

## Examples

Open or update a bug keyed on an external reference, then resolve the bug:

```yaml
- name: Track an outage in Azure Boards
  hosts: localhost
  connection: local
  gather_facts: false
  vars:
    ado_workitems_project: Platform Engineering
  tasks:
    - name: Open or update the bug
      ansible.builtin.include_role:
        name: ado.workitems.work_item
      vars:
        work_item_type: Bug
        work_item_match:
          System.Tags: {op: CONTAINS, value: INC0012345}
        work_item_title: INC0012345 - web01 unreachable
        work_item_tags: [aap, INC0012345]
        work_item_priority: 1
        work_item_assigned_to: ops@example.com

    # Copy the result before the next call: the role resets work_item_result.
    - name: Keep the bug ID
      ansible.builtin.set_fact:
        outage_bug_id: "{{ work_item_result.id }}"

    - name: Add a remediation task under the bug
      ansible.builtin.include_role:
        name: ado.workitems.work_item
      vars:
        work_item_type: Task
        work_item_match: {System.Title: Restart httpd on web01}
        work_item_title: Restart httpd on web01
        work_item_parent_id: "{{ outage_bug_id }}"

    - name: Resolve the bug with a discussion entry
      ansible.builtin.include_role:
        name: ado.workitems.work_item
      vars:
        work_item_id: "{{ outage_bug_id }}"
        work_item_workflow_state: Resolved
        work_item_history: Resolved by automation controller
```

Call an endpoint the roles do not wrap, for example the work item types of a project:

```yaml
- name: List work item types
  ansible.builtin.include_role:
    name: ado.workitems.api
  vars:
    api_path: _apis/wit/workitemtypes

- name: Show type names
  ansible.builtin.debug:
    msg: "{{ api_result.json.value | map(attribute='name') | list }}"
```

`playbooks/tickets/` holds one playbook per ticket operation (create, update, comment, assign, transition, link, query, delete, and open-on-job-failure). Each playbook starts with comments that list the automation controller setup it needs: project, credentials, job template, survey questions and workflow placement. `playbooks/tickets/README.md` walks through the shared setup once.

`playbooks/incident_lifecycle.yml` is a survey-ready job template playbook that runs the whole incident flow: open, child task, attachment, resolve.

## Behavior worth knowing

- **Idempotency.** The `work_item` role reads the current work item, compares each desired field (identity fields by email or display name, tags as a set, numbers numerically) and sends a JSON Patch only for differences. A second identical run reports no change.
- **Concurrency.** Every update starts with a `test` operation on the revision, so Azure DevOps rejects the update if someone edited the work item after the role read the work item.
- **Check mode.** Run the job template as a Check job type. Reads still run, and creates and updates go to Azure DevOps with `validateOnly=true`, so Azure DevOps validates required fields, rules and state transitions without saving. Deletes are skipped.
- **Unmanaged fields.** A field left `null` or empty is not touched. To clear a field, set the field to `null` in `work_item_fields`.
- **Results between calls.** Each role resets its result fact (`work_item_result`, `api_result` and so on) when it starts, and role variables are evaluated lazily. Copy a result into your own fact with `ansible.builtin.set_fact` before passing the result to the next role call.
- **Where roles run.** Run these roles against `localhost`. The attachment source file must be on the execution node.
- **Limits.** Simple attachment upload is limited to 130 MB. WIQL returns at most 20,000 work items. The comments endpoint is a preview API (`7.1-preview.4`).

## Where the Ansible Automation Platform adds value over ansible-core or AWX

The roles run on ansible-core. The platform adds what an Azure Boards integration needs in production: credential injection so no secret sits in a playbook or variable file, role-based access control on who can open or close work items, Check job types that preview changes through `validateOnly`, surveys for self-service record creation, workflows that open a work item on failure and close the work item on success, and Event-Driven Ansible rulebooks that react to Azure DevOps service hooks. AWX has the same automation controller features without Red Hat support, certified content or the supported execution environments.

## Testing

The collection has not been run against a live Azure DevOps organization. The tests below prove the roles are internally consistent and idempotent against a mock server; they do not prove the Azure DevOps or Entra ID behavior the mock server imitates.

`tests/mock/mock_ado_server.py` emulates the Azure DevOps and Entra ID endpoints the roles call. `tests/run_mock_tests.sh` starts the mock server and runs:

- `tests/playbooks/smoke.yml`: create, re-run with no change, transition, child task, comment, attachment upload and download, links, batch, query, delete.
- `tests/playbooks/check_mode.yml` with `--check`: confirms nothing is saved.
- `tests/playbooks/service_principal.yml`: Entra ID client credentials with the Microsoft Azure Resource Manager environment variables, and token refresh.
- `playbooks/incident_lifecycle.yml` twice: the second run reports `changed=0`.
- Every playbook in `playbooks/tickets/`, including reruns that must report no change, a check mode transition, and a delete that is refused when the confirmation ID does not match.

Filter unit tests: `python3 -m pytest tests/unit`.

The mock server is also useful for demonstrations without a live Azure DevOps organization.
