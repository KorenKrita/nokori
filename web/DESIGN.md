# DESIGN.md

> 精密暗黑——深空控制台里的工程美学，每个像素都像 BMW M 的碳纤维内饰一样有存在的理由。

## 1. Visual Theme & Atmosphere

**Style**: Dark Precision (暗黑精密)
**Keywords**: 深邃、精密、工业、克制发光、碳纤维、控制台、angular
**Tone**: 冷静的工程信心 — NOT 霓虹喧嚣、NOT 毛玻璃梦幻、NOT 圆润友好
**Feel**: 走进 BMW M 的夜间驾驶座舱——仪表在黑暗中精确发光，每个数字都有它该在的位置

**Interaction Tier**: L2 流畅交互
**Dependencies**: motion/react (已有) + CSS custom properties + IntersectionObserver

**参考融合**:
- 从 BMW M 取：zero border-radius、weight extremes (300/700/900)、tight line-heights、单一强调色克制使用
- 从 Nokori 现有保留：暗色 glass 表面、CSS 变量体系、MeshBackground 氛围层、spotlight hover

## 2. Color Palette & Roles

```css
:root.dark, :root:not(.light) {
  /* Backgrounds */
  --bg: #050505;                              /* 深空底色 */
  --surface: rgba(255, 255, 255, 0.03);       /* 卡片/容器 */
  --surface-alt: rgba(255, 255, 255, 0.015);  /* 交替区域 */
  --surface-hover: rgba(255, 255, 255, 0.06); /* 悬停态表面 */

  /* Borders */
  --border: rgba(255, 255, 255, 0.08);        /* 默认边框 */
  --border-hover: rgba(255, 255, 255, 0.16);  /* 悬停边框 */
  --border-focus: rgba(56, 189, 248, 0.5);    /* 焦点环 */

  /* Text */
  --text: #ffffff;                            /* 标题、重要文字 */
  --text-secondary: #a1a1aa;                  /* 正文、描述 */
  --text-tertiary: #71717a;                   /* 标签、辅助信息 */
  --text-muted: #52525b;                      /* 最弱文字 */

  /* Accent — 单一强调色，BMW 式克制 */
  --accent: #38bdf8;                          /* Sky blue — 仅用于交互元素 */
  --accent-hover: #7dd3fc;                    /* 强调色 hover */
  --accent-glow: rgba(56, 189, 248, 0.15);   /* 发光辅助 */

  /* Semantic */
  --success: #34d399;                         /* Emerald */
  --error: #fb7185;                           /* Rose */
  --warning: #fbbf24;                         /* Amber */
  --info: #a78bfa;                            /* Violet */

  /* RGB variants for rgba() */
  --bg-rgb: 5, 5, 5;
  --accent-rgb: 56, 189, 248;
  --success-rgb: 52, 211, 153;
  --error-rgb: 251, 113, 133;

  color-scheme: dark;
}

:root.light {
  --bg: #f8f9fa;
  --surface: #ffffff;
  --surface-alt: #f3f4f6;
  --surface-hover: #e5e7eb;

  --border: rgba(0, 0, 0, 0.10);
  --border-hover: rgba(0, 0, 0, 0.20);
  --border-focus: rgba(56, 189, 248, 0.5);

  --text: #111827;
  --text-secondary: #4b5563;
  --text-tertiary: #9ca3af;
  --text-muted: #d1d5db;

  --accent: #0284c7;
  --accent-hover: #0369a1;
  --accent-glow: rgba(2, 132, 199, 0.08);

  --success: #059669;
  --error: #e11d48;
  --warning: #d97706;
  --info: #7c3aed;

  --bg-rgb: 248, 249, 250;
  --accent-rgb: 2, 132, 199;

  color-scheme: light;
}
```

**Color Rules:**
- 所有颜色通过 CSS 变量引用，禁止硬编码 hex
- Accent 仅用于可交互元素（链接、按钮、活跃态指示器）——参考 BMW Blue 原则
- 语义色（success/error/warning）仅用于状态表达，禁止作为装饰色
- 发光效果统一用 `--accent-glow` 变量，禁止随意定义新的 glow 色值

## 3. Typography Rules

**Font Stack:**
```css
@import url('https://fonts.googleapis.com/css2?family=Geist:wght@300;400;500;600;700;900&family=Geist+Mono:wght@400;500&display=swap');

/* 实际使用本地字体（已在 index.html 预加载） */
--font-sans: "Geist", ui-sans-serif, system-ui, -apple-system, sans-serif;
--font-mono: "Geist Mono", ui-monospace, SFMono-Regular, monospace;
```

