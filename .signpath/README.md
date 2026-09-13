# SignPath / Authenticode preparation

This integration prepares signing for official `v*` tag releases. No actual signing or external SignPath configuration has been performed for the local candidate. SignPath Foundation onboarding and project approval are still necessary.

## External parameters

Configure these GitHub Actions settings after onboarding:

| GitHub type | Name | Purpose |
| --- | --- | --- |
| Actions secret | `SIGNPATH_API_TOKEN` | SignPath API token with submitter permission for the chosen project/signing policy. |
| Actions variable | `SIGNPATH_ORGANIZATION_ID` | SignPath organization ID. |
| Actions variable | `SIGNPATH_PROJECT_SLUG` | SignPath project slug. |
| Actions variable | `SIGNPATH_SIGNING_POLICY_SLUG` | Signing policy slug. |
| Actions variable | `SIGNPATH_ARTIFACT_CONFIGURATION_SLUG` | ZIP/executable artifact configuration slug. |
| Actions variable | `SIGNPATH_REQUIRED` | Exactly `true` or `false`; unset defaults to `false`. |

`SIGNPATH_REQUIRED` applies to official tags, not branch diagnostics. Manual dispatch on `release-hardening-v1.0.2` remains unsigned and uses no SignPath secrets even after onboarding. Secret references occur only in steps explicitly conditioned on `refs/tags/v*`.

For tags, only completely absent configuration with `SIGNPATH_REQUIRED=false` (or unset) allows an unsigned ZIP. Partial configuration, empty/whitespace fields, or malformed `SIGNPATH_REQUIRED` fail the release. Complete configuration activates signing even with `SIGNPATH_REQUIRED=false`; failure never silently falls back to the unsigned ZIP. After onboarding and validation of the first signed workflow, set `SIGNPATH_REQUIRED=true` to require signing for every official tag.

## Artifact transport and validation

The SignPath artifact configuration must have a `<zip-file>` root corresponding directly to `Dofusic.zip` and select only `Dofusic/Dofusic.exe` for Authenticode signing. Every path and byte in `Data/` and `Musiques/` must remain identical; no additional files are allowed. Do not configure a ZIP containing another ZIP or signing of all Data binaries.

The pinned upload action sends exactly `Release/Dofusic.zip` with `archive: false`; its returned `artifact-id` is submitted to the pinned SignPath v2 action. `wait-for-completion: true` and `skip-decompress: true` download the original returned ZIP to a fresh temporary directory. Before any extraction, the helper validates both archives: root/layout, CRC, duplicate paths, Windows aliases and collisions, traversal, regular files, identical paths and all non-EXE bytes. Only the returned EXE is extracted to a second fresh directory. `Get-AuthenticodeSignature` must return `Status=Valid` before replacing the candidate ZIP; missing or invalid signed output fails the release.

The original returned ZIP becomes the immutable final artifact. Final ZIP validation and SHA256, SBOM generation/validation, attestation and publication all follow signing and validation. The workflow never recompresses or modifies that ZIP after signing. The public link remains a single bundled portable download with its music included.

## GitHub permissions and token

Signing runs in its own tag-only `sign-windows` job with exactly `contents: read` and `actions: read`. The SignPath action therefore receives only that read-only job's ephemeral `github.token`; it never receives the final job's `contents: write`, `id-token: write` or `attestations: write`. The final `release-windows` job alone has the write permissions needed for attestation and publication. Top-level `permissions: {}` and checkout `persist-credentials: false` remain. [SignPath GitHub integration](https://docs.signpath.io/trusted-build-systems/github) requires Actions/Contents read for `github-token`; the [Download an artifact REST API](https://docs.github.com/en/rest/actions/artifacts#download-an-artifact) explicitly requires Actions read, including artifact downloads from this public repository.

Transport inputs are defined by the [SignPath action at the selected SHA](https://github.com/SignPath/github-action-submit-signing-request/blob/c92b958760219087e01f8d67a1669ed57afe2627/action.yml). No SignPath request was executed during this preparation; remote transport, policy approval and real Authenticode signing still require validation after onboarding.
