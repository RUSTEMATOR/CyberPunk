// Night City Life: the district in 3D (docs/PLAN_CITY3D.md, docs/PLAN_CITY3D_2.md).
// A pixelated first-person walk through the character's district, built from the bot's data. The doors open rooms
// and the same screens as the buttons; the residents stand at their places; other players walk the same street; the
// police chase a wanted player; the gangs hold their corners; the fixer's gigs glow on the map; the district has its
// own look; the Purge and the Blackwall show; the chrome works; there is sound; the ride is the one from the garage;
// the way out at the edge leads to the other districts. Everything is procedural; three.js is MIT (LICENSE-three.txt).
import * as THREE from "./three.module.min.js";
import { Sound } from "./sound.js";
import { buildRoom, hasRoom, ROOM_W, ROOM_D } from "./rooms.js";

// ---------------------------------------------------------------- data
const TOKEN = (location.hash.match(/t=([^&]+)/) || [])[1] || "";
const EMBED = window.parent !== window;
const $ = (id) => document.getElementById(id);
const DEMO = { demo: true,
  city: "nc", city_name: "Найт-Сити", district: "watson", district_name: "Уотсон", danger: 3, seed: 7, style: "dense",
  theme: { acc: "#FF3E8A", acc2: "#FFB547", bg: "#0B0A12", fog: "#120F1E" },
  doors: [
    { id: "p.bar", label: "Бар «Ржавый гвоздь»", kind: "Бар", room: "bar" },
    { id: "p.market", label: "Ночной рынок", kind: "Рынок", room: "market" },
    { id: "gigs", label: "Фиксер", kind: "Заказы", room: "gigs" },
    { id: "ripper", label: "Риппердок", kind: "Импланты", room: "ripper" },
    { id: "arena", label: "Ринг «Кости»", kind: "Арена", room: "arena" },
    { id: "property", label: "Твоё жильё", kind: "Дом", room: "property" },
  ],
  npcs: [{ id: 1, name: "Бармен Джо", arch: "bartender", door: "p.bar" }, { id: 2, name: "Торговка Мэй", arch: "vendor", door: "p.market" },
    { id: 3, name: "Коп Дэйв", arch: "cop", door: "p.market" }, { id: 4, name: "Наёмник Рико", arch: "merc", door: "p.bar" }],
  vehicle: { name: "Мотоцикл «Арч»", speed: 7, kind: "bike", dmg: 0 },
  me: { c: 0, name: "Ты" }, heat: 0, law: null, purge: false, bw: 0, bw_max: 5, gangs: [], gigs: [], chrome: [], exits: [],
};

async function load() {
  if (!TOKEN) return DEMO;
  try {
    const r = await fetch("/api/city3d/map?t=" + encodeURIComponent(TOKEN), { cache: "no-store" });
    const d = await r.json();
    return d && d.ok ? d : DEMO;
  } catch (e) { return DEMO; }
}
let posBusy = false; // the spot request in flight (phase 1); declared early: a restored room sends one at once
function post(path, body) {
  return fetch(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ t: TOKEN, ...body }) })
    .then((r) => r.json()).catch(() => null);
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
const clamp = (v, a, b) => Math.max(a, Math.min(b, v));

// ---------------------------------------------------------------- the look of the district (phase 5)
const STYLES = {
  dense: { h: 1, plaza: 0.14, lit: 0.55, neon: 0.3, roof: 0.45, landmark: "mega" },
  corpo: { h: 1.5, plaza: 0.12, lit: 0.72, neon: 0.1, roof: 0.3, base: [20, 26, 40], win: ["#bfe6ff", "#e8f6ff"], landmark: "tower" },
  rich: { h: 0.32, plaza: 0.4, lit: 0.5, neon: 0.06, roof: 0.1, trees: "tree", win: ["#ffd9a0"] },
  suburb: { h: 0.45, plaza: 0.25, lit: 0.5, neon: 0.14, roof: 0.25, trees: "palm" },
  industrial: { h: 0.45, plaza: 0.1, lit: 0.25, neon: 0.05, roof: 0.15, base: [26, 22, 18], chimneys: true, landmark: "stack" },
  docks: { h: 0.4, plaza: 0.38, lit: 0.3, neon: 0.06, roof: 0.15, containers: true, landmark: "crane" },
  ruins: { h: 0.75, plaza: 0.18, lit: 0.12, neon: 0.05, roof: 0.1, ruins: true, fires: 10, landmark: "shell" },
  dogtown: { h: 0.7, plaza: 0.2, lit: 0.2, neon: 0.22, roof: 0.3, ruins: true, fires: 8, spots: true, landmark: "shell" },
  desert: { h: 0.22, plaza: 0.1, empty: 0.45, lit: 0.18, neon: 0.1, roof: 0.15, base: [56, 44, 30], ground: "#6a5236", walk: "#7a6040", fires: 6, rain: false, landmark: "mast" },
  old: { h: 0.42, plaza: 0.16, lit: 0.6, neon: 0.08, roof: 0.2, base: [40, 26, 22], win: ["#ffcf8a", "#ffe2b0"] },
  japantown: { h: 0.9, plaza: 0.12, lit: 0.55, neon: 0.35, roof: 0.6, kanji: true, landmark: "pagoda" },
  sov: { h: 0.7, plaza: 0.14, lit: 0.4, neon: 0.04, roof: 0.2, base: [36, 36, 38], win: ["#e8d890"], red: true },
  libria: { h: 0.9, plaza: 0.1, lit: 0.35, neon: 0, roof: 0, base: [40, 40, 42], win: ["#d8d8d8", "#9a9a9a"] },
  aeon: { h: 0.8, plaza: 0.14, lit: 0.3, neon: 0.2, roof: 0.2, win: ["#39ff7a", "#e5ff6e"], clocks: true, landmark: "clock" },
  station: { h: 0.35, plaza: 0.35, lit: 0.8, neon: 0.04, roof: 0.1, base: [200, 204, 210], win: ["#9ad9cf", "#ffffff"], trees: "tree", ground: "#3a3e44", walk: "#55595f", rain: false },
};

// ---------------------------------------------------------------- scene
const data = await load();
const T = data.theme || DEMO.theme;
const ST = STYLES[data.style] || STYLES.dense;
const PURGE = !!data.purge, BW = clamp((data.bw || 0) / (data.bw_max || 5), 0, 1);
document.documentElement.style.setProperty("--acc", T.acc); document.documentElement.style.setProperty("--acc2", T.acc2);
document.documentElement.style.setProperty("--bg", T.bg);
$("dname").textContent = data.district_name; $("cname").textContent = data.city_name + (data.vehicle ? " · " + data.vehicle.name : "");
const R = rng(data.seed || hash(data.district));
const sound = new Sound();

const canvas = $("view");
const renderer = new THREE.WebGLRenderer({ canvas, antialias: false, powerPreference: "low-power" });
const PIX = 2; // the pixel size: the scene renders at half the screen and the canvas scales it up without smoothing
function resize() { renderer.setSize(Math.ceil(innerWidth / PIX), Math.ceil(innerHeight / PIX), false); camera.aspect = innerWidth / innerHeight; camera.updateProjectionMatrix(); }
const scene = new THREE.Scene();
const SKY = PURGE ? "#1a0404" : T.bg, FOG = PURGE ? "#3a0808" : (T.fog || T.bg);
scene.background = new THREE.Color(SKY);
scene.fog = new THREE.Fog(FOG, 18, 120);
const camera = new THREE.PerspectiveCamera(70, innerWidth / innerHeight, 0.1, 135); // past the fog nothing is drawn
resize(); addEventListener("resize", resize);
scene.add(new THREE.HemisphereLight(PURGE ? 0xff6060 : 0x8890ff, 0x201018, 0.55));
const moon = new THREE.DirectionalLight(PURGE ? 0xff8080 : 0xbfd0ff, 0.35); moon.position.set(30, 80, 10); scene.add(moon);

// the layout: a grid of blocks with streets between them
const B = 26, S = 12, N = 7; // block size, street width, blocks per side
const SPAN = N * B + (N + 1) * S, HALF = SPAN / 2, D_RING = 22;
const blockX = (i) => -HALF + S + i * (B + S);
const streetC = (i) => -HALF + S / 2 + i * (B + S);
const solids = []; // AABBs {x0, x1, z0, z1}
const NEON = [T.acc, T.acc2, "#4DE3FF", "#B26BFF", "#5CE0A0"];
const blinkers = [], spinners = [], puffs = [], fires = [];

const asphalt = canvasTex(64, 64, (g, w, h) => {
  g.fillStyle = ST.ground || "#16151b"; g.fillRect(0, 0, w, h);
  for (let i = 0; i < 380; i++) { const v = 18 + Math.floor(Math.random() * 22); g.fillStyle = `rgba(${v},${v},${v + 4},.6)`; g.fillRect(Math.random() * w, Math.random() * h, 1, 1); }
  if (ST.rain !== false) { g.fillStyle = "rgba(120,160,255,.08)"; g.fillRect(0, 30, w, 3); } // a wet streak
}, true);
asphalt.repeat.set((SPAN + 200) / 6, (SPAN + 200) / 6);
const ground = new THREE.Mesh(new THREE.PlaneGeometry(SPAN + 200, SPAN + 200), new THREE.MeshLambertMaterial({ map: asphalt }));
ground.rotation.x = -Math.PI / 2; scene.add(ground);
{ // the dashes, one instanced mesh
  const per = Math.ceil(SPAN / 6), marks = new THREE.InstancedMesh(new THREE.PlaneGeometry(0.25, 2.5), new THREE.MeshBasicMaterial({ color: 0xd9c56a }), (N + 1) * per * 2);
  const o = new THREE.Object3D(); let n = 0;
  for (let i = 0; i <= N; i++) {
    const c = streetC(i);
    for (let k = 0; k < per; k++) {
      const at = -HALF + k * 6;
      if ((at + HALF + 1.5) % (B + S) < S + 1.5) continue; // no dashes across the crossroads
      o.rotation.set(-Math.PI / 2, 0, 0); o.position.set(c, 0.02, at); o.updateMatrix(); marks.setMatrixAt(n++, o.matrix);
      o.rotation.set(-Math.PI / 2, 0, Math.PI / 2); o.position.set(at, 0.02, c); o.updateMatrix(); marks.setMatrixAt(n++, o.matrix);
    }
  }
  marks.count = n; scene.add(marks);
}
const walkTex = canvasTex(32, 32, (g, w, h) => { g.fillStyle = ST.walk || "#2a2830"; g.fillRect(0, 0, w, h); g.strokeStyle = "rgba(0,0,0,.3)"; for (let i = 0; i < w; i += 8) { g.strokeRect(i, 0, 8, h); g.strokeRect(0, i, w, 8); } }, true);
{ // all the sidewalks in one instanced mesh
  walkTex.repeat.set(B / 4, B / 4);
  const walks = new THREE.InstancedMesh(new THREE.BoxGeometry(B + 3, 0.25, B + 3), new THREE.MeshLambertMaterial({ map: walkTex }), N * N);
  const o = new THREE.Object3D(); let n = 0;
  for (let i = 0; i < N; i++) for (let j = 0; j < N; j++) { o.position.set(blockX(i) + B / 2, 0.12, blockX(j) + B / 2); o.updateMatrix(); walks.setMatrixAt(n++, o.matrix); }
  scene.add(walks);
}

