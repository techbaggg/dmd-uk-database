# DrGenZ NHS dm+d automatic catalogue updates

This repository publishes the public, compressed NHSBSA dm+d catalogue consumed by the DrGenZ Medical Database Android app.

## One-time secret setup
1. Change the NHS TRUD account password if the previous API key was exposed. A password change invalidates the old key and generates a replacement.
2. In this repository open **Settings → Secrets and variables → Actions**.
3. Create a repository secret named **TRUD_API_KEY** and paste the new key. Never commit the key or place it in the Android APK.
4. Open **Actions → Update NHS dm+d catalogue → Run workflow**.

The workflow uses TRUD item **24** (NHSBSA dm+d), verifies the release archive SHA-256, converts the XML records to gzip-compressed JSON, checks that the main dm+d tables are populated, then publishes the latest file and manifest to the orphan `catalog-data` branch. The branch is force-updated so old 13 MB catalogue copies do not accumulate in Git history.

## Public app endpoints
- Manifest: https://raw.githubusercontent.com/techbaggg/dmd-uk-database/catalog-data/uk/manifest.json
- Catalogue: https://raw.githubusercontent.com/techbaggg/dmd-uk-database/catalog-data/uk/medicine_catalog_uk.json.gz

The workflow runs weekly on Monday at 02:30 UTC and can also be started manually. The Android app checks the manifest weekly when network connectivity is available, verifies the downloaded catalogue SHA-256, and keeps its existing catalogue if an update fails.

The app bundles the 9 November 2026 dm+d 11.1.0 snapshot, so UK medicine searches remain available offline before the first successful update.

## Supplementary release
The current pipeline uses the main dm+d item (24). Item 25 can be added after the supplementary TRUD subscription is confirmed.
