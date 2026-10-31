// The fight in first person (owner request 31.10, docs/PLAN_FIGHT3D.md phase 2). The client only moves things and
// reports contacts: every projectile (or blade) that reaches an enemy asks the server for one attack of the player
// (POST /api/fight3d/hit), every projectile or blow of an enemy that reaches the player asks for one attack of that
// enemy (POST /api/fight3d/hurt). The server rolls the same dice as the text fight and answers with the hit, the damage
// and the state; the client shows it. A projectile that flies past costs nothing and rolls nothing.
//
// Input from the page (NcAxis): 5 fire held. The weapon is chosen by the page (OnWeapon).
using System;
using System.Collections;
using System.Collections.Generic;
using System.Runtime.InteropServices;
using UnityEngine;
using UnityEngine.Networking;

namespace NC
{
    [Serializable] public class FEnemy { public int i; public string id; public string name; public int hp; public int max_hp; public bool active; public bool ranged; public bool melee; public int attempts; public int stun; public int danger; }
    [Serializable] public class FWeapon { public string key; public string name; public bool melee; public int attempts; public string type; public string dice; }
    [Serializable] public class FPlayer { public int hp; public int max_hp; }
    [Serializable] public class FStrike { public bool hit; public bool rolled; public int total; public int dv; public string crit; public int dmg; public string down; public string dodged; public int left; }
    [Serializable] public class FView
    {
        public bool ok; public string status; public bool over; public string outcome; public bool reinforced; public bool died;
        public float tick_s; public float tick_left; public float left;
        public FPlayer player; public FEnemy[] enemies; public FWeapon[] weapons; public FStrike strike;
    }
    [Serializable] class Fx { public float x; public float y; public string text; public string kind; }

    public class NcFight : MonoBehaviour
    {
#if UNITY_WEBGL && !UNITY_EDITOR
        [DllImport("__Internal")] static extern void NcFightUi(string json);
        [DllImport("__Internal")] static extern void NcFx(string json);
        [DllImport("__Internal")] static extern float NcAxis(int i);
#else
        static void NcFightUi(string json) { Debug.Log("[NcFightUi] " + json); }
        static void NcFx(string json) { Debug.Log("[NcFx] " + json); }
        static float NcAxis(int i) { return 0f; }
#endif

        class Foe
        {
            public FEnemy data;
            public Transform t, bar, barBack, head;
            public float nextAttack, strafe, hurtFlash;
            public bool alive = true;
            public Vector3 home;
        }

        class Shot
        {
            public Transform t;
            public Vector3 dir;
            public float speed, life;
            public bool mine;
            public int foe = -1;      // the enemy who fired it
            public string weapon = "";
        }

        NcCity city;
        FView view;
        readonly List<Foe> foes = new List<Foe>();
        readonly List<Shot> shots = new List<Shot>();
        int weapon;
        float nextShot, lockedUntil, kick, flash;
        bool over, busy;
        Transform gun, muzzle;
        Light muzzleLight;
        Material shotMine, shotFoe;

        public void Begin(NcCity c)
        {
            city = c;
            shotMine = city.NeonMat(new Color(1f, 0.85f, 0.4f), 3f);
            shotFoe = city.NeonMat(new Color(1f, 0.2f, 0.3f), 3f);
            StartCoroutine(Get("/api/fight3d/state?t=" + UnityWebRequest.EscapeURL(city.Token), Spawn));
        }

        // -- the server --------------------------------------------------------------------------------------------------

        IEnumerator Get(string path, Action<FView> then)
        {
            using (var req = UnityWebRequest.Get(city.Api + path))
            {
                yield return req.SendWebRequest();
                Done(req, then);
            }
        }

        IEnumerator Post(string what, string body, Action<FView> then)
        {
            using (var req = new UnityWebRequest(city.Api + "/api/fight3d/" + what, "POST"))
            {
                req.uploadHandler = new UploadHandlerRaw(System.Text.Encoding.UTF8.GetBytes(body));
                req.downloadHandler = new DownloadHandlerBuffer();
                req.SetRequestHeader("Content-Type", "application/json");
                yield return req.SendWebRequest();
                Done(req, then);
            }
        }

