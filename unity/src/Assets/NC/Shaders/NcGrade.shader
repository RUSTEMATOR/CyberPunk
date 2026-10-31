// The colour grade of the Unity client (owner request 31.10: «дизайн в стиле Найт-Сити и цветокор», docs/PLAN_FIGHT3D.md):
// the look of Cyberpunk 2077's nights. Teal in the shadows, warm sodium yellow in the lights, a little more saturation and
// contrast, lifted blacks, a slight colour split towards the edges, a vignette and film grain. One full-screen pass.
Shader "NC/Grade"
{
    Properties { _MainTex ("", 2D) = "white" {} _Strength ("Strength", Float) = 1 }
    SubShader
    {
        ZTest Always Cull Off ZWrite Off
        Pass
        {
            CGPROGRAM
            #pragma vertex vert_img
            #pragma fragment frag
            #include "UnityCG.cginc"
            sampler2D _MainTex;
            float _Strength;

            fixed4 frag (v2f_img i) : SV_Target
            {
                float2 uv = i.uv;
                float2 d = uv - 0.5;
                float r2 = dot(d, d);
                float3 src;
                src.r = tex2D(_MainTex, uv + d * 0.0025).r;
                src.g = tex2D(_MainTex, uv).g;
                src.b = tex2D(_MainTex, uv - d * 0.0025).b;
                float3 c = src;
                float l = dot(c, float3(0.299, 0.587, 0.114));
                c += float3(-0.02, 0.07, 0.09) * (1 - l) * 0.9;   // teal shadows
                c += float3(0.12, 0.07, -0.05) * l * 0.9;         // sodium highlights
                c = lerp(float3(l, l, l), c, 1.28);               // saturation
                c = (c - 0.5) * 1.1 + 0.5;                        // contrast
                c = c * 0.93 + float3(0.028, 0.02, 0.04);         // lifted, slightly violet blacks
                c *= 1 - r2 * 1.1;                                // vignette
                float n = frac(sin(dot(uv * _ScreenParams.xy + _Time.y * 61.0, float2(12.9898, 78.233))) * 43758.5453);
                c += (n - 0.5) * 0.03;                            // grain
                return fixed4(lerp(src, saturate(c), _Strength), 1);
            }
            ENDCG
        }
    }
    Fallback Off
}
