// Night City Life: the district in 3D (owner request 30.10: «давай попробуем 3Д», docs/PLAN_CITY3D.md).
// A pixelated first-person walk through the character's district, built from the bot's data: the doors of the places
// open the same screens as the buttons, the people of the district stand at their places, the character's own ride
// waits at the curb. Everything is procedural (canvas textures), three.js is MIT (LICENSE-three.txt).
import * as THREE from "./three.module.min.js";

// ---------------------------------------------------------------- data
const TOKEN = (location.hash.match(/t=([^&]+)/) || [])[1] || "";
const EMBED = window.parent !== window;
const $ = (id) => document.getElementById(id);
const DEMO = { demo: true,
  city: "nc", city_name: "Найт-Сити", district: "watson", district_name: "Уотсон", danger: 3, seed: 7,
  theme: { acc: "#FF3E8A", acc2: "#FFB547", bg: "#0B0A12", fog: "#120F1E" },
  doors: [
    { id: "p.bar", label: "Бар «Ржавый гвоздь»", kind: "Бар" },
    { id: "p.market", label: "Ночной рынок", kind: "Рынок" },
    { id: "gigs", label: "Фиксер", kind: "Заказы" },
    { id: "ripper", label: "Риппердок", kind: "Импланты" },
    { id: "arena", label: "Ринг «Кости»", kind: "Арена" },
    { id: "property", label: "Твоё жильё", kind: "Дом" },
  ],
  npcs: [{ id: 1, name: "Бармен Джо", arch: "bartender", door: "p.bar" }, { id: 2, name: "Торговка Мэй", arch: "vendor", door: "p.market" },
    { id: 3, name: "Коп Дэйв", arch: "cop", door: "p.market" }, { id: 4, name: "Наёмник Рико", arch: "merc", door: "p.bar" }],
  vehicle: { name: "Мотоцикл «Арч»", speed: 7 },
};

async function load() {
  if (!TOKEN) return DEMO;
  try {
    const r = await fetch("/api/city3d/map?t=" + encodeURIComponent(TOKEN), { cache: "no-store" });
    const d = await r.json();
    return d && d.ok ? d : DEMO;
  } catch (e) { return DEMO; }
}

// ---------------------------------------------------------------- helpers
function rng(seed) { let s = (seed >>> 0) || 1; return () => { s ^= s << 13; s ^= s >>> 17; s ^= s << 5; return ((s >>> 0) % 100000) / 100000; }; }
function hash(str) { let h = 2166136261; for (const c of String(str)) { h ^= c.charCodeAt(0); h = Math.imul(h, 16777619); } return h >>> 0; }
function canvasTex(w, h, draw, repeat) {
  const c = document.createElement("canvas"); c.width = w; c.height = h; draw(c.getContext("2d"), w, h);
  const t = new THREE.CanvasTexture(c); t.magFilter = THREE.NearestFilter; t.minFilter = THREE.NearestFilter; t.colorSpace = THREE.SRGBColorSpace;
  if (repeat) { t.wrapS = t.wrapT = THREE.RepeatWrapping; }
  return t;
}
function toast(text, ms) { const t = $("toast"); t.textContent = text; t.style.display = "block"; clearTimeout(toast.id); toast.id = setTimeout(() => { t.style.display = "none"; }, ms || 2200); }
function haptic(kind) { try { const tg = window.parent && window.parent.Telegram && window.parent.Telegram.WebApp; if (tg && tg.HapticFeedback) tg.HapticFeedback.impactOccurred(kind || "light"); } catch (e) {} }

// ---------------------------------------------------------------- scene
const data = await load();
const T = data.theme || DEMO.theme;
document.documentElement.style.setProperty("--acc", T.acc); document.documentElement.style.setProperty("--acc2", T.acc2);
document.documentElement.style.setProperty("--bg", T.bg);
$("dname").textContent = data.district_name; $("cname").textContent = data.city_name + (data.vehicle ? " · " + data.vehicle.name : "");
const R = rng(data.seed || hash(data.district));

const canvas = $("view");
const renderer = new THREE.WebGLRenderer({ canvas, antialias: false, powerPreference: "low-power" });
const PIX = 2; // the pixel size: the scene renders at a third of the screen and the canvas scales it up without smoothing
function resize() { renderer.setSize(Math.ceil(innerWidth / PIX), Math.ceil(innerHeight / PIX), false); camera.aspect = innerWidth / innerHeight; camera.updateProjectionMatrix(); }
const scene = new THREE.Scene();
scene.background = new THREE.Color(T.bg);
scene.fog = new THREE.Fog(T.fog || T.bg, 18, 120);
const camera = new THREE.PerspectiveCamera(70, innerWidth / innerHeight, 0.1, 400);
resize(); addEventListener("resize", resize);
scene.add(new THREE.HemisphereLight(0x8890ff, 0x201018, 0.55));
const moon = new THREE.DirectionalLight(0xbfd0ff, 0.35); moon.position.set(30, 80, 10); scene.add(moon);

