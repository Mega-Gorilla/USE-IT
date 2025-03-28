# Development Workflow

## Branches
- **`stable`**: Mirrors the latest stable release. This branch is updated only when a new stable release is published (every few weeks).
- **`main`**: The primary development branch. This branch is updated frequently (every hour or more).

## Tags
- **`x.x.x`**: Stable release tags. These are created for stable releases and updated every few weeks.
- **`x.x.xrcXX`**: Pre-release tags. These are created for unstable pre-releases and updated every Friday at 5 PM UTC.

## Workflow Summary
1. **Push to `main`**:
   - Runs pre-commit hooks to fix formatting.
   - Executes tests to ensure code quality.

2. **Release a new version**:
   - If the tag is a pre-release (`x.x.xrcXX`), the package is pushed to PyPI as a pre-release.
   - If the tag is a stable release (`x.x.x`), the package is pushed to PyPI as a stable release, and the `stable` branch is updated to match the release.

3. **Scheduled Pre-Releases**:
   - Every Friday at 5 PM UTC, a new pre-release tag (`x.x.xrcXX`) is created from the `main` branch and pushed to the repository.
