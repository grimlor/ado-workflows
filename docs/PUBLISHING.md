# Publishing to PyPI

## How It Works

Publishing uses **PyPI Trusted Publishers** (OIDC) — no API tokens needed.
When you create a GitHub Release, the `publish.yml` workflow automatically
builds, tests, and uploads to PyPI.

## One-Time Setup

1. Go to https://pypi.org/manage/account/publishing/
2. Add a **pending publisher** with:
   - **PyPI project name:** `ado-workflows`
   - **Owner:** `grimlor`
   - **Repository:** `ado-workflows`
   - **Workflow name:** `publish.yml`
   - **Environment name:** `pypi`
3. Create a GitHub Environment named `pypi` in the repo settings:
   - Settings → Environments → New environment → `pypi`
   - Optional: add deployment protection rules (e.g., required reviewers)

## Publishing a Release

```bash
# 1. Bump version in pyproject.toml
# 2. Commit and push
git commit -am "Bump version to X.Y.Z"
git push origin main

# 3. Tag and create release
git tag vX.Y.Z
git push origin vX.Y.Z
# Then create a Release on GitHub from the tag
```

Or use `gh`:

```bash
gh release create vX.Y.Z --title "vX.Y.Z" --notes "Release notes here"
```

The `publish.yml` workflow will:
1. Run lint, type check, and tests
2. Build wheel + sdist
3. Upload to PyPI via OIDC (no tokens)

## Local Build (for testing)

```bash
uv build
ls dist/
# ado_workflows-X.Y.Z-py3-none-any.whl
# ado_workflows-X.Y.Z.tar.gz
```

## Version Bumps

Update `version` in `pyproject.toml`. Follow semver:

- **Patch** (0.1.1): bug fixes, no API changes
- **Minor** (0.2.0): new features, backward compatible
- **Major** (1.0.0): breaking changes

## Verification

After publishing:

```bash
pip install ado-workflows
python -c "from ado_workflows import AdoClient; print('OK')"
```