// the layout: a grid of blocks with streets between them
const B = 26, S = 12, N = 4; // block size, street width, blocks per side
const SPAN = N * B + (N + 1) * S, HALF = SPAN / 2;
const blockX = (i) => -HALF + S + i * (B + S);
const solids = []; // AABBs {x0, x1, z0, z1}

const asphalt = canvasTex(64, 64, (g, w, h) => {
  g.fillStyle = "#16151b"; g.fillRect(0, 0, w, h);
  for (let i = 0; i < 380; i++) { const v = 18 + Math.floor(Math.random() * 22); g.fillStyle = `rgb(${v},${v},${v + 4})`; g.fillRect(Math.random() * w, Math.random() * h, 1, 1); }
  g.fillStyle = "rgba(120,160,255,.08)"; g.fillRect(0, 30, w, 3); // a wet streak
}, true);
asphalt.repeat.set(SPAN / 6, SPAN / 6);
const ground = new THREE.Mesh(new THREE.PlaneGeometry(SPAN + 200, SPAN + 200), new THREE.MeshLambertMaterial({ map: asphalt }));
ground.rotation.x = -Math.PI / 2; scene.add(ground);
// lane marks
const markMat = new THREE.MeshBasicMaterial({ color: 0xd9c56a });
for (let i = 0; i <= N; i++) {
  const c = -HALF + S / 2 + i * (B + S);
  for (let k = -HALF; k < HALF; k += 6) {
    const m1 = new THREE.Mesh(new THREE.PlaneGeometry(0.25, 2.5), markMat); m1.rotation.x = -Math.PI / 2; m1.position.set(c, 0.02, k); scene.add(m1);
    const m2 = new THREE.Mesh(new THREE.PlaneGeometry(2.5, 0.25), markMat); m2.rotation.x = -Math.PI / 2; m2.position.set(k, 0.02, c); scene.add(m2);
  }
}
// sidewalks
const walkTex = canvasTex(32, 32, (g, w, h) => { g.fillStyle = "#2a2830"; g.fillRect(0, 0, w, h); g.strokeStyle = "#1c1b22"; for (let i = 0; i < w; i += 8) { g.strokeRect(i, 0, 8, h); g.strokeRect(0, i, w, 8); } }, true);
for (let i = 0; i < N; i++) for (let j = 0; j < N; j++) {
  const t = walkTex.clone(); t.needsUpdate = true; t.repeat.set(B / 4, B / 4);
  const walk = new THREE.Mesh(new THREE.BoxGeometry(B + 3, 0.25, B + 3), new THREE.MeshLambertMaterial({ map: t }));
  walk.position.set(blockX(i) + B / 2, 0.12, blockX(j) + B / 2); scene.add(walk);
}

