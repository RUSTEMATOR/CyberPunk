// The district of the player in first person (owner request 31.10, docs/PLAN_UNITY.md). Everything comes from
// /api/city3d/map: the blocks are made from the seed, every door of the map gets a doorway with a neon frame on a facade,
// the residents walk near their own door. The rules stay on the server: a door or a resident only opens a screen of the
// bot through the page (NcNear → «Войти» → press city:d3:<id>).
//
// Input is read from the page once a frame (NcAxis): 0 move x, 1 move y, 2 look dx, 3 look dy (both in pixels, consumed),
// 4 run. The page draws the joystick and the buttons in HTML, so Unity needs no input module and no fonts.
using System;
using System.Collections;
using System.Collections.Generic;
using System.Runtime.InteropServices;
using UnityEngine;
using UnityEngine.Networking;

namespace NC
{
    [Serializable] public class Boot { public string t; public string api; public string m; }
    [Serializable] public class Door { public string id; public string label; public string kind; public string room; }
    [Serializable] public class Npc { public int id; public string name; public string door; }
    [Serializable] public class Theme { public string acc; public string acc2; public string bg; public string fog; }
    [Serializable] public class Me { public int c; public string name; }
    [Serializable] public class MapData
    {
        public bool ok; public string status; public string city_name; public string district_name; public int seed;
        public Door[] doors; public Npc[] npcs; public Me me; public Theme theme;
    }
    [Serializable] class Stat { public float fps; public string gpu; public string device; public int sys_mb; public string stage; }
    [Serializable] class Near { public string id; public string label; public string kind; public string type; }

    public class NcCity : MonoBehaviour
    {
#if UNITY_WEBGL && !UNITY_EDITOR
        [DllImport("__Internal")] static extern void NcUi(string json);
        [DllImport("__Internal")] static extern void NcNear(string json);
        [DllImport("__Internal")] static extern void NcStat(string json);
        [DllImport("__Internal")] static extern float NcAxis(int i);
#else
        static void NcUi(string json) { Debug.Log("[NcUi] " + json); }
        static void NcNear(string json) { Debug.Log("[NcNear] " + json); }
        static void NcStat(string json) { Debug.Log("[NcStat] " + json); }
        static float NcAxis(int i) { return 0f; }
#endif

        public Material baseMat;   // Standard, plain
        public Material neonMat;   // Standard with _EMISSION on (the variant is kept by this asset)
        // the Blender models (unity/blender/make_models.py), set by NcBuild; null before the first Blender run
        public GameObject humanModel, pistolModel, rifleModel, bladeModel;

        // the layout of the district
        const int Grid = 4;            // blocks per side
        const float Block = 22f;       // a block's side
        const float Street = 11f;      // a street's width
        const float Eye = 1.65f;
        const float Walk = 4.2f, Run = 8.5f;
        const float LookSpeed = 0.18f; // degrees per pixel of the finger or the mouse

        Boot boot;
        MapData map;
        Camera cam;
        CharacterController body;
        float yaw, pitch, bootAt = -1f, fpsWindow, fps, nearTimer;
        int fpsFrames;
        bool statSent, ready;
        string nearId = "", step = "";
        System.Random rng = new System.Random(1);
        Color acc = new Color(1f, 0.24f, 0.54f), acc2 = new Color(1f, 0.71f, 0.28f), fogCol = new Color(0.09f, 0.06f, 0.12f);
        readonly Dictionary<string, Material> mats = new Dictionary<string, Material>();
        readonly Dictionary<string, Door> doorById = new Dictionary<string, Door>();
        readonly Dictionary<string, Vector3> doorPos = new Dictionary<string, Vector3>();   // a step in front of the door
        readonly Dictionary<string, Vector3> doorNormal = new Dictionary<string, Vector3>();
        readonly List<Transform> walkers = new List<Transform>();
        readonly List<Npc> walkerNpc = new List<Npc>();
        readonly List<Vector3> walkerHome = new List<Vector3>(), walkerGoal = new List<Vector3>();
        readonly List<Rect> solids = new List<Rect>(); // the buildings' footprints, for the walkers' goals
        // Night City (owner request 31.10 «дизайн в стиле Найт-Сити»): the dressing of the street and the sky
        readonly List<(Vector3 c, Vector2 size, float h)> houses = new List<(Vector3, Vector2, float)>();
        readonly List<Material> ads = new List<Material>();
        readonly List<(Transform t, Vector3 center, float radius, float speed, float phase)> avs = new List<(Transform, Vector3, float, float, float)>();
        // the rooms behind the doors (docs/PLAN_FIGHT3D.md phase 1): built on the first visit, far from the street
        readonly Dictionary<string, Room> rooms = new Dictionary<string, Room>();
        readonly Dictionary<string, List<Npc>> insiders = new Dictionary<string, List<Npc>>();
        readonly List<(Transform t, Npc n, string door)> roomPeople = new List<(Transform, Npc, string)>();
        Room inRoom;

