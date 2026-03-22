# Cassandra AI — Post-Mortem & Lessons Learned

## What Went Wrong (The Honest Version)

### 1. The Callback Cascade Bug
**The Problem:** Every render of App.jsx created new function references for `onStateChange`, `onTranscript`, etc. This triggered a chain reaction:
- New callback → new `updateState` → new `stopPipeline` 
- The cleanup `useEffect` saw `stopPipeline` change → ran cleanup
- **Result:** Pipeline destroyed immediately after starting

**Why It Happened:** Original code used inline arrows:
```javascript
// BROKEN — new reference every render
onStateChange: (state) => { setSystemState(state) }
```

**The Fix:** Stable `useCallback` refs + ref storage in hook:
```javascript
// FIXED — same reference forever
const handleStateChange = useCallback((state) => {
  setSystemState(state);
}, []);
```

### 2. The websockets Library API Change
**The Problem:** `websockets` v15 changed `extra_headers` → `additional_headers`. The backend failed to connect to OpenAI.

**Why It Happened:** Library version mismatch — code was written for v14, system had v15.

**The Fix:** Updated `main.py:280`:
```python
# Before: extra_headers=headers  ❌
# After:  additional_headers=headers  ✅
```

### 3. Tunnel Instability
**The Problem:** Cloudflare Quick Tunnels are ephemeral. When the backend restarted, the tunnel URL changed, causing "site can't be reached" errors.

**Why It Happened:** Quick Tunnels have no persistence. Every restart = new URL.

**The Fix:** None (intrinsic to free tier). Workaround: check tunnel status before testing.

### 4. Build/Deploy Confusion
**The Problem:** Changes to frontend code weren't reflected because:
- `dist/` folder wasn't rebuilt
- Or: rebuilt but not copied to root
- Or: backend restarted but frontend was cached

**Why It Happened:** Multiple build steps (npm run build → copy → restart) with no automation.

**The Fix:** Single-command deploy script (see below).

---

## Prevention Checklist

### Before Testing
```bash
# 1. Check backend is running
curl http://localhost:8000/health

# 2. Check tunnel is active (if using)
ps aux | grep cloudflared

# 3. Verify frontend env points to correct URL
cat frontend-react/.env
```

### Making Changes
**If changing frontend code:**
```bash
cd frontend-react
npm run build
cp -r dist/* ../  # Copy to root for serving
cp dist/processors/recorder.js ../processors/  # Sync worklet
```

**If changing backend code:**
```bash
# Kill existing
curl -X POST http://localhost:8000/shutdown  # if endpoint exists
# Or: pkill -f uvicorn

# Restart
source venv/bin/activate
nohup uvicorn main:app --host 0.0.0.0 --port 8000 >> server.log 2>&1 &
```

### Debugging Pipeline Issues
Watch for these console messages:
```
✅ [Capture] Mic access granted.
✅ [Capture] AudioWorklet connected.
✅ [Transport] Connected.
✅ [Pipeline] Started successfully.
```

**Bad signs:**
- "AudioContext was destroyed during initialization" → StrictMode or double-click
- "Disconnected" immediately after "Connected" → backend rejected connection
- "AI service unavailable" → OpenAI connection failed (check API key, websockets version)
- No orb movement → frequency analysis not running (check AudioWorklet)

---

## Quick Deploy Script

Save as `deploy.sh` in repo root:
```bash
#!/bin/bash
set -e

echo "🔧 Building frontend..."
cd frontend-react
npm run build
cp -r dist/* ../
cp dist/processors/recorder.js ../processors/
cd ..

echo "🔄 Restarting backend..."
pkill -f uvicorn || true
source venv/bin/activate
nohup uvicorn main:app --host 0.0.0.0 --port 8000 >> server.log 2>&1 &
sleep 2

echo "✅ Health check:"
curl -s http://localhost:8000/health

echo ""
echo "🚀 Ready. If using tunnel, run: cloudflared tunnel --url http://localhost:8000"
```

---

## Architecture Decisions to Keep

1. **Ref-based callbacks in hooks** — Prevents dependency cascades
2. **Gapless audio scheduling** — No micro-gaps between chunks
3. **Worklet mute/unmute** — Prevents echo feedback
4. **Exponential backoff reconnection** — Handles transient network issues
5. **Triple initialization lock** — Prevents StrictMode double-init

---

## When Things Break

**Step 1:** Check backend health
```bash
curl http://localhost:8000/health
```

**Step 2:** Check frontend build
```bash
ls -la frontend-react/dist/
```

**Step 3:** Check env vars
```bash
cat frontend-react/.env
grep VITE_API_URL frontend-react/.env
```

**Step 4:** Browser console
- Look for `[Transport]` messages
- Check Network tab for WebSocket status
- Check for CORS errors

---

## My Mistakes

1. **Didn't verify library versions** — Should have checked `websockets` API compatibility
2. **Assumed tunnel persistence** — Should have warned about ephemeral URLs
3. **Didn't provide single deploy command** — Multiple manual steps = error prone
4. **Didn't validate backend on startup** — Should have tested OpenAI connection immediately

---

## Working Configuration (As of Last Test)

**Backend:**
- Python 3.12
- websockets 15.0.1 (requires `additional_headers`)
- FastAPI + uvicorn
- OpenAI API key: valid

**Frontend:**
- React 18
- Vite 8.0.0
- Node 22+

**Tunnel:**
- cloudflared 2026.3.0
- Quick Tunnels (ephemeral)

---

Last updated: 2026-03-22
