# Release distribution

The first outside-adopter release is the workflow-only `core` profile. Both
channels carry the same archive and detached checksum metadata. Python 3.11+
and Git are required to apply it; the npm channel also requires Node and npm.
No harness package or registry service is included.

## GitHub Releases

A maintainer builds the release from a tagged source revision, pushes the tag,
and publishes through the existing release CLI:

```sh
python3 scripts/build-release.py build --release-id v1.0.0 --profile core \
  --archive dist/core.zip --metadata dist/core.release.json
python3 scripts/build-release.py publish --tag v1.0.0 \
  --archive dist/core.zip --metadata dist/core.release.json
```

The tag must exist on `origin`. The CLI fetches the remote default branch,
refuses tags outside its ancestry, verifies the archive and metadata, and
rebuilds the workflow-only archive from the tag to ensure the uploaded bytes
match that source. `gh` must be authenticated to the repository with release
write access. Publication explicitly targets the validated `origin` URL, even
when `GH_REPO` or a gh default points elsewhere. The command creates a release with both assets; it does not
replace an existing release. Product releases with an external harness runtime
are not supported by this publication command.

An adopter starts in an empty directory and downloads both assets from the
intended repository and release. Replace `OWNER/REPOSITORY` and the absolute
repository path below. Authenticate the repository, release tag, asset source,
and qualification record for these exact bytes through your organisation's
trusted GitHub channel **before executing the archive**. The archive includes
its installer; no producer checkout or Python packages are required.

```sh
gh release download v1.0.0 --repo OWNER/REPOSITORY \
  --pattern core.zip --pattern core.release.json --dir downloads
python3 -B downloads/core.zip validate --archive downloads/core.zip \
  --metadata downloads/core.release.json
python3 -B downloads/core.zip --root /absolute/path/to/repository apply \
  --archive downloads/core.zip --metadata downloads/core.release.json --maintenance
python3 -B /absolute/path/to/repository/.agent-skills/current \
  --root /absolute/path/to/repository status
```

If the trusted files are already present in `downloads/`, start with the first
Python command. `validate` checks the archive checksum, manifest, file digests,
profile and release identity. `apply` verifies again and validates the standalone
corpus before installing; healthy `status` reports `lifecycle: current` and empty
`blockers`. Keep `-B` to avoid bytecode writes into the installed generation.
Detached checksums detect corruption and mismatched files, **not publisher
identity**: an attacker who replaces both metadata and archive can replace their
checksums too. The archive's own verifier is executable code, not an independent
trust anchor. This release form does not add cryptographic signatures.

## npm package and private mirrors

Create the package from those same verified assets. Choose a package name and
version appropriate for the registry; they do not change the archive's generation.

```sh
python3 scripts/build-release.py npm --archive dist/core.zip \
  --metadata dist/core.release.json --output-directory dist/npm \
  --package-name @your-org/agent-skills --package-version 1.0.0
cd dist/npm
npm pack
npm publish --registry https://YOUR-REGISTRY
```

The package includes its archive, metadata, and Python lifecycle code taken from
the verified archive. It has no install hook. Installing the package makes the
installer available; explicitly applying it grants authority to change the chosen
repository:

```sh
npm install --save-dev @your-org/agent-skills@1.0.0 --registry https://YOUR-REGISTRY
npm exec -- agent-skills-release apply --root /absolute/path/to/repository --maintenance
```

For an offline/local check, replace the package specifier with the absolute path
to the tarball produced by `npm pack`. `--ignore-scripts` is supported. Set
`AGENT_SKILLS_PYTHON` to a Python 3.11+ executable if `python3` is not available.
The command calls the same `agent_skills.cli apply` lifecycle against the bundled
archive and metadata. Tampering with the archive or mismatching metadata is
refused before repository publication. Reapplying identical assets yields the
same generation as the archive channel.

Trust the package registry and package publisher as you would the installer:
the Python bootstrap is executable code. npm tarball integrity and lockfiles
help preserve the reviewed package bytes; they do not authenticate an arbitrary
publisher. Mirror the reviewed tarball without rewriting its release assets.
The package command checks the release before packaging, and `apply` checks it
again at use time. Registry selection, authentication, and operation remain the
adopter's responsibility.

Publication has boundary-fixture coverage with a real Git remote and local npm
tarball. A real public GitHub release is a separate shipping action after the
integration branch is admitted to the default branch and its release qualified.