// building facades
const NEON = [T.acc, T.acc2, "#4DE3FF", "#B26BFF", "#5CE0A0"];
function facade(seed) {
  const r = rng(seed);
  return canvasTex(32, 64, (g, w, h) => {
    const base = 14 + Math.floor(r() * 18); g.fillStyle = `rgb(${base},${base},${base + 8})`; g.fillRect(0, 0, w, h);
    for (let y = 3; y < h - 2; y += 6) for (let x = 2; x < w - 2; x += 5) {
      const lit = r(); g.fillStyle = lit > 0.72 ? NEON[Math.floor(r() * NEON.length)] : lit > 0.45 ? "#d8c9a0" : "#0b0b10";
      g.globalAlpha = lit > 0.72 ? 0.85 : 1; g.fillRect(x, y, 3, 3); g.globalAlpha = 1;
    }
  }, true);
}
const facades = Array.from({ length: 6 }, (_, i) => facade((data.seed || 1) * 31 + i));
function building(x0, z0, w, d, h) {
  const tex = facades[Math.floor(R() * facades.length)].clone(); tex.needsUpdate = true; tex.repeat.set(Math.max(1, w / 6), Math.max(1, h / 12));
  const mat = new THREE.MeshLambertMaterial({ map: tex, emissive: 0xffffff, emissiveMap: tex, emissiveIntensity: 0.55 });
  const roof = new THREE.MeshLambertMaterial({ color: 0x15141a });
  const m = new THREE.Mesh(new THREE.BoxGeometry(w, h, d), [mat, mat, roof, roof, mat, mat]);
  m.position.set(x0 + w / 2, h / 2, z0 + d / 2); scene.add(m);
  solids.push({ x0, x1: x0 + w, z0, z1: z0 + d });
  if (R() > 0.55) { // a rooftop sign that blinks
    const s = new THREE.Mesh(new THREE.BoxGeometry(w * 0.6, 1.4, 0.3), new THREE.MeshBasicMaterial({ color: NEON[Math.floor(R() * NEON.length)] }));
    s.position.set(x0 + w / 2, h + 1, z0 + d / 2); scene.add(s); blinkers.push(s);
  }
  return m;
}
const blinkers = [];
// each block: two to four buildings; the facades along the streets carry the doors
const slots = []; // door slots: {x, z, nx, nz} on a street-facing facade
for (let i = 0; i < N; i++) for (let j = 0; j < N; j++) {
  const bx = blockX(i), bz = blockX(j), center = Math.abs(i - (N - 1) / 2) + Math.abs(j - (N - 1) / 2) < 1.6;
  const split = R() > 0.5;
  const parts = split ? [[0, 0, B, B / 2 - 1], [0, B / 2 + 1, B, B / 2 - 1]] : [[0, 0, B / 2 - 1, B], [B / 2 + 1, 0, B / 2 - 1, B]];
  for (const [ox, oz, w, d] of parts) {
    const h = (center ? 34 : 12) + R() * (center ? 46 : 22);
    building(bx + ox, bz + oz, w, d, h);
    const sides = [];
    if (oz === 0) sides.push({ x: bx + ox + w / 2, z: bz - 0.05, nx: 0, nz: -1 });
    if (oz + d >= B - 0.01) sides.push({ x: bx + ox + w / 2, z: bz + B + 0.05, nx: 0, nz: 1 });
    if (ox === 0) sides.push({ x: bx - 0.05, z: bz + oz + d / 2, nx: -1, nz: 0 });
    if (ox + w >= B - 0.01) sides.push({ x: bx + B + 0.05, z: bz + oz + d / 2, nx: 1, nz: 0 });
    for (const s of sides) slots.push(s);
  }
}
// spread the doors over the slots nearest to the centre first
slots.sort((a, b) => (Math.hypot(a.x, a.z) + R() * 20) - (Math.hypot(b.x, b.z) + R() * 20));

function signTex(text, color) {
  return canvasTex(256, 64, (g, w, h) => {
    g.fillStyle = "#07060a"; g.fillRect(0, 0, w, h);
    g.strokeStyle = color; g.lineWidth = 4; g.strokeRect(2, 2, w - 4, h - 4);
    g.fillStyle = color; g.font = "bold 28px system-ui, sans-serif"; g.textAlign = "center"; g.textBaseline = "middle";
    let t = text; while (g.measureText(t).width > w - 16 && t.length > 4) t = t.slice(0, -2) + "…";
    g.fillText(t, w / 2, h / 2 + 1);
  });
}
const doors = [];
data.doors.forEach((d, i) => {
  const s = slots[i]; if (!s) return;
  const color = NEON[i % NEON.length];
  const rot = Math.atan2(s.nx, s.nz);
  const door = new THREE.Mesh(new THREE.PlaneGeometry(2.4, 3.2), new THREE.MeshBasicMaterial({ color: 0x0a0a0f }));
  door.position.set(s.x + s.nx * 0.02, 1.6, s.z + s.nz * 0.02); door.rotation.y = rot; scene.add(door);
  const frame = new THREE.Mesh(new THREE.PlaneGeometry(2.8, 3.6), new THREE.MeshBasicMaterial({ color }));
  frame.position.copy(door.position).add(new THREE.Vector3(-s.nx * 0.01, 0, -s.nz * 0.01)); frame.rotation.y = rot; scene.add(frame);
  const sign = new THREE.Mesh(new THREE.PlaneGeometry(7, 1.75), new THREE.MeshBasicMaterial({ map: signTex(d.label, color) }));
  sign.position.set(s.x + s.nx * 0.05, 4.4, s.z + s.nz * 0.05); sign.rotation.y = rot; scene.add(sign); blinkers.push(sign);
  const glow = new THREE.PointLight(new THREE.Color(color), 1.2, 9, 1.6); glow.position.set(s.x + s.nx * 1.5, 3, s.z + s.nz * 1.5);
  if (i < 6) scene.add(glow); // a few real lights, the rest is emissive
  doors.push({ ...d, x: s.x + s.nx * 1.6, z: s.z + s.nz * 1.6, nx: s.nx, nz: s.nz, color });
});