| Role | Font | Size | Weight | Line Height | Letter Spacing |
|------|------|------|--------|-------------|----------------|
| Hero H1 | Geist | 48px (3rem) | 300 | 1.1 | -0.03em |
| Page Title H2 | Geist | 28px (1.75rem) | 600 | 1.2 | -0.02em |
| Section H3 | Geist | 11px (0.6875rem) | 500 | 1.3 | 0.08em |
| Body | Geist | 14px (0.875rem) | 400 | 1.5 | 0 |
| Label / Caption | Geist | 12px (0.75rem) | 500 | 1.3 | 0.04em |
| Data / Numbers | Geist Mono | 14-48px | 500 | 1.1 | -0.02em |
| Code | Geist Mono | 12px (0.75rem) | 400 | 1.6 | 0 |

**Typography Rules:**
- Section H3（分类标题）用 **全大写 + 超宽字距 (0.08em)**——BMW 式 label 处理
- 数字用 Mono + `font-variant-numeric: tabular-nums`——保证数据对齐
- Hero/Page Title 用 Light/Semibold weight，正文用 Regular——参考 BMW 300/400 反差
- 行高全局偏紧（1.1-1.5），参考 BMW 1.15-1.30 压缩感
- **NEVER use**: Comic Sans, Papyrus, Impact, 任何 cursive/fantasy 字体

**Text Decoration:**
- Hero h1: subtle glow (`text-shadow: 0 0 60px rgba(var(--accent-rgb), 0.2)`)——暗色底 + 大字号触发
- Section H3: 无渐变、无投影（克制 label 不加装饰）
- Body: 无任何装饰

## 4. Component Stylings

### Cards (GlassCard)
```css
.card {
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: 4px;                   /* BMW angular: 从 16px 收到 4px */
  padding: 20px;
  position: relative;
  overflow: hidden;
  transition: border-color 0.3s cubic-bezier(0.32, 0.72, 0, 1),
              transform 0.4s cubic-bezier(0.32, 0.72, 0, 1);
}
.card:hover {
  border-color: var(--border-hover);
  transform: scale(1.004);
}
.card:focus-within {
  border-color: var(--border-focus);
  outline: none;
  box-shadow: 0 0 0 2px var(--accent-glow);
}
/* Spotlight overlay — 鼠标跟随 */
.card::before {
  content: '';
  position: absolute;
  inset: 0;
  border-radius: inherit;
  background: radial-gradient(
    350px circle at var(--mouse-x, 50%) var(--mouse-y, 50%),
    var(--accent-glow),
    transparent 60%
  );
  opacity: 0;
  transition: opacity 0.3s;
  pointer-events: none;
}
.card:hover::before {
  opacity: 1;
}
```

### Buttons
```css
.btn-primary {
  font-family: var(--font-sans);
  font-size: 13px;
  font-weight: 600;
  letter-spacing: 0.02em;
  padding: 8px 16px;
  border-radius: 4px;
  background: var(--accent);
  color: #000000;
  border: none;
  cursor: pointer;
  transition: background 0.2s, transform 0.2s, box-shadow 0.2s;
}
.btn-primary:hover {
  background: var(--accent-hover);
  transform: translateY(-1px);
  box-shadow: 0 4px 12px rgba(var(--accent-rgb), 0.25);
}
.btn-primary:active {
  transform: translateY(0);
  box-shadow: none;
}
.btn-primary:focus-visible {
  outline: 2px solid var(--accent);
  outline-offset: 2px;
}
.btn-primary:disabled {
  opacity: 0.4;
  cursor: not-allowed;
  transform: none;
  box-shadow: none;
}

.btn-ghost {
  font-family: var(--font-sans);
  font-size: 13px;
  font-weight: 500;
  padding: 8px 16px;
  border-radius: 4px;
  background: transparent;
  color: var(--text-secondary);
  border: 1px solid var(--border);
  cursor: pointer;
  transition: color 0.2s, border-color 0.2s, background 0.2s;
}
.btn-ghost:hover {
  color: var(--text);
  border-color: var(--border-hover);
  background: var(--surface-hover);
}
.btn-ghost:active {
  background: var(--surface);
}
.btn-ghost:focus-visible {
  outline: 2px solid var(--accent);
  outline-offset: 2px;
}
.btn-ghost:disabled {
  opacity: 0.4;
  cursor: not-allowed;
}
```

