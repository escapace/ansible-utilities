# Changelog

## Unreleased

- Require caller-owned result names through `github_request_result_var` and
  `github_download_result_var` instead of fixed result facts (breaking change).
- Prefix all helper state with `_escapace_utilities_<role>_`; validate result
  namespaces and publish results only after success.

- Add repeatable `github_request` and `github_download` helper roles with shared
  retry limits, validated inputs, per-host state, and documented result facts.
- Move HTTP integration coverage from `ansible-gh` and add download, checksum,
  repeated-call, input-validation, and check-mode coverage.

- Extract GitHub metadata and archive retry filters from `ansible-gh` into
  `escapace.utilities`.
- Add unit tests and installed-collection checks for Ansible Core 2.14 and 2.21.
