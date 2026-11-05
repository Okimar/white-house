# Vendored frontend libraries

No build step and no CDN: these files are served as they are.

| File | Source | License |
|---|---|---|
| `preact-htm.mjs` | npm `htm@3.1.1`, file `preact/standalone.mjs` (Preact core, hooks and htm in one bundle) | htm: Apache-2.0 (`LICENSE.htm`), Preact: MIT (`LICENSE.preact`) |
| `lucide-icons.mjs` | npm `lucide-static@0.460.0`: only the icons the app uses, as data (ADR 0014) | ISC (`LICENSE.lucide`) |

Exports of `preact-htm.mjs`: `h, html, render, Component, createContext, useState, useReducer, useEffect,
useLayoutEffect, useRef, useImperativeHandle, useMemo, useCallback, useContext, useErrorBoundary`.

To update: run `npm pack htm@X` in an empty temp directory, copy `package/preact/standalone.mjs`
here, update the table, then check the UI in a browser.
