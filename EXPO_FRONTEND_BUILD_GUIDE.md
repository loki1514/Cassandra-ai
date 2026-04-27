# Expo Frontend Build Guide for Cassandra

> **Goal:** Build a Perplexity-inspired React Native (Expo) client that talks to the Cassandra backend **purely through APIs**. No backend code lives inside the Expo app.

---

## 1. Architecture at a Glance

```
┌─────────────────────────────────────────────────────────────┐
│                    EXPO APP (React Native)                  │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────────────┐  │
│  │   Orb UI    │  │  Dashboard  │  │   Chat / Commands   │  │
│  │  (WebSocket)│  │  (HTTP API) │  │    (HTTP API)       │  │
│  └──────┬──────┘  └──────┬──────┘  └──────────┬──────────┘  │
│         │                │                    │             │
│         └────────────────┴────────────────────┘             │
│                          │                                  │
│                   API Client Layer                          │
│         (Axios/Fetch + Auth Headers + Toasts)               │
└──────────────────────────┬──────────────────────────────────┘
                           │ HTTPS / WSS
┌──────────────────────────▼──────────────────────────────────┐
│              CASSANDRA BACKEND (FastAPI)                    │
│     Runs on a server. You only need its public URL.         │
└─────────────────────────────────────────────────────────────┘
```

**Key Principle:** The Expo app is a **thin client**. All business logic, AI processing, database writes, and file generation happen on the Cassandra server.

---

## 2. Design Language: Perplexity-Inspired

Cassandra in Expo should feel like a **voice-first, ambient AI companion**. Use Perplexity’s visual language as the north star:

### 2.1 Visual Principles
| Element | Specification |
|---------|--------------|
| **Background** | Deep radial gradient: `midnight blue (#0B0F19)` → `slate purple (#1A1F3C)`. Avoid pure black. |
| **Glassmorphism** | Use `rgba(255,255,255,0.06)` backgrounds with `backdrop-blur(20px)` and `1px rgba(255,255,255,0.1)` borders for cards, modals, and drawers. |
| **Typography** | Inter or SF Pro. Headlines `24-32px` medium weight. Body `16px` regular. Monospace for code/transcripts `13px`. |
| **Accent** | Electric violet (`#8B5CF6`) for the orb and primary actions. Soft cyan (`#22D3EE`) for listening states. |
| **Motion** | Spring animations (`stiffness: 120, damping: 15`). Orb breathes with a 3-second sine-wave scale. |
| **Spacing** | Generous whitespace. Center-aligned hero content. Bottom-safe-area padding for iOS. |

### 2.2 The Orb
The orb is the **emotional center** of the app.
- **Idle:** Gentle glow pulse, scale `1.0` → `1.05` loop.
- **Listening:** Cyan rim ring expands outward (sonar effect). Scale `1.1`.
- **Processing:** Violet spinner inside the orb. Subtle wobble.
- **Speaking:** Orb has an audio-visualizer waveform reflected on its surface.
- **Pressed:** Quick scale-down to `0.95`, then haptic feedback.

### 2.3 Full-Screen Modals ("Pop-up Windows like a Screen")
Instead of small bottom sheets, use **full-screen modal overlays** that slide up with a shared-element transition:
- **Dashboard Modal:** Kpi cards, charts, recent tickets.
- **Chat Modal:** Threaded conversation history.
- **Files Modal:** Export requests, download links, share buttons.
- **Users Modal:** Invite form, pending invitations, role badges.

All modals share the same glass header with a drag handle and close button.

---

## 3. Authentication & API Client Setup

### 3.1 Flow
1. User logs in via **Supabase Auth** (email/OTP/Google).
2. Expo receives a `session.access_token` (JWT).
3. Store JWT securely in **Expo SecureStore** (iOS Keychain / Android Keystore).
4. Use the JWT in the `Authorization: Bearer <token>` header for **every** HTTP request.
5. For WebSocket, pass the JWT as a URL query param: `?token=<jwt>`.

