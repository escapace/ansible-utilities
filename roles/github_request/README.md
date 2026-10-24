# escapace.utilities.github_request

Retrieves public GitHub metadata from the managed POSIX host. The role performs
a GET request and any retry waits on that host; it manages no services or files.
The host needs HTTPS access and trusted CA certificates.

## Variables

- `github_request_result_var` is a required string naming the caller-owned host
  fact to receive the full successful response, for example `gh_latest_release`.
  Use a lowercase identifier starting with a letter and containing a caller
  prefix separated by an underscore. Ansible and helper namespaces are reserved.
- `github_request_url` is a required URL string. It accepts HTTPS endpoints and
  HTTP loopback URLs for local tests. Embedded credentials are invalid;
  the role ignores ambient `.netrc` credentials.
- `github_request_return_content` is a boolean that defaults to `false`. Set it
  to `true` to retain raw content, such as a checksum manifest. JSON responses
  also appear in the structured `json` result.

The role allows four attempts and at most 90 seconds of cumulative retry
waiting per invocation. It uses a 30-second socket timeout, not a total-operation
deadline. Retry waits honor GitHub's rate-limit instructions; waits exceeding
the remaining budget fail rather than retry early. Permanent HTTP errors and
unclassified transport or TLS errors fail immediately.

Use `include_role` with `allow_duplicates: true` for each operation. Each valid
invocation resets collection-prefixed internal retry state. Only a successful
call writes the registered `uri` response to `github_request_result_var`.
Choose different caller-prefixed names to retain independent responses. Reusing
a name deliberately replaces its value on success; a failed call leaves its
previous value unchanged. `register` on `include_role` does not return the child
task's response.

```yaml
- name: retrieve release metadata
  ansible.builtin.include_role:
    name: escapace.utilities.github_request
    allow_duplicates: true
    public: false
  vars:
    github_request_url: https://api.github.com/repos/cli/cli/releases/latest
    github_request_result_var: example_release
```

Check mode still performs the read-only GET request and per-host retry waits.
`public: false` hides role defaults and variables, not the chosen result fact.
Results and internal state remain host-scoped; namespacing is not private storage
or support for overlapping calls on the same host. Public GitHub API limits apply
per originating IP.
