import React, { useRef, useMemo } from 'react';
import { useFrame } from '@react-three/fiber';
import * as THREE from 'three';
import { createParticleTexture } from './particleTexture';
import { useOrbStore } from '../../stores/orbStore';

/**
 * InnerRing — Layer 2
 * 1000 particles distributed on a torus geometry.
 * Creates the bright cyan glowing ring visible around the core.
 * The entire torus rotates as a unit — particles don't move individually.
 */

const PARTICLE_COUNT = 3000;
const MAJOR_RADIUS = 0.25; // Distance from center
const MINOR_RADIUS = 0.05; // Tube thickness

export default function InnerRing() {
    const pointsRef = useRef();
    const texture = useMemo(() => createParticleTexture(32), []);

    // Generate torus-distributed positions
    const { positions, sizes, opacities } = useMemo(() => {
        const positions = new Float32Array(PARTICLE_COUNT * 3);
        const sizes = new Float32Array(PARTICLE_COUNT);
        const opacities = new Float32Array(PARTICLE_COUNT);

        for (let i = 0; i < PARTICLE_COUNT; i++) {
            // Parametric torus sampling
            const u = Math.random() * Math.PI * 2; // Around the ring
            const v = Math.random() * Math.PI * 2; // Around the tube

            const x = (MAJOR_RADIUS + MINOR_RADIUS * Math.cos(v)) * Math.cos(u);
            const y = MINOR_RADIUS * Math.sin(v);
            const z = (MAJOR_RADIUS + MINOR_RADIUS * Math.cos(v)) * Math.sin(u);

            positions[i * 3] = x;
            positions[i * 3 + 1] = y;
            positions[i * 3 + 2] = z;

            sizes[i] = 2 + Math.random() * 2; // 2–4px
            opacities[i] = 0.4 + Math.random() * 0.4; // 0.4–0.8
        }

        return { positions, sizes, opacities };
    }, []);

    // Animation: rotate torus, wobble X axis
    useFrame((_, delta) => {
        if (!pointsRef.current) return;

        const { params } = useOrbStore.getState();
        const time = performance.now() * 0.001;

        // Y-axis rotation (base 0.1 rad/s, scaled by state)
        pointsRef.current.rotation.y += params.rotationSpeed * delta * 2;

        // X-axis wobble (sinusoidal)
        pointsRef.current.rotation.x = Math.sin(time * 1.57) * params.wobbleAmount; // period ~4s

        // Brightness via opacity (scale material)
        if (pointsRef.current.material) {
            const { activationProgress } = useOrbStore.getState();
            pointsRef.current.material.opacity = params.ringBrightness * activationProgress;
        }
    });

    return (
        <points ref={pointsRef}>
            <bufferGeometry>
                <bufferAttribute
                    attach="attributes-position"
                    count={PARTICLE_COUNT}
                    array={positions}
                    itemSize={3}
                />
                <bufferAttribute
                    attach="attributes-size"
                    count={PARTICLE_COUNT}
                    array={sizes}
                    itemSize={1}
                />
            </bufferGeometry>
            <pointsMaterial
                map={texture}
                color={new THREE.Color('#60D0FF')}
                size={0.008}
                sizeAttenuation
                transparent
                opacity={0.5}
                blending={THREE.AdditiveBlending}
                depthWrite={false}
            />
        </points>
    );
}
