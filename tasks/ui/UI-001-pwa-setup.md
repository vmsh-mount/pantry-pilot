# UI-001 — PWA Setup

**Status:** ⏳ Pending  
**Area:** Frontend  
**Depends on:** nothing  
**Blocks:** nothing  

---

## Problem

The app has no `manifest.json`, no PWA meta tags, and no service worker. On Android Chrome, there is no "Add to Home Screen" prompt. When opened from a home screen shortcut, it shows the browser address bar and navigation chrome — it doesn't feel like a native app.

---

## Design Reference

`design/ui/index.html` targets 375px phone width and is intended to be used as a mobile-first PWA. The entire UX assumes full-screen, no browser chrome.

---

## What to Build

### 1. `app/cockpit/public/manifest.json`
```json
{
  "name": "PantryPilot",
  "short_name": "PantryPilot",
  "description": "Your weekly groceries, planned for you.",
  "start_url": "/",
  "display": "standalone",
  "background_color": "#F7F8F5",
  "theme_color": "#2D6A4F",
  "icons": [
    { "src": "/icons/icon-192.png", "sizes": "192x192", "type": "image/png" },
    { "src": "/icons/icon-512.png", "sizes": "512x512", "type": "image/png" },
    { "src": "/icons/icon-512.png", "sizes": "512x512", "type": "image/png", "purpose": "maskable" }
  ]
}
```

### 2. Icons
- `/public/icons/icon-192.png` — 192×192px, green background (#2D6A4F), 🥦 or wordmark
- `/public/icons/icon-512.png` — 512×512px, same

### 3. `app/cockpit/src/app/layout.tsx` — add to `<head>`:
```html
<link rel="manifest" href="/manifest.json" />
<meta name="theme-color" content="#2D6A4F" />
<meta name="mobile-web-app-capable" content="yes" />
<meta name="apple-mobile-web-app-capable" content="yes" />
<meta name="apple-mobile-web-app-status-bar-style" content="default" />
<meta name="apple-mobile-web-app-title" content="PantryPilot" />
<link rel="apple-touch-icon" href="/icons/icon-192.png" />
```

### 4. Service worker (optional for MVP)
Next.js 15 supports `next-pwa` or a custom `public/sw.js`. For MVP, skip offline caching — just the manifest is enough for install prompt.

---

## Files to Touch

| File | Action |
|------|--------|
| `app/cockpit/public/manifest.json` | Create |
| `app/cockpit/public/icons/icon-192.png` | Create |
| `app/cockpit/public/icons/icon-512.png` | Create |
| `app/cockpit/src/app/layout.tsx` | Add meta tags |

---

## Acceptance Criteria

- [ ] Chrome on Android shows "Add to Home Screen" banner or install prompt
- [ ] App opens without browser address bar when launched from home screen
- [ ] `theme-color` matches `#2D6A4F` (green status bar on Android)
- [ ] iOS Safari shows correct icon when added to home screen
