export const STATES = {
  ready: { color: [0.22, 0.80, 1.00], deep: [0.17, 0.38, 1.00], energy: 0.80, spin: 0.32, flow: 0.50 },
  listening: { color: [0.26, 1.00, 0.80], deep: [0.08, 0.62, 1.00], energy: 1.0, spin: 0.55, flow: 0.95 },
  thinking: { color: [0.66, 0.50, 1.00], deep: [0.96, 0.34, 0.84], energy: 0.95, spin: 2.6, flow: 1.8 },
  speaking: { color: [0.34, 0.80, 1.00], deep: [0.48, 0.38, 1.00], energy: 1.0, spin: 0.75, flow: 1.3 },
  muted: { color: [0.48, 0.54, 0.63], deep: [0.28, 0.32, 0.42], energy: 0.42, spin: 0.10, flow: 0.15 },
  starting: { color: [0.38, 0.58, 0.86], deep: [0.22, 0.30, 0.72], energy: 0.62, spin: 1.5, flow: 0.60 },
  error: { color: [1.00, 0.70, 0.28], deep: [1.00, 0.34, 0.20], energy: 0.82, spin: 0.25, flow: 0.40 },
};
STATES.hidden = STATES.muted;
STATES.away = STATES.muted;

const TAU = Math.PI * 2;
const LIVELY = new Set(['listening', 'thinking', 'speaking', 'starting']);
const CLOUDS = [[0.42, 0.9, 0.62, 0.0], [0.38, -0.7, 0.56, 2.1], [0.3, 1.3, 0.42, 4.0]];

const VERTEX = `#version 300 es
in vec2 aPosition;
void main() { gl_Position = vec4(aPosition, 0.0, 1.0); }`;

