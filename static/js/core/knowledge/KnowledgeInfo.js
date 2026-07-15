// ============================================================
// KNOWLEDGE ENGINE - KnowledgeInfo
// La única estructura de datos que el resto del programa debería
// conocer. Todo lo demás (colorDatabase, aliases, matcher,
// policies) es implementación interna del Knowledge Engine.
//
// `technique` existe como campo reservado desde ya: hoy siempre
// es null porque no hay techniqueDictionary/techniqueInterpreter
// todavía (ese es un sprint futuro), pero el shape ya lo soporta
// para no tener que romper consumidores cuando se agregue.
//
// Lo mismo aplica a `metadata`: es un bag abierto para lo que
// vaya apareciendo (fabric, customer, pantoneFamily,
// estimatedCoverage, process, recommendedLPI, recommendedMesh...)
// sin tener que tocar el shape principal cada vez.
// ============================================================

function createKnowledgeInfo(overrides = {}) {
    return {
        originalName: overrides.originalName ?? '',
        normalizedName: overrides.normalizedName ?? '',

        baseColor: overrides.baseColor ?? null,       // p.ej. "yellow"
        displayColor: overrides.displayColor ?? null, // hex a renderizar

        modifier: overrides.modifier ?? null,          // "light" | "dark" | "pale" | "deep" | "vibrant" | null

        technique: overrides.technique ?? null,        // reservado, sprint futuro

        confidence: overrides.confidence ?? 0,          // 0..1
        warnings: overrides.warnings ?? [],
        metadata: overrides.metadata ?? {}
    };
}
