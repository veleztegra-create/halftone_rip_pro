# Halftone RIP Pro - Python/Flask

Procesamiento profesional de separaciones CMYK con semitonos.
Soporta archivos PostScript (.ps) y PDF de hasta 2GB+ via Ghostscript.

## Requisitos

- Python 3.8+
- Ghostscript (para PS/PDF)

## Instalar Ghostscript

### Windows
1. Descarga desde: https://www.ghostscript.com/download/gsdnld.html
2. Instala el MSI (version AGPL o Artifex)
3. Asegurate que `gswin64c` o `gswin32c` este en el PATH
4. Verifica: abre CMD y ejecuta `gswin64c --version`

### macOS
```bash
brew install ghostscript
```

### Linux (Ubuntu/Debian)
```bash
sudo apt-get install ghostscript
```

### Linux (Fedora)
```bash
sudo dnf install ghostscript
```

## Instalar Python dependencies

```bash
pip install -r requirements.txt
```

## Uso

```bash
cd halftone_rip_pro
python app.py
```

Abre tu navegador en **http://localhost:5000**

## Formatos soportados

- **PostScript (.ps)** - hasta 2GB+
- **PDF (.pdf)**, incluyendo **PDF/X-1a** y **PDF/X-4**

El programa usa el dispositivo `tiffsep` de Ghostscript para extraer las
placas de color reales del archivo, ya sea que vengan:
- **Preseparadas por página** (el flujo clásico de Illustrator/CorelDraw:
  una página = un color), o
- **Compuestas en una sola página** (un PDF/X-4 con CMYK y/o tintas
  planas Pantone mezcladas, como exporta Illustrator por defecto).

En ambos casos, Ghostscript detecta automáticamente cada canal de tinta
con contenido real (CMYK + spot colors/Pantone declarados como
`/Separation` en el PDF) y descarta los canales sin tinta. El nombre de
cada placa (incluyendo nombres de Pantone) viene directo de Ghostscript,
no de un parseo manual del archivo.

> **Por qué no EPS/PNG/JPG/TIFF:** EPS casi siempre llega como una sola
> página de color compuesto sin la estructura de separación que necesita
> tiffsep para distinguir placas reales. Los formatos de imagen
> (PNG/JPG/TIFF) tampoco contienen información de separación de tintas —
> aplicarles un halftone sería una simulación visual sin relación con las
> placas reales de impresión.

## Caracteristicas

- Procesamiento 100% local (no se sube nada a la nube)
- Ghostscript nativo para archivos grandes (sin limitaciones de memoria)
- Separación real de canales vía `tiffsep` (CMYK + spot colors/Pantone),
  compatible con PDF compuesto y PDF/X-1a/X-4
- Semitono profesional con matriz Bayer 8x8
- Angulos CMYK automaticos: C:15, M:75, Y:0, K:45
- Vista previa compuesta con modos Multiply/Screen/Normal
- Analisis de diferencias contra imagen de referencia
- Exportacion ZIP con todas las separaciones
- Modo claro/oscuro

## Arquitectura

```
halftone_rip_pro/
├── app.py              # Servidor Flask + procesamiento
├── templates/
│   └── index.html      # Interfaz de usuario
├── uploads/            # Archivos subidos (temporal)
└── outputs/            # Separaciones generadas
```

## Ventajas

1. **Sin limites de tamaño**: Ghostscript procesa PS/PDF de 2GB+ via streaming
2. **Sin dependencias de navegador**: Todo el procesamiento en Python
3. **Sin CORS**: Servidor local maneja todo
4. **Mas rapido**: NumPy vectorizado vs JavaScript loops
5. **100% offline**: Sin CDNs ni servicios externos
