# GOTWiC Scanner — WASM Navigation Recovery Guideline

## Purpose
Restore map navigation after a game build changes the Unity WebAssembly function table. Preserve existing castle decoding, GUI, database, and updater behavior. Never select a replacement function by matching numeric names or nearby table slots alone.

## Verified recovery: 27 October 2026
- Previous working binary: `ws_map_v406_module.wasm`; old navigation slot **62958**, function index/name **145867**.
- Current binary: `gotwic_2.81.0_39b7b26851.wasm`; new navigation slot **62960**, function index/name **145870**.
- Old and new navigation function signatures: `(i32,i32,i32,i32,i32) -> i32`.
- Body sizes: 575 bytes each; raw byte similarity: 88.35%. Similarity is a clue, not proof.
- Verified call: `f(234125,150,200,250,300)` targeting X=125, Y=325 (map ID = Y*720 + X).
- Controlled validation returned `1`, received two incoming map message `9001` frames, and visibly moved the map.
- The previous slot 62958 now resolves to a different `(i32)->void` function; calling it did not navigate.

## Recovery workflow for future updates
1. **Preserve a working reference.** Retain the old `.wasm` and the matching scanner source. Record old game build, SHA-256, function table slot, function index/name, signature, navigation arguments, and expected return value.
2. **Diagnose, don't guess.** Attach to the Unity frame through Chrome CDP; inspect `window.gameInstance.Module.asm.__indirect_function_table`, table length, target slot's function name and arity. Do not assume a moved name still implements the same action.
3. **Identify the new build.** Read the game's loaded build/resource URLs in the Unity execution context. Download the current WASM (and framework JS if needed); validate WASM magic, size, SHA-256 and section structure. Keep the binary for future reference.
4. **Parse both WASM binaries offline.** Read type, import, function, element and code sections. Reconstruct table slot -> function index via element segments (including offset). Note imported-function count when resolving function bodies.
5. **Compare the verified old function with new candidates.** Filter by exact parameter/result types; rank using body size, byte similarity, normalized instruction patterns, call targets and call graph. Check that nearby functions exhibit consistent remapping. Never treat raw similarity as conclusive.
6. **Validate one candidate safely.** Check the live slot identity, callable arity and expected WASM signature. With the map positioned elsewhere, execute one controlled navigation to a known selectable safe location. Record JS return value, incoming map frames, and explicit visual movement confirmation. Do not run a blind batch of candidates.
7. **Patch both preflight and navigation together.** Update the slot and expected function identity in `gotwic_scanner_v2_2.py`. Retain the original five arguments unless independently disproved. Do not change castle protocol decoding as part of a navigation-only recovery.
8. **Verify end-to-end.** Run a normal scan and confirm that map frames arrive, castle records parse, GUI updates, and CSV/SQL behavior is unchanged. A successful map move alone is not proof of a complete scanner recovery.
9. **Release safely.** Increment the application version; package all runtime files, masks, launcher, dependencies, docs, updater, and per-file SHA-256/size manifest. Stage and verify changed files before installation. Do not automatically delete files missing from the manifest. Publish `version.json` last.

## Safety/diagnostic limits
- Highest selectable coordinates: X=650, Y=710; internal map-ID stride is 720, not the selectable X maximum.
- Avoid logging authentication tokens, session URLs, or unnecessary network payloads in shared reports.
- Keep diagnostic probes separate from production release files.
- Current release `1.0.7` includes the verified navigation fix; a complete live scanner regression test remains required.