// building facades in the style of the district
function facade(seed) {
  const r = rng(seed), base = ST.base || [14, 14, 22], win = ST.win || ["#d8c9a0"];
  return canvasTex(32, 64, (g, w, h) => {
    const k = Math.floor(r() * 18) - 9; g.fillStyle = `rgb(${base[0] + k},${base[1] + k},${base[2] + k})`; g.fillRect(0, 0, w, h);
    for (let y = 3; y < h - 2; y += 6) for (let x = 2; x < w - 2; x += 5) {
      const lit = r();
      g.fillStyle = lit < ST.neon ? NEON[Math.floor(r() * NEON.length)] : lit < ST.lit ? win[Math.floor(r() * win.length)] : "#0b0b10";
      if (ST.red && lit < 0.04) g.fillStyle = "#e33b2e";
      g.globalAlpha = lit < ST.neon ? 0.85 : 1; g.fillRect(x, y, 3, 3); g.globalAlpha = 1;
    }
  }, true);
}
const facades = Array.from({ length: 6 }, (_, i) => facade((data.seed || 1) * 31 + i));
const KANJI = "酒夜市電脳義体薬屋拉麺歌龍寿司金融警備";
function roofSignTex(color) {
  return canvasTex(64, 16, (g, w, h) => {
    g.fillStyle = "#050408"; g.fillRect(0, 0, w, h); g.fillStyle = color; g.font = "bold 12px sans-serif"; g.textBaseline = "middle";
    let t = ""; for (let i = 0; i < 4; i++) t += KANJI[Math.floor(R() * KANJI.length)];
    g.fillText(t, 4, h / 2 + 1);
  });
}
function clockTex() {
  return canvasTex(32, 32, (g) => {
    g.fillStyle = "#031007"; g.beginPath(); g.arc(16, 16, 15, 0, 7); g.fill(); g.strokeStyle = "#39ff7a"; g.lineWidth = 2; g.stroke();
    g.beginPath(); g.moveTo(16, 16); g.lineTo(16, 5); g.moveTo(16, 16); g.lineTo(24, 18); g.stroke();
  });
}
function building(x0, z0, w, d, h, plain) {
  h = Math.max(4, h);
  const tex = facades[Math.floor(R() * facades.length)].clone(); tex.needsUpdate = true; tex.repeat.set(Math.max(1, w / 6), Math.max(1, h / 12));
  const mat = new THREE.MeshLambertMaterial({ map: tex, emissive: 0xffffff, emissiveMap: tex, emissiveIntensity: 0.55 });
  const m = new THREE.Mesh(new THREE.BoxGeometry(w, h, d), mat); // one material: one draw call per building
  m.position.set(x0 + w / 2, h / 2, z0 + d / 2); scene.add(m);
  solids.push({ x0, x1: x0 + w, z0, z1: z0 + d });
  if (plain) return m;
  if (R() < ST.roof) { // a rooftop sign that blinks
    const color = ST.red && R() < 0.5 ? "#e33b2e" : NEON[Math.floor(R() * NEON.length)];
    const s = new THREE.Mesh(new THREE.BoxGeometry(w * 0.6, 1.4, 0.3), ST.kanji ? new THREE.MeshBasicMaterial({ map: roofSignTex(color) }) : new THREE.MeshBasicMaterial({ color }));
    s.position.set(x0 + w / 2, h + 1, z0 + d / 2); scene.add(s); blinkers.push(s);
  }
  if (ST.ruins && R() < 0.4) { // an unfinished top: bare beams
    const bm = new THREE.MeshLambertMaterial({ color: 0x2a2624 });
    for (const [bx, bz] of [[x0 + 1, z0 + 1], [x0 + w - 1, z0 + 1], [x0 + 1, z0 + d - 1], [x0 + w - 1, z0 + d - 1]]) {
      const b = new THREE.Mesh(new THREE.BoxGeometry(0.5, 7, 0.5), bm); b.position.set(bx, h + 3.5, bz); scene.add(b);
    }
    const top = new THREE.Mesh(new THREE.BoxGeometry(w - 1.5, 0.4, d - 1.5), bm); top.position.set(x0 + w / 2, h + 7, z0 + d / 2); scene.add(top);
  }
  if (ST.chimneys && R() < 0.35) { // a stack with smoke
    const c = new THREE.Mesh(new THREE.CylinderGeometry(0.8, 1, 9, 8), new THREE.MeshLambertMaterial({ color: 0x3a302a }));
    const cx = x0 + 2 + R() * (w - 4), cz = z0 + 2 + R() * (d - 4); c.position.set(cx, h + 4.5, cz); scene.add(c);
    smoke(cx, h + 9.2, cz);
  }
  if (ST.clocks && R() < 0.3) { // a clock face on the facade
    const cl = new THREE.Mesh(new THREE.PlaneGeometry(4, 4), new THREE.MeshBasicMaterial({ map: clockTex(), transparent: true }));
    cl.position.set(x0 + w / 2, h * 0.7, z0 - 0.06); cl.rotation.y = Math.PI; scene.add(cl);
  }
  return m;
}
const puffTex = canvasTex(16, 16, (g) => { const gr = g.createRadialGradient(8, 8, 1, 8, 8, 8); gr.addColorStop(0, "rgba(160,150,140,.7)"); gr.addColorStop(1, "rgba(160,150,140,0)"); g.fillStyle = gr; g.fillRect(0, 0, 16, 16); });
function smoke(x, y, z) {
  for (let k = 0; k < 3; k++) {
    const s = new THREE.Sprite(new THREE.SpriteMaterial({ map: puffTex, transparent: true, depthWrite: false }));
    s.position.set(x, y, z); s.scale.set(3, 3, 1); scene.add(s); puffs.push({ s, x, y, z, t: k / 3 });
  }
}
const fireTex = canvasTex(16, 16, (g) => { const gr = g.createRadialGradient(8, 10, 1, 8, 9, 8); gr.addColorStop(0, "rgba(255,240,160,1)"); gr.addColorStop(0.4, "rgba(255,120,30,.9)"); gr.addColorStop(1, "rgba(255,40,0,0)"); g.fillStyle = gr; g.fillRect(0, 0, 16, 16); });
function fire(x, z, big) { // a burning barrel
  const barrel = new THREE.Mesh(new THREE.CylinderGeometry(0.4, 0.4, 0.9, 8), new THREE.MeshLambertMaterial({ color: 0x3a3a3a }));
  barrel.position.set(x, 0.45, z); scene.add(barrel); solids.push({ x0: x - 0.4, x1: x + 0.4, z0: z - 0.4, z1: z + 0.4 });
  const f = new THREE.Sprite(new THREE.SpriteMaterial({ map: fireTex, transparent: true, depthWrite: false, blending: THREE.AdditiveBlending }));
  f.position.set(x, 1.3, z); f.scale.set(big ? 2.4 : 1.4, big ? 2.4 : 1.6, 1); scene.add(f); fires.push(f);
}
// each block: two buildings; the facades along the streets carry the doors
const slots = []; // door slots: {x, z, nx, nz} on a street-facing facade
const MID = (N - 1) / 2;
function sidesOf(x0, z0, w, d, bx, bz) {
  const out = [];
  if (z0 - bz < 0.01) out.push({ x: x0 + w / 2, z: bz - 0.05, nx: 0, nz: -1 });
  if (z0 + d - bz >= B - 0.01) out.push({ x: x0 + w / 2, z: bz + B + 0.05, nx: 0, nz: 1 });
  if (x0 - bx < 0.01) out.push({ x: bx - 0.05, z: z0 + d / 2, nx: -1, nz: 0 });
  if (x0 + w - bx >= B - 0.01) out.push({ x: bx + B + 0.05, z: z0 + d / 2, nx: 1, nz: 0 });
  return out;
}
function tree(x, z, kind) {
  const h = kind === "palm" ? 6 + R() * 2 : 2.5 + R() * 2;
  const trunk = new THREE.Mesh(new THREE.BoxGeometry(kind === "palm" ? 0.3 : 0.4, h, kind === "palm" ? 0.3 : 0.4), new THREE.MeshLambertMaterial({ color: 0x3a2a1e }));
  trunk.position.set(x, h / 2, z); scene.add(trunk);
  const crown = new THREE.Mesh(kind === "palm" ? new THREE.BoxGeometry(3.4, 0.4, 3.4) : new THREE.BoxGeometry(2.2, 1.6, 2.2),
    new THREE.MeshLambertMaterial({ color: kind === "palm" ? 0x1a4a2a : 0x0f3a2c, emissive: new THREE.Color(NEON[Math.floor(R() * NEON.length)]), emissiveIntensity: 0.15 }));
  crown.position.set(x, h + 0.4, z); if (kind === "palm") crown.rotation.y = R(); scene.add(crown);
  solids.push({ x0: x - 0.3, x1: x + 0.3, z0: z - 0.3, z1: z + 0.3 });
}
function plaza(bx, bz) { // an open square: trees, palms or container stacks
  const pad = new THREE.Mesh(new THREE.BoxGeometry(B, 0.3, B), new THREE.MeshLambertMaterial({ color: ST.containers ? 0x22201c : ST.ground === "#6a5236" ? 0x5a4630 : 0x1b2a24 }));
  pad.position.set(bx + B / 2, 0.15, bz + B / 2); scene.add(pad);
  if (ST.containers) {
    for (let k = 0; k < 7; k++) {
      const x = bx + 3 + R() * (B - 9), z = bz + 3 + R() * (B - 6), rows = 1 + Math.floor(R() * 3);
      for (let r = 0; r < rows; r++) {
        const c = new THREE.Mesh(new THREE.BoxGeometry(6, 2.5, 2.4), new THREE.MeshLambertMaterial({ color: new THREE.Color().setHSL(R(), 0.55, 0.35) }));
        c.position.set(x + 3, 1.25 + r * 2.5, z + 1.2); scene.add(c);
      }
      solids.push({ x0: x, x1: x + 6, z0: z, z1: z + 2.4 });
    }
    return;
  }
  for (let k = 0; k < 6; k++) tree(bx + 4 + R() * (B - 8), bz + 4 + R() * (B - 8), ST.trees || "tree");
}
function lot(bx, bz) { // an empty lot in the desert: a shack or two and a fire
  for (let k = 0; k < 2; k++) if (R() < 0.7) {
    const w = 5 + R() * 4, d = 4 + R() * 3, x = bx + 2 + R() * (B - w - 4), z = bz + 2 + R() * (B - d - 4);
    building(x, z, w, d, 3 + R() * 1.5, true);
  }
  if (R() < 0.5) fire(bx + B / 2 + (R() - 0.5) * 8, bz + B / 2 + (R() - 0.5) * 8);
}
// the landmark in the centre block (phase 5)
function landmark(kind, bx, bz) {
  const cx = bx + B / 2, cz = bz + B / 2;
  const add = (geo, color, x, y, z, basic) => { const m = new THREE.Mesh(geo, basic ? new THREE.MeshBasicMaterial({ color }) : new THREE.MeshLambertMaterial({ color })); m.position.set(x, y, z); scene.add(m); return m; };
  if (kind === "tower") {
    building(bx + 3, bz + 3, B - 6, B - 6, 150, true);
    for (let y = 20; y < 150; y += 18) add(new THREE.BoxGeometry(B - 5.6, 0.6, B - 5.6), 0xff2030, cx, y, cz, true);
    const top = add(new THREE.BoxGeometry(2, 6, 2), 0xff2030, cx, 154, cz, true); blinkers.push(top);
  } else if (kind === "mega") {
    building(bx, bz, B, B, 95, true);
    for (let y = 12; y < 95; y += 12) add(new THREE.BoxGeometry(B + 0.4, 0.4, B + 0.4), 0x4de3ff, cx, y, cz, true);
  } else if (kind === "mast") {
    for (let y = 0; y < 70; y += 10) add(new THREE.BoxGeometry(2.2 - y / 50, 10, 2.2 - y / 50), 0x6a5a4a, cx, y + 5, cz);
    for (let y = 20; y < 75; y += 25) blinkers.push(add(new THREE.BoxGeometry(0.8, 0.8, 0.8), 0xff2020, cx, y, cz, true));
    solids.push({ x0: cx - 1.2, x1: cx + 1.2, z0: cz - 1.2, z1: cz + 1.2 });
    lot(bx, bz); return [];
  } else if (kind === "crane") {
    add(new THREE.BoxGeometry(2, 50, 2), 0xd0a020, cx, 25, cz); add(new THREE.BoxGeometry(40, 2, 2), 0xd0a020, cx + 12, 50, cz);
    add(new THREE.BoxGeometry(0.2, 20, 0.2), 0x222222, cx + 28, 40, cz); solids.push({ x0: cx - 1, x1: cx + 1, z0: cz - 1, z1: cz + 1 });
    plaza(bx, bz); return [];
  } else if (kind === "clock") {
    building(bx + 7, bz + 7, B - 14, B - 14, 70, true);
    for (const [x, z, ry] of [[cx, bz + 6.94, Math.PI], [cx, bz + B - 6.94, 0], [bx + 6.94, cz, -Math.PI / 2], [bx + B - 6.94, cz, Math.PI / 2]]) {
      const cl = new THREE.Mesh(new THREE.PlaneGeometry(9, 9), new THREE.MeshBasicMaterial({ map: clockTex(), transparent: true }));
      cl.position.set(x, 58, z); cl.rotation.y = ry; scene.add(cl);
    }
  } else if (kind === "shell") {
    building(bx + 4, bz + 4, B - 8, B - 8, 40, true);
    const bm = new THREE.MeshLambertMaterial({ color: 0x2a2624 });
    for (const [x, z] of [[bx + 5, bz + 5], [bx + B - 5, bz + 5], [bx + 5, bz + B - 5], [bx + B - 5, bz + B - 5]]) { const b = new THREE.Mesh(new THREE.BoxGeometry(0.8, 50, 0.8), bm); b.position.set(x, 65, z); scene.add(b); }
    for (let y = 50; y < 90; y += 10) { const f = new THREE.Mesh(new THREE.BoxGeometry(B - 9, 0.5, B - 9), bm); f.position.set(cx, y, cz); scene.add(f); }
    fire(bx + 2, bz + 2, true);
  } else if (kind === "stack") {
    building(bx + 2, bz + 2, B - 4, B - 4, 14, true);
    const c = add(new THREE.CylinderGeometry(2.2, 3, 60, 10), 0x4a3a30, cx, 44, cz); smoke(cx, 75, cz); smoke(cx + 1, 76, cz);
  } else if (kind === "pagoda") {
    building(bx + 5, bz + 5, B - 10, B - 10, 18, true);
    for (let k = 0; k < 4; k++) {
      const s = B - 8 - k * 3.5, y = 18 + k * 8;
      add(new THREE.BoxGeometry(s + 4, 0.8, s + 4), 0xb02020, cx, y, cz); add(new THREE.BoxGeometry(s, 7, s), 0x2a1a1a, cx, y + 4, cz);
    }
  } else return null;
  // its four sides take doors like any block
  return sidesOf(bx, bz, B, B, bx, bz).map((s) => s);
}
const lmBlock = ST.landmark ? [Math.floor(MID), Math.floor(MID)] : null;
for (let i = 0; i < N; i++) for (let j = 0; j < N; j++) {
  const bx = blockX(i), bz = blockX(j), ring = Math.abs(i - MID) + Math.abs(j - MID), center = ring < 1.6;
  if (lmBlock && i === lmBlock[0] && j === lmBlock[1]) { const sides = landmark(ST.landmark, bx, bz); if (sides) { slots.push(...sides); continue; } }
  if (!center && ring > 1 && ST.empty && R() < ST.empty) { lot(bx, bz); continue; }
  if (!center && ring > 2 && R() < ST.plaza) { plaza(bx, bz); continue; }
  const split = R() > 0.5;
  const parts = split ? [[0, 0, B, B / 2 - 1], [0, B / 2 + 1, B, B / 2 - 1]] : [[0, 0, B / 2 - 1, B], [B / 2 + 1, 0, B / 2 - 1, B]];
  for (const [ox, oz, w, d] of parts) {
    const h0 = center ? 34 + R() * 46 : ring < 3.5 ? 18 + R() * 30 : 9 + R() * 16; // towers, then mid-rise, then low blocks
    building(bx + ox, bz + oz, w, d, h0 * ST.h);
    slots.push(...sidesOf(bx + ox, bz + oz, w, d, bx, bz));
  }
}
// the edge: a ring of blind blocks with ways out to the other districts (phase 11)
const EXIT_STREET = Math.round(N / 2);
const SIDES = [{ ax: "z", sg: -1 }, { ax: "x", sg: 1 }, { ax: "z", sg: 1 }, { ax: "x", sg: -1 }]; // north, east, south, west
const exits = (data.exits || []).slice(0, 4).map((e, k) => ({ ...e, side: SIDES[k], c: streetC(EXIT_STREET) }));
function gateTex(text) {
  return canvasTex(256, 64, (g, w, h) => {
    g.fillStyle = "#06060a"; g.fillRect(0, 0, w, h); g.strokeStyle = "#5ce0a0"; g.lineWidth = 4; g.strokeRect(2, 2, w - 4, h - 4);
    g.fillStyle = "#5ce0a0"; g.font = "bold 26px system-ui, sans-serif"; g.textAlign = "center"; g.textBaseline = "middle";
    let t = text; while (g.measureText(t).width > w - 16 && t.length > 4) t = t.slice(0, -2) + "…";
    g.fillText(t, w / 2, h / 2 + 1);
  });
}
{
  const pieces = [];
  for (let j = 0; j < N; j++) pieces.push([blockX(j), blockX(j) + B, -1]);
  for (let i = 0; i <= N; i++) pieces.push([streetC(i) - S / 2, streetC(i) + S / 2, i]);
  SIDES.forEach((sd, k) => {
    const ex = exits[k];
    const outer = sd.sg > 0 ? HALF : -HALF - D_RING;
    const list = pieces.slice();
    if (sd.ax === "x") list.push([-HALF - D_RING, -HALF, -1], [HALF, HALF + D_RING, -1]); // the corners belong to east and west
    for (const [a0, a1, si] of list) {
      if (ex && si === EXIT_STREET) continue; // the way out
      const h = 14 + R() * 30;
      if (sd.ax === "x") building(outer, a0, D_RING, a1 - a0, h, true); else building(a0, outer, a1 - a0, D_RING, h, true);
    }
    if (!ex) return;
    // the gate at the end of the way out: a barrier, an arch and the name of the district
    const far = sd.sg * (HALF + D_RING + 0.5), c = ex.c;
    const bar = new THREE.Mesh(new THREE.BoxGeometry(sd.ax === "x" ? 1 : S, 6, sd.ax === "x" ? S : 1), new THREE.MeshLambertMaterial({ color: 0x101016 }));
    if (sd.ax === "x") { bar.position.set(far, 3, c); solids.push({ x0: far - 0.5, x1: far + 0.5, z0: c - S / 2, z1: c + S / 2 }); }
    else { bar.position.set(c, 3, far); solids.push({ x0: c - S / 2, x1: c + S / 2, z0: far - 0.5, z1: far + 0.5 }); }
    scene.add(bar);
    const sign = new THREE.Mesh(new THREE.PlaneGeometry(9, 2.25), new THREE.MeshBasicMaterial({ map: gateTex((ex.home ? "⌂ " : "→ ") + ex.name) }));
    const at = sd.sg * (HALF + 1);
    if (sd.ax === "x") { sign.position.set(at, 7.5, c); sign.rotation.y = sd.sg > 0 ? -Math.PI / 2 : Math.PI / 2; }
    else { sign.position.set(c, 7.5, at); sign.rotation.y = sd.sg > 0 ? Math.PI : 0; }
    scene.add(sign); blinkers.push(sign);
    const arch = new THREE.Mesh(new THREE.BoxGeometry(sd.ax === "x" ? 0.4 : S, 0.4, sd.ax === "x" ? S : 0.4), new THREE.MeshBasicMaterial({ color: 0x5ce0a0 }));
    if (sd.ax === "x") arch.position.set(at, 6, c); else arch.position.set(c, 6, at);
    scene.add(arch);
  });
}
// spread the doors over the slots nearest to the centre first
slots.forEach((sl) => { sl.k = Math.hypot(sl.x, sl.z) + R() * 40; });
slots.sort((a, b) => a.k - b.k);
const usedSlots = new Set();

