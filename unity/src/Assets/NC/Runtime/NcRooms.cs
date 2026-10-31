// The rooms behind the doors (owner request 31.10 «зайти в клуб и т. п.», docs/PLAN_FIGHT3D.md phase 1). The same 13
// kinds as the three.js district (webapp/city3d/v4/rooms.js): a box of 16 × 12 × 4.5 m with the furniture of its kind,
// a glowing ring where the place's own screen opens (the counter), the exit in the front wall, and the spots where the
// people of the place stand. Procedural boxes; built once per door, far from the street.
using System;
using System.Collections.Generic;
using UnityEngine;

namespace NC
{
    public class Room
    {
        public string doorId;
        public Vector3 origin, counter, exit, spawn;
        public readonly List<Vector3> spots = new List<Vector3>();
        public Transform root;
    }

    public static class NcRooms
    {
        public const float W = 16f, D = 12f, H = 4.5f;

        static readonly HashSet<string> Kinds = new HashSet<string>
        {
            "bar", "club", "market", "clinic", "precinct", "corp_lobby", "turf", "gigs", "ripper", "work", "property", "arena", "arcade",
        };

        public static bool Has(string kind) => kind != null && Kinds.Contains(kind);

        // floor, wall, light, neon
        static readonly Dictionary<string, string[]> Look = new Dictionary<string, string[]>
        {
            ["bar"] = new[] { "#2a1a12", "#1c1416", "#ffb36b", "#ff3e8a" },
            ["club"] = new[] { "#120a1e", "#0c0814", "#b26bff", "#4de3ff" },
            ["market"] = new[] { "#24221c", "#1a1814", "#ffd27a", "#5ce0a0" },
            ["clinic"] = new[] { "#1c2426", "#182024", "#bff7ff", "#5ce0a0" },
            ["ripper"] = new[] { "#1c2426", "#141c20", "#9fefff", "#4de3ff" },
            ["precinct"] = new[] { "#20242c", "#1a1e26", "#d8e4ff", "#2b4cff" },
            ["corp_lobby"] = new[] { "#3a3c44", "#24262e", "#ffffff", "#6ec8ff" },
            ["turf"] = new[] { "#1e1a16", "#191512", "#ff8a3d", "#e0262b" },
            ["gigs"] = new[] { "#21170f", "#17110d", "#ffcf8a", "#ffb547" },
            ["work"] = new[] { "#22252a", "#1b1e23", "#e8f0ff", "#5aa9ff" },
            ["property"] = new[] { "#231c1a", "#1b1617", "#ffc89a", "#ff7eb6" },
            ["arena"] = new[] { "#1e1410", "#140e0c", "#ffdca0", "#ff3b2f" },
            ["arcade"] = new[] { "#0e0b18", "#0b0914", "#c08aff", "#ff5fb4" },
        };

        static Color C(string hex) { ColorUtility.TryParseHtmlString(hex, out var c); return c; }

