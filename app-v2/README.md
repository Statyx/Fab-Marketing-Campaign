# Customer 360 Cockpit

Marketing & churn cockpit for Microsoft Fabric — the V2 of the demo, hosted on Rayfin in
**Sweden Central** while the entire data plane stays in the existing West US 3 workspace.

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
│   │   ├── AuthPage.tsx      # Sign-in UI
│   │   ├── AppShell.tsx      # Nav, header, provenance footer
│   │   ├── PersonaPanels.tsx # The live panels each assistant owns
│   │   ├── KpiCard.tsx       # A figure plus the measure that produced it
│   │   └── QueryState.tsx    # Loading / error / empty wrapper
│   ├── pages/              # LandingPage, AgentPage, ArchitecturePage, DiagnosticsPage
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
