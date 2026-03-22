import React, { useRef, useMemo, useEffect } from 'react';
import { Canvas, useFrame } from '@react-three/fiber';
import { OrbitControls } from '@react-three/drei';
import { EffectComposer, Bloom } from '@react-three/postprocessing';
import * as THREE from 'three';
import { useOrbStore } from '../stores/orbStore';

/**
 * ParticleSwarm — The Cassandra Orb
 * 
 * Audio reactivity model:
 *   - IDLE: Slow breathing, gentle drift, neutral hue
 *   - LISTENING: Subtle mic-reactive pulse, cool cyan
 *   - SPEAKING (AI): Strong voice-reactive animation, warm gold
 *                     Particles expand/contract with AI voice amplitude
 *                     Core brightens on bass, shell shimmers on treble
 *   - THINKING: Fast spin, purple shift
 *   - ERROR: Red, minimal movement
 * 
 * The key insight: when the AI speaks, the orb IS the AI's body.
 * It should feel like the orb is the one talking — particles pulse
 * with each syllable, the core flares on emphasis, the shell breathes
 * with the voice rhythm.
 */
const ParticleSwarm = () => {
  const meshRef = useRef();
  const count = 20000;

  const dummy = useMemo(() => new THREE.Object3D(), []);
  const target = useMemo(() => new THREE.Vector3(), []);
  const pColor = useMemo(() => new THREE.Color(), []);

  // Read from Zustand store
  const orbState = useOrbStore((s) => s.state || 'idle');

  // Smoothed audio values (lerped in useFrame for no jitter)
  const smoothedRef = useRef({
    rms: 0,
    bass: 0,
    mid: 0,
    treble: 0,
    radius: 50,
    wobble: 0.29,
    spin: 1.54,
    hueShift: 0.26,
    bloom: 1.5,
  });

  // Particle home positions (Fibonacci sphere)
  const positions = useMemo(() => {
    const pos = [];
    for (let i = 0; i < count; i++) {
      pos.push(
        new THREE.Vector3(
          (Math.random() - 0.5) * 100,
          (Math.random() - 0.5) * 100,
          (Math.random() - 0.5) * 100
        )
      );
    }
    return pos;
  }, []);

  const material = useMemo(() => new THREE.MeshBasicMaterial({ color: 0xffffff }), []);
  const geometry = useMemo(() => new THREE.TetrahedronGeometry(0.25), []);

  useFrame((state) => {
    if (!meshRef.current) return;
    const time = state.clock.getElapsedTime();
    const s = smoothedRef.current;

    // ── Read live audio energy directly (no polling delay) ──
    const energy = window.getAudioEnergy?.() || {
      bass: 0, mid: 0, treble: 0, rms: 0,
      isUserSpeaking: false, isAISpeaking: false,
    };

    // Normalize frequency bands to 0-1
    const bass = Math.min(1, energy.bass / 1500);
    const mid = Math.min(1, energy.mid / 800);
    const treble = Math.min(1, energy.treble / 500);
    const rms = energy.rms;

    // Smooth audio values (prevents visual jitter)
    const audioSmoothing = 0.15;
    s.rms += (rms - s.rms) * audioSmoothing;
    s.bass += (bass - s.bass) * audioSmoothing;
    s.mid += (mid - s.mid) * audioSmoothing;
    s.treble += (treble - s.treble) * audioSmoothing;

    // ── State-driven target parameters ──
    let targetRadius, targetWobble, targetSpin, targetHue;

    if (orbState === 'speaking' || energy.isAISpeaking) {
      // ═══ AI IS SPEAKING — the orb is the voice ═══
      // Radius pulses with voice amplitude (bass-heavy = bigger pulse)
      targetRadius = 55 + s.rms * 40 + s.bass * 20;
      // Wobble drives per-particle displacement — voice makes it ripple
      targetWobble = 0.8 + s.rms * 3.0 + s.bass * 1.5;
      // Spin increases slightly with speech energy
      targetSpin = 1.8 + s.mid * 0.5;
      // Warm gold/amber when speaking
      targetHue = 0.12;

    } else if (orbState === 'listening' || energy.isUserSpeaking) {
      // ═══ USER IS SPEAKING — orb listens, subtle reaction ═══
      targetRadius = 50 + s.rms * 12;
      targetWobble = 0.4 + s.rms * 0.8;
      targetSpin = 1.54;
      // Cool cyan/teal when listening
      targetHue = 0.5;

    } else if (orbState === 'thinking' || orbState === 'connecting') {
      // ═══ PROCESSING — fast purposeful movement ═══
      targetRadius = 48;
      targetWobble = 0.6;
      targetSpin = 5.0;
      targetHue = 0.75; // Purple

    } else if (orbState === 'error') {
      // ═══ ERROR — minimal movement, red ═══
      targetRadius = 45;
      targetWobble = 0.1;
      targetSpin = 0.2;
      targetHue = 0.0;

    } else {
      // ═══ IDLE — gentle breathing ═══
      targetRadius = 50 + Math.sin(time * 0.5) * 2; // Subtle breathe
      targetWobble = 0.29;
      targetSpin = 1.54;
      targetHue = 0.26;
    }

    // ── Smooth transitions between states (never snap) ──
    const stateSmoothing = 0.06; // ~800ms effective transition
    s.radius += (targetRadius - s.radius) * stateSmoothing;
    s.wobble += (targetWobble - s.wobble) * stateSmoothing;
    s.spin += (targetSpin - s.spin) * stateSmoothing;
    s.hueShift += (targetHue - s.hueShift) * stateSmoothing;

    // ── Update all particles ──
    for (let i = 0; i < count; i++) {
      const phi = Math.acos(1 - 2 * (i + 0.5) / count);
      const theta = Math.PI * (1 + Math.sqrt(5)) * (i + 0.5);

      // Per-particle radius variation driven by audio
      // During AI speech, bass creates large slow waves, treble creates shimmer
      const bassWave = energy.isAISpeaking
        ? Math.sin(time * 2 + i * 0.005) * s.bass * 15
        : 0;
      const trebleShimmer = energy.isAISpeaking
        ? Math.sin(time * 8 + i * 0.1) * s.treble * 5
        : 0;

      const r = s.radius
        + Math.sin(time + i * 0.01) * s.wobble * 20
        + bassWave
        + trebleShimmer;

      const x = r * Math.cos(theta + time * s.spin) * Math.sin(phi);
      const y = r * Math.sin(theta + time * s.spin) * Math.sin(phi);
      const z = r * Math.cos(phi);

      target.set(x, y, z);

      // Particle lerp speed — faster during speech for snappier reactivity
      const lerpSpeed = (energy.isAISpeaking || energy.isUserSpeaking) ? 0.18 : 0.1;
      positions[i].lerp(target, lerpSpeed);

      dummy.position.copy(positions[i]);
      dummy.updateMatrix();
      meshRef.current.setMatrixAt(i, dummy.matrix);

      // ── Color ──
      // Base: rainbow cycle by particle index + time
      // AI Speaking: shift toward warm gold/amber, saturation increases with energy
      // Listening: shift toward cool cyan
      const saturation = (energy.isAISpeaking || energy.isUserSpeaking)
        ? 0.7 + s.rms * 0.3   // More saturated during speech
        : 0.7;
      const lightness = (energy.isAISpeaking)
        ? 0.5 + s.rms * 0.3   // Brighter during AI speech
        : 0.5;

      pColor.setHSL(
        ((i / count) + time * s.hueShift) % 1,
        saturation,
        lightness
      );
      meshRef.current.setColorAt(i, pColor);
    }

    meshRef.current.instanceMatrix.needsUpdate = true;
    if (meshRef.current.instanceColor) {
      meshRef.current.instanceColor.needsUpdate = true;
    }
  });

  return (
    <instancedMesh ref={meshRef} args={[geometry, material, count]} />
  );
};

