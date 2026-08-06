# Private Vercel preview runbook

This runbook governs a future protected **Preview** deployment. Deployment is currently blocked. It never authorizes a production deployment, public domain, public release, map promotion, acoustic combination, or engine run.

## Current package: zero scientific payloads

The exact Vercel-eligible display payload is defined by `src/data/private-preview-payload-contract.json`:

Total admitted science payload: **0 files / 0 bytes**. The four-region derivative (`8885c4…b45b`, 315,537 bytes) and Tarzana derivative (`60d0e3…9714`, 1,434,069 bytes) remain blocked because existing external-readiness records admit neither their source nor output rights. Airport/Metro terms remain unresolved and private Source-341 review evidence remains excluded. Raw LiDAR, terrain, buildings, runtime databases, campaign evidence, dependency bundles, local lineage manifests, environment files, and credentials are not eligible.

## Local admission

Run from this directory:

```sh
npm ci
npm run stage:private-preview
npm run typecheck
npm run lint
npm run build:private-preview
npm run test:contracts
npm run test:browser:private-preview
```

The build creates only an ignored, hash-bound metadata manifest in `public/_preview-data/`, verifies the independent exact source-path allowlist, exports the static Next.js shell, and audits every served path and credential pattern in `out/`. The default `npm run build` deliberately fails.

## Verified Vercel gate

Vercel Authentication with **Standard Protection** is the selected gate. It is available on all Vercel plans and restricts preview/deployment URLs to users with access. Password Protection is not selected because Vercel documents it as an Enterprise feature or paid Pro add-on.

Project `prj_XTkmGuFf6novot3axeqvnZAo0Ajx` on team `team_TNeSdFukSIvhwO0UOGDgjreq` has been created and Standard Protection has been externally verified. Unique deployment URLs redirect unauthenticated users to Vercel sign-in; the production custom domain remains public. This satisfies the platform-protection predicate only; it does not resolve scientific-payload rights. The current production remains the payload-free commit `c773066`.

Before any preview-branch upload, export fresh machine evidence again:

```sh
npx vercel@latest api /v9/projects/prj_XTkmGuFf6novot3axeqvnZAo0Ajx > /tmp/quiet-la-vercel-project.json
gh repo view Manorouss/quiet-la-noise-finder --json nameWithOwner,visibility,url > /tmp/quiet-la-github-repository.json
npm run verify:vercel-project -- /tmp/quiet-la-vercel-project.json /tmp/quiet-la-github-repository.json
```

The retained unauthenticated HTTP receipt proves only that the existing payload-free unique production deployment redirects to Vercel Authentication and sends `x-robots-tag: noindex`. It does not prove that a future `private-preview` deployment is covered by the project setting. The source contract therefore keeps future-preview protection unverified and upload authorization false until an immutable project-setting receipt or a newly created preview probe establishes that scope. Platform protection never admits payload rights.

## Deployment remains blocked

Do not commit, push, or deploy the empty shell as a completed preview. Do not place either modeled derivative on GitHub or Vercel while its exact hash remains absent from the rights allowlist.

After a separate evidence-backed rights review admits at least one exact hash, update the independent payload contract, its bound deployment hash/count/bytes, UI availability, and negative tests. Rebuild and re-audit locally. Only then may the orchestrator consider a `private-preview` branch. `vercel.json` disables main-branch Git deployment, and the build guard refuses all Vercel builds while the rights/upload gate is false as well as production, `main`, other branches, a wrong project, or a wrong team.

If rights are admitted and a future protected deployment is authorized, immediately test its URL from an unauthenticated browser or session. A direct request must be rejected or redirected to Vercel login; an unauthenticated `200` is a deployment blocker. Then use an authorized Vercel account to verify the portal loads, every exact allowlisted payload request succeeds, `robots.txt` disallows all crawlers, the `X-Robots-Tag` response header remains `noindex, nofollow, noarchive`, and all non-admitted rows remain unavailable.

The private preview then enters the required 48-hour soak. Public release and map promotion remain separate, false gates.

Official platform references: [Deployment Protection](https://vercel.com/docs/deployment-protection), [Vercel Authentication](https://vercel.com/docs/deployment-protection/methods-to-protect-deployments/vercel-authentication), [Git configuration](https://vercel.com/docs/project-configuration/git-configuration), and [`.vercelignore`](https://vercel.com/docs/deployments/vercel-ignore).
