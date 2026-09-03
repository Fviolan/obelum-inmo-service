# obelum-inmo-service

Motor de auditoría inmobiliaria para el Flujo 1 **"Audit Inmob web openrouter"** de n8n.

Envuelve los tres scripts de la skill `audit-inmob-web-f` (`recon.py`, `compare.py`,
`generate.py`) **sin modificarlos** y los expone por HTTP, igual que `obelum-pdf-service` hace
con la skill genérica.

Los ficheros viven en la subcarpeta `obelum-inmo-service/`, no en la raíz — mismo patrón que el
otro repo, para que Easypanel lo despliegue igual.

## Endpoints

| Método | Ruta | Qué hace |
|---|---|---|
| GET | `/health` | Estado, buscador activo y entradas en caché |
| POST | `/recon` | Audita una web. `{url, ciudad, paginas, cache}` |
| POST | `/competidores` | Busca rivales locales. `{ciudad, dominio_excluido, maximo}` |
| POST | `/comparativa` | Cruza recons. `{recon_cliente, recons_rivales, nombres}` |
| POST | `/asunto` | Asunto ya redactado para las variantes A y C |
| POST | `/validar` | Revisa el contenido del informe antes de maquetarlo |
| POST | `/report` | Genera PDF y HTML desde el `content.json` del informe |
| POST | `/auditoria` | **Todo lo anterior en una llamada**: recon, rivales, comparativa, informe validado, PDF y el asunto y cuerpo del Email 1 |

`/recon` devuelve, además del recon completo, los tres campos que el Flujo 1 escribe en Airtable:
`nombre`, `barrio` y `tiempo_carga`, más un `resumen_compacto` pensado para caber en un campo de
texto largo (el recon entero pasa del límite de 100.000 caracteres).

## `/validar`: las cuatro defensas

Cada una nacio de un fallo real detectado generando informes de verdad. n8n no
puede ejecutar estos validadores por su cuenta, por eso los expone el servicio.

| Comprobacion | Que evita |
|---|---|
| **maqueta** | textos que no caben y saldrian cortados a media palabra |
| **cifras** | estadisticas de sector inventadas (*el 70% de las consultas empiezan por WhatsApp*) |
| **ejemplos** | barrios y nombres propios que no salen de la web auditada (*«piso en Gracia»*) |
| **credibilidad** | un semaforo entero en rojo, que nadie se cree |

`/auditoria` ya hace ese bucle por dentro: LLM -> validar -> si hay avisos, otra vez con el
detalle -> `/report`. Dos vueltas bastan en la practica.

Los avisos dicen **cuantos caracteres caben**, no cuantas lineas: un modelo no traduce lineas a
caracteres y con el razonamiento apagado se lo saltaba. Con el objetivo exacto («tiene 85
caracteres, acortalo a 67») las correcciones bajaron de 421 a 258 segundos por auditoria.

Los prompts estan en `prompts/`.

## Trampas del despliegue

**El `Dockerfile` copia `*.py`, no una lista escrita a mano.** Listar los modulos uno a uno
rompio el despliegue dos veces: se anade un modulo, nadie actualiza la linea, la imagen construye
bien y el contenedor muere con `ModuleNotFoundError`. Desde fuera solo se ve un 502 sin pistas.

**El servicio exige la cabecera `X-Obelum-Token`** contra la variable `SERVICE_TOKEN`. `/health`
queda libre para el monitor de Easypanel. Si `SERVICE_TOKEN` no esta puesta, no se exige nada:
asi el desarrollo en local no necesita token.

**`RAZONAMIENTO=on`** por defecto. Medido sobre el mismo lead: con razonamiento 258 s, 0,0051 $ y
cero textos cortados; sin el, 110 s, 0,0017 $ y cinco titulares cortados a media palabra. Las
auditorias se generan de noche y se acumulan, asi que el reloj no aprieta y se prefiere la calidad.

