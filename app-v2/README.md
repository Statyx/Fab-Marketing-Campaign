# Customer 360 Cockpit

Marketing & churn cockpit for Microsoft Fabric — the V2 of the demo. The original deployment
hosts the app on Rayfin in **Sweden Central** with the data plane in West US 3. An isolated
replay targets the app and data at the selected profile's workspace instead.

The app is a browser-only client: there is no server-side code and no database. It reads the
`SM_Marketing_Analytics` semantic model directly over the Power BI `executeQueries` REST API
(DAX, ~1 s) and calls the Foundry supervisor over A2A (~40-60 s) for explanation and RCA.

One navigation, four assistants. Each one owns its own live panels *and* its own conversation,
side by side:

| Assistant | Panels it shows | What you ask it |
| --- | --- | --- |
| 🎯 Direction | 8 portfolio measures, risk distribution | portfolio value, exposure, NPS |
| 🛟 Retention | 4 cohort measures, clickable risk bands | who is at risk and why |
| 📣 Marketing | email pressure per campaign, outlier detection | which campaign caused it |
| 🛒 Commerce | CLV and revenue exposed, exposure per segment | which segment carries the value |

The app used to carry a second navigation over the same subject — a four-step arc (Détecter /
Diagnostiquer / Quantifier / Agir) that held every chart and no chat, next to four assistants
that held every chat and no chart. Each persona's description already used the arc's own
vocabulary. The arc is gone; its visuals now live inside the assistant they belong to
(`components/PersonaPanels.tsx`), and `/detect`, `/diagnose`, `/quantify` and `/act` redirect.

The merge is also the dark-mode fix: the arc pages hardcoded `bg-white` / `text-slate-900`
instead of the theme variables, which is why the night theme was unreadable.

**The chart is the way into the question.** Every row in every panel is a button: clicking it
sends a question with the figure already in it, always typed `mixed`, so the chart gives the
*combien* and the supervisor gives the *pourquoi* — and both subordinates have to fire, which
is the only staging where the supervisor visibly supervises.

Three rules the code enforces rather than documents:

- **Every figure comes from a model measure.** Nothing is re-derived client-side, so the app
  cannot silently disagree with the Power BI report (`At Risk %` is 7.8 %, over *Buyers* — a
  naive recomputation over Total Customers would show 6.9 %).
- **The culprit campaign is never named in code.** The marketing panel flags whatever exceeds
  1.5× the median sends-per-customer *and* 2× the median unsubscribe rate, and prints the
  thresholds on screen, so the cause emerges instead of being asserted.
- **Every generated question names a table and a column.** "How many at risk" has three
  correct answers (800 / 825 / 593) depending on the column read; an unscoped question is
  under-specified, not unstable.

`/diagnostics` runs a six-step connectivity proof: sign-in, Fabric token, cross-region item
read, Foundry token, supervisor call, and a DAX round-trip that checks a measure and its
underlying column against each other. **Nothing links to it** — it was a build-time
instrument, and on a demo it sat beside `/architecture` at the same weight while saying
nothing an audience wants. Type the URL. It is outside the auth gate on purpose, which is
what lets it answer when sign-in itself is what failed.

## Getting started

```bash
# This deploys to Fabric before starting the local dev server.
npm run dev
```

