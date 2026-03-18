import React, { useRef, useMemo } from 'react';
import { useFrame } from '@react-three/fiber';
import * as THREE from 'three';
import { createParticleTexture } from './particleTexture';
import { useOrbStore } from '../../stores/orbStore';

/**
 * OuterShell — Layer 3
 * 3200 particles distributed on a Fibonacci sphere with Perlin noise displacement.
 * This is the primary visual element — the cloudy, nebula-like shell.
 *
 * Uses a custom vertex shader for GPU-side noise displacement.
 * Each particle stores its "home" position and gets displaced radially
 * by a 3D noise field that scrolls over time.
 */

const PARTICLE_COUNT = 8000;
const SPHERE_RADIUS = 1.0;

// Simple GLSL Perlin noise (3D) — inlined for zero dependencies
const noiseGLSL = /* glsl */ `
  // Classic Perlin 3D noise — compact implementation
  vec4 permute(vec4 x) { return mod(((x*34.0)+1.0)*x, 289.0); }
  vec4 taylorInvSqrt(vec4 r) { return 1.79284291400159 - 0.85373472095314 * r; }
  vec3 fade(vec3 t) { return t*t*t*(t*(t*6.0-15.0)+10.0); }

  float cnoise(vec3 P) {
    vec3 Pi0 = floor(P);
    vec3 Pi1 = Pi0 + vec3(1.0);
    Pi0 = mod(Pi0, 289.0);
    Pi1 = mod(Pi1, 289.0);
    vec3 Pf0 = fract(P);
    vec3 Pf1 = Pf0 - vec3(1.0);
    vec4 ix = vec4(Pi0.x, Pi1.x, Pi0.x, Pi1.x);
    vec4 iy = vec4(Pi0.yy, Pi1.yy);
    vec4 iz0 = Pi0.zzzz;
    vec4 iz1 = Pi1.zzzz;

    vec4 ixy = permute(permute(ix) + iy);
    vec4 ixy0 = permute(ixy + iz0);
    vec4 ixy1 = permute(ixy + iz1);

    vec4 gx0 = ixy0 / 7.0;
    vec4 gy0 = fract(floor(gx0) / 7.0) - 0.5;
    gx0 = fract(gx0);
    vec4 gz0 = vec4(0.5) - abs(gx0) - abs(gy0);
    vec4 sz0 = step(gz0, vec4(0.0));
    gx0 -= sz0 * (step(0.0, gx0) - 0.5);
    gy0 -= sz0 * (step(0.0, gy0) - 0.5);

    vec4 gx1 = ixy1 / 7.0;
    vec4 gy1 = fract(floor(gx1) / 7.0) - 0.5;
    gx1 = fract(gx1);
    vec4 gz1 = vec4(0.5) - abs(gx1) - abs(gy1);
    vec4 sz1 = step(gz1, vec4(0.0));
    gx1 -= sz1 * (step(0.0, gx1) - 0.5);
    gy1 -= sz1 * (step(0.0, gy1) - 0.5);

    vec3 g000 = vec3(gx0.x,gy0.x,gz0.x);
    vec3 g100 = vec3(gx0.y,gy0.y,gz0.y);
    vec3 g010 = vec3(gx0.z,gy0.z,gz0.z);
    vec3 g110 = vec3(gx0.w,gy0.w,gz0.w);
    vec3 g001 = vec3(gx1.x,gy1.x,gz1.x);
    vec3 g101 = vec3(gx1.y,gy1.y,gz1.y);
    vec3 g011 = vec3(gx1.z,gy1.z,gz1.z);
    vec3 g111 = vec3(gx1.w,gy1.w,gz1.w);

    vec4 norm0 = taylorInvSqrt(vec4(dot(g000,g000), dot(g010,g010), dot(g100,g100), dot(g110,g110)));
    g000 *= norm0.x; g010 *= norm0.y; g100 *= norm0.z; g110 *= norm0.w;
    vec4 norm1 = taylorInvSqrt(vec4(dot(g001,g001), dot(g011,g011), dot(g101,g101), dot(g111,g111)));
    g001 *= norm1.x; g011 *= norm1.y; g101 *= norm1.z; g111 *= norm1.w;

    float n000 = dot(g000, Pf0);
    float n100 = dot(g100, vec3(Pf1.x, Pf0.yz));
    float n010 = dot(g010, vec3(Pf0.x, Pf1.y, Pf0.z));
    float n110 = dot(g110, vec3(Pf1.xy, Pf0.z));
    float n001 = dot(g001, vec3(Pf0.xy, Pf1.z));
    float n101 = dot(g101, vec3(Pf1.x, Pf0.y, Pf1.z));
    float n011 = dot(g011, vec3(Pf0.x, Pf1.yz));
    float n111 = dot(g111, Pf1);

    vec3 fade_xyz = fade(Pf0);
    vec4 n_z = mix(vec4(n000, n100, n010, n110), vec4(n001, n101, n011, n111), fade_xyz.z);
    vec2 n_yz = mix(n_z.xy, n_z.zw, fade_xyz.y);
    float n_xyz = mix(n_yz.x, n_yz.y, fade_xyz.x);
    return 2.2 * n_xyz;
  }
`;

