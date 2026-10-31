// The rooms behind the doors of the 3D district (docs/PLAN_CITY3D_2.md, phase 10). Each room is its own small scene:
// a box with a floor, walls and the furniture of its kind, a counter where the place's own screen opens, and the exit.
// Procedural boxes only, so a room costs a few draw calls.
export const ROOM_W = 16, ROOM_D = 12, ROOM_H = 4.5;

export function hasRoom(kind) {
  return ["bar", "club", "market", "clinic", "precinct", "corp_lobby", "turf", "gigs", "ripper", "work", "property",
    "arena", "arcade"].includes(kind);
}

// kinds of rooms: the floor, the walls, the light, what stands where
const LOOK = {
  bar: { floor: "#2a1a12", wall: "#1c1416", light: "#ffb36b", neon: "#ff3e8a" },
  club: { floor: "#120a1e", wall: "#0c0814", light: "#b26bff", neon: "#4de3ff" },
  market: { floor: "#24221c", wall: "#1a1814", light: "#ffd27a", neon: "#5ce0a0" },
  clinic: { floor: "#1c2426", wall: "#182024", light: "#bff7ff", neon: "#5ce0a0" },
  ripper: { floor: "#1c2426", wall: "#141c20", light: "#9fefff", neon: "#4de3ff" },
  precinct: { floor: "#20242c", wall: "#1a1e26", light: "#d8e4ff", neon: "#2b4cff" },
  corp_lobby: { floor: "#3a3c44", wall: "#24262e", light: "#ffffff", neon: "#6ec8ff" },
  turf: { floor: "#1e1a16", wall: "#191512", light: "#ff8a3d", neon: "#e0262b" },
  gigs: { floor: "#21170f", wall: "#17110d", light: "#ffcf8a", neon: "#ffb547" },
  work: { floor: "#22252a", wall: "#1b1e23", light: "#e8f0ff", neon: "#5aa9ff" },
  property: { floor: "#231c1a", wall: "#1b1617", light: "#ffc89a", neon: "#ff7eb6" },
  arena: { floor: "#1e1410", wall: "#140e0c", light: "#ffdca0", neon: "#ff3b2f" },
  arcade: { floor: "#0e0b18", wall: "#0b0914", light: "#c08aff", neon: "#ff5fb4" },
};

