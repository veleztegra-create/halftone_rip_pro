# Roadmap — Halftone RIP Pro

**Estado:** Propuesta — pendiente de aprobación
**Nota:** este corte entre RC1/v1.1/v2.0 es un criterio editorial mío, armado a partir de lo
discutido hasta ahora. No conozco los plazos ni compromisos reales con GraphXSource — este
documento es un punto de partida para que lo corrijas, no una fecha comprometida.

---

## RC1 (GraphXSource)

Lo mínimo para que GraphXSource pueda probar el producto de punta a punta.

- **Core RIP** (separaciones, halftone vía TIFFSEP/Ghostscript) — preexistente, funcionando.
- **Preview** (visualización de separaciones, edición de color por capa) — preexistente,
  funcionando.
- **Knowledge Engine v1** (`core/knowledge/knowledgeEngine.js`) — matching directo de
  nombre/alias/modificador a hex, sin tokenizer real. Ya integrado a Preview.
- **Diagnostic Protocol + Build Info** (`docs/DIAGNOSTIC_PROTOCOL.md`, `/api/build_info`) —
  no es una feature de producto, pero sin esto no se pueden triagear los reportes de bugs
  que van a empezar a llegar. Considero esto un requisito de RC1, no un nice-to-have.

**Explícitamente fuera de RC1**, aunque ya está diseñado o parcialmente construido:

- Domain Vocabulary (`core/knowledge/domain/`) — aislado, sin consumidor, a propósito.
- Knowledge Pipeline Spec v1.0 / KnowledgeRequest / KnowledgeInfo (contratos) — arquitectura
  congelada para lo que viene, no algo que RC1 necesite ejecutar.

---

## v1.1

Cerrar la brecha entre lo que promete la arquitectura congelada (Fase 0) y lo que el código
realmente hace.

- Sprint A/B del plan de Fase 0: `KnowledgeRequest` y `KnowledgeInfo` como objetos reales
  (sin lógica todavía), reemplazando el string suelto que usa `knowledgeEngine.js` hoy.
- Sprint C: pipeline vacío (Normalize → Tokenize → Recognize → Interpret → Validate) donde
  cada etapa es un passthrough — solo la estructura, sin comportamiento.
- Sprint D: implementación real de cada etapa (Tokenizer, Recognizers, Interpreter),
  consumiendo por fin el Domain Vocabulary que hoy está aislado.
- `pantone` y `semanticRoles` en `KnowledgeInfo` pasan de "campo en la spec" a "campo con
  datos reales".
- Resolución de las preguntas abiertas de `docs/KNOWLEDGE_PIPELINE_SPEC_v1.0.md` sección 6
  (desambiguación HD/Reflective, `silver`/`white`/`black` en `colorDatabase.js`, uso de
  `context`).
- Preflight — mencionado varias veces como idea, todavía sin ningún diseño propio. Necesita
  su propio documento antes de tener una fecha.

---

## v2.0

La visión de "sistema experto" completa — todavía no diseñada en ningún documento, solo
mencionada en discusión.

- Producer Adapters nuevos: Tech Pack, Schematic, Illustrator/AI nativo (hoy solo existe el
  flujo PDF vía Ghostscript).
- Exportadores: Excel, JSON, salida directa a cliente.
- `techniqueDictionary` / `techniqueInterpreter` reales (hoy `technique` en `KnowledgeInfo`
  es un campo reservado, siempre `null`).
- Cualquier heurística que use `context` de forma no trivial (la que hoy está reservada y
  vacía en `KnowledgeRequest`).

---

## Qué falta para que esto deje de ser una propuesta

1. Confirmar o corregir el corte RC1/v1.1/v2.0 de arriba.
2. Decidir si Preflight necesita su propio spec antes de entrar a v1.1, o si se diseña sobre
   la marcha una vez que el pipeline real exista.
3. Poner una fecha o un criterio de salida a RC1 (¿qué tiene que pasar para decir "listo,
   esto se le manda a GraphXSource"?) — este documento no propone ninguna, a propósito.
