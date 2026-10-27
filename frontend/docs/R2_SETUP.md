# Cloudflare R2 setup for desktop releases

One-time setup. Follow each section in order; the GitHub Actions workflow
(`.github/workflows/release-desktop.yml`) won't work until all four GitHub
secrets at the bottom are populated.

## 1. Create the R2 bucket

1. Cloudflare dashboard → **R2 Object Storage** → **Create bucket**.
2. Bucket name: `eazybooks-desktop-releases` (or anything; remember it).
3. Location: **Automatic** is fine.
4. Default storage class: **Standard**.
5. Create.

## 2. Expose the bucket publicly (one of the two)

The desktop installer needs to be reachable over plain HTTPS. Pick A or B.

### Option A — Custom domain (recommended)

Lets us use `https://downloads.eazybooks.ai/desktop/…` everywhere instead of
the long auto-generated R2 URL. Required if you want the website's
`DOWNLOAD_URL` and `electron-updater` feed URL to match what's hardcoded in
the codebase today.

1. In the bucket's **Settings → Public access → Custom Domains**, click
   **Connect Domain**.
2. Enter `downloads.eazybooks.ai` (or any subdomain of a domain that's already
   in your Cloudflare account).
3. Cloudflare adds the CNAME automatically. Wait for it to go green
   (~30 seconds).
4. Sanity check: `curl -I https://downloads.eazybooks.ai/` should return
   a Cloudflare-flavoured 200/404, not a connection refused.

### Option B — R2.dev managed subdomain

Fastest path if you don't have a domain handy. Note the URL will be something
like `https://pub-<hex>.r2.dev/` — you'll then need to **update three places**
in the repo to use that URL instead of `https://downloads.eazybooks.ai/`:

- `electron_app/package.json` → `build.publish[0].url`
- `src/components/Pages/EazyBooksCA/Tally.jsx` → `DESKTOP_DOWNLOAD_BASE`
- `.github/workflows/release-desktop.yml` → the URLs printed in the Summary step

1. Bucket → **Settings → Public access → R2.dev subdomain** → **Allow Access**.
2. Copy the `pub-<hex>.r2.dev` URL it gives you.

## 3. Create an API token for the workflow

1. Cloudflare dashboard → **R2 → Manage R2 API Tokens** → **Create API Token**.
2. Token name: `gh-actions-desktop-releases`.
3. Permissions: **Object Read & Write**.
4. Buckets: **Apply to specific buckets only** → select
   `eazybooks-desktop-releases`.
5. TTL: leave default (forever) or set what your security posture requires.
6. Create. **Copy the Access Key ID and Secret Access Key now — the Secret is
   shown once and only once.**
7. The page also shows your **Account ID** (or find it in the right sidebar
   of any R2 page) — copy that too.

## 4. Set GitHub repo secrets

In the `tallyAutomation-frontend` repo on GitHub → **Settings → Secrets and
variables → Actions → New repository secret**, add:

| Secret name | Value |
|---|---|
| `R2_ACCOUNT_ID` | Cloudflare Account ID from step 3.7 |
| `R2_ACCESS_KEY_ID` | Access Key ID from step 3.6 |
| `R2_SECRET_ACCESS_KEY` | Secret Access Key from step 3.6 |
| `R2_BUCKET_NAME` | `eazybooks-desktop-releases` (or whatever you named it) |

## 5. Smoke test

Trigger the workflow manually (without cutting a real tag yet):

1. GitHub → **Actions → Release Desktop → Run workflow**.
2. Version: `1.0.6` (matches what's currently in `package.json` — no commit
   churn if it's already that value).
3. Run.

Expected on success:

- Workflow goes green.
- R2 bucket contains `desktop/EazyBooks-Setup-1.0.6.exe`,
  `desktop/latest.exe`, `desktop/latest.yml`, and a `.blockmap`.
- `https://downloads.eazybooks.ai/desktop/latest.exe` downloads the installer
  in your browser.
- No new commit was pushed because the version didn't change.

## 6. Existing installs

Existing 1.0.6 installs have the OLD `setFeedURL` baked in pointing at the
AWS S3 bucket `eazybooks-application`. They will continue to poll that bucket
and **won't auto-discover R2**.

Since the AWS S3 bridge is intentionally skipped, existing users need to
manually redownload from the new R2 URL once. After they install the
R2-published build, all subsequent updates flow through R2 automatically.
Communicate this in release notes or a one-time in-app message before
deprecating the AWS S3 channel.