### Navigation (Sidebar)
```css
.nav-item {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 8px 12px;
  border-radius: 4px;
  font-size: 13px;
  font-weight: 400;
  color: var(--text-secondary);
  text-decoration: none;
  transition: color 0.25s, background 0.25s;
  position: relative;
}
.nav-item:hover {
  color: var(--text);
  background: rgba(255, 255, 255, 0.04);
}
.nav-item.active {
  color: var(--text);
  background: rgba(var(--accent-rgb), 0.08);
  font-weight: 500;
}
/* Active indicator — 左侧 2px 竖线 */
.nav-item.active::before {
  content: '';
  position: absolute;
  left: 0;
  top: 4px;
  bottom: 4px;
  width: 2px;
  background: var(--accent);
  border-radius: 1px;
}
```

### Filter Pills
```css
.pill {
  display: inline-flex;
  align-items: center;
  padding: 6px 12px;
  border-radius: 2px;                  /* 几乎直角 */
  font-size: 12px;
  font-weight: 500;
  letter-spacing: 0.02em;
  color: var(--text-secondary);
  cursor: pointer;
  transition: all 0.25s cubic-bezier(0.32, 0.72, 0, 1);
}
.pill:hover {
  color: var(--text);
  background: rgba(255, 255, 255, 0.04);
}
.pill.active {
  background: rgba(255, 255, 255, 0.12);
  color: var(--text);
}
```

### Status Badges
```css
.badge {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 2px 8px;
  border-radius: 2px;
  font-size: 11px;
  font-weight: 500;
  letter-spacing: 0.04em;
  text-transform: uppercase;
}
.badge-active {
  background: rgba(var(--success-rgb), 0.12);
  color: var(--success);
}
.badge-error {
  background: rgba(var(--error-rgb), 0.12);
  color: var(--error);
}
```

### Table
```css
.table {
  width: 100%;
  font-size: 13px;
  border-collapse: collapse;
}
.table th {
  font-size: 11px;
  font-weight: 500;
  letter-spacing: 0.08em;
  text-transform: uppercase;
  color: var(--text-tertiary);
  text-align: left;
  padding: 12px 8px;
  border-bottom: 1px solid var(--border);
}
.table td {
  padding: 12px 8px;
  border-bottom: 1px solid rgba(255, 255, 255, 0.04);
  color: var(--text-secondary);
}
.table tr:hover td {
  background: rgba(255, 255, 255, 0.02);
}
```

### Input Fields
```css
.input {
  font-family: var(--font-sans);
  font-size: 13px;
  padding: 8px 12px;
  border-radius: 4px;
  background: var(--surface);
  border: 1px solid var(--border);
  color: var(--text);
  transition: border-color 0.2s, box-shadow 0.2s;
}
.input:hover {
  border-color: var(--border-hover);
}
.input:focus {
  border-color: var(--accent);
  box-shadow: 0 0 0 3px var(--accent-glow);
  outline: none;
}
.input:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}
.input::placeholder {
  color: var(--text-muted);
}
```

## 5. Layout Principles

**Container:**
- Max width: 1400px (已有)
- Page padding: 24px
- Narrow variant (Config text-heavy): 960px

**Spacing Scale (4px base):**
- Section gap: 24px
- Component gap (grid): 16px
- Card internal padding: 20px
- Inline element gap: 8px
- Dense spacing (within cards): 12px

**Grid:**
```css
.grid {
  display: grid;
  grid-template-columns: repeat(12, 1fr);
  gap: 16px;
}
```

**Sidebar:**
- Fixed width: 240px (15rem)
- Background: glass surface + backdrop-blur-xl
- Separator: 1px border-right

## 6. Depth & Elevation

| Level | Treatment | Use |
|-------|-----------|-----|
| Base | 无阴影，纯 `--bg` | 页面背景 |
| Surface | 1px border + glass | 卡片、sidebar |
| Elevated | 1px border + spotlight glow on hover | 活跃卡片 |
| Floating | `0 8px 32px rgba(0,0,0,0.5)` | 对话框、弹层 |
| Focus | `0 0 0 2px var(--accent-glow)` | 键盘焦点环 |

**Shadow Philosophy**: 参考 BMW——阴影几乎不用。深度通过**边框对比度**和**发光强度**建立，而非传统 drop-shadow。暗色主题下阴影不可见，用 glow 代替。

## 7. Animation & Interaction

**Motion Philosophy**: 精密克制——像 BMW 仪表指针的归位，快准稳，无多余弹跳。
**Tier**: L2 (流畅交互)
**Easing**: `cubic-bezier(0.32, 0.72, 0, 1)` (全局统一，称为 "precision ease")

### Dependencies
```
motion/react (已安装，v12.40+)
```

