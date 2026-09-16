---
name: Model Harbor
description: Native macOS connections and a warm editorial public website.
colors:
  native-accent: "rgb(24% 55% 94%)"
  site-paper: "#f5f3eb"
  site-ink: "#193b3b"
  site-muted: "#516661"
  site-teal: "#075154"
  site-teal-hover: "#063e40"
  site-line: "#cbd3c9"
  site-mint: "#d6e8da"
  site-gold: "#f5bc53"
  site-dark: "#142f30"
  site-light: "#edf4ec"
  site-focus: "#b26b06"
typography:
  native-provider-title:
    fontFamily: "macOS system"
    fontSize: "22pt"
    fontWeight: 600
  native-header:
    fontFamily: "macOS system"
    fontSize: "16pt"
    fontWeight: 600
  native-model:
    fontFamily: "macOS system"
    fontSize: "13pt"
    fontWeight: 400
  native-status:
    fontFamily: "macOS system"
    fontSize: "12pt"
    fontWeight: 500
  native-footer:
    fontFamily: "macOS system"
    fontSize: "11pt"
    fontWeight: 400
  native-tab:
    fontFamily: "macOS system"
    fontSize: "10pt"
    fontWeight: 500
  site-display:
    fontFamily: "Newsreader, Georgia, serif"
    fontSize: "clamp(55px, 6.2vw, 82px)"
    fontWeight: 450
    lineHeight: 1.04
    letterSpacing: "-0.037em"
  site-heading:
    fontFamily: "Newsreader, Georgia, serif"
    fontSize: "clamp(36px, 4vw, 54px)"
    fontWeight: 450
    lineHeight: 1.04
    letterSpacing: "-0.032em"
  site-body:
    fontFamily: "Instrument, sans-serif"
    fontSize: "17px"
    fontWeight: 400
    lineHeight: 1.65
  site-button:
    fontFamily: "Instrument, sans-serif"
    fontSize: "15px"
    fontWeight: 600
rounded:
  native-tab: "9pt"
  site-button: "8px"
  site-select: "6px"
  site-container: "12px"
  site-demo: "16px"
spacing:
  native-tabs: "4pt"
  native-row: "7pt"
  native-header-gap: "10pt"
  native-content: "18pt"
  site-paragraph: "18px"
  site-demo: "26px"
components:
  native-provider-tab-selected:
    backgroundColor: "rgb(24% 55% 94% / 20%)"
    typography: "{typography.native-tab}"
    rounded: "{rounded.native-tab}"
    padding: "10pt 0"
  native-provider-title:
    typography: "{typography.native-provider-title}"
  native-model-row:
    typography: "{typography.native-model}"
    padding: "7pt 0"
  native-panel:
    width: "410pt"
  native-toolbar:
    height: "58pt"
    padding: "0 18pt"
  native-footer:
    typography: "{typography.native-footer}"
    height: "42pt"
    padding: "0 18pt"
  site-button-primary:
    backgroundColor: "{colors.site-teal}"
    textColor: "{colors.site-paper}"
    typography: "{typography.site-button}"
    rounded: "{rounded.site-button}"
    padding: "11px 20px"
  site-button-primary-hover:
    backgroundColor: "{colors.site-teal-hover}"
  site-demo:
    backgroundColor: "{colors.site-dark}"
    textColor: "{colors.site-light}"
    rounded: "{rounded.site-demo}"
    padding: "26px"
---

# Design System: Model Harbor

## Overview

**Creative North Star: "The native provider inspector"**

The macOS app uses a compact provider inspector with a toolbar, provider tabs, connection state, recent activity, and flat model rows. Adaptive system surfaces and native controls carry most of the interface. Cool blue identifies the selected tab and primary actions. The navy/platinum aperture monogram supplies the app's identity.

The public website keeps its warm paper background, teal reading colors, and editorial Newsreader headings. It shares the new mark with the app. Its interactive task-routing demonstration explains the product and remains labeled as illustrative. Keep the native and website rules scoped to their platforms.

