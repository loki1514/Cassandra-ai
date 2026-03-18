import * as THREE from 'three';

/**
 * Generates a soft radial gradient texture for particles.
 * White center fading to transparent edges — critical for
 * additive blending to produce volumetric glow, not hard circles.
 */
export function createParticleTexture(size = 64) {
    const canvas = document.createElement('canvas');
    canvas.width = size;
    canvas.height = size;

    const ctx = canvas.getContext('2d');
    const center = size / 2;

    const gradient = ctx.createRadialGradient(center, center, 0, center, center, center);
    gradient.addColorStop(0.0, 'rgba(255, 255, 255, 1.0)');
    gradient.addColorStop(0.2, 'rgba(255, 255, 255, 0.8)');
    gradient.addColorStop(0.5, 'rgba(255, 255, 255, 0.3)');
    gradient.addColorStop(0.8, 'rgba(255, 255, 255, 0.05)');
    gradient.addColorStop(1.0, 'rgba(255, 255, 255, 0.0)');

    ctx.fillStyle = gradient;
    ctx.fillRect(0, 0, size, size);

    const texture = new THREE.CanvasTexture(canvas);
    texture.needsUpdate = true;
    return texture;
}