export function buildRoom(THREE, kind, label, h) {
  const L = LOOK[kind] || LOOK.bar;
  const scene = new THREE.Scene();
  scene.background = new THREE.Color("#050408");
  scene.fog = new THREE.Fog("#050408", 10, 30);
  scene.add(new THREE.HemisphereLight(0xffffff, 0x303030, 1.4));
  const lamp = new THREE.PointLight(new THREE.Color(L.light), 28, 0, 1.3); lamp.position.set(0, ROOM_H - 0.4, 0); scene.add(lamp);
  const neonL = new THREE.PointLight(new THREE.Color(L.neon), 18, 0, 1.5); neonL.position.set(0, 2.6, -ROOM_D / 2 + 1); scene.add(neonL);
  const solids = [];
  const box = (w, hh, d, x, y, z, color, opts) => {
    const mat = opts && opts.emissive ? new THREE.MeshBasicMaterial({ color }) : new THREE.MeshLambertMaterial({ color, map: opts && opts.map || null });
    const m = new THREE.Mesh(new THREE.BoxGeometry(w, hh, d), mat); m.position.set(x, y, z); scene.add(m);
    if (!opts || opts.solid !== false) solids.push({ x0: x - w / 2, x1: x + w / 2, z0: z - d / 2, z1: z + d / 2 });
    return m;
  };
  // the shell: floor, ceiling, walls with panels
  const floorTex = h.canvasTex(32, 32, (g, w, hh) => {
    g.fillStyle = L.floor; g.fillRect(0, 0, w, hh);
    g.strokeStyle = "rgba(255,255,255,.05)"; for (let i = 0; i < w; i += 8) { g.strokeRect(i, 0, 8, hh); g.strokeRect(0, i, w, 8); }
  }, true);
  floorTex.repeat.set(ROOM_W / 4, ROOM_D / 4);
  const floor = new THREE.Mesh(new THREE.PlaneGeometry(ROOM_W, ROOM_D), new THREE.MeshLambertMaterial({ map: floorTex }));
  floor.rotation.x = -Math.PI / 2; scene.add(floor);
  const ceil = new THREE.Mesh(new THREE.PlaneGeometry(ROOM_W, ROOM_D), new THREE.MeshLambertMaterial({ color: 0x0a0a0e }));
  ceil.rotation.x = Math.PI / 2; ceil.position.y = ROOM_H; scene.add(ceil);
  const wallTex = h.canvasTex(32, 32, (g, w, hh) => {
    g.fillStyle = L.wall; g.fillRect(0, 0, w, hh); g.fillStyle = "rgba(255,255,255,.04)"; g.fillRect(0, 0, w, 2); g.fillRect(0, 0, 2, hh);
  }, true);
  wallTex.repeat.set(4, 1.5);
  const wallMat = new THREE.MeshLambertMaterial({ map: wallTex });
  for (const [w, d, x, z] of [[ROOM_W, 0.3, 0, -ROOM_D / 2], [ROOM_W, 0.3, 0, ROOM_D / 2], [0.3, ROOM_D, -ROOM_W / 2, 0], [0.3, ROOM_D, ROOM_W / 2, 0]]) {
    const m = new THREE.Mesh(new THREE.BoxGeometry(w, ROOM_H, d), wallMat); m.position.set(x, ROOM_H / 2, z); scene.add(m);
  }
  // a neon strip around the ceiling
  for (const [w, d, x, z] of [[ROOM_W - 0.4, 0.06, 0, -ROOM_D / 2 + 0.2], [ROOM_W - 0.4, 0.06, 0, ROOM_D / 2 - 0.2]]) box(w, 0.08, d, x, ROOM_H - 0.3, z, L.neon, { emissive: true, solid: false });
  // the exit: the door we came through, in the front wall
  box(2.4, 3.1, 0.1, 0, 1.55, ROOM_D / 2 - 0.12, "#0a0a0f", { emissive: true, solid: false });
  box(2.8, 0.15, 0.12, 0, 3.2, ROOM_D / 2 - 0.12, L.neon, { emissive: true, solid: false });
  const exitSign = new THREE.Mesh(new THREE.PlaneGeometry(1.6, 0.4), new THREE.MeshBasicMaterial({ map: h.signTex("ВЫХОД", "#5ce0a0") }));
  exitSign.position.set(0, 3.6, ROOM_D / 2 - 0.2); exitSign.rotation.y = Math.PI; scene.add(exitSign);
  // the name of the place over the back wall
  const sign = new THREE.Mesh(new THREE.PlaneGeometry(6, 1.5), new THREE.MeshBasicMaterial({ map: h.signTex(label, L.neon) }));
  sign.position.set(0, 3.3, -ROOM_D / 2 + 0.2); scene.add(sign);

  const spots = []; // where the people of the place stand
  let counter = { x: 0, z: -3.3 };
  const R = h.rng(h.hash(kind + label));
  if (kind === "bar" || kind === "club") {
    box(10, 1.1, 0.9, 0, 0.55, -4.3, "#3a2016");
    box(10, 0.06, 0.95, 0, 1.12, -4.3, L.neon, { emissive: true, solid: false });
    for (let i = 0; i < 9; i++) box(0.6, 0.12, 0.25, -4 + i, 2.0 + (i % 3) * 0.35, -5.7, ["#5ce0a0", "#ffb547", "#ff3e8a", "#4de3ff"][i % 4], { emissive: true, solid: false }); // bottles
    for (let i = 0; i < 6; i++) box(0.45, 0.8, 0.45, -3.75 + i * 1.5, 0.4, -3.2, "#222228");
    counter = { x: 0, z: -2.6 };
    if (kind === "club") {
      const tiles = h.canvasTex(32, 32, (g) => { for (let y = 0; y < 4; y++) for (let x = 0; x < 4; x++) { g.fillStyle = ["#ff3e8a", "#4de3ff", "#b26bff", "#5ce0a0"][(x + y) % 4]; g.fillRect(x * 8 + 1, y * 8 + 1, 6, 6); } }, true);
      tiles.repeat.set(2, 2);
      const dance = new THREE.Mesh(new THREE.PlaneGeometry(7, 5), new THREE.MeshBasicMaterial({ map: tiles, transparent: true, opacity: 0.55 }));
      dance.rotation.x = -Math.PI / 2; dance.position.set(0, 0.02, 2); scene.add(dance);
    }
    spots.push([-3, -2.4], [2.5, -2.4], [-5, 1], [4.5, 2], [0, 1.5], [-2, 3]);
  } else if (kind === "market") {
    for (const [x, z] of [[-5, -3], [0, -3], [5, -3], [-5, 1.5], [5, 1.5]]) {
      box(3, 0.9, 1.4, x, 0.45, z, "#4a3a24");
      box(3.4, 0.08, 1.8, x, 2.4, z, ["#d0402a", "#2aa86a", "#d0a02a", "#2a6ad0"][Math.floor(R() * 4)], { solid: false });
      for (let k = 0; k < 4; k++) box(0.4, 0.3, 0.4, x - 1 + k * 0.65, 1.05, z, ["#e05050", "#e0c040", "#50c060", "#e08030"][k], { solid: false });
    }
    counter = { x: 0, z: -1.6 };
    spots.push([-5, -1.6], [5, -1.6], [-2.5, 0], [2.5, 0.5], [0, 3]);
  } else if (kind === "clinic" || kind === "ripper") {
    box(2.2, 0.7, 0.9, 0, 0.6, -3.6, "#3a4448"); box(0.9, 1.0, 0.25, 0, 1.2, -4.0, "#3a4448", { solid: false }); // the chair
    for (const x of [-2.5, 2.5]) { box(1.4, 0.9, 0.12, x, 2.2, -5.6, "#081a10", { solid: false }); box(1.2, 0.7, 0.05, x, 2.2, -5.52, "#2aff8a", { emissive: true, solid: false }); }
    box(1.2, 2.2, 0.6, -6.6, 1.1, -3, "#5a646a"); box(1.2, 2.2, 0.6, 6.6, 1.1, -3, "#5a646a");
    box(0.12, 3.2, 0.12, 1.4, 1.6, -3.6, "#888", { solid: false }); box(1.6, 0.3, 0.6, 0.8, 3.1, -3.6, "#cfefff", { emissive: true, solid: false }); // the lamp arm
    counter = { x: 0, z: -2.4 };
    spots.push([-2.2, -2.6], [2.4, -2.2], [-5, 2], [5, 2.5]);
  } else if (kind === "precinct" || kind === "work") {
    for (const [x, z] of [[-4.5, -2.5], [0, -2.5], [4.5, -2.5], [-4.5, 1.5], [4.5, 1.5]]) {
      box(2.4, 0.8, 1.2, x, 0.4, z, kind === "precinct" ? "#2c3240" : "#30343c");
      box(0.9, 0.6, 0.06, x, 1.15, z - 0.3, "#0a1626", { solid: false }); box(0.8, 0.5, 0.02, x, 1.15, z - 0.26, "#5aa9ff", { emissive: true, solid: false });
    }
    if (kind === "precinct") for (let i = 0; i < 8; i++) box(0.08, 2.4, 0.08, -7 + i * 0.4, 1.2, -5.5, "#888", { solid: false }); // the cell bars
    counter = { x: 0, z: -1.2 };
    spots.push([-4.5, -1.3], [4.5, -1.3], [-1.5, 0.5], [2, 3], [-5, 3.5]);
  } else if (kind === "corp_lobby") {
    box(6, 1.1, 1.2, 0, 0.55, -3.8, "#e8ecf0"); box(6, 0.05, 1.25, 0, 1.12, -3.8, "#6ec8ff", { emissive: true, solid: false });
    for (let i = 0; i < 5; i++) box(0.25, 1.0, 1.2, -4 + i * 2, 0.5, 0.5, "#9aa4b0");
    box(4, 2.2, 0.05, 0, 2.4, -5.8, "#6ec8ff", { emissive: true, solid: false });
    counter = { x: 0, z: -2.5 };
    spots.push([-2, -2.6], [2, -2.6], [-6, 2], [6, 2], [0, 3.5]);
  } else if (kind === "turf") {
    for (let i = 0; i < 9; i++) box(1.1, 1.1, 1.1, -6 + R() * 12, 0.55 + (i % 3 === 0 ? 1.1 : 0), -5 + R() * 2.5, "#4a3a22");
    box(0.8, 1, 0.8, 3, 0.5, 1, "#3a3a3a"); box(0.6, 0.4, 0.6, 3, 1.2, 1, "#ff7a2a", { emissive: true, solid: false }); // the barrel fire
    const tag = new THREE.Mesh(new THREE.PlaneGeometry(5, 2), new THREE.MeshBasicMaterial({ map: h.tagTex(L.neon, h.hash(label)), transparent: true }));
    tag.position.set(-7.8, 2, 0); tag.rotation.y = Math.PI / 2; scene.add(tag);
    counter = { x: 0, z: -2.2 };
    spots.push([-3, -1.5], [2, -1], [4.5, 2.5], [-4, 2.5], [0, 1]);
  } else if (kind === "gigs") {
    box(3.6, 0.8, 1.6, 0, 0.4, -3.5, "#3a2412"); box(0.5, 0.06, 0.5, 0.8, 0.83, -3.5, "#ffcf8a", { emissive: true, solid: false });
    box(4, 0.7, 1.2, -5, 0.35, 3, "#5a1a1a"); box(4, 1.0, 0.3, -5, 0.9, 3.55, "#5a1a1a", { solid: false }); // the couch
    box(1.4, 2.2, 0.5, 6.5, 1.1, -4.5, "#2a2018"); // the safe
    counter = { x: 0, z: -2.2 };
    spots.push([0, -4.6], [-5, 2], [4, 1]);
  } else if (kind === "property") {
    box(3, 0.6, 2.2, -5, 0.3, -3.8, "#3a2a40"); box(3, 0.2, 0.6, -5, 0.7, -4.7, "#e0d0e8", { solid: false }); // the bed
    box(3.4, 0.7, 1.1, 3, 0.35, 2.5, "#30303a"); // the couch
    box(3, 1.7, 0.1, 3, 1.8, -5.8, "#0a0a14", { solid: false }); box(2.8, 1.5, 0.05, 3, 1.8, -5.74, "#4de3ff", { emissive: true, solid: false }); // the screen
    const win = new THREE.Mesh(new THREE.PlaneGeometry(5, 2.2), new THREE.MeshBasicMaterial({ map: h.skylineTex() }));
    win.position.set(7.82, 2.2, -1); win.rotation.y = -Math.PI / 2; scene.add(win);
    counter = { x: 3, z: -4.2 };
    spots.push([-2, 0], [1, 3]);
  } else if (kind === "arena") {
    box(7, 0.6, 7, 0, 0.3, -1.5, "#7a2a20");
    for (const [w, d, x, z] of [[7, 0.06, 0, -5], [7, 0.06, 0, 2], [0.06, 7, -3.5, -1.5], [0.06, 7, 3.5, -1.5]])
      for (const y of [1.1, 1.5, 1.9]) box(w, 0.05, d, x, y, z, "#ffd23f", { emissive: true, solid: false });
    for (const [x, z] of [[-3.5, -5], [3.5, -5], [-3.5, 2], [3.5, 2]]) box(0.2, 2, 0.2, x, 1, z, "#ccc", { solid: false });
    counter = { x: 0, z: 3.3 };
    spots.push([-5.8, -3], [5.8, -3], [-6, 3], [6, 3.5], [0, -1.5]);
  } else if (kind === "arcade") {
    for (let i = 0; i < 6; i++) for (const z of [-4.6, 1.2]) {
      const x = -6 + i * 2.4; box(1.2, 2, 0.9, x, 1, z, "#1a1426");
      box(0.9, 0.7, 0.05, x, 1.5, z + (z < 0 ? 0.46 : -0.46), ["#ff5fb4", "#4de3ff", "#5ce0a0", "#ffb547"][i % 4], { emissive: true, solid: false });
    }
    counter = { x: 0, z: -3.2 };
    spots.push([-4, -1.5], [3, -1], [-1, 3.5], [5, 3.5]);
  }
  // the counter marker: a glowing ring on the floor where the place's own screen opens
  const ring = new THREE.Mesh(new THREE.RingGeometry(0.45, 0.6, 20), new THREE.MeshBasicMaterial({ color: new THREE.Color(L.neon), side: THREE.DoubleSide }));
  ring.rotation.x = -Math.PI / 2; ring.position.set(counter.x, 0.03, counter.z); scene.add(ring);
  return { scene, solids, counter, ring, exit: { x: 0, z: ROOM_D / 2 - 0.9 }, spawn: { x: 0, z: ROOM_D / 2 - 1.8, yaw: 0 }, spots, neon: L.neon };
}
