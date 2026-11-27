# Investment Analyst UI

Frontend for Indium's investment research platform — a portfolio workspace where an analyst researches companies (secondary/qualitative research, quantitative/web-search research, stock comparisons, ALPHA analysis), clips findings, and assembles them into a report.

## Stack

- **React 19 + TypeScript + Vite**
- **Tailwind CSS** (with `corePlugins.preflight: false` — the app ships its own reset) + shadcn/ui-style primitives (`components.json`, Radix UI, `cn()` helper)
- **CSS custom properties** as the design-token layer (`src/index.css`) — one file drives both plain CSS (`var(--token)`) and Tailwind (`tailwind.config.js` reads the same vars), including light/dark theme via a `data-theme` attribute
- **Zustand** for shared client state (`src/store/`: analysis runs, clipboard, report editor, providers, pending navigation)
- **React Router v7**, **react-markdown** (+ remark/rehype plugins for GFM, math/KaTeX), **jsPDF/html2canvas** for PDF export

## Getting started

The UI is part of the Investment Analyst repository: the backend is in `../backend`, and the main [README](../README.md) sets up both with one command (`python scripts/dev.py setup`, then `start`). To work on the UI alone, against an API that is already running:

```bash
cd frontend
npm ci                     # exact versions from package-lock.json (Node 20.19+, 22 LTS recommended)
cp .env.example .env       # VITE_API_BASE_URL: where the browser reaches the API (default http://localhost:8000)
npm run dev                # http://localhost:5173, hot reload
```

| Script | Does |
|---|---|
| `npm run dev` | Vite dev server with hot reload |
| `npm run build` | Type check (`tsc -b`) and production build into `dist/` |
| `npm run lint` | ESLint (must be clean: CI fails otherwise) |
| `npm run preview` | Serve the production build locally |

From the repo root, `python scripts/dev.py check` runs these together with the backend tests. `VITE_API_BASE_URL` is read at **build time**, so a production build must be made with the address its users' browsers use for the API, and the backend must list this UI's address in `CORS_ALLOWED_ORIGINS`.

**Adding a library:** `npm install <name>` (or `-D` for build tools), then commit `package.json` and `package-lock.json` together.

### Docker

`docker compose --profile ui up -d --build` from the repo root builds this folder and serves it on port 3000 (set `VITE_API_BASE_URL` and `UI_PORT` in the root `.env`). On its own:

```bash
docker build --build-arg VITE_API_BASE_URL=https://api.example.com -t investment-analyst-frontend .
docker run -p 3000:80 investment-analyst-frontend
```

## App structure

```
src/
  App.tsx           Routes, gated by ProtectedRoute (redirects to /login when signed out)
  pages/            Route-level screens: Login, Home (portfolio workspace), Repository, MyReports, DataSources,
                    CrossPortfolioDashboard, AlphaRun
  features/         Self-contained areas, each with its own components and API calls:
    settings/       Settings screens (models, retrieval, answers, modules, prompts, documents, quant, AI providers,
                    reading engines), rendered from the backend's GET /settings/schema
    userDocs/       My documents: upload, review, approve
    documents/      Filing library and document inspector
    library/  notes/  dataSources/  chatScope/
  components/
    layout/         Sidebar, Header (global nav shell)
    ui/             Shared primitives (Button, Modal, ChatMessage, EmptyState, Tooltip, ResizableSidebar, ...)
    project/        Per-portfolio workspace tabs: secondary research (chat, ALPHA, compare), quantitative
                    research, creation (clipboard → report editor), run progress
    integrations/   Integration-specific form fields
  services/         One module per backend resource (auth, chat, portfolios, reports, analysis tasks, agents,
                    integrations, data sources, MCP servers, notes, providers, reading, settings). http.ts holds
                    the shared fetch and server-sent-event helpers; authInterceptor.ts refreshes tokens.
  store/            Zustand stores (analysis runs, clipboard, report editor, providers, pending navigation)
  context/          Providers (AuthContext, ThemeContext, ChatCacheContext: chats kept alive across tabs); their
                    hooks are in useAuth.ts, useTheme.ts, useChatCache.ts
  hooks/  lib/  data/
```

Chat answers stream from `POST /ask/stream` (server-sent events: steps, tokens, then the full result); long runs go through `/analysis-tasks` and are followed live on `/analysis-tasks/stream/...`. Settings forms are generated from the backend schema, so a setting added on the backend appears here without UI changes.

### Routing & auth

Routes are defined in `App.tsx` and gated by `ProtectedRoute` (redirects to `/login` when unauthenticated). `useAuth()` (`context/useAuth.ts`) gives the signed-in user; the access token lives only in memory and `services/authInterceptor.ts` refreshes it silently from the httpOnly refresh cookie on a 401. `Home` renders the Sidebar + Header shell and, once a project is selected, a tabbed workspace (Overview / Secondary Research / Quantitative Research / Creation) — each research tab stays mounted (`display: none` when inactive) so chat history and scroll position survive switching tabs.

### Design tokens & theming

All color/spacing/shadow/radius values live as CSS custom properties in `src/index.css` (`:root` for light, `[data-theme="dark"]` for dark overrides). `ThemeContext` toggles the `data-theme` attribute on `<html>` and persists the choice to `localStorage`. Avoid hardcoded hex/px values in new CSS — use the existing tokens (or add one) so theme changes stay a single-file edit.

### Research chat conventions

- `ChatMessage` (`components/ui`) is the shared bubble used by both research pages — it renders per-page accent color via a `--chat-message-accent` CSS variable set on each page's container.
- ALPHA Analysis and Stock Compare are **not** modeled as a back-and-forth conversation: no synthesized "user" turn is shown, just a labeled loading state followed by the single bot response (see `resultLabel` on `ChatMessage` in `SecondaryResearch.tsx`).
- Clipboard: selecting text or clicking a chart/image clips it; hovering a message card reveals an "Add to Clipboard" button for the whole message. Clipped items land in the Creation tab, grouped by source (Secondary/Quant) and by type (clips/summaries/drafts).

### Code rules

- Talk to the API through a `services/*Api.ts` module (URLs from `API_BASE` in `services/http.ts`, response types matching the backend). No hand-built `fetch` URLs in components.
- Files in `context/` export only their provider component; the hook and the context object live in `useX.ts`, so Vite's hot reload keeps component state.
- Zustand selectors return stored values. Never return a freshly built array or object from a selector: it re-renders forever.
- No `any`: type API responses (see `services/chatApi.ts`, `services/edgarApi.ts`) and narrow caught errors with `err instanceof Error`.
- A name that is intentionally unused starts with `_`; pull props you do not forward out with destructuring (`{ node, ...props }`).
- Effects list every dependency. When an effect must read the latest value without re-running for it, use `useEffectEvent` (see the history loading in the research screens).
- `npm run lint` and `npm run build` pass before every commit.

More on how the UI fits the whole system: [docs/ARCHITECTURE.md § The web UI](../docs/ARCHITECTURE.md#10-the-web-ui).