        void Done(UnityWebRequest req, Action<FView> then)
        {
            if (req.result != UnityWebRequest.Result.Success)
            {
                NcFightUi("{\"ok\":false,\"status\":\"net\"}");
                return;
            }
            var text = req.downloadHandler.text;
            FView v;
            try { v = JsonUtility.FromJson<FView>(text); } catch { return; }
            if (v == null) return;
            NcFightUi(text);
            then?.Invoke(v);
        }

        string Body(params (string k, object v)[] kv)
        {
            var sb = new System.Text.StringBuilder("{\"t\":\"" + city.Token + "\"");
            foreach (var (k, v) in kv)
                sb.Append(",\"").Append(k).Append("\":").Append(v is string s ? "\"" + s.Replace("\"", "") + "\"" : v is bool b ? (b ? "true" : "false") : Convert.ToString(v, System.Globalization.CultureInfo.InvariantCulture));
            return sb.Append("}").ToString();
        }

        void Apply(FView v)
        {
            view = v;
            if (v.enemies != null)
                foreach (var e in v.enemies)
                {
                    var f = foes.Find(x => x.data.i == e.i);
                    if (f == null) continue;
                    f.data = e;
                    if (f.alive && !e.active) Down(f);
                }
            if (v.over && !over) over = true;
        }

        // -- the enemies -------------------------------------------------------------------------------------------------

        void Spawn(FView v)
        {
            view = v;
            if (v.status != "fight" || v.enemies == null) return;
            var me = city.Body.transform;
            var fwd = me.forward; fwd.y = 0; fwd.Normalize();
            var side = Vector3.Cross(Vector3.up, fwd);
            int k = 0;
            foreach (var e in v.enemies)
            {
                if (!e.active) continue;
                var at = me.position + fwd * (11f + (k % 3) * 3.5f) + side * ((k % 2 == 0 ? -1 : 1) * (1.5f + (k / 2) * 1.8f));
                foes.Add(MakeFoe(e, at));
                k++;
            }
            BuildGun();
        }

        Foe MakeFoe(FEnemy e, Vector3 at)
        {
            Transform root, headT;
            var tone = e.ranged ? new Color(0.42f, 0.1f, 0.12f) : new Color(0.45f, 0.24f, 0.08f);
            if (city.humanModel != null)
            {
                root = NcBody.Make(city.humanModel, "e." + e.i, null, at, e.i * 7919 + e.danger, tone, e.ranged ? city.Accent : city.Accent2,
                                   city.PlainMat, city.NeonMat);
                var col = root.gameObject.AddComponent<CapsuleCollider>();
                col.center = new Vector3(0, 0.9f, 0); col.height = 1.8f; col.radius = 0.38f;
                headT = FindIn(root, "head") ?? root;
                if (e.ranged) root.GetComponent<NcRig>()?.Aim(true);
            }
            else
            {
                root = new GameObject("e." + e.i).transform;
                root.position = at;
                var body = GameObject.CreatePrimitive(PrimitiveType.Capsule);
                body.name = "e." + e.i;
                body.transform.SetParent(root, false);
                body.transform.localPosition = new Vector3(0, 0.9f, 0);
                body.transform.localScale = new Vector3(0.6f, 0.9f, 0.6f);
                body.GetComponent<Renderer>().sharedMaterial = city.PlainMat(tone);
                var head = GameObject.CreatePrimitive(PrimitiveType.Sphere);
                head.name = "e." + e.i;
                head.transform.SetParent(root, false);
                head.transform.localPosition = new Vector3(0, 1.95f, 0);
                head.transform.localScale = Vector3.one * 0.42f;
                head.GetComponent<Renderer>().sharedMaterial = city.PlainMat(new Color(0.75f, 0.6f, 0.5f));
                headT = head.transform;
            }
            // the health bar: a dark back and a red front, turned to the camera every frame
            var back = GameObject.CreatePrimitive(PrimitiveType.Cube);
            Destroy(back.GetComponent<Collider>());
            back.transform.SetParent(root, false);
            back.transform.localPosition = new Vector3(0, 2.5f, 0);
            back.transform.localScale = new Vector3(0.9f, 0.09f, 0.02f);
            back.GetComponent<Renderer>().sharedMaterial = city.PlainMat(new Color(0.05f, 0.05f, 0.06f));
            var bar = GameObject.CreatePrimitive(PrimitiveType.Cube);
            Destroy(bar.GetComponent<Collider>());
            bar.transform.SetParent(back.transform, false);
            bar.transform.localPosition = new Vector3(0, 0, -0.6f);
            bar.transform.localScale = new Vector3(1f, 0.8f, 0.5f);
            bar.GetComponent<Renderer>().sharedMaterial = city.NeonMat(new Color(1f, 0.15f, 0.2f), 1.6f);
            return new Foe { data = e, t = root, bar = bar.transform, barBack = back.transform, head = headT, home = at,
                             nextAttack = Time.time + 1.5f + UnityEngine.Random.value * 1.5f, strafe = UnityEngine.Random.value * 6f };
        }

