# Halftone RIP Pro v2.0 - Estructura Modular

## 🎨 Nuevo Diseño Moderno

- **Dark-first design** con glassmorphism
- **Micro-interacciones** y animaciones suaves
- **Sistema de diseño** con variables CSS
- **Responsive** para móvil y desktop

## 📁 Nueva Estructura de Archivos

```
halftone_rip_pro/
├── app.py                      # Backend Flask (sin cambios en la lógica)
├── templates/
│   └── index.html              # Entry point unificado (HTML limpio)
├── static/
│   ├── css/
│   │   └── styles.css          # Sistema de diseño completo
│   └── js/
│       ├── rip.js              # Módulo RIP & Separaciones
│       └── preview.js          # Módulo Preview & Diferencias
├── uploads/                    # Archivos subidos (temporal)
└── outputs/                    # Separaciones generadas
```

## 🔧 Cambios Realizados

### 1. Separación de JavaScript
- **`rip.js`**: Maneja todo lo relacionado con upload, configuración, procesamiento y resultados
- **`preview.js`**: Maneja visualización de separaciones, composición, capas y análisis de diferencias
- **Comunicación entre módulos** via CustomEvents (`rip:jobComplete`)

### 2. CSS Moderno
- **Variables CSS** para theming completo (dark/light)
- **Glassmorphism** en paneles y header
- **Animaciones** con keyframes (fadeIn, slideIn, pulse, shimmer)
- **Efectos hover** en tarjetas y botones
- **Scrollbar personalizada**
- **Gradientes** en acentos y displays

### 3. HTML Limpio
- Sin JavaScript inline
- Sin CSS inline
- Estructura semántica
- Carga de fuentes optimizada

## 🚀 Instalación

```bash
# 1. Copiar los archivos a tu carpeta del proyecto
cp app.py tu_proyecto/
cp -r templates/ tu_proyecto/
cp -r static/ tu_proyecto/

# 2. Instalar dependencias
pip install -r requirements.txt

# 3. Ejecutar
python app.py
```

## 🎯 Características del Nuevo Diseño

### RIP & Separaciones
- Drop zone con efecto de brillo al hover
- Stats cards con hover elevación
- Progress bar con animación de shine
- Console log con colores por tipo
- Tarjetas de separación con animación stagger

### Previsualizador
- Tabs de vista con indicador animado
- Canvas de composición en tiempo real
- Capas drag & drop para reordenar
- Color pickers integrados
- Modos Overprint / Knockout

### Análisis de Diferencias
- Drop zone para referencia
- Slider de opacidad para overlay
- Score card con color condicional
- Diff cards con barras animadas
- Badges de estado (OK / REVISAR / ERROR)

## 🌓 Tema Claro/Oscuro

Click en el botón del header para cambiar entre modos. La preferencia se guarda en localStorage.

## ⚠️ Notas Importantes

1. **NO modificar `app.py` a menos que sea necesario** - La ruta `/` debe mantener `render_template('index.html')`
2. **NO mezclar JavaScript inline** en el HTML - Todo está en los archivos `.js`
3. **NO mezclar CSS inline** - Todo está en `styles.css`
4. Si necesitas agregar funcionalidad, extiende los módulos JS correspondientes

## 🐛 Troubleshooting

**Problema**: Solo veo "Servidor funcionando correctamente"
**Solución**: Verifica que `app.py` tenga `render_template('index.html')` en la ruta `/`, no un HTML inline.

**Problema**: Los estilos no cargan
**Solución**: Verifica que la carpeta `static/css/` exista y que Flask esté configurado con `static_folder='static'`.

**Problema**: Los JS no funcionan
**Solución**: Verifica la consola del navegador (F12) para errores. Asegúrate de que los archivos estén en `static/js/`.