const FRAGMENT = `#version 300 es
precision highp float;
uniform vec2 uCenter;
uniform float uK;
uniform vec3 uColor;
uniform vec3 uDeep;
uniform float uEnergy;
uniform float uRadius;
uniform float uBreath;
uniform float uLevel;
uniform float uVoice;
uniform float uFlash;
uniform vec4 uMix;
uniform float uBusy;
uniform vec4 uTurns;
uniform vec4 uTimes;
uniform vec3 uSparks;
uniform float uTrail;
uniform float uGrain;
uniform vec4 uCloudA;
uniform vec2 uCloudB;
uniform vec3 uSpread;
uniform vec2 uRipples[8];
out vec4 fragColor;

const float TAU = 6.283185307179586;
const float PI = 3.141592653589793;
const vec3 SMOKE = vec3(0.02, 0.04, 0.075);
const vec3 GLASS = vec3(0.01, 0.02, 0.045);
const vec3 SATELLITE = vec3(0.78, 0.63, 1.0);

float sq(float x) { return x * x; }
float band(float r, float radius, float width) { return clamp(width * 0.5 + 0.5 - abs(r - radius), 0.0, 1.0); }
float arcMask(float a, float start, float extent, float radius) {
  float d = abs(mod(a - start - extent * 0.5 + PI, TAU) - PI);
  return clamp((extent * 0.5 - d) * radius + 0.5, 0.0, 1.0);
}
float gaps(float a, float k) {
  float g = 1.0;
  for (int i = 0; i < 4; i++) g *= 1.0 - arcMask(a, float(i) * TAU / 4.0, 0.16, 30.0 * k);
  return g;
}
float ticks(float a, float k) {
  float phase = fract(a / TAU * 72.0);
  return clamp((0.17 - abs(phase - 0.5)) * (TAU * 36.5 * k / 72.0) + 0.5, 0.0, 1.0);
}
float windowShape(float a) { return 0.12 + 0.3 * sq(0.5 + 0.5 * cos(a)); }
float arcs(float a, float k) {
  float r = 42.5 * k;
  return arcMask(a, 0.0, 1.25, r) + arcMask(a, PI, 0.55, r) + arcMask(a, 2.25, 0.16, r);
}
float trail(float a) { return pow(max(0.0, 1.0 - mod(-a, TAU) / TAU), 6.0); }
float tail(float a) { return sq(clamp(1.0 - mod(-a, TAU) / 0.9, 0.0, 1.0)); }
float spark(vec2 p, float angle, float orbit, float dotSize, float k) {
  float d = distance(p, vec2(cos(angle), sin(angle)) * orbit);
  return clamp(dotSize + 0.5 - d, 0.0, 1.0) + exp(-sq(d / (4.5 * k))) * 0.55;
}

void main() {
  float k = uK;
  vec2 p = vec2(gl_FragCoord.x - uCenter.x, uCenter.y - gl_FragCoord.y);
  float r = length(p);
  float theta = atan(p.y, p.x);
  vec3 color = uColor;
  vec3 hot = color * 0.35 + 0.65;
  float energy = uEnergy;
  float radius = uRadius;
  float edge = clamp((66.0 * k - 1.5 - r) / (6.0 * k), 0.0, 1.0);

  float outside = max(r - radius, 0.0);
  float halo = min((exp(-sq(outside / (6.5 * k))) * 0.5 + exp(-sq(outside / (17.0 * k))) * 0.2) * energy * edge, 1.0);
  float e = clamp((48.0 * k - r) / (7.0 * k), 0.0, 1.0);
  float shade = e * e * (3.0 - 2.0 * e) * 0.5 * (1.0 - halo);
  vec3 rgb = color * halo + SMOKE * shade;
  float alpha = halo + shade;

  if (r < 31.5 * k) {
    float depth = min(r / radius, 1.0);
    float c0 = exp(-dot(p - uCloudA.xy, p - uCloudA.xy) / sq(uSpread.x * radius));
    float c1 = exp(-dot(p - uCloudA.zw, p - uCloudA.zw) / sq(uSpread.y * radius));
    float c2 = exp(-dot(p - uCloudB, p - uCloudB) / sq(uSpread.z * radius));
    float grain = 0.8 + 0.2 * sin(3.0 * (theta + depth * 2.6 - uGrain) + depth * 4.0);
    float heart = exp(-sq(r / (radius * 0.3))) * (0.5 + 0.25 * uBreath + 0.55 * uLevel * uVoice + 0.45 * uFlash);
    float cube = depth * depth * depth;
    float rim = cube * cube * cube * 0.95 + cube * 0.22;
    float spot = exp(-(sq((p.x + radius * 0.34) / (radius * 0.36)) + sq((p.y + radius * 0.44) / (radius * 0.2)))) * 0.3;
    vec3 body = (uDeep * 0.1 + GLASS) * (1.0 - 0.3 * depth)
              + color * ((c0 * 0.62 * grain + rim) * energy)
              + uDeep * ((c1 * 0.7 * grain + rim * 0.35) * energy)
              + hot * ((c2 * 0.3 + heart) * energy) + spot;
    if (uMix.w > 0.01) {
      float along = clamp((p.x - p.y) * 0.7071, -radius * 0.62, radius * 0.62);
      float slash = clamp(1.25 * k + 0.5 - length(vec2(p.x - along * 0.7071, p.y + along * 0.7071)), 0.0, 1.0) * uMix.w;
      body = body * (1.0 - slash) + 0.8 * slash;
    }
    float cover = clamp(radius - r + 0.5, 0.0, 1.0) * 0.97;
    rgb = body * cover + rgb * (1.0 - cover);
    alpha = cover + alpha * (1.0 - cover);
  }

  if (r > 17.0 * k && r < 53.0 * k) {
    float line = band(r, radius + 5.5 * k, 0.9 * k) * gaps(theta - uTurns.x, k) * 0.4;
    line += band(r, 36.5 * k, 2.6 * k) * ticks(theta - uTurns.y, k) * windowShape(theta - uTurns.z);
    float arcValue = arcs(theta - uTurns.w, k) * (1.0 - 0.55 * uMix.y);
    line += band(r, 42.5 * k, 1.7 * k) * arcValue * 0.95;
    float glow = exp(-sq((r - 42.5 * k) / (3.2 * k))) * arcValue * 0.35;
    float sat = 0.0;
    for (int i = 0; i < 8; i++) {
      vec2 ripple = uRipples[i];
      if (ripple.y <= 0.0) continue;
      float d = abs(r - (radius + (3.0 + ripple.x * 30.0) * k));
      float fade = sq(1.0 - ripple.x) * ripple.y;
      line += clamp(0.7 * k + 0.5 - d, 0.0, 1.0) * fade * 0.7;
      glow += exp(-sq(d / (3.0 * k))) * fade * 0.3;
    }
    if (uMix.z > 0.01) {
      float sweep = clamp((r - radius - 2.0 * k) / (3.0 * k), 0.0, 1.0) * clamp((39.5 * k - r) / (3.0 * k), 0.0, 1.0);
      glow += sweep * trail(theta - uTrail) * 0.45 * uMix.z;
    }
    if (uBusy > 0.01) sat += band(r, 50.0 * k, 1.4 * k) * tail(theta - uSparks.z) * 0.55 * uBusy;
    if (uMix.y > 0.01) {
      float amplitude = (1.0 + 5.5 * uLevel) * k * uMix.y;
      for (int i = 0; i < 2; i++) {
        float shift = i == 0 ? 0.0 : 2.4;
        float strength = i == 0 ? 1.0 : 0.45;
        float wave = sin(3.0 * theta + uTimes.x + shift) * 0.62 + sin(5.0 * theta - uTimes.y + 1.3 + shift) * 0.38;
        float d = abs(r - (radius + 8.0 * k + amplitude * wave));
        line += clamp(0.85 * k + 0.5 - d, 0.0, 1.0) * strength * uMix.y;
        glow += exp(-sq(d / (2.6 * k))) * 0.4 * strength * uMix.y;
      }
    }
    glow *= edge * energy;
    line *= edge * min(1.0, 0.45 + 0.6 * energy);
    rgb += color * glow + hot * line + SATELLITE * sat;
    alpha += glow + line * 0.9 + sat;
  }

  if (uMix.z > 0.01) {
    float light = (spark(p, uSparks.x, 31.0 * k, 1.6 * k, k) + spark(p, uSparks.y, 48.0 * k, 1.6 * k, k)) * uMix.z * edge;
    rgb += hot * light;
    alpha += light;
  }
  if (uBusy > 0.01) {
    float light = spark(p, uSparks.z, 50.0 * k, 2.3 * k, k) * uBusy * edge;
    rgb += SATELLITE * light;
    alpha += light;
  }
  float cover = clamp(alpha, 0.0, 1.0);
  fragColor = vec4(clamp(rgb, vec3(0.0), vec3(cover)), cover);
}`;