## Decisiones que conviene no deshacer

**La URL se normaliza a la raíz del dominio.** Los leads de Airtable traen URLs profundas con UTM
(`gramar.es/es/?utm_source=Google&utm_medium=maps`). Auditar eso mide una página interna y falsea
el informe entero.

**El nombre se elige comparando con el dominio.** El `<title>` de estas webs es una frase de
marketing con la marca al principio *o al final* (`Compra, venta y alquiler … · Finques Garvi`).
Quedarse con el primer trozo da basura; se elige el trozo que más se parece al dominio.

**El barrio ignora las páginas de oficina.** `oficina-barcelona-3` parece una zona pero es la
ciudad con un número de sucursal detrás. Se limpian los dígitos finales y se descarta si coincide
con la ciudad del lead.

**Los competidores se filtran por título y extracto, no solo por dominio.** Sin eso se cuelan
artículos tipo "las 10 mejores inmobiliarias de Barcelona" de revistas de negocios. Se descartan
portales, franquicias nacionales, agregadores, prensa y listicles.

**Hay caché en disco (7 días por defecto).** Los rivales de una ciudad son los mismos para todos
los leads de esa ciudad: sin caché, 300 leads de Barcelona re-auditan las mismas dos webs 300
veces. Medido: **64,7 s el primer lead, 14,9 s el segundo**. El lead se audita siempre fresco
(`cache: false`, el valor por defecto); los rivales se piden con `cache: true`.

**Logging con traceback completo desde el primer día.** En `obelum-pdf-service` la causa de los
500 intermitentes nunca se diagnosticó porque el log solo tenía la línea de acceso.

## Variables de entorno

Ver `.env.example`. La única imprescindible es `SERPER_API_KEY`; sin ella el servicio cae a
DuckDuckGo, que funciona pero da resultados peores.

| Variable | Por defecto | Para qué |
|---|---|---|
| `SERPER_API_KEY` | — | serper.dev, buscador de competidores |
| `OPENROUTER_API_KEY` | — | redaccion del informe y del email |
| `SERVICE_TOKEN` | — | cabecera `X-Obelum-Token`; vacio = sin autenticacion |
| `RAZONAMIENTO` | `on` | `on`/`off`/`low`/`medium`/`high` |
| `MODELO_LLM` | `deepseek/deepseek-v4-flash` | modelo de OpenRouter |
| `RECON_PAGINAS` | 6 | páginas internas por lead |
| `RECON_PAGINAS_RIVAL` | 4 | páginas por competidor |
| `CACHE_DIR` | `/tmp/obelum-inmo-cache` | dónde vive la caché |
| `CACHE_HORAS` | 168 | validez de la caché |
| `TIMEOUT_BUSQUEDA` | 20 | segundos por búsqueda |

## Desarrollo local

```bash
cd obelum-inmo-service
cp .env.example .env          # y rellena SERPER_API_KEY
pip install -r requirements.txt
uvicorn app:app --port 8077
```

## Despliegue

Easypanel, proyecto `prueba1`, junto a `n8n` y `obelumpdfservice`. Se llama por red interna de
Docker: `http://prueba1_obeluminmoservice:8000` (el `:80` que enseña el panel es del proxy
externo y no sirve para llamadas internas).

## Coste y tiempo por lead

Medido de punta a punta con `/auditoria`, dos rivales y razonamiento activado:

| | Con razonamiento (por defecto) | Sin razonamiento |
|---|---|---|
| Tiempo | **258 s** | 110 s |
| Coste | **0,0051 $** | 0,0017 $ |
| Textos cortados en el PDF | **0** | 5 |

Los 304 leads: unas **22 horas** de reloj y **1,6 $**. Como el flujo va por cron de madrugada y
las auditorias se acumulan, se prefiere la calidad al reloj.

El recon en si es rapido (3 s desde el servidor, 15 s con los rivales cacheados): el tiempo se lo
lleva el modelo razonando.
