# Customer 360 Cockpit

Marketing & churn cockpit for Microsoft Fabric — the V2 of the demo, hosted on Rayfin in
**Sweden Central** while the entire data plane stays in the existing West US 3 workspace.

The app is a browser-only client: there is no server-side code and no database. It reads the
`SM_Marketing_Analytics` semantic model directly over the Power BI `executeQueries` REST API
(DAX, ~1 s) and calls the Foundry supervisor over A2A (~40-60 s) for explanation and RCA.

Four screens follow the demo arc:

| Screen | Question | Route |
| --- | --- | --- |
| 1. Détecter | how big is the churn exposure? | DAX |
| 2. Diagnostiquer | which campaign caused it? | DAX |
| 3. Quantifier | which segment carries the value at risk? | DAX |
| 4. Agir | why, and what do the customers actually say? | Foundry A2A |

Two rules the code enforces rather than documents:

- **Every figure comes from a model measure.** Nothing is re-derived client-side, so the app
  cannot silently disagree with the Power BI report (`At Risk %` is 7.8 %, over *Buyers* — a
  naive recomputation over Total Customers would show 6.9 %).
- **The culprit campaign is never named in code.** `DiagnosePage` flags whatever exceeds
  1.5× the median sends-per-customer and prints the threshold on screen.

`/diagnostics` (outside the auth gate) runs a six-step connectivity proof: sign-in, Fabric
token, cross-region item read, Foundry token, supervisor call, and a DAX round-trip that
checks a measure and its underlying column against each other.

## Getting started

```bash
# Deploy app to Fabric and start the local dev server
npm run dev
```

Open [http://localhost:5173](http://localhost:5173) to view the app.

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
│   │   ├── AuthPage.tsx    # Sign-in UI
│   │   ├── AppShell.tsx    # Nav, header, provenance footer
│   │   ├── KpiCard.tsx     # A figure plus the measure that produced it
│   │   └── QueryState.tsx  # Loading / error / empty wrapper
│   ├── pages/              # DetectPage, DiagnosePage, QuantifyPage, ActPage, DiagnosticsPage
│   └── services/
│       ├── IAuthService.ts        # Auth service contract + AuthUser type
│       ├── MockAuthService.ts     # Local-dev impl (email/password)
│       ├── RayfinAuthService.ts   # Production impl (Fabric brokered auth)
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
| `npm run build` | Production build |
| `npm run build:fabric` | Build for Fabric deployment (entrypoint for `rayfin up staticapp deploy`) |
| `npm run lint` | Lint with ESLint |
| `npm run test` | Run unit tests with Vitest |
| `npm run rayfin:up` | Deploy app to Fabric (no local dev server) |
