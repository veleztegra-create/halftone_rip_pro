// ============================================================
// KNOWLEDGE ENGINE - Modifier Detector
// Única responsabilidad: detectar un modificador tonal
// ("claro", "dark", "vibrant"...) dentro de un texto.
// No sabe nada de colores ni de la base de datos.
// ============================================================

const MODIFIER_KEYWORDS = {
    light: ["claro", "light", "luminoso", "brillante", "pálido"],
    dark: ["oscuro", "dark", "profundo", "intenso", "fuerte"],
    pale: ["muy claro", "pastel", "pálido", "suave", "diluido", "pale"],
    deep: ["muy oscuro", "profundo", "intenso", "saturado", "deep"],
    vibrant: ["vibrante", "vivo", "intenso", "brillante", "eléctrico", "vibrant"]
};

const ModifierDetector = {
    // Detecta el primer modificador presente en `text` y devuelve
    // el texto restante (sin el modificador) más el modificador detectado.
    detect: (text) => {
        let detectedModifier = null;
        let baseText = text;

        // Ordenar por longitud de keyword (para detectar "muy claro" antes que "claro")
        const sortedModifiers = Object.entries(MODIFIER_KEYWORDS)
            .sort((a, b) => b[1].join(' ').length - a[1].join(' ').length);

        for (const [modifier, keywords] of sortedModifiers) {
            for (const keyword of keywords) {
                if (text.includes(keyword)) {
                    detectedModifier = modifier;
                    baseText = text.replace(new RegExp(keyword, 'g'), '').trim();
                    break;
                }
            }
            if (detectedModifier) break;
        }

        return { baseTerm: baseText || text, modifier: detectedModifier };
    }
};
