import React, { useEffect, useRef } from 'react';

const GrowthOrb = () => {
    const p5ContainerRef = useRef(null);
    const p5InstanceRef = useRef(null);

    useEffect(() => {
        // Only initialize once to prevent WebGL context bloat or DUPLICATE canvases
        if (p5InstanceRef.current) return;

        let myP5Instance;

        const initP5 = async () => {
            const p5 = (await import('p5')).default;
            window.p5 = p5;
            await import('p5/lib/addons/p5.sound.js');

            const sketch = (p) => {
                let numPoints = 800;
                let baseSpeed = 0.018;
                let bassSensitivity = 2.0;
                let rotSensitivity = 0.02;
                let rotDirection = 1;

                // Color Schemes
                const colorDormant1 = [255, 255, 255];
                const colorDormant2 = [100, 100, 100];
                const colorListening1 = [150, 200, 255];
                const colorListening2 = [50, 100, 150];
                const colorThinking1 = [200, 150, 255];
                const colorThinking2 = [100, 50, 150];
                const colorSpeaking1 = [255, 255, 200];
                const colorSpeaking2 = [150, 150, 50];

                let angleColor1 = [...colorDormant1];
                let angleColor2 = [...colorDormant2];
                let targetColor1 = [...colorDormant1];
                let targetColor2 = [...colorDormant2];

                let angleX = 0;
                let angleY = 0;
                let sphereRadius;
                let baseCircleSize = 5;

                let mic;
                let fft;

                p.setup = () => {
                    const canvas = p.createCanvas(p.windowWidth, p.windowHeight, p.WEBGL);
                    canvas.parent(p5ContainerRef.current);
                    sphereRadius = p.windowHeight * 0.15;
                    p.noStroke();

                    mic = new p5.AudioIn();
                    fft = new p5.FFT(0.8);
                    fft.setInput(mic);

                    p.userStartAudio();
                    mic.start();
                };

                p.draw = () => {
                    p.clear();

                    // Read from GLOBAL state to decouple from React lifecycle
                    const currentOrbState = window.orbState || 'idle';

                    if (currentOrbState === 'idle' || currentOrbState === 'DORMANT') {
                        targetColor1 = colorDormant1;
                        targetColor2 = colorDormant2;
                    } else if (currentOrbState === 'listening') {
                        targetColor1 = colorListening1;
                        targetColor2 = colorListening2;
                    } else if (currentOrbState === 'thinking') {
                        targetColor1 = colorThinking1;
                        targetColor2 = colorThinking2;
                    } else if (currentOrbState === 'speaking') {
                        targetColor1 = colorSpeaking1;
                        targetColor2 = colorSpeaking2;
                    }

                    // Smooth transitions
                    for (let i = 0; i < 3; i++) {
                        angleColor1[i] = p.lerp(angleColor1[i], targetColor1[i], 0.05);
                        angleColor2[i] = p.lerp(angleColor2[i], targetColor2[i], 0.05);
                    }

                    fft.analyze();
                    let bassEnergy = fft.getEnergy("bass");
                    let trebleEnergy = fft.getEnergy("treble");
                    let highMidEnergy = fft.getEnergy("highMid");

                    // External audio sync (from vocal response)
                    if (window.getAudioEnergy && currentOrbState === 'speaking') {
                        const energy = window.getAudioEnergy();
                        bassEnergy = p.max(bassEnergy, energy.bass || 0);
                        highMidEnergy = p.max(highMidEnergy, energy.mid || 0);
                        trebleEnergy = p.max(trebleEnergy, energy.treble || 0);
                    }

                    let melodyMap = p.map(trebleEnergy + highMidEnergy, 200, 512, 0, rotSensitivity);
                    let currentBaseSpeed = baseSpeed;
                    if (currentOrbState === 'listening') currentBaseSpeed = 0.005;
                    if (currentOrbState === 'thinking') currentBaseSpeed = 0.03;

                    angleY += (currentBaseSpeed + melodyMap) * rotDirection;
                    angleX += (currentBaseSpeed * 0.5 + melodyMap * 0.3) * rotDirection;

                    p.rotateY(angleY);
                    p.rotateX(angleX);

                    let goldenAngle = p.PI * (3 - p.sqrt(5));

                    for (let i = 0; i < numPoints; i++) {
                        let y = 1 - (i / (numPoints - 1)) * 2;
                        let radiusAtY = p.sqrt(1 - y * y);
                        let theta = goldenAngle * i;

                        let x = p.cos(theta) * radiusAtY;
                        let z = p.sin(theta) * radiusAtY;

                        let px = x * sphereRadius;
                        let py = y * sphereRadius;
                        let pz = z * sphereRadius;

                        p.push();
                        p.translate(px, py, pz);

                        let rotX = p.asin(y);
                        let rotY = p.atan2(x, z);

                        p.rotateY(rotY);
                        p.rotateX(rotX);

                        let inter = p.map(y, -1, 1, 0, 1);
                        let col = p.lerpColor(
                            p.color(angleColor2[0], angleColor2[1], angleColor2[2]),
                            p.color(angleColor1[0], angleColor1[1], angleColor1[2]),
                            inter
                        );

                        let bassNormal = p.map(bassEnergy, 0, 255, 0, 1);
                        let r = baseCircleSize + (p.pow(bassNormal, 3) * bassSensitivity * 12);

                        p.blendMode(p.ADD);
                        p.fill(p.red(col), p.green(col), p.blue(col), 50);
                        p.circle(0, 0, r * 2.5);

                        p.blendMode(p.BLEND);
                        p.fill(p.red(col), p.green(col), p.blue(col), 255);
                        p.circle(0, 0, r);

                        p.pop();
                    }
                };

                p.windowResized = () => {
                    p.resizeCanvas(p.windowWidth, p.windowHeight);
                    sphereRadius = p.windowHeight * 0.15;
                };
            };

            myP5Instance = new p5(sketch);
            p5InstanceRef.current = myP5Instance;
        };

        initP5();

        return () => {
            if (p5InstanceRef.current) {
                p5InstanceRef.current.remove();
                p5InstanceRef.current = null;
            }
        };
    }, []);

    return (
        <div className="relative flex items-center justify-center h-full w-full bg-black overflow-hidden pointer-events-none">
            {/* Ambient Glow Background */}
            <div className="absolute inset-0 bg-gradient-to-b from-slate-900 to-black z-0 pointer-events-none" />

            {/* P5 Canvas Container - PERSISTENT */}
            <div ref={p5ContainerRef} className="absolute inset-0 flex items-center justify-center z-10 pointer-events-none" style={{ mixBlendMode: 'screen' }} />

            {/* Orbiting Data Rings (Visible in Thinking mode) */}
            {window.orbState === 'thinking' && (
                <>
                    <div
                        className="absolute border border-cyan-500/30 rounded-full animate-[spin_10s_linear_infinite]"
                        style={{ width: '400px', height: '400px' }}
                    />
                    <div
                        className="absolute border border-purple-500/30 rounded-full animate-[spin_15s_linear_infinite_reverse]"
                        style={{ width: '450px', height: '450px' }}
                    />
                </>
            )}

            {/* Status Text overlay */}
            <div className="absolute bottom-20 text-center z-20 pointer-events-none">
                <h1 className="text-cyan-400 tracking-[0.3em] text-sm font-bold mb-2 uppercase">
                    {window.orbState || 'IDLE'}
                </h1>
                <p className="text-slate-500 text-xs font-mono">
                    {window.orbState === 'idle' && "SYSTEM DORMANT - CLICK AWAKEN"}
                    {window.orbState === 'listening' && "AWAITING INPUT STREAM..."}
                    {window.orbState === 'thinking' && "QUERYING CORTEX/LLM..."}
                    {window.orbState === 'speaking' && "GENERATING VOCAL RESPONSE..."}
                </p>
            </div>
        </div>
    );
};

export default GrowthOrb;