const vertexShader = /* glsl */ `
  ${noiseGLSL}

  attribute vec3 homePosition;
  attribute float aSize;
  attribute float aOpacity;

  uniform float u_time;
  uniform float u_noiseAmplitude;
  uniform float u_noiseTimeScale;
  uniform float u_directionalBias;
  uniform float u_audioImpulse;
  uniform float u_activationProgress;

  varying float vOpacity;
  varying float vDistFromCenter;

  void main() {
    vec3 pos = homePosition;
    float radius = length(pos);
    vec3 dir = normalize(pos);

    // 3D Perlin noise displacement (2 octaves)
    float noiseFreq = 1.5;
    float timeOffset = u_time * u_noiseTimeScale;

    float n1 = cnoise(pos * noiseFreq + timeOffset);
    float n2 = cnoise(pos * noiseFreq * 2.0 + timeOffset * 1.5) * 0.5;
    float noiseVal = n1 + n2;

    // Radial displacement
    float displacement = noiseVal * u_noiseAmplitude * radius;

    // Directional bias (Y-axis gradient for "deciding" state)
    displacement += dir.y * u_directionalBias * 0.05;

    // Audio impulse (radial push, decays in JS)
    displacement += u_audioImpulse * 0.03;

    pos = dir * (radius + displacement);

    // Pass to fragment
    vOpacity = aOpacity * u_activationProgress;
    vDistFromCenter = radius;

    vec4 mvPosition = modelViewMatrix * vec4(pos, 1.0);
    gl_PointSize = aSize * (150.0 / -mvPosition.z);
    gl_Position = projectionMatrix * mvPosition;
  }
`;

const fragmentShader = /* glsl */ `
  uniform sampler2D u_map;
  uniform vec3 u_baseColor;
  uniform vec3 u_cyanTint;
  uniform float u_colorTemp;

  varying float vOpacity;
  varying float vDistFromCenter;

  void main() {
    vec4 texColor = texture2D(u_map, gl_PointCoord);

    // Color lerp: particles closer to center get cyan tint
    float centerInfluence = 1.0 - smoothstep(0.0, 1.2, vDistFromCenter);
    vec3 color = mix(u_baseColor, u_cyanTint, centerInfluence * 0.5);

    // Color temperature shift
    vec3 warmColor = vec3(0.816, 0.878, 1.0); // #D0E0FF
    color = mix(color, warmColor, u_colorTemp * 0.3);

    gl_FragColor = vec4(color, vOpacity * texColor.a);
  }
`;

