import React, { useEffect, useState } from 'react';
import { Plus, Copy, Trash2, CheckCircle, AlertCircle, RefreshCw } from 'lucide-react';

const API_URL = import.meta.env.VITE_API_URL || '';
const DEV_KEY = 'sk_cassandra_dev';

export default function ConsoleDashboard() {
  const [keys, setKeys] = useState([]);
  const [loading, setLoading] = useState(true);
  const [creating, setCreating] = useState(false);
  const [newKeyName, setNewKeyName] = useState('');
  const [newlyCreatedKey, setNewlyCreatedKey] = useState(null);
  const [error, setError] = useState('');
  const [copied, setCopied] = useState(false);

  const authHeaders = {
    'Authorization': `Bearer ${DEV_KEY}`,
    'Content-Type': 'application/json',
  };

  const fetchKeys = async () => {
    try {
      const res = await fetch(`${API_URL}/api/keys`, { headers: authHeaders });
      if (res.ok) {
        const data = await res.json();
        setKeys(data.keys || []);
      } else {
        setKeys([]);
      }
    } catch {
      setKeys([]);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchKeys();
  }, []);

  const createKey = async (e) => {
    e.preventDefault();
    setCreating(true);
    setError('');
    setNewlyCreatedKey(null);

    try {
      const res = await fetch(`${API_URL}/api/keys`, {
        method: 'POST',
        headers: authHeaders,
        body: JSON.stringify({ name: newKeyName || `Key ${new Date().toLocaleDateString()}` }),
      });

      if (!res.ok) {
        const err = await res.json();
        throw new Error(err.detail || 'Failed to create key');
      }

      const data = await res.json();
      setNewlyCreatedKey(data);
      setNewKeyName('');
      await fetchKeys();
    } catch (err) {
      setError(err.message);
    } finally {
      setCreating(false);
    }
  };

  const revokeKey = async (keyId) => {
    try {
      const res = await fetch(`${API_URL}/api/keys/${keyId}`, {
        method: 'DELETE',
        headers: authHeaders,
      });
      if (res.ok) {
        setKeys(keys.filter(k => k.id !== keyId));
      }
    } catch {
      setError('Failed to revoke key');
    }
  };

  const copyKey = (key) => {
    navigator.clipboard.writeText(key);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  return (
    <div>
      <div className="mb-8">
        <h1 className="text-2xl font-mono font-bold text-cyan-400 tracking-widest">API Keys</h1>
        <p className="text-slate-500 text-sm font-mono mt-1">Manage your Cassandra API keys for voice integration</p>
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
            disabled={creating}
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

      {/* Newly Created Key (one-time display) */}
      {newlyCreatedKey && (
        <div className="glass-panel p-6 rounded-2xl mb-6 border-emerald-500/30">
          <div className="flex items-center gap-2 text-emerald-400 text-sm font-mono mb-3">
            <CheckCircle size={16} />
            Key created successfully — copy it now, you won&apos;t see it again
          </div>
          <div className="flex items-center gap-3">
            <code className="flex-1 bg-black/50 border border-emerald-500/20 rounded-xl px-4 py-3 text-emerald-400 font-mono text-sm break-all">
              {newlyCreatedKey.key}
            </code>
            <button
              onClick={() => copyKey(newlyCreatedKey.key)}
              className="flex items-center gap-2 bg-emerald-500/10 border border-emerald-400/30 hover:bg-emerald-500/20 text-emerald-400 font-mono text-sm px-4 py-3 rounded-xl transition-all"
            >
              {copied ? <CheckCircle size={16} /> : <Copy size={16} />}
              {copied ? 'Copied!' : 'Copy'}
            </button>
          </div>
        </div>
      )}

      {/* Keys List */}
      <div className="glass-panel rounded-2xl overflow-hidden">
        <div className="p-6 border-b border-white/5">
          <h2 className="text-sm font-mono text-slate-400 tracking-widest uppercase">Your API Keys ({keys.length})</h2>
        </div>

        {loading ? (
          <div className="p-8 text-center text-slate-600 font-mono text-sm">
            <RefreshCw size={16} className="animate-spin inline mr-2" />Loading...
          </div>
        ) : keys.length === 0 ? (
          <div className="p-12 text-center">
            <div className="text-slate-600 font-mono text-sm mb-2">No API keys yet</div>
            <div className="text-slate-700 font-mono text-xs">Create your first key above</div>
          </div>
        ) : (
          <div className="divide-y divide-white/5">
            {keys.map((key) => (
              <div key={key.id} className="p-4 flex items-center gap-4 hover:bg-white/[0.02] transition-colors">
                <div className="flex-1 min-w-0">
                  <div className="text-cyan-400 font-mono text-sm">{key.name}</div>
                  <div className="text-slate-600 font-mono text-xs mt-1">
                    sk_cassandra_{key.prefix || '****'}... • Created {new Date(key.created_at).toLocaleDateString()}
                    {key.last_used && ` • Last used ${new Date(key.last_used).toLocaleDateString()}`}
                  </div>
                </div>
                <button
                  onClick={() => revokeKey(key.id)}
                  className="flex items-center gap-1 text-red-400/50 hover:text-red-400 hover:bg-red-400/5 font-mono text-xs px-3 py-2 rounded-lg transition-all"
                >
                  <Trash2 size={14} /> Revoke
                </button>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
