# Diagnostic Protocol

**Depende de:** ADR-010 (`docs/ADR/ADR-010.md`) — un bug, una hipótesis.

Cuando alguien (humano o IA) reporta un problema en Halftone RIP Pro, se sigue este orden.
No se salta ningún paso, y no se avanza al siguiente sin completar el anterior.

## 1. Identificar la Build

Antes de investigar nada, confirmar exactamente qué código está corriendo el servidor que
reportó el problema:

- `GET /api/build_info` — devuelve `version`, `build_label`, `generated_at`, y
  `knowledge_engine_hash` (hash automático del contenido de `static/js/core/knowledge/`,
  calculado al arrancar el servidor).
- El mismo dato aparece en el footer de la UI y en la consola del navegador al cargar la
  página (`console.log('[BUILD] ...')`).

`knowledge_engine_hash` es la fuente de verdad más confiable: `build_label` y `version` se
bumpean a mano en `BUILD_INFO.json` en cada release, así que pueden quedar desactualizados si
alguien lo olvida — el hash no puede mentir sobre si el código de `core/knowledge/` cambió.

## 2. Reproducir el problema paso a paso

Escribir los pasos exactos, no una descripción general. "El botón de analizar se queda
trabado" no es reproducible. "Subí el archivo X, hice click en Analizar, esperé N segundos,
el botón sigue en estado disabled" sí lo es.

## 3. Confirmar que el código ejecutado corresponde a esa Build

Sin caché ni ZIP antiguos. Esto es exactamente lo que faltó la primera vez que se aplicó este
protocolo (informalmente): un log de servidor mostraba requests a
`core/knowledge/domain/*.js`, archivos que el `index.html` actual ya no carga. La explicación
más simple no era un bug — era que el servidor corriendo todavía era una build anterior. El
paso 1 (`/api/build_info`) es lo que permite confirmar o descartar esto en segundos en vez de
investigar a ciegas.

## 4. Aislar una única hipótesis

De todas las explicaciones posibles, elegir **una**. Si hay varias candidatas razonables,
ordenarlas por costo de descarte (la más barata de confirmar/descartar primero) y anotar las
demás para después — no investigarlas todas en paralelo.

Ejemplo del caso real que originó esta ADR:

```
Hipótesis 1: el backend está lento (proceso en curso, respuesta tardía)
Hipótesis 2: se cargó un ZIP viejo (build desactualizada)
Hipótesis 3: hay caché del navegador
```

No se mezclan. Se confirma o descarta una antes de tocar la siguiente.

## 5. Buscar evidencia antes de modificar código

Logs del servidor, `/api/build_info`, la consola del navegador, el estado real de la UI.
Ningún cambio de código se hace como "a ver si esto lo arregla" antes de tener evidencia de
que la hipótesis aislada en el paso 4 es la correcta.

## 6. Solo entonces decidir si hay que corregir algo

Si la hipótesis confirmada es "build vieja" o "caché", el problema no es del código — no se
toca nada, se documenta la causa y se cierra. Si la hipótesis confirmada es un bug real,
recién ahí se corrige, y la corrección apunta puntualmente a esa causa confirmada, no a un
rediseño general motivado por la sospecha inicial.

---

## Por qué existe esto

Diez reportes de bugs no se pueden resolver por intuición. Sin un proceso repetible, se
corre el riesgo de (a) arreglar algo que en realidad era una versión vieja del programa, o
(b) introducir cambios innecesarios porque se mezclaron varias hipótesis en una sola
investigación. Este protocolo es la infraestructura de ingeniería para separar "problema
real" de "problema de entorno" antes de que GraphXSource empiece a mandar reportes.
