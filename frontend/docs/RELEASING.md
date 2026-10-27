# Cutting a desktop release

Prerequisites: `docs/R2_SETUP.md` is done and the four `R2_*` GitHub secrets
are populated.

## Normal release

```bash
# From the frontend repo root, on a clean main:
git checkout main
git pull
git tag v1.0.7              # bump as appropriate (semver)
git push origin v1.0.7
```

That's it. The `Release Desktop` workflow then:

1. Builds the Windows NSIS installer on a `windows-latest` runner.
2. Uploads three files to R2 under `desktop/`:
   - `EazyBooks-Setup-1.0.7.exe` — versioned binary
   - `latest.exe` — stable alias used by the website's Download button
   - `latest.yml` — feed the installed app polls for auto-updates
   - `EazyBooks-Setup-1.0.7.exe.blockmap` — delta-update integrity data
3. Updates `LATEST_VERSION` in `src/components/Pages/EazyBooksCA/Tally.jsx`,
   bumps `electron_app/package.json` to 1.0.7, commits to `main` with
   `[skip ci]`, and pushes.
4. Vercel sees the push and redeploys the website with the new version
   number shown on the Download button.

Installed apps pick up the update on their next periodic check (currently
on app start, see `electron.js:574`).

## Workflow-dispatch (manual) release

If you need to rebuild a version without making a new tag (e.g. the previous
run failed during upload), trigger manually:

- GitHub → **Actions → Release Desktop → Run workflow**.
- Enter the version number (without leading `v`, e.g. `1.0.7`).

## Hotfix flow

For a fast hotfix that doesn't need the regular release cadence:

1. Branch off main, fix, PR, merge.
2. Tag the merge commit: `git tag v1.0.7-hotfix.1 && git push origin v1.0.7-hotfix.1`.

Note: `electron-updater` doesn't natively understand pre-release identifiers,
so prefer plain `vX.Y.Z` for releases users should auto-receive. Use
pre-release tags only for builds you'll distribute manually.

## Rollback

If a release goes bad and you need previous-version installs to stop seeing
it:

1. Edit `latest.yml` on R2 to point back at the previous version's
   `EazyBooks-Setup-X.Y.Z.exe`, or
2. Delete `latest.yml` entirely (auto-update will silently no-op until you
   re-publish a new one), and
3. Bump `latest.exe` back to the previous installer so new downloads aren't
   broken either.

You can do these via `aws s3 cp` with the same R2 endpoint the workflow uses,
or directly in the Cloudflare R2 dashboard's bucket browser.

## What lives where

| Artifact | URL | Cache |
|---|---|---|
| Versioned installer | `https://pub-5d6be9a7b1324f2c99df92a72a3b216c.r2.dev/desktop/EazyBooks-Setup-{version}.exe` | immutable, 1 year |
| Latest installer (website button) | `https://pub-5d6be9a7b1324f2c99df92a72a3b216c.r2.dev/desktop/latest.exe` | 5 minutes |
| Auto-update feed | `https://pub-5d6be9a7b1324f2c99df92a72a3b216c.r2.dev/desktop/latest.yml` | 5 minutes |
| Blockmap (delta updates) | `https://pub-5d6be9a7b1324f2c99df92a72a3b216c.r2.dev/desktop/EazyBooks-Setup-{version}.exe.blockmap` | immutable, 1 year |

If a user reports the website still showing an old version after a release,
their browser is hitting cached HTML — hard refresh fixes it. The
`LATEST_VERSION` constant comes from the build, not from any runtime fetch.