        static Transform FindIn(Transform root, string n)
        {
            foreach (var t in root.GetComponentsInChildren<Transform>(true)) if (t.name == n) return t;
            return null;
        }

        void Down(Foe f)
        {
            f.alive = false;
            f.barBack.gameObject.SetActive(false);
            foreach (var c in f.t.GetComponentsInChildren<Collider>()) c.enabled = false;
            StartCoroutine(Fall(f.t));
        }

        IEnumerator Fall(Transform t)
        {
            var a = 0f;
            var start = t.rotation;
            var end = Quaternion.Euler(-88f, t.eulerAngles.y, 0);
            while (a < 1f) { a += Time.deltaTime * 2.2f; t.rotation = Quaternion.Slerp(start, end, a); yield return null; }
        }

        // -- the gun in hand ---------------------------------------------------------------------------------------------

        void BuildGun()
        {
            var cam = city.Cam.transform;
            gun = new GameObject("gun").transform;
            gun.SetParent(cam, false);
            gun.localPosition = new Vector3(0.17f, -0.21f, 0.42f);
            muzzle = new GameObject("muzzle").transform;
            muzzle.SetParent(gun, false);
            muzzle.localPosition = new Vector3(0, 0.02f, 0.22f);
            muzzleLight = muzzle.gameObject.AddComponent<Light>();
            muzzleLight.type = LightType.Point; muzzleLight.range = 7f; muzzleLight.color = new Color(1f, 0.8f, 0.45f); muzzleLight.intensity = 0f;
            ShowWeapon();
        }

        Transform held;

        // the model of the weapon in hand: a blade for melee, a rifle for long guns, a pistol for the rest
        void ShowWeapon()
        {
            var w = Current;
            if (gun == null || w == null) return;
            if (held != null) Destroy(held.gameObject);
            var longGun = w.type == "rifle" || w.type == "smg" || w.type == "shotgun" || w.type == "heavy";
            var model = w.melee ? city.bladeModel : longGun ? city.rifleModel : city.pistolModel;
            if (model == null)
            {
                held = new GameObject("held").transform;
                held.SetParent(gun, false);
                Part(held, Vector3.zero, w.melee ? new Vector3(0.035f, 0.055f, 0.5f) : new Vector3(0.07f, 0.11f, 0.32f), city.PlainMat(new Color(0.12f, 0.12f, 0.14f)));
                Part(held, new Vector3(0, 0.065f, 0.02f), new Vector3(0.02f, 0.02f, 0.26f), city.NeonMat(city.Accent, 1.6f));
                muzzle.localPosition = new Vector3(0, 0.02f, 0.22f);
                return;
            }
            held = Instantiate(model, gun, false).transform;
            held.localPosition = Vector3.zero; held.localRotation = Quaternion.identity; held.localScale = Vector3.one;
            Grip(held, w.melee ? 0.45f : longGun ? 0.6f : 0.22f,
                 w.melee ? new Vector3(0.02f, 0.02f, 0.12f) : longGun ? new Vector3(0, 0, 0.12f) : new Vector3(0, 0, 0.04f),
                 w.melee ? Quaternion.Euler(-35f, -10f, 0) : Quaternion.identity);
            foreach (var r in held.GetComponentsInChildren<Renderer>(true))
            {
                r.shadowCastingMode = UnityEngine.Rendering.ShadowCastingMode.Off;
                switch (r.name)
                {
                    case "glow": r.sharedMaterial = city.NeonMat(w.melee ? city.Accent2 : city.Accent, 2f); break;
                    case "slide": r.sharedMaterial = city.PlainMat(new Color(0.22f, 0.22f, 0.25f)); break;
                    case "muzzle": r.enabled = false; muzzle.position = r.bounds.center; break;
                    default: r.sharedMaterial = city.PlainMat(new Color(0.08f, 0.08f, 0.09f)); break;
                }
            }
        }

