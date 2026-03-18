import React, { useEffect, useRef } from 'react';
import { Canvas, useFrame } from '@react-three/fiber';
import { EffectComposer, Bloom } from '@react-three/postprocessing';
import CoreGlow from './orb/CoreGlow';
import InnerRing from './orb/InnerRing';
import OuterShell from './orb/OuterShell';
import { useOrbStore } from '../stores/orbStore';

/**
 * OrbScene — Internal R3F scene component.
 * Ticks the Zustand store every frame for smooth parameter interpolation.
 */
function OrbScene() {
    const groupRef = useRef();

    useFrame((_, delta) => {
        // Tick the state interpolation engine
        useOrbStore.getState().tick(delta);

        // Gentle whole-orb breathing (3s period, ±2% scale)
        if (groupRef.current) {
            const t = performance.now() * 0.001;
            const breathe = 1.0 + Math.sin(t * 2.094) * 0.02;
            groupRef.current.scale.setScalar(breathe);
        }
    });

    return (
        <group ref={groupRef}>
            <CoreGlow />
            <InnerRing />
            <OuterShell />
        </group>
    );
}

/**
 * CassandraOrb — Top-level component.
 * Renders the R3F Canvas with bloom post-processing.
 * Bridges window.orbState into the Zustand store.
 */
export default function CassandraOrb() {
    const setState = useOrbStore((s) => s.setState);
    const bloomStrength = useOrbStore((s) => s.params.bloomStrength);

    // Bridge legacy window.orbState into Zustand
    useEffect(() => {
        // Poll for window.orbState changes (set by useAudioPipeline)
        const interval = setInterval(() => {
            const currentState = window.orbState || 'idle';
            setState(currentState);

            // Also bridge audio energy if available
            if (window.getAudioEnergy) {
                const energy = window.getAudioEnergy();
                const level = Math.min(1, (energy.bass + energy.mid) / 3000);
                useOrbStore.getState().setAudioLevel(level);
            }
        }, 50); // 20Hz polling

        return () => clearInterval(interval);
    }, [setState]);

    return (
        <div
            style={{
                width: '100%',
                height: '100%',
                position: 'absolute',
                inset: 0,
                pointerEvents: 'none',
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'center',
            }}
        >
            <div style={{ width: '600px', height: '600px', maxWidth: '60vw', maxHeight: '60vh' }}>
                <Canvas
                    camera={{ position: [0, 0, 3.5], fov: 45 }}
                    gl={{
                        antialias: true,
                        alpha: true,
                        powerPreference: 'high-performance',
                    }}
                    style={{ background: 'transparent' }}
                    dpr={[1, 2]}
                >
                    <OrbScene />
                    <EffectComposer>
                        <Bloom
                            intensity={bloomStrength}
                            luminanceThreshold={0.4}
                            luminanceSmoothing={0.9}
                            mipmapBlur
                            radius={0.4}
                        />
                    </EffectComposer>
                </Canvas>
            </div>
        </div>
    );
}