function signTex(text, color) {
  return canvasTex(256, 64, (g, w, h) => {
    g.fillStyle = "#07060a"; g.fillRect(0, 0, w, h);
    g.strokeStyle = color; g.lineWidth = 4; g.strokeRect(2, 2, w - 4, h - 4);
    g.fillStyle = color; g.font = "bold 28px system-ui, sans-serif"; g.textAlign = "center"; g.textBaseline = "middle";
    let t = text; while (g.measureText(t).width > w - 16 && t.length > 4) t = t.slice(0, -2) + "…";
    g.fillText(t, w / 2, h / 2 + 1);
  });
}
function tagTex(color, seed) { // a gang tag: loops and a few letters (phase 3)
  const r = rng(seed);
  return canvasTex(96, 48, (g, w, h) => {
    g.clearRect(0, 0, w, h); g.strokeStyle = color; g.lineWidth = 3; g.lineCap = "round";
    g.beginPath(); g.moveTo(6, 30 + r() * 10);
    for (let i = 0; i < 9; i++) g.quadraticCurveTo(10 + i * 9 + r() * 6, 4 + r() * 40, 14 + i * 9, 10 + r() * 30);
    g.stroke(); g.fillStyle = "#fff"; g.globalAlpha = 0.6; g.font = "bold 14px sans-serif";
    g.fillText(String.fromCharCode(65 + Math.floor(r() * 26)) + String.fromCharCode(65 + Math.floor(r() * 26)), 60 + r() * 20, 40); g.globalAlpha = 1;
  });
}
function skylineTex() {
  return canvasTex(64, 32, (g, w, h) => {
    g.fillStyle = "#0a0816"; g.fillRect(0, 0, w, h);
    for (let x = 0; x < w; x += 6) { const bh = 8 + Math.random() * 20; g.fillStyle = "#14102a"; g.fillRect(x, h - bh, 5, bh); for (let y = h - bh + 2; y < h; y += 3) if (Math.random() > 0.5) { g.fillStyle = NEON[Math.floor(Math.random() * NEON.length)]; g.fillRect(x + 1 + Math.floor(Math.random() * 3), y, 1, 1); } }
  });
}
const doors = [];
data.doors.forEach((d, i) => {
  const s = slots[i]; if (!s) return;
  usedSlots.add(i);
  const color = NEON[i % NEON.length];
  const rot = Math.atan2(s.nx, s.nz);
  const door = new THREE.Mesh(new THREE.PlaneGeometry(2.4, 3.2), new THREE.MeshBasicMaterial({ color: 0x0a0a0f }));
  door.position.set(s.x + s.nx * 0.02, 1.6, s.z + s.nz * 0.02); door.rotation.y = rot; scene.add(door);
  const frame = new THREE.Mesh(new THREE.PlaneGeometry(2.8, 3.6), new THREE.MeshBasicMaterial({ color }));
  frame.position.copy(door.position).add(new THREE.Vector3(-s.nx * 0.01, 0, -s.nz * 0.01)); frame.rotation.y = rot; scene.add(frame);
  const sign = new THREE.Mesh(new THREE.PlaneGeometry(7, 1.75), new THREE.MeshBasicMaterial({ map: signTex(d.label, color) }));
  sign.position.set(s.x + s.nx * 0.05, 4.4, s.z + s.nz * 0.05); sign.rotation.y = rot; scene.add(sign); blinkers.push(sign);
  const glow = new THREE.PointLight(new THREE.Color(color), 1.2, 9, 1.6); glow.position.set(s.x + s.nx * 1.5, 3, s.z + s.nz * 1.5);
  if (i < 2) scene.add(glow); // two real lights, the rest is emissive (every light costs every pixel)
  doors.push({ ...d, x: s.x + s.nx * 1.6, z: s.z + s.nz * 1.6, nx: s.nx, nz: s.nz, color });
});
function freeSlot(seed) { // a facade without a door, picked by a number (gigs, graffiti)
  if (!slots.length) return null;
  for (let k = 0; k < slots.length; k++) {
    const i = (seed + k * 7) % slots.length;
    if (!usedSlots.has(i)) { usedSlots.add(i); return slots[i]; }
  }
  return null;
}

