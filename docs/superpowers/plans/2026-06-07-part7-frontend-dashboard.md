# Part 7: Frontend Dashboard (Nokori Dashboard) — Detailed Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Build the Nokori Dashboard tab within the Activity page, featuring charts (Recharts), conversion funnel, error aggregation, and time-range filtering.

**Architecture:** Second tab of the Activity page. Multiple sub-tabs within it (Overview, Errors, Rules). Uses `GET /api/monitor/overview`, `GET /api/monitor/errors`, `GET /api/monitor/errors/trend`. Charts rendered via Recharts, styled to match dark precision design system.

**Tech Stack:** React 19, TypeScript, Tailwind CSS 4, Recharts, Motion, existing component library.

---

## Research Findings (Round 2)

### API Endpoints Available (from Part 4)
- `GET /api/monitor/overview?session_id=&since=&until=` → `{total_events, total_errors, events_by_source, events_by_outcome, error_summary, pipeline_funnel}`
- `GET /api/monitor/errors?group_by=role|model_id|error_type|source&session_id=&since=` → `{errors: [{role/model_id/error_type, count}], group_by}`
- `GET /api/monitor/errors/trend?since=&session_id=` → `{trend: [{day, error_type, count}]}`

### Recharts Integration
- Install: `npm install recharts` (add to web/package.json)
- Components needed: `LineChart`, `BarChart`, `PieChart`, `ResponsiveContainer`, `Tooltip`, `Legend`
- Styling: use CSS variables for colors via Recharts `stroke`/`fill` props
- Dark theme: transparent backgrounds, light grid lines (`var(--color-border-subtle)`)

### Design System Constraints (from DESIGN.md)
- Charts must use CSS variable colors (no hardcoded hex)
- Font: Geist Mono for data numbers, `tabular-nums`
- Border-radius: 4px max on chart containers
- Animation: precision ease curve
- Tooltips: dark surface with border, matching GlassCard style
- No bounce animations

### Dashboard Sub-tabs (from product discussion)
1. **Overview** (default): Session activity cards with hover detail, conversion funnel, error pie chart
2. **Errors**: Trend line chart (by day), role × error_type table, model error ranking bar chart
3. (Future): Rule stats — can be placeholder for now

### Time Range Picker
- Preset buttons: 1h, 3h, 1d, 7d, 30d
- Custom: date range calendar picker (can use simple `<input type="date">` for MVP)
- Session filter: same dropdown as Timeline tab (shared state or re-fetched)
- Filter state applies to all sub-tabs

---

## File Structure

| File | Responsibility |
|------|---------------|
| `web/src/pages/Activity.tsx` | Add Dashboard tab content (modify from Part 6) |
| `web/src/components/dashboard/OverviewTab.tsx` (NEW) | Overview sub-tab with stats cards + funnel + pie |
| `web/src/components/dashboard/ErrorsTab.tsx` (NEW) | Error trends + breakdown charts |
| `web/src/components/dashboard/FunnelChart.tsx` (NEW) | Cold pipeline conversion funnel visualization |
| `web/src/components/dashboard/TimeRangePicker.tsx` (NEW) | Time range preset + custom picker |
| `web/package.json` | Add `recharts` dependency |

---

## Task 1: Install Recharts

```bash
cd web && npm install recharts
```

---

## Task 2: TimeRangePicker Component

**File:** `web/src/components/dashboard/TimeRangePicker.tsx`

```typescript
interface TimeRangePickerProps {
  value: string  // ISO since timestamp
  onChange: (since: string) => void
}
```

- Preset buttons: 1h, 3h, 1d, 7d, 30d (calculate ISO from now)
- Active state: highlighted pill style
- Optional custom date inputs

---

## Task 3: OverviewTab Component

**File:** `web/src/components/dashboard/OverviewTab.tsx`

- Stat cards: total_events, total_errors, sessions count (AnimatedNumber component)
- Events by source: horizontal bar chart (Recharts BarChart)
- Pipeline funnel: custom stepped visualization or horizontal bar chart with decreasing widths
- Error pie chart: PieChart with role/type breakdown

---

## Task 4: ErrorsTab Component

**File:** `web/src/components/dashboard/ErrorsTab.tsx`

- Trend chart: LineChart with X=day, Y=count, series per error_type
- Error table: role × model_id × error_type with counts
- Model ranking: horizontal BarChart

---

## Task 5: FunnelChart Component

**File:** `web/src/components/dashboard/FunnelChart.tsx`

Visualizes cold pipeline conversion:
```
Candidates → Admission Judge → Final Judge → Active/Candidate rules
```
- Each stage shows count + percentage of previous stage
- Horizontal bars with decreasing width
- Color gradient from blue (input) to green (output)

---

## Task 6: Wire Dashboard Tab into Activity Page

**Modify:** `web/src/pages/Activity.tsx`

- When "Nokori Dashboard" tab selected, show:
  - TimeRangePicker + Session filter (shared with Timeline tab)
  - Sub-tab navigation: Overview | Errors
  - Active sub-tab content

---

## Task 7: Verify Build + OCR Review + Commit

Standard flow per CLAUDE.md.

---

## Notes

- Recharts responsive containers need explicit height (e.g., 300px) or parent with defined height
- Dark theme: set `<ResponsiveContainer>` with `className` on parent, charts get colors from CSS vars
- For MVP, the funnel can be simple horizontal bars without a dedicated chart library — plain Tailwind divs with percentage widths work well and are more flexible
- Error trend chart may be empty for new installations — handle gracefully with empty state message