/**
 * DynamicBloom — Bloom intensity responds to state and voice energy
 */
const DynamicBloom = () => {
  const bloomRef = useRef();

  useFrame(() => {
    if (!bloomRef.current) return;
    const energy = window.getAudioEnergy?.() || { rms: 0, isAISpeaking: false };
    const orbState = window.orbState || 'idle';

    let targetIntensity = 1.5;

    if (orbState === 'speaking' || energy.isAISpeaking) {
      // Bloom flares with voice — brighter on loud syllables
      targetIntensity = 2.0 + energy.rms * 2.0;
    } else if (orbState === 'listening') {
      targetIntensity = 1.5 + energy.rms * 0.5;
    }

    // Smooth bloom transitions
    bloomRef.current.intensity +=
      (targetIntensity - bloomRef.current.intensity) * 0.1;
  });

  return (
    <Bloom
      ref={bloomRef}
      intensity={1.5}
      luminanceThreshold={0.1}
      luminanceSmoothing={0.9}
    />
  );
};

/**
 * CassandraOrb — Main export
 */
export default function CassandraOrb() {
  const setState = useOrbStore((s) => s.setState);
  const bloomIntensity = useOrbStore((s) => s.bloomIntensity || 1.5);

  useEffect(() => {
    const interval = setInterval(() => {
      const currentState = window.orbState || 'idle';
      setState(currentState);

      // Update bloom based on audio energy
      const energy = window.getAudioEnergy?.() || { rms: 0, isAISpeaking: false };
      const orbState = window.orbState || 'idle';

      let targetBloom = 1.5;
      if (orbState === 'speaking' || energy.isAISpeaking) {
        targetBloom = 2.0 + energy.rms * 2.0;
      } else if (orbState === 'listening') {
        targetBloom = 1.5 + energy.rms * 0.5;
      }

      useOrbStore.getState().setBloomIntensity?.(targetBloom);
    }, 100);

    return () => clearInterval(interval);
  }, [setState]);

  return (
    <div style={{
      width: '100%', height: '100%', position: 'absolute', inset: 0,
      pointerEvents: 'none', display: 'flex', alignItems: 'center',
      justifyContent: 'center', overflow: 'hidden',
      background: 'radial-gradient(circle, rgba(0,255,255,0.05), transparent 50%)',
    }}>
      <div style={{ width: '40vw', height: '40vw', minWidth: '300px', maxWidth: '600px' }}>
        <Canvas camera={{ position: [0, 0, 160], fov: 60 }} gl={{ alpha: true }}>
          <fog attach="fog" args={['#000000', 0.01]} />
          <ParticleSwarm />
          <OrbitControls autoRotate autoRotateSpeed={2.0} enableZoom={false} enablePan={false} />
          <EffectComposer>
            <Bloom
              intensity={bloomIntensity}
              luminanceThreshold={0.1}
              luminanceSmoothing={0.9}
            />
          </EffectComposer>
        </Canvas>
      </div>
    </div>
  );
}