// street lamps and rain
const lampMat = new THREE.MeshBasicMaterial({ color: 0xfff1c9 });
for (let i = 0; i <= N; i++) for (let j = 0; j < N; j++) {
  const x = -HALF + S / 2 + i * (B + S) + 4.5, z = blockX(j) + B / 2;
  const pole = new THREE.Mesh(new THREE.BoxGeometry(0.15, 5, 0.15), new THREE.MeshLambertMaterial({ color: 0x222228 })); pole.position.set(x, 2.5, z); scene.add(pole);
  const bulb = new THREE.Mesh(new THREE.BoxGeometry(0.6, 0.2, 0.6), lampMat); bulb.position.set(x, 5, z); scene.add(bulb);
}
const RAIN = 1400, rainGeo = new THREE.BufferGeometry(), rainPos = new Float32Array(RAIN * 3);
for (let i = 0; i < RAIN; i++) { rainPos[i * 3] = (Math.random() - 0.5) * 80; rainPos[i * 3 + 1] = Math.random() * 30; rainPos[i * 3 + 2] = (Math.random() - 0.5) * 80; }
rainGeo.setAttribute("position", new THREE.BufferAttribute(rainPos, 3));
const rain = new THREE.Points(rainGeo, new THREE.PointsMaterial({ color: 0x9fb4ff, size: 0.08, transparent: true, opacity: 0.6 }));
scene.add(rain);

// ---------------------------------------------------------------- people: pixel sprites at their places
function personTex(seed, arch) {
  const r = rng(seed);
  const skin = ["#f1c7a5", "#d9a37c", "#a8714f", "#6e4630", "#e8d1c0"][Math.floor(r() * 5)];
  const coat = { cop: "#2b4cff", corpo: "#2b2b33", bartender: "#5a2a1a", vendor: "#b8692b", merc: "#3c4a2a", ganger: "#b0203a", homeless: "#4a4038",
    techie: "#1f7a6a", nomad: "#8a6a3a", tourist: "#d8d080", performer: "#d02ad0", rich_kid: "#e0e0e0", dealer: "#4a1a6a", journalist: "#666666" }[arch]
    || NEON[Math.floor(r() * NEON.length)];
  const hair = ["#111", "#3a2414", "#d8c050", "#c02a6a", "#2aa8d0", "#eeeeee"][Math.floor(r() * 6)];
  return canvasTex(16, 32, (g) => {
    g.clearRect(0, 0, 16, 32);
    g.fillStyle = hair; g.fillRect(5, 1, 6, 3);
    g.fillStyle = skin; g.fillRect(5, 4, 6, 5);
    g.fillStyle = "#000"; g.fillRect(6, 6, 1, 1); g.fillRect(9, 6, 1, 1);
    g.fillStyle = coat; g.fillRect(4, 9, 8, 11); g.fillRect(3, 10, 1, 8); g.fillRect(12, 10, 1, 8);
    g.fillStyle = skin; g.fillRect(3, 18, 1, 2); g.fillRect(12, 18, 1, 2);
    g.fillStyle = "#1a1a22"; g.fillRect(5, 20, 3, 10); g.fillRect(8, 20, 3, 10);
    g.fillStyle = "#0a0a0a"; g.fillRect(5, 30, 3, 2); g.fillRect(8, 30, 3, 2);
    if (r() > 0.6) { g.fillStyle = NEON[Math.floor(r() * NEON.length)]; g.fillRect(4, 12, 8, 1); } // a glowing stripe of chrome
  });
}
const people = [];
for (const p of data.npcs || []) {
  const d = doors.find((x) => x.id === p.door) || doors[Math.floor(R() * doors.length)];
  if (!d) continue;
  const spr = new THREE.Sprite(new THREE.SpriteMaterial({ map: personTex(hash(p.name), p.arch) }));
  spr.scale.set(0.9, 1.8, 1);
  const a = R() * Math.PI * 2, rad = 2 + R() * 3;
  spr.position.set(d.x + Math.cos(a) * rad, 0.9, d.z + Math.sin(a) * rad); scene.add(spr);
  people.push({ ...p, demo: !!data.demo, spr, home: d, tx: spr.position.x, tz: spr.position.z, wait: R() * 3 });
}
// passers-by without names: the crowd of the street
for (let i = 0; i < 18; i++) {
  const spr = new THREE.Sprite(new THREE.SpriteMaterial({ map: personTex(i * 977 + (data.seed || 0), "") }));
  spr.scale.set(0.9, 1.8, 1);
  const lane = Math.floor(R() * (N + 1)), along = (R() - 0.5) * SPAN, x = -HALF + S / 2 + lane * (B + S) + (R() > 0.5 ? 4.6 : -4.6);
  spr.position.set(R() > 0.5 ? x : along, 0.9, R() > 0.5 ? along : x); scene.add(spr);
  people.push({ crowd: true, spr, dir: R() > 0.5 ? 1 : -1, axis: spr.position.x === x ? "z" : "x", speed: 0.8 + R() * 0.8 });
}