// street lamps, rain, fires of the Purge, the Blackwall in the sky
{
  const cnt = (N + 1) * N * 2, poles = new THREE.InstancedMesh(new THREE.BoxGeometry(0.15, 5, 0.15), new THREE.MeshLambertMaterial({ color: 0x222228 }), cnt);
  const bulbs = new THREE.InstancedMesh(new THREE.BoxGeometry(0.6, 0.2, 0.6), new THREE.MeshBasicMaterial({ color: PURGE ? 0xff5040 : 0xfff1c9 }), cnt), o = new THREE.Object3D(); let n = 0;
  for (let i = 0; i <= N; i++) for (let j = 0; j < N; j++) {
    const a = streetC(i) + 4.5, b = blockX(j) + B / 2;
    for (const [x, z] of [[a, b], [b, a - 9]]) {
      o.position.set(x, 2.5, z); o.updateMatrix(); poles.setMatrixAt(n, o.matrix);
      o.position.set(x, 5, z); o.updateMatrix(); bulbs.setMatrixAt(n, o.matrix); n++;
    }
  }
  scene.add(poles, bulbs);
}
const RAINY = ST.rain !== false;
const RAIN = RAINY ? 1400 : 0, rainGeo = new THREE.BufferGeometry(), rainPos = new Float32Array(Math.max(1, RAIN) * 3);
for (let i = 0; i < RAIN; i++) { rainPos[i * 3] = (Math.random() - 0.5) * 80; rainPos[i * 3 + 1] = Math.random() * 30; rainPos[i * 3 + 2] = (Math.random() - 0.5) * 80; }
rainGeo.setAttribute("position", new THREE.BufferAttribute(rainPos, 3));
if (RAINY) scene.add(new THREE.Points(rainGeo, new THREE.PointsMaterial({ color: 0x9fb4ff, size: 0.08, transparent: true, opacity: 0.6 })));
const nFires = (ST.fires || 0) + (PURGE ? 22 : 0);
for (let k = 0; k < nFires; k++) { // barrels on the sidewalks next to the streets
  const i = Math.floor(R() * (N + 1)), along = (R() - 0.5) * (SPAN - 20), side = R() > 0.5 ? 5.2 : -5.2;
  if (R() > 0.5) fire(streetC(i) + side, along, PURGE && R() < 0.3); else fire(along, streetC(i) + side, PURGE && R() < 0.3);
}
const cubes = [];
if (BW > 0) { // the Blackwall: black cubes with red edges hang over an infected district (phase 6)
  const n = Math.round(4 + BW * 14);
  for (let k = 0; k < n; k++) {
    const sz = 2 + R() * 6, c = new THREE.Mesh(new THREE.BoxGeometry(sz, sz, sz), new THREE.MeshBasicMaterial({ color: 0x000000 }));
    const e = new THREE.LineSegments(new THREE.EdgesGeometry(c.geometry), new THREE.LineBasicMaterial({ color: 0xff1030 })); c.add(e);
    c.position.set((R() - 0.5) * SPAN, 30 + R() * 40, (R() - 0.5) * SPAN); scene.add(c); cubes.push(c);
  }
}

// ---------------------------------------------------------------- people: pixel sprites
function personTex(seed, arch, coatColor) {
  const r = rng(seed);
  const skin = ["#f1c7a5", "#d9a37c", "#a8714f", "#6e4630", "#e8d1c0"][Math.floor(r() * 5)];
  const coat = coatColor || { cop: "#2b4cff", corpo: "#2b2b33", bartender: "#5a2a1a", vendor: "#b8692b", merc: "#3c4a2a", ganger: "#b0203a", homeless: "#4a4038",
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
    if (coatColor) { g.fillStyle = "#111"; g.fillRect(4, 13, 8, 2); g.fillStyle = coatColor; g.fillRect(5, 2, 6, 1); } // a gang's colours
    else if (r() > 0.6) { g.fillStyle = NEON[Math.floor(r() * NEON.length)]; g.fillRect(4, 12, 8, 1); } // a glowing stripe of chrome
  });
}
function labelTex(text, color) {
  return canvasTex(192, 24, (g, w, h) => {
    g.font = "bold 15px system-ui, sans-serif"; g.textAlign = "center"; g.textBaseline = "middle";
    while (g.measureText(text).width > w - 12 && text.length > 4) text = text.slice(0, -2) + "…";
    const tw = Math.min(w - 4, g.measureText(text).width + 10);
    g.fillStyle = "rgba(0,0,0,.6)"; g.fillRect((w - tw) / 2, 2, tw, h - 4);
    g.fillStyle = color || "#fff"; g.fillText(text, w / 2, h / 2 + 1);
  });
}
const people = [];
const roomKey = (door) => door.room || (door.id.startsWith("p.") ? door.id.slice(2) : door.id);
const roomOf = (door) => (door && hasRoom(roomKey(door)) ? roomKey(door) : null);
for (const p of data.npcs || []) {
  const d = doors.find((x) => x.id === p.door) || doors[Math.floor(R() * doors.length)];
  if (!d) continue;
  const inside = !!roomOf(d) && p.id % 2 === 0; // half of a place's people stand inside its room (phase 10)
  const spr = new THREE.Sprite(new THREE.SpriteMaterial({ map: personTex(hash(p.name), p.arch) }));
  spr.scale.set(0.9, 1.8, 1);
  const a = R() * Math.PI * 2, rad = 2 + R() * 3;
  spr.position.set(d.x + Math.cos(a) * rad, 0.9, d.z + Math.sin(a) * rad);
  if (!inside) scene.add(spr);
  people.push({ ...p, demo: !!data.demo, spr, home: d, inside, tx: spr.position.x, tz: spr.position.z, wait: R() * 3 });
}
const crowdTex = Array.from({ length: 12 }, (_, i) => personTex(i * 977 + (data.seed || 0), ""));
for (let i = 0; i < (PURGE ? 22 : 44); i++) { // the crowd: half as many on the Purge night
  const spr = new THREE.Sprite(new THREE.SpriteMaterial({ map: crowdTex[i % crowdTex.length] }));
  spr.scale.set(0.9, 1.8, 1);
  const lane = Math.floor(R() * (N + 1)), along = (R() - 0.5) * SPAN, x = streetC(lane) + (R() > 0.5 ? 4.6 : -4.6);
  const onZ = R() > 0.5;
  spr.position.set(onZ ? x : along, 0.9, onZ ? along : x); scene.add(spr);
  people.push({ crowd: true, spr, dir: R() > 0.5 ? 1 : -1, axis: onZ ? "z" : "x", speed: (0.8 + R() * 0.8) * (PURGE ? 2 : 1) });
}

// ---------------------------------------------------------------- the gangs on their corners (phase 3)
const gangers = [];
{
  const gs = data.gangs || [];
  let groups = 0;
  gs.forEach((g, gi) => {
    const n = 1 + (g.pct >= 50 ? 1 : 0) + (g.pct >= 75 ? 1 : 0);
    const tex = Array.from({ length: 3 }, (_, k) => personTex(hash(g.key) + k * 31, "", g.color));
    for (let k = 0; k < n && groups < 6; k++, groups++) {
      const i = 1 + Math.floor(R() * (N - 1)), j = 1 + Math.floor(R() * (N - 1));
      const cx = streetC(i) + (R() > 0.5 ? 7.2 : -7.2), cz = streetC(j) + (R() > 0.5 ? 7.2 : -7.2);
      const m = 3 + Math.floor(R() * 2);
      for (let q = 0; q < m; q++) {
        const spr = new THREE.Sprite(new THREE.SpriteMaterial({ map: tex[q % tex.length] }));
        spr.scale.set(0.95, 1.9, 1); spr.position.set(cx + (R() - 0.5) * 3, 0.95, cz + (R() - 0.5) * 3); scene.add(spr);
        gangers.push({ gang: g, spr, bx: spr.position.x, bz: spr.position.z, ph: R() * 6 });
      }
      // a tag on the nearest free facade
      let best = -1, bd = 1e9;
      slots.forEach((s, si) => { if (usedSlots.has(si)) return; const dd = Math.hypot(s.x - cx, s.z - cz); if (dd < bd) { bd = dd; best = si; } });
      if (best >= 0 && bd < 30) {
        usedSlots.add(best); const s = slots[best];
        const tag = new THREE.Mesh(new THREE.PlaneGeometry(4.2, 2.1), new THREE.MeshBasicMaterial({ map: tagTex(g.color, hash(g.key) + groups), transparent: true }));
        tag.position.set(s.x + s.nx * 0.07, 2, s.z + s.nz * 0.07); tag.rotation.y = Math.atan2(s.nx, s.nz); scene.add(tag);
      }
    }
  });
}

// ---------------------------------------------------------------- the gigs on the map (phase 4)
const beacons = [];
const beaconGeo = new THREE.OctahedronGeometry(0.7), beaconMat = new THREE.MeshBasicMaterial({ color: 0xffd23f });
const beamMat = new THREE.MeshBasicMaterial({ color: 0xffd23f, transparent: true, opacity: 0.18, depthWrite: false });
function beacon(x, z, info) {
  const m = new THREE.Mesh(beaconGeo, beaconMat); m.position.set(x, 3.6, z); scene.add(m); spinners.push(m);
  const beam = new THREE.Mesh(new THREE.CylinderGeometry(0.25, 0.25, 40, 6, 1, true), beamMat); beam.position.set(x, 20, z); scene.add(beam);
  beacons.push({ ...info, x, z, m });
}
for (const g of data.gigs || []) {
  const s = freeSlot(g.id * 13 + 5); if (!s) continue;
  beacon(s.x + s.nx * 2.2, s.z + s.nz * 2.2, { gig: g });
}
if (data.gig_run) { const f = doors.find((d) => d.id === "gigs"); if (f) beacon(f.x, f.z, { run: true }); }

