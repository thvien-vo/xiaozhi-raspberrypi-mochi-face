// Eye shape presets for the robot face.
//
// Sizes are multiples of the base eye and lids are fractions of the eye height.
// Tilts are in degrees: positive lowers the inner corner, so a positive lidTopTilt
// frowns and a negative one looks worried. Values mirror between the two eyes.
// A preset can override single eyes through `left` and `right` (viewer's left/right).
.pragma library

var EYE_DEFAULTS = {
    w: 1,               // width
    h: 1,               // height
    r: 0.5,             // corner radius, 0 = square, 1 = pill
    dy: 0,              // vertical offset
    tilt: 0,
    lidTop: 0,
    lidTopTilt: 0,
    lidBot: 0,
    lidBotRound: 0,     // 1 = curved lower lid, which turns the eye into a smiling arc
    body: 1,            // opacity of the rounded body, 0 when a shape replaces it
    heart: 0,           // opacity of the heart shape
    spiral: 0           // opacity of the spiral shape
};

var ARC = { h: 0.9, lidBot: 0.5, lidBotRound: 1, r: 0.5 };
var ARC_WINK = { w: 0.95, h: 0.62, lidBot: 0.42, lidBotRound: 1, r: 0.5, dy: 0.06 };

var EMOTIONS = {
    neutral: {},
    happy: { eye: ARC, dy: -0.02 },
    laughing: { eye: { w: 1.08, h: 0.95, lidBot: 0.56, lidBotRound: 1, r: 0.5 }, motion: "bounce" },
    funny: { eye: { h: 0.85, lidBot: 0.52, lidBotRound: 1, r: 0.5, tilt: 12 }, motion: "bounce", overlay: "tears" },
    sad: { eye: { h: 0.82, lidTop: 0.3, lidTopTilt: -20, dy: 0.08 } },
    crying: { eye: { h: 0.78, lidTop: 0.34, lidTopTilt: -22, dy: 0.08 }, overlay: "tears" },
    angry: { eye: { h: 0.88, lidTop: 0.34, lidTopTilt: 24 }, motion: "shake" },
    surprised: { eye: { w: 1.1, h: 1.18, r: 0.72 } },
    shocked: { eye: { w: 0.66, h: 1.2, r: 0.8 }, motion: "shake" },
    thinking: {
        left: { h: 0.9, lidTop: 0.12 },
        right: { h: 0.78, lidTop: 0.28, lidTopTilt: -10 },
        gazeX: 0.09, gazeY: -0.08
    },
    confused: { eye: { body: 0, spiral: 1 } },
    loving: { eye: { body: 0, heart: 1 }, motion: "pulse" },
    kissy: { left: ARC_WINK, right: { h: 0.92 }, overlay: "floatheart" },
    winking: { left: ARC_WINK, right: { h: 0.95 } },
    cool: { eye: { w: 1.14, h: 0.5, r: 0.14, tilt: 3 } },
    confident: { eye: { lidTop: 0.26, lidTopTilt: 9 }, dy: -0.03 },
    relaxed: { eye: { h: 0.92, r: 0.6, lidTop: 0.42 }, dy: 0.03 },
    sleepy: { eye: { lidTop: 0.62, lidTopTilt: -6, dy: 0.06 } },
    delicious: { eye: ARC, overlay: "drool" },
    embarrassed: { eye: { h: 0.86, lidTop: 0.2, dy: 0.05 }, gazeY: 0.1, overlay: "blush" },
    silly: { left: { w: 1.18, h: 1.18, r: 0.8 }, right: { w: 0.62, h: 0.62, r: 0.8 }, motion: "wobble" },
    asleep: { eye: { h: 0.06, r: 1, dy: 0.1 }, overlay: "zzz" }
};

function merged(base, extra) {
    var out = {};
    for (var key in base) out[key] = base[key];
    if (extra) for (var k in extra) out[k] = extra[k];
    return out;
}

// Which preset wins: sleep overrides everything, and "thinking" only replaces a
// neutral face so an emotion the server already sent stays visible.
function activeName(emotion, visualState, activity) {
    if (activity === "asleep") return "asleep";
    if (activity === "sleepy") return "sleepy";
    if (!EMOTIONS.hasOwnProperty(emotion)) return "neutral";
    if (emotion === "neutral" && visualState === "thinking") return "thinking";
    return emotion;
}

function resolveEye(preset, side, visualState) {
    var eye = merged(merged(EYE_DEFAULTS, preset.eye), preset[side]);
    eye.dy += preset.dy || 0;
    if (visualState === "listening" && eye.body > 0 && eye.lidBot === 0) {
        // Attentive: eyes open wider, lids lift
        eye.w *= 1.06;
        eye.h *= 1.14;
        eye.lidTop *= 0.4;
    }
    return eye;
}

function compose(name, visualState) {
    var preset = EMOTIONS[name] || EMOTIONS.neutral;
    return {
        left: resolveEye(preset, "left", visualState),
        right: resolveEye(preset, "right", visualState),
        motion: preset.motion || "",
        overlay: preset.overlay || "",
        gazeX: preset.gazeX || 0,
        gazeY: preset.gazeY || 0
    };
}