        void Start()
        {
            cam = Camera.main;
            if (cam != null) { cam.fieldOfView = 68f; cam.nearClipPlane = 0.08f; }
            Application.targetFrameRate = 60;
        }

        // called by the page: {"t": token, "api": origin}
        public void OnBoot(string json)
        {
            boot = JsonUtility.FromJson<Boot>(json);
            bootAt = Time.realtimeSinceStartup;
            StartCoroutine(LoadMap());
        }

        IEnumerator LoadMap()
        {
            var url = boot.api + "/api/city3d/map?t=" + UnityWebRequest.EscapeURL(boot.t ?? "");
            using (var req = UnityWebRequest.Get(url))
            {
                yield return req.SendWebRequest();
                if (req.result != UnityWebRequest.Result.Success)
                {
                    NcUi("{\"ok\":false,\"status\":\"net\"}");
                    yield break;
                }
                var text = req.downloadHandler.text;
                map = JsonUtility.FromJson<MapData>(text);
                NcUi(text);
                if (map != null && map.ok)
                {
                    try { Build(); }
                    catch (Exception e)
                    {
                        // the step and the error go to the metrics: a stripped build has no stack trace
                        NcStat(JsonUtility.ToJson(new Stat { stage = ("build-fail " + step + ": " + e.GetType().Name + " " + e.Message).Substring(0, Math.Min(200, ("build-fail " + step + ": " + e.GetType().Name + " " + e.Message).Length)) }));
                        statSent = true;
                    }
                }
            }
        }

        // -- materials --------------------------------------------------------------------------------------------------

        static Color Hex(string s, Color fallback)
        {
            return !string.IsNullOrEmpty(s) && ColorUtility.TryParseHtmlString(s, out var c) ? c : fallback;
        }

        // for NcFight
        public Camera Cam => cam;
        public CharacterController Body => body;
        public string Api => boot != null ? boot.api : "";
        public string Token => boot != null ? boot.t : "";
        public Material PlainMat(Color c) => Plain(c);
        public Material NeonMat(Color c, float power) => Neon(c, power);
        public Color Accent => acc;
        public Color Accent2 => acc2;

        Material Plain(Color c)
        {
            var key = "p" + ColorUtility.ToHtmlStringRGB(c);
            if (!mats.TryGetValue(key, out var m)) { m = new Material(baseMat) { color = c }; mats[key] = m; }
            return m;
        }

        Material Neon(Color c, float power = 2.2f)
        {
            var key = "n" + ColorUtility.ToHtmlStringRGB(c) + power;
            if (!mats.TryGetValue(key, out var m))
            {
                m = new Material(neonMat) { color = c * 0.3f };
                m.SetColor("_EmissionColor", c * power);
                mats[key] = m;
            }
            return m;
        }

        // the lit windows of the city: one small texture, every building tiles it at its own scale
        Texture2D windows;
        Texture2D Windows()
        {
            if (windows != null) return windows;
            const int n = 16;
            windows = new Texture2D(n, n, TextureFormat.RGBA32, false) { filterMode = FilterMode.Point, wrapMode = TextureWrapMode.Repeat };
            var r = new System.Random(map.seed);
            var warm = new[] { new Color(1f, 0.82f, 0.55f), new Color(0.75f, 0.85f, 1f), acc, acc2 };
            for (int y = 0; y < n; y++)
                for (int x = 0; x < n; x++)
                {
                    bool frame = x % 2 == 1 || y % 2 == 1;                // a dark gap between the windows
                    bool lit = !frame && r.NextDouble() < 0.33;
                    var c = frame ? new Color(0.02f, 0.02f, 0.03f) : lit ? warm[r.Next(warm.Length)] : new Color(0.06f, 0.06f, 0.09f);
                    windows.SetPixel(x, y, c);
                }
            windows.Apply();
            return windows;
        }

        Material BuildingMat(Color tint, Vector2 tiling)
        {
            var m = new Material(neonMat) { color = tint };
            m.mainTexture = Windows();
            m.mainTextureScale = tiling;
            m.SetTexture("_EmissionMap", Windows());
            m.SetColor("_EmissionColor", Color.white * 0.9f);
            return m;
        }

        GameObject Box(string name, Vector3 center, Vector3 size, Material m, Transform parent, bool solid = true)
        {
            var g = GameObject.CreatePrimitive(PrimitiveType.Cube);
            g.name = name;
            g.transform.SetParent(parent, false);
            g.transform.position = center;
            g.transform.localScale = size;
            g.GetComponent<Renderer>().sharedMaterial = m;
            if (!solid) Destroy(g.GetComponent<Collider>());
            return g;
        }

        // -- the district -----------------------------------------------------------------------------------------------

        static float Span => Grid * Block + (Grid + 1) * Street;
        static Vector3 BlockCenter(int i, int j)
        {
            var o = -Span / 2f + Street + Block / 2f;
            return new Vector3(o + i * (Block + Street), 0, o + j * (Block + Street));
        }

