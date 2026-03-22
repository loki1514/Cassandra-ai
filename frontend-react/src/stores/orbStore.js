import { create } from 'zustand';

/**
 * State parameter targets for each PRD-defined state.
 * Each key maps to an object of interpolation targets.
 */
const STATE_PARAMS = {
    dormant: {
        coreIntensity: 0.2,
        noiseAmplitude: 0.02,
        rotationSpeed: 0.02,
        ringBrightness: 0.1,
        bloomStrength: 0.4,
        colorTemp: 0.0,
        wobbleAmount: 0.02,
        directionalBias: 0.0,
        tangentialFlow: 0.0,
        noiseTimeScale: 0.15,
    },
    activating: {
        coreIntensity: 0.6,
        noiseAmplitude: 0.06,
        rotationSpeed: 0.05,
        ringBrightness: 0.5,
        bloomStrength: 0.8,
        colorTemp: 0.5,
        wobbleAmount: 0.04,
        directionalBias: 0.0,
        tangentialFlow: 0.0,
        noiseTimeScale: 0.2,
    },
    listening: {
        coreIntensity: 0.4,
        noiseAmplitude: 0.04,
        rotationSpeed: 0.05,
        ringBrightness: 0.4,
        bloomStrength: 0.6,
        colorTemp: 0.3,
        wobbleAmount: 0.02,
        directionalBias: 0.0,
        tangentialFlow: 0.0,
        noiseTimeScale: 0.15,
    },
    conversations: { // fixed typo in state name check
        coreIntensity: 0.65,
        noiseAmplitude: 0.10,
        rotationSpeed: 0.15,
        ringBrightness: 0.6,
        bloomStrength: 1.0,
        colorTemp: 0.8,
        wobbleAmount: 0.08,
        directionalBias: 0.0,
        tangentialFlow: 0.6,
        noiseTimeScale: 0.3,
    },
    deciding: {
        coreIntensity: 0.7,
        noiseAmplitude: 0.06,
        rotationSpeed: 0.08,
        ringBrightness: 0.5,
        bloomStrength: 0.9,
        colorTemp: 0.5,
        wobbleAmount: 0.0,
        directionalBias: 0.8,
        tangentialFlow: 0.0,
        noiseTimeScale: 0.15,
    },
};

/**
 * Per-parameter time constants (tau) for exponential lerp.
 * Smaller = snappier response. Unit: seconds.
 */
const TAU = {
    coreIntensity: 0.3,
    noiseAmplitude: 0.8,
    rotationSpeed: 1.2,
    ringBrightness: 0.4,
    bloomStrength: 0.4,
    colorTemp: 0.6,
    wobbleAmount: 0.8,
    directionalBias: 1.0,
    tangentialFlow: 0.8,
    noiseTimeScale: 0.6,
};

/** Map existing pipeline states to PRD states */
const STATE_MAP = {
    idle: 'dormant',
    listening: 'listening',
    thinking: 'conversing',
    speaking: 'conversing',
    error: 'dormant',
};

const initialValues = { ...STATE_PARAMS.dormant };

export const useOrbStore = create((set, get) => ({
    // Current PRD state name
    orbState: 'dormant',

    // Audio level (0–1), updated at high frequency
    audioLevel: 0.0,

    // Interpolated parameter values (the actual values used for rendering)
    params: { ...initialValues },

    // Target parameter values (what we're lerping toward)
    targets: { ...initialValues },

    // Activation progress (0–1), special animation for ignition
    activationProgress: 0.0,
    activationTarget: 0.0,

    // Bloom intensity for the post-processing effect
    bloomIntensity: 1.5,

    setBloomIntensity: (val) => set({ bloomIntensity: val }),

    /**
     * Set the orb state. Accepts either PRD state names or legacy pipeline states.
     */
    setState: (state) => {
        const mapped = STATE_MAP[state] || state;
        const targets = STATE_PARAMS[mapped];
        if (!targets) return;

        set({
            orbState: mapped,
            targets: { ...targets },
            activationTarget: mapped === 'dormant' ? 0.0 : 1.0,
        });
    },

    /**
     * Set audio level (0–1). Called at high frequency from audio pipeline.
     */
    setAudioLevel: (level) => {
        set({ audioLevel: Math.max(0, Math.min(1, level)) });
    },

    /**
     * Called every frame. Exponential lerps all params toward targets.
     * dt = delta time in seconds.
     */
    tick: (dt) => {
        const { params, targets, activationProgress, activationTarget, audioLevel, orbState } = get();
        const newParams = { ...params };

        // Exponential lerp: value += (target - value) * (1 - e^(-dt/tau))
        for (const key of Object.keys(TAU)) {
            const tau = TAU[key];
            const alpha = 1 - Math.exp(-dt / tau);
            let target = targets[key];

            // Audio reactivity overlays
            if (key === 'coreIntensity') {
                const gain = orbState === 'conversing' ? 0.25 : 0.15;
                target = targets[key] + audioLevel * gain;
            }

            newParams[key] = params[key] + (target - params[key]) * alpha;
        }

        // Activation progress lerp (fast, 0.15s tau)
        const actAlpha = 1 - Math.exp(-dt / 0.15);
        const newActivation = activationProgress + (activationTarget - activationProgress) * actAlpha;

        set({
            params: newParams,
            activationProgress: newActivation,
        });
    },
}));
