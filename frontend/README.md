# SLBM Order Tracker — Frontend

React + TypeScript + Vite + Tailwind + Recharts UI for the SLBM Order Tracker.
Implements all views — **Dashboard**, **Portfolio**, **Live SLB Data**,
**Contracts**, **Rollover**, **Alerts**, **Analytics**, **P&L**, and
**Settings** — with real-time in-app alert push over WebSocket (`/api/ws`).

The frontend talks **only** to the FastAPI backend (`/api/*`). NSE access stays
server-side (Phase 1 NSE client + caching); the browser never calls NSE directly.

## Prerequisites

- Node.js (tested with v24) and npm
- The Phase 1 backend running on `http://127.0.0.1:8000`

## Setup

```bash
cd frontend
npm install
```

## Commands

```bash
npm run dev       # start Vite dev server (http://localhost:5173), proxies /api -> :8000
npm test          # run the Vitest suite once
npm run build     # type-check (tsc -b) + production build
npm run preview   # preview the production build
```

## Configuration

Copy `.env.example` to `.env` to override the API base URL, dev-proxy target, or
polling interval. See that file for details.

## Architecture

- `src/api/` — centralized HTTP client + typed endpoints (no `fetch()` in components)
- `src/types/` — TypeScript mirror of the backend Pydantic schemas (source of truth)
- `src/hooks/useApiResource.ts` — data fetching with controlled polling, abort on unmount
- `src/hooks/useWebSocket.ts` — native WebSocket with auto-reconnect (real-time push)
- `src/context/LiveAlertsProvider.tsx` — app-wide live-alerts socket + in-app toasts
- `src/components/` — reusable UI (cards, tables, states, order book, forms, dialogs, toasts)
- `src/pages/` — Dashboard, Portfolio, Live SLB, Contracts, Rollover, Alerts, Analytics, P&L, Settings
- `src/layouts/` — application shell (sidebar, top bar)
- `src/tests/` — Vitest + React Testing Library (API mocked; no live NSE calls)
