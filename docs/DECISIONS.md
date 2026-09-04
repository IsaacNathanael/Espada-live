# Frozen decisions

1. Use OpenDrift as the primary drift framework. NOAA GNOME is prior art and operational-feasibility evidence, not a second implementation target.
2. Treat reverse drift as a probability distribution, never a single exact release point.
3. Add candidate-specific forward simulation before final vessel ranking.
4. Treat AIS gaps as supporting evidence only. They never prove deliberate behaviour.
5. Call the output an auditable investigation brief, not a prosecutable dossier.
6. Keep synthetic and real-case evaluation results separate.
7. Keep the answer key outside all inference function signatures.
8. Do not merge AI-generated code until tests pass and an integrator reviews the change.
9. Use a ResNet34-based U-Net for Sentinel-1 VV slick segmentation only after dataset/split verification; keep adaptive thresholding as the working fallback.
10. Attribution remains physics and transparent evidence scoring. Never describe its score as an ML probability or guilt probability.
