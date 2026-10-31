// The colour grade on the camera (Shaders/NcGrade.shader). The material is made by NcBuild so the shader is in the build.
using UnityEngine;

namespace NC
{
    [RequireComponent(typeof(Camera))]
    public class NcGrade : MonoBehaviour
    {
        public Material grade;

        void OnRenderImage(RenderTexture src, RenderTexture dst)
        {
            if (grade == null) { Graphics.Blit(src, dst); return; }
            Graphics.Blit(src, dst, grade);
        }
    }
}
