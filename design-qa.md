# Design QA — Jarvis Life V5 Cinematic Command Deck

## Comparison target

- Source visual truth: `artifacts/presentation/jarvis-life-product-vision.png`
- Rendered implementation: `artifacts/qa/build5-cinematic-approved-430x932.png`
- Focused source region: `artifacts/qa/reference-core-focus-final.png`
- Focused implementation region: `artifacts/qa/build5-cinematic-approved-core-focus.png`
- Route: `/vida`
- State: authenticated springboard, dark theme, synthetic empty account
- CSS viewport: `430 x 932`
- Implementation pixels: `430 x 932`, density normalized to 1 CSS pixel per captured pixel
- Source pixels: `1536 x 1024`; this is a wide product-vision board, not the same mobile state or crop
- Focused comparison pixels: source `500 x 620`; implementation `430 x 600`

The source is a presentation board containing a framed phone plus six surrounding product panels. The implementation capture is the app viewport itself. Comparison therefore targets the visual language and the phone's central command system, not a false pixel-for-pixel match of the full board.

## Findings

- No actionable P0, P1, or P2 findings remain in the final comparison.
- [P3] The concept phone shows eight illustrative modules; the current product renders six modules backed by live shell routes. This is an intentional truthfulness constraint: unavailable standalone products were not added as decorative fake apps. Health and nutrition remain represented inside the fitness domain until separate routes exist.
- [P3] The concept uses more illustrative, filled pictograms. The implementation uses one consistent Lucide family inside colored multi-ring instrument housings so every visible icon stays sharp, accessible, and interactive at runtime.

## Required fidelity surfaces

- Fonts and typography: Geist is used for display and body hierarchy; compact system telemetry uses the existing monospace stack. Headings, values, module names, status text, wrapping, and truncation were inspected at both tested sizes.
- Spacing and layout rhythm: the command header, 360 summary, orbital system, specialist council, and internal-app panels preserve a clear vertical hierarchy. No horizontal overflow was observed at `430 x 932` or `393 x 852`.
- Colors and visual tokens: black/navy surfaces, cyan instrumentation, blue neural energy, restrained green state signals, and per-module violet/blue/teal/gold accents map to the source direction without generic rounded-card styling.
- Image quality and asset fidelity: the hero is a generated raster neural sphere at `frontend/public/aether-neural-core.png`; radial masking removes the former rectangular crop and no visible halo or stretched asset was observed. The sphere uses `object-fit: contain`.
- Copy and content: labels describe real product state. Connections remain explicitly unavailable until provider/server configuration is present; the interface does not claim integrations are active.
- Icons: all visible product icons come from the same installed Lucide library and use consistent optical size, stroke, glow, color, and circular instrument housing.
- Accessibility: semantic buttons/tabs/regions remain intact, visible interactive targets measured at least `44 x 44` in the final springboard capture, focus styles remain present, and the V5 animation layer is disabled under `prefers-reduced-motion`.

## Full-view comparison evidence

The source and `build5-cinematic-approved-430x932.png` were opened in one comparison input. The final app now shares the defining source traits: a luminous spherical neural core, concentric live instrumentation, circular orbital modules, technical grid, cyan/gold energy accents, and faceted command panels. The app intentionally adds an above-the-fold real-data summary because the user requested a central view of life.

## Focused region comparison evidence

The source core crop and `build5-cinematic-approved-core-focus.png` were opened together in one comparison input. This focused pass checked sphere integration, icon housing, module spacing, orbit geometry, labels, glow strength, and the transition into the specialist panel. No clipping, rectangular image edge, broken icon, or overlapping primary label remained.

## Comparison history

### Pass 1 — blocked

- Earlier evidence: `artifacts/qa/build5-production-springboard.png`
- P1: generic rectangular cards and square icon tiles did not match the orbital presentation language.
- P1: the old lobed neural artwork and visible rectangular crop did not read as a premium living core.
- P2: Connections was visually detached from the orbital app system.
- Fixes: replaced the core asset, rebuilt the shell around concentric instrumentation, added six circular functional nodes, moved Connections into the orbit, and replaced generic surfaces with faceted command panels.

### Pass 2 — blocked

- Evidence: `artifacts/qa/build5-cinematic-login.png`
- P2: the new neural sphere still exposed the dark square edge of its source canvas on the login screen.
- Fix: applied an explicit radial alpha mask with the WebKit-prefixed fallback and changed the asset placement to `object-fit: contain`.
- Post-fix evidence: `artifacts/qa/build5-cinematic-login-v2.png`.

### Pass 3 — passed

- Evidence: `artifacts/qa/build5-cinematic-approved-430x932.png`, `artifacts/qa/build5-cinematic-approved-core-focus.png`, `artifacts/qa/build5-cinematic-finance.png`, and `artifacts/qa/build5-cinematic-connections.png`.
- Fixes since pass 2: added per-module color identity, upgraded circular instrument housings, added live HUD telemetry, removed visible scrollbars, enforced 44px touch targets, and improved internal-app surfaces.
- Browser checks: login/registration, HUD to springboard, springboard to Finanças, `A pagar`, springboard to Conexões, `Como funciona`, back navigation, viewport `430 x 932`, viewport `393 x 852`, horizontal overflow, and console warnings/errors.
- Console result: no warnings or errors in the tested final flows.

## Follow-up polish

- When Saúde, Agenda, and Nutrição become independent functional products, extend the orbit rather than adding decorative placeholders now.
- Add provider brand assets only after each integration is genuinely configured; do not show branded “connected” states before authorization succeeds.

## Final result

final result: passed