// traffic: cars that drive the streets in loops
function carMesh(color) {
  const g = new THREE.Group();
  const body = new THREE.Mesh(new THREE.BoxGeometry(2, 0.8, 4.2), new THREE.MeshLambertMaterial({ color })); body.position.y = 0.7; g.add(body);
  const cab = new THREE.Mesh(new THREE.BoxGeometry(1.7, 0.7, 2), new THREE.MeshLambertMaterial({ color: 0x101018 })); cab.position.set(0, 1.4, -0.2); g.add(cab);
  const head = new THREE.Mesh(new THREE.BoxGeometry(1.6, 0.2, 0.1), new THREE.MeshBasicMaterial({ color: 0xfff6d0 })); head.position.set(0, 0.8, 2.12); g.add(head);
  const tail = new THREE.Mesh(new THREE.BoxGeometry(1.6, 0.2, 0.1), new THREE.MeshBasicMaterial({ color: 0xff2040 })); tail.position.set(0, 0.8, -2.12); g.add(tail);
  for (const [x, z] of [[-1, 1.4], [1, 1.4], [-1, -1.4], [1, -1.4]]) {
    const w = new THREE.Mesh(new THREE.BoxGeometry(0.3, 0.6, 0.6), new THREE.MeshLambertMaterial({ color: 0x050505 })); w.position.set(x, 0.3, z); g.add(w);
  }
  return g;
}
function bikeMesh(color) { // a motorbike: a narrow body, a rider-less seat, two wheels
  const g = new THREE.Group();
  const body = new THREE.Mesh(new THREE.BoxGeometry(0.5, 0.5, 2), new THREE.MeshLambertMaterial({ color })); body.position.y = 0.75; g.add(body);
  const seat = new THREE.Mesh(new THREE.BoxGeometry(0.45, 0.2, 0.8), new THREE.MeshLambertMaterial({ color: 0x101018 })); seat.position.set(0, 1.08, -0.3); g.add(seat);
  const bar = new THREE.Mesh(new THREE.BoxGeometry(0.9, 0.08, 0.08), new THREE.MeshLambertMaterial({ color: 0x222222 })); bar.position.set(0, 1.25, 0.75); g.add(bar);
  const head = new THREE.Mesh(new THREE.BoxGeometry(0.3, 0.2, 0.1), new THREE.MeshBasicMaterial({ color: 0xfff6d0 })); head.position.set(0, 1, 1.02); g.add(head);
  const tail = new THREE.Mesh(new THREE.BoxGeometry(0.3, 0.12, 0.1), new THREE.MeshBasicMaterial({ color: 0xff2040 })); tail.position.set(0, 0.9, -1.02); g.add(tail);
  for (const z of [0.85, -0.85]) { const w = new THREE.Mesh(new THREE.BoxGeometry(0.18, 0.7, 0.7), new THREE.MeshLambertMaterial({ color: 0x050505 })); w.position.set(0, 0.35, z); g.add(w); }
  return g;
}
const traffic = [];
for (let i = 0; i < 7; i++) {
  const c = carMesh(new THREE.Color().setHSL(R(), 0.5, 0.35)); const lane = Math.floor(R() * (N + 1));
  const axis = R() > 0.5 ? "x" : "z", dir = R() > 0.5 ? 1 : -1, line = -HALF + S / 2 + lane * (B + S) + dir * 2.4;
  if (axis === "z") c.position.set(line, 0, (R() - 0.5) * SPAN); else c.position.set((R() - 0.5) * SPAN, 0, line);
  c.rotation.y = axis === "z" ? (dir > 0 ? 0 : Math.PI) : (dir > 0 ? Math.PI / 2 : -Math.PI / 2);
  scene.add(c); traffic.push({ c, axis, dir, speed: 8 + R() * 6 });
}