Open [http://localhost:5173](http://localhost:5173) to view the app.

### Isolated deployment profiles

`FAB_MARKETING_PROFILE_DIR` selects an absolute or **repository-relative** profile directory.
Otherwise, `deployments/active-profile.json` selects `{"profile":"new-tenant"}` under
`deployments/`. No selection keeps the original app environment and Rayfin behavior.
An invalid selection fails rather than falling back to the old tenant.

Prepare these private files before using the app with a selected profile:

| File under the profile | Contents |
| --- | --- |
| `config.yaml` / `state.json` | The shared Python deployment configuration and its stamped receipt |
| `app/.env.production.local` | Production `VITE_*` values |
| `app/.env.development.local` | Development `VITE_*` values |
| `app/.env` | Only `RAYFIN_TENANT_ID` and `RAYFIN_WORKSPACE_ID`, matching the selected receipt |

Both mode-local files must explicitly define `VITE_ENTRA_CLIENT_ID`, `VITE_ENTRA_TENANT_ID`,
`VITE_DATA_WORKSPACE_ID`, `VITE_SEMANTIC_MODEL_ID`, `VITE_FOUNDRY_ENDPOINT`,
`VITE_FOUNDRY_SUPERVISOR` and `VITE_FOUNDRY_MODEL`. Tenant, data workspace, model, and Foundry
settings are compared with the selected configuration/receipt; the SPA must not reuse the
legacy client ID. The Foundry endpoint must match both `state.foundry_project_endpoint` and
`config.foundry.project_endpoint`. The model deployment name comes only from
`config.foundry.model_deployment`; it is not a deployment receipt. SPA registration is supplied
separately, never inferred from Foundry state. Optional `VITE_FABRIC_*` and `VITE_RAYFIN_*` hosting values must match the
selected Rayfin receipt. Omit hosting values before the first app deployment: configured MSAL
still owns authentication, and the data/Foundry calls do not use Rayfin session tokens.

Vite loads **only the selected `app/` environment directory**, including when invoked through
`build:fabric`. The mode-local file must contain every `VITE_*` value; inherited or base-file
overrides cannot silently add a previous tenant's settings. `predev` and `prebuild` only read
and validate a selected profile; they do not run `rayfin env` or write the original app env.
Builds and `vite preview` use the selected `artifacts/app-v2/dist` directory, not the legacy `dist`.
The wrapper supplies `FAB_MARKETING_APP_DIST` to the existing Rayfin hosting configuration so
it packages that same output; without a profile, the folder still defaults to `dist`.
Tests do not authenticate, deploy, or generate environment files.

Selected builds also replace the original frozen-answer input. An absent
`artifacts/captures/frozen_answers.json` produces a build warning and an empty answer
dictionary: the existing live-supervisor path handles every miss. The old tenant's recording
is never substituted. A present capture must be valid JSON with `answers` and the capture
writer's `deployment` metadata: `tenantId`, `workspaceId`, `semanticModelId`, `dataAgentId`,
`projectEndpoint`, `agentName`, and `model`, all matching the selected receipt/environment.
Invalid or stale captures stop the build rather
than switching silently to live mode. Only `answers` is included in the bundle, never the
private provenance. Capture generators must use
`output_path(ANSWERS, "captures", "frozen_answers.json")` and write this metadata; the local app
workflow never invokes a capture generator.

After the deployment gate and separate approval, use the existing `npm run rayfin:up`
workflow. It supplies `--tenant`, `--workspace-id`, and `--env-file` itself. Profile workflows
reject target overrides, subcommands and `--force`. `npm run dev` **still deploys** (excluding
static hosting); it is not an offline-development command. `npm run build` and
`npm run build:fabric` build locally and never sign in.

Rayfin **1.34.0** has two independent local stores. Its exported `auth` module honors
`RAYFIN_CONFIG_DIR` (`dist/auth/constants.js`, `state.js`, `cache.js`); `AZURE_CONFIG_DIR`
does not isolate it. For a separately approved `rayfin login --tenant <tenant-id>`, set
`RAYFIN_CONFIG_DIR` to `deployment.rayfin_config_dir` from the selected private configuration.
If that field is absent, the wrapper derives `.rayfin` beside `deployment.azure_config_dir`.
Both locations must be absolute. Keep the Rayfin cache outside the repository and OneDrive,
for example beside the isolated Azure CLI cache under `LOCALAPPDATA/Azure-Brain/tenant-profiles`.
**Gitignore does not stop OneDrive syncing tokens.** Profile-local caches, normal/global caches,
overlapping Azure/Rayfin directories, and conflicting inherited cache selections are rejected.
The wrapper creates or moves no cache: a separately approved login owns it. The wrapper checks
the persisted user/tenant and silently acquires a token whose identity matches
`deployment.expected_account`; `up` cannot open a different account picker mid-deployment.
Before invoking `up`, it reads the workspace and any recorded AppBackend using that token.
A changed workspace name or wrong item is rejected before the CLI's name-first lookup.

For a profile already authenticated with Azure CLI, explicitly set
`deployment.rayfin_auth_source: azure-cli`. The wrapper then obtains a delegated Fabric token
through the shared Python profile/identity checks and passes it to the installed CLI through
its supported `RAYFIN_TOKEN` input. No Rayfin login/cache files are fabricated or populated.
Tenant, user, Fabric audience, expiry and the live target are checked before deployment.
An acquisition failure stops; it never falls back to another login. Arbitrary inherited
tokens remain forbidden. The default `rayfin` source keeps the separate-cache workflow above.

There is **no alternate deployment registry root** in the installed CLI.
`dist/utils/deployments-registry.js` fixes it at `rayfin/.deployments.json`.
`--env-file` selects interpolation input, not output. `dist/commands/up/up.js` still writes
`rayfin/.env` and the app-root `.env.local`, and selects receipts by sanitized workspace name
before workspace ID. Back up those files and the old mode-local envs **before the first new
deployment**. The wrapper ignores the old active entry, refuses name/ID collisions, and checks
the new active receipt plus retention of unrelated records after success. It never deletes or
renames old entries. Use the package scripts, not a raw `rayfin up`, to keep these guards.

The CLI also rewrites `rayfin.yml` with the resolved static folder and the new hosting URL.
The wrapper restores the parameterized template after the command, but only when those are
the only changes; unexpected concurrent edits cause an explicit error instead of being erased.
Live URLs and machine-specific paths stay in the ignored deployment receipts, not the template.

## Project structure

```text
├── rayfin/
│   └── rayfin.yml          # Fabric service configuration (auth + static hosting)
├── src/
│   ├── main.tsx            # Entry point + Rayfin client bootstrap
│   ├── App.tsx             # Routes and auth gate
│   ├── redirect.ts         # MSAL v5 popup landing page (see blank.html)
│   ├── hooks/
│   │   ├── AuthContext.tsx # React context wrapping the auth helpers
│   │   └── useDax.ts       # The single data-fetching primitive
│   ├── components/
│   │   ├── AuthPage.tsx      # Sign-in UI
│   │   ├── AppShell.tsx      # Nav, header, provenance footer
│   │   ├── PersonaPanels.tsx # The live panels each assistant owns
│   │   ├── KpiCard.tsx       # A figure plus the measure that produced it
│   │   └── QueryState.tsx    # Loading / error / empty wrapper
│   ├── pages/              # LandingPage, AgentPage, ArchitecturePage, DiagnosticsPage
│   └── services/
│       ├── IAuthService.ts        # Auth service contract + AuthUser type
│       ├── MockAuthService.ts     # Local-dev impl (email/password)
│       ├── RayfinAuthService.ts   # Non-MSAL fallback (Fabric brokered auth)
│       ├── MsalAuthService.ts     # Configured Entra user/session service
│       ├── msal.ts                # Token acquisition (3 distinct audiences)
│       ├── powerbi.ts             # executeQueries transport
│       ├── queries.ts             # Every DAX string + its row mapper
│       ├── foundry.ts             # Supervisor call, A2A routing evidence
│       ├── rayfinClient.ts        # Typed Rayfin client singleton
│       └── bootstrap.ts           # Reads env, picks the right auth service
└── package.json
```

## Scripts

| Command | Description |
|---------|-------------|
| `npm run dev` | Deploy app to Fabric and start local dev server |
| `npm run build` | Local production build using the selected profile, if any |
| `npm run build:fabric` | Build for Fabric deployment (entrypoint for `rayfin up staticapp deploy`) |
| `npm run lint` | Lint with ESLint |
| `npm run test` | Run offline unit tests with Vitest (no Rayfin preparation) |
| `npm run rayfin:up` | Deploy app to Fabric (no local dev server) |