### 3.2 Environment Variables (`.env` in Expo)
```bash
# ── App Config ────────────────────────────────────────────────────────────────
EXPO_PUBLIC_APP_NAME=Autopilot
EXPO_PUBLIC_APP_VERSION=1.0.0

# ── Supabase (Expo uses these for auth) ─────────────────────────────────────
# VITE_ prefix is required for Vite/webpack to inject at build time.
# These values come from your backend .env (Supabase project credentials).
VITE_SUPABASE_URL=https://hapwbiteqgusvjifxium.supabase.co
VITE_SUPABASE_ANON_KEY=eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6ImhhcHdiXRlcWd1c3ZqaWZ4aXVtIiwicm9sZSI6ImFub24iLCJpYXQiOjE3NzI2NDg3MTAsImV4cCI6MjA4ODIyNDcxMH0.vCVGys1SObclYC-72SibzOcjx4o2ZdCjSBCWOSpWO0s

# ── Voice Agent Backend Proxy ────────────────────────────────────────────────
# Route: Expo → Next.js proxy → OpenAI/Supermemory (keys stay server-side).
# LOCAL DEV: Set to your deployed Next.js project URL (ngrok if testing locally).
# PRODUCTION: Your Vercel Next.js project URL.
EXPO_PUBLIC_VOICE_API_URL=https://your-project.vercel.app

# ── Cassandra ECAPA Enrollment Server ─────────────────────────────────────────
# ECAPA-TDNN speaker embedding server. Enrollment proxied through /api/voice/enroll.
# LOCAL DEV: Start ECAPA server on port 8001 → http://localhost:8001/enroll
# PRODUCTION: https://your-ecapa-server.com/enroll
EXPO_PUBLIC_CASSANDRA_ECAPA_URL=http://localhost:8001/enroll

# ── Cassandra AI Backend ─────────────────────────────────────────────────────
# REST API — /health, /auth/session, /api/v1/* endpoints.
# LOCAL DEV: http://localhost:8000
# PRODUCTION: https://your-cassandra-backend.example.com
EXPO_PUBLIC_CASSANDRA_API_URL=http://localhost:8000

# WebSocket — real-time voice via /ws/audio/{org_id}.
# LOCAL DEV: ws://localhost:8000
# PRODUCTION: wss://your-cassandra-backend.example.com (SSL required)
EXPO_PUBLIC_CASSANDRA_WS_URL=ws://localhost:8000
```

### 3.3 API Client (`lib/cassandra.ts`)
```typescript
import axios from 'axios';
import * as SecureStore from 'expo-secure-store';

const API_URL = process.env.EXPO_PUBLIC_CASSANDRA_API_URL;

export const cassandra = axios.create({
  baseURL: API_URL,
  timeout: 30000,
  headers: { 'Content-Type': 'application/json' },
});

// Request interceptor: inject Bearer token
cassandra.interceptors.request.use(async (config) => {
  const token = await SecureStore.getItemAsync('jwt');
  if (token) {
    config.headers.Authorization = `Bearer ${token}`;
  }
  return config;
});

// Response interceptor: global toast for errors
cassandra.interceptors.response.use(
  (res) => res,
  (err) => {
    const message = err.response?.data?.detail || err.message;
    Toast.show({ type: 'error', text1: 'Cassandra Error', text2: message });
    return Promise.reject(err);
  }
);
```

---

## 4. State Management (Zustand Store)

Use **Zustand** for global state. Keep it simple.

```typescript
// stores/appStore.ts
import { create } from 'zustand';

export type OrbState = 'idle' | 'listening' | 'processing' | 'speaking' | 'error';

interface AppState {
  orbState: OrbState;
  setOrbState: (s: OrbState) => void;

  transcript: string;
  setTranscript: (t: string) => void;

  lastTickets: any[];
  setLastTickets: (t: any[]) => void;

  isConnected: boolean;
  setIsConnected: (c: boolean) => void;

  activeModal: 'dashboard' | 'chat' | 'files' | 'users' | 'skills' | null;
  setActiveModal: (m: AppState['activeModal']) => void;
}

export const useAppStore = create<AppState>((set) => ({
  orbState: 'idle',
  setOrbState: (s) => set({ orbState: s }),
  transcript: '',
  setTranscript: (t) => set({ transcript: t }),
  lastTickets: [],
  setLastTickets: (t) => set({ lastTickets: t }),
  isConnected: false,
  setIsConnected: (c) => set({ isConnected: c }),
  activeModal: null,
  setActiveModal: (m) => set({ activeModal: m }),
}));
```

---

## 5. The Orb & WebSocket Voice Loop

### 5.1 WebSocket Hook (`hooks/useCassandraWS.ts`)
This is the heart of the app.

