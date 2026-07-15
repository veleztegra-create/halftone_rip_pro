# Knowledge Pipeline Specification v1.0

**Estado:** Borrador — pendiente de aprobación
**Tipo de decisión:** Arquitectura (no implementación)
**Depende de:** ADR-005 (no modificar código estable por advertencias cosméticas), ADR-006 (el Knowledge Engine traduce texto a vocabulario del dominio antes de ejecutar cualquier algoritmo de interpretación)

**Misión del sistema, como referencia para toda decisión de esta spec:**
> Halftone RIP Pro no interpreta archivos; interpreta conocimiento de producción textil.

**Documentos relacionados (Fase 0 — congelamiento de arquitectura):**
- `docs/KNOWLEDGE_REQUEST_SPEC_v1.0.md` — contrato completo de `KnowledgeRequest`
- `docs/KNOWLEDGE_INFO_SPEC_v1.0.md` — contrato completo de `KnowledgeInfo`
- `docs/ADR/` — índice de decisiones de arquitectura
- `docs/ROADMAP.md` — qué entra en RC1 vs v1.1 vs v2.0

---

## 1. Alcance

Este documento congela el **contrato** del Knowledge Pipeline: qué entra, qué sale, quién le habla a quién, y qué le está prohibido hacer a cada etapa.

No congela implementación. El tokenizer, los recognizers, y el interpreter pueden reescribirse cuantas veces haga falta — con reglas, con un modelo NLP, con lo que sea — mientras respeten el contrato descrito acá. Esa es la distinción entre Decisión de Arquitectura y Decisión de Implementación que ya establecimos: este documento es lo primero, nunca lo segundo.

Una vez aprobado, cualquier cambio a este documento requiere su propia revisión — no se modifica implícitamente agregando un campo "por las dudas" en una PR de otra cosa.

---

## 2. Contrato: `KnowledgeRequest`

Es lo único que un Producer Adapter le puede pasar al Knowledge Engine. No hay una segunda puerta de entrada.

**Definición completa, campo por campo:** `docs/KNOWLEDGE_REQUEST_SPEC_v1.0.md`.

Resumen para este documento: `KnowledgeRequest` es siempre **un texto atómico** (un spot color, un nombre de técnica), nunca un archivo ni un lote. `KnowledgeEngine.resolveMany()` existe como wrapper delgado sobre `resolve()` para casos como Preflight — no es un contrato distinto (detalle y justificación en la spec dedicada).

---

## 3. Contrato: `KnowledgeInfo`

**Definición completa, campo por campo (qué representa cada uno, no cómo se calcula):** `docs/KNOWLEDGE_INFO_SPEC_v1.0.md`.

Dos reglas de este contrato que valen la pena repetir acá porque son las que más se van a violar por accidente:

- Es **inmutable una vez creado**. Ningún consumidor lo modifica in-place; si necesita una versión derivada, crea un objeto nuevo.
- Es **la única estructura de datos que un consumidor (Preview, Preflight, futuros exportadores) debería conocer**. Ningún consumidor importa `colorDatabase.js`, `domainVocabulary.js`, ni ningún archivo interno de `core/knowledge/` — solo llama `KnowledgeEngine.resolve()` / `resolveMany()` y lee el `KnowledgeInfo` resultante.

**Nota de estado:** la spec dedicada incluye dos campos (`pantone`, `semanticRoles`) que todavía no existen en `core/knowledge/KnowledgeInfo.js`. Ese documento describe el objetivo de Sprint B, no el estado actual del código.

---

## 4. Etapas del pipeline

```
Producer Adapter → KnowledgeRequest → [Tokenizer → Recognizers → Vocabulary → Interpreter] → KnowledgeInfo → Consumer
                                        └──────────────── Knowledge Engine ────────────────┘
```

### 4.1 Producer Adapter (fuera del Knowledge Engine)

Uno por cada formato de origen: PDF, Illustrator, Tech Pack, Schematic, y lo que aparezca después.

- **Entra:** el archivo/objeto nativo de ese formato.
- **Sale:** uno o más `KnowledgeRequest`.
- **Responsabilidad:** saber leer *ese* formato específico y extraer texto plano de él.
- **No sabe:** qué significa el texto. No decide si algo es un color, una técnica, ni nada del dominio.

### 4.2 Tokenizer

- **Entra:** `text` de un `KnowledgeRequest`.
- **Sale:** lista ordenada de tokens (strings).
- **Responsabilidad:** normalizar (lowercase, trim) y separar el texto en unidades. Nada más.
- **No sabe:** qué significa ningún token. No consulta `COLOR_DATABASE`, `DOMAIN_VOCABULARY`, ni `aliases.js`.

### 4.3 Recognizers

- **Entra:** un token individual.
- **Sale:** `{ matched: boolean, category, value }` o `null`.
- **Responsabilidad:** reconocer clases **abiertas** que no se pueden enumerar en un diccionario — hoy, códigos estilo Pantone (`domainPatterns.js`).
- **No sabe:** nada sobre otros tokens ni sobre el resto del texto. Es una función pura de un solo token.

