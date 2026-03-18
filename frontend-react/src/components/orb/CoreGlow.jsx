import React, { useRef, useMemo } from 'react';
import { useFrame } from '@react-three/fiber';
import * as THREE from 'three';
import { useOrbStore } from '../../stores/orbStore';

/**
 * CoreGlow — Layer 1 (V4 - Sharp Void Edition)
 * A ring-shaped glow with a true dark center void.
 * The center is absolute black (alpha 0).
 */

const vertexShader = /* glsl */ `
  varying vec2 vUv;
  void main() {
    vUv = uv;
    gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0);
  }
`;

const fragmentShader = /* glsl */ `
  uniform float u_intensity;
  uniform float u_time;
  uniform float u_activationProgress;

  varying vec2 vUv;

  void main() {
    vec2 center = vec2(0.5, 0.5);
    float dist = distance(vUv, center) * 2.0;

    // Breathing modulation
    float breathe = sin(u_time * 2.0) * 0.02;

    float d = dist / max(u_activationProgress, 0.001);

    // Solid core peak at center (d=0)
    // Core radius should be approx 0.35
    float coreRadius = 0.45 + breathe;
    float coreFalloff = 0.25;

    // Exponential falloff from the center
    float coreGlow = exp(-pow(d, 2.0) / (2.0 * pow(coreRadius, 2.0)));

    // Outer cutoff: fade out quickly at the edge
    float edgeMask = 1.0 - smoothstep(0.4, 0.7, d);

    float alpha = coreGlow * edgeMask;

    // Brightest at the center, then cyan
    vec3 white = vec3(1.0, 1.0, 1.0);
    vec3 cyan  = vec3(0.0, 0.9, 1.0);
    // Higher power for white peak consolidation
    vec3 color = mix(cyan, white, pow(coreGlow, 4.0));

    // Final apply
    gl_FragColor = vec4(color, alpha * u_intensity * u_activationProgress);
  }
`;

export default function CoreGlow() {
  const meshRef = useRef();
  const materialRef = useRef();

  const uniforms = useMemo(() => ({
    u_intensity: { value: 0.15 },
    u_time: { value: 0.0 },
    u_activationProgress: { value: 0.0 },
  }), []);

  useFrame((_, delta) => {
    const { params, activationProgress } = useOrbStore.getState();

    if (materialRef.current) {
      materialRef.current.uniforms.u_intensity.value = params.coreIntensity;
      materialRef.current.uniforms.u_time.value += delta;
      materialRef.current.uniforms.u_activationProgress.value = activationProgress;
    }
  });

  return (
    <mesh ref={meshRef} position={[0, 0, -0.05]}>
      <planeGeometry args={[2, 2]} />
      <shaderMaterial
        ref={materialRef}
        vertexShader={vertexShader}
        fragmentShader={fragmentShader}
        uniforms={uniforms}
        transparent
        depthWrite={false}
        blending={THREE.AdditiveBlending}
      />
    </mesh>
  );
}