// ---------------------------------------------------------------- the player and the ride
// the start: in the middle of the street a few steps from the first door, looking at its sign
const D0 = doors[0] || { x: 0, z: 0, nx: 0, nz: 1 };
const tx = -D0.nz, tz = D0.nx; // along the street
const player = { x: D0.x + D0.nx * 4.4 + tx * 9, z: D0.z + D0.nz * 4.4 + tz * 9, yaw: 0, pitch: 0.05, run: false, driving: false };
player.yaw = Math.atan2(-(D0.x - player.x), -(D0.z - player.z));
let ride = null;
if (data.vehicle) { // the character's own ride at the curb next to the start
  const bike = /мото|байк|bike|скутер/i.test(data.vehicle.name || "");
  const m = bike ? bikeMesh(new THREE.Color(T.acc)) : carMesh(new THREE.Color(T.acc)); scene.add(m);
  const rx = D0.x + D0.nx * 2.8 - tx * 5, rz = D0.z + D0.nz * 2.8 - tz * 5, ryaw = Math.atan2(-tx, -tz);
  ride = { m, x: rx, z: rz, yaw: ryaw, v: 0, max: 10 + (data.vehicle.speed || 5) * 2.6 };
  m.position.set(rx, 0, rz); m.rotation.y = ryaw + Math.PI;
}
window.NC3D = { player, get ride() { return ride; }, doors, blocked: (x, z, r) => blocked(x, z, r) }; // for the checks in a browser
function blocked(x, z, r) {
  if (Math.abs(x) > HALF + 40 || Math.abs(z) > HALF + 40) return true;
  for (const b of solids) if (x > b.x0 - r && x < b.x1 + r && z > b.z0 - r && z < b.z1 + r) return true;
  return false;
}

// ---------------------------------------------------------------- input
const keys = {};
addEventListener("keydown", (e) => { keys[e.code] = true; if (e.code === "KeyE" || e.code === "Enter") act(); if (e.code === "KeyF") toggleCar(); if (e.code === "Escape") leave(); });
addEventListener("keyup", (e) => { keys[e.code] = false; });
const stick = { x: 0, y: 0, id: null }, look = { id: null, lx: 0, ly: 0 };
const stickEl = $("stick"), knob = $("knob");
function stickMove(e) {
  const r = stickEl.getBoundingClientRect(), cx = r.left + r.width / 2, cy = r.top + r.height / 2;
  let dx = e.clientX - cx, dy = e.clientY - cy; const len = Math.hypot(dx, dy), max = r.width / 2 - 10;
  if (len > max) { dx *= max / len; dy *= max / len; }
  stick.x = dx / max; stick.y = dy / max; knob.style.transform = `translate(${dx}px, ${dy}px)`;
}
stickEl.addEventListener("pointerdown", (e) => { stick.id = e.pointerId; stickEl.setPointerCapture(e.pointerId); stickMove(e); });
stickEl.addEventListener("pointermove", (e) => { if (e.pointerId === stick.id) stickMove(e); });
const stickEnd = (e) => { if (e.pointerId !== stick.id) return; stick.id = null; stick.x = stick.y = 0; knob.style.transform = ""; };
stickEl.addEventListener("pointerup", stickEnd); stickEl.addEventListener("pointercancel", stickEnd);
canvas.addEventListener("pointerdown", (e) => { look.id = e.pointerId; look.lx = e.clientX; look.ly = e.clientY; canvas.setPointerCapture(e.pointerId); });
canvas.addEventListener("pointermove", (e) => {
  if (e.pointerId !== look.id) return;
  player.yaw -= (e.clientX - look.lx) * 0.006; player.pitch = Math.max(-1.1, Math.min(1.1, player.pitch - (e.clientY - look.ly) * 0.004));
  look.lx = e.clientX; look.ly = e.clientY;
});
canvas.addEventListener("pointerup", () => { look.id = null; });
$("run").addEventListener("click", () => { player.run = !player.run; $("run").style.borderColor = player.run ? T.acc : ""; });
$("act").addEventListener("click", () => act());
$("car").addEventListener("click", () => toggleCar());
$("close").addEventListener("click", () => leave());