```typescript
import { useEffect, useRef, useCallback } from 'react';
import { Audio } from 'expo-av';
import * as SecureStore from 'expo-secure-store';
import { useAppStore } from '../stores/appStore';

const API_URL = process.env.EXPO_PUBLIC_CASSANDRA_API_URL?.replace('https', 'wss');

export function useCassandraWS(orgId: string) {
  const wsRef = useRef<WebSocket | null>(null);
  const recordingRef = useRef<Audio.Recording | null>(null);
  const {
    setOrbState, setTranscript, setLastTickets, setIsConnected, transcript,
  } = useAppStore();

  // Connect
  useEffect(() => {
    if (!orgId) return;
    (async () => {
      const token = await SecureStore.getItemAsync('jwt');
      const wsUrl = `${API_URL}/ws/audio/${orgId}?token=${encodeURIComponent(token || '')}`;
      const ws = new WebSocket(wsUrl);

      ws.onopen = () => {
        setIsConnected(true);
        setOrbState('idle');
      };

      ws.onclose = () => {
        setIsConnected(false);
        setOrbState('idle');
      };

      ws.onmessage = async (event) => {
        if (typeof event.data === 'string') {
          const msg = JSON.parse(event.data);
          handleMessage(msg);
        } else {
          // Binary MP3 response — play it
          playAudioBuffer(event.data);
        }
      };

      wsRef.current = ws;
    })();

    return () => {
      wsRef.current?.close();
      stopRecording();
    };
  }, [orgId]);

  const handleMessage = (msg: any) => {
    switch (msg.type) {
      case 'connected':
        Toast.show({ type: 'success', text1: 'Cassandra is online' });
        break;
      case 'segment':
        setOrbState('processing');
        break;
      case 'pipeline_result':
        setTranscript(msg.data?.transcript || '');
        setLastTickets(msg.data?.tickets_created || []);
        break;
      case 'voice_response':
        setOrbState('speaking');
        break;
      case 'error':
        setOrbState('error');
        Toast.show({ type: 'error', text1: msg.message });
        break;
      case 'complete':
        setOrbState('idle');
        break;
    }
  };

  // Start recording and stream to WS
  const startListening = useCallback(async () => {
    setOrbState('listening');
    await Audio.requestPermissionsAsync();
    await Audio.setAudioModeAsync({ allowsRecordingIOS: true, playsInSilentModeIOS: true });

    const { recording } = await Audio.Recording.createAsync(
      Audio.RecordingOptionsPresets.HIGH_QUALITY
    );
    recordingRef.current = recording;

    // Polling strategy: read recording URI chunks and send raw bytes
    // (For production, implement a custom native recorder that emits PCM16 buffers)
  }, []);

  const stopListening = useCallback(async () => {
    await recordingRef.current?.stopAndUnloadAsync();
    recordingRef.current = null;
  }, []);

  const toggleOrb = useCallback(() => {
    const current = useAppStore.getState().orbState;
    if (current === 'idle' || current === 'error') {
      startListening();
    } else {
      stopListening();
      setOrbState('idle');
    }
  }, [startListening, stopListening]);

  return { toggleOrb, wsRef };
}
```

### 5.2 What Happens When the Orb is Clicked?
| Current State | Action |
|--------------|--------|
| **Idle / Error** | Start microphone recording → `orbState = 'listening'` → stream audio bytes over WS. |
| **Listening** | Stop recording → send final buffer → wait for `pipeline_result`. |
| **Processing** | (Optional) Send `{action: 'interrupt'}` JSON over WS to cancel TTS. |
| **Speaking** | Send `{action: 'interrupt'}` JSON over WS. Stop audio playback. Return to idle. |

### 5.3 Audio Playback Helper
```typescript
import { Audio } from 'expo-av';

let currentSound: Audio.Sound | null = null;

export async function playAudioBuffer(buffer: ArrayBuffer | Blob) {
  if (currentSound) {
    await currentSound.stopAsync();
    await currentSound.unloadAsync();
  }
  const blob = buffer instanceof Blob ? buffer : new Blob([buffer], { type: 'audio/mpeg' });
  const uri = URL.createObjectURL(blob);
  const { sound } = await Audio.Sound.createAsync({ uri });
  currentSound = sound;
  await sound.playAsync();
}
```

---

## 6. Status Toast System

Use `react-native-toast-message` with a **custom glassmorphism renderer**.

```typescript
// components/GlassToast.tsx
import { View, Text, StyleSheet } from 'react-native';

export const GlassToast = ({ text1, text2, type }: any) => {
  const borderColor = type === 'error' ? '#EF4444' : type === 'success' ? '#10B981' : '#8B5CF6';
  return (
    <View style={[styles.container, { borderLeftColor: borderColor }]}>
      <Text style={styles.title}>{text1}</Text>
      {text2 && <Text style={styles.body}>{text2}</Text>}
    </View>
  );
};

const styles = StyleSheet.create({
  container: {
    width: '90%',
    backgroundColor: 'rgba(255,255,255,0.08)',
    backdropFilter: 'blur(20px)',
    borderRadius: 16,
    padding: 16,
    borderLeftWidth: 4,
    borderWidth: 1,
    borderColor: 'rgba(255,255,255,0.1)',
  },
  title: { color: '#fff', fontWeight: '600', fontSize: 15 },
  body: { color: 'rgba(255,255,255,0.7)', fontSize: 13, marginTop: 4 },
});
```

