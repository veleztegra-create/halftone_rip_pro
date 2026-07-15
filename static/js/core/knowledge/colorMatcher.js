// ============================================================
// KNOWLEDGE ENGINE - Color Matcher
// Única responsabilidad: buscar colores (exacto, alias, con
// modificador, o aproximado). No decide qué hacer cuando no
// encuentra nada — eso es responsabilidad de la policy.
// Depende de: colorDatabase.js, aliases.js, modifierDetector.js
// ============================================================

const ColorMatcher = {
    // Búsqueda exacta por clave o alias
    findColor: (searchTerm) => {
        const normalized = searchTerm.toLowerCase().trim();

        for (const [key, color] of Object.entries(COLOR_DATABASE)) {
            const aliases = COLOR_ALIASES[key] || [];
            if (key === normalized || aliases.some(alias => alias === normalized)) {
                return { key, ...color };
            }
        }
        return null;
    },

    // Búsqueda con detección de modificador ("azul claro", "yellow vibrant")
    findColorSmart: (searchTerm) => {
        if (!searchTerm || searchTerm.trim() === '') return null;

        const normalized = searchTerm.toLowerCase().trim();
        const { baseTerm, modifier } = ModifierDetector.detect(normalized);

        let baseColor = ColorMatcher.findColor(baseTerm);

        if (!baseColor) {
            // Búsqueda palabra por palabra dentro del término
            const words = baseTerm.split(' ');
            for (const word of words) {
                baseColor = ColorMatcher.findColor(word);
                if (baseColor) break;
            }
        }

        if (!baseColor) return null;

        if (modifier && baseColor.modifiers[modifier]) {
            return {
                ...baseColor,
                displayColor: baseColor.modifiers[modifier],
                appliedModifier: modifier,
                isModified: true
            };
        }

        return {
            ...baseColor,
            appliedModifier: null,
            isModified: false
        };
    },

    // Color base + modificador explícito
    getColorWithModifier: (colorName, modifier) => {
        const color = ColorMatcher.findColor(colorName);
        if (!color || !color.modifiers[modifier]) return null;

        return {
            ...color,
            displayColor: color.modifiers[modifier],
            appliedModifier: modifier,
            isModified: true
        };
    },

    // Aproximación por score semántico cuando no hay match exacto
    findNearestColor: (searchTerm) => {
        const normalized = searchTerm.toLowerCase().trim();

        const smartResult = ColorMatcher.findColorSmart(searchTerm);
        if (smartResult) return smartResult;

        const words = normalized.split(' ');
        let bestMatch = null;
        let bestScore = 0;

        for (const [key, color] of Object.entries(COLOR_DATABASE)) {
            let score = 0;
            const aliases = COLOR_ALIASES[key] || [];

            for (const word of words) {
                if (!word) continue;
                if (aliases.some(alias => alias.includes(word))) {
                    score += 2;
                }
                if (color.semantic && color.semantic.family && word.includes(color.semantic.family)) {
                    score += 3;
                }
            }

            if (score > bestScore) {
                bestScore = score;
                bestMatch = { key, ...color };
            }
        }

        if (bestMatch && bestScore > 0) {
            return {
                ...bestMatch,
                isApproximation: true,
                originalSearch: searchTerm,
                matchScore: bestScore
            };
        }

        return null;
    }
};