### 4.4 Vocabulary

- **Entra:** un token individual.
- **Sale:** candidatos de categoría/color para ese token (`DOMAIN_VOCABULARY`, `ColorMatcher`/`aliases.js`).
- **Responsabilidad:** reportar **qué podría ser** ese token según el diccionario cerrado del dominio. Puede reportar más de una categoría posible (ver `HD`, `Reflective` en `domainVocabulary.js`).
- **No decide** cuál de esas categorías es la correcta cuando hay ambigüedad. Eso es del Interpreter.

### 4.5 Interpreter

- **Entra:** la secuencia de tokens ya anotados por Recognizers + Vocabulary.
- **Sale:** un `KnowledgeInfo` completo.
- **Responsabilidad:** es la **única** etapa autorizada a tomar decisiones finales — desambiguar (`HD` → ¿Technique o Effect?), aplicar `UndefinedColorPolicy` cuando nada matchea, calcular `confidence`, ensamblar el `KnowledgeInfo`.
- Hoy (`knowledgeEngine.js` v1) esta etapa está simplificada: no hay Tokenizer/Recognizers reales todavía, el matching es directo sobre el string completo. Ese es exactamente el gap que Sprint 3 va a cerrar — este documento no lo esconde.

### 4.6 Consumer

Preview, Preflight, y futuros exportadores (Tech Pack, Excel, JSON, cliente).

- **Entra:** `KnowledgeInfo`.
- **Responsabilidad:** decidir qué hacer con esa información (pintar un swatch, validar antes de imprimir, generar un reporte). Cada consumidor puede interpretar `KnowledgeInfo` distinto para su propio propósito.
- **No sabe:** cómo se llegó a ese `KnowledgeInfo`. No re-implementa lógica de matching ni le pregunta al usuario "¿qué color es esto?" por su cuenta — eso ya lo resolvió el Knowledge Engine (o falló explícitamente vía `warnings`/`confidence: 0`).

---

## 5. Prohibiciones explícitas

- Un **Recognizer** o la **Vocabulary** nunca leen el archivo de origen (PDF, AI, etc.) directamente. Solo ven el token que les llega. Si un Recognizer necesita "ver el PDF", es una señal de que la lógica pertenece al Producer Adapter, no acá.
- Un **Producer Adapter** nunca contiene lógica de interpretación de dominio (nada de `if (fileType === 'pdf') color = 'gold'`). Su única salida válida es texto plano dentro de un `KnowledgeRequest`.
- Ningún **Consumer** importa archivos internos de `core/knowledge/` (`colorDatabase.js`, `domainVocabulary.js`, `colorMatcher.js`, etc.). La única puerta pública es `KnowledgeEngine.resolve()` / `resolveMany()`.
- Ninguna etapa **muta** un `KnowledgeInfo` después de creado.
- El Knowledge Engine es **síncrono y sin I/O** (sin `fetch`, sin acceso a disco, sin llamadas de red). Tiene que poder correr en el hot path de UI (p. ej. mientras el usuario tipea un color en Preview) sin volverse una fuente de latencia o de fallos de red.
- Ninguna etapa interna (Tokenizer, Recognizers, Vocabulary) decide la **resolución final** de nada — solo el Interpreter tiene esa autoridad. Esto es lo que evita que mañana aparezca una segunda fuente de verdad compitiendo con el Interpreter.

---

## 6. Preguntas abiertas (no cerradas por este documento)

Las preguntas de contrato específicas de `KnowledgeRequest` y `KnowledgeInfo` (p. ej. `resolveMany`, el shape de `pantone`/`semanticRoles`) viven en sus respectivas specs dedicadas, no acá. Las que quedan abiertas a nivel *pipeline*:

1. **Desambiguación de `HD`/`Reflective`:** hoy `domainVocabulary.js` declara ambas categorías posibles con un `primary`. ¿El Interpreter debería poder anular el `primary` según contexto (palabra vecina), o el `primary` declarado en el vocabulario es la política final?
2. **`silver`, `white`, `black`:** están en el vocabulario pero no en `colorDatabase.js`. ¿Se agregan en el sprint del tokenizer o es un sprint de datos aparte?
3. **`context` en `KnowledgeRequest`:** está reservado y vacío a propósito. ¿Hay algo concreto que ya sepamos que va a necesitar (p. ej. `sourceType`), o se deja abierto hasta que aparezca la primera necesidad real?

---

## 7. Qué NO cambia con este documento

- No se escribe Tokenizer, Recognizers, ni Interpreter todavía. Este documento es el contrato que esas implementaciones futuras van a tener que cumplir.
- No se integra `domain/` a `index.html`. Sigue aislado (ver revisión anterior).
- El `knowledgeEngine.js` actual sigue funcionando exactamente igual para Preview. Este documento no rompe nada existente — describe hacia dónde crece.
