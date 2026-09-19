# Clasificador de Escritorio

Ordena el Escritorio de macOS con [TypeSafe](https://docs.typesafe.ai). Para cada archivo decide:

| Decisión | Quién la toma |
| --- | --- |
| **Las capturas de pantalla van siempre a `Capturas/`** | Código (nombre del archivo o metadato de macOS, que sobrevive a los renombres) |
| **Carpeta** destino | TypeSafe (`Choice`); si la confianza es baja → `Needs_Review/` |
| **Subcarpeta** dentro de esa carpeta | TypeSafe (`Choice`) |
| **Importancia**, de descartable a "no se puede perder" | TypeSafe (`Score` 0–3) |
| ¿Tiene **datos sensibles**? ¿Es **reemplazable**? ¿Es **temporal**? | TypeSafe (`Noul`, una probabilidad cada una) |
| ¿Es un **duplicado exacto**? | Código (SHA-256) |
| **Veredicto**: conservar / guardar / se puede borrar | Código, combinando todo lo anterior con umbrales editables |

**Nunca borra nada.** Los candidatos a borrar se apartan en `Para_Borrar/` para que los revises, cada
corrida se puede revertir con `--deshacer`, y queda un informe CSV con todos los puntajes.

## Requisitos

- macOS (usa `xattr`, `mdls`, `textutil` y, para leer imágenes y PDFs, Vision y PDFKit)
- Python 3.9+ — sin dependencias, alcanza con el `python3` del sistema
- Una API key de TypeSafe: https://console.typesafe.ai/
- Opcional: Xcode Command Line Tools (`swiftc`) para el OCR de `--con-contenido`

## Instalación

El script ordena **la carpeta que contiene a su propia carpeta**, así que hay que clonarlo dentro del Escritorio:

```bash
cd ~/Desktop
git clone https://github.com/sirviejo/clasificador-escritorio.git clasificador
cd clasificador
cp .env.example .env    # y completá TYPESAFE_API_KEY
```

## Uso

```bash
python3 clasificar.py --dry              # muestra qué haría, sin mover nada
python3 clasificar.py                    # clasifica y mueve
python3 clasificar.py --con-contenido    # además lee el archivo (ver "Privacidad")
python3 clasificar.py --carpeta Capturas # analiza los archivos sueltos de una carpeta del Escritorio
python3 clasificar.py --limite 20        # solo los primeros 20 archivos
python3 clasificar.py --solo-capturas    # solo mueve capturas; no usa la API
python3 clasificar.py --deshacer         # revierte la última corrida
```

Ejemplo de salida:

```
Analizando 3 archivos…
  Finanzas_Facturacion/Facturas_Emitidas/ ← INVOICE_0042_ACME.pdf  (conservar: importante + datos sensibles · importancia 3.0/3)
  Capturas/Mapas_Lugares/                 ← Captura de pantalla 2026-04-22….png  (guardar: sin motivo para borrar ni para destacar · importancia 0.8/3)
  Para_Borrar/                            ← Zoom-installer.dmg  (borrar: reemplazable · importancia 0.1/3)

Importantes (conservar): 1 · Guardados sin más: 1 · Se pueden borrar: 1 (apartados en Para_Borrar/, nada se borra solo)
Informe: informes/informe-20260919-125152.csv
```

## Cómo funciona

El código maneja el flujo; el modelo solo responde preguntas chicas y tipadas.

1. **Reglas fijas primero.** Capturas y duplicados exactos se detectan sin IA.
2. **Un request por lote de archivos.** Todas las preguntas de todos los archivos del lote viajan juntas y se
   responden en paralelo. Las de subcarpeta son *especulativas*: se pregunta una por cada carpeta posible
   ("suponiendo que va en X, ¿en qué subcarpeta?") y el código usa solo la de la carpeta que resultó elegida,
   en vez de hacer un segundo request.
3. **La política vive en el código.** El veredicto sale de combinar los puntajes con umbrales que están al
   principio de `clasificar.py` (`MIN_CONFIDENCE`, `IMPORTANTE_DESDE`, `BORRABLE_SENAL_MIN`…). Cambiarlos no
   requiere volver a consultar la API: los puntajes crudos quedan en el CSV.

| Veredicto | Regla |
| --- | --- |
| `conservar` | importancia ≥ 2.0 **o** probabilidad de datos sensibles ≥ 0.7 |
| `borrar` → `Para_Borrar/` | duplicado exacto, **o** importancia ≤ 1.0 y (reemplazable o temporal) ≥ 0.6 |
| `guardar` | todo lo demás: va a su carpeta sin marca |

Las carpetas que ya existen en tu Escritorio se ofrecen solas como opciones (descritas por lo que contienen),
igual que las subcarpetas existentes. Las categorías y subcarpetas predefinidas están en `CATEGORIES` y
`SUBFOLDERS`: editalas para tu forma de trabajar.

## Configuración (`.env`)

| Variable | |
| --- | --- |
| `TYPESAFE_API_KEY` | Obligatoria. |
| `PROPIETARIO` | Opcional. Quién sos (`Mi Empresa / Mi Nombre`), para distinguir facturas emitidas de recibidas. |
| `IGNORAR` | Opcional. Archivos o carpetas del Escritorio, separados por coma, que no se tocan ni se ofrecen como destino. |

## Privacidad

- **Por defecto** se envía a TypeSafe solo metadata: nombre, extensión, tipo, tamaño, fecha y URL de descarga.
  Las capturas no se analizan (su nombre no dice nada): van directo a `Capturas/`.
- **Con `--con-contenido`** se envían además los primeros 1.200 caracteres de cada archivo: texto plano,
  documentos (`textutil`), PDFs y el **OCR de las imágenes**, que se hace localmente con Vision. Una captura de
  tu home banking incluye tu saldo: usalo sabiendo eso.
- `.env`, `informes/` y `movimientos.jsonl` contienen tu key y los nombres de tus archivos; están en `.gitignore`.

Costo: Jev cobra por tokens de entrada (USD 0,042 por millón al momento de escribir esto); unos cientos de
archivos cuestan centavos.

## Mover capturas nuevas automáticamente (opcional)

`com.clasificador.capturas.plist` es un LaunchAgent que corre `--solo-capturas` (sin API) cada vez que cambia
el Escritorio:

```bash
sed "s#/Users/TU_USUARIO#$HOME#g" com.clasificador.capturas.plist > ~/Library/LaunchAgents/com.clasificador.capturas.plist
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.clasificador.capturas.plist
```

La primera vez macOS puede pedir permiso para que `python3` acceda al Escritorio. Para desinstalarlo:
`launchctl bootout gui/$(id -u)/com.clasificador.capturas` y borrá el `.plist`.

Alternativa sin script: `defaults write com.apple.screencapture location ~/Desktop/Capturas`.

## Limitaciones

- Solo macOS.
- Procesa los archivos sueltos de una carpeta; no mueve carpetas ni entra en subcarpetas.
- Los umbrales son un punto de partida razonable, no una verdad: corré con `--dry`, mirá el CSV y ajustalos.
- Las respuestas tipadas garantizan el formato, no que el juicio sea correcto. Por eso nada se borra solo.

## Licencia

[MIT](LICENSE)
