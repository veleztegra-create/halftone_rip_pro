# KnowledgeRequest Specification v1.0

**Estado:** Borrador — pendiente de aprobación
**Tipo de decisión:** Arquitectura (no implementación)
**Parte de:** Fase 0 — congelamiento de arquitectura, antes de Sprint A (`docs/ROADMAP.md`)

---

## 1. Qué es

`KnowledgeRequest` es la única forma válida de entrada al Knowledge Engine. Todo Producer
Adapter (PDF, Illustrator, Tech Pack, Schematic, input manual de UI) construye uno de estos
y nada más — no hay una segunda puerta de entrada, no hay overloads con distinta forma.

---

## 2. Shape

```
KnowledgeRequest
{
    text: string            // obligatorio
    source: string           // obligatorio
    context: object | null   // opcional, default null
    metadata: object | null  // opcional, default null
    timestamp: string | null // opcional, default null
    version: string           // obligatorio, default "1.0"
}
```

---

## 3. Campos, uno por uno

### `text` (string, obligatorio)

El texto plano ya extraído por el Producer Adapter. Esto es lo único que el Knowledge
Engine interpreta.

- Nunca una ruta de archivo.
- Nunca un buffer binario.
- Nunca un objeto del SDK de origen (un objeto de PDF.js, un layer de Illustrator, etc.).
- Es responsabilidad exclusiva del Producer Adapter reducir su formato nativo a este string
  antes de construir el `KnowledgeRequest`.

### `source` (string, obligatorio)

Identifica **qué Producer Adapter** generó este request. Ejemplos: `"pdf-spot-reader"`,
`"illustrator-adapter"`, `"preview-ui-manual-input"`.

- Uso previsto: trazabilidad y debugging ("¿de dónde vino este request que falló?").
- **Ningún stage del Knowledge Engine (Tokenizer, Recognizers, Vocabulary, Interpreter) lee
  este campo para tomar decisiones de interpretación hoy.** Que un texto llegue desde PDF o
  desde Illustrator no cambia cómo se interpreta — el idioma del dominio es el mismo
  (ADR-006). Si en el futuro se decide que `source` sí debe influir en algo, es una decisión
  nueva y explícita, no un uso silencioso de este campo.

### `context` (object | null, opcional)

Señales que **podrían** influir en la interpretación en el futuro — tipo de fuente, línea de
producto, cliente, lo que sea que el Interpreter algún día necesite para desambiguar mejor.

- Hoy: vacío, no leído por ningún stage. Existe para no tener que romper el contrato el día
  que el Interpreter necesite señales adicionales.
- El día que algo empiece a leer `context`, ese uso se documenta en esta spec — no aparece
  implícitamente en una PR de otra cosa.

### `metadata` (object | null, opcional)

Bookkeeping puramente descriptivo sobre el request en sí — un id de request, en qué panel de
la UI se generó, lo que sea útil para logs.

- **A diferencia de `context`, `metadata` nunca debe influir en la interpretación.** Ninguna
  etapa, ni hoy ni en el futuro, puede leer `metadata` para cambiar un resultado. Es
  exclusivamente para logging/tracing/telemetría.
- Si algo en `metadata` empieza a afectar un resultado, eso es un bug de arquitectura, no una
  función nueva — moverlo a `context` explícitamente en su lugar.

### `timestamp` (string ISO 8601 | null, opcional)

Cuándo se creó el request. Solo para telemetría — el significado de un color no depende de
cuándo se pidió. Ninguna etapa lo usa para decidir nada.

### `version` (string, obligatorio, default `"1.0"`)

La versión de **este contrato** (`KnowledgeRequest`), no de la app ni del build (eso ya lo
cubre `BUILD_INFO.json` / `/api/build_info`, que es un concepto distinto — identifica qué
código está corriendo, no qué forma tiene el dato). Sirve para que, si el shape de
`KnowledgeRequest` cambia en el futuro de forma incompatible, el Knowledge Engine pueda
detectar requests con una versión de contrato vieja y decidir qué hacer, en vez de fallar
silenciosamente contra un shape que ya no es el actual.

---

## 4. Tabla resumen: ¿quién puede influir en la interpretación?

| Campo       | ¿Puede influir en la interpretación? | Uso previsto                          |
|-------------|:-------------------------------------:|----------------------------------------|
| `text`      | Sí — es el input mismo                | Lo único que el Interpreter interpreta |
| `source`    | No (hoy)                              | Trazabilidad / debugging               |
| `context`   | Sí (a futuro, cuando se decida)       | Señales semánticas reservadas          |
| `metadata`  | **Nunca**                             | Logging / tracing / telemetría         |
| `timestamp` | **Nunca**                             | Telemetría                             |
| `version`   | No                                    | Compatibilidad del contrato en sí      |

---

## 5. API pública: `resolve` vs `resolveMany`

**Propuesta pendiente de aprobación:**

`KnowledgeRequest` es siempre **un texto atómico** — un spot color, un nombre de técnica.
Nunca representa un archivo completo ni un lote. Para casos como Preflight, que necesita
validar decenas de nombres de un archivo de una sola vez, la propuesta es:

```
KnowledgeEngine.resolve(request: KnowledgeRequest): KnowledgeInfo
KnowledgeEngine.resolveMany(requests: KnowledgeRequest[]): KnowledgeInfo[]
```

`resolveMany` es un wrapper delgado: itera y llama `resolve()` por cada uno. No comparte
estado entre llamadas, no es un contrato distinto, no cambia el shape de `KnowledgeInfo`. El
Knowledge Engine nunca necesita saber qué es un "archivo" o un "lote" — solo sabe resolver un
texto a la vez, siempre.

Si la intención original para Preflight era otra (por ejemplo, que el motor sí necesite ver
varios requests juntos para detectar inconsistencias entre ellos — dos colores con el mismo
nombre resolviendo distinto dentro del mismo archivo, por ejemplo), **eso es un contrato
distinto** y hay que decirlo acá antes de Sprint A, no después.