**When to show toasts:**
- `WebSocket connected` → success toast
- `pipeline_result` with tickets → success toast "Created ticket: X"
- `error` from WS or HTTP 4xx/5xx → error toast
- Export ready → success toast with "Download" action
- User invited → success toast

---

## 7. Full-Screen Modal: Dashboard

This is the **primary "pop-up window like a screen"**.

### 7.1 Backend Endpoint
```
GET /analytics/dashboard?org_id={org_id}&period=7d
```

### 7.2 Frontend Screen (`modals/DashboardModal.tsx`)
```typescript
import { useQuery } from '@tanstack/react-query';
import { cassandra } from '../lib/cassandra';

export function DashboardModal({ orgId, onClose }: { orgId: string; onClose: () => void }) {
  const { data } = useQuery({
    queryKey: ['dashboard', orgId],
    queryFn: async () => {
      const res = await cassandra.get('/analytics/dashboard', { params: { org_id: orgId } });
      return res.data;
    },
  });

  return (
    <FullScreenModal title="Dashboard" onClose={onClose}>
      <ScrollView contentContainerStyle={{ padding: 20, paddingBottom: 100 }}>
        {/* KPI Grid */}
        <View style={styles.grid}>
          <KpiCard label="Tickets Created" value={data?.tickets_created ?? '-'} />
          <KpiCard label="Resolved" value={data?.tickets_resolved ?? '-'} />
          <KpiCard label="Open" value={data?.tickets_open ?? '-'} />
          <KpiCard label="SLA %" value={`${Math.round((data?.sla_compliance_rate || 0) * 100)}%`} />
        </View>

        {/* Categories */}
        <SectionTitle>Top Categories</SectionTitle>
        {data?.top_categories?.map((cat: any) => (
          <GlassRow key={cat.category} left={cat.category} right={`${cat.count}`} />
        ))}

        {/* Agent Performance */}
        <SectionTitle>Agent Performance</SectionTitle>
        {data?.agent_performance?.map((agent: any) => (
          <GlassRow key={agent.agent_id} left={agent.name} right={`${agent.resolved} resolved`} />
        ))}
      </ScrollView>
    </FullScreenModal>
  );
}
```

---

## 8. Full-Screen Modal: Chat / Skills Commands

### 8.1 Skill Commands Map
The frontend exposes a **command palette** that maps to backend feature endpoints. These are the "skills" the orb (and the chat UI) can trigger.

| Skill Name | Frontend Trigger | Backend Endpoint | Description |
|------------|------------------|------------------|-------------|
| **Smart Query** | Chat: "What's the status of HVAC?" | `POST /api/v1/features/voice/smart-query` | NL status query |
| **Create Ticket** | Chat: "Create a ticket for broken AC" | `POST /api/v1/features/voice/ticket` | NL ticket creation |
| **Batch Commands** | Voice: "Create tickets for kitchen, lobby, elevator" | `POST /api/v1/features/voice/batch` | Multi-ticket extraction |
| **Escalate** | Chat: "Escalate ticket 123 to critical" | `POST /api/v1/features/voice/escalate` | Priority escalation |
| **Snooze** | Chat: "Snooze ticket 123 to tomorrow" | `POST /api/v1/features/voice/snooze` | Reschedule ticket |
| **Research** | Chat: "Research best HVAC vendors" | `POST /api/v1/features/chat/research` | Perplexity-powered research |
| **Predictive Tickets** | Dashboard card tap | `POST /api/v1/features/ai/predictive-tickets` | AI suggestions |
| **Feasibility Report** | Property view → "Run report" | `POST /api/v1/features/bd/feasibility-report` | BD report |
| **OPEX Estimate** | Property view → "Estimate OPEX" | `POST /api/v1/features/facility/opex-estimate` | Cost estimation |
| **Checklist Voice** | Orb while in room: "Check off cleaning" | `POST /api/v1/features/checklists/voice-process` | Checklist completion |
| **Photo Evidence** | Camera capture | `POST /api/v1/features/checklists/photo-capture` | Defect detection |
| **AR Inspection** | Scan asset tag | `POST /api/v1/features/checklists/ar-process` | AR asset scan |
| **Report Generate** | "Generate weekly report" | `POST /api/v1/features/reports/generate` | PDF/voice report |
| **Notion Push** | "Send this to Notion" | `POST /api/v1/features/integrations/notion` | Integration hub |
| **Queue Offline** | No network detected | `POST /api/v1/features/operations/queue-command` | Offline command buffer |

