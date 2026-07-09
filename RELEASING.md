# Releasing jvagent

jvagent ships to [PyPI](https://pypi.org/project/jvagent/) via **Trusted
Publishing** (OIDC). No API tokens are stored in the repo — the
[`publish-pypi.yaml`](.github/workflows/publish-pypi.yaml) workflow authenticates
to PyPI/TestPyPI using GitHub's OIDC identity.

## Versioning

- Single source of truth: [`jvagent/version.py`](jvagent/version.py)
  (`__version__`). `pyproject.toml` reads it dynamically.
- Follows [PEP 440](https://peps.python.org/pep-0440/) /
  [SemVer](https://semver.org/): `MAJOR.MINOR.PATCH`, with pre-releases as
  `rcN` / `aN` / `bN` (e.g. `0.1.0rc1`).
- The publish workflow **fails the build if the git tag does not match
  `version.py`**, so the two can never drift.

## One-time setup (per index)

Configure trusted publishers before the first publish:

1. **PyPI** → https://pypi.org/manage/account/publishing/ → add a pending publisher:
   - PyPI Project Name: `jvagent`
   - Owner: `TrueSelph`
   - Repository name: `jvagent`
   - Workflow name: `publish-pypi.yaml`
   - Environment name: **(leave blank)** — the production publish job does not use a
     GitHub environment (OIDC `sub` is `repo:TrueSelph/jvagent`, same pattern as
     `jvspatial`’s `publish.yml`). A blank environment on PyPI is required; setting
     `pypi` here while the workflow omits `environment:` causes
     `invalid-publisher` on first final release.
2. **TestPyPI** → https://test.pypi.org/manage/account/publishing/ → same, with
   Environment name: `testpypi` (the pre-release job uses GitHub environment
   `testpypi`).
3. In the GitHub repo, create a
   [environment](https://github.com/TrueSelph/jvagent/settings/environments)
   named `testpypi` (required for TestPyPI OIDC). The optional `pypi` GitHub
   environment is not used by the workflow; you may delete it or keep it for
   future approval gates.
4. **`RELEASE_PAT` secret** (for the auto-tag workflow). Add a repo
   [secret](https://github.com/TrueSelph/jvagent/settings/secrets/actions)
   `RELEASE_PAT` — a fine-grained PAT (or GitHub App token) with `contents:
   write` on this repo. [`auto-tag.yaml`](.github/workflows/auto-tag.yaml) uses
   it to push the release tag. A tag pushed with the default `GITHUB_TOKEN` does
   **not** trigger `publish-pypi.yaml` (GitHub suppresses workflow cascades), so
   without this secret the auto-tag job fails fast and you fall back to tagging
   by hand (below).

## Cutting a release

1. Update [`CHANGELOG.md`](CHANGELOG.md): move `[Unreleased]` entries under a new
   `## [X.Y.Z] - YYYY-MM-DD` heading; leave a fresh `[Unreleased]` stub.
2. Bump `__version__` in [`jvagent/version.py`](jvagent/version.py).
3. Commit on `main` (via PR), e.g. `chore(release): 0.1.0rc1`.
4. **Merging that PR auto-tags the release.** When `jvagent/version.py` changes
   on `main`, [`auto-tag.yaml`](.github/workflows/auto-tag.yaml) pushes the
   matching `vX.Y.Z` tag (using `RELEASE_PAT`), which triggers the publish below.
   No manual tagging needed. If `RELEASE_PAT` is unset, or you need to (re)tag by
   hand:

   ```bash
   git tag v0.1.0rc1
   git push origin v0.1.0rc1
   ```

5. The workflow builds the sdist + wheel, runs `twine check`, then:
   - **pre-release tag** (`rc`/`a`/`b`) → publishes to **TestPyPI**.
   - **final tag** → publishes to **PyPI**.

6. Verify the install:

   ```bash
   # RC (from TestPyPI):
   pip install -i https://test.pypi.org/simple/ \
       --extra-index-url https://pypi.org/simple/ jvagent==0.1.0rc1
   # Final:
   pip install jvagent==X.Y.Z
   ```

7. Create a GitHub Release from the tag, pasting the CHANGELOG section.

## Retry a failed publish

If the build succeeded but Trusted Publishing failed (e.g. pending publisher not
yet configured), fix the PyPI publisher (see **One-time setup**), then re-run
without re-tagging:

1. Actions → **Publish jvagent to PyPI** → **Run workflow**
2. Enter the tag (e.g. `v0.1.1`) → Run

## Local build check (no publish)

```bash
python scripts/build_jvchat.py   # build + stage the jvchat UI (requires Node)
python -m build
python -m twine check dist/*
```

> The publish workflow runs `scripts/build_jvchat.py` automatically before
> `python -m build`, so the bundled `jvagent/webui/dist/` UI ships in released
> wheels. A local `python -m build` without that step produces a wheel whose
> `jvagent chat` command reports the UI is not bundled.

## Docker base image

A separate workflow,
[`release-docker.yaml`](.github/workflows/release-docker.yaml), builds and pushes
the `jvagent-base` image to the private Harbor registry on changes to
`jvagent/version.py`. It is independent of the PyPI release flow.