        void Build()
        {
            step = "camera";
            if (cam == null) cam = Camera.main;
            if (cam == null) cam = FindFirstObjectByType<Camera>();
            if (cam == null) cam = new GameObject("Camera").AddComponent<Camera>();
            step = "theme";
            rng = new System.Random(map.seed);
            if (map.theme != null)
            {
                acc = Hex(map.theme.acc, acc); acc2 = Hex(map.theme.acc2, acc2); fogCol = Hex(map.theme.fog, fogCol);
            }
            step = "fog";
            RenderSettings.fogColor = fogCol;
            cam.backgroundColor = fogCol * 0.7f;
            step = "root";
            var root = new GameObject("district").transform;

            // the ground: asphalt and the sidewalk slabs of the blocks
            var ground = Box("ground", new Vector3(0, -0.5f, 0), new Vector3(Span + 60, 1, Span + 60), Plain(new Color(0.07f, 0.07f, 0.09f)), root);
            ground.name = "ground";
            var walkMat = Plain(new Color(0.16f, 0.15f, 0.19f));
            var tints = new[] { new Color(0.32f, 0.3f, 0.36f), new Color(0.36f, 0.3f, 0.28f), new Color(0.26f, 0.3f, 0.36f), new Color(0.3f, 0.28f, 0.34f) };

            step = "windows";
            Windows();
            step = "blocks";
            var faces = new List<(Vector3 center, Vector3 normal, float width)>();
            for (int i = 0; i < Grid; i++)
                for (int j = 0; j < Grid; j++)
                {
                    var c = BlockCenter(i, j);
                    Box($"walk.{i}.{j}", c + Vector3.up * 0.08f, new Vector3(Block + 3.2f, 0.16f, Block + 3.2f), walkMat, root, false);
                    // a block is one tower or two halves of different height
                    bool split = rng.NextDouble() < 0.45;
                    var parts = split
                        ? new[] { (c + new Vector3(-Block / 4f, 0, 0), new Vector2(Block / 2f - 0.6f, Block)), (c + new Vector3(Block / 4f, 0, 0), new Vector2(Block / 2f - 0.6f, Block)) }
                        : new[] { (c, new Vector2(Block, Block)) };
                    foreach (var (pc, sz) in parts)
                    {
                        var h = 9f + (float)rng.NextDouble() * (rng.NextDouble() < 0.25 ? 34f : 16f);
                        var tint = tints[rng.Next(tints.Length)];
                        var m = BuildingMat(tint, new Vector2(sz.x / 3.2f / 8f, h / 3.4f / 8f));
                        Box("house", pc + Vector3.up * h / 2f, new Vector3(sz.x, h, sz.y), m, root);
                        houses.Add((pc, sz, h));
                        // a lit edge on the roof
                        Box("roof", pc + Vector3.up * (h + 0.15f), new Vector3(sz.x + 0.3f, 0.3f, sz.y + 0.3f), Neon(rng.NextDouble() < 0.5 ? acc : acc2, 1.4f), root, false);
                    }
                    solids.Add(new Rect(c.x - Block / 2f - 0.4f, c.z - Block / 2f - 0.4f, Block + 0.8f, Block + 0.8f));
                    // the four facades that look at a street
                    faces.Add((c + new Vector3(0, 0, -Block / 2f), Vector3.back, Block));
                    faces.Add((c + new Vector3(0, 0, Block / 2f), Vector3.forward, Block));
                    faces.Add((c + new Vector3(-Block / 2f, 0, 0), Vector3.left, Block));
                    faces.Add((c + new Vector3(Block / 2f, 0, 0), Vector3.right, Block));
                }

            step = "dress";
            Dress(root);
            step = "lamps";
            // the street lamps at the crossings, and the lane marks
            var lampMat = Plain(new Color(0.12f, 0.12f, 0.14f));
            var lampHead = Neon(new Color(1f, 0.85f, 0.6f), 2.6f);
            for (int i = 0; i <= Grid; i++)
                for (int j = 0; j <= Grid; j++)
                {
                    var o = -Span / 2f + Street / 2f;
                    var p = new Vector3(o + i * (Block + Street) + 3.6f, 0, o + j * (Block + Street) + 3.6f);
                    Box("lamp", p + Vector3.up * 2.6f, new Vector3(0.18f, 5.2f, 0.18f), lampMat, root);
                    Box("lamp-head", p + Vector3.up * 5.25f, new Vector3(0.7f, 0.18f, 0.7f), lampHead, root, false);
                }
            var mark = Neon(new Color(0.9f, 0.85f, 0.6f), 0.5f);
            for (int k = 0; k <= Grid; k++)
            {
                var o = -Span / 2f + Street / 2f + k * (Block + Street);
                for (float t = -Span / 2f; t < Span / 2f; t += 6f)
                {
                    Box("mark", new Vector3(o, 0.01f, t), new Vector3(0.2f, 0.02f, 2.4f), mark, root, false);
                    Box("mark", new Vector3(t, 0.01f, o), new Vector3(2.4f, 0.02f, 0.2f), mark, root, false);
                }
            }

            step = "doors";
            // the doors: spread over the facades, never two on one facade while a free one is left
            var doors = map.doors ?? new Door[0];
            var order = new List<int>();
            for (int k = 0; k < faces.Count; k++) order.Add(k);
            for (int k = order.Count - 1; k > 0; k--) { int s = rng.Next(k + 1); (order[k], order[s]) = (order[s], order[k]); }
            for (int d = 0; d < doors.Length; d++)
            {
                var f = faces[order[d % order.Count]];
                var along = Vector3.Cross(Vector3.up, f.normal);
                var shift = ((float)rng.NextDouble() - 0.5f) * (f.width - 8f) + (d >= order.Count ? 5f : 0f);
                var at = f.center + along * shift;
                MakeDoor(doors[d], at, f.normal, along, root);
            }

            // the residents near their doors
            step = "people";
            var people = new GameObject("people").transform; // moving, so not in the static batch
            var npcs = map.npcs ?? new Npc[0];
            for (int k = 0; k < npcs.Length && k < 60; k++)
            {
                var n = npcs[k];
                if (n.door != null && doorById.ContainsKey(n.door) && NcRooms.Has(RoomKey(doorById[n.door])) && n.id % 2 == 0)
                {
                    if (!insiders.ContainsKey(n.door)) insiders[n.door] = new List<Npc>();
                    insiders[n.door].Add(n);
                    continue;
                }
                var home = n.door != null && doorPos.ContainsKey(n.door) ? doorPos[n.door] : RandomStreet();
                var p = NcBody.Make(humanModel, "n." + n.id, people, home + Jitter(2.5f), n.id, NcBody.Jacket(rng, acc, acc2),
                                    rng.NextDouble() < 0.5 ? acc : acc2, Plain, Neon);
                walkers.Add(p); walkerNpc.Add(n); walkerHome.Add(home); walkerGoal.Add(home + Jitter(4f));
            }

            step = "batch";
            // the small lit pieces cast no shadow; then the whole still district goes into one static batch
            foreach (var r in root.GetComponentsInChildren<Renderer>())
                if (r.name != "house" && r.name != "lamp") r.shadowCastingMode = UnityEngine.Rendering.ShadowCastingMode.Off;
            StaticBatchingUtility.Combine(root.gameObject);
            step = "spawn";
            Spawn();
            ready = true;
            if (boot != null && boot.m == "fight")
            {
                // the fight in first person (docs/PLAN_FIGHT3D.md phase 2): down the street, the enemies ahead
                yaw = 0f;
                gameObject.AddComponent<NcFight>().Begin(this);
            }
        }

