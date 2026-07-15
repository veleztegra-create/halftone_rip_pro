# KnowledgeInfo Specification v1.0

**Estado:** Borrador — pendiente de aprobación
**Tipo de decisión:** Arquitectura (no implementación)
**Parte de:** Fase 0 — congelamiento de arquitectura, antes de Sprint B (`docs/ROADMAP.md`)

**Nota de estado importante:** este documento describe **qué representa** cada campo, no
cómo se calcula — ese es trabajo del Interpreter (implementación, no arquitectura). Dos
campos de acá (`pantone`, `semanticRoles`) **no existen todavía** en
`core/knowledge/KnowledgeInfo.js` (el v1 implementado). Esta spec es el objetivo de Sprint
B, no una descripción del código actual. La diferencia entre lo que sigue y el código de hoy
queda marcada explícitamente en cada campo.

---

## 1. Qué es

`KnowledgeInfo` es la única estructura de datos que un Consumer (Preview, Preflight, futuros
exportadores) debería conocer. Es lo que el Knowledge Engine devuelve por cada
`KnowledgeRequest` resuelto. Es **inmutable** una vez creado.

---

## 2. Shape

```
KnowledgeInfo
{
    originalName: string
    normalizedName: string

    baseColor: string | null
    displayColor: string | null
    modifier: string | null

    technique: string | null
    pantone: string | null

    confidence: number
    warnings: string[]
    semanticRoles: string[]
    metadata: object
}
```

---

## 3. Campos, uno por uno

### `originalName` (string) — *ya implementado*

El `text` del `KnowledgeRequest`, sin modificar. Trazabilidad hacia el input original tal
cual llegó, para poder mostrarle al usuario o a un log exactamente qué se tipeó/extrajo.

### `normalizedName` (string) — *ya implementado*

Versión normalizada (lowercase + trim) usada internamente para matching. Se expone porque a
veces un consumidor (o un log de debugging) necesita ver qué es lo que realmente se comparó
contra el vocabulario, no el string crudo.

### `baseColor` (string | null) — *ya implementado*

La familia de color interna resuelta — una clave de `COLOR_DATABASE` (p. ej. `"yellow"`).
Es la paleta, no un valor hex. `null` si no se resolvió ningún color base.

### `displayColor` (string | null) — *ya implementado*

El hex concreto a renderizar. Puede ser distinto del hex "puro" de `baseColor` si hay un
`modifier` aplicado (p. ej. `baseColor: "yellow"` + `modifier: "dark"` → `displayColor` es el
hex de amarillo oscuro, no el amarillo base).

### `modifier` (string | null) — *ya implementado*

Intensidad tonal detectada: `"light"` | `"dark"` | `"pale"` | `"deep"` | `"vibrant"` | `null`.

### `technique` (string | null) — *reservado, ya existe el campo, siempre `null` hoy*

La técnica de impresión resuelta como decisión final (p. ej. `"metallic"`, `"puff"`,
`"laser"`). Es un valor **singular e interpretado** — la conclusión del Interpreter sobre
cuál es *la* técnica, después de resolver cualquier ambigüedad. Sigue `null` hasta que exista
un intérprete de técnicas real.

### `pantone` (string | null) — **NUEVO, no existe en el v1 implementado**

Un código estilo Pantone reconocido en el texto de origen (p. ej. `"872C"`), cuando
corresponde. `pantone` y `baseColor` **no son mutuamente excluyentes** — un mismo
`KnowledgeRequest` puede resolver ambos a la vez: `baseColor`/`displayColor` para uso interno
de paleta (p. ej. pintar un swatch en Preview), y `pantone` como referencia externa de
producción (lo que va a un Tech Pack o a una orden de producción). `null` si no hay código
reconocido en el texto.

### `confidence` (number, 0..1) — *ya implementado*

Qué tan seguro está el Interpreter de esta resolución. `1` = match exacto contra el
vocabulario. Baja cuando se usó aproximación (`mapped_to_nearest`) o fallback
(`use_default`). No es una probabilidad calibrada — es una señal ordinal para que un
consumidor decida cuánto confiar en el resultado sin tener que parsear `warnings`.

### `warnings` (string[]) — *ya implementado*

Mensajes legibles para humanos sobre por qué `confidence` no es `1`, o qué se asumió al
resolver. Vacío si no hubo advertencias. Estos mensajes están pensados para mostrarse
directamente a un usuario o loggearse — no para que un consumidor haga `warnings.includes(...)`
y ramifique lógica sobre el texto exacto (para eso está `metadata`).

### `semanticRoles` (string[]) — **NUEVO, no existe en el v1 implementado**

Las categorías del Domain Vocabulary (`DOMAIN_CATEGORIES`) detectadas en el texto de origen,
en orden — por ejemplo, para `"Dark Athletic Gold"`:
`["Modifier", "ColorQualifier", "BaseColor"]`.

Este campo es el puente entre el Domain Vocabulary (hoy aislado en `core/knowledge/domain/`,
sin ningún consumidor — ver revisión de integración anterior) y el resultado final. No es
redundante con `technique`/`modifier`: `semanticRoles` es la traza cruda de categorización
por token, mientras que `technique`/`modifier`/`baseColor` son las conclusiones finales ya
interpretadas y desambiguadas. Un consumidor que quiera explicar *por qué* el Interpreter
llegó a una conclusión (por ejemplo, un modo debug en Preview) lee `semanticRoles`; un
consumidor que solo quiere pintar un swatch lee `displayColor` y listo.

### `metadata` (object) — *ya implementado*

Bag abierto para lo que no tiene campo propio todavía: qué estrategia de
`UndefinedColorPolicy` se usó, `matchScore` de una aproximación, etc. Mismo espíritu que hoy
— extensible sin tener que tocar el shape principal cada vez que aparece un dato nuevo.

---

## 4. Qué NO es este documento

No define cómo el Interpreter calcula `confidence`, cómo desambigua `semanticRoles`
superpuestos, ni cómo se reconoce un código Pantone. Eso es Sprint C/D (implementación). Este
documento congela únicamente **qué significa cada campo una vez que existe** — para que
cualquier implementación futura del Interpreter, sea con reglas, con NLP, o con lo que sea,
tenga que producir exactamente este shape con este significado.
