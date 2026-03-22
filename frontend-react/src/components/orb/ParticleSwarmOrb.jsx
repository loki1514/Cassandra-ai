import React, { useRef, useMemo } from 'react';
import { useFrame } from '@react-three/fiber';
import * as THREE from 'three';
import { useOrbStore } from '../../stores/orbStore';

const PARTICLE_COUNT = 20000;

export default function ParticleSwarmOrb() {
    const meshRef = useRef();

    // Using InstancedMesh for high performance, ZERO garbage collection in loop
    const target = useMemo(() => new THREE.Vector3(), []);
    const color = useMemo(() => new THREE.Color(), []);
    const dummy = useMemo(() => new THREE.Object3D(), []);

    const controls = useRef({});

    // UI Helper mocks
    const addControl = (id, label, min, max, initialValue) => {
        if (controls.current[id] === undefined) {
            controls.current[id] = initialValue;
        }
        return controls.current[id];
    };

    const setInfo = (title, description) => {
        // Mock setInfo
    };

    const annotate = (id, positionVector, labelText) => {
        // Mock annotate
    };

    useFrame((state, delta) => {
        if (!meshRef.current) return;

        const time = state.clock.elapsedTime;
        const count = PARTICLE_COUNT;

        // Audio reactivity from store
        const { audioLevel } = useOrbStore.getState();
        const audioBoost = 1.0 + (audioLevel || 0) * 1.5;

        for (let i = 0; i < count; i++) {
            // --- START OF REQUESTED JAVASCRIPT FUNCTION BODY ---
            const scale = 1.3 * audioBoost;
            
            // Perfect Fibonacci Sphere distribution
            const y = 1 - (i / (count - 1)) * 2;
            const radiusAtY = Math.sqrt(1 - y * y);
            const goldenAngle = Math.PI * (3 - Math.sqrt(5));
            const theta = goldenAngle * i;
            
            const t = time * 0.5;
            
            // Base sphere coordinates
            let x = Math.cos(theta) * radiusAtY;
            let z = Math.sin(theta) * radiusAtY;
            
            // Organic, flowing noise fields using nested sine/cosine
            const f1 = 2.0; const f2 = 5.0;
            const n1 = Math.sin(x * f1 + t) * Math.cos(y * f1 - t) * Math.sin(z * f1 + t);
            const n2 = Math.sin(x * f2 - t * 1.5) * Math.cos(y * f2 + t * 1.5) * Math.sin(z * f2 - t * 1.5);
            
            // Composite noise from -1 to 1
            const noise = (n1 + n2 * 0.5) * 0.66;
            
            // Displace radially based on the noise map
            const displacement = 1.0 + (noise * 0.15) * audioBoost;
            
            x *= displacement * scale;
            let ry = y * displacement * scale;
            z *= displacement * scale;
            
            // Rotate the entire structure around Y over time
            const rotY = t * 0.3;
            const cx = Math.cos(rotY);
            const sx = Math.sin(rotY);
            const nx = x * cx - z * sx;
            const nz = x * sx + z * cx;
            
            target.set(nx, ry, nz);
            
            // Premium Cyan/Blue core aesthetic based on high/low displacement
            const intensity = (noise + 1.0) * 0.5; // 0.0 to 1.0
            
            // Hue shifts from 0.5 (Cyan) to 0.65 (Deep Blue)
            const hue = 0.5 + intensity * 0.15;
            const sat = 0.8 + intensity * 0.2;
            const lit = 0.2 + intensity * 0.5; // Brighter where pushed outward
            color.setHSL(hue, sat, lit);
            
            if (i === 0) setInfo("Cassandra Swarm", "Fluid Neural Architecture.");
            // --- END OF REQUESTED JAVASCRIPT FUNCTION BODY ---

            // Apply calculated position and color to InstancedMesh dummy
            dummy.position.copy(target);
            // Size particles dynamically based on their outward displacement
            dummy.scale.setScalar(0.005 + intensity * 0.005);
            dummy.updateMatrix();
            meshRef.current.setMatrixAt(i, dummy.matrix);
            meshRef.current.setColorAt(i, color);
        }

        meshRef.current.instanceMatrix.needsUpdate = true;
        if (meshRef.current.instanceColor) {
            meshRef.current.instanceColor.needsUpdate = true;
        }
    });

    return (
        <instancedMesh ref={meshRef} args={[null, null, PARTICLE_COUNT]}>
            <sphereGeometry args={[1, 8, 8]} />
            <meshBasicMaterial toneMapped={false} />
        </instancedMesh>
    );
}