### 8.2 Chat UI Implementation
```typescript
// modals/ChatModal.tsx
import { useState } from 'react';
import { GiftedChat, IMessage } from 'react-native-gifted-chat';
import { cassandra } from '../lib/cassandra';

export function ChatModal({ orgId, onClose }: { orgId: string; onClose: () => void }) {
  const [messages, setMessages] = useState<IMessage[]>([]);

  const onSend = async (newMessages: IMessage[] = []) => {
    setMessages((prev) => GiftedChat.append(prev, newMessages));
    const text = newMessages[0].text;

    // Default route: smart-query for open-ended questions
    let endpoint = '/api/v1/features/voice/smart-query';
    let body: any = { query_text: text, org_id: orgId };

    // Simple intent routing on the frontend
    const lower = text.toLowerCase();
    if (lower.includes('create ticket') || lower.includes('new ticket')) {
      endpoint = '/api/v1/features/voice/ticket';
      body = { audio_text: text, org_id: orgId };
    } else if (lower.includes('research') || lower.includes('find out')) {
      endpoint = '/api/v1/features/chat/research';
      body = { query: text, org_id: orgId };
    } else if (lower.includes('report')) {
      endpoint = '/api/v1/features/reports/generate';
      body = { report_type: 'weekly', property_id: 'default', period: '7d', org_id: orgId };
    }

    try {
      const res = await cassandra.post(endpoint, body);
      const reply = {
        _id: Math.random().toString(),
        text: formatReply(res.data),
        createdAt: new Date(),
        user: { _id: 'cassandra', name: 'Cassandra', avatar: '...' },
      };
      setMessages((prev) => GiftedChat.append(prev, [reply]));
    } catch (e: any) {
      // error handled by global interceptor + toast
    }
  };

  return (
    <FullScreenModal title="Chat with Cassandra" onClose={onClose}>
      <GiftedChat
        messages={messages}
        onSend={onSend}
        user={{ _id: 'user' }}
        renderBubble={(props) => <GlassBubble {...props} />}
      />
    </FullScreenModal>
  );
}
```

---

## 9. Full-Screen Modal: Add Users / Team Management

### 9.1 Backend Endpoints
```
POST /onboarding/{org_id}/invite       → Invite team members
GET  /onboarding/state/{org_id}        → Get onboarding progress
```