### Entrance — Page Level
```tsx
// 页面入场: fade + translateY + blur
const pageVariants = {
  hidden: { opacity: 0, y: 16, filter: 'blur(6px)' },
  show: {
    opacity: 1, y: 0, filter: 'blur(0px)',
    transition: { duration: 0.5, ease: [0.32, 0.72, 0, 1] }
  }
}
```

### Entrance — Stagger Cards
```tsx
const stagger = {
  hidden: {},
  show: { transition: { staggerChildren: 0.06 } }
}
const cardVariant = {
  hidden: { opacity: 0, y: 20, filter: 'blur(6px)', scale: 0.97 },
  show: {
    opacity: 1, y: 0, filter: 'blur(0px)', scale: 1,
    transition: { duration: 0.55, ease: [0.32, 0.72, 0, 1] }
  }
}
```

### Scroll Reveal (L2 必备 — 当前缺失)
```tsx
// 所有非首屏内容使用 whileInView
<motion.div
  initial={{ opacity: 0, y: 24 }}
  whileInView={{ opacity: 1, y: 0 }}
  viewport={{ once: true, margin: '-60px' }}
  transition={{ duration: 0.5, ease: [0.32, 0.72, 0, 1] }}
/>
```

### Sidebar Active Indicator (layoutId 滑动)
```tsx
// 活跃导航项的背景 pill 跟随切换
{isActive && (
  <motion.div
    layoutId="nav-active-pill"
    className="absolute inset-0 rounded bg-[rgba(var(--accent-rgb),0.08)]"
    transition={{ type: 'spring', stiffness: 500, damping: 35 }}
  />
)}
```

### Number Pulse (数值变化反馈)
```tsx
// 数值变化时短暂闪烁对应语义色
<motion.span
  key={value}
  initial={{ color: delta > 0 ? 'var(--success)' : 'var(--error)' }}
  animate={{ color: 'var(--text)' }}
  transition={{ duration: 1.2 }}
/>
```

### Hero Title — SplitText Stagger (Signature Moment #1)
```tsx
// Dashboard 标题逐字入场
const chars = title.split('')
chars.map((char, i) => (
  <motion.span
    key={i}
    initial={{ opacity: 0, y: 20, filter: 'blur(4px)' }}
    animate={{ opacity: 1, y: 0, filter: 'blur(0px)' }}
    transition={{ duration: 0.4, delay: i * 0.03, ease: [0.32, 0.72, 0, 1] }}
  />
))
```

### SpotlightCard Hover (Signature Moment #2)
```tsx
// 鼠标跟随聚光灯 — 已有，保留并增强边框 glow
// 增加边框方向性发光
style={{
  borderImage: `radial-gradient(
    200px circle at ${x}px ${y}px,
    rgba(var(--accent-rgb), 0.3),
    var(--border) 60%
  ) 1`,
}}
```

### Skeleton Shimmer (替代 pulse)
```css
@keyframes shimmer {
  from { background-position: -200% 0; }
  to { background-position: 200% 0; }
}
.skeleton {
  background: linear-gradient(
    90deg,
    var(--surface) 25%,
    rgba(255, 255, 255, 0.06) 50%,
    var(--surface) 75%
  );
  background-size: 200% 100%;
  animation: shimmer 1.5s ease-in-out infinite;
  border-radius: 4px;
}
```

### Page Transition (AnimatePresence)
```tsx
<AnimatePresence mode="wait">
  <motion.div
    key={pathname}
    initial={{ opacity: 0, x: 8 }}
    animate={{ opacity: 1, x: 0 }}
    exit={{ opacity: 0, x: -8 }}
    transition={{ duration: 0.25, ease: [0.32, 0.72, 0, 1] }}
  />
</AnimatePresence>
```

### Hover & Focus States
```css
/* 所有可交互元素的统一 hover 升格 */
[data-interactive]:hover {
  --border-color: var(--border-hover);
}
/* Focus-visible ring */
:focus-visible {
  outline: 2px solid var(--accent);
  outline-offset: 2px;
}
/* 按钮 hover 上浮 */
button:hover {
  transform: translateY(-1px);
}
button:active {
  transform: translateY(0);
}
```

### Reduced Motion
```css
@media (prefers-reduced-motion: reduce) {
  *, *::before, *::after {
    animation-duration: 0.01ms !important;
    animation-iteration-count: 1 !important;
    transition-duration: 0.15s !important;
  }
  .mesh-bg { display: none; }
}
```

### Signature Moments 清单 (L2 最低 4 个)

