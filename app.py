#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Halftone RIP Pro - SERIGRAFÍA PROFESIONAL ULTRA RÁPIDA
Con Numba JIT + Procesamiento Paralelo + Extracción Avanzada de Spot Colors
Soporte: CMYK + Pantone + Colores directos vía %%PlateColor:
"""

import os
import io
import base64
import zipfile
import shutil
import subprocess
import sys
import re
import hashlib
import colorsys
from datetime import datetime
from threading import Lock
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from functools import lru_cache
from collections import OrderedDict
import multiprocessing

# Configurar stdout y stderr para usar UTF-8 y evitar errores de codificación en Windows
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8', errors='backslashreplace')
if hasattr(sys.stderr, 'reconfigure'):
    sys.stderr.reconfigure(encoding='utf-8', errors='backslashreplace')


from flask import Flask, render_template, request, jsonify, send_file
from werkzeug.utils import secure_filename
from PIL import Image
import numpy as np

# Desactivar el límite de "decompression bomb" de Pillow.
# Ese límite (128 megapíxeles por defecto) está pensado para servicios web
# que reciben uploads de desconocidos. Este programa es una herramienta de
# escritorio local: el único que "sube" archivos es el propio usuario,
# procesando sus propios diseños en su propia máquina. Placas de impresión
# textil reales (lonas grandes a 600-1200 DPI) superan ese límite por
# completo sin tener nada de malicioso - es justo el caso de uso normal
# de este programa.
Image.MAX_IMAGE_PIXELS = None

# Intentar importar numba para aceleración
try:
    from numba import jit, prange
    NUMBA_AVAILABLE = True
    print("[OK] Numba disponible - Aceleración JIT activada")
except ImportError:
    NUMBA_AVAILABLE = False
    print("[WARN] Numba no disponible - Usando modo estándar")

app = Flask(__name__, template_folder='templates', static_folder='static', static_url_path='/static')
app.secret_key = 'halftone-rip-pro-secret-key-2024'
app.config['MAX_CONTENT_LENGTH'] = 10 * 1024 * 1024 * 1024
app.config['UPLOAD_FOLDER'] = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'uploads')
app.config['OUTPUT_FOLDER'] = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'outputs')

for folder in [app.config['UPLOAD_FOLDER'], app.config['OUTPUT_FOLDER']]:
    os.makedirs(folder, exist_ok=True)

# Configuración de rendimiento
PERFORMANCE_CONFIG = {
    'max_workers': max(1, multiprocessing.cpu_count() - 1),
    'gs_threads': 4,
    'use_numba': NUMBA_AVAILABLE,
    'timeout_per_page': 120
}

print(f"[PERF] Configuración: {PERFORMANCE_CONFIG['max_workers']} workers paralelos")

# =============================================================================
# FORMATOS SOPORTADOS
# =============================================================================
# Solo .ps y .pdf: son los únicos formatos donde cada placa de color llega
# como una página separada del archivo (requisito real para hacer separaciones
# de serigrafía). .eps casi siempre es una sola página/un solo color compuesto,
# y los formatos de imagen (PNG/JPG/TIFF) no contienen información real de
# separación de tintas: aplicarles un "halftone" sería una simulación sin
# relación con las placas reales de impresión.
SUPPORTED_EXTENSIONS = {'ps', 'pdf'}

UNSUPPORTED_FORMAT_REASONS = {
    'eps': 'Los archivos EPS suelen venir como una sola página/un solo color compuesto, no separado por placas. No es posible generar separaciones reales a partir de un EPS.',
    'png': 'Las imágenes PNG/JPG/TIFF no contienen información de separación de tintas (son color compuesto). Un halftone sobre estos archivos no representaría placas de impresión reales.',
    'jpg': 'Las imágenes PNG/JPG/TIFF no contienen información de separación de tintas (son color compuesto). Un halftone sobre estos archivos no representaría placas de impresión reales.',
    'jpeg': 'Las imágenes PNG/JPG/TIFF no contienen información de separación de tintas (son color compuesto). Un halftone sobre estos archivos no representaría placas de impresión reales.',
    'tiff': 'Las imágenes PNG/JPG/TIFF no contienen información de separación de tintas (son color compuesto). Un halftone sobre estos archivos no representaría placas de impresión reales.',
    'tif': 'Las imágenes PNG/JPG/TIFF no contienen información de separación de tintas (son color compuesto). Un halftone sobre estos archivos no representaría placas de impresión reales.',
}


def build_unsupported_format_error(ext):
    """Mensaje de error claro y específico según el formato rechazado."""
    ext = (ext or '').lower().lstrip('.')
    base = f"Formato .{ext} no soportado. "
    reason = UNSUPPORTED_FORMAT_REASONS.get(ext)
    if reason:
        base += reason + " "
    base += "Este programa trabaja con archivos .ps o .pdf, preseparados por placa o compuestos en CMYK/PDF-X4."
    return base


def friendly_error_message(exc):
    """
    Traduce una excepción técnica a un mensaje claro para el usuario final.
    Conserva el detalle técnico solo en consola (vía traceback.format_exc()),
    nunca en la respuesta JSON que ve la persona usando el programa.
    """
    msg = str(exc)

    if isinstance(exc, MemoryError):
        return ('El archivo es demasiado grande para procesarlo con la memoria '
                'disponible. Intenta con un DPI más bajo o cierra otros programas '
                'para liberar memoria.')

    if isinstance(exc, FileNotFoundError):
        return ('No se encontró un archivo necesario para completar la operación. '
                'Si el archivo fue eliminado o movido, vuelve a subirlo e intenta de nuevo.')

    if isinstance(exc, PermissionError):
        return ('El programa no tiene permiso para leer o escribir un archivo. '
                'Verifica que la carpeta del programa no esté en modo solo lectura '
                'y que no tengas el archivo abierto en otro programa.')

    if isinstance(exc, subprocess.TimeoutExpired):
        return ('Ghostscript tardó demasiado en procesar el archivo y se canceló. '
                'Esto suele pasar con archivos muy pesados, con muchas páginas o '
                'muy complejos. Intenta con un DPI más bajo.')

    if isinstance(exc, zipfile.BadZipFile):
        return 'No se pudo crear el archivo ZIP de salida. Intenta procesar el archivo de nuevo.'

    if isinstance(exc, (OSError, IOError)) and 'No space left' in msg:
        return ('No queda espacio en disco para guardar los resultados. '
                'Libera espacio e intenta de nuevo.')

    if type(exc).__name__ == 'UnidentifiedImageError':
        return ('No se pudo leer una de las imágenes. Verifica que el archivo '
                'no esté dañado y que sea un formato de imagen válido (PNG, JPG, TIFF).')

    if isinstance(exc, RuntimeError) and msg.startswith('Ghostscript (tiffsep) falló'):
        return ('Ghostscript no pudo procesar el archivo. Esto suele indicar que '
                'el archivo está dañado, corrupto o no es un PostScript/PDF válido. '
                'Intenta volver a exportarlo desde Illustrator/CorelDraw.')

    if isinstance(exc, RuntimeError) and 'no generó ninguna separación' in msg:
        return msg  # ya es un mensaje claro escrito para el usuario final

    # Fallback: no se reconoce el tipo de error, pero igual evitamos
    # mostrar la traza cruda de Python al usuario.
    return ('Ocurrió un error inesperado al procesar el archivo. '
            'Revisa que el archivo no esté dañado e intenta de nuevo. '
            f'(Detalle técnico: {msg})')

# =============================================================================
# LISTA NEGRA DE PALABRAS PROHIBIDAS (no son nombres de color)
# =============================================================================

COLOR_BLACKLIST = {
    # Constantes y operadores PostScript
    'none', 'all', 'true', 'false', 'null', 'mark', 'count', 'copy',
    'dup', 'exch', 'pop', 'index', 'roll', 'clear', 'cleartomark',
    'def', 'bind', 'readonly', 'put', 'get', 'begin', 'end',
    'save', 'restore', 'gsave', 'grestore', 'showpage', 'erasepage',
    'newpath', 'moveto', 'lineto', 'curveto', 'arc', 'closepath',
    'stroke', 'fill', 'eofill', 'clip', 'rectclip',
    'setcolorspace', 'setcolor', 'setgray', 'setrgbcolor', 'setcmykcolor',
    'image', 'colorimage', 'translate', 'scale', 'rotate',
    # Variables de color comunes en código (NO son nombres de separación)
    'red', 'green', 'blue', 'cyan', 'magenta', 'yellow', 'black',
    'white', 'gray', 'grey', 'orange', 'purple', 'pink', 'brown',
    '_red_', '_green_', '_blue_', '_cyan_', '_magenta_', '_yellow_',
    '_black_', '_white_', '_gray_', '_grey_', '_orange_', '_purple_',
    '_pink_', '_brown_',
    # Palabras genéricas que nunca son nombres de tinta
    'process', 'spot', 'color', 'colour', 'ink', 'tinta', 'custom',
    'separation', 'colorspace', 'devicecmyk', 'devicergb', 'devicegray',
    'pattern', 'indexed', 'default', 'standard', 'normal', 'regular',
    'generic', 'solid', 'mixed', 'blend', 'overlay', 'multiply', 'screen',
    'array', 'dict', 'string', 'name', 'real', 'integer', 'boolean',
    'file', 'operator', 'fonttype', 'encoding', 'painttype', 'fontname',
    'page', 'pages', 'media', 'mediabox', 'cropbox', 'bleedbox', 'trimbox',
    'boundingbox', 'documentprocesscolors', 'documentcustomcolors',
    'pagecustomcolors', 'pageprocesscolors', 'hiResBoundingBox',
    # Constantes numéricas
    'zero', 'one', 'two', 'half', 'quarter', 'third', 'full', 'empty',
    'min', 'max', 'avg', 'sum', 'total',
    # Prefijos de variables de código
    'tmp', 'temp', 'buf', 'ptr', 'obj', 'val', 'var', 'arg', 'param',
    'ret', 'res', 'err', 'ok', 'done', 'start', 'stop', 'run', 'go',
    'new', 'old', 'curr', 'prev', 'next', 'last', 'first', 'init',
    'src', 'dst', 'data', 'info', 'cfg', 'mode', 'type', 'kind',
    'set', 'list', 'map', 'item', 'key', 'id', 'idx', 'pos', 'size',
    'num', 'rate', 'base', 'main', 'extra', 'special', 'unique',
}

# Colores comunes de 3-4 letras que SÍ son válidos
VALID_SHORT_COLORS = {
    'gold', 'pink', 'teal', 'navy', 'lime', 'plum', 'ruby', 'jade',
    'ivory', 'beige', 'coral', 'khaki', 'olive', 'wheat', 'snow',
    'mint', 'rose', 'sand', 'coal', 'lead', 'zinc', 'tin', 'pewter',
    'rust', 'copper', 'bronze', 'silver'
}


def is_blacklisted(name):
    """Verifica si un nombre está en la lista negra."""
    if not name:
        return True
    n = name.lower().strip().strip('_').strip('()')
    
    # Lista negra directa
    if n in COLOR_BLACKLIST:
        return True
    
    # Palabras muy cortas (1-2 letras) que no son colores comunes
    if len(n) <= 2 and n.isalpha() and n not in VALID_SHORT_COLORS:
        return True
    
    # Palabras que son solo números
    if n.replace(' ', '').replace('-', '').replace('.', '').isdigit():
        return True
    
    # Si contiene operadores matemáticos, etc.
    if any(op in n for op in ['%', '+', '-', '*', '/', '=', '<', '>', '&', '|', '!']):
        return True
    
    return False


def clean_color_name(name):
    """Limpia y normaliza un nombre de color."""
    if not name:
        return ""
    name = re.sub(r'[^\w\s\-\.]', '', name)
    name = re.sub(r'\s+', ' ', name).strip()
    # No capitalizar todo, preservar formato original
    return name


def looks_like_color_name(name):
    """Heurística estricta: ¿parece un nombre de tinta real?"""
    if not name or len(name) < 2 or len(name) > 50:
        return False
    if is_blacklisted(name):
        return False

    nl = name.lower()

    # Sistemas de color conocidos
    for sys in ['pantone', 'hks', 'toyo', 'dic', 'anpa', 'ral', 'ncs',
                'focoltone', 'trumatch', 'munsell']:
        if sys in nl:
            return True

    # Códigos tipo Pantone123, HKS43, RAL9010, 485c, 485 C
    if re.match(r'^[A-Za-z]{2,6}\s*\d{2,5}', name):
        return True
    
    # Números como 485c, 485C, 485 C
    if re.match(r'^\d{3}\s*[cC]\b', name):
        return True
    if re.search(r'\d{3}\s*[cC]', nl):
        return True

    # Contiene números + sufijo de acabado (C, U, CP, UP, etc.)
    if re.search(r'\d+\s+[CMUP]\b', name):
        return True
    if re.search(r'\d+\s+(CP|UP|CVC|CVU|EC|HC|PC|TC|TP|XGC|N)\b', name):
        return True

    # Palabras de color en español
    color_words = ['azul', 'rojo', 'verde', 'amarillo', 'negro', 'blanco', 
                   'gris', 'naranja', 'morado', 'rosa', 'celeste', 'violeta', 
                   'marrón', 'turquesa', 'beige', 'ocre', 'dorado', 'plateado',
                   'bronce', 'cobre']
    if nl in color_words:
        return True

    # Términos específicos de serigrafía
    for term in ['fluor', 'neon', 'metallic', 'pearl', 'matte', 'gloss',
                 'reflex', 'opaque', 'transparent', 'underbase', 'highlight',
                 'cover', 'base', 'simulated', 'extended', 'vibrant']:
        if term in nl:
            return True

    return False


# =============================================================================
# SEMITONO OPTIMIZADO CON NUMBA
# =============================================================================

if NUMBA_AVAILABLE:
    @jit(nopython=True, parallel=True, cache=True)
    def apply_halftone_fast(channel_array, lpi, dpi, angle_deg, shape_type):
        """Semitono optimizado con Numba - 10x más rápido"""
        h, w = channel_array.shape
        theta = np.deg2rad(angle_deg)
        T = dpi / lpi
        
        result = np.zeros((h, w), dtype=np.uint8)
        cos_t = np.cos(theta)
        sin_t = np.sin(theta)
        
        for y in prange(h):
            for x in prange(w):
                u = x * cos_t - y * sin_t
                v = x * sin_t + y * cos_t
                
                u_norm = ((u / T) - np.round(u / T)) * 2.0
                v_norm = ((v / T) - np.round(v / T)) * 2.0
                
                if shape_type == 0:  # round
                    S = np.cos(np.pi * u_norm) + np.cos(np.pi * v_norm)
                    threshold = (S + 2.0) / 4.0
                elif shape_type == 1:  # ellipse
                    S = 3.0 * np.cos(np.pi * u_norm) + np.cos(np.pi * v_norm)
                    threshold = (S + 4.0) / 8.0
                elif shape_type == 2:  # line
                    S = np.cos(np.pi * v_norm)
                    threshold = (S + 1.0) / 2.0
                elif shape_type == 3:  # square
                    S = np.cos(np.pi * u_norm) * np.cos(np.pi * v_norm)
                    threshold = (S + 1.0) / 2.0
                else:  # diamond
                    S = np.cos(np.pi * u_norm) + np.cos(np.pi * (u_norm + v_norm)/2.0)
                    threshold = (S + 2.0) / 4.0
                
                ink_density = (255.0 - channel_array[y, x]) / 255.0
                result[y, x] = 0 if ink_density > threshold else 255
        
        return result
    
    def apply_halftone(channel_array, lpi, dpi, angle_deg, shape):
        shape_map = {'round': 0, 'ellipse': 1, 'line': 2, 'square': 3, 'diamond': 4}
        shape_type = shape_map.get(shape, 0)
        return apply_halftone_fast(channel_array, lpi, dpi, angle_deg, shape_type)
else:
    def apply_halftone(channel_array, lpi, dpi, angle_deg, shape):
        h, w = channel_array.shape
        theta = np.deg2rad(angle_deg)
        T = dpi / lpi
        
        x = np.arange(w, dtype=np.float32)
        y = np.arange(h, dtype=np.float32)
        x_grid, y_grid = np.meshgrid(x, y)
        
        cos_t, sin_t = np.cos(theta), np.sin(theta)
        u = x_grid * cos_t - y_grid * sin_t
        v = x_grid * sin_t + y_grid * cos_t
        
        u_cell, v_cell = u / T, v / T
        u_norm = (u_cell - np.round(u_cell)) * 2.0
        v_norm = (v_cell - np.round(v_cell)) * 2.0
        
        if shape == 'round':
            S = np.cos(np.pi * u_norm) + np.cos(np.pi * v_norm)
        elif shape == 'ellipse':
            S = 3.0 * np.cos(np.pi * u_norm) + np.cos(np.pi * v_norm)
        elif shape == 'line':
            S = np.cos(np.pi * v_norm)
        elif shape == 'square':
            S = np.cos(np.pi * u_norm) * np.cos(np.pi * v_norm)
        else:
            S = np.cos(np.pi * u_norm) + np.cos(np.pi * v_norm)
        
        threshold = (S + 2.0) / 4.0
        ink_density = (255.0 - channel_array) / 255.0
        return np.where(ink_density > threshold, 0, 255).astype(np.uint8)


# =============================================================================
# DETECTAR GHOSTSCRIPT
# =============================================================================

def find_ghostscript():
    commands = ['gswin64c', 'gswin32c', 'gs']
    for cmd in commands:
        try:
            result = subprocess.run([cmd, '--version'], capture_output=True, text=True, timeout=5)
            if result.returncode == 0:
                version = result.stdout.strip()
                print(f"[OK] Ghostscript: {cmd} v{version}")
                return cmd, version
        except:
            continue
    return None, None

GS_CMD, GS_VERSION = find_ghostscript()
GS_AVAILABLE = GS_CMD is not None


# =============================================================================
# UTILIDADES
# =============================================================================

def pil_to_base64(img, fmt='PNG'):
    buffer = io.BytesIO()
    img.save(buffer, format=fmt)
    img_str = base64.b64encode(buffer.getvalue()).decode()
    return f'data:image/{fmt.lower()};base64,{img_str}'

def smart_thumbnail(img, target_max=800):
    """
    Reduce una imagen de halftone para vista previa sin el aliasing severo
    que produce un resize directo de gran factor (ej. 5100px -> 309px,
    ~16x). Una trama de puntos es una señal de alta frecuencia: reducirla
    de un solo salto con cualquier filtro (incluso Lanczos) cae muy por
    debajo del límite de Nyquist y el patrón visual resultante deja de
    representar la trama real, aunque el archivo guardado en disco a
    resolución completa sí esté correcto.

    Estrategia: reducir por bloques (PIL .reduce(), que promedia píxeles
    en vez de solo muestrear) en pasos de 2x hasta acercarse al tamaño
    objetivo, y solo en el último tramo usar un resize fino con Lanczos.
    Esto da una vista previa visualmente representativa de la trama real.

    target_max más alto (800 en vez del clásico 150-400 de un thumbnail
    genérico) porque una trama fina necesita más píxeles de destino para
    que el patrón siga siendo reconocible como puntos en vez de ruido.
    """
    while img.size[0] > target_max * 2 and img.size[1] > target_max * 2:
        img = img.reduce(2)

    if max(img.size) > target_max:
        ratio = target_max / max(img.size)
        new_size = (max(1, int(img.size[0] * ratio)), max(1, int(img.size[1] * ratio)))
        img = img.resize(new_size, Image.LANCZOS)

    return img

def get_file_size_mb(filepath):
    return round(os.path.getsize(filepath) / (1024 * 1024), 2)


# =============================================================================
# DETECCIÓN DINÁMICA DE PÁGINAS Y COLORES
# =============================================================================

_ps_metadata_cache = {}

def decode_ps_string(s):
    """Decodifica secuencias de escape octales de PostScript (ej. \\347) a texto legible."""
    if not s:
        return ""
    def replace_octal(match):
        return chr(int(match.group(1), 8))
    try:
        # Reemplazar secuencias del tipo \ooo
        decoded = re.sub(r'\\([0-7]{3})', replace_octal, s)
        raw_bytes = decoded.encode('latin-1', errors='replace')
        # Intentar decodificar con formatos comunes (UTF-8, CP1251 para cirílico, CP1252 para europeo, Latin-1)
        for encoding in ['utf-8', 'cp1251', 'cp1252', 'latin-1']:
            try:
                return raw_bytes.decode(encoding)
            except UnicodeDecodeError:
                continue
        return decoded
    except Exception:
        return s


def parse_ps_file(filepath):
    """
    Escanea un archivo PostScript línea por línea de forma eficiente en una sola pasada.
    Retorna el número total de páginas y un mapa de número_de_página -> nombre_de_color.
    Utiliza caché para optimizar llamadas repetidas.
    """
    try:
        mtime = os.path.getmtime(filepath)
        cache_key = (filepath, mtime)
        if cache_key in _ps_metadata_cache:
            return _ps_metadata_cache[cache_key]
    except Exception:
        cache_key = None

    page_count = 0
    page_colors = {}

    try:
        with open(filepath, 'r', encoding='latin-1', errors='ignore') as f:
            for line in f:
                if line.startswith('%%Page:'):
                    page_count += 1
                elif '%%PlateColor:' in line:
                    match = re.search(r'%%PlateColor:\s*([^\n]+)', line, re.IGNORECASE)
                    if match and page_count > 0:
                        color = decode_ps_string(match.group(1).strip().strip('()'))
                        color = clean_color_name(color)
                        if color and color.lower() not in ['none', 'all']:
                            page_colors[page_count] = color
                elif '/PlateColor' in line:
                    match = re.search(r'/PlateColor\s*\(\s*([^)]+)\)', line, re.IGNORECASE)
                    if match and page_count > 0:
                        color = decode_ps_string(match.group(1).strip())
                        color = clean_color_name(color)
                        if color and color.lower() not in ['none', 'all']:
                            page_colors[page_count] = color

    except Exception as e:
        print(f"[PARSE PS] Error leyendo metadatos de PostScript: {e}")

    result = {
        'page_count': page_count if page_count > 0 else 4,
        'page_colors': page_colors
    }

    if cache_key:
        _ps_metadata_cache[cache_key] = result

    return result


def escape_ps_string(s):
    """
    Escapa una cadena para usarla de forma segura dentro de (...) en
    sintaxis PostScript. Necesario porque paréntesis y barras invertidas
    tienen significado especial dentro de una cadena PS: una ruta de
    Windows (que usa \\ como separador) o una carpeta con un paréntesis
    literal en el nombre (algo tan común como "Halftone RIP Pro (v2)")
    rompe el comando si no se escapa primero.
    """
    s = s.replace('\\', '\\\\')  # backslash primero, antes de escapar parentesis
    s = s.replace('(', '\\(')
    s = s.replace(')', '\\)')
    return s


def count_pdf_pages_via_gs(filepath, timeout=15):
    """
    Cuenta páginas reales de un PDF usando el propio Ghostscript.
    Necesario porque un PDF no tiene los marcadores %%Page:/%%Pages: de
    PostScript que usa parse_ps_file, así que ese parseo de texto siempre
    fallaría (y caería al valor de relleno) para cualquier PDF.
    """
    if not GS_AVAILABLE:
        return None
    try:
        escaped_path = escape_ps_string(filepath)
        cmd = [GS_CMD, '-q', '-dNODISPLAY', '-dBATCH', '-dNOSAFER', '-c',
               f'({escaped_path}) (r) file runpdfbegin pdfpagecount =']
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        output = result.stdout.strip().splitlines()
        return int(output[-1]) if output else None
    except (subprocess.TimeoutExpired, ValueError, IndexError, OSError):
        return None


def get_real_page_count(filepath):
    """
    Retorna la cantidad de páginas reales del archivo.
    Para .pdf usa Ghostscript directamente (más confiable: un PDF no tiene
    los marcadores de texto %%Page: que existen en PostScript).
    Para .ps/.eps usa el parseo de texto existente.
    """
    ext = os.path.splitext(filepath)[1].lower()

    if ext == '.pdf':
        count = count_pdf_pages_via_gs(filepath)
        if count and count > 0:
            print(f"[PAGES] Páginas reales contadas (PDF vía Ghostscript): {count}")
            return count
        # Si Ghostscript no pudo contarlas, no hay un fallback confiable
        # específico para PDF: usamos el mismo valor de relleno que el
        # resto del programa ya asume en otros lados (4).
        print("[PAGES] No se pudo determinar el conteo real de páginas del PDF, usando valor de relleno")
        return 4

    try:
        metadata = parse_ps_file(filepath)
        print(f"[PAGES] Páginas reales contadas: {metadata['page_count']}")
        return metadata['page_count']
    except Exception as e:
        print(f"[PAGES] Error al obtener conteo: {e}")
        return 4


# =============================================================================
# EXTRACCIÓN DE NOMBRES DE COLORES (VERSIÓN MEJORADA Y DINÁMICA)
# =============================================================================

def extract_pantone_names(filepath, page_count):
    """
    Asigna los nombres de color a cada página de renderizado.
    Prioriza el mapeo página-a-página detectado en la lectura lineal y eficiente del archivo.
    Si faltan nombres, realiza una búsqueda por patrones y fallback a CMYK.
    """
    metadata = parse_ps_file(filepath)
    detected_colors = metadata['page_colors']
    
    color_names = {}
    
    # Asignar nombres detectados directamente por página
    for page_num in range(1, page_count + 1):
        if page_num in detected_colors and detected_colors[page_num]:
            color_names[page_num] = detected_colors[page_num]

    # Si hay páginas sin nombre de color asignado, intentamos un escaneo por patrones general
    missing_pages = [p for p in range(1, page_count + 1) if p not in color_names]
    if missing_pages:
        print(f"[NAMES] Faltan nombres para páginas {missing_pages}. Iniciando fallback por patrones...")
        
        # Escaneo de patrones clásicos (primeros 2MB para evitar lentitud)
        try:
            with open(filepath, 'r', encoding='latin-1', errors='ignore') as f:
                content = f.read(2000000)
        except Exception as e:
            print(f"[NAMES] Fallback falló al leer archivo: {e}")
            content = ""
            
        unique_names = []
        seen = set()
        
        patterns = [
            r'%%PlateColor:\s*([^\n]+)',
            r'/Separation\s*\(\s*([^)]+)\)',
            r'/ColorName\s*\(\s*([^)]+)\)',
            r'findcmykcustomcolor\s*\(\s*([^)]+)\)',
            r'%%DocumentCustomColors:\s*([^\n]+)',
            r'%%CMYKCustomColor:\s*[0-9\.\s]+\s*\(([^)]+)\)',
            r'/PlateColor\s*\(\s*([^)]+)\)',
        ]
        
        for pattern in patterns:
            for m in re.finditer(pattern, content, re.IGNORECASE):
                raw = m.group(1).strip()
                if 'DocumentCustomColors' in pattern:
                    tokens = raw.split()
                else:
                    tokens = [raw]
                
                for token in tokens:
                    token = token.strip('()')
                    if token.lower() in ('(atend)', 'atend', ''):
                        continue
                    
                    n = decode_ps_string(token)
                    n = clean_color_name(n)
                    
                    if n and n.lower() not in seen:
                        if n.lower() not in ['none', 'all', 'true', 'false', 'null']:
                            if not re.match(r'^\d+$', n) and len(n) >= 2:
                                if n.lower() not in ['cyan', 'magenta', 'yellow', 'black']:
                                    seen.add(n.lower())
                                    unique_names.append(n)
                                    
        # Rellenar las páginas faltantes usando los colores del fallback por patrones o nombres genéricos
        fallback_cmyk = {1: "Cyan", 2: "Magenta", 3: "Yellow", 4: "Black"}
        
        spot_idx = 0
        for page_num in missing_pages:
            # Si la página está en el rango CMYK y no tiene color detectado, probamos CMYK tradicional
            if page_num <= 4 and page_count >= 4:
                color_names[page_num] = fallback_cmyk[page_num]
            elif spot_idx < len(unique_names):
                color_names[page_num] = unique_names[spot_idx]
                spot_idx += 1
            else:
                # Si no quedan nombres de spot, usar nombres de CMYK/Spot por defecto
                if page_num <= 4:
                    color_names[page_num] = fallback_cmyk[page_num]
                else:
                    color_names[page_num] = f"Spot_{page_num}"
                    
    print(f"[NAMES] Mapa de colores de separación final: {color_names}")
    return color_names



# =============================================================================
# RENDERIZADO RÁPIDO DE PÁGINA
# =============================================================================

def render_page_fast(filepath, output_dir, dpi, page_num):
    """Renderizado optimizado de página - PNG para máxima calidad"""
    output_path = os.path.join(output_dir, f"page_{page_num}.png")
    filepath_norm = os.path.abspath(filepath).replace('\\', '/')
    
    cmd = [
        GS_CMD, '-dNOPAUSE', '-dBATCH', '-dSAFER',
        '-sDEVICE=png16m',
        f'-r{dpi}',
        f'-dFirstPage={page_num}', f'-dLastPage={page_num}',
        '-dPDFFitPage', '-dUseCropBox',
        '-dGraphicsAlphaBits=4',
        '-dTextAlphaBits=4',
        f'-dNumRenderingThreads={PERFORMANCE_CONFIG["gs_threads"]}',
        f'-sOutputFile={output_path}', filepath_norm
    ]
    
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, 
                               timeout=PERFORMANCE_CONFIG['timeout_per_page'])
        
        if os.path.exists(output_path) and os.path.getsize(output_path) > 5000:
            img = Image.open(output_path)
            if img.mode == 'RGB':
                img = img.convert('L')
            # Validar que la imagen no esté vacía o uniforme
            try:
                arr = np.array(img)
                if arr.max() == arr.min():
                    print(f"[WARN] Página {page_num}: imagen uniforme (posible error de renderizado)")
            except Exception:
                pass
            return img
        else:
            print(f"[PAGE {page_num}] Archivo no generado o muy pequeño")
    except Exception as e:
        print(f"[PAGE {page_num}] Error: {e}")
    
    return None


# =============================================================================
# PROCESAMIENTO PARALELO DE PÁGINAS
# =============================================================================

def process_with_tiffsep(filepath, output_dir, dpi=600, timeout=300):
    """
    Procesa un archivo .ps/.pdf con Ghostscript usando el dispositivo tiffsep,
    que separa el documento en sus canales de color reales (CMYK + spot colors
    detectados automáticamente por Ghostscript), uno o más por página.

    Soporta tanto archivos ya preseparados por placa (el .ps clásico de
    Illustrator, una página = un color) como PDF/X-4 o PDF/X-1a compuestos
    (una página con varios canales CMYK + spot mezclados), porque en ambos
    casos tiffsep entrega los canales reales con tinta y deja en blanco
    puro (255) los canales que esa página no usa. Los canales en blanco
    puro se descartan: no representan ninguna placa real.

    Retorna un OrderedDict {nombre_color: imagen_PIL_modo_L}, la misma
    interfaz que usaba process_postscript_parallel, así que el resto del
    pipeline (halftone, ZIP, análisis, preview) no necesita cambios.
    """
    if not GS_AVAILABLE:
        raise RuntimeError("Ghostscript no está disponible")

    os.makedirs(output_dir, exist_ok=True)
    output_pattern = os.path.join(output_dir, 'sep_%d.tif')

    # NumRenderingThreads: el cuello de botella real de tiffsep es el
    # overhead de abrir/interpretar el archivo (no escala con páginas, ya
    # medido), así que esto da una mejora modesta, no dramática - ayuda
    # sobre todo en el rasterizado de páginas con mucho contenido vectorial
    # (texto, trazos complejos), que sí es paralelizable internamente por
    # Ghostscript dentro de una misma página.
    render_threads = PERFORMANCE_CONFIG['max_workers']

    cmd = [
        GS_CMD,
        '-sDEVICE=tiffsep',
        '-dNOPAUSE', '-dBATCH', '-dSAFER',
        f'-r{dpi}',
        '-dMaxBitmap=2147483647',
        f'-dNumRenderingThreads={render_threads}',
        f'-sOutputFile={output_pattern}',
        filepath
    ]

    print(f"[TIFFSEP] Ejecutando Ghostscript: {' '.join(cmd)}")
    start_time = time.time()
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)

    if result.returncode != 0:
        raise RuntimeError(f"Ghostscript (tiffsep) falló: {result.stderr[-2000:]}")

    # Patrón de archivos generados: sep_<pagina>(<NombreColor>).tif
    # El archivo sin paréntesis (sep_<pagina>.tif) es el compuesto CMYK
    # visual completo - se descarta, no es una placa individual.
    pattern = re.compile(r'^sep_(\d+)\((.+)\)\.tif$', re.IGNORECASE)

    found = []  # [(page_num, color_name, filepath)]
    for fname in os.listdir(output_dir):
        m = pattern.match(fname)
        if m:
            page_num = int(m.group(1))
            color_name = clean_color_name(m.group(2))
            found.append((page_num, color_name, os.path.join(output_dir, fname)))

    if not found:
        raise RuntimeError(
            "Ghostscript no generó ninguna separación. Verifica que el archivo "
            "no esté dañado y que tenga contenido de color válido."
        )

    # Nombres reales por página vía comentarios PostScript (%%PlateColor:,
    # %%DocumentCustomColors:, etc.). Necesario porque un .ps clásico de
    # Illustrator suele "simular" una tinta plana pintando en escala de
    # grises dentro del canal K, sin declarar un objeto /Separation real -
    # tiffsep no tiene forma de saber que esa página es "PANTONE 286 C" y
    # no simplemente "Black", porque para Ghostscript el contenido gráfico
    # sí es K puro. Esto solo aplica a PostScript: un PDF que use tintas
    # planas reales ya las declara con /Separation, que tiffsep sí lee.
    #
    # Importante: se usa parse_ps_file() directamente (no
    # extract_pantone_names(), que también mezcla un fallback posicional
    # ficticio del tipo "página 1 = Cyan, página 2 = Magenta..." cuando no
    # encuentra comentarios reales). Ese fallback por posición no tiene
    # ninguna relación con el contenido real de la página, y usarlo aquí
    # como si fuera un nombre confiable causaba que tiffsep reportara
    # "Cyan"/"Magenta" en páginas que en realidad eran K puro con tintas
    # planas distintas sin comentario - el problema opuesto al que este
    # override intenta arreglar. parse_ps_file() solo reporta nombres de
    # comentarios %%PlateColor:/PlateColor genuinos, sin inventar nada.
    plate_comment_names = {}
    ext = os.path.splitext(filepath)[1].lower()
    if ext in ('.ps', '.eps'):
        try:
            ps_metadata = parse_ps_file(filepath)
            plate_comment_names = ps_metadata.get('page_colors', {})
        except Exception as e:
            print(f"[TIFFSEP] No se pudieron leer nombres de comentarios PS: {e}")

    # Contar cuántos canales CON TINTA REAL tiene cada página, para saber
    # si el override por comentario es seguro de aplicar (solo tiene
    # sentido cuando "una página = un color", que es la premisa bajo la
    # que existen los comentarios %%PlateColor:). Importante: tiffsep
    # genera un archivo .tif por cada canal CMYK aunque esté en blanco
    # puro, así que hay que aplicar el mismo filtro de "tinta real" que
    # se usa más abajo, o esta cuenta siempre daría 4 por página.
    pages_channel_count = {}
    for page_num, color_name, tif_path in found:
        probe_img = Image.open(tif_path)
        if probe_img.mode != 'L':
            probe_img = probe_img.convert('L')
        if np.array(probe_img).min() < 255:
            pages_channel_count[page_num] = pages_channel_count.get(page_num, 0) + 1

    # Orden estable: por página, luego CMYK en su orden estándar y
    # finalmente spot colors alfabético, para una salida predecible.
    cmyk_order = {'cyan': 0, 'magenta': 1, 'yellow': 2, 'black': 3}
    found.sort(key=lambda x: (x[0], cmyk_order.get(x[1].lower(), 99), x[1]))

    plates = OrderedDict()
    warnings = []
    cmyk_names_seen = {}  # 'black' -> [lista de nombres finales usados], para detectar colisiones reales

    for page_num, color_name, tif_path in found:
        img = Image.open(tif_path)
        if img.mode != 'L':
            img = img.convert('L')
        arr = np.array(img)

        # Descartar canales sin tinta real (blanco puro = 255 en absolutamente
        # todo el canal). Sin margen de tolerancia: incluso una placa con
        # cobertura muy sutil (1% o menos) debe conservarse.
        if arr.min() >= 255:
            continue

        # Override por nombre real de comentario PS, solo si esta página
        # tiene un único canal con tinta (la premisa de "una página = un
        # color" se cumple) y el comentario detectó un nombre para ella.
        real_name = plate_comment_names.get(page_num)
        used_generic_name = True
        if real_name and pages_channel_count.get(page_num) == 1:
            print(f"[TIFFSEP] Página {page_num}: '{color_name}' -> nombre real '{real_name}' (vía comentario PS)")
            color_name = real_name
            used_generic_name = False

        # Registrar en qué páginas aparece cada nombre genérico de canal
        # CMYK (Cyan/Magenta/Yellow/Black), tanto la primera aparición como
        # las siguientes. Si el mismo nombre termina en 2+ páginas, es una
        # señal real (no aleatoria) de tintas planas sin comentario
        # %%PlateColor: que las identifique correctamente.
        if used_generic_name:
            cmyk_names_seen.setdefault(color_name, []).append(page_num)

        final_name = color_name if color_name else f"Color_pagina_{page_num}"
        if final_name in plates:
            final_name = f"{final_name}_p{page_num}"

        # Cobertura total de la placa, para detectar posibles inversiones
        # (una placa con casi toda el área cubierta de tinta es inusual
        # para una separación de serigrafía normal, y a veces indica que
        # el archivo tenía los colores invertidos o un fondo de color
        # sólido sin querer incluido en esa placa).
        ink_fraction = float(np.mean((255 - arr) / 255.0))
        if ink_fraction > 0.95:
            warnings.append(
                f"'{final_name}' tiene {ink_fraction*100:.0f}% de cobertura de tinta en toda el área. "
                f"Verifica que no sea un fondo sólido incluido por error o una placa invertida."
            )

        plates[final_name] = img
        print(f"[TIFFSEP] Placa detectada: '{final_name}' (página {page_num})")

    # Si el mismo nombre CMYK genérico apareció en 2+ páginas sin nombre
    # real detectado, es una señal real (no aleatoria) de que el archivo
    # probablemente tiene tintas planas sin declarar correctamente.
    for generic_name, pages in cmyk_names_seen.items():
        if len(pages) > 1:
            warnings.append(
                f"{len(pages)} placas distintas se detectaron como '{generic_name}' "
                f"(páginas {', '.join(map(str, pages))}). Si estas páginas son "
                f"tintas Pantone distintas, agrega un comentario %%PlateColor: (nombre) en cada "
                f"página al exportar desde Illustrator para que se detecten con su nombre real."
            )

    elapsed = time.time() - start_time
    print(f"[TIFFSEP] Completado en {elapsed:.1f}s ({len(plates)} placas con tinta)")

    return plates, warnings


# -----------------------------------------------------------------------------
# DEPRECADO: pipeline anterior basado en render_page_fast (png16m) +
# extract_pantone_names (parseo manual de texto PostScript).
# Se mantiene sin usar por ahora como referencia/respaldo; reemplazado por
# process_with_tiffsep, que obtiene las placas y sus nombres directo de
# Ghostscript en vez de adivinarlos a partir del texto del archivo.
# -----------------------------------------------------------------------------
def process_postscript_parallel(filepath, output_dir, dpi=600):
    """
    Procesar páginas en paralelo.
    Usa lista para preservar TODAS las páginas sin duplicados.
    """
    
    start_time = time.time()
    page_count = get_real_page_count(filepath)
    print(f"[PARALELO] {page_count} páginas, {PERFORMANCE_CONFIG['max_workers']} workers")
    
    # Extraer nombres de color
    color_names = extract_pantone_names(filepath, page_count)
    
    # Usar lista para preservar TODAS las páginas
    plates = []  # [(page_num, color_name, image), ...]
    
    with ThreadPoolExecutor(max_workers=PERFORMANCE_CONFIG['max_workers']) as executor:
        futures = {
            executor.submit(render_page_fast, filepath, output_dir, dpi, page_num): page_num
            for page_num in range(1, page_count + 1)
        }
        
        for future in as_completed(futures):
            page_num = futures[future]
            img = future.result()
            
            if img:
                color_name = color_names.get(page_num, f"Color_{page_num}")
                plates.append((page_num, color_name, img))
                print(f"[PARALELO] Página {page_num}/{page_count}: {color_name} ✓")
            else:
                print(f"[PARALELO] Página {page_num}: falló ✗")
    
    # Ordenar por número de página
    plates.sort(key=lambda x: x[0])
    
    elapsed = time.time() - start_time
    print(f"[PARALELO] Completado en {elapsed:.1f}s ({len(plates)}/{page_count} páginas)")
    
    # Convertir a OrderedDict (preserva orden, sin duplicados)
    return OrderedDict((name, img) for _, name, img in plates)


# =============================================================================
# COMPOSITE PREVIEW
# =============================================================================

def create_composite_preview(channels):
    color_palette = [
        (0, 180, 216), (230, 58, 110), (245, 200, 0), (26, 26, 46),
        (255, 99, 132), (75, 192, 192), (153, 102, 255), (255, 159, 64),
        (199, 199, 199), (83, 102, 255), (255, 205, 86), (201, 203, 207)
    ]
    
    if not channels:
        return Image.new('RGB', (500, 500), (255, 255, 255))
    
    base = list(channels.values())[0]
    w, h = base.size
    composite = np.ones((h, w, 3), dtype=np.uint8) * 255
    
    for idx, (name, img) in enumerate(channels.items()):
        color = color_palette[idx % len(color_palette)]
        arr = np.array(img)
        inv = 255 - arr
        for i in range(3):
            composite[:,:,i] = (composite[:,:,i].astype(np.uint16) * inv.astype(np.uint16) // 255).astype(np.uint8)
    
    return Image.fromarray(composite, mode='RGB')


# =============================================================================
# ANÁLISIS DE DIFERENCIAS
# =============================================================================

def analyze_differences(sep_img, ref_img):
    w, h = min(sep_img.size[0], ref_img.size[0]), min(sep_img.size[1], ref_img.size[1])
    sep_img = sep_img.resize((w, h), Image.LANCZOS).convert('RGB')
    ref_img = ref_img.resize((w, h), Image.LANCZOS).convert('RGB')

    sep_arr = np.array(sep_img).astype(np.float32)
    ref_arr = np.array(ref_img).astype(np.float32)

    diff = np.abs(sep_arr - ref_arr)
    diff_mean = np.mean(diff, axis=2)

    total_pixels = w * h
    avg_diff = np.mean(diff_mean)
    bright_px = np.sum(np.sum(sep_arr, axis=2) > 600)
    dark_px = np.sum(np.sum(sep_arr, axis=2) < 150)

    sep_norm = sep_arr / 255.0
    mx = np.max(sep_norm, axis=2)
    mn = np.min(sep_norm, axis=2)
    sat_mask = (mx > 0) & ((mx - mn) / mx > 0.4)
    sat_px = np.sum(sat_mask)

    bright_pct = int(bright_px / total_pixels * 100)
    dark_pct = int(dark_px / total_pixels * 100)
    sat_pct = int(sat_px / total_pixels * 100)
    diff_score = int(avg_diff)

    diff_map = np.zeros((h, w, 4), dtype=np.uint8)
    intensity = np.clip(diff_mean * 3, 0, 255).astype(np.uint8)
    high_diff = diff_mean > 30
    diff_map[high_diff] = [220, 60, 60, np.clip(intensity[high_diff] * 2, 0, 255)]
    diff_map[~high_diff] = [0, 200, 0, 0]

    findings = []
    if avg_diff < 8:
        findings.append({'title': 'Densidad general', 'status': 'ok', 'pct': 95, 'msg': 'Cobertura de tinta muy cercana a la referencia. Excelente registro.'})
    elif avg_diff < 20:
        findings.append({'title': 'Densidad general', 'status': 'warn', 'pct': 70, 'msg': 'Diferencia promedio de ' + str(diff_score) + '/255 por pixel. Revisa la ganancia de punto.'})
    else:
        findings.append({'title': 'Densidad general', 'status': 'err', 'pct': 30, 'msg': 'Diferencia alta (' + str(diff_score) + '/255). Posible error en curvas de tinta o mala separacion.'})

    if bright_pct > 40:
        findings.append({'title': 'Zonas claras', 'status': 'warn', 'pct': bright_pct, 'msg': str(bright_pct) + '% del area es muy clara. Verifica que no haya perdida de semitonos.'})
    else:
        findings.append({'title': 'Zonas claras', 'status': 'ok', 'pct': 100 - bright_pct, 'msg': 'Balance de altas luces correcto (' + str(bright_pct) + '% area clara).'})

    if dark_pct > 35:
        findings.append({'title': 'Zonas oscuras', 'status': 'warn', 'pct': dark_pct, 'msg': str(dark_pct) + '% del area tiene sombras muy densas. Riesgo de empastamiento.'})
    else:
        findings.append({'title': 'Zonas oscuras', 'status': 'ok', 'pct': 100 - dark_pct, 'msg': 'Sombras dentro del rango aceptable.'})

    if sat_pct > 60:
        findings.append({'title': 'Saturacion', 'status': 'warn', 'pct': sat_pct, 'msg': str(sat_pct) + '% del area tiene colores muy saturados. Verifica gamut.'})
    else:
        findings.append({'title': 'Saturacion', 'status': 'ok', 'pct': 100 - sat_pct, 'msg': 'Saturacion dentro del gamut estandar.'})

    return {
        'score': diff_score,
        'diff_map': Image.fromarray(diff_map, mode='RGBA'),
        'findings': findings,
        'metrics': {'avg_diff': diff_score, 'bright_pct': bright_pct, 'dark_pct': dark_pct, 'sat_pct': sat_pct}
    }


# =============================================================================
# RUTAS FLASK
# =============================================================================

@app.route('/')
def index():
    gs_status = f"Ghostscript v{GS_VERSION}" if GS_AVAILABLE else "No instalado"
    numba_status = "✓ Activo" if NUMBA_AVAILABLE else "✗ No instalado"
    return render_template('index.html', gs_status=gs_status, numba_status=numba_status)


@app.route('/api/upload_chunk', methods=['POST'])
def upload_chunk():
    file = request.files.get('file')
    filename = request.form.get('filename')
    chunk_index = int(request.form.get('chunk_index', 0))
    total_chunks = int(request.form.get('total_chunks', 1))
    upload_id = request.form.get('upload_id')
    
    if not file or not filename or not upload_id:
        return jsonify({'error': 'Missing parameters'}), 400
    
    filename = secure_filename(filename)
    ext = filename.split('.')[-1].lower()
    if ext not in SUPPORTED_EXTENSIONS:
        return jsonify({'error': build_unsupported_format_error(ext)}), 400
    
    temp_filename = f"{upload_id}_{filename}"
    filepath = os.path.join(app.config['UPLOAD_FOLDER'], temp_filename)
    
    mode = 'wb' if chunk_index == 0 else 'ab'
    with open(filepath, mode) as f:
        f.write(file.read())
    
    if chunk_index + 1 == total_chunks:
        file_size_mb = get_file_size_mb(filepath)
        is_ps_pdf = ext in SUPPORTED_EXTENSIONS
        
        page_count = 1
        if is_ps_pdf and GS_AVAILABLE:
            page_count = get_real_page_count(filepath)
        
        return jsonify({
            'success': True,
            'filename': temp_filename,
            'original_name': filename,
            'file_size_mb': file_size_mb,
            'page_count': page_count,
            'gs_available': GS_AVAILABLE
        })
    
    return jsonify({'success': True, 'chunk_index': chunk_index})




@app.route('/api/upload', methods=['POST'])
def upload_simple():
    """Endpoint simple para archivos pequeños (sin chunks)"""
    file = request.files.get('file')
    if not file:
        return jsonify({'error': 'No file provided'}), 400

    filename = secure_filename(file.filename)
    ext = filename.split('.')[-1].lower()
    if ext not in SUPPORTED_EXTENSIONS:
        return jsonify({'error': build_unsupported_format_error(ext)}), 400

    upload_id = str(int(time.time() * 1000)) + str(__import__('random').randint(1000, 9999))
    temp_filename = f"{upload_id}_{filename}"
    filepath = os.path.join(app.config['UPLOAD_FOLDER'], temp_filename)
    file.save(filepath)

    file_size_mb = get_file_size_mb(filepath)
    is_ps_pdf = ext in SUPPORTED_EXTENSIONS
    page_count = 1
    if is_ps_pdf and GS_AVAILABLE:
        page_count = get_real_page_count(filepath)

    return jsonify({
        'success': True,
        'filename': temp_filename,
        'original_name': filename,
        'file_size_mb': file_size_mb,
        'page_count': page_count,
        'gs_available': GS_AVAILABLE
    })

@app.route('/api/process', methods=['POST'])
def process_separations():
    data = request.json
    filename = data.get('filename')
    config = data.get('config', {})
    
    if not filename:
        return jsonify({'error': 'No filename provided'}), 400
    
    filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
    if not os.path.exists(filepath):
        return jsonify({'error': 'File not found'}), 404
    
    try:
        ext = os.path.splitext(filename)[1].lower().lstrip('.')
        
        dpi = config.get('dpi', 600)
        lpi = config.get('lpi', 55)
        dot_shape = config.get('dot_shape', 'round')
        auto_angles = config.get('auto_angles', True)

        # Advertencia real (no aleatoria): el ratio DPI/LPI recomendado
        # para halftone es 8:1-16:1 (ver tip de la UI). Fuera de ese rango
        # la trama puede salir con puntos irregulares o el archivo pesa
        # más de lo necesario sin mejorar la calidad.
        real_warnings = []
        dpi_lpi_ratio = dpi / lpi if lpi else 0
        if dpi_lpi_ratio < 8:
            real_warnings.append(
                f'El ratio DPI/LPI configurado es {dpi_lpi_ratio:.1f}:1 (recomendado 8:1-16:1). '
                f'Un ratio bajo puede producir puntos de trama irregulares o "escalonados". '
                f'Considera subir el DPI o bajar el LPI.'
            )
        elif dpi_lpi_ratio > 16:
            real_warnings.append(
                f'El ratio DPI/LPI configurado es {dpi_lpi_ratio:.1f}:1 (recomendado 8:1-16:1). '
                f'Un ratio muy alto no mejora la calidad de la trama y solo aumenta el peso del archivo.'
            )
        
        temp_dir = os.path.join(app.config['OUTPUT_FOLDER'], 'temp_' + filename.rsplit('.', 1)[0])
        os.makedirs(temp_dir, exist_ok=True)
        
        overall_start = time.time()
        
        # Procesar páginas en paralelo
        if ext not in SUPPORTED_EXTENSIONS:
            return jsonify({'error': build_unsupported_format_error(ext)}), 400
        
        if not GS_AVAILABLE:
            return jsonify({'error': (
                'Ghostscript no está instalado o no se encontró en el sistema. '
                'Es necesario para leer archivos .ps/.pdf. '
                'Descárgalo desde https://www.ghostscript.com/download/gsdnld.html '
                'e instálalo, luego reinicia el programa.'
            )}), 400
        
        print("\n" + "=" * 60)
        print("PROCESANDO CON TIFFSEP - SEPARACIÓN REAL DE CANALES")
        print(f"Archivo: {filename}")
        print(f"DPI: {dpi} | LPI: {lpi} | Punto: {dot_shape}")
        print(f"Numba: {'Activado' if NUMBA_AVAILABLE else 'Desactivado'}")
        print("=" * 60 + "\n")
        
        # Timeout escalado por página: con todo el archivo en una sola
        # invocación de Ghostscript, un valor fijo sería insuficiente para
        # archivos grandes y excesivo para archivos pequeños.
        real_page_count = get_real_page_count(filepath) if GS_AVAILABLE else 1
        gs_timeout = max(120, real_page_count * PERFORMANCE_CONFIG['timeout_per_page'])

        plates, tiffsep_warnings = process_with_tiffsep(filepath, temp_dir, dpi, timeout=gs_timeout)
        real_warnings.extend(tiffsep_warnings)
        

        if not plates:
            return jsonify({'error': (
                'No se detectó ninguna placa con tinta en el archivo. '
                'Verifica que el archivo tenga contenido de color y que '
                'esté correctamente exportado en CMYK.'
            )}), 400
        
        # Verificar tamaños consistentes
        sizes = [img.size for img in plates.values()]
        if len(set(sizes)) > 1:
            print(f"[WARN] Tamaños inconsistentes: {set(sizes)}")
            from collections import Counter
            most_common_size = Counter(sizes).most_common(1)[0][0]
            print(f"[FIX] Redimensionando a {most_common_size}")
            for name, img in plates.items():
                if img.size != most_common_size:
                    plates[name] = img.resize(most_common_size, Image.LANCZOS)
        
        # Aplicar semitono, generar preview y guardar a disco - todo en un
        # solo paso por placa. Esto evita mantener en memoria al mismo
        # tiempo las imágenes originales (plates) Y las procesadas
        # (channels): cada placa se libera apenas se guarda, lo cual
        # importa mucho con archivos grandes (lonas/placas a 600+ DPI
        # pueden pesar 500MB+ por canal sin comprimir).
        print("\n[PROCESS] Aplicando semitono...")
        halftone_start = time.time()

        output_dir = os.path.join(app.config['OUTPUT_FOLDER'], filename.rsplit('.', 1)[0])
        os.makedirs(output_dir, exist_ok=True)

        angles = [15, 75, 0, 45, 30, 60, 10, 80, 20, 70]
        results = {}
        saved_files = []
        thumbnails = OrderedDict()  # solo miniaturas, para composite/instrucciones
        plate_names = list(plates.keys())
        real_size = (0, 0)

        for idx, name in enumerate(plate_names):
            img_plate = plates.pop(name)  # liberar referencia en plates de inmediato
            arr_plate = np.array(img_plate)
            del img_plate

            angle = angles[idx % len(angles)] if auto_angles else config.get('angle', 45)
            halftoned_arr = apply_halftone(arr_plate, lpi, dpi, angle, dot_shape)
            del arr_plate

            pil_img = Image.fromarray(halftoned_arr, mode='L')
            del halftoned_arr
            real_size = pil_img.size

            # Miniatura compartida: se usa tanto para el preview en la
            # respuesta JSON como para el composite visual y las
            # instrucciones de texto. Generarla una sola vez evita tener
            # la imagen a resolución completa viva más de lo necesario.
            #
            # Usa smart_thumbnail en vez de un resize directo: una trama de
            # halftone es una señal de alta frecuencia, y reducirla de un
            # solo salto (5100px -> 400px, ~13x) genera aliasing severo que
            # hace parecer que el punto de la trama es de un tamaño
            # completamente distinto al real, aunque el PNG guardado en
            # disco a resolución completa esté correcto.
            preview_img = smart_thumbnail(pil_img.copy(), target_max=800)
            results[name] = {
                'preview': pil_to_base64(preview_img),
                'size': real_size
            }
            thumbnails[name] = preview_img

            # Guardar a disco y liberar la imagen completa de memoria
            safe_name = name.replace(' ', '_').replace('/', '_').lower()
            safe_name = re.sub(r'[^\w\-_]', '', safe_name)
            out_path = os.path.join(output_dir, f"{safe_name}.png")
            pil_img.save(out_path, 'PNG', optimize=False)
            saved_files.append({'name': name, 'path': out_path})
            print(f"[SAVE] {name}: {real_size}")
            del pil_img

        # Manifiesto: guarda el nombre real de cada placa junto a su archivo,
        # en el ORDEN EN QUE SE PROCESARON (no alfabético). Sin esto, cualquier
        # endpoint que necesite volver a saber "qué archivo es qué color" tenía
        # que adivinarlo re-listando el directorio y ordenando alfabéticamente,
        # lo cual rompe el emparejamiento nombre<->archivo en cuanto el orden
        # alfabético de los nombres de archivo no coincide con el orden real
        # de extracción de Ghostscript (ej. C, M, Y, K procesados en ese orden
        # pero listados como black, cyan, magenta, yellow al ordenar A-Z).
        manifest_path = os.path.join(output_dir, 'manifest.json')
        try:
            import json as _json
            manifest = [
                {'name': sf['name'], 'filename': os.path.basename(sf['path'])}
                for sf in saved_files
            ]
            with open(manifest_path, 'w', encoding='utf-8') as mf:
                _json.dump(manifest, mf, ensure_ascii=False, indent=2)
        except Exception as _e:
            print(f"[WARN] No se pudo escribir manifest.json: {_e}")

        halftone_time = time.time() - halftone_start
        print(f"[PROCESS] Semitono completado en {halftone_time:.1f} segundos")
        
        # Limpiar temporal
        shutil.rmtree(temp_dir, ignore_errors=True)
        
        # Crear ZIP
        zip_path = os.path.join(output_dir, 'separaciones.zip')
        # Normalizar para URL: obtener path relativo a OUTPUT_FOLDER
        rel_zip = os.path.relpath(zip_path, app.config['OUTPUT_FOLDER']).replace(os.sep, '/').replace('\\', '/')

        with zipfile.ZipFile(zip_path, 'w') as zf:
            for sf in saved_files:
                zf.write(sf['path'], os.path.basename(sf['path']))
        
        # Instrucciones
        instrucciones = os.path.join(output_dir, 'INSTRUCCIONES_SERIGRAFIA.txt')
        with open(instrucciones, 'w', encoding='utf-8') as f:
            f.write("=" * 60 + "\n")
            f.write("INSTRUCCIONES PARA SERIGRAFÍA\n")
            f.write("=" * 60 + "\n\n")
            f.write(f"Archivo: {filename}\n")
            f.write(f"Fecha: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n")
            f.write(f"TOTAL DE COLORES: {len(thumbnails)}\n\n")
            f.write("LISTA DE PANTALLAS:\n")
            f.write("-" * 40 + "\n")
            for i, name in enumerate(thumbnails.keys(), 1):
                f.write(f"{i}. {name} - {real_size[0]}x{real_size[1]}px\n")
            f.write("\n")
            f.write(f"PARÁMETROS: LPI={lpi}, DPI={dpi}, PUNTO={dot_shape}\n")
            f.write(f"\nTIEMPO DE PROCESAMIENTO: {time.time() - overall_start:.1f} segundos\n")
        
        # El composite se genera a partir de las miniaturas (no de las
        # imágenes a resolución completa): es solo una vista previa visual,
        # no algo que se imprima, así que no vale la pena pagar su costo
        # de memoria (un array RGB completo) con archivos grandes.
        composite = create_composite_preview(thumbnails)
        
        total_time = time.time() - overall_start
        print(f"\n[PROCESS] ¡COMPLETADO! {len(thumbnails)} colores en {total_time:.1f} segundos\n")
        
        # Convertir OrderedDict a dict para JSON
        channels_dict = {k: v for k, v in results.items()}
        
        return jsonify({
            'success': True,
            'channels': channels_dict,
            'composite': pil_to_base64(composite),
            'size': real_size,
            'zip_path': rel_zip,
            'color_count': len(thumbnails),
            'processing_time': total_time,
            'warnings': real_warnings
        })
        
    except Exception as e:
        import traceback
        print(f"[ERROR] {traceback.format_exc()}")
        return jsonify({'error': friendly_error_message(e)}), 500


@app.route('/api/analyze', methods=['POST'])
def analyze():
    data = request.json
    sep_data = data.get('separation')
    ref_data = data.get('reference')

    if not sep_data or not ref_data:
        return jsonify({'error': 'Missing images'}), 400

    try:
        sep_bytes = base64.b64decode(sep_data.split(',')[1])
        ref_bytes = base64.b64decode(ref_data.split(',')[1])

        sep_img = Image.open(io.BytesIO(sep_bytes))
        ref_img = Image.open(io.BytesIO(ref_bytes))

        result = analyze_differences(sep_img, ref_img)

        return jsonify({
            'success': True,
            'score': result['score'],
            'diff_map': pil_to_base64(result['diff_map']),
            'findings': result['findings'],
            'metrics': result['metrics']
        })

    except Exception as e:
        import traceback
        print(f"[ERROR /api/analyze] {traceback.format_exc()}")
        return jsonify({'error': friendly_error_message(e)}), 500


@app.route('/api/download/<path:filename>')
def download_file(filename):
    safe_path = os.path.normpath(filename)
    if safe_path.startswith('..'):
        return jsonify({'error': 'Invalid path'}), 403
    filepath = os.path.join(app.config['OUTPUT_FOLDER'], safe_path)
    if os.path.exists(filepath) and os.path.isfile(filepath):
        return send_file(filepath, as_attachment=True)
    return jsonify({'error': 'File not found'}), 404


def load_job_manifest(output_dir):
    """
    Devuelve la lista ordenada [{'name': ..., 'filename': ...}, ...] de las
    placas de un job, en el ORDEN REAL de procesamiento.

    Usa manifest.json si existe (jobs generados después de este fix). Si no
    existe (jobs viejos, o si algo falló al escribirlo), cae de vuelta al
    comportamiento anterior: listar el directorio y ordenar alfabéticamente,
    infiriendo el nombre desde el nombre de archivo. Ese fallback es el que
    causaba el desalineamiento nombre<->archivo, así que solo debe usarse
    como último recurso.
    """
    import json as _json

    manifest_path = os.path.join(output_dir, 'manifest.json')
    if os.path.exists(manifest_path):
        try:
            with open(manifest_path, 'r', encoding='utf-8') as mf:
                manifest = _json.load(mf)
            # Validar que los archivos referenciados todavía existan
            manifest = [
                item for item in manifest
                if os.path.exists(os.path.join(output_dir, item['filename']))
            ]
            if manifest:
                return manifest
        except Exception as e:
            print(f"[WARN] manifest.json inválido, usando fallback: {e}")

    # Fallback: comportamiento anterior (orden alfabético inferido)
    png_files = sorted([
        f for f in os.listdir(output_dir)
        if f.endswith('.png') and f not in ('separaciones.zip', 'separaciones.pdf', 'composite_visual.png')
    ])
    return [
        {'name': f.replace('.png', '').replace('_', ' ').title(), 'filename': f}
        for f in png_files
    ]


@app.route('/api/generate_pdf', methods=['POST'])
def generate_pdf():
    """
    Genera un PDF de múltiples páginas a partir de las separaciones PNG guardadas.
    Cada página del PDF contiene una separación con su nombre.
    """
    data = request.json
    job_name = data.get('job_name')  # nombre de carpeta en outputs
    color_names = data.get('color_names', [])  # lista ordenada de nombres de color (opcional, override)

    if not job_name:
        return jsonify({'error': 'job_name requerido'}), 400

    output_dir = os.path.join(app.config['OUTPUT_FOLDER'], job_name)
    if not os.path.exists(output_dir):
        return jsonify({'error': 'No se encontraron separaciones. Procesa el archivo primero.'}), 404

    # Orden real de procesamiento (no alfabético) + nombre correcto de cada
    # placa, leído del manifiesto guardado al momento de procesar. Antes esto
    # se re-derivaba con sorted(os.listdir(...)), lo que desalineaba el
    # nombre de color con el archivo cuando el orden alfabético no coincidía
    # con el orden real de extracción de Ghostscript.
    manifest = load_job_manifest(output_dir)

    if not manifest:
        return jsonify({'error': 'No se encontraron archivos PNG de separaciones'}), 404

    try:
        pages = []

        for entry in manifest:
            png_file = entry['filename']
            png_path = os.path.join(output_dir, png_file)
            img = Image.open(png_path).convert('RGB')

            # Sin banda de etiqueta: la página del PDF es exactamente la
            # imagen de la separación, al mismo tamaño en píxeles. Agregar
            # una banda arriba le sumaba altura al lienzo, y como el PDF se
            # guarda a una resolución fija (resolution=150), esa altura extra
            # cambia el tamaño físico de la página — lo cual encoge o
            # desalinea el arte real respecto al tamaño de impresión
            # esperado. El nombre de cada placa ya está en el nombre de
            # archivo/manifiesto para identificarla, así que no hace falta
            # dibujarlo encima del área imprimible.
            pages.append(img)

        if not pages:
            return jsonify({'error': 'No se pudieron cargar las imágenes'}), 500

        # Guardar PDF
        pdf_path = os.path.join(output_dir, 'separaciones.pdf')
        pages[0].save(
            pdf_path,
            format='PDF',
            save_all=True,
            append_images=pages[1:],
            resolution=150
        )

        print(f'[PDF] Generado: {pdf_path} ({len(pages)} páginas)')
        return jsonify({
            'success': True,
            'pdf_path': pdf_path,
            'pages': len(pages)
        })

    except Exception as e:
        import traceback
        print(f'[PDF ERROR] {traceback.format_exc()}')
        return jsonify({'error': friendly_error_message(e)}), 500


@app.route('/api/cleanup', methods=['POST'])
def cleanup():
    try:
        for folder in [app.config['UPLOAD_FOLDER'], app.config['OUTPUT_FOLDER']]:
            shutil.rmtree(folder, ignore_errors=True)
            os.makedirs(folder, exist_ok=True)
        return jsonify({'success': True})
    except Exception as e:
        import traceback
        print(f"[ERROR /api/cleanup] {traceback.format_exc()}")
        return jsonify({'error': friendly_error_message(e)}), 500


# =============================================================================
# MAIN
# =============================================================================



# =============================================================================
# VISUALIZACIÓN DE SEPARACIONES CON COLORES REALES
# =============================================================================

# Paleta de colores CMYK estándar
CMYK_COLORS = {
    'Cyan': (0, 180, 216),
    'Magenta': (230, 58, 110),
    'Yellow': (245, 200, 0),
    'Black': (26, 26, 46),
}

# Colores comunes para spot colors (fallback)
SPOT_COLOR_PALETTE = [
    (255, 99, 132), (75, 192, 192), (153, 102, 255), (255, 159, 64),
    (199, 199, 199), (83, 102, 255), (255, 205, 86), (201, 203, 207),
    (54, 162, 235), (255, 99, 132), (255, 206, 86), (75, 192, 192),
    (153, 102, 255), (255, 159, 64), (255, 99, 71), (100, 149, 237),
    (255, 215, 0), (0, 128, 128), (128, 0, 128), (255, 165, 0),
]

def get_color_for_separation(name, idx=0):
    """
    Asigna un color RGB de referencia a una separación, basado en su
    nombre. Importante: este color NUNCA pretende ser el color Pantone
    real (eso requeriría una base de datos con licencia de Pantone LLC,
    que es contenido propietario). Es solo un color distintivo y estable
    para diferenciar visualmente las placas en la interfaz - el usuario
    puede corregirlo manualmente al color real con el selector de color
    que ya está disponible junto a cada separación.
    """
    name_lower = name.lower().strip()

    # CMYK estándar: estos sí son colores convencionales reales (el cyan,
    # magenta, amarillo y negro de impresión), no hay ambigüedad aquí.
    for cmyk_name, color in CMYK_COLORS.items():
        if cmyk_name.lower() in name_lower:
            return color

    # Colores por nombre en español/inglés: es una ayuda visual razonable
    # (si la placa se llama "Rojo", mostrar un tono rojizo orienta al
    # usuario), no pretende ser un Pantone exacto.
    color_map = {
        'rojo': (220, 20, 60), 'red': (220, 20, 60),
        'azul': (30, 144, 255), 'blue': (30, 144, 255),
        'verde': (34, 139, 34), 'green': (34, 139, 34),
        'amarillo': (255, 215, 0), 'yellow': (255, 215, 0),
        'naranja': (255, 140, 0), 'orange': (255, 140, 0),
        'morado': (128, 0, 128), 'purple': (128, 0, 128),
        'rosa': (255, 105, 180), 'pink': (255, 105, 180),
        'gris': (128, 128, 128), 'gray': (128, 128, 128), 'grey': (128, 128, 128),
        'blanco': (255, 255, 255), 'white': (255, 255, 255),
        'negro': (0, 0, 0), 'black': (0, 0, 0),
        'cafe': (139, 69, 19), 'brown': (139, 69, 19), 'marrón': (139, 69, 19),
        'turquesa': (64, 224, 208), 'teal': (64, 224, 208),
        'violeta': (238, 130, 238), 'violet': (238, 130, 238),
        'dorado': (255, 215, 0), 'gold': (255, 215, 0),
        'plateado': (192, 192, 192), 'silver': (192, 192, 192),
        'bronze': (205, 127, 50), 'bronce': (205, 127, 50),
        'cobre': (184, 115, 51), 'copper': (184, 115, 51),
    }

    for color_name, color in color_map.items():
        if color_name in name_lower:
            return color

    # Para cualquier otro nombre (Pantone con número, spot color sin
    # traducción reconocida, etc.): color estable derivado del nombre
    # completo vía hash, no solo del número. Esto evita que "286 C" y
    # "286 U" (Pantones distintos) terminen mostrando el mismo color por
    # compartir el número, y asegura que el mismo nombre dé siempre el
    # mismo color entre sesiones sin pretender ser una guía Pantone real.
    h = hashlib.md5(name_lower.encode()).hexdigest()
    hue = int(h[:8], 16) / 0xffffffff
    sat = 0.55 + (int(h[8:10], 16) / 255) * 0.25   # 0.55-0.80: evita grises desaturados
    light = 0.45 + (int(h[10:12], 16) / 255) * 0.15  # 0.45-0.60: evita extremos muy claros/oscuros
    r, g, b = colorsys.hls_to_rgb(hue, light, sat)
    return (int(r * 255), int(g * 255), int(b * 255))


@app.route('/api/separation_info', methods=['POST'])
def get_separation_info():
    """
    Devuelve información de colores para cada separación.
    Incluye el color RGB asignado, si es CMYK o spot, y el preview.
    """
    data = request.json
    job_name = data.get('job_name')

    if not job_name:
        return jsonify({'error': 'job_name requerido'}), 400

    output_dir = os.path.join(app.config['OUTPUT_FOLDER'], job_name)
    if not os.path.exists(output_dir):
        return jsonify({'error': 'Trabajo no encontrado'}), 404

    try:
        # Nombre y orden reales desde el manifiesto (evita inferir el nombre
        # solo desde el nombre de archivo, que pierde caracteres especiales
        # que sí se guardan en el manifiesto, ej. apóstrofes, "&", etc.)
        manifest = load_job_manifest(output_dir)

        separations_info = []
        for idx, entry in enumerate(manifest):
            png_file = entry['filename']
            name = entry['name']

            # Determinar si es CMYK o spot
            is_cmyk = any(c in name.lower() for c in ['cyan', 'magenta', 'yellow', 'black'])

            # Obtener color asignado
            color_rgb = get_color_for_separation(name, idx)
            hex_color = '#{:02x}{:02x}{:02x}'.format(*color_rgb)

            # Obtener tamaño de la imagen
            img_path = os.path.join(output_dir, png_file)
            try:
                with Image.open(img_path) as img:
                    size = img.size
            except:
                size = [0, 0]

            separations_info.append({
                'name': name,
                'filename': png_file,
                'is_cmyk': is_cmyk,
                'color_rgb': color_rgb,
                'hex_color': hex_color,
                'size': size,
                'index': idx
            })

        return jsonify({
            'success': True,
            'separations': separations_info,
            'count': len(separations_info)
        })

    except Exception as e:
        import traceback
        print(f"[ERROR /api/separation_info] {traceback.format_exc()}")
        return jsonify({'error': friendly_error_message(e)}), 500


@app.route('/api/composite_visual', methods=['POST'])
def generate_composite_visual():
    """
    Genera una composición visual real donde cada separación se muestra en su color.
    Soporta modos: overprint (suma de tintas) o knockout (solo donde no hay otra tinta).
    """
    data = request.json
    job_name = data.get('job_name')
    mode = data.get('mode', 'overprint')  # overprint o knockout

    if not job_name:
        return jsonify({'error': 'job_name requerido'}), 400

    output_dir = os.path.join(app.config['OUTPUT_FOLDER'], job_name)
    if not os.path.exists(output_dir):
        return jsonify({'error': 'Trabajo no encontrado'}), 404

    try:
        # Nombre y orden reales desde el manifiesto: para "overprint" el
        # orden en que se superponen las tintas importa para que la
        # simulación se parezca al resultado real de imprenta.
        manifest = load_job_manifest(output_dir)

        if not manifest:
            return jsonify({'error': 'No hay separaciones'}), 404

        # Cargar primera imagen como base
        base_img = Image.open(os.path.join(output_dir, manifest[0]['filename'])).convert('L')
        w, h = base_img.size

        # Crear canvas RGBA
        composite = np.zeros((h, w, 4), dtype=np.uint8)
        composite[:, :, 3] = 255  # Alpha completo

        # Procesar cada separación
        for idx, entry in enumerate(manifest):
            png_file = entry['filename']
            img = Image.open(os.path.join(output_dir, png_file)).convert('L')
            if img.size != (w, h):
                img = img.resize((w, h), Image.LANCZOS)

            arr = np.array(img)
            name = entry['name']
            color_rgb = get_color_for_separation(name, idx)

            # Invertir: en separación, negro = tinta, blanco = papel
            # Queremos: tinta en color, papel transparente
            ink_mask = (255 - arr).astype(np.float32) / 255.0  # 0-1, donde 1 = tinta

            if mode == 'overprint':
                # Overprint: las tintas se suman (como en impresión real)
                for c in range(3):
                    composite[:, :, c] = np.clip(
                        composite[:, :, c].astype(np.float32) + 
                        ink_mask * color_rgb[c] * 0.8, 0, 255
                    ).astype(np.uint8)
            else:
                # Knockout: cada tinta reemplaza lo que está debajo
                for c in range(3):
                    composite[:, :, c] = np.where(
                        ink_mask > 0.1,
                        (composite[:, :, c].astype(np.float32) * (1 - ink_mask) + 
                         color_rgb[c] * ink_mask * 0.9).astype(np.uint8),
                        composite[:, :, c]
                    )

        # Convertir a PIL y guardar
        result_img = Image.fromarray(composite, mode='RGBA')

        # Guardar temporal
        preview_path = os.path.join(output_dir, 'composite_visual.png')
        result_img.save(preview_path, 'PNG')

        # Devolver como base64
        return jsonify({
            'success': True,
            'composite': pil_to_base64(result_img),
            'mode': mode,
            'colors_used': len(manifest)
        })

    except Exception as e:
        import traceback
        print(f"[ERROR /api/composite_visual] {traceback.format_exc()}")
        return jsonify({'error': friendly_error_message(e)}), 500


@app.route('/api/separation_preview/<job_name>/<filename>')
def get_separation_preview(job_name, filename):
    """Devuelve una separación individual coloreada para el preview."""
    output_dir = os.path.join(app.config['OUTPUT_FOLDER'], job_name)
    filepath = os.path.join(output_dir, filename)

    if not os.path.exists(filepath):
        return jsonify({'error': 'Archivo no encontrado'}), 404

    try:
        img = Image.open(filepath).convert('L')
        name = filename.replace('.png', '').replace('_', ' ').title()

        # Encontrar índice para color (mismo orden que separation_info y
        # composite_visual, para que el color asignado a esta placa sea
        # siempre el mismo sin importar desde qué pantalla se consulte)
        manifest = load_job_manifest(output_dir)
        manifest_filenames = [m['filename'] for m in manifest]
        idx = manifest_filenames.index(filename) if filename in manifest_filenames else 0
        color_rgb = get_color_for_separation(name, idx)

        # Crear imagen coloreada
        arr = np.array(img)
        h, w = arr.shape

        # Invertir: negro = tinta
        ink_mask = (255 - arr).astype(np.float32) / 255.0

        colored = np.zeros((h, w, 3), dtype=np.uint8)
        for c in range(3):
            colored[:, :, c] = (ink_mask * color_rgb[c]).astype(np.uint8)

        result = Image.fromarray(colored, mode='RGB')
        return jsonify({
            'success': True,
            'preview': pil_to_base64(result),
            'name': name,
            'hex_color': '#{:02x}{:02x}{:02x}'.format(*color_rgb),
            'color_rgb': color_rgb
        })

    except Exception as e:
        import traceback
        print(f"[ERROR /api/separation_preview] {traceback.format_exc()}")
        return jsonify({'error': friendly_error_message(e)}), 500


# =============================================================================
# ANÁLISIS DE DIFERENCIAS MEJORADO
# =============================================================================

def _largest_ink_blob_bbox(mask):
    """
    mask: array 2D booleano, True = hay tinta/contenido.
    Devuelve (x0, y0, x1, y1) del blob conexo MÁS GRANDE, ignorando manchas
    pequeñas y aisladas (típicamente cruces de registro, barras de color,
    texto de especificaciones) que no forman parte del arte principal.
    Si scipy no está disponible, cae a la bbox de todo el contenido (menos
    robusto ante guías de registro, pero sigue funcionando).
    """
    if not mask.any():
        return None
    try:
        from scipy import ndimage
        labeled, n = ndimage.label(mask)
        if n == 0:
            return None
        sizes = ndimage.sum(mask, labeled, index=range(1, n + 1))
        largest_label = int(np.argmax(sizes)) + 1
        ys, xs = np.where(labeled == largest_label)
    except ImportError:
        ys, xs = np.where(mask)
    return int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1


def _phase_correlation_shift(a, b):
    """
    Encuentra el desplazamiento (dx, dy) que mejor alinea b sobre a usando
    correlación de fase (FFT). a y b deben tener el mismo tamaño. Devuelve
    también un score de confianza 0-1 (qué tan definido/agudo es el pico:
    un pico muy marcado = coincidencia clara, un pico plano = imágenes que
    no se parecen y la corrección no es confiable).
    """
    h, w = a.shape
    Fa = np.fft.fft2(a)
    Fb = np.fft.fft2(b)
    R = Fa * np.conj(Fb)
    denom = np.abs(R)
    denom[denom < 1e-8] = 1e-8
    R /= denom
    r = np.fft.ifft2(R).real

    peak_idx = np.unravel_index(np.argmax(r), r.shape)
    peak_val = r[peak_idx]
    mean_val = float(np.mean(r))
    std_val = float(np.std(r)) + 1e-8
    confidence = float(np.clip((peak_val - mean_val) / (std_val * 6.0), 0.0, 1.0))

    dy, dx = peak_idx
    if dy > h // 2:
        dy -= h
    if dx > w // 2:
        dx -= w
    return int(dx), int(dy), confidence


@app.route('/api/auto_align', methods=['POST'])
def auto_align():
    """
    Calcula automáticamente escala + posición para que la imagen de
    referencia encaje sobre la separación, en vez de que el usuario tenga
    que mover los sliders a mano.

    Estrategia (2 pasos, sin asumir que la referencia y la separación
    tienen el mismo tamaño ni el mismo contenido extra):

    1. BBOX POR CONTENIDO: en la separación (que trae guías de registro,
       texto de specs, y el tamaño real de placa) se detecta el blob de
       tinta conexo MÁS GRANDE — asumiendo que el arte real es la mancha
       de tinta más grande, y las guías/marcas de registro son manchas
       pequeñas y separadas. Se hace lo mismo en la referencia. Con las dos
       cajas se calcula la escala y el desplazamiento que hacen coincidir
       esas cajas.

    2. REFINAMIENTO POR CORRELACIÓN DE FASE (FFT): con la referencia ya
       escalada y ubicada según el paso 1, se busca el ajuste fino de
       posición (unos pocos píxeles) comparando el patrón de tinta real,
       no solo la caja — esto corrige el registro con precisión aunque la
       forma del arte no sea un rectángulo perfecto.

    Devuelve scale/offset_x/offset_y en el mismo espacio de píxeles reales
    que ya usa /api/analyze_detailed (align_scale, align_x, align_y), más
    un score de confianza para que el frontend pueda avisar si el match es
    débil y conviene ajustar a mano.
    """
    data = request.json
    job_name = data.get('job_name')
    ref_data = data.get('reference')

    if not job_name or not ref_data:
        return jsonify({'error': 'job_name y reference requeridos'}), 400

    output_dir = os.path.join(app.config['OUTPUT_FOLDER'], job_name)
    if not os.path.exists(output_dir):
        return jsonify({'error': 'Trabajo no encontrado'}), 404

    try:
        manifest = load_job_manifest(output_dir)
        if not manifest:
            return jsonify({'error': 'No hay separaciones'}), 404

        ref_bytes = base64.b64decode(ref_data.split(',')[1])
        ref_img_full = Image.open(io.BytesIO(ref_bytes)).convert('L')

        sample = Image.open(os.path.join(output_dir, manifest[0]['filename'])).convert('L')
        sep_w, sep_h = sample.size

        # Trabajar en baja resolución (lado largo ~700px) para que sea
        # rápido y para que el detalle de trama/semitono no ensucie la
        # detección de blobs ni la correlación.
        WORK_DIM = 700
        f_sep = WORK_DIM / max(sep_w, sep_h)
        proc_w, proc_h = max(1, round(sep_w * f_sep)), max(1, round(sep_h * f_sep))

        # Cobertura de tinta combinada de la separación completa (mismo
        # modelo "luz que atraviesa" que usa analyze_detailed)
        light_through = np.ones((proc_h, proc_w), dtype=np.float32)
        for entry in manifest:
            plate = Image.open(os.path.join(output_dir, entry['filename'])).convert('L')
            plate = plate.resize((proc_w, proc_h), Image.LANCZOS)
            arr = np.array(plate).astype(np.float32)
            ink = (255.0 - arr) / 255.0
            light_through *= (1.0 - ink)
        sep_ink = 1.0 - light_through  # 0..1, cobertura de tinta

        sep_mask = sep_ink > 0.04
        bbox_sep = _largest_ink_blob_bbox(sep_mask)
        if bbox_sep is None:
            return jsonify({'error': 'La separación no tiene tinta detectable para alinear'}), 400

        # Referencia a la misma resolución de trabajo
        ref_w, ref_h = ref_img_full.size
        f_ref = WORK_DIM / max(ref_w, ref_h)
        rproc_w, rproc_h = max(1, round(ref_w * f_ref)), max(1, round(ref_h * f_ref))
        ref_small = ref_img_full.resize((rproc_w, rproc_h), Image.LANCZOS)
        ref_arr = np.array(ref_small).astype(np.float32)
        ref_ink = (255.0 - ref_arr) / 255.0
        ref_mask = ref_ink > 0.04
        bbox_ref = _largest_ink_blob_bbox(ref_mask)
        if bbox_ref is None:
            return jsonify({'error': 'No se detectó contenido en la imagen de referencia'}), 400

        # --- Paso 1: escala y offset por bounding box (espacio real, px) ---
        sx0, sy0, sx1, sy1 = [v / f_sep for v in bbox_sep]
        rx0, ry0, rx1, ry1 = [v / f_ref for v in bbox_ref]
        sep_box_w, sep_box_h = sx1 - sx0, sy1 - sy0
        ref_box_w, ref_box_h = max(rx1 - rx0, 1e-6), max(ry1 - ry0, 1e-6)

        scale = ((sep_box_w / ref_box_w) + (sep_box_h / ref_box_h)) / 2.0
        scale = float(np.clip(scale, 0.10, 3.00))  # mismos límites que el slider de escala

        offset_x = sx0 - rx0 * scale
        offset_y = sy0 - ry0 * scale

        # --- Paso 2: refinamiento por correlación de fase (FFT) ---
        # Construir, en la grilla de trabajo de la separación, cómo se vería
        # la referencia ya escalada/ubicada según el paso 1, para comparar
        # patrón de tinta contra patrón de tinta (no solo cajas).
        ref_scaled_full = ref_img_full.resize(
            (max(1, round(ref_w * scale)), max(1, round(ref_h * scale))), Image.LANCZOS
        )
        ref_placed_full = Image.new('L', (sep_w, sep_h), 255)
        ref_placed_full.paste(ref_scaled_full, (round(offset_x), round(offset_y)))
        ref_placed_proc = ref_placed_full.resize((proc_w, proc_h), Image.LANCZOS)
        ref_placed_ink = (255.0 - np.array(ref_placed_proc).astype(np.float32)) / 255.0

        dx, dy, confidence = _phase_correlation_shift(sep_ink, ref_placed_ink)

        # Límite de seguridad: si la corrección que pide la FFT es enorme
        # (más de 15% del lado de trabajo), probablemente es ruido/alias del
        # wrap-around de la FFT, no una alineación real; se descarta y se
        # deja el resultado del paso 1 solo.
        max_shift = 0.15 * WORK_DIM
        if abs(dx) <= max_shift and abs(dy) <= max_shift:
            offset_x -= dx / f_sep
            offset_y -= dy / f_sep
        else:
            confidence = min(confidence, 0.4)

        return jsonify({
            'success': True,
            'scale': scale,
            'offset_x': round(offset_x),
            'offset_y': round(offset_y),
            'confidence': round(confidence, 2)
        })

    except Exception as e:
        import traceback
        print(f"[ERROR /api/auto_align] {traceback.format_exc()}")
        return jsonify({'error': friendly_error_message(e)}), 500


@app.route('/api/analyze_detailed', methods=['POST'])
def analyze_detailed():
    """
    Análisis detallado de diferencias entre separación y referencia.
    Detecta: cobertura faltante, overprint incorrecto, registro, ganancia de punto.
    """
    data = request.json
    job_name = data.get('job_name')
    ref_data = data.get('reference')  # base64 de imagen de referencia

    if not job_name or not ref_data:
        return jsonify({'error': 'job_name y reference requeridos'}), 400

    output_dir = os.path.join(app.config['OUTPUT_FOLDER'], job_name)
    if not os.path.exists(output_dir):
        return jsonify({'error': 'Trabajo no encontrado'}), 404

    try:
        # Cargar imagen de referencia
        ref_bytes = base64.b64decode(ref_data.split(',')[1])
        ref_img = Image.open(io.BytesIO(ref_bytes)).convert('RGB')

        # Buscar separaciones
        png_files = sorted([
            f for f in os.listdir(output_dir)
            if f.endswith('.png') and f != 'separaciones.zip' and f != 'separaciones.pdf' and f != 'composite_visual.png'
        ])

        if not png_files:
            return jsonify({'error': 'No hay separaciones'}), 404

        align_scale = max(0.05, float(data.get('align_scale', 1.0)))
        align_x     = int(float(data.get('align_x', 0)))
        align_y     = int(float(data.get('align_y', 0)))

        # Redimensionar y alinear referencia al tamaño de las separaciones
        sample_img = Image.open(os.path.join(output_dir, png_files[0])).convert('L')
        w, h = sample_img.size

        # Escalar la referencia según el factor del usuario
        scaled_w = max(1, int(ref_img.width  * align_scale))
        scaled_h = max(1, int(ref_img.height * align_scale))
        ref_scaled = ref_img.resize((scaled_w, scaled_h), Image.LANCZOS)

        # Pegar en un lienzo blanco del mismo tamaño que las separaciones,
        # con el offset X/Y indicado (la zona que quede fuera del lienzo se recorta)
        aligned_ref = Image.new('RGB', (w, h), (255, 255, 255))
        paste_box = (align_x, align_y)
        # Validar que al menos parte de la imagen quede dentro del lienzo
        aligned_ref.paste(ref_scaled, paste_box)
        ref_arr = np.array(aligned_ref).astype(np.float32)

        # Analizar cada separación
        findings = []
        # Para el mapa/score global se necesita una cobertura COMBINADA que
        # siga en escala 0-1 sin importar cuántas placas tengan tinta en el
        # mismo punto. Sumar la tinta de cada placa de forma aritmética
        # (como hacía esta función antes) puede superar 1.0 fácilmente en
        # zonas con varias tintas superpuestas (ej. 5 placas al 80% suman
        # 4.0), lo que generaba diferencias de "104%" o más sin que hubiera
        # ningún error real. El modelo correcto es el mismo que describe
        # cómo se ve la tinta superpuesta en la práctica: cada capa deja
        # pasar la luz proporcionalmente, así que se combinan multiplicando
        # la "luz no absorbida" de cada placa, no sumando la tinta.
        light_through = np.ones((h, w), dtype=np.float32)

        for idx, png_file in enumerate(png_files):
            sep_img = Image.open(os.path.join(output_dir, png_file)).convert('L').resize((w, h), Image.LANCZOS)
            sep_arr = np.array(sep_img).astype(np.float32)

            # Invertir: en separación, 0 = tinta, 255 = papel
            ink = (255 - sep_arr) / 255.0
            light_through *= (1.0 - ink)

            name = png_file.replace('.png', '').replace('_', ' ').title()

            # Calcular métricas
            ink_pixels = np.sum(ink > 0.1)
            total_pixels = w * h
            coverage_pct = ink_pixels / total_pixels * 100

            # Detectar problemas de cobertura. Una cobertura baja (1-5%) no
            # es por sí sola sospechosa: colores de acento, detalles finos
            # o tintas secundarias son legítimamente así de pequeños por
            # diseño. Lo que sí amerita una advertencia más seria es que la
            # placa esté prácticamente vacía del todo (<0.3%), que es el
            # patrón real de "esta separación no se generó bien".
            if coverage_pct < 0.3:
                findings.append({
                    'title': f'{name} - Posiblemente vacía',
                    'status': 'warn',
                    'pct': round(coverage_pct, 2),
                    'msg': f'Solo {coverage_pct:.2f}% de cobertura, prácticamente sin tinta. Verifica que la separación se haya generado correctamente.'
                })
            elif coverage_pct < 5:
                findings.append({
                    'title': f'{name} - Cobertura baja',
                    'status': 'ok',
                    'pct': round(coverage_pct, 1),
                    'msg': f'{coverage_pct:.1f}% de cobertura. Es normal para colores de acento o detalles pequeños; revisa solo si esperabas más área.'
                })

            # Detectar áreas con tinta donde la referencia es blanca (overprint no deseado)
            ref_gray = np.mean(ref_arr, axis=2)
            ref_ink = (255 - ref_gray) / 255.0  # tinta en referencia

            # Diferencia: tinta en separación pero no en referencia
            false_ink = np.sum((ink > 0.3) & (ref_ink < 0.1)) / total_pixels * 100
            if false_ink > 10:
                findings.append({
                    'title': f'{name} - Posible overprint no deseado',
                    'status': 'warn',
                    'pct': int(false_ink),
                    'msg': f'{int(false_ink)}% de tinta donde la referencia es blanca. Revisa overprint.'
                })

        # Cobertura combinada real (0-1), para el mapa de diferencias y el
        # score global - ver nota arriba sobre por qué no es una suma simple.
        total_coverage = 1.0 - light_through

        # Análisis global
        total_coverage_pct = int(np.mean(total_coverage) * 100)

        # Crear mapa de diferencias
        ref_gray = np.mean(ref_arr, axis=2)
        ref_ink = (255 - ref_gray) / 255.0

        diff_map = np.zeros((h, w, 4), dtype=np.uint8)

        # Diferencia absoluta
        diff = np.abs(total_coverage - ref_ink)

        # Pintar: verde = coincidencia, rojo = diferencia
        high_diff = diff > 0.3
        med_diff = (diff > 0.15) & ~high_diff

        diff_map[high_diff] = [220, 60, 60, 200]  # Rojo: diferencia alta
        diff_map[med_diff] = [245, 200, 0, 150]   # Amarillo: diferencia media
        diff_map[~(high_diff | med_diff)] = [0, 200, 100, 80]  # Verde: coincidencia

        diff_img = Image.fromarray(diff_map, mode='RGBA')

        # Score general
        avg_diff = np.mean(diff) * 100
        score = int(avg_diff)

        # Agregar hallazgo global
        if score < 15:
            findings.insert(0, {
                'title': 'Coincidencia general',
                'status': 'ok',
                'pct': 100 - score,
                'msg': f'Excelente coincidencia con referencia. Diferencia promedio: {score}%'
            })
        elif score < 30:
            findings.insert(0, {
                'title': 'Coincidencia general',
                'status': 'warn',
                'pct': 100 - score,
                'msg': f'Diferencia moderada ({score}%). Revisa separaciones individuales.'
            })
        else:
            findings.insert(0, {
                'title': 'Coincidencia general',
                'status': 'err',
                'pct': max(0, 100 - score),
                'msg': f'Alta diferencia ({score}%). Posible error en separación o referencia incorrecta.'
            })

        return jsonify({
            'success': True,
            'score': score,
            'diff_map': pil_to_base64(diff_img),
            'findings': findings,
            'metrics': {
                'avg_diff': score,
                'total_coverage': total_coverage_pct,
                'separation_count': len(png_files)
            }
        })

    except Exception as e:
        import traceback
        print(f"[ERROR /api/analyze_detailed] {traceback.format_exc()}")
        return jsonify({'error': friendly_error_message(e)}), 500

if __name__ == '__main__':
    print("=" * 70)
    print("  HALFTONE RIP PRO - SERIGRAFÍA PROFESIONAL")
    print("  Con Numba JIT + Procesamiento Paralelo")
    print("  Extracción avanzada de Spot Colors (%%PlateColor:, Pantone, etc.)")
    print("  Con lista negra para evitar 'None', 'All' y falsos positivos")
    print("=" * 70)
    print(f"  Ghostscript: {GS_VERSION if GS_AVAILABLE else 'NO INSTALADO'}")
    print(f"  Numba JIT: {'✓ ACTIVADO (10x más rápido)' if NUMBA_AVAILABLE else '✗ No instalado'}")
    print(f"  Workers paralelos: {PERFORMANCE_CONFIG['max_workers']}")
    print(f"  Máximo archivo: 10 GB")
    print("=" * 70)
    print("  http://localhost:5000")
    print("=" * 70)
    app.run(host='0.0.0.0', port=5000, debug=True, threaded=True)