let orbState = 'DORMANT';
window.orbState = orbState;
let numPoints = 800;

let baseSpeed = 0.018;
let bassSensitivity = 2.0;
let rotSensitivity = 0.02;
let rotDirection = 1;

// Mode Colors
const modeColors = {
    'GENERAL': { c1: [138, 43, 226], c2: [70, 20, 110] }, // violet
    'MARKETING': { c1: [255, 0, 255], c2: [120, 0, 120] }, // magenta
    'GROWTH': { c1: [57, 255, 20], c2: [25, 120, 10] }, // neon green
    'OPS': { c1: [255, 165, 0], c2: [130, 80, 0] }, // orange
    'TECH': { c1: [0, 255, 255], c2: [0, 120, 120] } // cyan
};

let currentMode = 'GENERAL';
let colorDormant1 = [...modeColors['GENERAL'].c1];
let colorDormant2 = [...modeColors['GENERAL'].c2];

let colorListening1 = [0, 200, 255]; // blue glow
let colorListening2 = [0, 150, 180];

let colorThinking1 = [170, 50, 255]; // purple pulse
let colorThinking2 = [80, 0, 150];

let colorSpeaking1 = [255, 215, 0]; // gold energy
let colorSpeaking2 = [200, 150, 0];

let colorMemory1 = [255, 255, 255]; // white pulse
let colorMemory2 = [220, 220, 220];

let angleColor1 = [...colorDormant1];
let angleColor2 = [...colorDormant2];
let targetColor1 = [...colorDormant1];
let targetColor2 = [...colorDormant2];

let angleX = 0;
let angleY = 0;
let sphereRadius;
let baseCircleSize = 5;

let isAudioStarted = false;
let mic;
let fft;

function setup() {
    createCanvas(windowWidth, windowHeight, WEBGL);
    sphereRadius = windowHeight * 0.13;
    noStroke();

    mic = new p5.AudioIn();
    fft = new p5.FFT(0.8);
    fft.setInput(mic);
}

function draw() {
    clear(); // transparent so CSS background shows

    if (!isAudioStarted) {
        return;
    }

    if (orbState === 'DORMANT') {
        targetColor1 = colorDormant1;
        targetColor2 = colorDormant2;
    } else if (orbState === 'LISTENING') {
        targetColor1 = colorListening1;
        targetColor2 = colorListening2;
    } else if (orbState === 'THINKING') {
        targetColor1 = colorThinking1;
        targetColor2 = colorThinking2;
    } else if (orbState === 'SPEAKING') {
        targetColor1 = colorSpeaking1;
        targetColor2 = colorSpeaking2;
    } else if (orbState === 'MEMORY_RECALL') {
        targetColor1 = colorMemory1;
        targetColor2 = colorMemory2;
    }

    // Lerp colors for smooth transitions
    for (let i = 0; i < 3; i++) {
        angleColor1[i] = lerp(angleColor1[i], targetColor1[i], 0.05);
        angleColor2[i] = lerp(angleColor2[i], targetColor2[i], 0.05);
    }

    fft.analyze();
    let bassEnergy = fft.getEnergy("bass");
    let trebleEnergy = fft.getEnergy("treble");
    let highMidEnergy = fft.getEnergy("highMid");

    if ((orbState === 'SPEAKING' || orbState === 'MEMORY_RECALL') && window.getAudioEnergy) {
        let energy = window.getAudioEnergy();
        bassEnergy = max(bassEnergy, energy.bass || 0);
        highMidEnergy = max(highMidEnergy, energy.mid || 0);
        trebleEnergy = max(trebleEnergy, energy.treble || 0);
    }

    let melodyMap = map(trebleEnergy + highMidEnergy, 200, 512, 0, rotSensitivity);

    let currentBaseSpeed = baseSpeed;
    if (orbState === 'LISTENING') currentBaseSpeed = 0.005;
    if (orbState === 'THINKING') currentBaseSpeed = 0.03;

    angleY += (currentBaseSpeed + melodyMap) * rotDirection;
    angleX += (currentBaseSpeed * 0.5 + melodyMap * 0.3) * rotDirection;

    rotateY(angleY);
    rotateX(angleX);

    let goldenAngle = PI * (3 - sqrt(5));

    for (let i = 0; i < numPoints; i++) {
        let y = 1 - (i / (numPoints - 1)) * 2;
        let radiusAtY = sqrt(1 - y * y);
        let theta = goldenAngle * i;

        let x = cos(theta) * radiusAtY;
        let z = sin(theta) * radiusAtY;

        let px = x * sphereRadius;
        let py = y * sphereRadius;
        let pz = z * sphereRadius;

        push();
        translate(px, py, pz);

        let rotX = asin(y);
        let rotY = atan2(x, z);

        rotateY(rotY);
        rotateX(rotX);

        let inter = map(y, -1, 1, 0, 1);
        let c1 = color(angleColor1[0], angleColor1[1], angleColor1[2]);
        let c2 = color(angleColor2[0], angleColor2[1], angleColor2[2]);
        let col = lerpColor(c2, c1, inter);

        let bassNormal = map(bassEnergy, 0, 255, 0, 1);
        let bassPump = pow(bassNormal, 3);
        let r = baseCircleSize + (bassPump * bassSensitivity * 12);

        blendMode(ADD);
        fill(red(col), green(col), blue(col), 50);
        circle(0, 0, r * 2.5);

        blendMode(BLEND);
        fill(red(col), green(col), blue(col), 255);
        circle(0, 0, r);

        pop();
    }
}

function windowResized() {
    resizeCanvas(windowWidth, windowHeight);
    sphereRadius = windowHeight * 0.13;
}

window.setOrbState = function (state) {
    orbState = state;
    window.orbState = state;
    document.getElementById('status-text').innerText = `SYSTEM ${state}`;
};

window.setAgentMode = function (mode) {
    currentMode = mode;
    colorDormant1 = [...modeColors[mode].c1];
    colorDormant2 = [...modeColors[mode].c2];
    document.getElementById('sub-status').innerText = `Initializing ${mode} Protocol...`;
    setTimeout(() => {
        document.getElementById('sub-status').innerText = `${mode} PROTOCOL ACTIVE`;
    }, 1500);
};

document.addEventListener("DOMContentLoaded", () => {
    const clickTarget = document.getElementById('orb-click-target');
    if (clickTarget) {
        clickTarget.addEventListener('click', () => {
            if (!isAudioStarted) {
                userStartAudio();
                mic.start();
                isAudioStarted = true;

                if (window.initBackendPipeline) {
                    window.initBackendPipeline();
                }
            }
        });
    }
});
