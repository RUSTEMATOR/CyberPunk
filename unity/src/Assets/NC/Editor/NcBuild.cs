// The WebGL build of the Unity client, run by the bot on the owner's Mac (app/services/unity_jobs.py, docs/PLAN_UNITY.md):
//   Unity -batchmode -quit -projectPath unity/NightCity3D -buildTarget WebGL
//         -executeMethod NC.EditorTools.NcBuild.WebGL -ncOut <webapp/unity/uYYMMDDHHMM> -ncVersion <uYYMMDDHHMM>
// The scene is made here from code on every build, so the project needs no hand-made assets.
using System;
using System.IO;
using System.Linq;
using UnityEditor;
using UnityEditor.Build;
using UnityEditor.Build.Reporting;
using UnityEditor.SceneManagement;
using UnityEngine;
using UnityEngine.SceneManagement;

namespace NC.EditorTools
{
    public static class NcBuild
    {
        const string ScenePath = "Assets/NC/Generated/Probe.unity";
        const string MatPath = "Assets/NC/Generated/Base.mat";
        const string NeonPath = "Assets/NC/Generated/Neon.mat";

        static string Arg(string name, string fallback)
        {
            var args = Environment.GetCommandLineArgs();
            var i = Array.IndexOf(args, name);
            return i >= 0 && i + 1 < args.Length ? args[i + 1] : fallback;
        }

        public static void WebGL()
        {
            try
            {
                var outDir = Arg("-ncOut", Path.GetFullPath("Build/WebGL"));
                var version = Arg("-ncVersion", "dev");
                Settings(version);
                MakeScene();
                var opts = new BuildPlayerOptions
                {
                    scenes = new[] { ScenePath },
                    locationPathName = outDir,
                    target = BuildTarget.WebGL,
                    // a clean build every time: the incremental one once kept the old C# in the wasm (31.10)
                    options = BuildOptions.CleanBuildCache,
                };
                var report = BuildPipeline.BuildPlayer(opts);
                Debug.Log($"[NcBuild] result {report.summary.result}, {report.summary.totalSize} bytes, {report.summary.totalTime}");
                if (report.summary.result != BuildResult.Succeeded)
                {
                    EditorApplication.Exit(2);
                    return;
                }
                WriteInfo(outDir, version);
            }
            catch (Exception e)
            {
                Debug.LogError("[NcBuild] failed: " + e);
                EditorApplication.Exit(1);
            }
        }

        static void Settings(string version)
        {
            PlayerSettings.companyName = "Night City Life";
            PlayerSettings.productName = "Night City 3D";
            PlayerSettings.bundleVersion = version;
            PlayerSettings.runInBackground = false;
            PlayerSettings.stripEngineCode = true;
            var target = NamedBuildTarget.WebGL;
            PlayerSettings.SetManagedStrippingLevel(target, ManagedStrippingLevel.High);
            PlayerSettings.SetIl2CppCompilerConfiguration(target, Il2CppCompilerConfiguration.Master);
            PlayerSettings.WebGL.template = "PROJECT:NC";
            // gzip with the browser's own unpacking: the server sends no Content-Encoding, Cloudflare stays out of it
            PlayerSettings.WebGL.compressionFormat = WebGLCompressionFormat.Gzip;
            PlayerSettings.WebGL.decompressionFallback = true;
            PlayerSettings.WebGL.dataCaching = true;
            PlayerSettings.WebGL.nameFilesAsHashes = false;
            PlayerSettings.WebGL.exceptionSupport = WebGLExceptionSupport.ExplicitlyThrownExceptionsOnly;
            PlayerSettings.WebGL.showDiagnostics = false;
            Compat();
        }

        // owner report 31.10 (iPhone 16 Pro Max, iOS 18.7, Telegram): the default «WebAssembly 2023» build (native
        // exceptions, SIMD) does not start in the iOS web view. The switch is set by reflection: its name moved
        // between Unity versions, and a missing property must not break the build. Every WebGL setting goes to the log.
        static void Compat()
        {
            var type = typeof(PlayerSettings.WebGL);
            foreach (var p in type.GetProperties(System.Reflection.BindingFlags.Public | System.Reflection.BindingFlags.Static))
            {
                object v = null;
                try { v = p.GetValue(null); } catch { }
                Debug.Log($"[NcBuild] WebGL.{p.Name} = {v}");
            }
            foreach (var name in new[] { "wasm2023", "webAssembly2023", "useWasm2023" })
            {
                var p = type.GetProperty(name, System.Reflection.BindingFlags.Public | System.Reflection.BindingFlags.Static);
                if (p != null && p.CanWrite && p.PropertyType == typeof(bool))
                {
                    p.SetValue(null, false);
                    Debug.Log($"[NcBuild] {name} -> false");
                }
            }
        }

