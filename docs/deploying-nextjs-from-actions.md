# Shipping a change to a Next.js site from GitHub Actions

Design note for Phase 4. Nothing here is built yet.

## The path

1. The agent's publish step runs in the private ops repository. It checks out the
   site's own repository with a token that can push to that one repository only.
2. It applies the change to an allowed path (article data, page metadata, sitemap,
   structured data). A change anywhere else stops here and becomes a pull request.
3. It runs `npm ci`, the type check, `next build` and a link check on the runner. A
   failure stops the run before anything is pushed.
4. It commits and pushes to the site's default branch.
5. It deploys.

## Deploying to a platform with a CLI

Where the host deploys from an upload instead of from git, step 5 is the host's CLI
with an API token stored as a secret, run from the site checkout. Things to settle in
Phase 4, on a throwaway app first:

- **Can the CLI log in without a prompt?** It must accept the token from a flag or a
  variable.
- **Does it report failure?** A detached deploy can fail silently. Run it attached so
  the build log and the exit code reach the workflow.
- **Where does the build run?** If the host builds on its own servers, anything the
  build fetches from the open internet (fonts, remote data) can fail there even though
  it works on the runner. Build inputs have to be in the repository.
- **Lockfile and versions.** If the host's builder is older than the runner's tooling,
  it may reject the lockfile or a framework version. The local build passing is not
  proof the host's build will.
- **Upload reach.** The upload endpoint has to be reachable from the runner's network.
  A plain request to it is not a reliable test; a real upload is.

## Rollback

Every shipped change is one commit. Restoring is a revert commit through the same path,
prepared as a ready-made pull request when the daily watch sees a page lose most of its
clicks.

## If the host cannot be reached from GitHub

The fallback is a self-hosted runner for that one site, on a machine that can reach the
host. The workflow stays the same; only `runs-on` changes.