| # | 位置 | 效果 | 来源 |
|---|------|------|------|
| 1 | Dashboard 标题 | SplitText 逐字入场 + subtle glow | reactbits: SplitText |
| 2 | 所有 GlassCard | Spotlight hover + 边框方向 glow | reactbits: SpotlightCard |
| 3 | Sidebar 切换 | layoutId spring 滑动 | motion/react |
| 4 | 数字变化 | AnimatedNumber + 语义色脉冲 | 自定义 |
| 5 | 骨架屏 | 方向性 shimmer | CSS keyframes |
| 6 | 页面切换 | AnimatePresence fade+slide | motion/react |

## 8. Do's and Don'ts

### Do
- 用 CSS 变量引用所有颜色，暗/亮双主题通过变量切换
- 保持全局统一的 precision ease `cubic-bezier(0.32, 0.72, 0, 1)`
- 数据数字用 Geist Mono + `tabular-nums`
- Section 标题用全大写 + 宽字距 (0.08em) 标签风格
- 边框用于建立层次，而非阴影
- 每个可交互元素必须有 hover + focus-visible 态
- 动效持续时间保持 0.2s-0.6s 区间（绝不超过 1s）
- 响应式断点下简化动效（移动端关闭 MeshBackground）

### Don't
- ❌ 圆角不超过 4px（从当前 16px 收紧到 BMW angular 标准）——pills/badges 例外用 2px
- ❌ 不使用 `border-radius: 9999px` 圆形元素（ThemeSwitcher 除外）
- ❌ 不在暗色主题用 drop-shadow（暗色底阴影不可见，用 glow/border 代替）
- ❌ Accent 色禁止用于大面积背景或装饰性元素
- ❌ 不用弹性/回弹动画 (spring bounce)——用 precision ease 代替
- ❌ 不用 `animate-pulse`——用 shimmer 代替
- ❌ 不在移动端启用 spotlight hover（无鼠标）
- ❌ 禁止超过 3 种颜色同时出现在同一个卡片内
- ❌ 不用 `filter: blur()` 在运动元素上（性能红线）
- ❌ 不在正文段落添加任何文字装饰（渐变/投影/动画）
- ❌ 行高禁止超过 1.6（保持压缩感）

## 9. Responsive Behavior

**Breakpoints:**
| Name | Width | Key Changes |
|------|-------|-------------|
| Desktop L | > 1400px | 12-col grid, sidebar 240px, max-width 1400 |
| Desktop | 1024-1400px | 12-col grid, sidebar 240px |
| Tablet | 768-1024px | Sidebar → top bar + hamburger, 6-col grid |
| Mobile | < 768px | 单列, 无 sidebar, 底部 tab bar |

**Touch Targets:** minimum 44×44px
**Collapsing Strategy:**
- Sidebar: Desktop 固定侧边 → Tablet 顶部 navbar + hamburger → Mobile 底部 tab bar
- Dashboard grid: 12-col → 6-col → 单列堆叠
- GlassCard spotlight: Desktop only（移动端关闭鼠标跟随）
- MeshBackground: 移动端隐藏（性能）
- Table: 移动端改为垂直卡片列表

```css
@media (max-width: 1024px) {
  .sidebar { display: none; }
  .main { margin-left: 0; }
  .grid { grid-template-columns: repeat(6, 1fr); }
}

@media (max-width: 768px) {
  .grid { grid-template-columns: 1fr; }
  .mesh-bg { display: none; }
  .card { border-radius: 4px; padding: 16px; }
  .page-title { font-size: 24px; }
}
```

---

## 改进路线图（从当前状态出发）

### Phase 1: Token & Geometry（不改组件逻辑）
- [x] CSS 变量体系 → 已有，保留
- [ ] `border-radius: rounded-2xl` → 全局改为 `rounded` (4px)
- [ ] 补充 `--accent-glow` 等新变量
- [ ] Section 标题样式统一为 uppercase + tracking-wider

### Phase 2: Motion Upgrade（补齐 L2 缺失项）
- [ ] `whileInView` scroll reveal（非首屏元素）
- [ ] Sidebar `layoutId` active indicator
- [ ] PageSkeleton → shimmer 替代 pulse
- [ ] Dashboard 标题 SplitText 入场
- [ ] AnimatePresence 页面切换

### Phase 3: Card Enhancement
- [ ] GlassCard 边框方向 glow
- [ ] AnimatedNumber 变化时语义色脉冲
- [ ] hover border-color 升格

### Phase 4: Polish
- [ ] 空状态 SVG 图标 + 微动画
- [ ] Logs 终端增强（行号 gutter + level badge）
- [ ] MeshBackground opacity 提升至 8%-12%