        static string RoomKey(Door d)
        {
            if (!string.IsNullOrEmpty(d.room)) return d.room;
            return d.id != null && d.id.StartsWith("p.") ? d.id.Substring(2) : d.id;
        }

        // called by the page («Войти» at a door with a room): the player stands inside, at the exit, facing the hall
        public void OnEnter(string doorId)
        {
            if (body == null || !doorById.ContainsKey(doorId)) return;
            var door = doorById[doorId];
            var kind = RoomKey(door);
            if (!NcRooms.Has(kind)) return;
            if (!rooms.TryGetValue(doorId, out var room))
            {
                var origin = new Vector3(3000f + rooms.Count * 60f, 0, 3000f);
                room = NcRooms.Build(kind, doorId, origin, Plain, Neon);
                rooms[doorId] = room;
                var list = insiders.ContainsKey(doorId) ? insiders[doorId] : new List<Npc>();
                for (int i = 0; i < list.Count && i < room.spots.Count; i++)
                {
                    var pr = new System.Random(list[i].id);
                    var p = NcBody.Make(humanModel, "n." + list[i].id, room.root, room.spots[i], list[i].id, NcBody.Jacket(pr, acc, acc2),
                                        pr.NextDouble() < 0.5 ? acc : acc2, Plain, Neon);
                    var look = room.spawn - p.position; look.y = 0;
                    if (look.sqrMagnitude > 0.01f) p.rotation = Quaternion.LookRotation(look);
                    roomPeople.Add((p, list[i], doorId));
                }
            }
            inRoom = room;
            Place(room.spawn, 180f);
            nearId = "\u0001"; // not "": an empty spot after the move must still clear the card
        }

        // called by the page («Выйти» at the room's exit): back on the street in front of the door, facing away from it
        public void OnExit(string unused)
        {
            if (inRoom == null) return;
            var id = inRoom.doorId;
            inRoom = null;
            var n = doorNormal[id];
            Place(doorPos[id] + n * 1.2f + Vector3.up * 0.05f, Mathf.Atan2(n.x, n.z) * Mathf.Rad2Deg);
            nearId = "\u0001"; // not "": an empty spot after the move must still clear the card
        }

        void Place(Vector3 at, float faceYaw)
        {
            body.enabled = false;
            body.transform.position = at;
            body.enabled = true;
            yaw = faceYaw; pitch = 0f;
        }