        // the model lies along the view, the muzzle forward, at the length of the weapon, whatever the FBX import did to its
        // axes and units (owner's screenshot 31.10: the pistol was a long strip across the screen)
        void Grip(Transform h, float length, Vector3 at, Quaternion extra)
        {
            var toGun = gun.worldToLocalMatrix;
            var min = Vector3.one * float.MaxValue; var max = Vector3.one * float.MinValue;
            Vector3? muz = null;
            foreach (var mf in h.GetComponentsInChildren<MeshFilter>(true))
            {
                if (mf.sharedMesh == null) continue;
                var m = toGun * mf.transform.localToWorldMatrix;
                var bb = mf.sharedMesh.bounds;
                for (int k = 0; k < 8; k++)
                {
                    var c = bb.center + Vector3.Scale(bb.extents, new Vector3((k & 1) == 0 ? -1 : 1, (k & 2) == 0 ? -1 : 1, (k & 4) == 0 ? -1 : 1));
                    var q = m.MultiplyPoint3x4(c);
                    min = Vector3.Min(min, q); max = Vector3.Max(max, q);
                }
                if (mf.name == "muzzle") muz = m.MultiplyPoint3x4(bb.center);
            }
            if (min.x > max.x) return;
            var size = max - min; var center = (min + max) / 2f;
            var rot = Quaternion.identity;
            var len = size.z;
            if (size.x >= size.y && size.x > size.z) { rot = Quaternion.Euler(0, -90f, 0); len = size.x; }
            else if (size.y > size.x && size.y > size.z) { rot = Quaternion.Euler(90f, 0, 0); len = size.y; }
            if (muz.HasValue && (rot * (muz.Value - center)).z < 0) rot = Quaternion.Euler(0, 180f, 0) * rot;
            var s = length / Mathf.Max(0.001f, len);
            var r = extra * rot;
            h.localRotation = r;
            h.localScale = Vector3.one * s;
            h.localPosition = at - r * center * s;
        }

        void Part(Transform parent, Vector3 pos, Vector3 size, Material m)
        {
            var g = GameObject.CreatePrimitive(PrimitiveType.Cube);
            Destroy(g.GetComponent<Collider>());
            g.transform.SetParent(parent, false);
            g.transform.localPosition = pos;
            g.transform.localScale = size;
            var r = g.GetComponent<Renderer>();
            r.sharedMaterial = m;
            r.shadowCastingMode = UnityEngine.Rendering.ShadowCastingMode.Off;
        }

        FWeapon Current => view != null && view.weapons != null && view.weapons.Length > 0 ? view.weapons[Mathf.Clamp(weapon, 0, view.weapons.Length - 1)] : null;

        // called by the page: the weapon chosen by its index in view.weapons
        public void OnWeapon(string index)
        {
            if (int.TryParse(index, out var i)) { weapon = i; ShowWeapon(); }
        }

        // called by the page when the client closes in the middle of the fight
        public void OnLeave(string unused)
        {
            StartCoroutine(Post("leave", Body(), null));
        }

        // -- the frame ---------------------------------------------------------------------------------------------------