// ---------------------------------------------------------------- traffic and the police (phase 2)
const vehicleKind = (data.vehicle && (data.vehicle.kind || (/мото|байк|bike|скутер/i.test(data.vehicle.name || "") ? "bike" : "car"))) || "car";
function carMesh(color, kind) {
  const g = new THREE.Group();
  const L = kind === "truck" ? 5.6 : kind === "joke" ? 2.6 : 4.2, W = kind === "truck" ? 2.4 : kind === "joke" ? 1.4 : 2;
  const body = new THREE.Mesh(new THREE.BoxGeometry(W, kind === "truck" ? 1.1 : 0.8, L), new THREE.MeshLambertMaterial({ color })); body.position.y = kind === "truck" ? 0.9 : 0.7; g.add(body);
  const cab = new THREE.Mesh(new THREE.BoxGeometry(W * 0.85, kind === "truck" ? 1.2 : 0.7, kind === "truck" ? 1.8 : L * 0.48), new THREE.MeshLambertMaterial({ color: 0x101018 }));
  cab.position.set(0, kind === "truck" ? 2 : 1.4, kind === "truck" ? L * 0.25 : -0.2); g.add(cab);
  const head = new THREE.Mesh(new THREE.BoxGeometry(W * 0.8, 0.2, 0.1), new THREE.MeshBasicMaterial({ color: 0xfff6d0 })); head.position.set(0, 0.8, L / 2 + 0.02); g.add(head);
  const tail = new THREE.Mesh(new THREE.BoxGeometry(W * 0.8, 0.2, 0.1), new THREE.MeshBasicMaterial({ color: 0xff2040 })); tail.position.set(0, 0.8, -L / 2 - 0.02); g.add(tail);
  if (kind !== "aero") for (const [x, z] of [[-W / 2, L / 3], [W / 2, L / 3], [-W / 2, -L / 3], [W / 2, -L / 3]]) {
    const w = new THREE.Mesh(new THREE.BoxGeometry(0.3, 0.6, 0.6), new THREE.MeshLambertMaterial({ color: 0x050505 })); w.position.set(x, 0.3, z); g.add(w);
  } else { // an AV floats: a glow under it instead of wheels
    const glow = new THREE.Mesh(new THREE.PlaneGeometry(W, L), new THREE.MeshBasicMaterial({ color: 0x4de3ff, transparent: true, opacity: 0.5 }));
    glow.rotation.x = -Math.PI / 2; glow.position.y = 0.05; g.add(glow); g.userData.hover = true;
  }
  return g;
}
function bikeMesh(color) {
  const g = new THREE.Group();
  const body = new THREE.Mesh(new THREE.BoxGeometry(0.5, 0.5, 2), new THREE.MeshLambertMaterial({ color })); body.position.y = 0.75; g.add(body);
  const seat = new THREE.Mesh(new THREE.BoxGeometry(0.45, 0.2, 0.8), new THREE.MeshLambertMaterial({ color: 0x101018 })); seat.position.set(0, 1.08, -0.3); g.add(seat);
  const bar = new THREE.Mesh(new THREE.BoxGeometry(0.9, 0.08, 0.08), new THREE.MeshLambertMaterial({ color: 0x222222 })); bar.position.set(0, 1.25, 0.75); g.add(bar);
  const head = new THREE.Mesh(new THREE.BoxGeometry(0.3, 0.2, 0.1), new THREE.MeshBasicMaterial({ color: 0xfff6d0 })); head.position.set(0, 1, 1.02); g.add(head);
  const tail = new THREE.Mesh(new THREE.BoxGeometry(0.3, 0.12, 0.1), new THREE.MeshBasicMaterial({ color: 0xff2040 })); tail.position.set(0, 0.9, -1.02); g.add(tail);
  for (const z of [0.85, -0.85]) { const w = new THREE.Mesh(new THREE.BoxGeometry(0.18, 0.7, 0.7), new THREE.MeshLambertMaterial({ color: 0x050505 })); w.position.set(0, 0.35, z); g.add(w); }
  return g;
}
const rideMesh = (color, kind) => (kind === "bike" ? bikeMesh(color) : carMesh(color, kind));
const traffic = [];
for (let i = 0; i < (PURGE ? 6 : 12); i++) {
  const c = carMesh(new THREE.Color().setHSL(R(), 0.5, 0.35), "car"); const lane = Math.floor(R() * (N + 1));
  const axis = R() > 0.5 ? "x" : "z", dir = R() > 0.5 ? 1 : -1, line = streetC(lane) + dir * 2.4;
  if (axis === "z") c.position.set(line, 0, (R() - 0.5) * SPAN); else c.position.set((R() - 0.5) * SPAN, 0, line);
  c.rotation.y = axis === "z" ? (dir > 0 ? 0 : Math.PI) : (dir > 0 ? Math.PI / 2 : -Math.PI / 2);
  scene.add(c); traffic.push({ c, axis, dir, speed: 8 + R() * 6 });
}
const cops = [];
const LAW = data.law, MAXTAC = LAW === "maxtac";
if (LAW && !PURGE) {
  const n = Math.min(5, 1 + Math.floor((data.heat || 1) / 2));
  for (let k = 0; k < n; k++) {
    const m = carMesh(MAXTAC ? 0x0a0a0c : 0x1b2a6b, MAXTAC ? "aero" : "car");
    const red = new THREE.Mesh(new THREE.BoxGeometry(0.5, 0.2, 0.3), new THREE.MeshBasicMaterial({ color: 0xff1020 })); red.position.set(-0.35, 1.85, -0.2); m.add(red);
    const blue = new THREE.Mesh(new THREE.BoxGeometry(0.5, 0.2, 0.3), new THREE.MeshBasicMaterial({ color: 0x2050ff })); blue.position.set(0.35, 1.85, -0.2); m.add(blue);
    const lane = Math.floor(R() * (N + 1)), axis = R() > 0.5 ? "x" : "z";
    const cop = { m, red, blue, axis, dir: R() > 0.5 ? 1 : -1, x: 0, z: 0, yaw: 0, mode: "patrol", speed: 9 };
    if (axis === "z") { cop.x = streetC(lane); cop.z = (R() - 0.5) * (SPAN - 30); } else { cop.z = streetC(lane); cop.x = (R() - 0.5) * (SPAN - 30); }
    scene.add(m); cops.push(cop);
  }
}
const chase = { on: false, caughtT: 0, lostT: 0, cool: 6, done: false };
function updateHeat() {
  const el = $("heat");
  if (!LAW || PURGE) { el.style.display = "none"; return; }
  el.style.display = "block"; el.textContent = "🚨 Розыск " + data.heat + " · " + (MAXTAC ? "МаксТак" : "NCPD") + (chase.on ? " · ПОГОНЯ" : "");
  el.classList.toggle("chase", chase.on);
}
updateHeat();

// ---------------------------------------------------------------- the player and the ride
const D0 = doors[0] || { x: 0, z: 0, nx: 0, nz: 1 };
const tx0 = -D0.nz, tz0 = D0.nx; // along the street
const player = { x: D0.x + D0.nx * 4.4 + tx0 * 9, z: D0.z + D0.nz * 4.4 + tz0 * 9, yaw: 0, pitch: 0.05, run: false, driving: false, cam: "fp" };
player.yaw = Math.atan2(-(D0.x - player.x), -(D0.z - player.z));
let ride = null;
if (data.vehicle) { // the character's own ride at the curb next to the start (phase 9: the model by its kind)
  const m = rideMesh(new THREE.Color(T.acc), vehicleKind); scene.add(m);
  const rx = D0.x + D0.nx * 2.8 - tx0 * 5, rz = D0.z + D0.nz * 2.8 - tz0 * 5, ryaw = Math.atan2(-tx0, -tz0);
  const accel = vehicleKind === "truck" ? 0.42 : vehicleKind === "joke" ? 0.5 : 0.6;
  ride = { m, kind: vehicleKind, bike: vehicleKind === "bike", x: rx, z: rz, yaw: ryaw, v: 0, accel, max: 10 + (data.vehicle.speed || 5) * 2.6, dmg: data.vehicle.dmg || 0 };
  m.position.set(rx, m.userData.hover ? 0.9 : 0, rz); m.rotation.y = ryaw + Math.PI;
}
const rideY = () => (ride && ride.m.userData.hover ? 0.9 + Math.sin(performance.now() / 300) * 0.08 : 0);
// arriving from another district: at the gate that leads back (phase 11)
try {
  const ar = JSON.parse(localStorage.getItem("nc3d.arrive") || "null");
  if (ar && Date.now() - ar.t < 10 * 60 * 1000) {
    localStorage.removeItem("nc3d.arrive");
    const ex = exits.find((e) => e.key === ar.from);
    if (ex) {
      const inner = ex.side.sg * (HALF - 3), c = ex.c;
      if (ex.side.ax === "x") { player.x = inner; player.z = c - 2; player.yaw = ex.side.sg > 0 ? Math.PI / 2 : -Math.PI / 2; }
      else { player.x = c - 2; player.z = inner; player.yaw = ex.side.sg > 0 ? 0 : Math.PI; }
      if (ride && ar.drv) {
        ride.x = ex.side.ax === "x" ? inner : c + 2.4; ride.z = ex.side.ax === "x" ? c + 2.4 : inner; ride.yaw = player.yaw;
        player.driving = true;
      }
    }
  }
} catch (e) {}

function blocked(x, z, r) {
  if (Math.abs(x) > HALF + D_RING + 1 || Math.abs(z) > HALF + D_RING + 1) return true;
  for (const b of solids) if (x > b.x0 - r && x < b.x1 + r && z > b.z0 - r && z < b.z1 + r) return true;
  return false;
}

// my own figure for the third-person view (phase 7) and the figures of other players (phase 1)
const lookCache = {};
function lookTex(cid) {
  if (lookCache[cid]) return lookCache[cid];
  const tex = new THREE.TextureLoader().load("/api/city3d/look?t=" + encodeURIComponent(TOKEN) + "&c=" + cid);
  tex.magFilter = THREE.NearestFilter; tex.minFilter = THREE.NearestFilter; tex.colorSpace = THREE.SRGBColorSpace;
  lookCache[cid] = tex; return tex;
}
const meSpr = new THREE.Sprite(new THREE.SpriteMaterial({ map: TOKEN && data.me && data.me.c ? lookTex(data.me.c) : personTex(7, "merc") }));
meSpr.scale.set(1.44, 1.85, 1); meSpr.visible = false; scene.add(meSpr);

// ---------------------------------------------------------------- the rooms (phase 10)
let room = null; // {door, built, people: [...], others: {}}
const roomCache = {};
const roomHelpers = { canvasTex, signTex, tagTex, skylineTex, rng, hash };
function enterRoom(door, at) {
  const kind = roomKey(door);
  if (!roomCache[door.id]) roomCache[door.id] = buildRoom(THREE, kind, door.label, roomHelpers);
  const built = roomCache[door.id];
  room = { door, built, people: [] };
  // the residents of the place who stand inside
  let si = 0;
  for (const p of people) {
    if (p.crowd || !p.inside || p.home !== door || p.gone) continue;
    const sp = built.spots[si % built.spots.length]; si++;
    if (p.spr.parent !== built.scene) built.scene.add(p.spr);
    p.spr.position.set(sp[0], 0.9, sp[1]); p.spr.material.rotation = 0; p.down = 0;
    room.people.push(p);
  }
  built.scene.add(meSpr);
  player.rx = at ? at.x : built.spawn.x; player.rz = at ? at.z : built.spawn.z; player.ryaw = at ? at.yaw : built.spawn.yaw;
  player.driving = false;
  sound.tone(320, 0.12, 0.05); sound.tone(480, 0.12, 0.04);
  posNow();
}
function leaveRoom() {
  if (!room) return;
  const d = room.door; scene.add(meSpr);
  room = null;
  player.x = d.x + d.nx * 1.2; player.z = d.z + d.nz * 1.2; player.yaw = Math.atan2(d.nx, d.nz);
  sound.tone(480, 0.1, 0.04); sound.tone(320, 0.1, 0.04);
  posNow();
}
function roomBlocked(x, z, r) {
  if (Math.abs(x) > ROOM_W / 2 - 0.5 - r || Math.abs(z) > ROOM_D / 2 - 0.5 - r) return true;
  for (const b of room.built.solids) if (x > b.x0 - r && x < b.x1 + r && z > b.z0 - r && z < b.z1 + r) return true;
  return false;
}

// owner report 30.10: after a door the player comes back to the same spot (and room); 20 minutes
const SPOT_KEY = "nc3d.spot";
function saveSpot() {
  try {
    localStorage.setItem(SPOT_KEY, JSON.stringify({ d: data.district, t: Date.now(), x: player.x, z: player.z, yaw: player.yaw,
      pitch: player.pitch, driving: player.driving, cam: player.cam, ride: ride ? { x: ride.x, z: ride.z, yaw: ride.yaw } : null,
      room: room ? { id: room.door.id, x: player.rx, z: player.rz, yaw: player.ryaw } : null }));
  } catch (e) {}
}
try {
  const sp = JSON.parse(localStorage.getItem(SPOT_KEY) || "null");
  if (sp && sp.d === data.district && Date.now() - sp.t < 20 * 60 * 1000 && !blocked(sp.x, sp.z, 0.3)) {
    player.x = sp.x; player.z = sp.z; player.yaw = sp.yaw; player.pitch = sp.pitch || 0; player.cam = sp.cam || "fp";
    if (ride && sp.ride) { ride.x = sp.ride.x; ride.z = sp.ride.z; ride.yaw = sp.ride.yaw; }
    if (ride && sp.driving) player.driving = true;
    if (sp.room) { const d = doors.find((x) => x.id === sp.room.id); if (d && roomOf(d)) enterRoom(d, sp.room); }
  }
} catch (e) {}
if (ride) { ride.m.position.set(ride.x, rideY(), ride.z); ride.m.rotation.y = ride.yaw + Math.PI; }

