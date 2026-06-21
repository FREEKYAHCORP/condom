# Lens M0 Chrome extension

Unpacked MV3 extension for the X-only M0/M1 experiment.

## What it does

- Hooks page `fetch`/XHR in the MAIN world and passively copies X response bodies the page already receives.
- Logs DOM fallback candidates and behavior/exposure events.
- Talks only to local core: `http://127.0.0.1:8765`.
- Popup toggles three modes:
  - Native X
  - Cheap reranker
  - MiniMax M3
- In non-native modes it adds rank badges/highlights to visible X articles. Optional experimental DOM reorder is behind a checkbox.

## What it does not do

- No API key in extension.
- No X internal endpoint calls.
- No seal/skin/composer.
- No behavior is sent to the Encounter prompt.

## Run

```powershell
cd path\to\condom
python scripts\init_db.py
python scripts\serve_core.py
```

Then Chrome → `chrome://extensions` → Developer mode → Load unpacked → select:

```text
path\to\condom\extension
```

Open `https://x.com/home`, open the extension popup, start a fresh session if desired, and choose a mode.

## Notes

- Response body capture uses an injected page hook because MV3 `webRequest` does not expose bodies.
- DOM exposure is the native-order spine for M0 scoring.
- Save/bookmark detection is best-effort via X button labels/testids and needs real-session validation.