const UNIFORMS = ['uCenter', 'uK', 'uColor', 'uDeep', 'uEnergy', 'uRadius', 'uBreath', 'uLevel', 'uVoice', 'uFlash',
  'uMix', 'uBusy', 'uTurns', 'uTimes', 'uSparks', 'uTrail', 'uGrain', 'uCloudA', 'uCloudB', 'uSpread', 'uRipples'];

function wrap(angle) {
  return ((angle % TAU) + TAU) % TAU;
}

export class OrbRenderer {
  // size is the orb's resting box. With fill the canvas covers the whole window, so the orb can be drawn anywhere in
  // it and at any scale; otherwise the canvas is just the orb.
  constructor(canvas, { size = 132, interactive = true, fill = false } = {}) {
    this.canvas = canvas;
    this.size = size;
    this.interactive = interactive;
    this.fill = fill;
    this.center = null;
    this.scale = 1;
    this.moving = false;
    this.onFrame = null;
    this.state = 'starting';
    this.color = [...STATES.starting.color];
    this.deep = [...STATES.starting.deep];
    this.energy = 0.62;
    this.spin = 1.5;
    this.flow = 0.6;
    this.mix = { listening: 0, speaking: 0, thinking: 0, muted: 0, busy: 0 };
    this.turn = 0;
    this.swirl = 0;
    this.level = 0;
    this.input = 0;
    this.inputAt = -Infinity;
    this.flash = 0;
    this.ripples = [];
    this.lastRipple = 0;
    this.last = null;
    this.drawn = 0;
    this.busy = false;
    this.hover = false;
    this.running = false;
    this.frameHandle = 0;
    this.ripplesBuffer = new Float32Array(16);
    this.resize();
    this.gl = this.setup();
    canvas.addEventListener('webglcontextlost', (event) => {
      event.preventDefault();
      this.gl = null;
    });
    canvas.addEventListener('webglcontextrestored', () => {
      this.gl = this.setup();
    });
  }

  resize() {
    const ratio = Math.max(1, window.devicePixelRatio || 1);
    const width = this.fill ? Math.max(1, window.innerWidth || this.size) : this.size;
    const height = this.fill ? Math.max(1, window.innerHeight || this.size) : this.size;
    if (ratio === this.ratio && width === this.width && height === this.height) return;
    this.ratio = ratio;
    this.width = width;
    this.height = height;
    this.canvas.width = Math.round(width * ratio);
    this.canvas.height = Math.round(height * ratio);
    this.canvas.style.width = width + 'px';
    this.canvas.style.height = height + 'px';
    this.pixels = Math.round(this.size * ratio);
  }

  // Where the orb's centre sits in the canvas (CSS pixels) and how large it is drawn.
  setLayout({ cx = null, cy = null, scale = 1 } = {}) {
    this.center = Number.isFinite(cx) && Number.isFinite(cy) ? { x: cx, y: cy } : null;
    this.scale = Number.isFinite(scale) && scale > 0 ? scale : 1;
  }

  get centerX() {
    return this.center ? this.center.x : this.width / 2;
  }

  get centerY() {
    return this.center ? this.center.y : this.height / 2;
  }