### 9.2 Frontend Screen (`modals/UsersModal.tsx`)
```typescript
export function UsersModal({ orgId, onClose }: { orgId: string; onClose: () => void }) {
  const [emails, setEmails] = useState('');
  const [role, setRole] = useState<'admin' | 'manager' | 'member'>('member');

  const inviteMutation = useMutation({
    mutationFn: async () => {
      const emailList = emails.split(',').map((e) => e.trim()).filter(Boolean);
      const res = await cassandra.post(`/onboarding/${orgId}/invite`, {
        emails: emailList,
        role,
        message: `You've been invited to join Cassandra for your organization.`,
      });
      return res.data;
    },
    onSuccess: (data) => {
      Toast.show({
        type: 'success',
        text1: 'Invitations Sent',
        text2: `${data.invitations_sent} invite(s) delivered.`,
      });
      setEmails('');
    },
  });

  return (
    <FullScreenModal title="Team Members" onClose={onClose}>
      <View style={{ padding: 20 }}>
        <Text style={styles.label}>Email addresses (comma separated)</Text>
        <TextInput
          value={emails}
          onChangeText={setEmails}
          placeholderTextColor="rgba(255,255,255,0.4)"
          style={styles.input}
          autoCapitalize="none"
          keyboardType="email-address"
        />

        <Text style={styles.label}>Role</Text>
        <SegmentedControl values={['Member', 'Manager', 'Admin']} onChange={setRole} />

        <GradientButton onPress={() => inviteMutation.mutate()} loading={inviteMutation.isPending}>
          Send Invites
        </GradientButton>
      </View>
    </FullScreenModal>
  );
}
```

---

## 10. Full-Screen Modal: Files & Downloads

### 10.1 Backend Endpoints
```
POST /export/request              → Request GDPR/data export
GET  /export/{export_id}/status   → Poll for download URL
```

### 10.2 Frontend Screen (`modals/FilesModal.tsx`)
```typescript
export function FilesModal({ orgId, userId, onClose }: any) {
  const [exports, setExports] = useState<any[]>([]);

  const requestExport = async () => {
    const res = await cassandra.post('/export/request', {
      user_id: userId,
      org_id: orgId,
      include_attachments: true,
      format: 'json',
    });
    const exportId = res.data.export_id;
    Toast.show({ type: 'info', text1: 'Preparing export...' });

    // Poll every 3 seconds
    const interval = setInterval(async () => {
      const statusRes = await cassandra.get(`/export/${exportId}/status`);
      const status = statusRes.data;
      if (status.status === 'completed') {
        clearInterval(interval);
        setExports((prev) => [...prev, status]);
        Toast.show({ type: 'success', text1: 'Export ready!', text2: 'Tap to download.' });
      }
    }, 3000);
  };

  return (
    <FullScreenModal title="Files & Exports" onClose={onClose}>
      <View style={{ padding: 20 }}>
        <GradientButton onPress={requestExport}>Request Data Export</GradientButton>

        <Text style={styles.sectionTitle}>Available Downloads</Text>
        {exports.map((ex) => (
          <GlassRow
            key={ex.export_id}
            left={ex.export_id}
            right={formatBytes(ex.size_bytes)}
            onPress={() => Linking.openURL(ex.download_url)}
          />
        ))}
      </View>
    </FullScreenModal>
  );
}
```

---

## 11. Complete API Endpoint Map

Keep this table open in your editor while building.

### 11.1 Health & Meta
| Method | Endpoint | Frontend Use |
|--------|----------|--------------|
| `GET` | `/health` | Splash screen "Checking connection..." |
| `GET` | `/health/dashboard` | Admin diagnostics card |
| `GET` | `/api/v1/me` | Profile screen: show role & org |

### 11.2 Auth & Session
| Method | Endpoint | Frontend Use |
|--------|----------|--------------|
| `POST` | `/auth/session` | (Optional) Exchange API key for WS token |
| `POST` | `/api/keys` | Admin: generate new API key |
| `GET` | `/api/keys` | Admin: list existing keys |
| `DELETE` | `/api/keys/{key_id}` | Admin: revoke key |

### 11.3 Voice (WebSocket)
| Method | Endpoint | Frontend Use |
|--------|----------|--------------|
| `WS` | `/ws/audio/{org_id}?token=JWT` | **Main orb voice channel** |
| `WS` | `/ws/audio` | Unauthenticated test/buffer |
| `POST` | `/api/v1/voice/process` | Upload recorded audio file manually |
| `POST` | `/api/v1/voice/transcribe` | Transcribe pre-recorded audio |

### 11.4 Voice Response (Orb Talking)
| Method | Endpoint | Frontend Use |
|--------|----------|--------------|
| `POST` | `/voice/query` | Text → text |
| `POST` | `/voice/query/audio` | Text → MP3 audio |
| `WS` | `/voice/query/stream` | Text → streaming audio |

### 11.5 Features Router (`/api/v1/features`)
| Method | Endpoint | Skill |
|--------|----------|-------|
| `POST` | `/ai/predictive-tickets` | AI Suggestions |
| `POST` | `/bd/feasibility-report` | BD Report |
| `POST` | `/chat/research` | Research |
| `POST` | `/checklists/ar-process` | AR Scan |
| `GET` | `/checklists/compliance-templates` | Checklist Templates |
| `POST` | `/checklists/drift-check` | Compliance Drift |
| `POST` | `/checklists/photo-capture` | Photo Evidence |
| `POST` | `/checklists/voice-process` | Voice Checklist |
| `POST` | `/facility/opex-estimate` | OPEX Estimate |
| `POST` | `/integrations/notion` | Notion Push |
| `POST` | `/operations/queue-command` | Offline Queue |
| `POST` | `/reports/generate` | Report Generator |
| `POST` | `/quality/log-issue` | Feedback Loop |
| `GET` | `/quality/weekly-analysis` | Quality Analytics |
| `POST` | `/voice/smart-query` | Status Query |
| `POST` | `/voice/ticket` | NL Ticket |
| `POST` | `/voice/batch` | Batch Commands |
| `POST` | `/voice/escalate` | Escalation |
| `POST` | `/voice/snooze` | Snooze/Reschedule |

### 11.6 Rooms & Memory
| Method | Endpoint | Frontend Use |
|--------|----------|--------------|
| `POST` | `/{property_id}/rooms` | Create a room |
| `GET` | `/{property_id}/rooms/{room_id}` | Room details |
| `PATCH` | `/{property_id}/rooms/{room_id}/participants` | Add participant |
| `POST` | `/{property_id}/rooms/{room_id}/end` | End room session |
| `GET` | `/{property_id}/rooms/{room_id}/analysis` | View post-session analysis |
| `POST` | `/api/v1/memory/search` | Search Supermemory |

### 11.7 Onboarding & Export
| Method | Endpoint | Frontend Use |
|--------|----------|--------------|
| `GET` | `/onboarding/state/{org_id}` | Onboarding progress bar |
| `POST` | `/onboarding/{org_id}/setup` | Org setup wizard |
| `POST` | `/onboarding/{org_id}/invite` | **Add users / invite team** |
| `POST` | `/export/request` | Request data export |
| `GET` | `/export/{export_id}/status` | Get download link |

### 11.8 Analytics
| Method | Endpoint | Frontend Use |
|--------|----------|--------------|
| `GET` | `/analytics/dashboard` | **Dashboard KPIs** |
| `POST` | `/analytics/query` | Custom charts |
| `GET` | `/analytics/commitments` | Commitment tracking |

---

## 12. Shared UI Primitives

Build these once. Reuse everywhere.

```typescript
// components/FullScreenModal.tsx
import { Modal, View, Text, TouchableOpacity, StyleSheet } from 'react-native';