        void Update()
        {
            if (city == null || city.Body == null) return;
            var dt = Time.deltaTime;
            var me = city.Body.transform.position;
            var cam = city.Cam;

            // the gun: recoil and the muzzle flash fade
            kick = Mathf.MoveTowards(kick, 0, dt * 6f);
            flash = Mathf.MoveTowards(flash, 0, dt * 14f);
            if (gun != null)
            {
                // left of the Fire button and turned a little, so the side of the gun shows (owner's screenshot 31.10)
                gun.localPosition = new Vector3(0.17f, -0.21f + kick * 0.03f, 0.42f - kick * 0.09f);
                gun.localRotation = Quaternion.Euler(-kick * 14f, -7f, -10f);
                muzzleLight.intensity = flash * 4f;
            }

            // the player fires
            var w = Current;
            if (!over && w != null && NcAxis(5) > 0.5f && Time.time >= nextShot && Time.time >= lockedUntil)
            {
                nextShot = Time.time + Mathf.Max(0.22f, (view.tick_s > 0 ? view.tick_s : 3f) / Mathf.Max(1, w.attempts) * 0.25f);
                kick = 1f;
                if (w.melee) Swing(w);
                else Fire(w);
            }

            // the enemies
            foreach (var f in foes)
            {
                if (!f.alive) continue;
                var to = me - f.t.position; to.y = 0;
                var dist = to.magnitude;
                var dir = dist > 0.01f ? to / dist : Vector3.forward;
                f.t.rotation = Quaternion.Slerp(f.t.rotation, Quaternion.LookRotation(dir), dt * 6f);
                var want = f.data.ranged && !f.data.melee ? 9f : 1.6f;
                f.strafe += dt;
                var side = Vector3.Cross(Vector3.up, dir) * Mathf.Sin(f.strafe * 0.9f) * (f.data.ranged ? 1.6f : 0f);
                var move = (dist > want + 0.6f ? dir : dist < want - 1.5f ? -dir : Vector3.zero) * (f.data.ranged ? 2.2f : 4.2f) + side;
                if (!over && f.data.stun <= 0) StepFoe(f, move * dt);
                // the bar faces the camera and shows the health
                var hpPart = f.data.max_hp > 0 ? Mathf.Clamp01((float)f.data.hp / f.data.max_hp) : 0f;
                f.bar.localScale = new Vector3(Mathf.Max(0.001f, hpPart), 0.8f, 0.5f);
                f.bar.localPosition = new Vector3(-(1f - hpPart) / 2f, 0, -0.6f);
                f.barBack.rotation = Quaternion.LookRotation(f.barBack.position - cam.transform.position);
                // the attack, at the pace of the text: attempts per tick
                if (over || busy || f.data.stun > 0 || Time.time < f.nextAttack) continue;
                var pace = (view.tick_s > 0 ? view.tick_s : 3f) / Mathf.Max(1, f.data.attempts);
                f.nextAttack = Time.time + pace * UnityEngine.Random.Range(0.85f, 1.25f);
                if (f.data.melee && dist < 2.2f) { Hurt(f, true); continue; }
                var eye = f.t.position + Vector3.up * 1.45f; // the gun hand, raised
                if (f.data.ranged && dist < 30f && Clear(eye, cam.transform.position))
                {
                    var aim = (cam.transform.position + Vector3.down * 0.3f - eye).normalized;
                    aim = Quaternion.Euler(UnityEngine.Random.Range(-2f, 2f), UnityEngine.Random.Range(-3f, 3f), 0) * aim;
                    Launch(eye + aim * 0.6f, aim, 24f, false, f.data.i, "");
                }
            }

            // the projectiles
            for (int i = shots.Count - 1; i >= 0; i--)
            {
                var s = shots[i];
                var step = s.dir * s.speed * dt;
                s.life -= dt;
                var from = s.t.position;
                bool gone = s.life <= 0;
                if (!gone && Physics.Raycast(from, s.dir, out var hit, step.magnitude + 0.05f))
                {
                    gone = true;
                    if (s.mine)
                    {
                        var f = FoeOf(hit.collider.transform);
                        if (f != null && f.alive) Hit(f, s.weapon, false, hit.point);
                    }
                    else if (hit.collider == city.Body)
                    {
                        var f = foes.Find(x => x.data.i == s.foe);
                        if (f != null && f.alive) Hurt(f, false);
                    }
                }
                if (!gone && !s.mine)
                {
                    // the player has no collider of his own here: the shot hits within half a metre of the body line
                    var head = cam.transform.position;
                    var p = ClosestOnSegment(head + Vector3.down * 1.4f, head, from + step * 0.5f);
                    if ((p - (from + step * 0.5f)).magnitude < 0.45f)
                    {
                        gone = true;
                        var f = foes.Find(x => x.data.i == s.foe);
                        if (f != null && f.alive) Hurt(f, false);
                    }
                }
                if (gone) { Destroy(s.t.gameObject); shots.RemoveAt(i); continue; }
                s.t.position = from + step;
            }
        }

        static Vector3 ClosestOnSegment(Vector3 a, Vector3 b, Vector3 p)
        {
            var ab = b - a;
            var k = Mathf.Clamp01(Vector3.Dot(p - a, ab) / Mathf.Max(0.0001f, ab.sqrMagnitude));
            return a + ab * k;
        }

        void StepFoe(Foe f, Vector3 delta)
        {
            if (delta.sqrMagnitude < 1e-6f) return;
            var from = f.t.position + Vector3.up * 0.9f;
            if (Physics.SphereCast(from, 0.35f, delta.normalized, out var hit, delta.magnitude + 0.3f) && FoeOf(hit.collider.transform) == null)
                return; // a wall: stay
            f.t.position += delta;
        }