        Vector3 Jitter(float r)
        {
            return new Vector3(((float)rng.NextDouble() - 0.5f) * 2f * r, 0, ((float)rng.NextDouble() - 0.5f) * 2f * r);
        }

        Vector3 RandomStreet()
        {
            var o = -Span / 2f + Street / 2f;
            var k = rng.Next(Grid + 1);
            var t = ((float)rng.NextDouble() - 0.5f) * (Span - Street);
            return rng.NextDouble() < 0.5 ? new Vector3(o + k * (Block + Street), 0, t) : new Vector3(t, 0, o + k * (Block + Street));
        }

        void MakeDoor(Door d, Vector3 at, Vector3 normal, Vector3 along, Transform root)
        {
            var col = Color.HSVToRGB(Mathf.Abs((d.kind ?? "").GetHashCode() % 360) / 360f, 0.7f, 1f);
            var neon = Neon(col, 1.35f); // brighter goes white on screen
            var g = new GameObject(d.id).transform;
            g.SetParent(root, false);
            g.position = at;
            g.rotation = Quaternion.LookRotation(normal);
            // the doorway: a dark slab just out of the wall, the neon frame, a sign board above
            Piece(g, new Vector3(0, 1.6f, 0.08f), new Vector3(2.4f, 3.2f, 0.16f), Plain(new Color(0.02f, 0.02f, 0.03f)));
            Piece(g, new Vector3(-1.3f, 1.7f, 0.18f), new Vector3(0.14f, 3.4f, 0.14f), neon);
            Piece(g, new Vector3(1.3f, 1.7f, 0.18f), new Vector3(0.14f, 3.4f, 0.14f), neon);
            Piece(g, new Vector3(0, 3.4f, 0.18f), new Vector3(2.74f, 0.14f, 0.14f), neon);
            Piece(g, new Vector3(0, 4.5f, 0.25f), new Vector3(4.2f, 1.1f, 0.2f), Neon(col * 0.6f, 0.9f));
            Piece(g, new Vector3(0, 4.5f, 0.37f), new Vector3(3.6f, 0.12f, 0.04f), Neon(Color.Lerp(col, Color.white, 0.35f), 1.5f));
            doorById[d.id] = d;
            doorPos[d.id] = at + normal * 1.6f;
            doorNormal[d.id] = normal;
        }

        void Piece(Transform parent, Vector3 local, Vector3 size, Material m)
        {
            var g = GameObject.CreatePrimitive(PrimitiveType.Cube);
            Destroy(g.GetComponent<Collider>());
            g.transform.SetParent(parent, false);
            g.transform.localPosition = local;
            g.transform.localScale = size;
            g.GetComponent<Renderer>().sharedMaterial = m;
        }

        // -- the player -------------------------------------------------------------------------------------------------

        void Spawn()
        {
            var go = new GameObject("player");
            body = go.AddComponent<CharacterController>();
            body.height = 1.8f; body.radius = 0.35f; body.center = new Vector3(0, 0.9f, 0); body.stepOffset = 0.3f;
            var o = -Span / 2f + Street / 2f + (Grid / 2) * (Block + Street);
            go.transform.position = new Vector3(o, 0.05f, o);
            cam.transform.SetParent(go.transform, false);
            cam.transform.localPosition = new Vector3(0, Eye, 0);
            cam.transform.localRotation = Quaternion.identity;
            yaw = 45f; pitch = 0f;
        }

        // called by the page when a door is chosen in «Все двери»: the player stands in front of it
        public void OnPick(string doorId)
        {
            if (body == null || string.IsNullOrEmpty(doorId) || !doorPos.ContainsKey(doorId)) return;
            inRoom = null;
            var n = doorNormal[doorId];
            body.enabled = false;
            body.transform.position = doorPos[doorId] + n * 2.4f + Vector3.up * 0.05f;
            body.enabled = true;
            var look = -n;
            yaw = Mathf.Atan2(look.x, look.z) * Mathf.Rad2Deg;
            pitch = 0f;
        }

        // the ad texture: blocks of colour and dark gaps, like a wall of video screens at night
        Texture2D adTex;
        Texture2D AdTexture()
        {
            if (adTex != null) return adTex;
            adTex = new Texture2D(32, 16, TextureFormat.RGBA32, false) { filterMode = FilterMode.Point, wrapMode = TextureWrapMode.Repeat };
            var r = new System.Random(map.seed + 7);
            var pal = new[] { acc, acc2, new Color(0.3f, 0.9f, 1f), new Color(1f, 1f, 1f), new Color(0.6f, 0.3f, 1f) };
            // panels of 4×4 texels: a colour field, a stripe of text, or dark
            for (int bx = 0; bx < 8; bx++)
                for (int by = 0; by < 4; by++)
                {
                    var col = pal[r.Next(pal.Length)] * (0.55f + (float)r.NextDouble() * 0.45f);
                    var kind = r.Next(4);
                    for (int x = 0; x < 4; x++)
                        for (int y = 0; y < 4; y++)
                        {
                            var on = kind == 0 ? false : kind == 1 ? y == 1 || (y == 2 && x < 3) : true;
                            adTex.SetPixel(bx * 4 + x, by * 4 + y, on ? col : new Color(0.03f, 0.02f, 0.05f));
                        }
                }
            adTex.Apply();
            return adTex;
        }

