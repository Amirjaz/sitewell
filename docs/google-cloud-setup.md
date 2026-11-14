# Search Console access: one-time Google setup

sitewell reads Search Console through Google's API. That needs a Google Cloud project,
which is free for this use. You do this once, and it takes about ten minutes.

There are two ways to sign in. Use the first unless you know you want the second.

| | Sign in as yourself | Service account |
|---|---|---|
| Works with | Whatever access your Google account already has | Only properties where you add the service account as a user |
| Needs | One browser sign-in | Owner rights on each property |
| Secret to store | A refresh token | A key file |

## 1. Create the project and switch the API on

1. Open <https://console.cloud.google.com/> and sign in with the Google account that can
   see your Search Console properties.
2. Top bar, project picker, **New project**. Name it `sitewell`. Create it and select it.
3. Menu, **APIs & Services**, **Library**. Search for **Google Search Console API**,
   open it and press **Enable**.

## 2a. Sign in as yourself

1. Menu, **APIs & Services**, **OAuth consent screen** (Google may call it **Google Auth
   Platform**). Choose **External**. Fill in an app name (`sitewell`) and your email in
   both email fields. Save.
2. On the **Audience** page press **Publish app** so the status reads **In production**.
   This matters: while an app is in **Testing**, Google cancels its sign-in after seven
   days and the agent stops working. Publishing here does not make anything public and
   needs no review for your own use.
3. **Clients**, **Create client**, application type **Desktop app**. Create it and
   download the JSON file. Save it outside any git folder.
4. On your computer:

   ```bash
   sitewell gsc auth --client-secrets path/to/client.json --out path/to/gsc-credentials.json
   ```

   A browser opens. Pick the account. Google may warn that the app is not verified:
   choose **Advanced**, then **Go to sitewell**. Allow read access to Search Console.
5. Check it:

   ```bash
   export SITEWELL_GSC_CREDENTIALS_FILE=path/to/gsc-credentials.json
   sitewell gsc sites
   ```

   Every property you can see is listed with your permission level.

## 2b. Or: a service account

1. Menu, **IAM & Admin**, **Service Accounts**, **Create service account**. Name it
   `sitewell-reader`. It needs no roles. Finish.
2. Open it, **Keys**, **Add key**, **Create new key**, **JSON**. Save the file outside
   any git folder.
3. In Search Console, for each property: **Settings**, **Users and permissions**,
   **Add user**. Paste the service account's email (it ends in
   `.iam.gserviceaccount.com`) and give it **Full** permission. Restricted is enough for
   click data but not for URL inspection.
4. Point `SITEWELL_GSC_CREDENTIALS_FILE` at the key file and run `sitewell gsc sites`.

## 3. Give it to GitHub Actions

The same JSON document goes into one secret. From the folder of the repository that
runs the agent:

```bash
gh secret set SITEWELL_GSC_CREDENTIALS < path/to/gsc-credentials.json
```

Workflows pass it on as the `SITEWELL_GSC_CREDENTIALS` variable. Nothing prints it.

## Limits worth knowing

- Search Analytics returns at most 25,000 rows per request; sitewell pages through.
- Data for the last two or three days is still being counted, so fetches stop three days
  back by default.
- URL inspection allows 2,000 calls a day and 600 a minute per property.
- With a `query` or `page` breakdown Google leaves out rare queries. Site totals always
  come from the by-date pull.