**Key Characteristics:**

- Native system typography and keyboard behavior for the macOS utility.
- A readable selected tab with primary text on a lightly tinted background.
- Connection labels, symbols, and activity values drawn from current bridge state.
- A shared navy/platinum mark with separate native and website layouts.

This record follows the current SwiftUI source, `ProviderPresentation.swift`, `AppStore.swift`, the accent asset, and `site/styles.css`. The direction is recorded in `.impeccable/surfaces/connections.md`. Review captures in `.impeccable/review` are evidence of the versions captured, not approval of every later source revision. Generated visual studies are direction material, not production screenshots.

## Colors

The native panel uses adaptive macOS neutrals with one cool blue accent. The website uses warm paper, dark teal, mint, and a small amount of amber.

### Primary

The native accent comes from the asset catalog. Selected provider tabs use this accent at 20% opacity and retain `Color.primary` text. Primary buttons use SwiftUI's bordered-prominent style. Website primary actions use `site-teal`, with `site-teal-hover` on hover.

### Secondary

The website's `site-gold` marks illustrative task activity and text selection. `site-mint` provides a pale supporting background. These colors do not define native connection states.

### Neutral

Native backgrounds use `NSColor.windowBackgroundColor`. Text uses `Color.primary` and `Color.secondary`; dividers use the native `Divider`. These adapt to system, light, and dark appearance. Their resolved colors are deliberately absent from the static palette. Native green, orange, and red carry connected, request-failure, and error states, accompanied by text or symbols.

Website paper, ink, muted text, and line tokens define the reading surface. The dark demo uses `site-dark` and `site-light`. The focus token supplies the website's visible keyboard outline.

**The Platform Palette Rule.** Keep the cool native accent and the warm website palette in their respective interfaces. The shared icon does not require matching page backgrounds.

**The Readable Selection Rule.** Selected provider labels and symbols use primary text on the accent tint. Do not color the selected label blue or teal against that tint.

## Typography

Native typography uses the macOS system typeface. Provider titles, toolbar titles, model rows, status text, footer text, and tabs use the roles in frontmatter. Supporting copy uses SwiftUI's semantic caption, subheadline, and headline styles. Native measurements are points. Preserve native control sizing and font metrics.

Website display and section headings use self-hosted Newsreader. Reading text and navigation use self-hosted Instrument Sans, registered as `Instrument` in CSS. Code uses system monospace. Website measurements are CSS pixels unless a fluid expression is specified. Paragraphs have a maximum width of 70ch.

**The Native Type Rule.** Use platform system typography for the macOS utility. Editorial website headings belong on the website.

## Layout

The native panel has a fixed width from `native-panel` and a measured content height. The toolbar and footer remain outside the scrolling body. The body has equal content insets and uses open vertical stacks instead of a grid of cards.

The app measures its body and limits that height to the larger of 220 points or the current screen's visible height minus 156 points. It requests a window height of the fitted body plus 102 points. Scroll indicators appear when content exceeds that limit. Long help and error text can wrap. Native window chrome remains controlled by macOS.

Provider tabs share the available width. Each tab places a symbol above its name and state dot. The selected provider's title and connection label sit on one line of the layout. Activity follows, then a divider, the full saved model list, task-selection guidance, and connection actions. Settings has separate Menu bar, Providers, and Advanced segments.

The website's main container is at most 1200 pixels wide with 56-pixel side margins. It tightens at 1000 pixels, and changes to a single-column layout with 20-pixel side margins at 720 pixels. A wider layout adjustment starts at 1450 pixels. Native panel sizing does not use these website breakpoints.

## Elevation & Depth

The native panel uses system window chrome, dividers, and selected-tab tint. It has no authored card shadows or blur layers. The generated icon has a beveled navy tile and dimensional platinum emblem; that rendering belongs to the icon.