        bool Clear(Vector3 a, Vector3 b)
        {
            var d = b - a;
            if (!Physics.Raycast(a, d.normalized, out var hit, d.magnitude)) return true;
            return hit.collider == city.Body || FoeOf(hit.collider.transform) != null;
        }

        Foe FoeOf(Transform t)
        {
            while (t != null)
            {
                if (t.name.StartsWith("e.") && int.TryParse(t.name.Substring(2), out var i)) return foes.Find(x => x.data.i == i);
                t = t.parent;
            }
            return null;
        }

        void Launch(Vector3 at, Vector3 dir, float speed, bool mine, int foe, string weaponKey)
        {
            var g = GameObject.CreatePrimitive(PrimitiveType.Sphere);
            Destroy(g.GetComponent<Collider>());
            g.transform.position = at;
            g.transform.localScale = Vector3.one * (mine ? 0.09f : 0.14f);
            var r = g.GetComponent<Renderer>();
            r.sharedMaterial = mine ? shotMine : shotFoe;
            r.shadowCastingMode = UnityEngine.Rendering.ShadowCastingMode.Off;
            shots.Add(new Shot { t = g.transform, dir = dir, speed = speed, life = 2.5f, mine = mine, foe = foe, weapon = weaponKey });
        }

        void Fire(FWeapon w)
        {
            flash = 1f;
            var cam = city.Cam.transform;
            var dir = Quaternion.Euler(UnityEngine.Random.Range(-0.6f, 0.6f), UnityEngine.Random.Range(-0.6f, 0.6f), 0) * cam.forward;
            Launch(muzzle != null ? muzzle.position : cam.position, dir, 60f, true, -1, w.key);
        }

        void Swing(FWeapon w)
        {
            var cam = city.Cam.transform;
            Foe best = null; var bestD = 2.4f;
            foreach (var f in foes)
            {
                if (!f.alive) continue;
                var to = f.t.position - city.Body.transform.position; to.y = 0;
                if (to.magnitude < bestD && Vector3.Dot(cam.forward, to.normalized) > 0.5f) { bestD = to.magnitude; best = f; }
            }
            if (best != null) Hit(best, w.key, true, best.head.position);
        }

        void Hit(Foe f, string weaponKey, bool melee, Vector3 at)
        {
            var screen = city.Cam.WorldToViewportPoint(at);
            StartCoroutine(Post("hit", Body(("i", f.data.i), ("w", weaponKey), ("m", melee)), v =>
            {
                Apply(v);
                if (v.status == "cooldown")
                {
                    lockedUntil = Time.time + Mathf.Max(0.1f, v.left);
                    Show(screen, "перезарядка", "info");
                    return;
                }
                var s = v.strike;
                if (s == null || !s.rolled) { if (v.status == "hit" && s != null && !string.IsNullOrEmpty(s.dodged)) Show(screen, "увернулся", "miss"); return; }
                if (s.hit && s.dmg > 0) Show(screen, (s.crit == "success" ? "крит " : s.crit == "fail" ? "провал " : "") + "−" + s.dmg + "  " + s.total + "/" + s.dv, "dmg");
                else Show(screen, (string.IsNullOrEmpty(s.dodged) ? "мимо " : "увернулся ") + s.total + "/" + s.dv, "miss");
                if (s.left <= 0 && v.tick_left > 0) lockedUntil = Time.time + v.tick_left;
            }));
        }

        void Hurt(Foe f, bool melee)
        {
            busy = true;
            StartCoroutine(Post("hurt", Body(("i", f.data.i), ("m", melee)), v =>
            {
                busy = false;
                Apply(v);
                var s = v.strike;
                if (s == null || !s.rolled) return;
                Show(new Vector3(0.5f, 0.62f, 1), s.hit && s.dmg > 0 ? "−" + s.dmg + "  " + s.total + "/" + s.dv : "мимо " + s.total + "/" + s.dv,
                     s.hit && s.dmg > 0 ? "hurt" : "dodge");
            }));
        }

        void Show(Vector3 viewport, string text, string kind)
        {
            if (viewport.z < 0) return;
            NcFx(JsonUtility.ToJson(new Fx { x = viewport.x, y = 1f - viewport.y, text = text, kind = kind }));
        }
    }
}