// ---------------------------------------------------------------- what is near and what the button does
let near = null;
function findNear() {
  const px = player.driving ? ride.x : player.x, pz = player.driving ? ride.z : player.z;
  let best = null, bd = 3.2;
  for (const d of doors) { const dist = Math.hypot(d.x - px, d.z - pz); if (dist < bd) { bd = dist; best = { kind: "door", d }; } }
  if (!player.driving) for (const p of people) {
    if (p.crowd) continue; const dist = Math.hypot(p.spr.position.x - px, p.spr.position.z - pz);
    if (dist < Math.min(bd, 2.2)) { bd = dist; best = { kind: "npc", p }; }
  }
  return best;
}
function showNear() {
  const n = findNear(); near = n;
  const pr = $("prompt"), a = $("act");
  if (!n) { pr.style.display = "none"; a.style.display = "none"; }
  else if (n.kind === "door") { pr.innerHTML = ""; pr.append(n.d.label); const s = document.createElement("small"); s.textContent = n.d.kind; pr.append(s); pr.style.display = "block"; a.textContent = "Войти"; a.style.display = "block"; }
  else { pr.innerHTML = ""; pr.append(n.p.name); const s = document.createElement("small"); s.textContent = n.p.home ? n.p.home.label : ""; pr.append(s); pr.style.display = "block"; a.textContent = "Подойти"; a.style.display = "block"; }
  const c = $("car");
  if (ride && !player.driving && Math.hypot(ride.x - player.x, ride.z - player.z) < 3.5) { c.textContent = "Сесть"; c.style.display = "block"; }
  else if (player.driving) { c.textContent = "Пешком"; c.style.display = "block"; }
  else c.style.display = "none";
}
function press(arg) { // the window of the player is on the city screen: «city:d3:<door>» (handlers/city.py)
  haptic("medium");
  if (!EMBED) { toast("Двери открываются в приложении игры: открой район из Mini App.", 2600); return; }
  window.parent.postMessage({ nc: "press", screen: "city", action: "d3", arg: String(arg) }, location.origin);
}
function act() {
  if (!near) return;
  if (near.kind === "door") press(near.d.id);
  else press(near.p.demo ? near.p.home.id : "n." + near.p.id);
}
function toggleCar() {
  if (!ride) return;
  if (player.driving) {
    const sx = ride.x + Math.cos(ride.yaw) * 2.2, sz = ride.z - Math.sin(ride.yaw) * 2.2;
    player.x = blocked(sx, sz, 0.4) ? ride.x : sx; player.z = blocked(sx, sz, 0.4) ? ride.z + 2.5 : sz; player.driving = false; player.yaw = ride.yaw; ride.v = 0;
  } else if (Math.hypot(ride.x - player.x, ride.z - player.z) < 3.5) { player.driving = true; haptic("heavy"); }
}
function leave() {
  if (EMBED) { try { window.parent.postMessage({ nc: "close" }, location.origin); return; } catch (e) {} }
  history.length > 1 ? history.back() : window.close();
}

// ---------------------------------------------------------------- minimap
const mini = $("mini").getContext("2d");
function drawMini() {
  const W = 92, s = W / (SPAN + 10), px = player.driving ? ride.x : player.x, pz = player.driving ? ride.z : player.z;
  mini.clearRect(0, 0, W, W); mini.fillStyle = "rgba(255,255,255,.12)";
  for (const b of solids) mini.fillRect((b.x0 + HALF) * s + 4, (b.z0 + HALF) * s + 4, (b.x1 - b.x0) * s, (b.z1 - b.z0) * s);
  for (const d of doors) { mini.fillStyle = d.color; mini.fillRect((d.x + HALF) * s + 3, (d.z + HALF) * s + 3, 3, 3); }
  if (ride && !player.driving) { mini.fillStyle = "#fff"; mini.fillRect((ride.x + HALF) * s + 3, (ride.z + HALF) * s + 3, 3, 2); }
  const yaw = player.driving ? ride.yaw : player.yaw; const x = (px + HALF) * s + 4, y = (pz + HALF) * s + 4;
  mini.fillStyle = T.acc2; mini.beginPath(); mini.moveTo(x - Math.sin(yaw) * 5, y - Math.cos(yaw) * 5);
  mini.lineTo(x + Math.cos(yaw) * 3, y - Math.sin(yaw) * 3); mini.lineTo(x - Math.cos(yaw) * 3, y + Math.sin(yaw) * 3); mini.fill();
}

