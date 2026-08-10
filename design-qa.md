# Jarvis Life — Design QA

## Comparison target

- Source visual truth: `/var/folders/xq/1qwtfnzj1r7ffk8nsym6zsw00000gn/T/codex-clipboard-40cc3a5d-c216-4279-a061-21af7d6a817f.png`
- Earlier rejected implementation: `/var/folders/xq/1qwtfnzj1r7ffk8nsym6zsw00000gn/T/codex-clipboard-c27c0f91-5272-4299-a745-b2d1d4f560d7.png`
- Current implementation evidence:
  - `docs/qa/jarvis-life-springboard-430x932.png`
  - `docs/qa/jarvis-life-springboard-375x667.png`
  - `docs/qa/jarvis-life-springboard-375x667-bottom.png`
  - `docs/qa/jarvis-life-hud-393x852.png`
  - `docs/qa/jarvis-life-finance-430x932.png`
- Route/state: authenticated Life OS, Jarvis HUD, app springboard and Finance summary.

The source is a 2606 × 1440 desktop control panel. The implementation is an iPhone-first product, so this is an art-direction and system-language comparison, not a literal desktop-layout clone. The user's product decision to keep the five apps orbiting the Jarvis core is intentional.

## Normalization

| Artifact | Pixels | CSS viewport | Density treatment |
|---|---:|---:|---|
| Source control panel | 2606 × 1440 | unknown | viewed at high detail; no pixel-fidelity claim |
| Large phone implementation | 430 × 932 | 430 × 932 | 1 CSS px per captured pixel |
| Compact phone implementation | 375 × 667 | 375 × 667 | 1 CSS px per captured pixel |
| Baseline phone implementation | 393 × 852 | 393 × 852 | 1 CSS px per captured pixel |

The source and implementation were opened together in one comparison input. A browser-hosted composite was blocked by the browser URL policy, so the dedicated local image viewer supplied both original files in the same inspection result.

## Full-view comparison evidence

- The implementation now carries the source's true-black canvas, cyan/green telemetry accents, restrained 28 px grid, hairline panel borders, compact monospaced readouts and dense system-status grouping.
- The source's desktop side rails translate into a mobile status strip, specialist console and per-app telemetry without copying a desktop layout into a phone.
- The generated neural-core image is a real raster asset with clean circular integration. The black rotated square visible in the earlier rejected implementation is absent.
- The source is intentionally denser. On mobile, the central Jarvis core and the five product domains retain priority, while secondary telemetry moves below and remains scrollable.

## Focused-region evidence

Separate crops were unnecessary because the original files made the critical regions legible at inspection size. Focused review covered:

- header/status strip: spacing, mono labels, system state and logout target;
- neural core/orbit: image crop, masking, app-node placement and label hierarchy;
- specialist console: two-column rhythm, wrapping, icon consistency and touch targets;
- Finance window: top bar, segmented navigation, empty state and protection against springboard layer bleed.

## Required fidelity surfaces

- Fonts and typography: Geist Variable is used for primary UI and SF Mono/Roboto Mono fallbacks for telemetry. The first pass exposed 6–7 px operational labels; the current pass raises important labels and specialist text to 7–10 px while preserving the technical hierarchy. Long specialist names wrap instead of truncating.
- Spacing and layout rhythm: the orbit, status strip and specialist console remain aligned at 375, 393 and 430 px. At 375 × 667 the home surface scrolls internally from 667 px to 953 px; the body itself does not overflow horizontally.
- Colors and tokens: black `#040708`, cyan `#2dd9f5`, green `#55e6a5`, restrained translucent panels and low-alpha grid map cleanly to the source. Critical, warning and healthy states remain distinct.
- Image quality and asset fidelity: the Jarvis core uses `frontend/public/aether-neural-core.png`, not CSS art or an inline SVG approximation. Its crop stays circular and sharp in the HUD and springboard evidence.
- Copy and content: Portuguese product copy is standalone and domain-specific. Specialist names make the IA role explicit rather than presenting generic app labels.
- Icons: app and action controls use the existing Lucide icon family with consistent stroke weight. No emoji or text-glyph substitute is visible.
- Accessibility and interaction: visible focus rings remain cyan; all springboard buttons measured at least 44 × 44 px at 375 px width; the compact layout scrolls rather than clipping interactive content.

## Findings and comparison history

### Pass 1 — blocked

- [P1] Neural-core asset exposed a large black rotated rectangle.
  - Evidence: earlier rejected implementation screenshot.
  - Impact: the hero looked pasted on rather than native to the system.
  - Fix: replaced the treatment with a clean circular raster asset integration and screen blend.
  - Post-fix evidence: current HUD and springboard screenshots.
- [P1] App windows allowed the springboard orbit to bleed above the opened app.
  - Evidence: browser-rendered Finance state during the first implementation pass.
  - Impact: broken layer hierarchy made internal apps look unfinished.
  - Fix: raised `.oj-window` to the foreground and gave it an opaque grid-backed surface.
  - Post-fix evidence: `docs/qa/jarvis-life-finance-430x932.png`, with computed `z-index: 10` and no visible bleed.

### Pass 2 — blocked

- [P2] Several mobile telemetry and specialist labels were 6–7 px and too faint.
  - Evidence: first 375 × 667 and 430 × 932 captures.
  - Impact: the technical language survived, but operational labels were needlessly hard to read on a phone.
  - Fix: increased meaningful labels to 7–10 px and raised muted-text opacity without brightening decorative grid lines.
  - Post-fix evidence: current 375 × 667 and 430 × 932 screenshots.

### Pass 3 — passed

- No actionable P0, P1 or P2 visual mismatch remains for the selected mobile art direction.
- P3 polish: the empty `Sinais de hoje` panel is intentionally quiet but can gain compact onboarding guidance when the integration hub is implemented.

## Primary interactions and runtime checks

- registration → Jarvis HUD;
- app-grid control → springboard;
- Finance app → isolated app window;
- `IA` control → Jarvis conversation surface;
- internal scroll at compact height;
- responsive captures at 375 × 667, 393 × 852 and 430 × 932;
- fresh browser tab after the `crypto.randomUUID` fallback: zero console warnings or errors;
- springboard button audit at 375 px: zero targets below 44 × 44 px.

## Implementation checklist

- [x] Remove the black-square hero treatment.
- [x] Enforce app-window foreground layering.
- [x] Improve operational label readability.
- [x] Validate compact and large iPhone widths.
- [x] Validate touch targets and fresh-console state.
- [ ] Add integration-driven content to the empty signals surface in a later product pass.

final result: passed