export function FullScreenModal({ visible, title, onClose, children }: any) {
  return (
    <Modal animationType="slide" presentationStyle="fullScreen" visible={visible}>
      <View style={styles.root}>
        <View style={styles.header}>
          <Text style={styles.title}>{title}</Text>
          <TouchableOpacity onPress={onClose} style={styles.closeBtn}>
            <Text style={styles.closeText}>✕</Text>
          </TouchableOpacity>
        </View>
        {children}
      </View>
    </Modal>
  );
}

const styles = StyleSheet.create({
  root: {
    flex: 1,
    backgroundColor: '#0B0F19',
  },
  header: {
    paddingTop: 60,
    paddingHorizontal: 20,
    paddingBottom: 16,
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'center',
    borderBottomWidth: 1,
    borderBottomColor: 'rgba(255,255,255,0.08)',
  },
  title: { color: '#fff', fontSize: 20, fontWeight: '600' },
  closeBtn: { width: 36, height: 36, borderRadius: 18, backgroundColor: 'rgba(255,255,255,0.1)', alignItems: 'center', justifyContent: 'center' },
  closeText: { color: '#fff', fontSize: 16 },
});
```

```typescript
// components/GlassCard.tsx
export const GlassCard = ({ children, style }: any) => (
  <View style={[{
    backgroundColor: 'rgba(255,255,255,0.05)',
    borderRadius: 20,
    padding: 20,
    borderWidth: 1,
    borderColor: 'rgba(255,255,255,0.1)',
  }, style]}>
    {children}
  </View>
);
```

```typescript
// components/GradientButton.tsx
import { LinearGradient } from 'expo-linear-gradient';
import { TouchableOpacity, Text, ActivityIndicator } from 'react-native';