// ---------------------------------------------------------------- the loop
let last = performance.now(), nearT = 0;
function frame(now) {
  const dt = Math.min(0.05, (now - last) / 1000); last = now;
  let fwd = (keys.KeyW || keys.ArrowUp ? 1 : 0) - (keys.KeyS || keys.ArrowDown ? 1 : 0) - stick.y;
  let side = (keys.KeyD || keys.ArrowRight ? 1 : 0) - (keys.KeyA || keys.ArrowLeft ? 1 : 0) + stick.x;
  fwd = Math.max(-1, Math.min(1, fwd)); side = Math.max(-1, Math.min(1, side));
  if (player.driving) {
    ride.v += fwd * dt * (ride.max * 0.6); ride.v *= 1 - dt * 0.6; ride.v = Math.max(-ride.max * 0.3, Math.min(ride.max, ride.v));
    ride.yaw -= side * dt * 1.8 * Math.min(1, Math.abs(ride.v) / 6) * Math.sign(ride.v || 1);
    const nx = ride.x - Math.sin(ride.yaw) * ride.v * dt, nz = ride.z - Math.cos(ride.yaw) * ride.v * dt;
    if (!blocked(nx, nz, 1.2)) { ride.x = nx; ride.z = nz; } else { if (Math.abs(ride.v) > 8) haptic("heavy"); ride.v *= -0.3; }
    ride.m.position.set(ride.x, 0, ride.z); ride.m.rotation.y = ride.yaw + Math.PI;
    const back = 7, up = 3.4;
    camera.position.set(ride.x + Math.sin(ride.yaw) * back, up, ride.z + Math.cos(ride.yaw) * back);
    camera.lookAt(ride.x - Math.sin(ride.yaw) * 6, 1.2, ride.z - Math.cos(ride.yaw) * 6);
  } else {
    const sp = (player.run || keys.ShiftLeft ? 8 : 4.2) * dt;
    const dx = (-Math.sin(player.yaw) * fwd + Math.cos(player.yaw) * side) * sp, dz = (-Math.cos(player.yaw) * fwd - Math.sin(player.yaw) * side) * sp;
    if (!blocked(player.x + dx, player.z, 0.4)) player.x += dx;
    if (!blocked(player.x, player.z + dz, 0.4)) player.z += dz;
    const bob = (fwd || side) ? Math.sin(now / 110) * 0.05 : 0;
    camera.position.set(player.x, 1.62 + bob, player.z);
    camera.rotation.set(player.pitch, player.yaw, 0, "YXZ");
  }
  // people walk
  for (const p of people) {
    if (p.crowd) {
      p.spr.position[p.axis] += p.dir * p.speed * dt;
      if (Math.abs(p.spr.position[p.axis]) > HALF + 8) p.dir *= -1;
      continue;
    }
    p.wait -= dt;
    if (p.wait <= 0) { const a = Math.random() * Math.PI * 2, r = 1.5 + Math.random() * 3.5; p.tx = p.home.x + Math.cos(a) * r; p.tz = p.home.z + Math.sin(a) * r; p.wait = 3 + Math.random() * 4; }
    const dx = p.tx - p.spr.position.x, dz = p.tz - p.spr.position.z, len = Math.hypot(dx, dz);
    if (len > 0.1 && !blocked(p.spr.position.x + dx / len * dt, p.spr.position.z + dz / len * dt, 0.3)) {
      p.spr.position.x += dx / len * dt * 0.9; p.spr.position.z += dz / len * dt * 0.9;
    }
  }
  for (const t of traffic) {
    const v = t.speed * dt * t.dir; t.c.position[t.axis] += v;
    if (t.c.position[t.axis] > HALF + 30) t.c.position[t.axis] = -HALF - 30; if (t.c.position[t.axis] < -HALF - 30) t.c.position[t.axis] = HALF + 30;
  }
  // rain follows the camera
  const pos = rainGeo.attributes.position.array;
  for (let i = 0; i < RAIN; i++) { pos[i * 3 + 1] -= 22 * dt; if (pos[i * 3 + 1] < 0) { pos[i * 3 + 1] = 28; pos[i * 3] = camera.position.x + (Math.random() - 0.5) * 80; pos[i * 3 + 2] = camera.position.z + (Math.random() - 0.5) * 80; } }
  rainGeo.attributes.position.needsUpdate = true;
  for (let i = 0; i < blinkers.length; i++) blinkers[i].visible = Math.sin(now / 400 + i * 1.7) > -0.92;
  nearT -= dt; if (nearT <= 0) { showNear(); drawMini(); nearT = 0.15; }
  renderer.render(scene, camera);
  requestAnimationFrame(frame);
}
if (EMBED) $("close").style.display = "none"; // the app has its own «← Найт-Сити» above the frame
$("load").remove();
if (!data.doors.length) toast("Здесь пока пусто.", 2000);
requestAnimationFrame(frame);
