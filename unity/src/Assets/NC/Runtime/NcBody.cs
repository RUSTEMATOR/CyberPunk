// People of the Unity client from the Blender model (unity/blender/make_models.py, docs/PLAN_FIGHT3D.md phase 3): the
// human is made of parts with pivots at the joints, so the walk is a swing of the arms and legs in code. Without the
// model (a build before the first Blender run) a person is a capsule, as before.
using System;
using UnityEngine;

namespace NC
{
    public class NcRig : MonoBehaviour
    {
        Transform armL, armR, legL, legR, head;
        float phase;
        Vector3 last;
        public float lift; // the capsule fallback stands on its middle
        float seed;

        void Awake()
        {
            armL = Find("arm_l"); armR = Find("arm_r"); legL = Find("leg_l"); legR = Find("leg_r"); head = Find("head");
            last = transform.position;
            seed = UnityEngine.Random.value * 100f;
        }

        Transform Find(string n)
        {
            foreach (var t in GetComponentsInChildren<Transform>(true)) if (t.name == n) return t;
            return null;
        }

        // the swing follows the distance actually walked, so a standing person stands still
        void LateUpdate()
        {
            var moved = transform.position - last; moved.y = 0;
            last = transform.position;
            var speed = moved.magnitude / Mathf.Max(0.0001f, Time.deltaTime);
            phase += moved.magnitude * 3.4f;
            var amount = Mathf.Clamp01(speed / 1.2f);
            var s = Mathf.Sin(phase) * 32f * amount;
            if (legL != null) legL.localRotation = Quaternion.Euler(s, 0, 0);
            if (legR != null) legR.localRotation = Quaternion.Euler(-s, 0, 0);
            if (armL != null) armL.localRotation = Quaternion.Euler(-s * 0.8f, 0, 0);
            if (armR != null) armR.localRotation = Quaternion.Euler(s * 0.8f, 0, 0);
            if (head != null) head.localRotation = Quaternion.Euler(0, Mathf.Sin(Time.time * 0.37f + seed) * 12f * (1 - amount), 0);
        }

        // an arm raised forward (the gun hand of an enemy)
        public void Aim(bool on)
        {
            if (armR != null && on) armR.localRotation = Quaternion.Euler(-80f, 0, 0);
        }
    }

    public static class NcBody
    {
        static readonly Color[] Skins = { new Color(0.85f, 0.66f, 0.52f), new Color(0.62f, 0.44f, 0.32f), new Color(0.42f, 0.28f, 0.2f), new Color(0.93f, 0.78f, 0.66f) };

        public static Transform Make(GameObject model, string name, Transform parent, Vector3 feet, int seed, Color jacket, Color accent,
                                     Func<Color, Material> plain, Func<Color, float, Material> neon)
        {
            var r = new System.Random(seed);
            if (model == null)
            {
                var p = GameObject.CreatePrimitive(PrimitiveType.Capsule);
                p.name = name;
                UnityEngine.Object.Destroy(p.GetComponent<Collider>());
                p.transform.SetParent(parent, true);
                p.transform.localScale = new Vector3(0.55f, 0.9f, 0.55f);
                p.transform.position = feet + Vector3.up * 0.9f;
                p.GetComponent<Renderer>().sharedMaterial = plain(jacket);
                p.AddComponent<NcRig>().lift = 0.9f;
                return p.transform;
            }
            var g = UnityEngine.Object.Instantiate(model, feet, Quaternion.identity, parent);
            g.name = name;
            var skin = Skins[r.Next(Skins.Length)];
            var pants = Color.Lerp(new Color(0.1f, 0.1f, 0.12f), jacket, 0.15f);
            var shirt = Color.Lerp(jacket, Color.black, 0.45f);
            foreach (var rend in g.GetComponentsInChildren<Renderer>(true))
            {
                switch (rend.name)
                {
                    case "visor": rend.sharedMaterial = neon(accent, 2.2f); break;
                    case "head": rend.sharedMaterial = plain(skin); break;
                    case "jacket": rend.sharedMaterial = plain(jacket); break;
                    case "torso": case "arm_l": case "arm_r": rend.sharedMaterial = plain(shirt); break;
                    default: rend.sharedMaterial = plain(pants); break;
                }
                rend.shadowCastingMode = UnityEngine.Rendering.ShadowCastingMode.On;
            }
            Fit(g.transform, feet);
            g.AddComponent<NcRig>();
            return g.transform;
        }

        // the person stands on the given point and is a person's height, whatever the FBX import did to its pivot or units
        static void Fit(Transform g, Vector3 feet)
        {
            var rs = g.GetComponentsInChildren<Renderer>(true);
            if (rs.Length == 0) return;
            var b = rs[0].bounds;
            foreach (var r in rs) b.Encapsulate(r.bounds);
            if (b.size.y < 1.4f || b.size.y > 2.2f)
            {
                g.localScale *= 1.78f / Mathf.Max(0.01f, b.size.y);
                b = rs[0].bounds;
                foreach (var r in rs) b.Encapsulate(r.bounds);
            }
            g.position += new Vector3(feet.x - b.center.x, feet.y - b.min.y, feet.z - b.center.z);
        }

        // the jacket colours of the street: dark with a tint, now and then a loud one
        public static Color Jacket(System.Random r, Color acc, Color acc2)
        {
            var roll = r.NextDouble();
            if (roll < 0.15) return Color.Lerp(acc, Color.black, 0.35f);
            if (roll < 0.25) return Color.Lerp(acc2, Color.black, 0.4f);
            return Color.HSVToRGB((float)r.NextDouble(), 0.25f + (float)r.NextDouble() * 0.3f, 0.18f + (float)r.NextDouble() * 0.25f);
        }
    }
}
