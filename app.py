#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Halftone RIP Pro - SERIGRAFÍA PROFESIONAL ULTRA RÁPIDA
Con Numba JIT + Procesamiento Paralelo + Extracción Avanzada de Spot Colors
Soporte: CMYK + Pantone + Colores directos vía %%PlateColor:
"""

import os
import io
import json
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

# =============================================================================
# BUILD INFO (Protocolo de Diagnóstico - ADR-010, paso 1: identificar la build)
#
# BUILD_INFO.json es una etiqueta manual (se bumpea a mano en cada release).
# knowledge_engine_hash es automático: hashea el contenido real de
# static/js/core/knowledge/ al arrancar. Si alguien olvida bumpear el JSON,
# el hash igual delata si el código que se está sirviendo cambió.
# =============================================================================

def _compute_knowledge_engine_hash():
    knowledge_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'static', 'js', 'core', 'knowledge')
    hasher = hashlib.sha256()
    if os.path.isdir(knowledge_dir):
        for root, _, files in os.walk(knowledge_dir):
            for fname in sorted(files):
                fpath = os.path.join(root, fname)
                try:
                    with open(fpath, 'rb') as f:
                        hasher.update(fpath.encode('utf-8'))
                        hasher.update(f.read())
                except OSError:
                    pass
    else:
        return None
    return hasher.hexdigest()[:12]


def _load_build_info():
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'BUILD_INFO.json')
    try:
        with open(path, 'r', encoding='utf-8') as f:
            info = json.load(f)
    except (OSError, json.JSONDecodeError):
        info = {
            "version": "unknown",
            "build_label": "unknown",
            "generated_at": None,
            "summary": "BUILD_INFO.json no encontrado o inválido"
        }
    info['knowledge_engine_hash'] = _compute_knowledge_engine_hash()
    return info


BUILD_INFO = _load_build_info()
print(f"[BUILD] v{BUILD_INFO.get('version')} \"{BUILD_INFO.get('build_label')}\" "
      f"— knowledge_engine_hash={BUILD_INFO.get('knowledge_engine_hash')}")

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
# DEPURACIÓN: GUARDADO DE IMÁGENES INTERMEDIAS
# =============================================================================

_DEBUG_COUNTER = 0
_DEBUG_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'debug')
os.makedirs(_DEBUG_DIR, exist_ok=True)

def debug_save_image(img, label, step_num):
    """Guarda una imagen en la carpeta debug/ con nombre secuencial."""
    global _DEBUG_COUNTER
    if img is None:
        print(f"[DEBUG] {label} es None, omitiendo guardado")
        return
    try:
        if hasattr(img, 'shape'):
            h, w = img.shape[:2]
            mode = 'array'
        else:
            w, h = img.size
            mode = 'PIL'
        _DEBUG_COUNTER += 1
        filename = f"{_DEBUG_COUNTER:02d}_{label}.png"
        filepath = os.path.join(_DEBUG_DIR, filename)
        if mode == 'array':
            if img.dtype == bool:
                img_to_save = (img * 255).astype(np.uint8)
            else:
                img_to_save = img.astype(np.uint8)
            if img_to_save.ndim == 2:
                pil_img = Image.fromarray(img_to_save, mode='L')
            elif img_to_save.ndim == 3:
                if img_to_save.shape[2] == 1:
                    pil_img = Image.fromarray(img_to_save.squeeze(), mode='L')
                elif img_to_save.shape[2] == 3:
                    pil_img = Image.fromarray(img_to_save, mode='RGB')
                elif img_to_save.shape[2] == 4:
                    pil_img = Image.fromarray(img_to_save, mode='RGBA')
                else:
                    pil_img = Image.fromarray(img_to_save)
            else:
                pil_img = Image.fromarray(img_to_save)
        else:
            pil_img = img.copy()
        pil_img.save(filepath, 'PNG')
        print(f"[DEBUG] PASO {_DEBUG_COUNTER:02d}: {label} -> {filename} (dim: {h}x{w})")
    except Exception as e:
        print(f"[DEBUG] Error guardando {label}: {e}")

# =============================================================================
# FORMATOS SOPORTADOS
# =============================================================================
SUPPORTED_EXTENSIONS = {'ps', 'pdf'}

UNSUPPORTED_FORMAT_REASONS = {
    'eps': 'Los archivos EPS suelen venir como una sola página/un solo color compuesto, no separado por placas.',
    'png': 'Las imágenes PNG/JPG/TIFF no contienen información de separación de tintas.',
    'jpg': 'Las imágenes PNG/JPG/TIFF no contienen información de separación de tintas.',
    'jpeg': 'Las imágenes PNG/JPG/TIFF no contienen información de separación de tintas.',
    'tiff': 'Las imágenes PNG/JPG/TIFF no contienen información de separación de tintas.',
    'tif': 'Las imágenes PNG/JPG/TIFF no contienen información de separación de tintas.',
}


def build_unsupported_format_error(ext):
    ext = (ext or '').lower().lstrip('.')
    base = f"Formato .{ext} no soportado. "
    reason = UNSUPPORTED_FORMAT_REASONS.get(ext)
    if reason:
        base += reason + " "
    base += "Este programa trabaja con archivos .ps o .pdf, preseparados por placa o compuestos en CMYK/PDF-X4."
    return base


def friendly_error_message(exc):
    msg = str(exc)
    if isinstance(exc, MemoryError):
        return ('El archivo es demasiado grande para procesarlo con la memoria disponible.')
    if isinstance(exc, FileNotFoundError):
        return ('No se encontró un archivo necesario para completar la operación.')
    if isinstance(exc, PermissionError):
        return ('El programa no tiene permiso para leer o escribir un archivo.')
    if isinstance(exc, subprocess.TimeoutExpired):
        return ('Ghostscript tardó demasiado en procesar el archivo y se canceló.')
    if isinstance(exc, zipfile.BadZipFile):
        return 'No se pudo crear el archivo ZIP de salida.'
    if isinstance(exc, (OSError, IOError)) and 'No space left' in msg:
        return ('No queda espacio en disco para guardar los resultados.')
    if type(exc).__name__ == 'UnidentifiedImageError':
        return ('No se pudo leer una de las imágenes. Verifica que el archivo no esté dañado.')
    if isinstance(exc, RuntimeError) and msg.startswith('Ghostscript (tiffsep) falló'):
        return ('Ghostscript no pudo procesar el archivo. Verifica que sea un PostScript/PDF válido.')
    if isinstance(exc, RuntimeError) and 'no generó ninguna separación' in msg:
        return msg
    return ('Ocurrió un error inesperado al procesar el archivo. Revisa que el archivo no esté dañado.')

# =============================================================================
# LISTA NEGRA DE PALABRAS PROHIBIDAS
# =============================================================================

COLOR_BLACKLIST = {
    'none', 'all', 'true', 'false', 'null', 'mark', 'count', 'copy',
    'dup', 'exch', 'pop', 'index', 'roll', 'clear', 'cleartomark',
    'def', 'bind', 'readonly', 'put', 'get', 'begin', 'end',
    'save', 'restore', 'gsave', 'grestore', 'showpage', 'erasepage',
    'newpath', 'moveto', 'lineto', 'curveto', 'arc', 'closepath',
    'stroke', 'fill', 'eofill', 'clip', 'rectclip',
    'setcolorspace', 'setcolor', 'setgray', 'setrgbcolor', 'setcmykcolor',
    'image', 'colorimage', 'translate', 'scale', 'rotate',
    'red', 'green', 'blue', 'cyan', 'magenta', 'yellow', 'black',
    'white', 'gray', 'grey', 'orange', 'purple', 'pink', 'brown',
    'process', 'spot', 'color', 'colour', 'ink', 'tinta', 'custom',
    'separation', 'colorspace', 'devicecmyk', 'devicergb', 'devicegray',
    'pattern', 'indexed', 'default', 'standard', 'normal', 'regular',
    'generic', 'solid', 'mixed', 'blend', 'overlay', 'multiply', 'screen',
    'array', 'dict', 'string', 'name', 'real', 'integer', 'boolean',
    'file', 'operator', 'fonttype', 'encoding', 'painttype', 'fontname',
    'page', 'pages', 'media', 'mediabox', 'cropbox', 'bleedbox', 'trimbox',
    'boundingbox', 'documentprocesscolors', 'documentcustomcolors',
    'pagecustomcolors', 'pageprocesscolors', 'hiResBoundingBox',
}

VALID_SHORT_COLORS = {
    'gold', 'pink', 'teal', 'navy', 'lime', 'plum', 'ruby', 'jade',
    'ivory', 'beige', 'coral', 'khaki', 'olive', 'wheat', 'snow',
    'mint', 'rose', 'sand', 'coal', 'lead', 'zinc', 'tin', 'pewter',
    'rust', 'copper', 'bronze', 'silver'
}


def is_blacklisted(name):
    if not name:
        return True
    n = name.lower().strip().strip('_').strip('()')
    if n in COLOR_BLACKLIST:
        return True
    if len(n) <= 2 and n.isalpha() and n not in VALID_SHORT_COLORS:
        return True
    if n.replace(' ', '').replace('-', '').replace('.', '').isdigit():
        return True
    if any(op in n for op in ['%', '+', '-', '*', '/', '=', '<', '>', '&', '|', '!']):
        return True
    return False


def clean_color_name(name):
    if not name:
        return ""
    name = re.sub(r'[^\w\s\-\.]', '', name)
    name = re.sub(r'\s+', ' ', name).strip()
    return name


def looks_like_color_name(name):
    if not name or len(name) < 2 or len(name) > 50:
        return False
    if is_blacklisted(name):
        return False
    nl = name.lower()
    for sys in ['pantone', 'hks', 'toyo', 'dic', 'anpa', 'ral', 'ncs',
                'focoltone', 'trumatch', 'munsell']:
        if sys in nl:
            return True
    if re.match(r'^[A-Za-z]{2,6}\s*\d{2,5}', name):
        return True
    if re.match(r'^\d{3}\s*[cC]\b', name):
        return True
    if re.search(r'\d{3}\s*[cC]', nl):
        return True
    if re.search(r'\d+\s+[CMUP]\b', name):
        return True
    if re.search(r'\d+\s+(CP|UP|CVC|CVU|EC|HC|PC|TC|TP|XGC|N)\b', name):
        return True
    color_words = ['azul', 'rojo', 'verde', 'amarillo', 'negro', 'blanco', 
                   'gris', 'naranja', 'morado', 'rosa', 'celeste', 'violeta', 
                   'marrón', 'turquesa', 'beige', 'ocre', 'dorado', 'plateado',
                   'bronce', 'cobre']
    if nl in color_words:
        return True
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
                if shape_type == 0:
                    S = np.cos(np.pi * u_norm) + np.cos(np.pi * v_norm)
                    threshold = (S + 2.0) / 4.0
                elif shape_type == 1:
                    S = 3.0 * np.cos(np.pi * u_norm) + np.cos(np.pi * v_norm)
                    threshold = (S + 4.0) / 8.0
                elif shape_type == 2:
                    S = np.cos(np.pi * v_norm)
                    threshold = (S + 1.0) / 2.0
                elif shape_type == 3:
                    S = np.cos(np.pi * u_norm) * np.cos(np.pi * v_norm)
                    threshold = (S + 1.0) / 2.0
                else:
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


def assert_analysis_dimensions(label, image_or_array, expected_size):
    if hasattr(image_or_array, 'size') and not isinstance(image_or_array, np.ndarray):
        current_size = image_or_array.size
    else:
        arr = np.asarray(image_or_array)
        if arr.ndim < 2:
            raise ValueError(f"{label} no es una imagen/array 2D válido para análisis")
        current_size = (arr.shape[1], arr.shape[0])
    if tuple(current_size) != tuple(expected_size):
        raise ValueError(f"{label} cambió de tamaño: esperado {expected_size}, actual {current_size}")


def image_copy_for_analysis(image, mode=None):
    copied = image.copy()
    return copied.convert(mode) if mode else copied


def resize_copy_for_analysis(image, size, resample=Image.LANCZOS):
    original_size = image.size
    resized = image.copy().resize(size, resample)
    assert_analysis_dimensions('Imagen fuente de análisis', image, original_size)
    assert_analysis_dimensions('Imagen redimensionada para análisis', resized, size)
    return resized


def smart_thumbnail(img, target_max=800):
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
    if not s:
        return ""
    def replace_octal(match):
        return chr(int(match.group(1), 8))
    try:
        decoded = re.sub(r'\\([0-7]{3})', replace_octal, s)
        raw_bytes = decoded.encode('latin-1', errors='replace')
        for encoding in ['utf-8', 'cp1251', 'cp1252', 'latin-1']:
            try:
                return raw_bytes.decode(encoding)
            except UnicodeDecodeError:
                continue
        return decoded
    except Exception:
        return s


def parse_ps_file(filepath):
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
        print(f"[PARSE PS] Error: {e}")
    result = {'page_count': page_count if page_count > 0 else 4, 'page_colors': page_colors}
    if cache_key:
        _ps_metadata_cache[cache_key] = result
    return result


def escape_ps_string(s):
    s = s.replace('\\', '\\\\')
    s = s.replace('(', '\\(')
    s = s.replace(')', '\\)')
    return s


def count_pdf_pages_via_gs(filepath, timeout=15):
    if not GS_AVAILABLE:
        return None
    try:
        escaped_path = escape_ps_string(filepath)
        cmd = [GS_CMD, '-q', '-dNODISPLAY', '-dBATCH', '-dNOSAFER', '-c',
               f'({escaped_path}) (r) file runpdfbegin pdfpagecount =']
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        output = result.stdout.strip().splitlines()
        return int(output[-1]) if output else None
    except:
        return None


def get_real_page_count(filepath):
    ext = os.path.splitext(filepath)[1].lower()
    if ext == '.pdf':
        count = count_pdf_pages_via_gs(filepath)
        if count and count > 0:
            print(f"[PAGES] PDF: {count} páginas")
            return count
        print("[PAGES] No se pudo contar PDF, usando 4")
        return 4
    try:
        metadata = parse_ps_file(filepath)
        print(f"[PAGES] PS: {metadata['page_count']} páginas")
        return metadata['page_count']
    except Exception as e:
        print(f"[PAGES] Error: {e}")
        return 4


def extract_pantone_names(filepath, page_count):
    metadata = parse_ps_file(filepath)
    detected_colors = metadata['page_colors']
    color_names = {}
    for page_num in range(1, page_count + 1):
        if page_num in detected_colors and detected_colors[page_num]:
            color_names[page_num] = detected_colors[page_num]
    missing_pages = [p for p in range(1, page_count + 1) if p not in color_names]
    if missing_pages:
        print(f"[NAMES] Faltan nombres para {missing_pages}")
        try:
            with open(filepath, 'r', encoding='latin-1', errors='ignore') as f:
                content = f.read(2000000)
        except:
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
        fallback_cmyk = {1: "Cyan", 2: "Magenta", 3: "Yellow", 4: "Black"}
        spot_idx = 0
        for page_num in missing_pages:
            if page_num <= 4 and page_count >= 4:
                color_names[page_num] = fallback_cmyk[page_num]
            elif spot_idx < len(unique_names):
                color_names[page_num] = unique_names[spot_idx]
                spot_idx += 1
            else:
                if page_num <= 4:
                    color_names[page_num] = fallback_cmyk[page_num]
                else:
                    color_names[page_num] = f"Spot_{page_num}"
    print(f"[NAMES] Final: {color_names}")
    return color_names

# =============================================================================
# RENDERIZADO RÁPIDO DE PÁGINA
# =============================================================================

def render_page_fast(filepath, output_dir, dpi, page_num):
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
        subprocess.run(cmd, capture_output=True, text=True, timeout=PERFORMANCE_CONFIG['timeout_per_page'])
        if os.path.exists(output_path) and os.path.getsize(output_path) > 5000:
            img = Image.open(output_path)
            if img.mode == 'RGB':
                img = img.convert('L')
            return img
        else:
            print(f"[PAGE {page_num}] No generado")
    except Exception as e:
        print(f"[PAGE {page_num}] Error: {e}")
    return None

# =============================================================================
# PROCESAMIENTO PARALELO DE PÁGINAS
# =============================================================================

def process_with_tiffsep(filepath, output_dir, dpi=600, timeout=300):
    if not GS_AVAILABLE:
        raise RuntimeError("Ghostscript no está disponible")
    os.makedirs(output_dir, exist_ok=True)
    output_pattern = os.path.join(output_dir, 'sep_%d.tif')
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
    print(f"[TIFFSEP] Ejecutando Ghostscript...")
    start_time = time.time()
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    if result.returncode != 0:
        raise RuntimeError(f"Ghostscript (tiffsep) falló: {result.stderr[-2000:]}")
    pattern = re.compile(r'^sep_(\d+)\((.+)\)\.tif$', re.IGNORECASE)
    found = []
    for fname in os.listdir(output_dir):
        m = pattern.match(fname)
        if m:
            page_num = int(m.group(1))
            color_name = clean_color_name(m.group(2))
            found.append((page_num, color_name, os.path.join(output_dir, fname)))
    if not found:
        raise RuntimeError("Ghostscript no generó ninguna separación.")
    plate_comment_names = {}
    ext = os.path.splitext(filepath)[1].lower()
    if ext in ('.ps', '.eps'):
        try:
            ps_metadata = parse_ps_file(filepath)
            plate_comment_names = ps_metadata.get('page_colors', {})
        except:
            pass
    pages_channel_count = {}
    for page_num, color_name, tif_path in found:
        probe_img = Image.open(tif_path)
        if probe_img.mode != 'L':
            probe_img = probe_img.convert('L')
        if np.array(probe_img).min() < 255:
            pages_channel_count[page_num] = pages_channel_count.get(page_num, 0) + 1
    cmyk_order = {'cyan': 0, 'magenta': 1, 'yellow': 2, 'black': 3}
    found.sort(key=lambda x: (x[0], cmyk_order.get(x[1].lower(), 99), x[1]))
    plates = OrderedDict()
    warnings = []
    cmyk_names_seen = {}
    for page_num, color_name, tif_path in found:
        img = Image.open(tif_path)
        if img.mode != 'L':
            img = img.convert('L')
        arr = np.array(img)
        if arr.min() >= 255:
            continue
        real_name = plate_comment_names.get(page_num)
        used_generic_name = True
        if real_name and pages_channel_count.get(page_num) == 1:
            print(f"[TIFFSEP] Página {page_num}: '{color_name}' -> '{real_name}'")
            color_name = real_name
            used_generic_name = False
        if used_generic_name:
            cmyk_names_seen.setdefault(color_name, []).append(page_num)
        final_name = color_name if color_name else f"Color_pagina_{page_num}"
        if final_name in plates:
            final_name = f"{final_name}_p{page_num}"
        ink_fraction = float(np.mean((255 - arr) / 255.0))
        if ink_fraction > 0.95:
            warnings.append(f"'{final_name}' tiene {ink_fraction*100:.0f}% de cobertura.")
        plates[final_name] = img
        print(f"[TIFFSEP] Placa: '{final_name}' (página {page_num})")
    for generic_name, pages in cmyk_names_seen.items():
        if len(pages) > 1:
            warnings.append(f"{len(pages)} placas se detectaron como '{generic_name}'")
    elapsed = time.time() - start_time
    print(f"[TIFFSEP] Completado en {elapsed:.1f}s ({len(plates)} placas)")
    return plates, warnings


def process_postscript_parallel(filepath, output_dir, dpi=600):
    start_time = time.time()
    page_count = get_real_page_count(filepath)
    print(f"[PARALELO] {page_count} páginas")
    color_names = extract_pantone_names(filepath, page_count)
    plates = []
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
                print(f"[PARALELO] Página {page_num} ✓")
            else:
                print(f"[PARALELO] Página {page_num} ✗")
    plates.sort(key=lambda x: x[0])
    elapsed = time.time() - start_time
    print(f"[PARALELO] Completado en {elapsed:.1f}s")
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

def image_to_grayscale_array(image):
    return np.array(image.convert('L'), dtype=np.uint8)


def remove_reference_background(img, tolerance=32, border_margin=3):
    """
    Muchas fotos/mockups de referencia traen un fondo de color sólido (estudio,
    plantilla, etc.) que NO se imprime. Si no se quita antes de comparar, el
    algoritmo lo cuenta como "tinta" (cualquier píxel no-blanco = diseño), y
    aparece como un enorme bloque de "información faltante" al comparar contra
    la separación (que sí tiene fondo blanco/transparente real).

    Estrategia: se muestrea el color del borde de la imagen y se hace un
    flood-fill (relleno por conectividad, partiendo de los 4 bordes) de los
    píxeles de color parecido. Solo se pinta de blanco lo que está CONECTADO
    al borde, así que si el diseño tiene internamente un color parecido al
    fondo (ej. una prenda roja sobre fondo rojo) no se toca, porque no está
    conectado al borde de la imagen.
    """
    rgb = np.array(img.convert('RGB')).astype(np.int16)
    h, w = rgb.shape[:2]
    if h < border_margin * 2 + 1 or w < border_margin * 2 + 1:
        return img.convert('RGB')

    border_pixels = np.concatenate([
        rgb[0:border_margin, :, :].reshape(-1, 3),
        rgb[-border_margin:, :, :].reshape(-1, 3),
        rgb[:, 0:border_margin, :].reshape(-1, 3),
        rgb[:, -border_margin:, :].reshape(-1, 3),
    ])
    bg_color = np.median(border_pixels, axis=0)
    dist = np.sqrt(((rgb - bg_color) ** 2).sum(axis=2))
    close_mask = dist < tolerance

    try:
        from scipy import ndimage
        labeled, count = ndimage.label(close_mask)
        if count == 0:
            return img.convert('RGB')
        border_labels = set(np.unique(labeled[0, :]).tolist())
        border_labels |= set(np.unique(labeled[-1, :]).tolist())
        border_labels |= set(np.unique(labeled[:, 0]).tolist())
        border_labels |= set(np.unique(labeled[:, -1]).tolist())
        border_labels.discard(0)
        bg_mask = np.isin(labeled, list(border_labels))
    except ImportError:
        bg_mask = close_mask

    out = np.array(img.convert('RGB'))
    out[bg_mask] = (255, 255, 255)
    return Image.fromarray(out, mode='RGB')


def ink_mask_from_image(image, threshold=245):
    gray = image_to_grayscale_array(image)
    return gray < int(np.clip(threshold, 1, 255))


def clean_binary_mask(mask, min_component_area=8, morph_radius=1):
    mask = np.asarray(mask, dtype=bool)
    try:
        from scipy import ndimage
        if morph_radius > 0:
            structure = np.ones((morph_radius * 2 + 1, morph_radius * 2 + 1), dtype=bool)
            mask = ndimage.binary_opening(mask, structure=structure)
            mask = ndimage.binary_closing(mask, structure=structure)
        labeled, count = ndimage.label(mask)
        if count == 0:
            return mask.astype(bool)
        sizes = np.bincount(labeled.ravel())
        keep = sizes >= max(1, int(min_component_area))
        keep[0] = False
        return keep[labeled].astype(bool)
    except ImportError:
        return mask.astype(bool)


def dilate_mask(mask, px):
    """Expande una máscara binaria N píxeles en todas direcciones."""
    mask = np.asarray(mask, dtype=bool)
    if px <= 0:
        return mask
    try:
        from scipy import ndimage
        structure = np.ones((3, 3), dtype=bool)
        return ndimage.binary_dilation(mask, structure=structure, iterations=int(px))
    except ImportError:
        return mask


def _uniform_filter_numpy_fallback(arr, size):
    """
    Box blur (filtro de promedio) usando solo NumPy, vía imagen integral
    (suma acumulada), para cuando scipy no está instalado. Más lento que
    scipy.ndimage.uniform_filter pero da el mismo resultado.
    """
    size = max(1, int(size))
    h, w = arr.shape
    pad = size // 2
    padded = np.pad(arr, ((pad, size - 1 - pad), (pad, size - 1 - pad)), mode='edge')
    integral = np.cumsum(np.cumsum(padded, axis=0), axis=1)
    integral = np.pad(integral, ((1, 0), (1, 0)), mode='constant')
    y0, x0 = np.mgrid[0:h, 0:w]
    y1 = y0 + size
    x1 = x0 + size
    total = (integral[y1, x1] - integral[y0, x1] - integral[y1, x0] + integral[y0, x0])
    return (total / (size * size)).astype(np.float32)


def compute_density_metrics(ref_gray_arr, sep_mask, kernel_px=9, density_tolerance=0.25):
    """
    Para arte con trama de medios tonos (halftone): un punto de trama nunca
    va a caer exactamente en el mismo píxel que una referencia de tono
    continuo, aunque la reproducción sea perfecta -- el punto representa una
    ZONA de tono, no un píxel exacto. Comparar píxel a píxel ahí genera ruido
    masivo (cada hueco entre puntos se marca como "faltante").

    En vez de eso, se promedia la cobertura de tinta en una ventana del
    tamaño aproximado de la celda de trama (kernel_px) y se compara contra
    el TONO CONTINUO real de la referencia (no contra una máscara ya
    binarizada/umbralizada -- eso descartaría la gradación tonal, que es
    justo la información que se necesita para juzgar si la densidad de
    puntos es correcta).

    ref_gray_arr: array de escala de grises (0-255) de la referencia, SIN
    binarizar.
    sep_mask: máscara binaria de tinta de la separación (los puntos de trama
    sí son genuinamente blanco/negro a nivel de impresión).
    """
    ref_gray_arr = np.asarray(ref_gray_arr, dtype=np.float32)
    target_density = np.clip(1.0 - (ref_gray_arr / 255.0), 0.0, 1.0)
    sep_float = sep_mask.astype(np.float32)
    try:
        from scipy import ndimage
        ref_density = ndimage.uniform_filter(target_density, size=kernel_px)
        sep_density = ndimage.uniform_filter(sep_float, size=kernel_px)
    except ImportError:
        ref_density = _uniform_filter_numpy_fallback(target_density, kernel_px)
        sep_density = _uniform_filter_numpy_fallback(sep_float, kernel_px)

    diff = ref_density - sep_density
    missing_mask = diff > density_tolerance
    extra_mask = diff < -density_tolerance

    ref_area = ref_density > 0.05
    sep_area = sep_density > 0.05
    ref_pixels = int(ref_area.sum())
    sep_pixels = int(sep_area.sum())
    union_area = int((ref_area | sep_area).sum())
    fn = int(missing_mask.sum())
    fp = int(extra_mask.sum())
    tp = max(0, union_area - fn - fp)

    iou = tp / union_area if union_area else 1.0
    recall = (ref_pixels - fn) / ref_pixels if ref_pixels else 1.0
    precision = (sep_pixels - fp) / sep_pixels if sep_pixels else (1.0 if ref_pixels == 0 else 0.0)
    dice = (2 * tp) / (2 * tp + fp + fn) if (2 * tp + fp + fn) else 1.0
    missing_pct = (fn / ref_pixels * 100.0) if ref_pixels else 0.0
    extra_pct = (fp / ref_pixels * 100.0) if ref_pixels else (100.0 if sep_pixels else 0.0)

    metrics = {
        'true_positive_pixels': tp,
        'false_positive_pixels': fp,
        'false_negative_pixels': fn,
        'reference_pixels': ref_pixels,
        'separation_pixels': sep_pixels,
        'iou': round(iou, 4),
        'dice': round(dice, 4),
        'precision': round(precision, 4),
        'recall': round(recall, 4),
        'missing_pct': round(missing_pct, 2),
        'extra_pct': round(extra_pct, 2),
        'mode': 'density',
        'kernel_px': kernel_px,
        'density_tolerance': density_tolerance,
    }
    return missing_mask, extra_mask, metrics


def compute_binary_metrics(ref_mask, sep_mask, edge_tolerance_px=0):
    """
    edge_tolerance_px: si es > 0, un píxel de referencia solo cuenta como
    "faltante" si NO hay ningún píxel de separación dentro de esa distancia
    (y viceversa para "adicional"). Esto evita que un simple halo de borde
    de unos pocos píxeles (por antialiasing de la foto de referencia o un
    desalineamiento sub-píxel) se cuente como pérdida real de información,
    sin dejar de detectar objetos/fragmentos genuinamente faltantes o
    agregados (esos siguen siendo más anchos que la tolerancia).
    """
    ref_mask = np.asarray(ref_mask, dtype=bool)
    sep_mask = np.asarray(sep_mask, dtype=bool)
    if ref_mask.shape != sep_mask.shape:
        raise ValueError("Las máscaras deben tener el mismo tamaño")

    ref_pixels = int(ref_mask.sum())
    sep_pixels = int(sep_mask.sum())

    if edge_tolerance_px and edge_tolerance_px > 0:
        sep_dilated = dilate_mask(sep_mask, edge_tolerance_px)
        ref_dilated = dilate_mask(ref_mask, edge_tolerance_px)
        fn_mask = ref_mask & ~sep_dilated
        fp_mask = sep_mask & ~ref_dilated
        fn = int(fn_mask.sum())
        fp = int(fp_mask.sum())
        union_count = int((ref_mask | sep_mask).sum())
        tp = max(0, union_count - fn - fp)
    else:
        tp_mask = ref_mask & sep_mask
        fp_mask = ~ref_mask & sep_mask
        fn_mask = ref_mask & ~sep_mask
        tp = int(tp_mask.sum())
        fp = int(fp_mask.sum())
        fn = int(fn_mask.sum())

    union = tp + fp + fn
    iou = tp / union if union else 1.0
    dice = (2 * tp) / (2 * tp + fp + fn) if (2 * tp + fp + fn) else 1.0
    precision = (sep_pixels - fp) / sep_pixels if sep_pixels else (1.0 if ref_pixels == 0 else 0.0)
    recall = (ref_pixels - fn) / ref_pixels if ref_pixels else 1.0
    missing_pct = (fn / ref_pixels * 100.0) if ref_pixels else 0.0
    extra_pct = (fp / ref_pixels * 100.0) if ref_pixels else (100.0 if sep_pixels else 0.0)
    return {
        'true_positive_pixels': tp,
        'false_positive_pixels': fp,
        'false_negative_pixels': fn,
        'reference_pixels': ref_pixels,
        'separation_pixels': sep_pixels,
        'iou': round(iou, 4),
        'dice': round(dice, 4),
        'precision': round(precision, 4),
        'recall': round(recall, 4),
        'missing_pct': round(missing_pct, 2),
        'extra_pct': round(extra_pct, 2),
    }


def connected_components_summary(mask, min_area=8):
    mask = np.asarray(mask, dtype=bool)
    try:
        from scipy import ndimage
        labeled, count = ndimage.label(mask)
        components = []
        for label in range(1, count + 1):
            ys, xs = np.where(labeled == label)
            area = int(len(xs))
            if area < min_area:
                continue
            components.append({
                'label': label,
                'area': area,
                'bbox': (int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1),
                'centroid': (float(xs.mean()), float(ys.mean())),
            })
        return components
    except ImportError:
        ys, xs = np.where(mask)
        if len(xs) < min_area:
            return []
        return [{'label': 1, 'area': int(len(xs)), 'bbox': (int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1), 'centroid': (float(xs.mean()), float(ys.mean()))}]


def describe_region(bbox, image_size):
    x0, y0, x1, y1 = bbox
    w, h = image_size
    cx = (x0 + x1) / 2.0
    cy = (y0 + y1) / 2.0
    horizontal = 'izquierda' if cx < w / 3 else 'derecha' if cx > 2 * w / 3 else 'centro'
    vertical = 'superior' if cy < h / 3 else 'inferior' if cy > 2 * h / 3 else 'central'
    if horizontal == 'centro' and vertical == 'central':
        return 'zona central'
    if horizontal == 'centro':
        return f'zona {vertical}'
    if vertical == 'central':
        return f'lateral {horizontal}'
    return f'esquina {vertical} {horizontal}'


def build_structural_diff_map(ref_mask, sep_mask, missing_mask=None, extra_mask=None):
    ref_mask = np.asarray(ref_mask, dtype=bool)
    sep_mask = np.asarray(sep_mask, dtype=bool)
    missing = np.asarray(missing_mask, dtype=bool) if missing_mask is not None else (ref_mask & ~sep_mask)
    extra = np.asarray(extra_mask, dtype=bool) if extra_mask is not None else (~ref_mask & sep_mask)
    h, w = ref_mask.shape
    diff_map = np.zeros((h, w, 4), dtype=np.uint8)
    match = ~missing & ~extra & (ref_mask | sep_mask)
    diff_map[match] = [0, 200, 100, 120]
    diff_map[missing] = [220, 60, 60, 220]
    diff_map[extra] = [40, 120, 255, 220]
    return Image.fromarray(diff_map, mode='RGBA')


def _components_from_error_mask(error_mask, min_area):
    return connected_components_summary(error_mask, min_area=min_area)


def build_structural_findings(metrics, missing_components, extra_components, image_size):
    findings = []
    score = structural_score_from_metrics(metrics, len(missing_components), len(extra_components))
    if score <= 5:
        findings.append({'title': 'Geometría del diseño', 'status': 'ok', 'pct': 100, 'msg': 'No se detectaron pérdidas de información.'})
    elif score <= 20:
        findings.append({'title': 'Geometría del diseño', 'status': 'warn', 'pct': max(0, 100 - score), 'msg': f'La geometría es similar, pero hay {metrics["missing_pct"]:.2f}% faltante y {metrics["extra_pct"]:.2f}% adicional.'})
    else:
        findings.append({'title': 'Geometría del diseño', 'status': 'err', 'pct': max(0, 100 - score), 'msg': f'Se detectaron cambios estructurales importantes: IoU {metrics["iou"]:.3f}, recall {metrics["recall"]:.3f}.'})
    for comp in sorted(missing_components, key=lambda c: c['area'], reverse=True)[:3]:
        findings.append({'title': 'Información faltante', 'status': 'err', 'pct': min(100, max(5, int(comp['area'] ** 0.5))), 'msg': f'Se perdió un objeto o fragmento de {comp["area"]} px en la {describe_region(comp["bbox"], image_size)}.'})
    for comp in sorted(extra_components, key=lambda c: c['area'], reverse=True)[:3]:
        findings.append({'title': 'Información adicional', 'status': 'warn', 'pct': min(100, max(5, int(comp['area'] ** 0.5))), 'msg': f'Se detectó un objeto adicional de {comp["area"]} px en la {describe_region(comp["bbox"], image_size)}.'})
    if not missing_components and not extra_components and score > 5:
        findings.append({'title': 'Contorno modificado', 'status': 'warn', 'pct': max(0, 100 - score), 'msg': 'Las diferencias se concentran en bordes o detalles finos.'})
    return findings


def structural_score_from_metrics(metrics, missing_component_count=0, extra_component_count=0):
    missing_penalty = min(70.0, metrics['missing_pct'] * 2.8)
    extra_penalty = min(25.0, metrics['extra_pct'] * 1.2)
    iou_penalty = max(0.0, (1.0 - metrics['iou']) * 60.0)
    component_penalty = min(15.0, missing_component_count * 4.0 + extra_component_count * 2.0)
    return int(np.clip(missing_penalty + extra_penalty + iou_penalty + component_penalty, 0, 100))


# =============================================================================
# BLOB DETECTION MEJORADA PARA AUTO-ALINEACIÓN
# =============================================================================

def _largest_ink_blob_bbox(mask, filter_elongated=True, min_density=0.3, min_area=200):
    """
    Encuentra el BLOB de tinta más relevante, filtrando guías/barras.
    
    Args:
        mask: array booleano 2D (True = tinta)
        filter_elongated: si True, filtra BLOBs muy alargados (probables guías)
        min_density: densidad mínima de tinta en el BLOB (0-1)
        min_area: área mínima en píxeles para considerar un BLOB
    """
    if not mask.any():
        return None
    
    try:
        from scipy import ndimage
        labeled, n = ndimage.label(mask)
        if n == 0:
            return None
        
        props = []
        for label in range(1, n + 1):
            ys, xs = np.where(labeled == label)
            if len(xs) < min_area:
                continue
            
            x0, y0 = xs.min(), ys.min()
            x1, y1 = xs.max(), ys.max()
            w = x1 - x0 + 1
            h = y1 - y0 + 1
            area = len(xs)
            density = area / (w * h) if (w * h) > 0 else 0
            aspect_ratio = max(w, h) / min(w, h) if min(w, h) > 0 else 999
            
            props.append({
                'label': label,
                'area': area,
                'bbox': (int(x0), int(y0), int(x1), int(y1)),
                'width': int(w),
                'height': int(h),
                'density': float(density),
                'aspect_ratio': float(aspect_ratio),
                'centroid': (float(xs.mean()), float(ys.mean()))
            })
        
        if not props:
            return None
        
        if filter_elongated:
            filtered_props = [p for p in props if p['aspect_ratio'] < 4.0]
            if filtered_props:
                props = filtered_props
        
        filtered_props = [p for p in props if p['density'] > min_density]
        if filtered_props:
            props = filtered_props
        
        if not props:
            props = sorted(props, key=lambda p: p['area'], reverse=True)
            if not props:
                return None
            best = props[0]
        else:
            best = max(props, key=lambda p: p['area'])
        
        print(f"[BLOB] Seleccionado: area={best['area']}, bbox={best['bbox']}, "
              f"aspect={best['aspect_ratio']:.2f}, density={best['density']:.2f}")
        
        return best['bbox']
        
    except ImportError:
        ys, xs = np.where(mask)
        if len(xs) == 0:
            return None
        return int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1


def _find_art_blob(mask_sep, min_area=1000):
    """
    Encuentra el BLOB de arte real dentro del área de impresión.
    Ignora el área de impresión completa y busca BLOBs interiores.
    
    Args:
        mask_sep: array booleano 2D de la separación (True = tinta)
        min_area: área mínima para considerar un BLOB
    
    Returns:
        tuple (x0, y0, x1, y1) del BLOB de arte, o None si no se encuentra
    """
    if not mask_sep.any():
        return None
    
    try:
        from scipy import ndimage
        labeled, n = ndimage.label(mask_sep)
        if n == 0:
            return None
        
        # Encontrar todos los BLOBs
        props = []
        for label in range(1, n + 1):
            ys, xs = np.where(labeled == label)
            if len(xs) < min_area:
                continue
            
            x0, y0 = xs.min(), ys.min()
            x1, y1 = xs.max(), ys.max()
            w = x1 - x0 + 1
            h = y1 - y0 + 1
            area = len(xs)
            density = area / (w * h) if (w * h) > 0 else 0
            aspect_ratio = max(w, h) / min(w, h) if min(w, h) > 0 else 999
            
            props.append({
                'label': label,
                'area': area,
                'bbox': (int(x0), int(y0), int(x1), int(y1)),
                'width': int(w),
                'height': int(h),
                'density': float(density),
                'aspect_ratio': float(aspect_ratio),
                'centroid': (float(xs.mean()), float(ys.mean()))
            })
        
        if not props:
            return None
        
        # Ordenar por área (el más grande primero)
        props.sort(key=lambda p: p['area'], reverse=True)
        outer = props[0]  # El área de impresión completa
        
        # Si solo hay un BLOB, es el área de impresión (no hay arte interior)
        if len(props) == 1:
            print(f"[ART BLOB] Solo hay un BLOB (área de impresión), usando ese")
            return outer['bbox']
        
        # Buscar BLOBs dentro del área de impresión (que no sean el exterior)
        inner_blobs = []
        ox0, oy0, ox1, oy1 = outer['bbox']
        outer_w = ox1 - ox0
        outer_h = oy1 - oy0
        
        for p in props[1:]:  # Saltar el primero (el exterior)
            ix0, iy0, ix1, iy1 = p['bbox']
            
            # El BLOB interior debe estar dentro del exterior con un margen
            # El margen es del 5% del tamaño del exterior
            margin_x = outer_w * 0.05
            margin_y = outer_h * 0.05
            
            if (ix0 > ox0 + margin_x and ix1 < ox1 - margin_x and
                iy0 > oy0 + margin_y and iy1 < oy1 - margin_y):
                inner_blobs.append(p)
        
        if inner_blobs:
            # Seleccionar el BLOB interior con mayor densidad (el arte)
            # También considerar el área: el arte suele ser grande y denso
            best = max(inner_blobs, key=lambda p: p['density'] * p['area'] / 1000)
            print(f"[ART BLOB] Encontrado arte interior: area={best['area']}, "
                  f"bbox={best['bbox']}, density={best['density']:.2f}, "
                  f"aspect={best['aspect_ratio']:.2f}")
            return best['bbox']
        
        # No se encontró arte interior, usar el BLOB más grande (área de impresión)
        print(f"[ART BLOB] No se encontró arte interior, usando área de impresión")
        return outer['bbox']
        
    except ImportError:
        # Fallback: usar el BLOB más grande
        ys, xs = np.where(mask_sep)
        if len(xs) == 0:
            return None
        return int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1


def compare_structural_images(ref_img, sep_img, threshold=245, min_component_area=8, morph_radius=1,
                               edge_tolerance_px=3, density_mode=False, density_kernel_px=9, density_tolerance=0.25):
    debug_save_image(ref_img, "loaded_ref", 0)
    debug_save_image(sep_img, "loaded_sep", 0)
    
    ref_original_size = ref_img.size
    sep_original_size = sep_img.size
    if ref_original_size != sep_original_size:
        raise ValueError(f"Tamaños diferentes: referencia {ref_img.size}, separación {sep_img.size}")
    
    ref_analysis_img = image_copy_for_analysis(ref_img)
    sep_analysis_img = image_copy_for_analysis(sep_img)
    assert_analysis_dimensions('Referencia', ref_analysis_img, ref_original_size)
    assert_analysis_dimensions('Separación', sep_analysis_img, sep_original_size)
    
    ref_mask = clean_binary_mask(
        ink_mask_from_image(ref_analysis_img, threshold=threshold),
        min_component_area=min_component_area,
        morph_radius=morph_radius
    )
    sep_mask = clean_binary_mask(
        ink_mask_from_image(sep_analysis_img, threshold=threshold),
        min_component_area=min_component_area,
        morph_radius=morph_radius
    )
    assert_analysis_dimensions('Máscara referencia', ref_mask, ref_original_size)
    assert_analysis_dimensions('Máscara separación', sep_mask, sep_original_size)
    
    debug_save_image(ref_mask, "binarized_ref", 0)
    debug_save_image(sep_mask, "binarized_sep", 0)
    
    if density_mode:
        ref_gray_arr = np.array(ref_analysis_img.convert('L'), dtype=np.float32)
        missing_mask, extra_mask, metrics = compute_density_metrics(
            ref_gray_arr, sep_mask, kernel_px=density_kernel_px, density_tolerance=density_tolerance
        )
    else:
        metrics = compute_binary_metrics(ref_mask, sep_mask, edge_tolerance_px=edge_tolerance_px)

        # Mismo criterio de tolerancia que compute_binary_metrics: un píxel solo
        # cuenta como faltante/adicional si está más lejos que edge_tolerance_px
        # del otro diseño. Así un halo de borde fino (desalineamiento sub-píxel,
        # antialiasing de la foto de referencia) no se reporta como pérdida real,
        # pero un objeto/fragmento genuinamente ausente sigue detectándose.
        if edge_tolerance_px and edge_tolerance_px > 0:
            sep_dilated = dilate_mask(sep_mask, edge_tolerance_px)
            ref_dilated = dilate_mask(ref_mask, edge_tolerance_px)
            missing_mask = ref_mask & ~sep_dilated
            extra_mask = sep_mask & ~ref_dilated
        else:
            missing_mask = ref_mask & ~sep_mask
            extra_mask = ~ref_mask & sep_mask

    missing_components = _components_from_error_mask(missing_mask, min_component_area)
    extra_components = _components_from_error_mask(extra_mask, min_component_area)
    
    metrics.update({
        'lost_components': len(missing_components),
        'new_components': len(extra_components),
        'component_count_ref': len(connected_components_summary(ref_mask, min_component_area)),
        'component_count_sep': len(connected_components_summary(sep_mask, min_component_area)),
        'edge_tolerance_px': edge_tolerance_px,
    })
    
    score = structural_score_from_metrics(metrics, len(missing_components), len(extra_components))
    findings = build_structural_findings(metrics, missing_components, extra_components, ref_original_size)
    diff_map = build_structural_diff_map(ref_mask, sep_mask, missing_mask=missing_mask, extra_mask=extra_mask)
    assert_analysis_dimensions('Mapa de diferencias', diff_map, ref_original_size)
    
    debug_save_image(diff_map, "diff_map", 0)
    
    assert_analysis_dimensions('Referencia original', ref_img, ref_original_size)
    assert_analysis_dimensions('Separación original', sep_img, sep_original_size)
    
    return {
        'score': score,
        'diff_map': diff_map,
        'findings': findings,
        'metrics': metrics,
        'masks': {
            'reference': ref_mask,
            'separation': sep_mask,
            'missing': missing_mask,
            'extra': extra_mask,
        }
    }


def analyze_differences(sep_img, ref_img):
    sep_img = image_copy_for_analysis(sep_img)
    ref_img = image_copy_for_analysis(ref_img)
    if sep_img.size != ref_img.size:
        w, h = min(sep_img.size[0], ref_img.size[0]), min(sep_img.size[1], ref_img.size[1])
        sep_img = resize_copy_for_analysis(sep_img, (w, h))
        ref_img = resize_copy_for_analysis(ref_img, (w, h))
    result = compare_structural_images(ref_img, sep_img)
    return {
        'score': result['score'],
        'diff_map': result['diff_map'],
        'findings': result['findings'],
        'metrics': result['metrics']
    }

# =============================================================================
# RUTAS FLASK
# =============================================================================

@app.route('/')
def index():
    gs_status = f"Ghostscript v{GS_VERSION}" if GS_AVAILABLE else "No instalado"
    numba_status = "✓ Activo" if NUMBA_AVAILABLE else "✗ No instalado"
    return render_template('index.html', gs_status=gs_status, numba_status=numba_status, build_info=BUILD_INFO)


@app.route('/api/build_info')
def build_info():
    # Protocolo de Diagnóstico (ADR-010), paso 1 y 3: identificar y confirmar
    # la build real que está corriendo el servidor que se está probando.
    return jsonify(BUILD_INFO)


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
        page_count = get_real_page_count(filepath) if GS_AVAILABLE else 1
        return jsonify({'success': True, 'filename': temp_filename, 'original_name': filename, 'file_size_mb': file_size_mb, 'page_count': page_count, 'gs_available': GS_AVAILABLE})
    return jsonify({'success': True, 'chunk_index': chunk_index})


@app.route('/api/upload', methods=['POST'])
def upload_simple():
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
    page_count = get_real_page_count(filepath) if GS_AVAILABLE else 1
    return jsonify({'success': True, 'filename': temp_filename, 'original_name': filename, 'file_size_mb': file_size_mb, 'page_count': page_count, 'gs_available': GS_AVAILABLE})


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
        
        real_warnings = []
        dpi_lpi_ratio = dpi / lpi if lpi else 0
        if dpi_lpi_ratio < 8:
            real_warnings.append(f'El ratio DPI/LPI es {dpi_lpi_ratio:.1f}:1 (recomendado 8:1-16:1).')
        elif dpi_lpi_ratio > 16:
            real_warnings.append(f'El ratio DPI/LPI es {dpi_lpi_ratio:.1f}:1 (recomendado 8:1-16:1).')
        
        temp_dir = os.path.join(app.config['OUTPUT_FOLDER'], 'temp_' + filename.rsplit('.', 1)[0])
        os.makedirs(temp_dir, exist_ok=True)
        overall_start = time.time()
        
        if ext not in SUPPORTED_EXTENSIONS:
            return jsonify({'error': build_unsupported_format_error(ext)}), 400
        if not GS_AVAILABLE:
            return jsonify({'error': 'Ghostscript no está instalado.'}), 400
        
        print("\n" + "=" * 60)
        print("PROCESANDO CON TIFFSEP")
        print(f"Archivo: {filename}")
        print(f"DPI: {dpi} | LPI: {lpi} | Punto: {dot_shape}")
        print("=" * 60 + "\n")
        
        real_page_count = get_real_page_count(filepath) if GS_AVAILABLE else 1
        gs_timeout = max(120, real_page_count * PERFORMANCE_CONFIG['timeout_per_page'])
        plates, tiffsep_warnings = process_with_tiffsep(filepath, temp_dir, dpi, timeout=gs_timeout)
        real_warnings.extend(tiffsep_warnings)
        
        if not plates:
            return jsonify({'error': 'No se detectó ninguna placa con tinta.'}), 400
        
        sizes = [img.size for img in plates.values()]
        if len(set(sizes)) > 1:
            print(f"[WARN] Tamaños inconsistentes: {set(sizes)}")
            from collections import Counter
            most_common_size = Counter(sizes).most_common(1)[0][0]
            for name, img in plates.items():
                if img.size != most_common_size:
                    plates[name] = img.resize(most_common_size, Image.LANCZOS)
        
        print("\n[PROCESS] Aplicando semitono...")
        halftone_start = time.time()
        output_dir = os.path.join(app.config['OUTPUT_FOLDER'], filename.rsplit('.', 1)[0])
        os.makedirs(output_dir, exist_ok=True)
        angles = [15, 75, 0, 45, 30, 60, 10, 80, 20, 70]
        results = {}
        saved_files = []
        thumbnails = OrderedDict()
        plate_names = list(plates.keys())
        real_size = (0, 0)
        
        for idx, name in enumerate(plate_names):
            img_plate = plates.pop(name)
            arr_plate = np.array(img_plate)
            del img_plate
            angle = angles[idx % len(angles)] if auto_angles else config.get('angle', 45)
            halftoned_arr = apply_halftone(arr_plate, lpi, dpi, angle, dot_shape)
            del arr_plate
            pil_img = Image.fromarray(halftoned_arr, mode='L')
            del halftoned_arr
            real_size = pil_img.size
            preview_img = smart_thumbnail(pil_img.copy(), target_max=800)
            results[name] = {'preview': pil_to_base64(preview_img), 'size': real_size}
            thumbnails[name] = preview_img
            safe_name = name.replace(' ', '_').replace('/', '_').lower()
            safe_name = re.sub(r'[^\w\-_]', '', safe_name)
            out_path = os.path.join(output_dir, f"{safe_name}.png")
            pil_img.save(out_path, 'PNG', optimize=False)
            saved_files.append({'name': name, 'path': out_path})
            print(f"[SAVE] {name}: {real_size}")
            del pil_img
        
        manifest_path = os.path.join(output_dir, 'manifest.json')
        try:
            import json as _json
            manifest = [{'name': sf['name'], 'filename': os.path.basename(sf['path'])} for sf in saved_files]
            with open(manifest_path, 'w', encoding='utf-8') as mf:
                _json.dump(manifest, mf, ensure_ascii=False, indent=2)
        except Exception as _e:
            print(f"[WARN] No se pudo escribir manifest.json: {_e}")

        meta_path = os.path.join(output_dir, 'job_meta.json')
        try:
            import json as _json
            with open(meta_path, 'w', encoding='utf-8') as mfp:
                _json.dump({'lpi': lpi, 'dpi': dpi, 'dot_shape': dot_shape}, mfp, ensure_ascii=False, indent=2)
        except Exception as _e:
            print(f"[WARN] No se pudo escribir job_meta.json: {_e}")
        
        halftone_time = time.time() - halftone_start
        print(f"[PROCESS] Semitono completado en {halftone_time:.1f}s")
        shutil.rmtree(temp_dir, ignore_errors=True)
        
        zip_path = os.path.join(output_dir, 'separaciones.zip')
        rel_zip = os.path.relpath(zip_path, app.config['OUTPUT_FOLDER']).replace(os.sep, '/').replace('\\', '/')
        with zipfile.ZipFile(zip_path, 'w') as zf:
            for sf in saved_files:
                zf.write(sf['path'], os.path.basename(sf['path']))
        
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
        
        composite = create_composite_preview(thumbnails)
        total_time = time.time() - overall_start
        print(f"\n[PROCESS] ¡COMPLETADO! {len(thumbnails)} colores en {total_time:.1f}s\n")
        
        return jsonify({
            'success': True,
            'channels': {k: v for k, v in results.items()},
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
    import json as _json
    manifest_path = os.path.join(output_dir, 'manifest.json')
    if os.path.exists(manifest_path):
        try:
            with open(manifest_path, 'r', encoding='utf-8') as mf:
                manifest = _json.load(mf)
            manifest = [item for item in manifest if os.path.exists(os.path.join(output_dir, item['filename']))]
            if manifest:
                return manifest
        except Exception as e:
            print(f"[WARN] manifest.json inválido: {e}")
    diagnostic_names = {'composite_visual.png', 'diff_map.png', 'difference_map.png', 'analysis_diff.png', 'structural_diff.png'}
    diagnostic_prefixes = ('preview_', 'thumb_', 'thumbnail_', 'diff_', 'difference_', 'diagnostic_')
    png_files = sorted([f for f in os.listdir(output_dir) if f.lower().endswith('.png') and f.lower() not in diagnostic_names and not f.lower().startswith(diagnostic_prefixes)])
    return [{'name': f.replace('.png', '').replace('_', ' ').title(), 'filename': f} for f in png_files]


@app.route('/api/generate_pdf', methods=['POST'])
def generate_pdf():
    data = request.json
    job_name = data.get('job_name')
    if not job_name:
        return jsonify({'error': 'job_name requerido'}), 400
    output_dir = os.path.join(app.config['OUTPUT_FOLDER'], job_name)
    if not os.path.exists(output_dir):
        return jsonify({'error': 'No se encontraron separaciones.'}), 404
    manifest = load_job_manifest(output_dir)
    if not manifest:
        return jsonify({'error': 'No se encontraron archivos PNG.'}), 404
    try:
        pages = []
        for entry in manifest:
            png_file = entry['filename']
            png_path = os.path.join(output_dir, png_file)
            img = Image.open(png_path).convert('RGB')
            pages.append(img)
        if not pages:
            return jsonify({'error': 'No se pudieron cargar las imágenes.'}), 500
        pdf_path = os.path.join(output_dir, 'separaciones.pdf')
        pages[0].save(pdf_path, format='PDF', save_all=True, append_images=pages[1:], resolution=150)
        print(f'[PDF] Generado: {pdf_path} ({len(pages)} páginas)')
        return jsonify({'success': True, 'pdf_path': pdf_path, 'pages': len(pages)})
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
# VISUALIZACIÓN DE SEPARACIONES CON COLORES REALES
# =============================================================================

CMYK_COLORS = {
    'Cyan': (0, 180, 216),
    'Magenta': (230, 58, 110),
    'Yellow': (245, 200, 0),
    'Black': (26, 26, 46),
}

SPOT_COLOR_PALETTE = [
    (255, 99, 132), (75, 192, 192), (153, 102, 255), (255, 159, 64),
    (199, 199, 199), (83, 102, 255), (255, 205, 86), (201, 203, 207),
    (54, 162, 235), (255, 99, 132), (255, 206, 86), (75, 192, 192),
    (153, 102, 255), (255, 159, 64), (255, 99, 71), (100, 149, 237),
    (255, 215, 0), (0, 128, 128), (128, 0, 128), (255, 165, 0),
]


def get_color_for_separation(name, idx=0):
    name_lower = name.lower().strip()
    for cmyk_name, color in CMYK_COLORS.items():
        if cmyk_name.lower() in name_lower:
            return color
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
    h = hashlib.md5(name_lower.encode()).hexdigest()
    hue = int(h[:8], 16) / 0xffffffff
    sat = 0.55 + (int(h[8:10], 16) / 255) * 0.25
    light = 0.45 + (int(h[10:12], 16) / 255) * 0.15
    r, g, b = colorsys.hls_to_rgb(hue, light, sat)
    return (int(r * 255), int(g * 255), int(b * 255))


@app.route('/api/separation_info', methods=['POST'])
def get_separation_info():
    data = request.json
    job_name = data.get('job_name')
    if not job_name:
        return jsonify({'error': 'job_name requerido'}), 400
    output_dir = os.path.join(app.config['OUTPUT_FOLDER'], job_name)
    if not os.path.exists(output_dir):
        return jsonify({'error': 'Trabajo no encontrado'}), 404
    try:
        manifest = load_job_manifest(output_dir)
        separations_info = []
        for idx, entry in enumerate(manifest):
            png_file = entry['filename']
            name = entry['name']
            is_cmyk = any(c in name.lower() for c in ['cyan', 'magenta', 'yellow', 'black'])
            color_rgb = get_color_for_separation(name, idx)
            hex_color = '#{:02x}{:02x}{:02x}'.format(*color_rgb)
            img_path = os.path.join(output_dir, png_file)
            try:
                with Image.open(img_path) as img:
                    size = img.size
            except:
                size = [0, 0]
            separations_info.append({'name': name, 'filename': png_file, 'is_cmyk': is_cmyk, 'color_rgb': color_rgb, 'hex_color': hex_color, 'size': size, 'index': idx})
        return jsonify({'success': True, 'separations': separations_info, 'count': len(separations_info)})
    except Exception as e:
        import traceback
        print(f"[ERROR /api/separation_info] {traceback.format_exc()}")
        return jsonify({'error': friendly_error_message(e)}), 500


@app.route('/api/composite_visual', methods=['POST'])
def generate_composite_visual():
    data = request.json
    job_name = data.get('job_name')
    mode = data.get('mode', 'overprint')
    if not job_name:
        return jsonify({'error': 'job_name requerido'}), 400
    output_dir = os.path.join(app.config['OUTPUT_FOLDER'], job_name)
    if not os.path.exists(output_dir):
        return jsonify({'error': 'Trabajo no encontrado'}), 404
    try:
        manifest = load_job_manifest(output_dir)
        if not manifest:
            return jsonify({'error': 'No hay separaciones'}), 404
        base_img = Image.open(os.path.join(output_dir, manifest[0]['filename'])).convert('L')
        w, h = base_img.size
        composite = np.zeros((h, w, 4), dtype=np.uint8)
        composite[:, :, 3] = 255
        for idx, entry in enumerate(manifest):
            png_file = entry['filename']
            img = Image.open(os.path.join(output_dir, png_file)).convert('L')
            if img.size != (w, h):
                img = img.resize((w, h), Image.LANCZOS)
            arr = np.array(img)
            name = entry['name']
            color_rgb = get_color_for_separation(name, idx)
            ink_mask = (255 - arr).astype(np.float32) / 255.0
            if mode == 'overprint':
                for c in range(3):
                    composite[:, :, c] = np.clip(composite[:, :, c].astype(np.float32) + ink_mask * color_rgb[c] * 0.8, 0, 255).astype(np.uint8)
            else:
                for c in range(3):
                    composite[:, :, c] = np.where(ink_mask > 0.1, (composite[:, :, c].astype(np.float32) * (1 - ink_mask) + color_rgb[c] * ink_mask * 0.9).astype(np.uint8), composite[:, :, c])
        result_img = Image.fromarray(composite, mode='RGBA')
        preview_path = os.path.join(output_dir, 'composite_visual.png')
        result_img.save(preview_path, 'PNG')
        return jsonify({'success': True, 'composite': pil_to_base64(result_img), 'mode': mode, 'colors_used': len(manifest)})
    except Exception as e:
        import traceback
        print(f"[ERROR /api/composite_visual] {traceback.format_exc()}")
        return jsonify({'error': friendly_error_message(e)}), 500


@app.route('/api/separation_preview/<job_name>/<filename>')
def get_separation_preview(job_name, filename):
    output_dir = os.path.join(app.config['OUTPUT_FOLDER'], job_name)
    filepath = os.path.join(output_dir, filename)
    if not os.path.exists(filepath):
        return jsonify({'error': 'Archivo no encontrado'}), 404
    try:
        img = Image.open(filepath).convert('L')
        name = filename.replace('.png', '').replace('_', ' ').title()
        manifest = load_job_manifest(output_dir)
        manifest_filenames = [m['filename'] for m in manifest]
        idx = manifest_filenames.index(filename) if filename in manifest_filenames else 0
        color_rgb = get_color_for_separation(name, idx)
        arr = np.array(img)
        h, w = arr.shape
        ink_mask = (255 - arr).astype(np.float32) / 255.0
        colored = np.zeros((h, w, 3), dtype=np.uint8)
        for c in range(3):
            colored[:, :, c] = (ink_mask * color_rgb[c]).astype(np.uint8)
        result = Image.fromarray(colored, mode='RGB')
        return jsonify({'success': True, 'preview': pil_to_base64(result), 'name': name, 'hex_color': '#{:02x}{:02x}{:02x}'.format(*color_rgb), 'color_rgb': color_rgb})
    except Exception as e:
        import traceback
        print(f"[ERROR /api/separation_preview] {traceback.format_exc()}")
        return jsonify({'error': friendly_error_message(e)}), 500

# =============================================================================
# ANÁLISIS DE DIFERENCIAS MEJORADO - AUTO-ALINEACIÓN
# =============================================================================

def _largest_ink_blob_bbox_fallback(mask):
    """Versión de fallback sin scipy."""
    ys, xs = np.where(mask)
    if len(xs) == 0:
        return None
    return int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1


def _phase_correlation_shift(a, b):
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
    referencia encaje sobre la separación.
    
    Estrategia mejorada:
    1. Encuentra el BLOB en la referencia (el arte limpio)
    2. Encuentra el BLOB de arte REAL en la separación (interior del área de impresión)
    3. Calcula escala y offset para centrar la referencia sobre ese BLOB
    4. Refinamiento por correlación de fase (FFT)
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

        # Trabajar en baja resolución (lado largo ~700px)
        WORK_DIM = 700
        f_sep = WORK_DIM / max(sep_w, sep_h)
        proc_w, proc_h = max(1, round(sep_w * f_sep)), max(1, round(sep_h * f_sep))

        # Cobertura de tinta combinada de la separación
        light_through = np.ones((proc_h, proc_w), dtype=np.float32)
        for entry in manifest:
            plate = Image.open(os.path.join(output_dir, entry['filename'])).convert('L')
            plate = plate.resize((proc_w, proc_h), Image.LANCZOS)
            arr = np.array(plate).astype(np.float32)
            ink = (255.0 - arr) / 255.0
            light_through *= (1.0 - ink)
        sep_ink = 1.0 - light_through
        sep_mask = sep_ink > 0.04

        # Referencia a la misma resolución de trabajo
        ref_w, ref_h = ref_img_full.size
        f_ref = WORK_DIM / max(ref_w, ref_h)
        rproc_w, rproc_h = max(1, round(ref_w * f_ref)), max(1, round(ref_h * f_ref))
        ref_small = ref_img_full.resize((rproc_w, rproc_h), Image.LANCZOS)
        ref_arr = np.array(ref_small).astype(np.float32)
        ref_ink = (255.0 - ref_arr) / 255.0
        ref_mask = ref_ink > 0.04

        # Encontrar BLOB en la referencia (el arte limpio)
        bbox_ref = _largest_ink_blob_bbox(ref_mask, filter_elongated=False, min_density=0.05)
        if bbox_ref is None:
            return jsonify({'error': 'No se detectó contenido en la imagen de referencia'}), 400

        # Encontrar el arte REAL en la separación (interior del área de impresión)
        bbox_sep = _find_art_blob(sep_mask, min_area=500)
        if bbox_sep is None:
            # Fallback: usar el BLOB más grande
            bbox_sep = _largest_ink_blob_bbox(sep_mask, filter_elongated=False, min_density=0.0)
        if bbox_sep is None:
            return jsonify({'error': 'La separación no tiene tinta detectable para alinear'}), 400

        # --- Calcular escala y offset ---
        sx0, sy0, sx1, sy1 = [v / f_sep for v in bbox_sep]
        rx0, ry0, rx1, ry1 = [v / f_ref for v in bbox_ref]
        
        sep_box_w, sep_box_h = sx1 - sx0, sy1 - sy0
        ref_box_w, ref_box_h = max(rx1 - rx0, 1e-6), max(ry1 - ry0, 1e-6)

        # Calcular escala basada en el tamaño de los BLOBs
        scale_w = sep_box_w / ref_box_w if ref_box_w > 0 else 1.0
        scale_h = sep_box_h / ref_box_h if ref_box_h > 0 else 1.0
        scale = (scale_w + scale_h) / 2.0
        
        # Límite de seguridad amplio (evita valores degenerados/0/infinito), no
        # una suposición de que ref y separación tienen resolución parecida:
        # una foto de referencia .jpg suele tener MUCHA menos resolución que
        # una separación a 600dpi, así que la escala real necesaria puede ser
        # de varios cientos de %.
        scale = float(np.clip(scale, 0.01, 100.0))

        # Centrar la referencia sobre el BLOB de la separación
        sep_center_x = (sx0 + sx1) / 2.0
        sep_center_y = (sy0 + sy1) / 2.0
        
        ref_scaled_w = ref_box_w * scale
        ref_scaled_h = ref_box_h * scale
        ref_center_x = (rx0 + rx1) * scale / 2.0
        ref_center_y = (ry0 + ry1) * scale / 2.0
        
        offset_x = sep_center_x - ref_center_x
        offset_y = sep_center_y - ref_center_y

        # Asegurar que la referencia no se salga del lienzo
        margin_w = ref_scaled_w * 0.05
        margin_h = ref_scaled_h * 0.05
        
        if offset_x < -margin_w:
            offset_x = -margin_w
        if offset_y < -margin_h:
            offset_y = -margin_h
        if offset_x + ref_scaled_w > sep_w + margin_w:
            offset_x = sep_w - ref_scaled_w - margin_w
        if offset_y + ref_scaled_h > sep_h + margin_h:
            offset_y = sep_h - ref_scaled_h - margin_h

        # --- Refinamiento por correlación de fase (FFT) ---
        ref_scaled_full = ref_img_full.resize(
            (max(1, round(ref_w * scale)), max(1, round(ref_h * scale))), Image.LANCZOS
        )
        ref_placed_full = Image.new('L', (sep_w, sep_h), 255)
        ref_placed_full.paste(ref_scaled_full, (round(offset_x), round(offset_y)))
        ref_placed_proc = ref_placed_full.resize((proc_w, proc_h), Image.LANCZOS)
        ref_placed_ink = (255.0 - np.array(ref_placed_proc).astype(np.float32)) / 255.0

        dx, dy, confidence = _phase_correlation_shift(sep_ink, ref_placed_ink)

        max_shift = 0.15 * WORK_DIM
        if abs(dx) <= max_shift and abs(dy) <= max_shift:
            offset_x -= dx / f_sep
            offset_y -= dy / f_sep
        else:
            confidence = min(confidence, 0.4)

        offset_x = round(offset_x)
        offset_y = round(offset_y)

        print(f"[AUTO_ALIGN] BLOB sep: ({sx0:.0f},{sy0:.0f})->({sx1:.0f},{sy1:.0f}) "
              f"tamaño {sep_box_w:.0f}x{sep_box_h:.0f}")
        print(f"[AUTO_ALIGN] BLOB ref: ({rx0:.0f},{ry0:.0f})->({rx1:.0f},{ry1:.0f}) "
              f"tamaño {ref_box_w:.0f}x{ref_box_h:.0f}")
        # El slider/align_scale de /api/analyze_detailed (y el de la UI) no usan
        # escala absoluta en píxeles nativos, sino escala relativa al "encaje"
        # (align_scale=1.0 = referencia encajada al ancho del lienzo, igual que
        # se ve en pantalla por CSS width:100%). Convertimos aquí para que el
        # resultado de auto-alinear sea directamente usable por esos dos.
        base_fit_scale = (sep_w / ref_w) if ref_w else 1.0
        relative_scale = scale / base_fit_scale if base_fit_scale else scale

        print(f"[AUTO_ALIGN] Escala absoluta: {scale:.3f}, relativa al encaje: "
              f"{relative_scale:.3f} ({relative_scale*100:.1f}%), "
              f"Offset: ({offset_x}, {offset_y}), Confianza: {confidence:.2f}")

        return jsonify({
            'success': True,
            'scale': relative_scale,
            'offset_x': offset_x,
            'offset_y': offset_y,
            'confidence': round(confidence, 2)
        })

    except Exception as e:
        import traceback
        print(f"[ERROR /api/auto_align] {traceback.format_exc()}")
        return jsonify({'error': friendly_error_message(e)}), 500


def is_reinforcement_channel_name(name, extra_keywords=None):
    """
    Detecta canales tipo 'blanco de refuerzo' / underbase por su nombre.
    Esta tinta va DEBAJO de otras capas para dar opacidad sobre prendas
    oscuras -- no es una marca visible propia, así que no debe contarse
    como "tinta visible" al armar la separación combinada para comparar
    contra la foto de referencia (si se cuenta, cada punto de refuerzo
    aparece como "información adicional" falsa).

    OJO: deliberadamente NO se incluye 'blanco'/'white' a secas por
    defecto, porque un canal blanco puede ser un color de diseño
    genuinamente visible (ej. texto blanco sobre playera de color) y no
    siempre refuerzo. 'extra_keywords' permite ajustar esto por trabajo.
    """
    default_keywords = ['base', 'refuerzo', 'underbase', 'under base', 'highlight blanco']
    keywords = default_keywords + list(extra_keywords or [])
    normalized = (name or '').strip().lower()
    normalized = (normalized
                  .replace('á', 'a').replace('é', 'e').replace('í', 'i')
                  .replace('ó', 'o').replace('ú', 'u'))
    return any(kw in normalized for kw in keywords)


@app.route('/api/analyze_detailed', methods=['POST'])
def analyze_detailed():
    """
    Análisis detallado de diferencias entre separación y referencia.
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
        ref_bytes = base64.b64decode(ref_data.split(',')[1])
        with Image.open(io.BytesIO(ref_bytes)) as uploaded_ref:
            ref_img = image_copy_for_analysis(uploaded_ref, mode='RGB')

        # Debe hacerse ANTES de escalar/pegar sobre el lienzo grande: el
        # flood-fill necesita ver el borde real de la FOTO de referencia
        # (donde normalmente está el fondo del mockup), no el borde del
        # lienzo blanco final, donde el fondo ya quedaría aislado en el
        # centro y no conectado a ningún borde.
        if data.get('remove_ref_background', True):
            ref_img = remove_reference_background(ref_img)
        ref_source_size = ref_img.size

        manifest = load_job_manifest(output_dir)
        if not manifest:
            return jsonify({'error': 'No hay separaciones'}), 404

        align_scale = max(0.05, float(data.get('align_scale', 1.0)))
        align_x = int(float(data.get('align_x', 0)))
        align_y = int(float(data.get('align_y', 0)))

        with Image.open(os.path.join(output_dir, manifest[0]['filename'])) as sample_src:
            sample_img = image_copy_for_analysis(sample_src, mode='L')
        w, h = sample_img.size
        analysis_size = (w, h)
        assert_analysis_dimensions('Separación muestra', sample_img, analysis_size)

        # El frontend muestra la referencia a "ancho 100% del contenedor" por CSS,
        # es decir, encajada al ancho del lienzo de separación ANTES de aplicar el
        # zoom del slider. align_scale=1.0 (100%) debe significar "encaja al ancho
        # del lienzo", no "tamaño nativo del jpg". Sin esta base, una referencia de
        # baja resolución (foto/jpg) quedaba minúscula en una esquina al comparar,
        # aunque en pantalla se viera bien encajada.
        base_fit_scale = (w / ref_img.width) if ref_img.width else 1.0
        effective_scale = align_scale * base_fit_scale
        scaled_w = max(1, int(round(ref_img.width * effective_scale)))
        scaled_h = max(1, int(round(ref_img.height * effective_scale)))
        ref_scaled = resize_copy_for_analysis(ref_img, (scaled_w, scaled_h))
        assert_analysis_dimensions('Referencia original', ref_img, ref_source_size)

        aligned_ref = Image.new('RGB', analysis_size, (255, 255, 255))
        paste_box = (align_x, align_y)
        aligned_ref.paste(ref_scaled, paste_box)
        assert_analysis_dimensions('Referencia alineada', aligned_ref, analysis_size)
        ref_arr = np.array(aligned_ref).astype(np.float32)
        assert_analysis_dimensions('Array referencia alineada', ref_arr, analysis_size)

        findings = []
        light_through = np.ones((h, w), dtype=np.float32)

        reinforcement_mode = str(data.get('reinforcement_channels', 'auto')).lower()
        extra_reinforcement_keywords = [
            kw.strip().lower() for kw in str(data.get('reinforcement_keywords', '')).split(',') if kw.strip()
        ]
        excluded_channels = []

        for idx, entry in enumerate(manifest):
            png_file = entry['filename']
            sep_path = os.path.join(output_dir, png_file)
            with Image.open(sep_path) as sep_src:
                sep_img = image_copy_for_analysis(sep_src, mode='L')
            if sep_img.size != analysis_size:
                sep_img = resize_copy_for_analysis(sep_img, analysis_size)
            assert_analysis_dimensions(f"Separación '{png_file}'", sep_img, analysis_size)
            sep_arr = np.array(sep_img).astype(np.float32)
            assert_analysis_dimensions(f"Array separación '{png_file}'", sep_arr, analysis_size)

            ink = (255 - sep_arr) / 255.0
            assert_analysis_dimensions(f"Máscara tinta '{png_file}'", ink, analysis_size)

            name = entry.get('name') or png_file.replace('.png', '').replace('_', ' ').title()

            # Blanco de refuerzo / underbase: no cuenta como "tinta visible"
            # para la comparación estructural (ver is_reinforcement_channel_name).
            is_reinforcement = (
                reinforcement_mode != 'none' and
                is_reinforcement_channel_name(name, extra_reinforcement_keywords)
            )
            if is_reinforcement:
                excluded_channels.append(name)
            else:
                light_through *= (1.0 - ink)

            ink_pixels = np.sum(ink > 0.1)
            total_pixels = w * h
            coverage_pct = ink_pixels / total_pixels * 100

            if coverage_pct < 0.3:
                findings.append({
                    'title': f'{name} - Posiblemente vacía',
                    'status': 'warn',
                    'pct': round(coverage_pct, 2),
                    'msg': f'Solo {coverage_pct:.2f}% de cobertura.'
                })
            elif coverage_pct < 5:
                findings.append({
                    'title': f'{name} - Cobertura baja',
                    'status': 'ok',
                    'pct': round(coverage_pct, 1),
                    'msg': f'{coverage_pct:.1f}% de cobertura.' + (' (refuerzo, excluido del encaje estructural)' if is_reinforcement else '')
                })

            ref_gray = np.mean(ref_arr, axis=2)
            ref_ink = (255 - ref_gray) / 255.0
            false_ink = np.sum((ink > 0.3) & (ref_ink < 0.1)) / total_pixels * 100
            if false_ink > 10 and not is_reinforcement:
                findings.append({
                    'title': f'{name} - Posible overprint no deseado',
                    'status': 'warn',
                    'pct': int(false_ink),
                    'msg': f'{int(false_ink)}% de tinta donde la referencia es blanca.'
                })

        if excluded_channels:
            print(f"[ANALYZE] Canales de refuerzo excluidos del encaje estructural: {excluded_channels}")

        total_coverage = 1.0 - light_through
        total_coverage_pct = int(np.mean(total_coverage) * 100)

        combined_sep_arr = np.clip(255.0 - total_coverage * 255.0, 0, 255).astype(np.uint8)
        combined_sep_img = Image.fromarray(combined_sep_arr, mode='L')
        assert_analysis_dimensions('Separación combinada', combined_sep_img, analysis_size)

        debug_save_image(aligned_ref, "aligned_ref", 0)
        debug_save_image(combined_sep_img, "combined_sep", 0)

        edge_tolerance_px = int(float(data.get('edge_tolerance_px', 3)))
        edge_tolerance_px = max(0, min(edge_tolerance_px, 25))

        # Auto-detección de arte con tramado/medios tonos: si el job guardó su
        # LPI (líneas por pulgada) al generar las separaciones, la celda de
        # trama en píxeles es dpi/lpi. Un LPI de 65+ y un ratio dpi/lpi típico
        # de impresión (8-16:1) indican una separación con puntos de trama
        # finos, donde comparar píxel-a-píxel no tiene sentido (ver
        # compute_density_metrics). El frontend puede forzar 'on'/'off' o
        # dejarlo en 'auto'.
        density_mode_req = str(data.get('density_mode', 'auto')).lower()
        halftone_cell_px = None
        try:
            import json as _json
            meta_path = os.path.join(output_dir, 'job_meta.json')
            if os.path.exists(meta_path):
                with open(meta_path, 'r', encoding='utf-8') as mfp:
                    job_meta = _json.load(mfp)
                job_lpi = job_meta.get('lpi')
                job_dpi = job_meta.get('dpi')
                if job_lpi and job_dpi and job_lpi > 0:
                    halftone_cell_px = job_dpi / job_lpi
        except Exception as _e:
            print(f"[WARN] No se pudo leer job_meta.json: {_e}")

        if density_mode_req == 'on':
            use_density_mode = True
        elif density_mode_req == 'off':
            use_density_mode = False
        else:
            # auto: solo si el job tiene info de trama y la celda es
            # razonablemente fina (si es muy gruesa, la comparación normal
            # con tolerancia de borde ya la resuelve bien).
            use_density_mode = halftone_cell_px is not None and halftone_cell_px >= 3

        density_kernel_px = max(3, int(round((halftone_cell_px or 9) * 1.5)))

        structural = compare_structural_images(
            aligned_ref, combined_sep_img,
            edge_tolerance_px=edge_tolerance_px,
            density_mode=use_density_mode,
            density_kernel_px=density_kernel_px,
        )
        print(f"[ANALYZE] Modo comparación: {'densidad (halftone_cell=' + str(round(halftone_cell_px,1)) + 'px, kernel=' + str(density_kernel_px) + 'px)' if use_density_mode else 'binario + tolerancia de borde'}")
        score = structural['score']
        diff_img = structural['diff_map']
        assert_analysis_dimensions('Mapa de diferencias', diff_img, analysis_size)

        findings = structural['findings'] + findings

        return jsonify({
            'success': True,
            'score': score,
            'diff_map': pil_to_base64(diff_img),
            'findings': findings,
            'metrics': {
                **structural['metrics'],
                'structural_score': score,
                'total_coverage': total_coverage_pct,
                'separation_count': len(manifest)
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
    print("  Extracción avanzada de Spot Colors")
    print("=" * 70)
    print(f"  Ghostscript: {GS_VERSION if GS_AVAILABLE else 'NO INSTALADO'}")
    print(f"  Numba JIT: {'✓ ACTIVADO' if NUMBA_AVAILABLE else '✗ No instalado'}")
    print(f"  Workers paralelos: {PERFORMANCE_CONFIG['max_workers']}")
    print(f"  Máximo archivo: 10 GB")
    print("=" * 70)
    print("  http://localhost:5000")
    print("=" * 70)
    app.run(host='0.0.0.0', port=5000, debug=True, threaded=True)