// ---------------------------------------------------------------- other players (phase 1)
const others = {}; // character id -> {spr, label, car, x, z, yaw, tx, tz, seen}
function syncOthers(list) {
  const now = performance.now();
  for (const o of list || []) {
    let it = others[o.c];
    if (!it) {
      const spr = new THREE.Sprite(new THREE.SpriteMaterial({ map: lookTex(o.c) })); spr.scale.set(1.44, 1.85, 1);
      const label = new THREE.Sprite(new THREE.SpriteMaterial({ map: labelTex(o.name, T.acc2), depthTest: false })); label.scale.set(3.2, 0.4, 1);
      const car = rideMesh(new THREE.Color().setHSL((hash(o.name) % 360) / 360, 0.7, 0.45), o.drv === "bike" ? "bike" : o.drv || "car");
      it = others[o.c] = { c: o.c, name: o.name, spr, label, car, x: o.x, z: o.z, yaw: o.yaw };
    }
    Object.assign(it, { tx: o.x, tz: o.z, tyaw: o.yaw, drv: o.drv, room: o.room, seen: now });
  }
  for (const k of Object.keys(others)) if (now - others[k].seen > 4000) { const it = others[k]; for (const m of [it.spr, it.label, it.car]) if (m.parent) m.parent.remove(m); delete others[k]; }
}
function placeOthers(dt) {
  const here = room ? room.built.scene : scene, roomId = room ? room.door.id : "";
  for (const it of Object.values(others)) {
    const show = (it.room || "") === roomId;
    const k = Math.min(1, dt * 6); it.x += (it.tx - it.x) * k; it.z += (it.tz - it.z) * k; it.yaw = it.tyaw;
    for (const m of [it.spr, it.label, it.car]) { if (m.parent && m.parent !== here) m.parent.remove(m); }
    if (!show) continue;
    if (it.drv && !room) { if (!it.car.parent) here.add(it.car); it.car.position.set(it.x, 0, it.z); it.car.rotation.y = it.yaw + Math.PI; }
    else { if (!it.spr.parent) here.add(it.spr); it.spr.position.set(it.x, 0.93, it.z); }
    if (!it.label.parent) here.add(it.label); it.label.position.set(it.x, it.drv && !room ? 2.8 : 2.3, it.z);
  }
}
function posNow() {
  if (!TOKEN || data.demo || posBusy) return;
  posBusy = true;
  const at = room ? { x: player.rx, z: player.rz, yaw: player.ryaw } : player.driving ? { x: ride.x, z: ride.z, yaw: ride.yaw } : { x: player.x, z: player.z, yaw: player.yaw };
  post("/api/city3d/pos", { ...at, drv: player.driving && ride ? ride.kind : "", room: room ? room.door.id : "" })
    .then((d) => { if (d && d.ok) syncOthers(d.players); }).finally(() => { posBusy = false; });
}
setInterval(posNow, 1500);

// ---------------------------------------------------------------- input
const keys = {};
addEventListener("keydown", (e) => { keys[e.code] = true; sound.start(); if (e.code === "KeyE" || e.code === "Enter") act(); if (e.code === "KeyF") toggleCar(); if (e.code === "KeyV") toggleCam(); if (e.code === "Escape") leave(); });
addEventListener("keyup", (e) => { keys[e.code] = false; });
const stick = { x: 0, y: 0, id: null }, look = { id: null, lx: 0, ly: 0 };
const stickEl = $("stick"), knob = $("knob");
function stickMove(e) {
  const r = stickEl.getBoundingClientRect(), cx = r.left + r.width / 2, cy = r.top + r.height / 2;
  let dx = e.clientX - cx, dy = e.clientY - cy; const len = Math.hypot(dx, dy), max = r.width / 2 - 10;
  if (len > max) { dx *= max / len; dy *= max / len; }
  stick.x = dx / max; stick.y = dy / max; knob.style.transform = `translate(${dx}px, ${dy}px)`;
}
stickEl.addEventListener("pointerdown", (e) => { sound.start(); stick.id = e.pointerId; stickEl.setPointerCapture(e.pointerId); stickMove(e); });
stickEl.addEventListener("pointermove", (e) => { if (e.pointerId === stick.id) stickMove(e); });
const stickEnd = (e) => { if (e.pointerId !== stick.id) return; stick.id = null; stick.x = stick.y = 0; knob.style.transform = ""; };
stickEl.addEventListener("pointerup", stickEnd); stickEl.addEventListener("pointercancel", stickEnd);
canvas.addEventListener("pointerdown", (e) => { sound.start(); look.id = e.pointerId; look.lx = e.clientX; look.ly = e.clientY; canvas.setPointerCapture(e.pointerId); });
canvas.addEventListener("pointermove", (e) => {
  if (e.pointerId !== look.id) return;
  const dyaw = (e.clientX - look.lx) * 0.006;
  if (room) player.ryaw -= dyaw; else player.yaw -= dyaw;
  player.pitch = clamp(player.pitch - (e.clientY - look.ly) * 0.004, -1.1, 1.1);
  look.lx = e.clientX; look.ly = e.clientY;
});
canvas.addEventListener("pointerup", () => { look.id = null; });
$("run").addEventListener("click", () => { player.run = !player.run; $("run").style.borderColor = player.run ? T.acc : ""; });
$("act").addEventListener("click", () => act());
$("car").addEventListener("click", () => toggleCar());
$("close").addEventListener("click", () => leave());
$("cam").addEventListener("click", () => toggleCam());
$("snd").addEventListener("click", () => { const on = sound.toggle(); $("snd").textContent = on ? "Звук" : "Тихо"; });
$("snd").textContent = sound.on ? "Звук" : "Тихо";
if (data.unity) { // owner report 31.10: admins open the Unity client from here (same frame, same origin)
  const u = document.createElement("button"); u.className = "btn"; u.type = "button"; u.textContent = "Unity";
  u.addEventListener("click", () => { location.href = data.unity; });
  document.querySelector(".tools").append(u);
}
function toggleCam() { player.cam = player.cam === "fp" ? "tp" : "fp"; $("cam").textContent = player.cam === "fp" ? "Вид" : "Вид 3"; }

// ---------------------------------------------------------------- the chrome (phase 7)
const ABIL = {
  sandevistan: { label: "Сандевистан", word: "САНДЕВИСТАН", dur: 6, slow: 0.25, tint: "rgba(60,255,170,.22)", color: "#5ce0a0" },
  kerenzikov: { label: "Керензиков", word: "КЕРЕНЗИКОВ", dur: 3, slow: 0.3, tint: "rgba(120,170,255,.22)", color: "#6ec8ff" },
  berserk: { label: "Берсерк", word: "БЕРСЕРК", dur: 6, slow: 1, run: 3, tint: "rgba(255,40,40,.22)", color: "#ff3b2f" },
};
const COOLDOWN = 45;
const active = { key: null, left: 0 }, cools = {};
for (const k of data.chrome || []) {
  const a = ABIL[k]; if (!a) continue;
  const b = document.createElement("button"); b.className = "btn"; b.type = "button"; b.textContent = a.label; b.dataset.k = k;
  b.addEventListener("click", () => useChrome(k)); $("abil").append(b); cools[k] = 0;
}
function useChrome(k) {
  const a = ABIL[k]; if (!a || active.key || cools[k] > 0) return;
  active.key = k; active.left = a.dur; cools[k] = COOLDOWN;
  const t = $("tint"); t.style.background = a.tint; t.style.opacity = "1";
  const w = $("fxword"); w.textContent = a.word; w.style.color = a.color; w.classList.remove("go"); void w.offsetWidth; w.classList.add("go");
  haptic("heavy"); if (k === "berserk") sound.tone(90, 0.6, 0.15, "sawtooth"); else sound.slowmo();
}
function chromeTick(dt) {
  for (const k of Object.keys(cools)) if (cools[k] > 0) cools[k] = Math.max(0, cools[k] - dt);
  if (active.key) { active.left -= dt; if (active.left <= 0) { active.key = null; $("tint").style.opacity = "0"; } }
  for (const b of $("abil").children) { const c = cools[b.dataset.k]; b.disabled = c > 0; b.textContent = ABIL[b.dataset.k].label + (c > 0 ? " " + Math.ceil(c) : ""); }
}
const worldScale = () => (active.key ? ABIL[active.key].slow : 1);

// ---------------------------------------------------------------- what is near and what the button does
let near = null;
function findNear() {
  if (room) {
    const b = room.built;
    let best = null, bd = 1.6;
    const dc = Math.hypot(b.counter.x - player.rx, b.counter.z - player.rz); if (dc < 1.4) { best = { kind: "counter" }; bd = dc; }
    const de = Math.hypot(b.exit.x - player.rx, b.exit.z - player.rz); if (de < bd) { best = { kind: "exit" }; bd = de; }
    for (const p of room.people) { const dd = Math.hypot(p.spr.position.x - player.rx, p.spr.position.z - player.rz); if (dd < Math.min(bd, 1.8)) { bd = dd; best = { kind: "npc", p }; } }
    for (const it of Object.values(others)) if ((it.room || "") === room.door.id) { const dd = Math.hypot(it.x - player.rx, it.z - player.rz); if (dd < Math.min(bd, 1.8)) { bd = dd; best = { kind: "player", it }; } }
    return best;
  }
  const px = player.driving ? ride.x : player.x, pz = player.driving ? ride.z : player.z;
  for (const ex of exits) { // the way out: deep inside the gap
    const along = ex.side.ax === "x" ? px : pz, across = ex.side.ax === "x" ? pz : px;
    if (along * ex.side.sg > HALF + 6 && Math.abs(across - ex.c) < S / 2) return { kind: "exit_d", ex };
  }
  let best = null, bd = 3.2;
  for (const d of doors) { const dist = Math.hypot(d.x - px, d.z - pz); if (dist < bd) { bd = dist; best = { kind: "door", d }; } }
  for (const b of beacons) { const dist = Math.hypot(b.x - px, b.z - pz); if (dist < Math.min(bd, 3.4)) { bd = dist; best = { kind: "gig", b }; } }
  if (!player.driving) {
    for (const p of people) {
      if (p.crowd || p.inside || p.down > 0 || p.gone) continue; const dist = Math.hypot(p.spr.position.x - px, p.spr.position.z - pz);
      if (dist < Math.min(bd, 2.2)) { bd = dist; best = { kind: "npc", p }; }
    }
    for (const g of gangers) { const dist = Math.hypot(g.spr.position.x - px, g.spr.position.z - pz); if (dist < Math.min(bd, 2.4)) { bd = dist; best = { kind: "gang", g }; } }
    for (const it of Object.values(others)) if (!it.room) { const dist = Math.hypot(it.x - px, it.z - pz); if (dist < Math.min(bd, 2.4)) { bd = dist; best = { kind: "player", it }; } }
  }
  return best;
}
function promptOf(n) {
  if (n.kind === "door") return [n.d.label, n.d.kind, "Войти"];
  if (n.kind === "npc") return [n.p.name, n.p.home ? n.p.home.label : "", "Подойти"];
  if (n.kind === "gig") return n.b.run ? ["Заказ идёт", "Продолжить у фиксера", "Открыть"] : ["Заказ: " + n.b.gig.title, n.b.gig.pay + " эдди", "Взять"];
  if (n.kind === "gang") return [n.g.gang.name, n.g.gang.mine ? "Твоя банда" : "Их территория", n.g.gang.mine ? "Свои" : "Драться"];
  if (n.kind === "player") return [n.it.name, "Игрок", "Подойти"];
  if (n.kind === "exit_d") return [(n.ex.home ? "Домой: " : "Поехать: ") + n.ex.name, "Такси в другой район", "Поехать"];
  if (n.kind === "counter") return [room.door.label, room.door.kind, "Открыть"];
  if (n.kind === "exit") return ["Выход", "На улицу", "Выйти"];
  return ["", "", ""];
}
function showNear() {
  const n = findNear(); near = n;
  const pr = $("prompt"), a = $("act");
  if (!n) { pr.style.display = "none"; a.style.display = "none"; }
  else {
    const [t, sub, btn] = promptOf(n);
    pr.innerHTML = ""; pr.append(t); const s = document.createElement("small"); s.textContent = sub; pr.append(s);
    pr.style.display = "block"; a.textContent = btn; a.style.display = "block";
  }
  const c = $("car");
  if (room || !ride) c.style.display = "none";
  else if (!player.driving && Math.hypot(ride.x - player.x, ride.z - player.z) < 3.5) { c.textContent = "Сесть"; c.style.display = "block"; }
  else if (player.driving) { c.textContent = "Пешком"; c.style.display = "block"; }
  else c.style.display = "none";
  sound.update({ rain: RAINY, inside: !!room, driving: player.driving, speed: ride ? ride.v : 0, siren: chase.on || (PURGE && !room), sirenDist: nearestCop(),
    hum: !!(n && (n.kind === "door" || n.kind === "counter")) });
}
function press(arg) { // the window of the player is on the city screen: «city:d3:<door>» (handlers/city.py)
  haptic("medium"); sound.tone(660, 0.08, 0.05);
  if (!EMBED) { toast("Двери открываются в приложении игры: открой район из Mini App.", 2600); return; }
  saveSpot();
  window.parent.postMessage({ nc: "press", screen: "city", action: "d3", arg: String(arg) }, location.origin);
}
function act() {
  sound.start();
  if (!near) return;
  const n = near;
  if (n.kind === "door") { if (roomOf(n.d)) enterRoom(n.d); else press(n.d.id); }
  else if (n.kind === "npc") press(n.p.demo ? n.p.home.id : "n." + n.p.id);
  else if (n.kind === "gig") press(n.b.run ? "gigs" : "gig." + n.b.gig.id);
  else if (n.kind === "gang") { if (n.g.gang.mine) toast("Свои кивают: «Чумба, на районе тихо».", 2000); else press("gang"); }
  else if (n.kind === "player") press("pl." + n.it.c);
  else if (n.kind === "counter") press(room.door.id);
  else if (n.kind === "exit") leaveRoom();
  else if (n.kind === "exit_d") {
    try { localStorage.setItem("nc3d.arrive", JSON.stringify({ from: data.district, t: Date.now(), drv: player.driving })); localStorage.removeItem(SPOT_KEY); } catch (e) {}
    if (!EMBED) { toast("Поездки работают в приложении игры.", 2400); return; }
    haptic("medium"); window.parent.postMessage({ nc: "press", screen: "city", action: "d3", arg: "trip." + n.ex.key }, location.origin);
  }
}
function toggleCar() {
  if (!ride || room) return;
  if (player.driving) {
    const sx = ride.x + Math.cos(ride.yaw) * 2.2, sz = ride.z - Math.sin(ride.yaw) * 2.2;
    player.x = blocked(sx, sz, 0.4) ? ride.x : sx; player.z = blocked(sx, sz, 0.4) ? ride.z + 2.5 : sz; player.driving = false; player.yaw = ride.yaw; ride.v = 0;
  } else if (Math.hypot(ride.x - player.x, ride.z - player.z) < 3.5) { player.driving = true; haptic("heavy"); }
}
function leave() {
  if (EMBED) { try { window.parent.postMessage({ nc: "close" }, location.origin); return; } catch (e) {} }
  history.length > 1 ? history.back() : window.close();
}

