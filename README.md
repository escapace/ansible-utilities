# escapace.utilities

Reusable Ansible filters and helper roles distributed directly from GitHub.
Filters calculate values on the controller without side effects. The GitHub
helper roles perform requests and retry waits on the managed host.

## Install

Add the collection to the consuming project's `requirements.yaml`:

```yaml
collections:
  - name: https://github.com/escapace/ansible-utilities.git
    type: git
    version: origin/trunk
```

Run `ansible-galaxy collection install -r requirements.yaml` from that project.
The installer retrieves this collection from Git, not the Galaxy service.
Reinstall to pick up changes on `trunk`; running a playbook does not update it.
The metadata version stays `0.0.0`; the selected Git commit determines the code.

## GitHub helper roles

- [`escapace.utilities.github_request`](roles/github_request/README.md) retrieves
  metadata, including JSON and checksum manifests, with rate-limit-aware retries.
- [`escapace.utilities.github_download`](roles/github_download/README.md) downloads
  an artifact using a required SHA-256 checksum and conservative HTTP retries.

Both helpers allow four attempts and 90 seconds of cumulative retry waiting per
invocation. They execute requests and waits on the managed host, not the
controller. Each invocation has fresh per-host retry state.

Invoke operations explicitly with `include_role`, `allow_duplicates: true`,
and `public: false`; do not use metadata dependencies to trigger requests.
Calls export separate host facts: `github_request_result` and
`github_download_result`. Copy a result into a caller-owned variable before
another call to the same helper. `public: false` does not hide exported facts,
and registering `include_role` does not return the underlying module response.

For loops that retain multiple results, put the helper invocation and result
capture together in a caller-owned task file and loop over `include_tasks`.

## GitHub retry filters

- `escapace.utilities.github_metadata_retry(response, policy, state)` accepts
  an `ansible.builtin.uri` result. It handles primary and secondary rate limits,
  `Retry-After` seconds or HTTP dates, and transient HTTP errors.
- `escapace.utilities.github_archive_retry(response, policy, state)` accepts
  an `ansible.builtin.get_url` result. It retries only recognized transient HTTP
  errors because that module does not expose rate-limit headers.

Both filters require a result with an explicit boolean `failed` field, as
provided by registered Ansible module results. They retry HTTP `408`, `500`,
`502`, `503`, and `504`. Metadata requests also recognize GitHub rate limits
from HTTP `429`, or HTTP `403` with rate-limit headers or a secondary-limit
message. Other HTTP failures and unclassified transport or TLS failures stop.

Each call takes these mappings:

- `policy.max_attempts`: positive integer, including the initial request.
- `policy.max_wait_seconds`: nonnegative integer cumulative sleeping budget.
- `state.attempt`: one-based integer, no greater than `max_attempts`.
- `state.waited_seconds`: nonnegative integer, no greater than the waiting
  budget. An ASCII decimal string is also accepted for older Ansible versions
  that serialize computed facts as strings.
- `state.now_epoch`: fresh, finite numeric UTC epoch timestamp.
- `state.jitter_seconds`: integer `0` or `1`, chosen by the caller.

For example, this calculates a decision without contacting GitHub:

```yaml
- name: inspect a rate-limit retry decision
  ansible.builtin.debug:
    msg: >-
      {{ {'status': 429, 'failed': true, 'retry_after': '5'}
         | escapace.utilities.github_metadata_retry(
             {'max_attempts': 4, 'max_wait_seconds': 90},
             {'attempt': 1, 'waited_seconds': 0,
              'now_epoch': now(utc=true).timestamp(), 'jitter_seconds': 0}
           ) }}
```

The result is `{"action": "retry", "delay_seconds": 5}`. Successful responses
return `{"action": "success"}`; terminal failures return
`{"action": "fail", "reason": "..."}`. Invalid input shapes, policy, or state
raise a filter error. Malformed retry headers return a failure decision.

Ordinary transient failures back off by `2 ** attempt` seconds. Metadata
decisions also honor the longest applicable server instruction. Secondary
limits without instructions start at 60 seconds and increase exponentially.
The filters add caller-supplied jitter and refuse a retry when attempts or
waiting budget are exhausted. They never shorten a server-required wait to
fit the budget. Server `Date` headers provide the reference clock for absolute
retry instructions when available.

Filters are stateless and can be called repeatedly or from multiple roles.
The caller owns per-host attempt counters, cumulative waiting, module
results, and failure handling. Reset that state for each logical request.
No role activation or global plugin-path configuration is needed.

Use `ansible-doc -t filter escapace.utilities.github_metadata_retry` or
`ansible-doc -t filter escapace.utilities.github_archive_retry` for parameter
and return documentation.

## Development

Use `.yaml` for YAML files except `galaxy.yml` and `meta/runtime.yml`, whose
exact names Ansible requires.

Run `uv sync --locked`, `uv run python -W error -m unittest discover -s tests -v`,
`uv run black --check plugins tests`, and `uv run ansible-lint --offline`.
Tests install temporary collections to verify namespaced loading, repeated
helper calls, retry decisions, HTTP response sequences, checksum verification,
input validation, and check mode without contacting GitHub.