        bool InsideHouse(Vector3 p, float margin)
        {
            foreach (var (c, s, _) in houses)
                if (Mathf.Abs(p.x - c.x) < s.x / 2f + margin && Mathf.Abs(p.z - c.z) < s.y / 2f + margin) return true;
            return false;
        }

        void Dress(Transform root)
        {
            var dark = Plain(new Color(0.16f, 0.16f, 0.18f));
            var signCols = new[] { acc, acc2, new Color(0.25f, 0.9f, 1f), new Color(0.65f, 0.35f, 1f), new Color(0.35f, 1f, 0.55f) };
            foreach (var (pc, sz, h) in houses)
            {
                var hx = sz.x / 2f; var hz = sz.y / 2f;
                // air conditioners and balconies on the walls
                int n = 4 + rng.Next(8);
                for (int k = 0; k < n; k++)
                {
                    var face = rng.Next(4);
                    var y = 3f + (float)rng.NextDouble() * Mathf.Max(1f, h - 5f);
                    var along = ((float)rng.NextDouble() - 0.5f) * ((face < 2 ? sz.x : sz.y) - 2f);
                    Vector3 pos; Vector3 size;
                    if (face == 0) { pos = pc + new Vector3(along, y, -hz - 0.3f); size = new Vector3(1f, 0.7f, 0.6f); }
                    else if (face == 1) { pos = pc + new Vector3(along, y, hz + 0.3f); size = new Vector3(1f, 0.7f, 0.6f); }
                    else if (face == 2) { pos = pc + new Vector3(-hx - 0.3f, y, along); size = new Vector3(0.6f, 0.7f, 1f); }
                    else { pos = pc + new Vector3(hx + 0.3f, y, along); size = new Vector3(0.6f, 0.7f, 1f); }
                    Box("ac", pos, size, dark, root, false);
                }
                // a vertical neon sign at a corner
                if (rng.NextDouble() < 0.7)
                {
                    var cx = rng.NextDouble() < 0.5 ? -1 : 1; var cz = rng.NextDouble() < 0.5 ? -1 : 1;
                    var sh = 3.5f + (float)rng.NextDouble() * 4f;
                    var y = 4.5f + (float)rng.NextDouble() * Mathf.Max(1f, Mathf.Min(10f, h - sh - 2f));
                    var col = signCols[rng.Next(signCols.Length)];
                    var flat = rng.NextDouble() < 0.5;
                    var pos = pc + new Vector3(cx * (hx + 0.55f), y + sh / 2f, cz * (hz + 0.55f));
                    Box("sign", pos, flat ? new Vector3(1.0f, sh, 0.22f) : new Vector3(0.22f, sh, 1.0f), Plain(new Color(0.05f, 0.05f, 0.07f)), root, false);
                    Box("sign", pos + (flat ? new Vector3(0, 0, 0.13f * cz) : new Vector3(0.13f * cx, 0, 0)), flat ? new Vector3(0.8f, sh - 0.4f, 0.05f) : new Vector3(0.05f, sh - 0.4f, 0.8f), Neon(col, 1.5f), root, false);
                }
                // a big video screen on a tall building
                if (h > 18f && rng.NextDouble() < 0.65)
                {
                    var face = rng.Next(4);
                    var w = Mathf.Min((face < 2 ? sz.x : sz.y) - 2f, 10f);
                    var y = Mathf.Min(h - 4f, 9f + (float)rng.NextDouble() * (h - 14f));
                    var m = new Material(neonMat) { color = Color.white * 0.15f, mainTexture = AdTexture() };
                    m.SetTexture("_EmissionMap", AdTexture());
                    m.SetColor("_EmissionColor", Color.white * 1.3f);
                    m.mainTextureScale = new Vector2(0.5f + (float)rng.NextDouble() * 0.5f, 1f);
                    ads.Add(m);
                    Vector3 pos, size;
                    if (face == 0) { pos = pc + new Vector3(0, y, -hz - 0.15f); size = new Vector3(w, 4.5f, 0.1f); }
                    else if (face == 1) { pos = pc + new Vector3(0, y, hz + 0.15f); size = new Vector3(w, 4.5f, 0.1f); }
                    else if (face == 2) { pos = pc + new Vector3(-hx - 0.15f, y, 0); size = new Vector3(0.1f, 4.5f, w); }
                    else { pos = pc + new Vector3(hx + 0.15f, y, 0); size = new Vector3(0.1f, 4.5f, w); }
                    Box("ad", pos, size, m, root, false);
                }
            }
            // cables over the streets
            var cable = Plain(new Color(0.04f, 0.04f, 0.05f));
            for (int k = 0; k <= Grid; k++)
            {
                var o = -Span / 2f + Street / 2f + k * (Block + Street);
                for (int c = 0; c < 6; c++)
                {
                    var t0 = -Span / 2f + Street + (float)rng.NextDouble() * (Span - 2 * Street);
                    var y = 7f + (float)rng.NextDouble() * 5f;
                    Box("cable", new Vector3(o, y, t0), new Vector3(Street + 2f, 0.05f, 0.05f), cable, root, false);
                    Box("cable", new Vector3(t0, y + 0.6f, o), new Vector3(0.05f, 0.05f, Street + 2f), cable, root, false);
                }
            }
            // vending machines and bins on the sidewalks
            for (int k = 0; k < houses.Count * 2; k++)
            {
                var (pc, sz, h) = houses[rng.Next(houses.Count)];
                var face = rng.Next(4);
                var along = ((float)rng.NextDouble() - 0.5f) * ((face < 2 ? sz.x : sz.y) - 3f);
                var hx = sz.x / 2f; var hz = sz.y / 2f;
                Vector3 pos, n;
                if (face == 0) { pos = pc + new Vector3(along, 0, -hz - 0.8f); n = Vector3.back; }
                else if (face == 1) { pos = pc + new Vector3(along, 0, hz + 0.8f); n = Vector3.forward; }
                else if (face == 2) { pos = pc + new Vector3(-hx - 0.8f, 0, along); n = Vector3.left; }
                else { pos = pc + new Vector3(hx + 0.8f, 0, along); n = Vector3.right; }
                if (InsideHouse(pos, 0.6f)) continue; // the gap between the two halves of a block
                if (rng.NextDouble() < 0.5)
                {
                    var g = Box("vending", pos + Vector3.up * 1.05f, new Vector3(0.95f, 1.9f, 0.95f), Plain(new Color(0.1f, 0.1f, 0.12f)), root);
                    Box("vending-face", pos + Vector3.up * 1.15f + n * 0.49f, face < 2 ? new Vector3(0.75f, 1.3f, 0.03f) : new Vector3(0.03f, 1.3f, 0.75f),
                        Neon(signCols[rng.Next(signCols.Length)], 1.3f), root, false);
                }
                else Box("bin", pos + Vector3.up * 0.5f, new Vector3(0.7f, 1.0f, 0.7f), Plain(new Color(0.12f, 0.17f, 0.13f)), root);
            }
            // the megacity around: towers in the fog with red lights on top
            var ringMat = BuildingMat(new Color(0.16f, 0.15f, 0.2f), new Vector2(2.5f, 9f));
            var red = Neon(new Color(1f, 0.1f, 0.1f), 2.5f);
            for (int k = 0; k < 30; k++)
            {
                var a = k / 30f * Mathf.PI * 2f + (float)rng.NextDouble() * 0.15f;
                var dist = Span / 2f + 28f + (float)rng.NextDouble() * 55f;
                var w = 14f + (float)rng.NextDouble() * 18f;
                var h = 55f + (float)rng.NextDouble() * 110f;
                var c = new Vector3(Mathf.Cos(a) * dist, 0, Mathf.Sin(a) * dist);
                Box("tower", c + Vector3.up * h / 2f, new Vector3(w, h, w), ringMat, root, false);
                Box("tower-light", c + Vector3.up * (h + 0.6f), new Vector3(1.2f, 1.2f, 1.2f), red, root, false);
            }
            // AVs in the sky
            var avMat = Plain(new Color(0.12f, 0.12f, 0.14f));
            for (int k = 0; k < 5; k++)
            {
                var av = new GameObject("av").transform;
                Box("av-body", Vector3.zero, new Vector3(2.4f, 0.9f, 5f), avMat, av, false).transform.localPosition = Vector3.zero;
                Box("av-light", Vector3.zero, new Vector3(2.5f, 0.12f, 0.12f), Neon(acc, 2.5f), av, false).transform.localPosition = new Vector3(0, -0.3f, 2.4f);
                Box("av-light", Vector3.zero, new Vector3(2.5f, 0.12f, 0.12f), Neon(new Color(1f, 0.15f, 0.15f), 2f), av, false).transform.localPosition = new Vector3(0, -0.3f, -2.4f);
                avs.Add((av, new Vector3(((float)rng.NextDouble() - 0.5f) * 60f, 38f + (float)rng.NextDouble() * 30f, ((float)rng.NextDouble() - 0.5f) * 60f),
                         40f + (float)rng.NextDouble() * 60f, (0.04f + (float)rng.NextDouble() * 0.05f) * (rng.NextDouble() < 0.5 ? 1 : -1), (float)rng.NextDouble() * 6f));
            }
        }