// ---------------------------------------------------------------- hits: the ride or a berserk knocks people over
let hitToastT = 0;
function knock(p, sp, fx, fz, onFoot) {
  const push = Math.min(5, 1 + sp * 0.2);
  const nx = p.spr.position.x + fx * push, nz = p.spr.position.z + fz * push;
  if (!blocked(nx, nz, 0.2)) { p.spr.position.x = nx; p.spr.position.z = nz; }
  p.spr.material.rotation = Math.PI / 2 * (Math.random() > 0.5 ? 1 : -1); p.spr.position.y = 0.35;
  p.down = p.crowd ? 5 + Math.random() * 3 : 4;
  if (ride && !onFoot) ride.v *= 0.7;
  haptic("heavy"); sound.hit();
  if (p.crowd) {
    if (performance.now() - hitToastT > 4000) { hitToastT = performance.now(); toast(onFoot ? "Прохожий отлетает от удара плечом." : ["Прохожий кувыркается через капот.", "Кто-то отлетает на тротуар и матерится.", "Глухой удар. Прохожий катится по асфальту."][Math.floor(Math.random() * 3)], 1800); }
    return;
  }
  if (!TOKEN || p.demo) { toast(p.name + (onFoot ? " летит на асфальт." : " летит через капот."), 2000); if (sp >= 9 && !onFoot) p.gone = true; return; }
  post("/api/city3d/hit", { n: p.id, s: onFoot ? 5 : Math.round(sp * 10) / 10 }).then((d) => {
    if (!d || !d.ok) return;
    if (d.status === "hurt") {
      p.gone = true;
      if (p.down <= 0 || p.down > 1e8) { p.spr.visible = true; p.spr.material.rotation = Math.PI / 2; p.spr.position.y = 0.35; }
      p.down = 6;
    }
    if (d.line) toast(d.line, 3200);
  });
}
let crashT = 0;
function crashed(sp) { // phase 9: a wall at speed damages the ride
  sound.crash(); haptic("heavy");
  if (sp < 10 || performance.now() - crashT < 4000) return;
  crashT = performance.now();
  if (!TOKEN || data.demo) { toast("Удар о стену.", 1500); return; }
  post("/api/city3d/crash", { s: Math.round(sp * 10) / 10 }).then((d) => { if (d && d.line) { toast(d.line, 2200); if (ride) ride.dmg = d.dmg; } });
}

// ---------------------------------------------------------------- the police: patrol, chase, caught, lost (phase 2)
const onStreet = (v) => { const k = Math.round((v + HALF - S / 2) / (B + S)); return { k, c: streetC(clamp(k, 0, N)) }; };
function nearestCop() { let m = 999; const px = player.driving ? ride.x : player.x, pz = player.driving ? ride.z : player.z; for (const c of cops) m = Math.min(m, Math.hypot(c.x - px, c.z - pz)); return m; }
function copsTick(dt) {
  if (!cops.length) return;
  const tx = room ? room.door.x : player.driving ? ride.x : player.x, tz = room ? room.door.z : player.driving ? ride.z : player.z;
  const ws = worldScale();
  chase.cool = Math.max(0, chase.cool - dt);
  let minD = 999;
  for (const c of cops) {
    const d = Math.hypot(tx - c.x, tz - c.z); minD = Math.min(minD, d);
    if (!chase.on && !chase.done && chase.cool <= 0 && !room && d < 24) {
      chase.on = true; chase.lostT = 0; chase.caughtT = 0; updateHeat();
      toast(MAXTAC ? "МаксТак засёк тебя. Беги." : "Патруль NCPD тебя заметил!", 2200); haptic("heavy");
    }
    let sp = (chase.on ? (MAXTAC ? 19 : 15) : 8) * ws * dt;
    if (chase.on && d < 12 && !room) { // close: straight at the target
      const ux = (tx - c.x) / (d || 1), uz = (tz - c.z) / (d || 1);
      if (!blocked(c.x + ux * sp, c.z + uz * sp, 1.0)) { c.x += ux * sp; c.z += uz * sp; c.yaw = Math.atan2(-ux, -uz); }
      else if (!blocked(c.x + ux * sp, c.z, 1.0)) c.x += ux * sp; else if (!blocked(c.x, c.z + uz * sp, 1.0)) c.z += uz * sp;
    } else { // along the streets: at a crossroads turn toward the target (or keep patrolling)
      const sx = onStreet(c.x), sz = onStreet(c.z);
      const atCross = Math.abs(c.x - sx.c) < 1.2 && Math.abs(c.z - sz.c) < 1.2;
      if (atCross && chase.on && !c.turned) {
        const dx = tx - c.x, dz = tz - c.z;
        if (Math.abs(dx) > Math.abs(dz)) { c.axis = "x"; c.dir = Math.sign(dx) || 1; c.z = sz.c; } else { c.axis = "z"; c.dir = Math.sign(dz) || 1; c.x = sx.c; }
        c.turned = true;
      } else if (!atCross) c.turned = false;
      if (c.axis === "x") c.x += c.dir * sp; else c.z += c.dir * sp;
      const lim = HALF - S / 2;
      if (c.x > lim || c.x < -lim) { c.x = clamp(c.x, -lim, lim); c.dir = c.axis === "x" ? -c.dir : c.dir; }
      if (c.z > lim || c.z < -lim) { c.z = clamp(c.z, -lim, lim); c.dir = c.axis === "z" ? -c.dir : c.dir; }
      c.yaw = c.axis === "x" ? (c.dir > 0 ? -Math.PI / 2 : Math.PI / 2) : (c.dir > 0 ? Math.PI : 0);
    }
    c.m.position.set(c.x, c.m.userData.hover ? 1.2 : 0, c.z); c.m.rotation.y = c.yaw + Math.PI;
    const flip = Math.floor(performance.now() / 180) % 2 === 0;
    c.red.visible = !chase.on || flip; c.blue.visible = !chase.on || !flip;
  }
  if (!chase.on) return;
  if (minD < 2.6 && !room) {
    chase.caughtT += dt;
    if (chase.caughtT > 1.2) { chase.on = false; chase.done = true; updateHeat(); toast("Тебя прижали к асфальту.", 1600); press("cops"); }
  } else chase.caughtT = 0;
  if (minD > 70 || room) {
    chase.lostT += dt;
    if (chase.lostT > 12) {
      chase.on = false; chase.cool = 25; updateHeat();
      if (!TOKEN || data.demo) toast("Ушёл от погони.", 2000);
      else post("/api/city3d/escape", {}).then((d) => { if (d && d.line) toast(d.line, 2600); if (d && d.heat != null) { data.heat = d.heat; updateHeat(); } });
    }
  } else chase.lostT = 0;
}

// ---------------------------------------------------------------- the Blackwall (phase 6)
let glitchT = BW > 0 ? 4 + Math.random() * 6 : 1e9;
function glitch() {
  const g = $("glitch"); g.innerHTML = ""; g.style.display = "block";
  const n = 3 + Math.floor(BW * 6);
  for (let k = 0; k < n; k++) {
    const el = document.createElement(Math.random() > 0.5 ? "i" : "b");
    el.style.top = Math.random() * 100 + "%"; el.style.height = 2 + Math.random() * 14 + "px";
    el.style.transform = "translateX(" + (Math.random() * 40 - 20) + "px)"; g.append(el);
  }
  if (Math.random() < 0.3 + BW * 0.3) document.body.classList.add("bwflash");
  sound.glitch();
  setTimeout(() => { g.style.display = "none"; document.body.classList.remove("bwflash"); }, 120 + Math.random() * 200);
}