  setup() {
    const gl = this.canvas.getContext('webgl2', { alpha: true, premultipliedAlpha: true, antialias: false,
                                                    depth: false, stencil: false, powerPreference: 'low-power' });
    if (!gl) {
      this.fallback = this.canvas.getContext('2d');
      return null;
    }
    const compile = (type, source) => {
      const shader = gl.createShader(type);
      gl.shaderSource(shader, source);
      gl.compileShader(shader);
      if (!gl.getShaderParameter(shader, gl.COMPILE_STATUS)) throw new Error(gl.getShaderInfoLog(shader) || 'Orb shader failed');
      return shader;
    };
    const program = gl.createProgram();
    gl.attachShader(program, compile(gl.VERTEX_SHADER, VERTEX));
    gl.attachShader(program, compile(gl.FRAGMENT_SHADER, FRAGMENT));
    gl.linkProgram(program);
    if (!gl.getProgramParameter(program, gl.LINK_STATUS)) throw new Error(gl.getProgramInfoLog(program) || 'Orb program failed');
    gl.useProgram(program);
    const buffer = gl.createBuffer();
    gl.bindBuffer(gl.ARRAY_BUFFER, buffer);
    gl.bufferData(gl.ARRAY_BUFFER, new Float32Array([-1, -1, 3, -1, -1, 3]), gl.STATIC_DRAW);
    const position = gl.getAttribLocation(program, 'aPosition');
    gl.enableVertexAttribArray(position);
    gl.vertexAttribPointer(position, 2, gl.FLOAT, false, 0, 0);
    this.uniforms = Object.fromEntries(UNIFORMS.map((name) => [name, gl.getUniformLocation(program, name)]));
    gl.disable(gl.BLEND);
    return gl;
  }

  setState(state) {
    this.state = STATES[state] ? state : 'ready';
  }

  setLevel(level, now = performance.now() / 1000) {
    this.input = Math.max(0, Math.min(1, Number(level) || 0));
    this.inputAt = now;
  }

  setBusy(busy) {
    this.busy = Boolean(busy);
  }

  setHover(hover) {
    this.hover = Boolean(hover);
  }

  pulse(now = performance.now() / 1000) {
    this.flash = 1;
    this.ripples.push([now, 1]);
  }

  interval() {
    if (this.moving) return 0;
    if (this.busy || LIVELY.has(this.state) || this.flash > 0.02 || this.ripples.length) return 1000 / 60;
    return this.state === 'muted' || this.state === 'hidden' || this.state === 'away' ? 1000 / 15 : 1000 / 30;
  }

  start() {
    if (this.running) return;
    this.running = true;
    const loop = (time) => {
      if (!this.running) return;
      this.frameHandle = requestAnimationFrame(loop);
      this.onFrame?.(time);
      if (time - this.drawn < this.interval() - 2) return;
      this.drawn = time;
      this.frame(time / 1000);
    };
    this.frameHandle = requestAnimationFrame(loop);
  }

  stop() {
    this.running = false;
    cancelAnimationFrame(this.frameHandle);
    this.last = null;
  }

  frame(now) {
    const dt = this.last === null ? 0.04 : Math.min(0.12, Math.max(0, now - this.last));
    this.last = now;
    const mood = STATES[this.state] ?? STATES.ready;
    const ease = 1 - Math.exp(-dt * 5);
    for (let index = 0; index < 3; index++) {
      this.color[index] += (mood.color[index] - this.color[index]) * ease;
      this.deep[index] += (mood.deep[index] - this.deep[index]) * ease;
    }
    this.energy += (mood.energy + (this.hover ? 0.12 : 0) - this.energy) * ease;
    this.spin += (mood.spin - this.spin) * ease;
    this.flow += (mood.flow - this.flow) * ease;
    const blend = 1 - Math.exp(-dt * 6);
    for (const key of Object.keys(this.mix)) {
      const goal = key === 'busy' ? Number(this.busy) : Number(this.state === key);
      this.mix[key] += (goal - this.mix[key]) * blend;
    }
    this.turn = (this.turn + this.spin * dt) % (TAU * 3600);
    this.swirl = (this.swirl + this.flow * dt) % (TAU * 3600);
    const input = now - this.inputAt < 0.4 ? this.input : 0;
    const response = input > this.level ? 1 - Math.pow(0.4, dt * 30) : 1 - Math.pow(0.85, dt * 30);
    this.level += (input - this.level) * response;
    this.flash *= Math.exp(-dt * 3);
    if (this.state === 'listening' && now - this.lastRipple > 1.1 - 0.75 * this.level) {
      this.ripples.push([now, 0.35 + 0.65 * this.level]);
      this.lastRipple = now;
    }
    this.ripples = this.ripples.filter(([born]) => now - born < 1.5).slice(-8);
    this.draw(now);
  }