        void Update()
        {
            // the screens run and the AVs fly, also in the fight
            for (int k = 0; k < ads.Count; k++) ads[k].mainTextureOffset = new Vector2(Time.time * (0.03f + k % 3 * 0.02f), Mathf.Floor(Time.time * 0.5f + k) * 0.25f);
            foreach (var av in avs)
            {
                var ang = av.phase + Time.time * av.speed;
                var to = av.center + new Vector3(Mathf.Cos(ang) * av.radius, Mathf.Sin(ang * 0.7f) * 3f, Mathf.Sin(ang) * av.radius);
                var way = to - av.t.position;
                if (way.sqrMagnitude > 0.0001f) av.t.rotation = Quaternion.LookRotation(way);
                av.t.position = to;
            }
            var dt = Time.deltaTime;
            fpsFrames++;
            fpsWindow += Time.unscaledDeltaTime;
            if (fpsWindow >= 1f) { fps = fpsFrames / fpsWindow; fpsFrames = 0; fpsWindow = 0; }
            if (!statSent && bootAt >= 0 && Time.realtimeSinceStartup - bootAt > 8f)
            {
                statSent = true;
                NcStat(JsonUtility.ToJson(new Stat
                {
                    fps = Mathf.Round(fps * 10f) / 10f, gpu = SystemInfo.graphicsDeviceName, device = SystemInfo.deviceModel,
                    sys_mb = SystemInfo.systemMemorySize, stage = ready ? "ready-fps" : "nomap",
                }));
            }
            if (!ready) return;

            // look and move
            yaw += NcAxis(2) * LookSpeed;
            pitch = Mathf.Clamp(pitch + NcAxis(3) * LookSpeed, -75f, 75f);
            body.transform.rotation = Quaternion.Euler(0, yaw, 0);
            cam.transform.localRotation = Quaternion.Euler(pitch, 0, 0);
            var move = new Vector3(Mathf.Clamp(NcAxis(0), -1f, 1f), 0, Mathf.Clamp(NcAxis(1), -1f, 1f));
            if (move.sqrMagnitude > 1f) move.Normalize();
            var speed = NcAxis(4) > 0.5f ? Run : Walk;
            body.SimpleMove(body.transform.TransformDirection(move) * speed);
            if (body.transform.position.y < -5f) Spawn();

            // the residents stroll near their doors
            for (int i = 0; i < walkers.Count; i++)
            {
                var w = walkers[i];
                var rig = w.GetComponent<NcRig>();
                var goal = walkerGoal[i] + Vector3.up * (rig != null ? rig.lift : 0.9f);
                var step = Vector3.MoveTowards(w.position, goal, dt * 1.3f);
                if ((step - w.position).sqrMagnitude > 0.0001f) w.rotation = Quaternion.LookRotation(step - w.position);
                w.position = step;
                if ((w.position - goal).sqrMagnitude < 0.04f) walkerGoal[i] = walkerHome[i] + Jitter(4f);
            }

            nearTimer -= dt;
            if (nearTimer <= 0f) { nearTimer = 0.15f; CheckNear(); }
        }

