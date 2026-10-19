# Releasing Tinta

The version in `pyproject.toml` is the source for release tags. Use `X.Y.ZrcN` for a release candidate and `X.Y.Z` for a final release. The tag must be `v` followed by that exact version.

| Tag | TestPyPI | PyPI |
| --- | --- | --- |
| `v0.2.2rc1` | Automatic | Not published |
| `v0.2.2` | Automatic | After TestPyPI checks and environment approval |

The versions above are examples. Choose the next version before preparing a release.

## GitHub configuration

In the repository's **Settings > Environments**, create two environments:

- `testpypi`: allow deployment from tags matching `v*`. No reviewer is needed for automatic candidate publication.
- `pypi`: allow deployment from tags matching `v*` and add yourself under **Required reviewers**. Leave **Prevent self-review** disabled if you are the only maintainer and will start the release yourself.

Keep GitHub Actions enabled and allow the actions referenced by `.github/workflows/release.yml`. The workflow requests `id-token: write` only for publishing jobs and `contents: write` for the GitHub Release job. It does not need permission to create pull requests.

The approval before PyPI is enforced by the environment's required reviewer rule. Creating an environment without that rule will allow publication without a manual approval. Required reviewers are available for public repositories on GitHub's current Free, Pro, and Team plans; private repositories may require a different plan.

## TestPyPI and PyPI configuration

Configure a GitHub Trusted Publisher separately on TestPyPI and PyPI. For a project that does not exist yet, use a pending publisher under the account's publishing settings. For an existing project, add the publisher in that project's publishing settings.

| Field | TestPyPI | PyPI |
| --- | --- | --- |
| Project name | `tinta-mcp` | `tinta-mcp` |
| Owner | `quenazsoares` | `quenazsoares` |
| Repository | `tinta-mcp` | `tinta-mcp` |
| Workflow filename | `release.yml` | `release.yml` |
| Environment | `testpypi` | `pypi` |

Use the workflow filename, without the `.github/workflows/` prefix. Both accounts must be ready to publish and the package name must be available to you on each index.

Trusted Publishing uses temporary credentials issued to GitHub Actions. This workflow does not use `TEST_PYPI_API_TOKEN` or `PYPI_API_TOKEN` secrets. Your local GPG key stays on your machine; it is used to sign commits and tags, not to authenticate package uploads.

See the [PyPI publisher setup](https://docs.pypi.org/trusted-publishers/adding-a-publisher/) and [GitHub environment rules](https://docs.github.com/en/actions/reference/workflows-and-actions/deployments-and-environments).

## Prepare a version

Start from a clean checkout containing all changes intended for the release. Run the quality checks and review the results before tagging.

```bash
uv run python scripts/release.py prepare 0.2.2rc1
```

The command updates `pyproject.toml` and `uv.lock` and prints the signed Git commands to run after reviewing the diff. It does not commit, create a tag, or push. It rejects an existing tag, a version that does not advance the current release, and pending changes.

For the example above, the remaining steps are:

```bash
git diff -- pyproject.toml uv.lock
git add pyproject.toml uv.lock
git commit -S -m "chore(release): prepare 0.2.2rc1"
git tag -s v0.2.2rc1 -m "Release 0.2.2rc1"
git push --atomic origin HEAD v0.2.2rc1
```

Run this on the branch you intend to release, normally `main`. The push starts the release workflow. The workflow verifies tag/version agreement, tests, dependency audit, and live Linux Inkscape integration before building the distributions.

## Test and promote

Wheel and sdist are built once. The workflow verifies the skill ZIP and bundled package assets, checks package metadata, and tests installation from the wheel. The verified distributions and their SHA-256 hashes are saved as a workflow artifact for 30 days.

GitHub Releases attach three files: the wheel, the source distribution (`.tar.gz`), and `tinta-designer-skill.zip` for manual skill installation. The Python package includes the server, extension, and skill, so separate extension and all-in-one ZIPs are not generated. GitHub also provides source code ZIP and tar.gz links for the tagged repository.

The release notes point users to uvx for installation, the skill ZIP for manual setup, and the source archives for development. Final versions are installed from PyPI; candidates are available on TestPyPI or through the attached wheel. Uploads explicitly select `.whl` and `.tar.gz` files so build metadata such as `dist/.gitignore` is excluded.

After uploading to TestPyPI, the workflow downloads both published files and compares their hashes with the original build. It tests the downloaded wheel on Windows and Ubuntu 26.04 with Python 3.12 and 3.13. Dependencies come from the normal PyPI index. Extension and skill installation use temporary directories, leaving the user's Inkscape profile untouched.

Each validation job adds a table to the workflow's Summary page with the package version, runner image, Python version, and outcomes of the hash and installation checks. The summary also runs after a failure and reports failed or skipped checks. Detailed output remains in the job logs; no separate report download is needed.

Candidates stop after TestPyPI validation. If a candidate needs a fix, prepare the next candidate, such as `0.2.2rc2`.

For a final release, prepare `0.2.2` using the same steps. After TestPyPI validation, the `pypi` job waits for the configured environment approval. Review the results under **Actions > Release**, then use **Review deployments** to approve publication. PyPI receives the same files tested on TestPyPI. Changing an `rc` version to a final version creates new artifacts, which are tested again.

Each index retains its published version's files. Publish changed contents under a new version. An unchanged upload can be retried; the workflow checks existing files before uploading.

## Manual runs

Under **Actions > Release > Run workflow**, choose a version tag when requesting publication:

- `none` runs verification and builds without publishing to either index.
- `testpypi` publishes only to TestPyPI and validates the downloaded package.
- `pypi` requires a final version tag, publishes to TestPyPI, validates it, and then waits for PyPI approval.

Publication from a branch or a tag that disagrees with `pyproject.toml` is rejected. Tag pushes also create a GitHub Release; candidate releases are marked as prereleases.