        static void MakeScene()
        {
            Directory.CreateDirectory("Assets/NC/Generated");
            var mat = new Material(Shader.Find("Standard")) { name = "NcBase" };
            mat.SetFloat("_Glossiness", 0.25f);
            AssetDatabase.CreateAsset(mat, MatPath);
            // the lit material: _EMISSION on, so the build keeps that variant of the Standard shader
            var neon = new Material(Shader.Find("Standard")) { name = "NcNeon" };
            neon.EnableKeyword("_EMISSION");
            neon.SetColor("_EmissionColor", Color.white);
            neon.globalIlluminationFlags = MaterialGlobalIlluminationFlags.None;
            AssetDatabase.CreateAsset(neon, NeonPath);

            var scene = EditorSceneManager.NewScene(NewSceneSetup.EmptyScene, NewSceneMode.Single);
            RenderSettings.ambientMode = UnityEngine.Rendering.AmbientMode.Flat;
            RenderSettings.ambientLight = new Color(0.22f, 0.18f, 0.30f);
            RenderSettings.fog = true;
            RenderSettings.fogColor = new Color(0.07f, 0.05f, 0.11f);
            RenderSettings.fogMode = FogMode.Linear;
            RenderSettings.fogStartDistance = 15f;
            RenderSettings.fogEndDistance = 210f;

            var camGo = new GameObject("Camera") { tag = "MainCamera" };
            var cam = camGo.AddComponent<Camera>();
            cam.clearFlags = CameraClearFlags.SolidColor;
            cam.backgroundColor = new Color(0.04f, 0.03f, 0.07f);
            cam.fieldOfView = 55f;
            cam.farClipPlane = 280f;
            // the colour grade (owner request 31.10 «цветокор»): teal shadows, sodium lights, vignette, grain
            var gradeShader = Shader.Find("NC/Grade");
            if (gradeShader != null)
            {
                var grade = new Material(gradeShader) { name = "NcGrade" };
                AssetDatabase.CreateAsset(grade, "Assets/NC/Generated/Grade.mat");
                camGo.AddComponent<NC.NcGrade>().grade = grade;
            }
            Debug.Log($"[NcBuild] grade shader {gradeShader != null}");
            camGo.transform.position = new Vector3(0, 14, -22);
            camGo.transform.LookAt(Vector3.zero);

            var sun = new GameObject("Light").AddComponent<Light>();
            sun.type = LightType.Directional;
            sun.color = new Color(0.75f, 0.7f, 1f);
            sun.intensity = 0.9f;
            sun.shadows = LightShadows.Soft;
            sun.transform.rotation = Quaternion.Euler(55, -35, 0);

            // the doors are made at runtime by CreatePrimitive: without a collider in the scene the engine strips physics
            var keep = new GameObject("keep-physics");
            keep.AddComponent<BoxCollider>();
            keep.AddComponent<CapsuleCollider>();
            keep.AddComponent<MeshCollider>();
            keep.AddComponent<CharacterController>();
            keep.SetActive(false);

            var root = new GameObject("NC");
            var city = root.AddComponent<NC.NcCity>();
            city.baseMat = AssetDatabase.LoadAssetAtPath<Material>(MatPath);
            city.neonMat = AssetDatabase.LoadAssetAtPath<Material>(NeonPath);
            // the Blender models (unity/blender/make_models.py, run by the bot's job "blender_models"); missing = boxes
            city.humanModel = AssetDatabase.LoadAssetAtPath<GameObject>("Assets/NC/Models/human.fbx");
            city.pistolModel = AssetDatabase.LoadAssetAtPath<GameObject>("Assets/NC/Models/pistol.fbx");
            city.rifleModel = AssetDatabase.LoadAssetAtPath<GameObject>("Assets/NC/Models/rifle.fbx");
            city.bladeModel = AssetDatabase.LoadAssetAtPath<GameObject>("Assets/NC/Models/blade.fbx");
            Debug.Log($"[NcBuild] models: human {city.humanModel != null}, pistol {city.pistolModel != null}, rifle {city.rifleModel != null}, blade {city.bladeModel != null}");

            EditorSceneManager.SaveScene(scene, ScenePath);
            EditorBuildSettings.scenes = new[] { new EditorBuildSettingsScene(ScenePath, true) };
            AssetDatabase.SaveAssets();
        }

        // build.json next to index.html: the loader shows the size before the download
        static void WriteInfo(string outDir, string version)
        {
            var build = Path.Combine(outDir, "Build");
            var files = Directory.Exists(build) ? Directory.GetFiles(build) : new string[0];
            long total = files.Sum(f => new FileInfo(f).Length);
            var parts = string.Join(",", files.Select(f => $"\"{Path.GetFileName(f)}\":{new FileInfo(f).Length}"));
            File.WriteAllText(Path.Combine(outDir, "build.json"),
                $"{{\"version\":\"{version}\",\"unity\":\"{Application.unityVersion}\",\"bytes\":{total},\"files\":{{{parts}}}}}");
        }
    }
}