        // what the player stands at: the nearest door in front within 3 m, else the nearest resident within 2.2 m
        void CheckNear()
        {
            var me = body.transform.position;
            var fwd = body.transform.forward;
            string best = ""; float bestD = 3.2f; Near near = null;
            if (inRoom != null)
            {
                var door = doorById[inRoom.doorId];
                var toC = inRoom.counter - me; toC.y = 0;
                var toE = inRoom.exit - me; toE.y = 0;
                if (toC.magnitude < 1.6f) { best = "c." + door.id; near = new Near { id = door.id, label = door.label, kind = door.kind, type = "counter" }; }
                else if (toE.magnitude < 1.7f) { best = "x." + door.id; near = new Near { id = door.id, label = door.label, kind = "", type = "exit" }; }
                else
                {
                    bestD = 2.2f;
                    foreach (var rp in roomPeople)
                    {
                        if (rp.door != door.id) continue;
                        var to = rp.t.position - me; to.y = 0;
                        if (to.magnitude < bestD) { bestD = to.magnitude; best = "n." + rp.n.id; near = new Near { id = best, label = rp.n.name, kind = "", type = "npc" }; }
                    }
                }
                if (best == nearId) return;
                nearId = best;
                NcNear(near == null ? "" : JsonUtility.ToJson(near));
                return;
            }
            foreach (var kv in doorPos)
            {
                var to = kv.Value - me; to.y = 0;
                var d = to.magnitude;
                if (d < bestD && (d < 1.2f || Vector3.Dot(fwd, to.normalized) > 0.3f))
                {
                    bestD = d; best = kv.Key;
                    var door = doorById[kv.Key];
                    near = new Near { id = door.id, label = door.label, kind = door.kind, type = NcRooms.Has(RoomKey(door)) ? "room" : "door" };
                }
            }
            if (near == null)
            {
                bestD = 2.4f;
                for (int i = 0; i < walkers.Count; i++)
                {
                    var to = walkers[i].position - me; to.y = 0;
                    var d = to.magnitude;
                    if (d < bestD) { bestD = d; best = "n." + walkerNpc[i].id; near = new Near { id = best, label = walkerNpc[i].name, kind = "", type = "npc" }; }
                }
            }
            if (best == nearId) return;
            nearId = best;
            NcNear(near == null ? "" : JsonUtility.ToJson(near));
        }
    }
}
