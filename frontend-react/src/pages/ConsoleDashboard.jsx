import React, { useEffect, useState, useCallback } from 'react';
import { useNavigate } from 'react-router-dom';
import { Plus, Copy, Trash2, CheckCircle, AlertCircle, RefreshCw, LogOut } from 'lucide-react';
import { fmsSupabase, getFmsToken } from '../lib/supabase';

const API_URL = import.meta.env.VITE_CASSANDRA_API_URL || 'http://localhost:8000';

export default function ConsoleDashboard() {
  const navigate = useNavigate();
  const [keys, setKeys] = useState([]);
  const [loading, setLoading] = useState(true);
  const [creating, setCreating] = useState(false);
  const [newKeyName, setNewKeyName] = useState('');
  const [newlyCreatedKey, setNewlyCreatedKey] = useState(null);
  const [newlyConfirmed, setNewlyConfirmed] = useState(false);
  const [error, setError] = useState('');
  const [copied, setCopied] = useState(false);
  const [revoking, setRevoking] = useState(null);

  // Auth guard: redirect to login if no session
  useEffect(() => {
    const { data: { subscription } } = fmsSupabase.auth.onAuthStateChange((_event, session) => {
      if (!session) navigate('/console/login');
    });
    fmsSupabase.auth.getSession().then(({ data: { session } }) => {
      if (!session) navigate('/console/login');
    });
    return () => subscription.unsubscribe();
  }, [navigate]);

  const fetchKeys = useCallback(async () => {
    setLoading(true);
    setError('');
    try {
      const token = await getFmsToken();
      const res = await fetch(`${API_URL}/api/keys`, {
        headers: {
          Authorization: `Bearer ${token}`,
          'Content-Type': 'application/json',
        },
      });
      if (res.status === 401 || res.status === 403) {
        await fmsSupabase.auth.signOut();
        navigate('/console/login');
        return;
      }
      if (!res.ok) {
        const err = await res.json();
        throw new Error(err.detail || `HTTP ${res.status}`);
      }
      const data = await res.json();
      setKeys(data.keys || []);
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  }, [navigate]);

  useEffect(() => {
    fetchKeys();
  }, [fetchKeys]);

  const createKey = async (e) => {
    e.preventDefault();
    setCreating(true);
    setError('');
    setNewlyCreatedKey(null);
    setNewlyConfirmed(false);
    try {
      const token = await getFmsToken();
      const res = await fetch(`${API_URL}/api/keys`, {
        method: 'POST',
        headers: {
          Authorization: `Bearer ${token}`,
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({ name: newKeyName.trim() || `Key ${new Date().toLocaleDateString()}` }),
      });
      if (res.status === 401 || res.status === 403) {
        await fmsSupabase.auth.signOut();
        navigate('/console/login');
        return;
      }
      if (!res.ok) {
        const err = await res.json();
        throw new Error(err.detail || `HTTP ${res.status}`);
      }
      const data = await res.json();
      setNewlyCreatedKey(data.api_key);
      setNewlyConfirmed(false);
      setNewKeyName('');
      await fetchKeys();
    } catch (err) {
      setError(err.message);
    } finally {
      setCreating(false);
    }
  };

  const revokeKey = async (keyId) => {
    setRevoking(keyId);
    setError('');
    try {
      const token = await getFmsToken();
      const res = await fetch(`${API_URL}/api/keys/${keyId}`, {
        method: 'DELETE',
        headers: {
          Authorization: `Bearer ${token}`,
        },
      });
      if (res.status === 401 || res.status === 403) {
        await fmsSupabase.auth.signOut();
        navigate('/console/login');
        return;
      }
      if (!res.ok) {
        const err = await res.json();
        throw new Error(err.detail || `HTTP ${res.status}`);
      }
      // Update local state without refetching
      setKeys(keys.map(k => k.id === keyId ? { ...k, is_active: false } : k));
    } catch (err) {
      setError(err.message);
    } finally {
      setRevoking(null);
    }
  };

  const copyKey = (key) => {
    navigator.clipboard.writeText(key);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  const handleSignOut = async () => {
    await fmsSupabase.auth.signOut();
    navigate('/console/login');
  };

  const confirmKeySeen = () => {
    setNewlyConfirmed(true);
  };

  return (
    <div>
      {/* Header */}
      <div className="flex items-center justify-between mb-8">
        <div>
          <h1 className="text-2xl font-mono font-bold text-cyan-400 tracking-widest">API Keys</h1>
          <p className="text-slate-500 text-sm font-mono mt-1">
            Manage your Cassandra API keys for voice integration
          </p>
        </div>
        <div className="flex items-center gap-3">
          <button
            onClick={fetchKeys}
            className="flex items-center gap-1.5 text-slate-500 hover:text-cyan-400 font-mono text-xs px-3 py-2 rounded-lg transition-colors"
            title="Refresh keys"
          >
            <RefreshCw size={13} className={loading ? 'animate-spin' : ''} />
            Refresh
          </button>
          <button
            onClick={handleSignOut}
            className="flex items-center gap-1.5 text-red-400/60 hover:text-red-400 font-mono text-xs px-3 py-2 rounded-lg transition-colors"
          >
            <LogOut size={13} />
            Sign Out
          </button>
        </div>
      </div>

      {/* Create Key Form */}
      <div className="glass-panel p-6 rounded-2xl mb-6">
        <h2 className="text-sm font-mono text-slate-400 tracking-widest mb-4 uppercase">Create New Key</h2>
        <form onSubmit={createKey} className="flex gap-3">
          <input
            type="text"
            value={newKeyName}
            onChange={(e) => setNewKeyName(e.target.value)}
            placeholder="Key name (optional)"
            className="flex-1 bg-black/30 border border-white/10 rounded-xl px-4 py-3 text-cyan-400 font-mono text-sm focus:border-cyan-400/50 focus:outline-none transition-colors"
          />
          <button
            type="submit"
            disabled={creating || !!newlyCreatedKey}
            className="flex items-center gap-2 bg-cyan-500/10 border border-cyan-400/30 hover:border-cyan-400/60 hover:bg-cyan-500/20 text-cyan-400 font-mono text-sm px-6 py-3 rounded-xl transition-all disabled:opacity-50"
          >
            <Plus size={16} />
            {creating ? 'Creating...' : 'Create Key'}
          </button>
        </form>
        {error && (
          <div className="mt-3 flex items-center gap-2 text-red-400 text-sm font-mono">
            <AlertCircle size={14} /> {error}
          </div>
        )}
      </div>

      {/* Newly Created Key — one-time display */}
      {newlyCreatedKey && !newlyConfirmed && (
        <div className="glass-panel p-6 rounded-2xl mb-6 border-emerald-500/30">
          <div className="flex items-center gap-2 text-emerald-400 text-sm font-mono mb-3">
            <CheckCircle size={16} />
            Key created — copy it now, you won&apos;t see it again
          </div>
          <div className="flex items-center gap-3">
            <code className="flex-1 bg-black/50 border border-emerald-500/20 rounded-xl px-4 py-3 text-emerald-400 font-mono text-sm break-all">
              {newlyCreatedKey}
            </code>
            <button
              onClick={() => copyKey(newlyCreatedKey)}
              className="flex items-center gap-2 bg-emerald-500/10 border border-emerald-400/30 hover:bg-emerald-500/20 text-emerald-400 font-mono text-sm px-4 py-3 rounded-xl transition-all"
            >
              {copied ? <CheckCircle size={16} /> : <Copy size={16} />}
              {copied ? 'Copied!' : 'Copy'}
            </button>
          </div>
          <div className="mt-4">
            <button
              onClick={confirmKeySeen}
              className="w-full py-2.5 bg-emerald-500/10 border border-emerald-500/30 hover:bg-emerald-500/20 text-emerald-400 font-mono text-sm rounded-xl transition-all"
            >
              I&apos;ve saved this key — hide it
            </button>
          </div>
        </div>
      )}

      {/* Keys List */}
      <div className="glass-panel rounded-2xl overflow-hidden">
        <div className="p-6 border-b border-white/5">
          <h2 className="text-sm font-mono text-slate-400 tracking-widest uppercase">
            Your API Keys ({keys.length})
          </h2>
        </div>

        {loading ? (
          <div className="p-8 text-center text-slate-600 font-mono text-sm">
            <RefreshCw size={16} className="animate-spin inline mr-2" />
            Loading...
          </div>
        ) : keys.length === 0 ? (
          <div className="p-12 text-center">
            <div className="text-slate-600 font-mono text-sm mb-2">No API keys yet</div>
            <div className="text-slate-700 font-mono text-xs">Create your first key above</div>
          </div>
        ) : (
          <div className="divide-y divide-white/5">
            {keys.map((key) => (
              <div
                key={key.id}
                className="p-4 flex items-center gap-4 hover:bg-white/[0.02] transition-colors"
              >
                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-2">
                    <span className="text-cyan-400 font-mono text-sm">{key.name}</span>
                    {!key.is_active && (
                      <span className="text-[10px] font-mono px-1.5 py-0.5 rounded bg-red-500/10 border border-red-500/30 text-red-400">
                        Revoked
                      </span>
                    )}
                    {key.is_active && (
                      <span className="text-[10px] font-mono px-1.5 py-0.5 rounded bg-emerald-500/10 border border-emerald-500/30 text-emerald-400">
                        Active
                      </span>
                    )}
                  </div>
                  <div className="text-slate-600 font-mono text-xs mt-1">
                    sk_cassandra_{key.key_prefix?.replace('sk_cassandra_', '') || '****'}...
                    &nbsp;&bull;&nbsp;Created {new Date(key.created_at).toLocaleDateString()}
                    {key.last_used && (
                      <>
                        &nbsp;&bull;&nbsp;Last used {new Date(key.last_used).toLocaleDateString()}
                      </>
                    )}
                  </div>
                </div>
                {key.is_active && (
                  <button
                    onClick={() => revokeKey(key.id)}
                    disabled={revoking === key.id}
                    className="flex items-center gap-1 text-red-400/50 hover:text-red-400 hover:bg-red-400/5 font-mono text-xs px-3 py-2 rounded-lg transition-all disabled:opacity-50"
                  >
                    {revoking === key.id ? (
                      <RefreshCw size={14} className="animate-spin" />
                    ) : (
                      <Trash2 size={14} />
                    )}
                    Revoke
                  </button>
                )}
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