export const GradientButton = ({ onPress, children, loading }: any) => (
  <TouchableOpacity onPress={onPress} activeOpacity={0.8} style={{ marginVertical: 12 }}>
    <LinearGradient
      colors={['#8B5CF6', '#6366F1']}
      start={{ x: 0, y: 0 }}
      end={{ x: 1, y: 1 }}
      style={{ paddingVertical: 16, borderRadius: 16, alignItems: 'center' }}
    >
      {loading ? <ActivityIndicator color="#fff" /> : <Text style={{ color: '#fff', fontWeight: '600', fontSize: 16 }}>{children}</Text>}
    </LinearGradient>
  </TouchableOpacity>
);
```

---

## 13. Home Screen Layout

The main screen should be **orb-centric**. Everything else floats around it.

```typescript
// app/index.tsx
export default function HomeScreen() {
  const { activeModal, setActiveModal, orbState, transcript } = useAppStore();
  const { user } = useAuth(); // your Supabase auth hook
  const { toggleOrb } = useCassandraWS(user?.org_id);

  return (
    <View style={{ flex: 1, backgroundColor: '#0B0F19' }}>
      {/* Background gradient mesh */}
      <RadialGradient center={[width/2, height/2]} colors={['#1A1F3C', '#0B0F19']} style={StyleSheet.absoluteFill} />

      {/* Top bar: connection status + menu */}
      <View style={styles.topBar}>
        <ConnectionPill />
        <TouchableOpacity onPress={() => setActiveModal('dashboard')}>
          <Text style={styles.menuIcon}>☰</Text>
        </TouchableOpacity>
      </View>

      {/* Center: Orb */}
      <View style={styles.orbContainer}>
        <Orb state={orbState} onPress={toggleOrb} />
        {transcript ? (
          <Text style={styles.transcript}>{transcript}</Text>
        ) : (
          <Text style={styles.hint}>Tap the orb to talk to Cassandra</Text>
        )}
      </View>

      {/* Bottom dock: quick actions */}
      <View style={styles.dock}>
        <DockButton icon="💬" label="Chat" onPress={() => setActiveModal('chat')} />
        <DockButton icon="👥" label="Team" onPress={() => setActiveModal('users')} />
        <DockButton icon="📁" label="Files" onPress={() => setActiveModal('files')} />
        <DockButton icon="⚡" label="Skills" onPress={() => setActiveModal('skills')} />
      </View>

      {/* Modals */}
      <DashboardModal visible={activeModal === 'dashboard'} onClose={() => setActiveModal(null)} orgId={user?.org_id} />
      <ChatModal visible={activeModal === 'chat'} onClose={() => setActiveModal(null)} orgId={user?.org_id} />
      <UsersModal visible={activeModal === 'users'} onClose={() => setActiveModal(null)} orgId={user?.org_id} />
      <FilesModal visible={activeModal === 'files'} onClose={() => setActiveModal(null)} orgId={user?.org_id} userId={user?.id} />
    </View>
  );
}
```

---

## 14. Frontend-Backend Sync Checklist

Use this checklist before every release to ensure the frontend and backend are in sync.

### 14.1 Contract Verification
- [ ] Every `POST` body in the frontend matches the Pydantic model in the backend.
- [ ] All required fields (`org_id`, `user_id`, `audio_text`, etc.) are present.
- [ ] Query params (`?org_id=...&period=7d`) match backend signatures exactly.
- [ ] WebSocket JSON control messages (`session_start`, `interrupt`, `status`) match backend expectations.

### 14.2 Auth Verification
- [ ] JWT is fetched from SecureStore and attached to **every** HTTP request.
- [ ] JWT is URL-encoded before being appended to WebSocket URL.
- [ ] Token refresh logic is implemented (Supabase `onAuthStateChange`).
- [ ] 401 responses trigger a re-login flow instead of an infinite error loop.

### 14.3 Error Handling
- [ ] Backend `4xx` errors show a user-friendly toast (not raw JSON).
- [ ] WebSocket disconnections auto-retry with exponential backoff.
- [ ] `voice_response` TTS failures are handled gracefully (orb shows text fallback).

### 14.4 Feature Flags
- [ ] Features that require backend modules (e.g., `ELEVENLABS_API_KEY`) are hidden or disabled if the backend health check reports them as unhealthy.
- [ ] Dashboard modals check `health/dashboard` components before rendering advanced charts.

### 14.5 Data Consistency
- [ ] After mutating data (invite user, create ticket), the frontend **invalidates** the related TanStack Query cache keys.
- [ ] Optimistic updates are rolled back on mutation failure.

### 14.6 Audio Contract
- [ ] Expo records audio at **16kHz, mono, 16-bit PCM** before sending over WS.
- [ ] If native recording can't produce PCM16, implement a downsample step in JavaScript.
- [ ] Audio chunk sizes are validated client-side (reject < 50ms or > 5s per frame).

---

## 15. Recommended Expo Dependencies

```json
{
  "dependencies": {
    "expo": "~50.x",
    "expo-av": "~13.x",
    "expo-secure-store": "~12.x",
    "expo-linear-gradient": "~12.x",
    "expo-haptics": "~12.x",
    "react-native-reanimated": "~3.x",
    "react-native-gesture-handler": "~2.x",
    "react-native-svg": "~14.x",
    "@supabase/supabase-js": "^2.x",
    "axios": "^1.x",
    "zustand": "^4.x",
    "@tanstack/react-query": "^5.x",
    "react-native-toast-message": "^2.x",
    "react-native-gifted-chat": "^2.x"
  }
}
```

---

## 16. Quick Start for the Expo Team

1. **Create the Expo app:** `npx create-expo-app cassandra-expo --template blank-typescript`
2. **Install deps** from Section 15.
3. **Copy `.env`** from Section 3.2.
4. **Build the API client** (`lib/cassandra.ts`) from Section 3.3.
5. **Build the Zustand store** (`stores/appStore.ts`) from Section 4.
6. **Build the WebSocket hook** (`hooks/useCassandraWS.ts`) from Section 5.
7. **Build the 4 shared primitives** from Section 12.
8. **Build the 5 modals:** Dashboard, Chat, Users, Files, Skills.
9. **Build the Home screen** from Section 13.
10. **Run the Cassandra backend** locally and point `EXPO_PUBLIC_CASSANDRA_API_URL` to your machine's IP.
11. **Test the orb.** Speak. Expect: greeting → transcript → ticket → voice response.

---

**End of Guide.**
