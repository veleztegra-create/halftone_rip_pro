// ============================================================
// KNOWLEDGE ENGINE - Coordinator
// Punto de entrada público del Knowledge Engine. Es lo único
// que el resto de la app (rip.js, preview.js) debería llamar.
//
// IMPORTANTE - alcance de este sprint:
// Esto todavía NO es el motor tokenizer descrito en la revisión
// de arquitectura (texto -> tokens -> knowledge -> resultado).
// Por ahora resolve() envuelve el matching existente (exacto /
// alias / modificador / aproximado) y lo empaqueta como
// KnowledgeInfo. El tokenizer real (colorInterpreter,
// techniqueInterpreter) es la tarea del próximo sprint, sobre
// esta misma base.
// ============================================================

const KnowledgeEngine = (function () {

    function resolve(searchTerm, strategy = UNDEFINED_COLOR_POLICY.defaultStrategy) {
        const originalName = searchTerm || '';
        const normalizedName = originalName.toLowerCase().trim();

        if (!normalizedName) {
            return createKnowledgeInfo({
                originalName,
                normalizedName,
                displayColor: UNDEFINED_COLOR_POLICY.defaultColor.display,
                confidence: 0,
                warnings: ['Entrada vacía; se usó un color por defecto.'],
                metadata: { strategy: 'use_default', isFallback: true }
            });
        }

        // 1. Match exacto (clave, alias, o alias + modificador)
        const smart = ColorMatcher.findColorSmart(normalizedName);
        if (smart) {
            return createKnowledgeInfo({
                originalName,
                normalizedName,
                baseColor: smart.key,
                displayColor: smart.displayColor,
                modifier: smart.appliedModifier,
                confidence: 1,
                metadata: { strategy: 'exact_match' }
            });
        }

        // 2. Sin match: aplicar la política de colores no definidos
        const resolved = UndefinedColorPolicy.resolve(normalizedName, strategy, ColorMatcher);
        return createKnowledgeInfo({
            originalName,
            normalizedName,
            baseColor: resolved.baseColor,
            displayColor: resolved.displayColor,
            modifier: resolved.modifier,
            confidence: resolved.confidence,
            warnings: resolved.warnings,
            metadata: resolved.metadata
        });
    }

    return { resolve };
})();