The website uses a single diffuse shadow beneath its dark routing demonstration. Its exact value lives in the sidecar. Other sections rely on background color, borders, and spacing. The demonstration briefly changes a select's background after a routing choice. Reduced-motion preferences disable animation and smooth scrolling. The native app defines no custom transition animation in this panel.

## Shapes

Native provider tabs use the rounded shape in frontmatter. Model rows remain flat. Five-point state dots appear in tabs and a six-point bridge dot appears in the footer. Buttons, fields, pickers, checkboxes, and menus retain native macOS geometry.

The public website uses modestly rounded buttons and code containers, with a larger radius for the routing demonstration. On narrow screens, the demonstration uses the standard website container radius.

The shared identity is an H-like aperture with two platinum piers and an ice-blue connecting bar on a navy rounded square. The generated master is `assets/brand/harbor-icon-master.png`; `assets/brand/harbor-icon-master.prompt.txt` records its exact image-generation prompt. `scripts/render-icon.swift` derives app and public raster assets from that master. The old teal branching mark is retired. Preserve this provenance when replacing or exporting the mark.

## Components

### Provider navigation

Tabs are plain native buttons with SF Symbols, provider names, and state dots. Unselected tabs use secondary text and a clear background. Selected tabs follow the readable selection rule. Each accessible label includes the provider name and connection state. Visible-provider settings leave at least one provider available.

### Connection and activity

Connection text uses the provider's readiness check. Codex can show Configured while waiting for its first completed upstream response. The connection count excludes that unverified state. Working appears while requests are active. Activity reports in-progress requests, the last response, a more recent failure, and actual completed session requests. It does not display quota, balance, or billing estimates.

### Model rows

A small cube symbol precedes the model name. Names can use two lines; Baseten reasoning labels align at the trailing edge. The available count comes from the saved model list. Model rows are read-only. Their guidance directs the user to each Codex task's model picker.

### Buttons and fields

Connection actions use native bordered or bordered-prominent buttons. Existing Baseten and OpenRouter connections use a Manage connection menu. Settings uses native segmented pickers, toggles, and menus. OpenRouter starts with a secure key field and verification action. Successful verification reveals search, checkboxes, seven models per page, and a save action. Editing the key removes the verified state. Empty, loading, disconnected, and error states remain visible in text.

### Menu bar

The menu-bar label can show connection, activity, last requested model, connected-provider count, Harbor name, or icon only. Connection follows the selected provider; activity follows the most recent request's provider. The symbol changes from `h.square` to `waveform` during activity. This uses native SF Symbols, not a reduced raster copy of the app icon. Label text is limited to one line and truncates in the middle.

### Public website demonstration

The dark demonstration contains real HTML selects. Each changes only its own illustrative task. Primary website links have the button geometry and hover state in frontmatter. All interactive elements receive visible focus. Keep source access, attribution, limitations, and setup readable outside the demonstration.

The sidecar contains browser-renderable component previews. Native previews explain structure and roles; browser form controls cannot reproduce AppKit rendering exactly and are not app screenshots. Sidecar tonal ramps are generated swatch previews, not additional colors used by the app.

## Do's and Don'ts

### Do:

- Do use native system colors, fonts, keyboard behavior, and control sizing in the macOS app.
- Do pair provider status colors with visible labels and accessible descriptions.
- Do derive activity and connection counts from actual provider state.
- Do keep the native panel fitted to content until the screen requires scrolling.
- Do retain the generated icon master and its prompt with every derivative workflow.
- Do preserve the website's warm editorial system and clearly illustrative task demo.

### Don't:

- Don't restore the teal branching-route mark or treat old captures as the current identity.
- Don't use tinted text for the selected provider tab.
- Don't present configured Codex credentials as a verified connection before a completed response.
- Don't invent quota bars, account balances, or request totals.
- Don't make Harbor's default or selected provider imply one active model across all Codex tasks.
- Don't describe generated studies, browser previews, or stale review captures as approved production screenshots.
