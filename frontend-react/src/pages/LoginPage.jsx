import React, { useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { fmsSupabase } from '../lib/supabase';

export default function LoginPage() {
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [isSignUp, setIsSignUp] = useState(false);
  const [loading, setLoading] = useState(false);
  const [message, setMessage] = useState('');
  const [error, setError] = useState('');
  const navigate = useNavigate();

  const handleSubmit = async (e) => {
    e.preventDefault();
    setLoading(true);
    setError('');
    setMessage('');

    try {
      if (isSignUp) {
        const { error } = await fmsSupabase.auth.signUp({
          email,
          password,
        });
        if (error) throw error;
        setMessage('Check your email for a confirmation link!');
      } else {
        const { error } = await fmsSupabase.auth.signInWithPassword({
          email,
          password,
        });
        if (error) throw error;
        navigate('/console/dashboard');
      }
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="min-h-screen bg-slate-950 flex items-center justify-center p-4">
      <div className="glass-panel max-w-md w-full p-8 rounded-2xl">
        {/* Logo */}
        <div className="text-center mb-8">
          <div className="inline-flex items-center gap-2 text-cyan-400 font-mono font-bold tracking-widest text-lg mb-2">
            <span className="text-2xl">◉</span> CASSANDRA
          </div>
          <p className="text-slate-500 text-sm font-mono">Console — API Key Management</p>
        </div>

        {/* Form */}
        <form onSubmit={handleSubmit} className="space-y-4">
          <div>
            <label className="block text-[10px] text-slate-400 font-mono mb-2 tracking-widest uppercase">
              Email
            </label>
            <input
              type="email"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              required
              className="w-full bg-black/30 border border-white/10 rounded-xl px-4 py-3 text-cyan-400 font-mono text-sm focus:border-cyan-400/50 focus:outline-none transition-colors"
              placeholder="you@company.com"
            />
          </div>

          <div>
            <label className="block text-[10px] text-slate-400 font-mono mb-2 tracking-widest uppercase">
              Password
            </label>
            <input
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              required
              minLength={6}
              className="w-full bg-black/30 border border-white/10 rounded-xl px-4 py-3 text-cyan-400 font-mono text-sm focus:border-cyan-400/50 focus:outline-none transition-colors"
              placeholder="••••••••"
            />
          </div>

          {error && (
            <div className="bg-red-900/20 border border-red-500/30 rounded-xl px-4 py-3 text-red-400 text-sm font-mono">
              {error}
            </div>
          )}

          {message && (
            <div className="bg-emerald-900/20 border border-emerald-500/30 rounded-xl px-4 py-3 text-emerald-400 text-sm font-mono">
              {message}
            </div>
          )}

          <button
            type="submit"
            disabled={loading}
            className="w-full bg-cyan-500/10 border border-cyan-400/30 hover:border-cyan-400/60 hover:bg-cyan-500/20 text-cyan-400 font-mono text-sm tracking-widest py-3 rounded-xl transition-all duration-300 disabled:opacity-50"
          >
            {loading ? 'Processing...' : isSignUp ? 'Create Account' : 'Sign In'}
          </button>
        </form>

        {/* Toggle */}
        <div className="mt-6 text-center">
          <button
            onClick={() => { setIsSignUp(!isSignUp); setError(''); setMessage(''); }}
            className="text-slate-500 hover:text-cyan-400 text-sm font-mono transition-colors"
          >
            {isSignUp ? 'Already have an account? Sign in' : "Don't have an account? Sign up"}
          </button>
        </div>

        {/* Back to app */}
        <div className="mt-4 text-center">
          <Link to="/" className="text-slate-600 hover:text-slate-400 text-xs font-mono transition-colors">
            ← Back to App
          </Link>
        </div>
      </div>
    </div>
  );
}
