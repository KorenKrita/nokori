# Part 6: Frontend Timeline Page — Detailed Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Build the Timeline tab as a new page at `/activity`, showing a real-time scrolling event flow with two-layer collapse, session/type filtering, color-coded type labels, and result badges.

**Architecture:** New route `/activity` → `Activity.tsx` with two tabs (Timeline | Nokori Dashboard). Part 6 implements the Timeline tab only; Dashboard tab shows placeholder. Uses 5s polling via `useEffect` + `setInterval`, hitting `GET /api/timeline` and `GET /api/timeline/sessions`.

**Tech Stack:** React 19, TypeScript, Tailwind CSS 4, Motion (framer-motion), Phosphor Icons, existing `useApi` hook pattern.

---

## Research Findings (Round 2)

### Frontend Patterns Observed
- **Routing:** `createBrowserRouter` in `App.tsx`, lazy imports per page
- **Layout:** `Layout.tsx` has `NAV_ITEMS` array, NavLink with layoutId animation
- **API fetching:** `useApi<T>(path, params)` hook (single fetch + refetch support); for polling need custom hook or `setInterval` wrapper
- **Page structure:** motion.div entrance animation, PageSkeleton for loading, GlassCard for containers
- **i18n:** `t(key)` function, translations in 3 locales (zh/en/ja) in `web/src/lib/i18n.ts`
- **Components available:** FilterPill, GlassCard, StatusBadge, StatusDot, AnimatedNumber, PageSkeleton
- **Styling:** All via Tailwind classes referencing CSS variables (`var(--color-*)`)

### API Endpoints Available (from Part 4)
- `GET /api/timeline?after_id=&session_id=&source=&limit=50` → `{events: [...], count, has_more}`
- `GET /api/timeline/sessions?limit=50` → `{sessions: [{session_id, last_active, event_count}]}`

### Key Design Decisions (from product discussion)
- Newest at bottom, optional auto-scroll toggle
- Same session + same hook type collapsed as one group (layer 1)
- Expand group → individual event summaries (layer 2)
- Expand event → full details JSON
- Type label: colored text (distinct hue per type)
- Result: small colored badge
- Session dropdown + hook type multi-select filter at top
- 5s polling interval (hardcoded)
- Rule short_ids clickable → `/rules/{shortId}`

### Color Palette for Event Types (extended from DESIGN.md)
| Source | Color (Tailwind class / CSS var) |
|--------|----------------------------------|
| session_start | `text-sky-400` (accent blue) |
| user_prompt_submit | `text-emerald-400` (green) |
| pre_tool_use | `text-violet-400` (purple) |
| session_end | `text-zinc-400` (neutral gray) |
| cold_pipeline | `text-amber-400` (warm gold) |
| cli_* | `text-orange-400` (orange) |
| maintenance | `text-teal-400` (teal) |

### Outcome Badges
| Outcome Pattern | Badge Style |
|-----------------|-------------|
| ok / injected / active | green dot or badge |
| blocked | red badge |
| passed_* / noop | dim gray |
| *_failed / rejected | amber/red |
| pending | blue pulsing |

---

## File Structure

| File | Responsibility |
|------|---------------|
| `web/src/pages/Activity.tsx` (NEW) | Page with two tabs; Timeline content here |
| `web/src/components/TimelineEvent.tsx` (NEW) | Single event row (collapsed/expanded) |
| `web/src/components/TimelineGroup.tsx` (NEW) | Grouped events (same session + source) |
| `web/src/hooks/usePolling.ts` (NEW) | Polling hook with 5s interval |
| `web/src/App.tsx` | Add route |
| `web/src/components/Layout.tsx` | Add nav item |
| `web/src/lib/i18n.ts` | Add translation keys |
| `web/src/lib/types.ts` | Add TimelineEvent type |

---

## Task 1: Types and Polling Hook

**Files:**
- Modify: `web/src/lib/types.ts` — add `TimelineEvent` and `TimelineSession` types
- Create: `web/src/hooks/usePolling.ts` — polling hook

### TimelineEvent type:
```typescript
export interface TimelineEvent {
  id: string
  session_id: string | null
  source: string
  outcome: string | null
  prompt_snippet: string | null
  details: Record<string, unknown> | null
  created_at: string
}

export interface TimelineSession {
  session_id: string
  last_active: string
  event_count: number
}
```

### usePolling hook:
```typescript
export function usePolling<T>(
  fetcher: () => Promise<T>,
  intervalMs: number,
  deps: unknown[] = []
): { data: T | null; isLoading: boolean }
```
- Calls fetcher immediately, then every `intervalMs`
- Cancels on unmount or deps change
- Returns latest data + loading state

---

## Task 2: TimelineEvent and TimelineGroup Components

**Files:**
- Create: `web/src/components/TimelineEvent.tsx`
- Create: `web/src/components/TimelineGroup.tsx`

### TimelineEvent:
- Collapsed: `[time] [colored source label] [outcome badge] [prompt snippet preview]`
- Expanded: full details JSON in `<pre>` + rule links
- Toggle via chevron icon (CaretDown from Phosphor)
- Details JSON rendered as key-value pairs with syntax highlighting

### TimelineGroup:
- Header: `[source type colored] × {count} events [latest time]`
- Collapsed: just header
- Expanded: list of TimelineEvent components (each also collapsible)
- Animation: motion.div with height auto transition

---

## Task 3: Activity Page with Timeline Tab

**Files:**
- Create: `web/src/pages/Activity.tsx`
- Modify: `web/src/App.tsx` — add lazy import + route
- Modify: `web/src/components/Layout.tsx` — add nav item
- Modify: `web/src/lib/i18n.ts` — add keys

### Page structure:
```
┌─ Filter bar: [Session dropdown] [Type multi-select pills] [Auto-scroll toggle]
├─ Tab bar: [Timeline (active)] [Nokori Dashboard (placeholder)]
└─ Timeline content:
    ├─ TimelineGroup (session_start × 1)
    ├─ TimelineGroup (user_prompt_submit × 5)
    ├─ TimelineGroup (pre_tool_use × 23)
    ├─ TimelineGroup (session_end × 1)
    └─ [Auto-scroll anchor]
```

### Grouping logic:
- Group consecutive events with same `session_id` AND same `source`
- If `session_id` is null, group by `source` alone
- Groups break when either session_id or source changes

### Polling:
- Fetch `/api/timeline?after_id={lastId}&limit=50` every 5s
- Append new events to local state
- If auto-scroll enabled, scroll to bottom after append

### Filter state:
- Session dropdown: fetched from `/api/timeline/sessions`
- Source pills: hardcoded list of known sources
- When filter changes, reset event list and fetch fresh

---

## Task 4: Verify Build

- Run: `cd web && npm run build`
- Expected: no TypeScript errors, bundle compiles

---

## Task 5: OCR Review + Commit

Standard flow per CLAUDE.md.