  draw(now) {
    if (Math.max(1, window.devicePixelRatio || 1) !== this.ratio
        || (this.fill && (window.innerWidth !== this.width || window.innerHeight !== this.height))) {
      this.resize();
    }
    const voice = this.mix.listening + this.mix.speaking;
    const breath = 0.5 + 0.5 * Math.sin(now * (voice > 0.5 ? 2.2 : 1.1));
    const energy = Math.min(1.25, this.energy + this.flash * 0.35);
    const k = this.pixels / 132 * this.scale;
    const radius = (24.5 + 1.1 * breath + 3.2 * this.level * voice + 1.5 * this.flash) * k;
    const cx = this.centerX * this.ratio;
    const cy = this.centerY * this.ratio;
    if (!this.gl) {
      this.drawFallback(radius, energy, k, cx, cy);
      return;
    }
    const gl = this.gl;
    const u = this.uniforms;
    gl.viewport(0, 0, this.canvas.width, this.canvas.height);
    gl.uniform2f(u.uCenter, cx, this.canvas.height - cy);
    gl.uniform1f(u.uK, k);
    gl.uniform3fv(u.uColor, this.color);
    gl.uniform3fv(u.uDeep, this.deep);
    gl.uniform1f(u.uEnergy, energy);
    gl.uniform1f(u.uRadius, radius);
    gl.uniform1f(u.uBreath, breath);
    gl.uniform1f(u.uLevel, this.level);
    gl.uniform1f(u.uVoice, voice);
    gl.uniform1f(u.uFlash, this.flash);
    gl.uniform4f(u.uMix, this.mix.listening, this.mix.speaking, this.mix.thinking, this.mix.muted);
    gl.uniform1f(u.uBusy, this.mix.busy);
    gl.uniform4f(u.uTurns, wrap(this.turn * 0.5), wrap(this.turn * 1.4 * TAU / 72), wrap(-this.turn * 0.45), wrap(this.turn));
    gl.uniform4f(u.uTimes, wrap(now * 3.7), wrap(now * 2.6), 0, 0);
    gl.uniform3f(u.uSparks, wrap(now * 2.3), wrap(now * -1.7 + 2.0), wrap(now * 1.5));
    gl.uniform1f(u.uTrail, wrap(this.turn * 1.6));
    gl.uniform1f(u.uGrain, wrap(this.swirl));
    const centers = CLOUDS.map(([orbit, speed, , offset]) => {
      const angle = this.swirl * speed + offset;
      return [Math.cos(angle) * orbit * radius, Math.sin(angle) * orbit * radius];
    });
    gl.uniform4f(u.uCloudA, centers[0][0], centers[0][1], centers[1][0], centers[1][1]);
    gl.uniform2f(u.uCloudB, centers[2][0], centers[2][1]);
    gl.uniform3f(u.uSpread, CLOUDS[0][2], CLOUDS[1][2], CLOUDS[2][2]);
    this.ripplesBuffer.fill(0);
    this.ripples.forEach(([born, strength], index) => {
      this.ripplesBuffer[index * 2] = Math.min(1, (now - born) / 1.5);
      this.ripplesBuffer[index * 2 + 1] = strength;
    });
    gl.uniform2fv(u.uRipples, this.ripplesBuffer);
    gl.drawArrays(gl.TRIANGLES, 0, 3);
  }

  drawFallback(radius, energy, k, x, y) {
    const context = this.fallback;
    if (!context) return;
    const [r, g, b] = this.color.map((value) => Math.round(value * 255));
    context.clearRect(0, 0, this.canvas.width, this.canvas.height);
    const glow = context.createRadialGradient(x, y, radius * 0.4, x, y, 66 * k);
    glow.addColorStop(0, `rgba(${r}, ${g}, ${b}, ${0.55 * energy})`);
    glow.addColorStop(1, 'rgba(0, 0, 0, 0)');
    context.fillStyle = glow;
    context.fillRect(0, 0, this.canvas.width, this.canvas.height);
    const core = context.createRadialGradient(x - radius * 0.3, y - radius * 0.35, 1, x, y, radius);
    core.addColorStop(0, 'rgba(255, 255, 255, 0.95)');
    core.addColorStop(0.35, `rgb(${r}, ${g}, ${b})`);
    core.addColorStop(1, 'rgba(8, 14, 30, 0.95)');
    context.fillStyle = core;
    context.beginPath();
    context.arc(x, y, radius, 0, TAU);
    context.fill();
  }
}