// ---------------------------------------------------------------- minimap
const mini = $("mini").getContext("2d");
function drawMini() {
  const W = 92;
  mini.clearRect(0, 0, W, W);
  if (room) { mini.fillStyle = "rgba(255,255,255,.15)"; mini.fillRect(16, 26, 60, 40); mini.fillStyle = T.acc2; mini.fillRect(16 + (player.rx + ROOM_W / 2) / ROOM_W * 60 - 2, 26 + (player.rz + ROOM_D / 2) / ROOM_D * 40 - 2, 4, 4); return; }
  const s = 0.62, px = player.driving ? ride.x : player.x, pz = player.driving ? ride.z : player.z, R2 = W / 2 / s + 30;
  const mx = (x) => (x - px) * s + W / 2, mz = (z) => (z - pz) * s + W / 2, edge = (v) => clamp(v, 3, W - 6);
  mini.fillStyle = "rgba(255,255,255,.12)";
  for (const b of solids) if (b.x1 > px - R2 && b.x0 < px + R2 && b.z1 > pz - R2 && b.z0 < pz + R2) mini.fillRect(mx(b.x0), mz(b.z0), (b.x1 - b.x0) * s, (b.z1 - b.z0) * s);
  for (const d of doors) { mini.fillStyle = d.color; mini.fillRect(edge(mx(d.x) - 1.5), edge(mz(d.z) - 1.5), 3, 3); }
  for (const b of beacons) { mini.fillStyle = "#ffd23f"; mini.fillRect(edge(mx(b.x) - 2), edge(mz(b.z) - 2), 4, 4); }
  for (const ex of exits) { const x = ex.side.ax === "x" ? ex.side.sg * (HALF + 8) : ex.c, z = ex.side.ax === "x" ? ex.c : ex.side.sg * (HALF + 8); mini.fillStyle = "#5ce0a0"; mini.fillRect(edge(mx(x) - 2), edge(mz(z) - 2), 4, 4); }
  for (const g of gangers) { const x = mx(g.spr.position.x), z = mz(g.spr.position.z); if (x > 0 && x < W && z > 0 && z < W) { mini.fillStyle = g.gang.color; mini.fillRect(x - 1, z - 1, 2, 2); } }
  for (const it of Object.values(others)) if (!it.room) { mini.fillStyle = "#fff"; mini.fillRect(edge(mx(it.x) - 2), edge(mz(it.z) - 2), 4, 4); }
  const flip = Math.floor(performance.now() / 250) % 2;
  for (const c of cops) { const x = mx(c.x), z = mz(c.z); if (x > 0 && x < W && z > 0 && z < W) { mini.fillStyle = flip ? "#ff2030" : "#3060ff"; mini.fillRect(x - 2, z - 2, 4, 4); } }
  if (ride && !player.driving) { mini.fillStyle = "#fff"; mini.fillRect(mx(ride.x) - 1.5, mz(ride.z) - 1, 3, 2); }
  const yaw = player.driving ? ride.yaw : player.yaw; const x = W / 2, y = W / 2;
  mini.fillStyle = T.acc2; mini.beginPath(); mini.moveTo(x - Math.sin(yaw) * 5, y - Math.cos(yaw) * 5);
  mini.lineTo(x + Math.cos(yaw) * 3, y - Math.sin(yaw) * 3); mini.lineTo(x - Math.cos(yaw) * 3, y + Math.sin(yaw) * 3); mini.fill();
}

// ---------------------------------------------------------------- the loop
let last = performance.now(), nearT = 0, stepT = 0;
window.NC3D = { player, get ride() { return ride; }, doors, people, gangers, beacons, cops, chase, exits, others, get room() { return room; },
  blocked: (x, z, r) => blocked(x, z, r), enterRoom, leaveRoom, useChrome, glitch, HALF, streetC };
function frame(now) {
  const dt = Math.min(0.05, (now - last) / 1000); last = now;
  const ws = worldScale(), wdt = dt * ws;
  let fwd = (keys.KeyW || keys.ArrowUp ? 1 : 0) - (keys.KeyS || keys.ArrowDown ? 1 : 0) - stick.y;
  let side = (keys.KeyD || keys.ArrowRight ? 1 : 0) - (keys.KeyA || keys.ArrowLeft ? 1 : 0) + stick.x;
  fwd = clamp(fwd, -1, 1); side = clamp(side, -1, 1);
  const berserk = active.key === "berserk";
  if (room) { // inside a room: on foot, its own little world
    const sp = (player.run || keys.ShiftLeft ? 5 : 3) * dt * (berserk ? 2 : 1);
    const dx = (-Math.sin(player.ryaw) * fwd + Math.cos(player.ryaw) * side) * sp, dz = (-Math.cos(player.ryaw) * fwd - Math.sin(player.ryaw) * side) * sp;
    if (!roomBlocked(player.rx + dx, player.rz, 0.3)) player.rx += dx;
    if (!roomBlocked(player.rx, player.rz + dz, 0.3)) player.rz += dz;
    if (fwd || side) { stepT -= dt; if (stepT <= 0) { sound.step(); stepT = 0.45; } }
    if (player.cam === "tp") {
      meSpr.visible = true; meSpr.position.set(player.rx, 0.93, player.rz);
      camera.position.set(player.rx + Math.sin(player.ryaw) * 3, 2.3, player.rz + Math.cos(player.ryaw) * 3);
      camera.lookAt(player.rx - Math.sin(player.ryaw) * 2, 1.4, player.rz - Math.cos(player.ryaw) * 2);
    } else {
      meSpr.visible = false; camera.position.set(player.rx, 1.62, player.rz); camera.rotation.set(player.pitch, player.ryaw, 0, "YXZ");
    }
    room.built.ring.rotation.z += dt;
  } else if (player.driving) {
    ride.v += fwd * dt * (ride.max * ride.accel); ride.v *= 1 - dt * 0.6; ride.v = clamp(ride.v, -ride.max * 0.3, ride.max);
    ride.yaw -= side * dt * 1.8 * Math.min(1, Math.abs(ride.v) / 6) * Math.sign(ride.v || 1);
    const nx = ride.x - Math.sin(ride.yaw) * ride.v * dt, nz = ride.z - Math.cos(ride.yaw) * ride.v * dt;
    if (!blocked(nx, nz, ride.kind === "truck" ? 1.5 : 1.2)) { ride.x = nx; ride.z = nz; }
    else { const sp = Math.abs(ride.v); if (sp > 5) crashed(sp); ride.v *= -0.3; }
    ride.m.position.set(ride.x, rideY(), ride.z); ride.m.rotation.y = ride.yaw + Math.PI;
    const back = ride.kind === "truck" ? 8.5 : 7, up = ride.kind === "truck" ? 4 : 3.4;
    camera.position.set(ride.x + Math.sin(ride.yaw) * back, up, ride.z + Math.cos(ride.yaw) * back);
    camera.lookAt(ride.x - Math.sin(ride.yaw) * 6, 1.2, ride.z - Math.cos(ride.yaw) * 6);
    meSpr.visible = false;
  } else {
    const sp = (player.run || keys.ShiftLeft ? 8 : 4.2) * dt * (berserk ? ABIL.berserk.run : 1);
    const dx = (-Math.sin(player.yaw) * fwd + Math.cos(player.yaw) * side) * sp, dz = (-Math.cos(player.yaw) * fwd - Math.sin(player.yaw) * side) * sp;
    if (!blocked(player.x + dx, player.z, 0.4)) player.x += dx;
    if (!blocked(player.x, player.z + dz, 0.4)) player.z += dz;
    if (fwd || side) { stepT -= dt; if (stepT <= 0) { sound.step(); stepT = player.run || berserk ? 0.28 : 0.45; } }
    if (player.cam === "tp") {
      meSpr.visible = true; meSpr.position.set(player.x, 0.93, player.z);
      camera.position.set(player.x + Math.sin(player.yaw) * 3.4, 2.2, player.z + Math.cos(player.yaw) * 3.4);
      camera.lookAt(player.x - Math.sin(player.yaw) * 2, 1.5, player.z - Math.cos(player.yaw) * 2);
    } else {
      meSpr.visible = false;
      const bob = (fwd || side) ? Math.sin(now / 110) * 0.05 : 0;
      camera.position.set(player.x, 1.62 + bob, player.z);
      camera.rotation.set(player.pitch, player.yaw, 0, "YXZ");
    }
  }
  if (!room) {
    // people walk; the ride (or a berserk on foot) knocks them over
    const fast = player.driving && Math.abs(ride.v) > 3, hitR = ride && ride.bike ? 1.0 : 1.6;
    const fx = player.driving ? -Math.sin(ride.yaw) * Math.sign(ride.v) : -Math.sin(player.yaw), fz = player.driving ? -Math.cos(ride.yaw) * Math.sign(ride.v) : -Math.cos(player.yaw);
    const hx = player.driving ? ride.x : player.x, hz = player.driving ? ride.z : player.z;
    const bump = berserk && !player.driving && (fwd || side);
    for (const p of people) {
      if (p.inside) continue;
      if (p.down > 0) {
        p.down -= wdt;
        if (p.down <= 0) { if (p.gone) { p.spr.visible = false; p.down = 1e9; } else { p.spr.material.rotation = 0; p.spr.position.y = 0.9; } }
        continue;
      }
      if ((fast && Math.abs(p.spr.position.x - hx) < hitR && Math.abs(p.spr.position.z - hz) < hitR)
        || (bump && Math.abs(p.spr.position.x - hx) < 0.9 && Math.abs(p.spr.position.z - hz) < 0.9)) { knock(p, fast ? Math.abs(ride.v) : 6, fx, fz, !fast); continue; }
      if (p.crowd) {
        p.spr.position[p.axis] += p.dir * p.speed * wdt;
        if (Math.abs(p.spr.position[p.axis]) > HALF + 8) p.dir *= -1;
        continue;
      }
      p.wait -= wdt;
      if (p.wait <= 0) { const a = Math.random() * Math.PI * 2, r = 1.5 + Math.random() * 3.5; p.tx = p.home.x + Math.cos(a) * r; p.tz = p.home.z + Math.sin(a) * r; p.wait = 3 + Math.random() * 4; }
      const dx = p.tx - p.spr.position.x, dz = p.tz - p.spr.position.z, len = Math.hypot(dx, dz);
      if (len > 0.1 && !blocked(p.spr.position.x + dx / len * wdt, p.spr.position.z + dz / len * wdt, 0.3)) {
        p.spr.position.x += dx / len * wdt * 0.9; p.spr.position.z += dz / len * wdt * 0.9;
      }
    }
    for (const g of gangers) { g.ph += wdt; g.spr.position.x = g.bx + Math.sin(g.ph * 0.7) * 0.25; g.spr.position.z = g.bz + Math.cos(g.ph * 0.5) * 0.25; }
    for (const t of traffic) {
      const v = t.speed * wdt * t.dir; t.c.position[t.axis] += v;
      if (t.c.position[t.axis] > HALF + 30) t.c.position[t.axis] = -HALF - 30; if (t.c.position[t.axis] < -HALF - 30) t.c.position[t.axis] = HALF + 30;
    }
    if (RAINY) { // rain follows the camera
      const pos = rainGeo.attributes.position.array;
      for (let i = 0; i < RAIN; i++) { pos[i * 3 + 1] -= 22 * wdt; if (pos[i * 3 + 1] < 0) { pos[i * 3 + 1] = 28; pos[i * 3] = camera.position.x + (Math.random() - 0.5) * 80; pos[i * 3 + 2] = camera.position.z + (Math.random() - 0.5) * 80; } }
      rainGeo.attributes.position.needsUpdate = true;
    }
    for (let i = 0; i < blinkers.length; i++) blinkers[i].visible = Math.sin(now / 400 + i * 1.7) > -0.92;
    for (const m of spinners) { m.rotation.y += dt * 2; m.position.y = 3.6 + Math.sin(now / 400) * 0.3; }
    for (const p of puffs) { p.t = (p.t + wdt * 0.15) % 1; p.s.position.set(p.x + p.t * 3, p.y + p.t * 10, p.z); p.s.material.opacity = 0.7 * (1 - p.t); p.s.scale.setScalar(2 + p.t * 5); }
    for (let i = 0; i < fires.length; i++) { const f = fires[i]; const k = 1 + Math.sin(now / 90 + i) * 0.12 + Math.random() * 0.08; f.scale.y = f.userData.h || (f.userData.h = f.scale.y); f.scale.y = f.userData.h * k; }
    for (const c of cubes) { c.rotation.x += dt * 0.2; c.rotation.y += dt * 0.3; }
  }
  copsTick(dt);
  chromeTick(dt);
  placeOthers(dt);
  if (BW > 0) { glitchT -= dt; if (glitchT <= 0) { glitch(); glitchT = (3 + Math.random() * 10) / (0.4 + BW); } }
  nearT -= dt; if (nearT <= 0) { showNear(); drawMini(); nearT = 0.15; }
  renderer.render(room ? room.built.scene : scene, camera);
  requestAnimationFrame(frame);
}
if (EMBED) $("close").style.display = "none"; // the app has its own «← Найт-Сити» above the frame
$("load").remove();
if (PURGE) toast("Судная ночь. Закона нет до утра.", 3000);
else if (BW >= 0.6) toast("Чёрный заслон близко: район трещит.", 2600);
if (!data.doors.length) toast("Здесь пока пусто.", 2000);
requestAnimationFrame(frame);
