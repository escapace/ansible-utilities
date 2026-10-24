# escapace.utilities.github_download

Downloads a SHA-256-verified artifact to a file on the managed POSIX host.
The role does not resolve versions or checksums, extract files, create parent
directories, or manage services.

## Variables

- `github_download_url` is a required URL string. It accepts HTTPS endpoints
  and HTTP loopback URLs for local tests. Embedded credentials are invalid;
  the role ignores ambient `.netrc` credentials.
- `github_download_dest` is a required absolute managed-host file path. Its
  parent directory must exist. An existing destination must be a regular file;
  the role refuses directories, symbolic links, and other special files.
- `github_download_checksum` is required and accepts
  `sha256:<64 hexadecimal characters>`. Verification cannot be disabled.
- `github_download_owner` defaults to `root`; it accepts an owner name or
  numeric UID string.
- `github_download_group` defaults to `root`; it accepts a group name or
  numeric GID string.
- `github_download_mode` defaults to `"0644"`; it accepts a quoted four-digit
  octal mode beginning with `0`.

The caller supplies an authoritative checksum for the exact artifact and
privileges sufficient to write the destination and set ownership. The host
needs HTTPS access and trusted CA certificates.

The role allows four attempts and at most 90 seconds of cumulative retry
waiting per invocation, with a 30-second socket timeout. It retries only
recognized transient HTTP errors. Rate-limit responses, checksum failures,
and unclassified transport or TLS errors stop immediately because `get_url`
does not expose the headers needed for rate-limit-aware retries.

Use `include_role` with `allow_duplicates: true` for each operation. Each valid
invocation resets retry state. A successful call replaces the host fact
`github_download_result` with the registered `get_url` response. Save that fact
before another download if needed; `register` on `include_role` does not return
the child task's response. Interleaving metadata requests and downloads does
not overwrite the other helper's result.

```yaml
- name: download a verified artifact
  ansible.builtin.include_role:
    name: escapace.utilities.github_download
    allow_duplicates: true
    public: false
  vars:
    github_download_url: '{{ artifact_url }}'
    github_download_dest: /tmp/artifact.tar.gz
    github_download_checksum: '{{ artifact_checksum }}'
```

Check mode follows `get_url` behavior: it makes HEAD requests, performs bounded
retry waits, and writes no artifact. It cannot verify the archive or reliably
predict whether the destination would change.