        public static Room Build(string kind, string doorId, Vector3 origin, Func<Color, Material> plain, Func<Color, float, Material> neon)
        {
            var look = Look.ContainsKey(kind) ? Look[kind] : Look["bar"];
            var room = new Room { doorId = doorId, origin = origin, root = new GameObject("room." + doorId).transform };
            room.root.position = origin;
            var t = room.root;
            var neonCol = C(look[3]);
            var rng = new System.Random((kind + doorId).GetHashCode());

            void Box(float w, float h, float d, float x, float y, float z, Color col, bool glow = false, bool solid = true, float power = 1.6f)
            {
                var g = GameObject.CreatePrimitive(PrimitiveType.Cube);
                g.transform.SetParent(t, false);
                g.transform.localPosition = new Vector3(x, y, z);
                g.transform.localScale = new Vector3(w, h, d);
                g.GetComponent<Renderer>().sharedMaterial = glow ? neon(col, power) : plain(col);
                if (glow) g.GetComponent<Renderer>().shadowCastingMode = UnityEngine.Rendering.ShadowCastingMode.Off;
                if (!solid) UnityEngine.Object.Destroy(g.GetComponent<Collider>());
            }

            // the shell: floor, ceiling, four walls, a neon strip under the ceiling
            Box(W, 0.2f, D, 0, -0.1f, 0, C(look[0]));
            Box(W, 0.2f, D, 0, H + 0.1f, 0, new Color(0.04f, 0.04f, 0.05f), false, false);
            Box(W, H, 0.3f, 0, H / 2, -D / 2, C(look[1]));
            Box(W, H, 0.3f, 0, H / 2, D / 2, C(look[1]));
            Box(0.3f, H, D, -W / 2, H / 2, 0, C(look[1]));
            Box(0.3f, H, D, W / 2, H / 2, 0, C(look[1]));
            Box(W - 0.4f, 0.08f, 0.06f, 0, H - 0.3f, -D / 2 + 0.2f, neonCol, true, false);
            Box(W - 0.4f, 0.08f, 0.06f, 0, H - 0.3f, D / 2 - 0.2f, neonCol, true, false);
            // the exit: a dark doorway in the front wall with a green lintel
            Box(2.4f, 3.1f, 0.1f, 0, 1.55f, D / 2 - 0.2f, new Color(0.02f, 0.02f, 0.03f), false, false);
            Box(2.8f, 0.15f, 0.12f, 0, 3.2f, D / 2 - 0.22f, new Color(0.36f, 0.88f, 0.63f), true, false);
            // the name board over the back wall (the name itself is in the page's card)
            Box(6f, 1.4f, 0.1f, 0, 3.3f, -D / 2 + 0.25f, neonCol * 0.5f, true, false, 0.8f);

            var counter = new Vector2(0, -3.3f);
            var spots = new List<Vector2>();
            Color[] bottles = { C("#5ce0a0"), C("#ffb547"), C("#ff3e8a"), C("#4de3ff") };
            if (kind == "bar" || kind == "club")
            {
                Box(10, 1.1f, 0.9f, 0, 0.55f, -4.3f, C("#3a2016"));
                Box(10, 0.06f, 0.95f, 0, 1.12f, -4.3f, neonCol, true, false);
                for (int i = 0; i < 9; i++) Box(0.6f, 0.12f, 0.25f, -4 + i, 2.0f + (i % 3) * 0.35f, -5.7f, bottles[i % 4], true, false);
                for (int i = 0; i < 6; i++) Box(0.45f, 0.8f, 0.45f, -3.75f + i * 1.5f, 0.4f, -3.2f, C("#222228"));
                counter = new Vector2(0, -2.6f);
                if (kind == "club")
                {
                    Color[] tiles = { C("#ff3e8a"), C("#4de3ff"), C("#b26bff"), C("#5ce0a0") };
                    for (int x = 0; x < 6; x++)
                        for (int z = 0; z < 4; z++)
                            Box(1.1f, 0.03f, 1.1f, -3.3f + x * 1.2f, 0.02f, 0.4f + z * 1.2f, tiles[(x + z) % 4], true, false, 0.7f);
                    Box(4, 0.5f, 1.6f, 0, 0.25f, 5.0f - 0.8f, C("#1a1426")); // the DJ stand by the door
                }
                spots.AddRange(new[] { new Vector2(-3, -2.4f), new Vector2(2.5f, -2.4f), new Vector2(-5, 1), new Vector2(4.5f, 2), new Vector2(0, 1.5f), new Vector2(-2, 3) });
            }
            else if (kind == "market")
            {
                Color[] roofs = { C("#d0402a"), C("#2aa86a"), C("#d0a02a"), C("#2a6ad0") };
                foreach (var p in new[] { new Vector2(-5, -3), new Vector2(0, -3), new Vector2(5, -3), new Vector2(-5, 1.5f), new Vector2(5, 1.5f) })
                {
                    Box(3, 0.9f, 1.4f, p.x, 0.45f, p.y, C("#4a3a24"));
                    Box(3.4f, 0.08f, 1.8f, p.x, 2.4f, p.y, roofs[rng.Next(4)], false, false);
                    for (int k = 0; k < 4; k++) Box(0.4f, 0.3f, 0.4f, p.x - 1 + k * 0.65f, 1.05f, p.y, bottles[k], false, false);
                }
                counter = new Vector2(0, -1.6f);
                spots.AddRange(new[] { new Vector2(-5, -1.6f), new Vector2(5, -1.6f), new Vector2(-2.5f, 0), new Vector2(2.5f, 0.5f), new Vector2(0, 3) });
            }
            else if (kind == "clinic" || kind == "ripper")
            {
                Box(2.2f, 0.7f, 0.9f, 0, 0.6f, -3.6f, C("#3a4448"));
                Box(0.9f, 1.0f, 0.25f, 0, 1.2f, -4.0f, C("#3a4448"), false, false);
                foreach (var x in new[] { -2.5f, 2.5f })
                {
                    Box(1.4f, 0.9f, 0.12f, x, 2.2f, -5.6f, C("#081a10"), false, false);
                    Box(1.2f, 0.7f, 0.05f, x, 2.2f, -5.52f, C("#2aff8a"), true, false, 1.2f);
                }
                Box(1.2f, 2.2f, 0.6f, -6.6f, 1.1f, -3, C("#5a646a"));
                Box(1.2f, 2.2f, 0.6f, 6.6f, 1.1f, -3, C("#5a646a"));
                Box(0.12f, 3.2f, 0.12f, 1.4f, 1.6f, -3.6f, C("#888888"), false, false);
                Box(1.6f, 0.3f, 0.6f, 0.8f, 3.1f, -3.6f, C("#cfefff"), true, false, 1.4f);
                counter = new Vector2(0, -2.4f);
                spots.AddRange(new[] { new Vector2(-2.2f, -2.6f), new Vector2(2.4f, -2.2f), new Vector2(-5, 2), new Vector2(5, 2.5f) });
            }
            else if (kind == "precinct" || kind == "work")
            {
                foreach (var p in new[] { new Vector2(-4.5f, -2.5f), new Vector2(0, -2.5f), new Vector2(4.5f, -2.5f), new Vector2(-4.5f, 1.5f), new Vector2(4.5f, 1.5f) })
                {
                    Box(2.4f, 0.8f, 1.2f, p.x, 0.4f, p.y, kind == "precinct" ? C("#2c3240") : C("#30343c"));
                    Box(0.9f, 0.6f, 0.06f, p.x, 1.15f, p.y - 0.3f, C("#0a1626"), false, false);
                    Box(0.8f, 0.5f, 0.02f, p.x, 1.15f, p.y - 0.26f, C("#5aa9ff"), true, false, 1.2f);
                }
                if (kind == "precinct") for (int i = 0; i < 8; i++) Box(0.08f, 2.4f, 0.08f, -7 + i * 0.4f, 1.2f, -5.5f, C("#888888"), false, false);
                counter = new Vector2(0, -1.2f);
                spots.AddRange(new[] { new Vector2(-4.5f, -1.3f), new Vector2(4.5f, -1.3f), new Vector2(-1.5f, 0.5f), new Vector2(2, 3), new Vector2(-5, 3.5f) });
            }
            else if (kind == "corp_lobby")
            {
                Box(6, 1.1f, 1.2f, 0, 0.55f, -3.8f, C("#e8ecf0"));
                Box(6, 0.05f, 1.25f, 0, 1.12f, -3.8f, C("#6ec8ff"), true, false);
                for (int i = 0; i < 5; i++) Box(0.25f, 1.0f, 1.2f, -4 + i * 2, 0.5f, 0.5f, C("#9aa4b0"));
                Box(4, 2.2f, 0.05f, 0, 2.4f, -5.8f, C("#6ec8ff"), true, false, 1.0f);
                counter = new Vector2(0, -2.5f);
                spots.AddRange(new[] { new Vector2(-2, -2.6f), new Vector2(2, -2.6f), new Vector2(-6, 2), new Vector2(6, 2), new Vector2(0, 3.5f) });
            }
            else if (kind == "turf")
            {
                for (int i = 0; i < 9; i++) Box(1.1f, 1.1f, 1.1f, -6 + (float)rng.NextDouble() * 12, 0.55f + (i % 3 == 0 ? 1.1f : 0), -5 + (float)rng.NextDouble() * 2.5f, C("#4a3a22"));
                Box(0.8f, 1, 0.8f, 3, 0.5f, 1, C("#3a3a3a"));
                Box(0.6f, 0.4f, 0.6f, 3, 1.2f, 1, C("#ff7a2a"), true, false, 2.2f); // the barrel fire
                Box(0.1f, 2, 5, -7.75f, 2, 0, neonCol, true, false, 0.9f);          // the tag on the wall
                counter = new Vector2(0, -2.2f);
                spots.AddRange(new[] { new Vector2(-3, -1.5f), new Vector2(2, -1), new Vector2(4.5f, 2.5f), new Vector2(-4, 2.5f), new Vector2(0, 1) });
            }
            else if (kind == "gigs")
            {
                Box(3.6f, 0.8f, 1.6f, 0, 0.4f, -3.5f, C("#3a2412"));
                Box(0.5f, 0.06f, 0.5f, 0.8f, 0.83f, -3.5f, C("#ffcf8a"), true, false);
                Box(4, 0.7f, 1.2f, -5, 0.35f, 3, C("#5a1a1a"));
                Box(4, 1.0f, 0.3f, -5, 0.9f, 3.55f, C("#5a1a1a"), false, false);
                Box(1.4f, 2.2f, 0.5f, 6.5f, 1.1f, -4.5f, C("#2a2018"));
                counter = new Vector2(0, -2.2f);
                spots.AddRange(new[] { new Vector2(0, -4.6f), new Vector2(-5, 2), new Vector2(4, 1) });
            }
            else if (kind == "property")
            {
                Box(3, 0.6f, 2.2f, -5, 0.3f, -3.8f, C("#3a2a40"));
                Box(3, 0.2f, 0.6f, -5, 0.7f, -4.7f, C("#e0d0e8"), false, false);
                Box(3.4f, 0.7f, 1.1f, 3, 0.35f, 2.5f, C("#30303a"));
                Box(3, 1.7f, 0.1f, 3, 1.8f, -5.8f, C("#0a0a14"), false, false);
                Box(2.8f, 1.5f, 0.05f, 3, 1.8f, -5.74f, C("#4de3ff"), true, false, 1.0f);
                Box(0.05f, 2.2f, 5, 7.8f, 2.2f, -1, C("#1a2a4a"), true, false, 0.6f); // the window with the city
                counter = new Vector2(3, -4.2f);
                spots.AddRange(new[] { new Vector2(-2, 0), new Vector2(1, 3) });
            }
            else if (kind == "arena")
            {
                Box(7, 0.6f, 7, 0, 0.3f, -1.5f, C("#7a2a20"));
                foreach (var y in new[] { 1.1f, 1.5f, 1.9f })
                {
                    Box(7, 0.05f, 0.06f, 0, y, -5, C("#ffd23f"), true, false);
                    Box(7, 0.05f, 0.06f, 0, y, 2, C("#ffd23f"), true, false);
                    Box(0.06f, 0.05f, 7, -3.5f, y, -1.5f, C("#ffd23f"), true, false);
                    Box(0.06f, 0.05f, 7, 3.5f, y, -1.5f, C("#ffd23f"), true, false);
                }
                foreach (var p in new[] { new Vector2(-3.5f, -5), new Vector2(3.5f, -5), new Vector2(-3.5f, 2), new Vector2(3.5f, 2) })
                    Box(0.2f, 2, 0.2f, p.x, 1, p.y, C("#cccccc"), false, false);
                counter = new Vector2(0, 3.3f);
                spots.AddRange(new[] { new Vector2(-5.8f, -3), new Vector2(5.8f, -3), new Vector2(-6, 3), new Vector2(6, 3.5f), new Vector2(0, -1.5f) });
            }
            else if (kind == "arcade")
            {
                Color[] screens = { C("#ff5fb4"), C("#4de3ff"), C("#5ce0a0"), C("#ffb547") };
                for (int i = 0; i < 6; i++)
                    foreach (var z in new[] { -4.6f, 1.2f })
                    {
                        var x = -6 + i * 2.4f;
                        Box(1.2f, 2, 0.9f, x, 1, z, C("#1a1426"));
                        Box(0.9f, 0.7f, 0.05f, x, 1.5f, z + (z < 0 ? 0.46f : -0.46f), screens[i % 4], true, false, 1.3f);
                    }
                counter = new Vector2(0, -3.2f);
                spots.AddRange(new[] { new Vector2(-4, -1.5f), new Vector2(3, -1), new Vector2(-1, 3.5f), new Vector2(5, 3.5f) });
            }

            // the ring at the counter, where the place's own screen opens
            var ring = GameObject.CreatePrimitive(PrimitiveType.Cylinder);
            UnityEngine.Object.Destroy(ring.GetComponent<Collider>());
            ring.transform.SetParent(t, false);
            ring.transform.localPosition = new Vector3(counter.x, 0.02f, counter.y);
            ring.transform.localScale = new Vector3(1.2f, 0.01f, 1.2f);
            ring.GetComponent<Renderer>().sharedMaterial = neon(neonCol, 1.8f);

            // the light: one warm lamp over the hall and the neon by the back wall
            var lamp = new GameObject("lamp").AddComponent<Light>();
            lamp.type = LightType.Point; lamp.color = C(look[2]); lamp.range = 14f; lamp.intensity = 1.6f;
            lamp.transform.SetParent(t, false); lamp.transform.localPosition = new Vector3(0, H - 0.5f, 0);
            var glow = new GameObject("neon-light").AddComponent<Light>();
            glow.type = LightType.Point; glow.color = neonCol; glow.range = 9f; glow.intensity = 1.4f;
            glow.transform.SetParent(t, false); glow.transform.localPosition = new Vector3(0, 2.6f, -D / 2 + 1.2f);

            room.counter = origin + new Vector3(counter.x, 0, counter.y);
            room.exit = origin + new Vector3(0, 0, D / 2 - 0.9f);
            room.spawn = origin + new Vector3(0, 0.05f, D / 2 - 2.0f);
            foreach (var s in spots) room.spots.Add(origin + new Vector3(s.x, 0, s.y));
            return room;
        }
    }
}