export default function OuterShell() {
    const pointsRef = useRef();
    const materialRef = useRef();
    const texture = useMemo(() => createParticleTexture(64), []);
    const audioImpulseRef = useRef(0);

    // Generate Fibonacci sphere positions
    const { homePositions, sizes, opacities } = useMemo(() => {
        const homePositions = new Float32Array(PARTICLE_COUNT * 3);
        const sizes = new Float32Array(PARTICLE_COUNT);
        const opacities = new Float32Array(PARTICLE_COUNT);

        const goldenAngle = Math.PI * (3 - Math.sqrt(5));

        for (let i = 0; i < PARTICLE_COUNT; i++) {
            // Fibonacci sphere
            const y = 1 - (i / (PARTICLE_COUNT - 1)) * 2; // -1 to 1
            const radiusAtY = Math.sqrt(1 - y * y);
            const theta = goldenAngle * i;

            const x = Math.cos(theta) * radiusAtY;
            const z = Math.sin(theta) * radiusAtY;

            // Jitter by ±5% for organic clustering
            const jitter = 0.95 + Math.random() * 0.1;

            homePositions[i * 3] = x * SPHERE_RADIUS * jitter;
            homePositions[i * 3 + 1] = y * SPHERE_RADIUS * jitter;
            homePositions[i * 3 + 2] = z * SPHERE_RADIUS * jitter;

            sizes[i] = 1.0 + Math.random() * 2.0; // 1–3px

            // Lower opacity at equator, higher at poles
            const poleWeight = Math.abs(y);
            opacities[i] = 0.05 + poleWeight * 0.10 + Math.random() * 0.05;
        }

        return { homePositions, sizes, opacities };
    }, []);

    const uniforms = useMemo(() => ({
        u_time: { value: 0 },
        u_noiseAmplitude: { value: 0.04 },
        u_noiseTimeScale: { value: 0.15 },
        u_directionalBias: { value: 0.0 },
        u_audioImpulse: { value: 0.0 },
        u_map: { value: texture },
        u_baseColor: { value: new THREE.Color('#FFFFFF') },
        u_cyanTint: { value: new THREE.Color('#D0E0FF') },
        u_colorTemp: { value: 0.0 },
        u_activationProgress: { value: 0.0 },
    }), [texture]);

    useFrame((_, delta) => {
        if (!materialRef.current) return;

        const { params, audioLevel } = useOrbStore.getState();

        // Update uniforms
        materialRef.current.uniforms.u_time.value += delta;
        materialRef.current.uniforms.u_noiseAmplitude.value = params.noiseAmplitude;
        materialRef.current.uniforms.u_noiseTimeScale.value = params.noiseTimeScale;
        materialRef.current.uniforms.u_directionalBias.value = params.directionalBias;
        materialRef.current.uniforms.u_colorTemp.value = params.colorTemp;
        materialRef.current.uniforms.u_activationProgress.value = activationProgress;

        // Audio impulse with 200ms decay
        audioImpulseRef.current = audioImpulseRef.current * Math.exp(-delta / 0.2) + audioLevel * 0.5 * delta;
        materialRef.current.uniforms.u_audioImpulse.value = audioImpulseRef.current;

        // Rotate shell
        if (pointsRef.current) {
            pointsRef.current.rotation.y += params.rotationSpeed * delta;
        }
    });

    return (
        <points ref={pointsRef}>
            <bufferGeometry>
                <bufferAttribute
                    attach="attributes-position"
                    count={PARTICLE_COUNT}
                    array={homePositions}
                    itemSize={3}
                />
                <bufferAttribute
                    attach="attributes-homePosition"
                    count={PARTICLE_COUNT}
                    array={homePositions}
                    itemSize={3}
                />
                <bufferAttribute
                    attach="attributes-aSize"
                    count={PARTICLE_COUNT}
                    array={sizes}
                    itemSize={1}
                />
                <bufferAttribute
                    attach="attributes-aOpacity"
                    count={PARTICLE_COUNT}
                    array={opacities}
                    itemSize={1}
                />
            </bufferGeometry>
            <shaderMaterial
                ref={materialRef}
                vertexShader={vertexShader}
                fragmentShader={fragmentShader}
                uniforms={uniforms}
                transparent
                depthWrite={false}
                blending={THREE.AdditiveBlending}
            />
        </points>
    );
}
