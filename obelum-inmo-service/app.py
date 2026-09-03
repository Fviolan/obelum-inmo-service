"""obelum-inmo-service — motor de auditoría inmobiliaria para n8n.

Envuelve los scripts de la skill audit-inmob-web-f (skill/recon.py, compare.py,
generate.py) SIN modificarlos y los expone por HTTP:

    GET  /health          estado del servicio
    POST /recon           audita una web y devuelve datos + nombre/barrio/tiempo
    POST /competidores    busca 2-3 inmobiliarias rivales en una ciudad
    POST /comparativa     cruza el recon del cliente con los de los rivales
    POST /report          genera el PDF y el HTML del informe

Diseñado para el Flujo 1 "Audit Inmob web openrouter".
"""
from __future__ import annotations

import base64
import json
import logging
import os
import re
import sys
import time
import traceback
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlparse

import requests
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

SKILL = Path(__file__).parent / "skill"
sys.path.insert(0, str(SKILL))

import auditoria  # noqa: E402
import compare  # noqa: E402
from cifras import revisar_cifras  # noqa: E402
from credibilidad import revisar_credibilidad  # noqa: E402
from ejemplos import revisar_ejemplos  # noqa: E402
import generate  # noqa: E402
import recon  # noqa: E402

# --- logging: traceback completo desde el primer día ------------------------
# En obelum-pdf-service los 500 intermitentes nunca se diagnosticaron porque el
# log solo tenía la línea de acceso. Aquí no.
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    stream=sys.stdout,
)
log = logging.getLogger("obelum-inmo")

app = FastAPI(title="Obelum Inmo Service", version="1.0.0")

SERVICE_TOKEN = os.getenv("SERVICE_TOKEN", "").strip()
# /health queda libre para que el monitor de Easypanel siga funcionando
RUTAS_LIBRES = {"/health"}


@app.middleware("http")
async def exigir_token(request: Request, call_next):
    """El servicio esta expuesto en internet y su repo es publico: sin esto,
    cualquiera puede gastar la cuota de Serper y generar informes con el logo
    de Obelum. Si SERVICE_TOKEN no esta puesto, no se exige nada (desarrollo)."""
    if SERVICE_TOKEN and request.url.path not in RUTAS_LIBRES:
        if request.headers.get("X-Obelum-Token", "") != SERVICE_TOKEN:
            log.warning("peticion rechazada sin token: %s %s",
                        request.method, request.url.path)
            return JSONResponse(status_code=401,
                                content={"ok": False, "error": "token invalido o ausente"})
    return await call_next(request)


PAGINAS_POR_DEFECTO = int(os.getenv("RECON_PAGINAS", "6"))
PAGINAS_RIVAL = int(os.getenv("RECON_PAGINAS_RIVAL", "4"))
TIMEOUT_BUSQUEDA = int(os.getenv("TIMEOUT_BUSQUEDA", "20"))
SERPER_API_KEY = os.getenv("SERPER_API_KEY", "").strip()

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")


# --- utilidades -------------------------------------------------------------

def normalizar_url(url: str) -> str:
    """Devuelve la raíz del dominio.

    Los leads de Airtable traen URLs profundas con UTM
    ('gramar.es/es/?utm_source=Google&utm_medium=maps'). Auditar eso mide una
    página interna en vez de la home y falsea todo el informe.
    """
    url = (url or "").strip()
    if not url:
        raise ValueError("URL vacía")
    if not url.startswith(("http://", "https://")):
        url = "https://" + url
    p = urlparse(url)
    if not p.netloc:
        raise ValueError(f"URL no válida: {url}")
    return f"{p.scheme}://{p.netloc}/"


def dominio(url: str) -> str:
    return urlparse(normalizar_url(url)).netloc.replace("www.", "").lower()


def _normaliza(txt: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (txt or "").lower())


def extraer_nombre(datos: dict) -> str:
    """Nombre comercial: schema > trozo del title que casa con el dominio > dominio.

    El title de estas webs suele ser una frase de marketing con la marca pegada
    al principio O al final ("Compra, venta ... . Les Corts y Hospitalet - Finques
    Garvi"), asi que no vale con quedarse con el primer trozo: se elige el que
    mas se parece al dominio.
    """
    home = datos.get("home") or {}
    for bloque in (home.get("schema", {}).get("blocks") or []):
        tipo = bloque.get("@type")
        tipos = tipo if isinstance(tipo, list) else [tipo]
        if any(t in ("RealEstateAgent", "LocalBusiness", "Organization") for t in tipos):
            nombre = (bloque.get("name") or "").strip()
            if nombre:
                return nombre[:80]

    host = dominio(datos.get("site", {}).get("url_final")
                   or datos.get("site", {}).get("url_original") or "")
    raiz = _normaliza(host.split(".")[0])
    del_dominio = host.split(".")[0].replace("-", " ").title() if host else ""

    titulo = (home.get("seo", {}).get("title") or "").strip()
    trozos = [t.strip() for t in re.split(r"\s[|\-–—·:]\s", titulo) if t.strip()]
    trozos = [re.sub(r"^(bienvenidos?\s+a|inicio|home)\s+", "", t, flags=re.I).strip()
              for t in trozos]

    # 1) el trozo que coincide con el dominio es casi siempre la marca
    for t in trozos:
        n = _normaliza(t)
        if n and raiz and (n in raiz or raiz in n):
            return t[:80]

    # 2) si no, el trozo corto que no parezca una frase de marketing
    cortos = [t for t in trozos if len(t) <= 40 and "," not in t
              and not re.search(r"(compra|venta|alquiler|vende|encuentra|somos)", t, re.I)]
    if cortos:
        return min(cortos, key=len)[:80]

    return del_dominio


ZONA_RE = re.compile(r"/(?:zona|zonas|barrio|barrios|oficina|oficinas|delegacion|"
                     r"inmobiliaria-en|pisos-en|viviendas-en)[/-]([a-z0-9\-]{3,40})",
                     re.I)


def extraer_barrio(datos: dict, ciudad_lead: str = "") -> tuple[str, bool]:
    """Barrio o zona principal. Si no se detecta uno creible, la ciudad del lead.

    Ojo con las paginas de oficina ("oficina-barcelona-3"): dan un slug que
    parece zona pero es la ciudad con un numero de sucursal detras.
    """
    ciudad = (ciudad_lead or "").strip()
    urls = list((datos.get("home") or {}).get("links", {}).get("internal") or [])
    for sm in (datos.get("site") or {}).get("sitemaps", []):
        urls.extend(sm.get("sample") or [])

    for u in urls:
        m = ZONA_RE.search(urlparse(u).path)
        if not m:
            continue
        crudo = m.group(1)
        crudo = re.sub(r"\.(htm|html|php|aspx?)$", "", crudo, flags=re.I)
        crudo = re.sub(r"[-_]?\d+$", "", crudo)          # sucursal: "-3"
        limpio = crudo.replace("-", " ").replace("_", " ").strip().title()
        if not limpio or len(limpio) < 3:
            continue
        if ciudad and _normaliza(limpio) == _normaliza(ciudad):
            continue                                      # es la ciudad, no un barrio
        return limpio[:60], True                          # barrio de verdad

    return ciudad[:60], False                             # respaldo: solo la ciudad


def tiempo_carga_seg(datos: dict) -> float | None:
    ms = (datos.get("resumen") or {}).get("tiempo_medio_ms")
    return round(ms / 1000.0, 1) if isinstance(ms, (int, float)) else None


def resumen_compacto(datos: dict) -> dict:
    """Extracto para Airtable. El recon entero pasa de 100.000 caracteres,
    que es el límite de un campo de texto largo."""
    r = datos.get("resumen") or {}
    s = datos.get("site") or {}
    home = datos.get("home") or {}
    return {
        "paginas": r.get("paginas_analizadas"),
        "cms_crm": r.get("cms_y_crm"),
        "analytics": r.get("analytics"),
        "chat": r.get("chat"),
        "schema": r.get("schema_types"),
        "captacion": r.get("hay_formulario_captacion"),
        "paginas_captacion": (r.get("paginas_captacion") or [])[:3],
        # tres senales, igual que con WhatsApp: formulario de busqueda, URLs de
        # listado o ficha en la portada, o rastros de buscador en el JS. Solo con
        # las tres a cero se puede afirmar que no tienen buscador propio.
        "buscador": bool(r.get("hay_buscador"))
                    or bool(r.get("listados_en_home"))
                    or bool(r.get("fichas_en_home"))
                    or (r.get("senales_buscador") or 0) > 0,
        "buscador_formulario": bool(r.get("hay_buscador")),
        "buscador_senales_js": r.get("senales_buscador") or 0,
        "fichas_home": r.get("fichas_en_home"),
        "listados_home": r.get("listados_en_home"),
        # tres senales: enlace directo, widget detectado o la palabra en el HTML.
        # Solo con las tres a cero se puede afirmar que no tienen WhatsApp.
        "whatsapp": bool(r.get("whatsapp"))
                    or any("whatsapp" in c.lower() for c in (r.get("chat") or []))
                    or (r.get("menciones_whatsapp") or 0) > 0,
        "whatsapp_enlace": bool(r.get("whatsapp")),
        "whatsapp_menciones": r.get("menciones_whatsapp") or 0,
        "telefonos": (r.get("telefonos") or [])[:3],
        "redes": list((r.get("redes") or {}).keys()),
        "portales": r.get("portales"),
        "ssl_ok": s.get("certificado_ssl_valido"),
        "hsts": (s.get("transporte") or {}).get("hsts"),
        "servidor": (s.get("servidor") or {}).get("server"),
        "robots": (s.get("robots_txt") or {}).get("exists"),
        "sitemap_urls": s.get("sitemap_total_urls"),
        "error_404": s.get("error_404"),
        "tiempo_medio_ms": r.get("tiempo_medio_ms"),
        "peso_imagenes_kb": (s.get("peso_imagenes_home") or {}).get("peso_total_kb"),
        # peso de la portada tal y como lo descarga un movil: HTML + galeria
        "peso_home_kb": round((home.get("html_bytes") or 0) / 1024
                              + ((s.get("peso_imagenes_home") or {}).get("peso_total_kb") or 0), 1),
        "imagenes_pesadas": (s.get("peso_imagenes_home") or {}).get("imagenes_de_mas_de_200kb"),
        "img_sin_alt": f"{r.get('imagenes_sin_alt_total')}/{r.get('imagenes_total')}",
        "sin_meta_desc": len(r.get("paginas_sin_meta_description") or []),
        "titulos_duplicados": r.get("titulos_duplicados"),
        "idiomas": r.get("idiomas_en_urls"),
        "legales": bool(r.get("hay_legal")),
        "colores_marca": (s.get("colores_marca") or [])[:3],
        "h1_home": (home.get("headings") or {}).get("h1"),
    }


# --- búsqueda de competidores ----------------------------------------------

PORTALES_Y_FRANQUICIAS = {
    # portales: compiten, pero el cliente no puede ganarles
    "idealista.com", "fotocasa.es", "habitaclia.com", "pisos.com", "yaencontre.com",
    "kyero.com", "thinkspain.com", "rightmove.co.uk", "milanuncios.com",
    "spainhouses.net", "properstar.es", "indomio.es", "tucasa.com",
    "realadvisor.es", "housell.com", "clikalia.com", "vivla.com",
    # franquicias nacionales: otra liga de presupuesto
    "tecnocasa.es", "donpiso.com", "engelvoelkers.com", "century21.es",
    "remax.es", "lacasa.net", "housfy.com", "comprarcasa.com", "look-look.es",
    "redpiso.es", "gilmar.es", "solvia.es", "haya.es", "aliseda.es",
    # directorios y redes: no son competencia
    "google.com", "google.es", "facebook.com", "instagram.com", "linkedin.com",
    "youtube.com", "twitter.com", "x.com", "tiktok.com", "wikipedia.org",
    "paginasamarillas.es", "yelp.es", "tripadvisor.es", "maps.google.com",
    "empresia.es", "einforma.com", "axesor.es", "infoempresa.com",
    # prensa y blogs: aparecen con articulos tipo "las mejores inmobiliarias de X"
    "tiempodenegocios.com", "emprendedores.es", "expansion.com", "elpais.com",
    "lavanguardia.com", "elmundo.es", "abc.es", "20minutos.es", "eleconomista.es",
    "idealista.pro", "brainsre.news", "ejeprime.com", "observatorioinmobiliario.es",
}

# senales de que el resultado es una agencia y no un articulo sobre agencias
SENAL_INMO = re.compile(r"(inmobiliari|finques|fincas|inmuebles|api|real\s*estate|"
                        r"pisos|viviendas|alquiler|administracion\s+de\s+fincas)", re.I)
# titulares de listicle: "las 10 mejores inmobiliarias de Barcelona"
LISTICLE = re.compile(r"(mejores|top\s*\d|ranking|listado\s+de|las\s+\d+|guia\s+de)", re.I)


def es_candidato(host: str, excluido: str, titulo: str = "", extracto: str = "") -> bool:
    host = host.replace("www.", "").lower()
    if not host or host == excluido:
        return False
    if any(host == m or host.endswith("." + m) for m in PORTALES_Y_FRANQUICIAS):
        return False
    if re.search(r"(blog|revista|noticias|magazine|prensa|diario)", host, re.I):
        return False

    contexto = f"{titulo} {extracto}"
    if contexto.strip():
        # un articulo sobre inmobiliarias no es una inmobiliaria
        if LISTICLE.search(titulo):
            return False
        # tiene que oler a agencia: o el dominio o el resultado lo dicen
        if not (SENAL_INMO.search(contexto) or SENAL_INMO.search(host)):
            return False
    return True


def buscar_serper(consulta: str) -> list[str]:
    r = requests.post(
        "https://google.serper.dev/search",
        headers={"X-API-KEY": SERPER_API_KEY, "Content-Type": "application/json"},
        json={"q": consulta, "gl": "es", "hl": "es", "num": 20},
        timeout=TIMEOUT_BUSQUEDA,
    )
    r.raise_for_status()
    return [{"url": x.get("link", ""), "titulo": x.get("title", ""),
             "extracto": x.get("snippet", "")}
            for x in (r.json().get("organic") or [])]


def buscar_duckduckgo(consulta: str) -> list[str]:
    r = requests.post(
        "https://html.duckduckgo.com/html/",
        data={"q": consulta, "kl": "es-es"},
        headers={"User-Agent": UA, "Content-Type": "application/x-www-form-urlencoded"},
        timeout=TIMEOUT_BUSQUEDA,
    )
    r.raise_for_status()
    enlaces = []
    for m in re.finditer(r'href="(https?://[^"]+)"', r.text):
        u = m.group(1)
        # DDG envuelve los resultados en un redirector
        if "duckduckgo.com/l/?uddg=" in u:
            from urllib.parse import parse_qs, unquote
            q = parse_qs(urlparse(u).query).get("uddg")
            if q:
                enlaces.append({"url": unquote(q[0]), "titulo": "", "extracto": ""})
        elif "duckduckgo.com" not in u:
            enlaces.append({"url": u, "titulo": "", "extracto": ""})
    return enlaces


def buscar(consulta: str) -> tuple[list[dict], str]:
    """Devuelve (enlaces, proveedor usado)."""
    if SERPER_API_KEY:
        try:
            return buscar_serper(consulta), "serper"
        except Exception as exc:
            log.warning("Serper falló (%s), caigo a DuckDuckGo", exc)
    return buscar_duckduckgo(consulta), "duckduckgo"


# --- hallazgo principal y variante de asunto --------------------------------
# Las variantes A y C dependen de un dato duro y se deciden por regla; entre B,
# D y E elige el LLM, que es donde el matiz de tono importa. Asi dos ejecuciones
# del mismo lead dan siempre la misma variante y el A/B se puede leer.

UMBRAL_CARGA = float(os.getenv("UMBRAL_CARGA", "2.5"))      # segundos
UMBRAL_PESO_KB = float(os.getenv("UMBRAL_PESO_KB", "3072"))   # 3 MB de portada

# los nombres coinciden EXACTAMENTE con las opciones del campo "Tipo Hallazgo"
# de Airtable; si cambian alli, hay que cambiarlos aqui
CAT_VELOCIDAD = "Velocidad"
CAT_WHATSAPP = "Sin WhatsApp"
CAT_CAPTACION = "Sin captacion propietarios"
CAT_SCHEMA = "Sin datos estructurados"
CAT_SEO = "Sin SEO local"
CAT_SSL = "Seguridad / SSL"
CAT_OTRO = "Otro"


def paginas_zona(datos: dict) -> int:
    """Reutiliza la heuristica de compare.py para no tener dos versiones."""
    try:
        return compare.paginas_zona(datos)
    except Exception:
        return 0


def analizar_hallazgos(datos: dict, barrio: str, tiempo: float | None) -> dict:
    """Ordena los hallazgos por gravedad y decide la variante cuando toca."""
    rc = resumen_compacto(datos)
    candidatos = []

    def anota(cat, gravedad, frase):
        candidatos.append({"tipo": cat, "gravedad": gravedad, "hallazgo": frase})

    if rc.get("ssl_ok") is False:
        anota(CAT_SSL, 100,
              "El certificado no valida: parte de los visitantes ven aviso de sitio no seguro")

    # Dos formas de ir lenta, y se elige la peor: hay webs que responden rapido
    # pero descargan 6 MB de fotos, y con datos moviles eso duele igual.
    peso = rc.get("peso_home_kb") or 0
    exceso_tiempo = (tiempo / UMBRAL_CARGA) if tiempo else 0
    exceso_peso = (peso / UMBRAL_PESO_KB) if peso else 0
    metrica_a = None

    if exceso_tiempo >= exceso_peso and exceso_tiempo > 1:
        metrica_a = {"metrica": "tiempo", "valor": tiempo, "unidad": "s",
                     "umbral": UMBRAL_CARGA, "exceso": round(exceso_tiempo, 2)}
        anota(CAT_VELOCIDAD, 90,
              f"La web tarda {tiempo} segundos en responder de media")
    elif exceso_peso > 1:
        mb = round(peso / 1024, 1)
        metrica_a = {"metrica": "peso", "valor": mb, "unidad": "MB",
                     "umbral": round(UMBRAL_PESO_KB / 1024, 1),
                     "exceso": round(exceso_peso, 2)}
        anota(CAT_VELOCIDAD, 90,
              f"La portada descarga {mb} MB: en móvil con datos es una eternidad")


    if not rc.get("whatsapp"):
        anota(CAT_WHATSAPP, 85,
              "No hay botón de WhatsApp en la web")
    elif not rc.get("whatsapp_enlace") and rc.get("whatsapp_menciones", 0) < 3:
        # rastro debil: puede ser un widget o una mencion suelta. No se afirma
        # nada en el asunto, pero queda anotado para el informe.
        anota(CAT_OTRO, 20,
              "El acceso a WhatsApp no está claro desde la home")

    if not rc.get("captacion"):
        anota(CAT_CAPTACION, 80,
              "No hay página ni formulario para captar propietarios que quieren vender")

    esquemas = rc.get("schema") or []
    if not any(t in esquemas for t in ("RealEstateAgent", "LocalBusiness", "Organization")):
        anota(CAT_SCHEMA, 60,
              "Google no tiene datos estructurados: no sabe que esto es una inmobiliaria")

    if not rc.get("sitemap_urls"):
        anota(CAT_SEO, 55,
              "No hay sitemap: los inmuebles nuevos tardan semanas en indexarse")

    if not rc.get("buscador"):
        anota(CAT_OTRO, 50,
              "No hay buscador de inmuebles propio: quien busca piso acaba en los portales")
    elif not rc.get("buscador_formulario") and rc.get("buscador_senales_js", 0) < 3:
        # hay algun rastro pero no un buscador claro: se anota sin afirmar nada
        anota(CAT_OTRO, 25,
              "El buscador de inmuebles no se encuentra facilmente desde la portada")

    if not paginas_zona(datos):
        anota(CAT_SEO, 45,
              "Ni una pagina por zona: no aparece en las busquedas de barrio")

    sin_gancho = not candidatos
    if sin_gancho:
        # web sana: no hay gancho duro. El flujo puede saltarsela o el LLM
        # tirar de la comparativa. Lo que NO se hace es inventarse un problema.
        anota(CAT_OTRO, 10,
              "Sin fallos graves: el angulo tiene que salir de la comparativa")

    candidatos.sort(key=lambda c: -c["gravedad"])

    # --- reglas duras
    hay_velocidad = metrica_a is not None
    hay_whatsapp = any(c["tipo"] == CAT_WHATSAPP for c in candidatos)

    if hay_velocidad and barrio:
        # la A nombra el barrio: sin barrio detectado el asunto queda cojo
        tipo_e1, decidido, cat = "A", "regla", CAT_VELOCIDAD
    elif hay_whatsapp:
        tipo_e1, decidido, cat = "C", "regla", CAT_WHATSAPP
    else:
        tipo_e1, decidido, cat = None, "llm", candidatos[0]["tipo"]

    principal = next(c for c in candidatos if c["tipo"] == cat)

    return {
        "tipo_e1": tipo_e1,
        "decidido_por": decidido,
        "variantes_permitidas": [tipo_e1] if tipo_e1 else ["B", "D", "E"],
        "tipo_hallazgo": cat,
        "hallazgo_principal": principal["hallazgo"],
        "candidatos": candidatos[:5],
        "sin_gancho_claro": sin_gancho,
        "metrica_a": metrica_a,          # que dato justifica la variante A
        "umbral_carga": UMBRAL_CARGA,
        "umbral_peso_kb": UMBRAL_PESO_KB,
    }


# --- cache -----------------------------------------------------------------
# Los rivales de una ciudad son los mismos para TODOS los leads de esa ciudad.
# Sin cache, 300 leads de Barcelona re-auditan las mismas 2 webs 300 veces:
# 72s por lead en vez de ~10s.

CACHE_DIR = Path(os.getenv("CACHE_DIR", "/tmp/obelum-inmo-cache"))
CACHE_HORAS = int(os.getenv("CACHE_HORAS", "168"))          # 7 dias


def _ruta_cache(clave: str) -> Path:
    seguro = re.sub(r"[^a-z0-9_.-]", "_", clave.lower())[:120]
    return CACHE_DIR / f"{seguro}.json"


def cache_leer(clave: str, horas: int = CACHE_HORAS):
    ruta = _ruta_cache(clave)
    try:
        if not ruta.exists():
            return None
        if (time.time() - ruta.stat().st_mtime) > horas * 3600:
            return None
        return json.loads(ruta.read_text(encoding="utf-8"))
    except Exception as exc:
        log.warning("cache ilegible %s: %s", clave, exc)
        return None


def cache_escribir(clave: str, valor) -> None:
    try:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        _ruta_cache(clave).write_text(json.dumps(valor, ensure_ascii=False),
                                      encoding="utf-8")
    except Exception as exc:
        log.warning("no se pudo cachear %s: %s", clave, exc)


# --- modelos de entrada -----------------------------------------------------

class ReconIn(BaseModel):
    url: str
    ciudad: str = ""
    paginas: int = Field(default=0, ge=0, le=20)
    # el lead se audita siempre fresco; los rivales, desde cache
    cache: bool = False


class CompetidoresIn(BaseModel):
    ciudad: str
    dominio_excluido: str = ""
    maximo: int = Field(default=3, ge=1, le=3)


class ComparativaIn(BaseModel):
    recon_cliente: dict
    recons_rivales: list[dict]
    nombres: list[str] = []


# --- endpoints --------------------------------------------------------------

@app.get("/health")
def health():
    return {
        "status": "ok",
        "servicio": "obelum-inmo-service",
        "buscador": "serper" if SERPER_API_KEY else "duckduckgo",
        "paginas_por_defecto": PAGINAS_POR_DEFECTO,
        "cache_dir": str(CACHE_DIR),
        "cache_horas": CACHE_HORAS,
        "cache_entradas": len(list(CACHE_DIR.glob("*.json"))) if CACHE_DIR.exists() else 0,
    }


@app.post("/recon")
def endpoint_recon(entrada: ReconIn):
    t0 = time.time()
    url = normalizar_url(entrada.url)
    paginas = entrada.paginas or PAGINAS_POR_DEFECTO
    clave = f"recon_{dominio(url)}_{paginas}"

    if entrada.cache:
        guardado = cache_leer(clave)
        if guardado:
            guardado["desde_cache"] = True
            log.info("recon %s servido de cache", url)
            return guardado

    log.info("recon %s (paginas=%s)", url, paginas)
    datos = recon.run(url, paginas, quiet=True)
    if "error" in datos:
        log.warning("recon fallido %s: %s", url, datos["error"])
        # 422 y no 502: el proxy de Easypanel intercepta los 502 y los sustituye
        # por su propia pagina HTML, asi que el motivo real del fallo no llegaria
        # a n8n y en Error Auditoria se guardaria basura en vez de la causa.
        return JSONResponse(status_code=422, content={
            "ok": False, "url": url, "error": datos["error"],
        })

    barrio_txt, barrio_real = extraer_barrio(datos, entrada.ciudad)
    salida = {
        "ok": True,
        "url_normalizada": url,
        "url_original": entrada.url,
        "nombre": extraer_nombre(datos),
        "barrio": barrio_txt,
        "barrio_es_real": barrio_real,
        "tiempo_carga": tiempo_carga_seg(datos),
        "colores_marca": (datos.get("site") or {}).get("colores_marca", []),
        "resumen_compacto": resumen_compacto(datos),
        "recon": datos,
        "segundos": round(time.time() - t0, 1),
    }
    salida["hallazgo"] = analizar_hallazgos(datos, barrio_txt,
                                            salida["tiempo_carga"])
    salida["desde_cache"] = False
    if entrada.cache:
        cache_escribir(clave, salida)
    log.info("recon ok %s -> %s (%.1fs)", url, salida["nombre"], salida["segundos"])
    return salida


@app.post("/competidores")
def endpoint_competidores(entrada: CompetidoresIn):
    ciudad = entrada.ciudad.strip()
    if not ciudad:
        return JSONResponse(status_code=400, content={"ok": False,
                                                      "error": "falta la ciudad"})
    excluido = dominio(entrada.dominio_excluido) if entrada.dominio_excluido else ""

    clave = f"competidores_{ciudad}_{excluido}_{entrada.maximo}"
    guardado = cache_leer(clave)
    if guardado:
        guardado["desde_cache"] = True
        log.info("competidores %s servidos de cache", ciudad)
        return guardado

    consultas = [
        f"inmobiliaria en {ciudad}",
        f"vender piso {ciudad}",
        f"agencia inmobiliaria {ciudad}",
    ]
    vistos, elegidos, proveedor = set(), [], ""
    for consulta in consultas:
        if len(elegidos) >= entrada.maximo:
            break
        try:
            enlaces, proveedor = buscar(consulta)
        except Exception as exc:
            log.warning("busqueda '%s' fallo: %s", consulta, exc)
            continue
        for item in enlaces:
            host = urlparse(item["url"]).netloc.replace("www.", "").lower()
            if host in vistos:
                continue
            vistos.add(host)
            if es_candidato(host, excluido, item["titulo"], item["extracto"]):
                elegidos.append({"dominio": host, "url": f"https://{host}/",
                                 "titulo": item["titulo"][:120],
                                 "encontrado_en": consulta})
                if len(elegidos) >= entrada.maximo:
                    break

    log.info("competidores %s -> %s (%s)", ciudad,
             [c["dominio"] for c in elegidos], proveedor)
    salida = {"ok": True, "ciudad": ciudad, "proveedor": proveedor,
              "descartados": len(vistos) - len(elegidos),
              "competidores": elegidos, "desde_cache": False}
    if elegidos:
        cache_escribir(clave, salida)
    return salida


@app.post("/comparativa")
def endpoint_comparativa(entrada: ComparativaIn):
    recons = [entrada.recon_cliente] + list(entrada.recons_rivales)
    nombres = list(entrada.nombres)
    while len(nombres) < len(recons):
        d = recons[len(nombres)]
        nombres.append(compare.nombre_por_defecto(d))
    datos = compare.construir(recons, nombres[:len(recons)])
    log.info("comparativa: pierde en %s", datos.get("_pierde_en"))
    return {"ok": True, "competencia": datos}


# caracteres que Windows, Airtable y los clientes de correo no admiten
# en un nombre de archivo
CARACTERES_INVALIDOS = set(chr(92) + '/:*?"<>|') | {chr(13), chr(10), chr(9)}


def nombre_archivo(contenido: dict) -> str:
    """<Nombre de la inmobiliaria>_auditoria — es el adjunto que ve el cliente,
    asi que lleva su nombre, no un slug interno."""
    crudo = (contenido.get("cliente") or contenido.get("slug") or "Auditoria").strip()
    limpio = "".join(c for c in crudo if c not in CARACTERES_INVALIDOS)
    limpio = re.sub(r"\s+", " ", limpio).strip(" .")[:60]
    return f"{limpio}_auditoria" if limpio else "Auditoria"


@app.post("/report")
async def endpoint_report(request: Request):
    contenido = await request.json()
    avisos = generate.validar(contenido)
    slug = contenido.get("slug", "informe")
    base = nombre_archivo(contenido)

    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        ruta_html = Path(tmp) / f"{base}.html"
        ruta_pdf = Path(tmp) / f"{base}.pdf"
        generate.create_html(contenido, ruta_html)
        generate.create_pdf(contenido, ruta_pdf)
        pdf = ruta_pdf.read_bytes()
        html = ruta_html.read_bytes()

    log.info("informe %s: pdf %s KB, avisos=%s", slug, round(len(pdf) / 1024), avisos)
    return {
        "ok": True,
        "nombre_pdf": f"{base}.pdf",
        "nombre_html": f"{base}.html",
        "pdf_base64": base64.b64encode(pdf).decode("ascii"),
        "html_base64": base64.b64encode(html).decode("ascii"),
        "avisos": avisos,
    }



class AsuntoIn(BaseModel):
    nombre: str
    hallazgo: dict
    resumen_compacto: dict = {}
    pesos_rivales_mb: list[float] = []


def _mb(txt: float) -> str:
    """8.57 -> '8,6' (coma decimal, como se escribe en espanol)."""
    return f"{round(txt, 1)}".replace(".", ",")


@app.post("/asunto")
def endpoint_asunto(entrada: AsuntoIn):
    """Redacta el asunto de las variantes decididas por regla (A y C).

    B, D y E las escribe el LLM: ahi el matiz de tono importa y no hay un dato
    duro que dicte la frase.
    """
    h = entrada.hallazgo or {}
    tipo = h.get("tipo_e1")

    if tipo == "A":
        metrica = h.get("metrica_a") or {}
        if metrica.get("metrica") == "peso":
            mb = metrica.get("valor") or 0
            rivales = [x for x in entrada.pesos_rivales_mb if x and x > 0]
            if rivales:
                media = sum(rivales) / len(rivales)
                veces = int(mb / media) if media else 0
                # por debajo de 3x la comparacion no impresiona y suena forzada
                if veces >= 3:
                    return {"ok": True, "tipo_e1": "A", "origen": "regla",
                            "asunto": f"Tu portada pesa {_mb(mb)} MB, {veces} veces "
                                      f"más que la de tu competencia",
                            "dato": {"mb": mb, "veces": veces,
                                     "media_rivales_mb": round(media, 2)}}
            return {"ok": True, "tipo_e1": "A", "origen": "regla",
                    "asunto": f"Tu portada pesa {_mb(mb)} MB en el móvil",
                    "dato": {"mb": mb}}

        segundos = str(metrica.get("valor", "")).replace(".", ",")
        return {"ok": True, "tipo_e1": "A", "origen": "regla",
                "asunto": f"Tu web tarda {segundos} segundos en responder",
                "dato": {"segundos": metrica.get("valor")}}

    if tipo == "C":
        return {"ok": True, "tipo_e1": "C", "origen": "regla",
                "asunto": "Tu web no tiene botón de WhatsApp",
                "dato": {}}

    return {"ok": True, "tipo_e1": None, "origen": "llm",
            "variantes_permitidas": h.get("variantes_permitidas", ["B", "D", "E"]),
            "asunto": None,
            "nota": "Lo redacta el LLM: elige entre B, D y E segun el tono."}



class ValidarIn(BaseModel):
    contenido: dict                 # lo que devolvio el LLM
    permitido: dict = {}            # lo que se le paso: datos medidos + comparativa


@app.post("/validar")
def endpoint_validar(entrada: ValidarIn):
    """Revisa el contenido del informe antes de maquetarlo.

    Cuatro comprobaciones, cada una nacida de un fallo real:
      maqueta      - textos que no caben y saldrian cortados a media palabra
      cifras       - numeros que no estan en los datos medidos (estadisticas
                     de sector inventadas)
      ejemplos     - barrios y nombres propios que no salen de la web auditada
      credibilidad - un semaforo entero en rojo no se lo cree nadie

    Devuelve los avisos agrupados y una lista plana lista para devolversela al
    LLM en la peticion de correccion.
    """
    contenido = entrada.contenido or {}
    permitido = entrada.permitido or {}

    prueba = dict(contenido)
    if "competencia" not in prueba:
        prueba["competencia"] = {"titular": contenido.get("titular_competencia", "")}

    grupos = {
        "maqueta": generate.revisar_ajuste(prueba),
        "cifras": revisar_cifras(contenido, permitido),
        "ejemplos": revisar_ejemplos(contenido, permitido),
        "credibilidad": revisar_credibilidad(contenido, permitido),
    }
    todos = [a for lista in grupos.values() for a in lista]
    recuentos = generate.validar(prueba)

    log.info("validar: %s avisos (%s)", len(todos),
             {k: len(v) for k, v in grupos.items()})
    return {
        "ok": not todos,
        "avisos": todos,
        "por_tipo": grupos,
        "total": len(todos),
        "recuento_campos": [a for a in recuentos if a not in todos],
    }



class AuditoriaIn(BaseModel):
    url: str
    ciudad: str = ""
    rivales: int = Field(default=2, ge=0, le=3)


@app.post("/auditoria")
def endpoint_auditoria(entrada: AuditoriaIn):
    """Auditoria completa de un lead en una sola llamada.

    Encadena todo lo que el Flujo 1 necesita: mide la web, busca y audita a sus
    rivales, cruza la comparativa, redacta el informe, lo valida y lo corrige,
    maqueta el PDF y escribe el asunto y el cuerpo del Email 1.

    Se hace aqui y no en el canvas de n8n porque toda esta logica ya estaba
    escrita y probada en Python: replicarla en veinte nodos seria reescribirla
    en JavaScript y volver a estrenar sus fallos.
    """
    t0 = time.time()
    gasto = auditoria.GastoLLM()
    ciudad = (entrada.ciudad or "").strip()

    # 1) el lead, siempre fresco
    cli = endpoint_recon(ReconIn(url=entrada.url, ciudad=ciudad, cache=False))
    if isinstance(cli, JSONResponse):
        return cli

    # 2) los rivales de su ciudad, desde cache: son los mismos para todos los
    #    leads de esa ciudad y sin cache cada lead tardaria 65 s en vez de 15
    recons_rivales, nombres_rivales, pesos_rivales = [], [], []
    if entrada.rivales and ciudad:
        try:
            comp = endpoint_competidores(CompetidoresIn(
                ciudad=ciudad, dominio_excluido=entrada.url,
                maximo=entrada.rivales))
            for c in (comp.get("competidores") or []):
                d = endpoint_recon(ReconIn(url=c["url"], ciudad=ciudad,
                                           paginas=PAGINAS_RIVAL, cache=True))
                if not isinstance(d, JSONResponse):
                    recons_rivales.append(d["recon"])
                    nombres_rivales.append(d["nombre"])
                    # el peso de la portada de cada rival sostiene el asunto de
                    # la variante A: «pesa X MB, N veces mas que tu competencia»
                    pesos_rivales.append(round(
                        (d["resumen_compacto"].get("peso_home_kb") or 0) / 1024, 2))
        except Exception as exc:
            # sin rivales el informe sale de 3 paginas en vez de 4: se puede
            # entregar igual, asi que un fallo aqui no tumba el lead
            log.warning("competidores fallaron (%s): informe sin comparativa",
                        type(exc).__name__)

    comparativa = {}
    if recons_rivales:
        comparativa = endpoint_comparativa(ComparativaIn(
            recon_cliente=cli["recon"], recons_rivales=recons_rivales,
            nombres=[cli["nombre"]] + nombres_rivales))["competencia"]

    # 3) el informe: lo que el modelo puede citar y nada mas
    h = cli["hallazgo"]
    permitido = {
        "web": cli["url_normalizada"], "nombre": cli["nombre"], "ciudad": ciudad,
        "hallazgo_principal": h["hallazgo_principal"],
        # sin el campo gravedad: es una puntuacion interna y el modelo la citaba
        # literalmente en el informe del cliente («gravedad 85»)
        "todos_los_hallazgos": [c["hallazgo"] for c in h["candidatos"]],
        "datos_medidos": cli["resumen_compacto"],
    }
    if comparativa:
        permitido["comparativa_con_rivales"] = {
            "columnas": comparativa["columnas"], "filas": comparativa["filas"],
            "pierde_en": comparativa["_pierde_en"],
            "gana_en": comparativa["_gana_en"]}

    def validar(contenido):
        prueba = dict(contenido)
        prueba["competencia"] = {"titular": contenido.get("titular_competencia", "")}
        return endpoint_validar(ValidarIn(contenido=prueba,
                                          permitido=permitido))["avisos"]

    contenido, avisos = auditoria.generar_informe(permitido, gasto, validar)

    # 4) el PDF
    comp_final = dict(comparativa)
    comp_final.pop("_nota_titular", None)
    if comp_final:
        comp_final["titular"] = contenido.pop("titular_competencia", "")
    else:
        contenido.pop("titular_competencia", None)

    ficha = {
        "slug": re.sub(r"[^a-z0-9]+", "-", (cli["nombre"] or "lead").lower()).strip("-")[:40],
        "url": cli["url_normalizada"], "cliente": cli["nombre"], "ciudad": ciudad,
        "sector": "Inmobiliaria", "fecha": time.strftime("%Y-%m-%d"),
        "marca": {"acento": (cli.get("colores_marca") or ["#0f4c81"])[0],
                  "acento2": "#0b3358"},
        "note": (f"Datos obtenidos por analisis automatizado de "
                 f"{cli['resumen_compacto'].get('paginas')} paginas del sitio. "
                 f"Sin acceso a Analytics ni Search Console del cliente."),
        **contenido,
    }
    if comp_final:
        ficha["competencia"] = comp_final

    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        base = nombre_archivo(ficha)
        ruta_pdf = Path(tmp) / f"{base}.pdf"
        generate.create_pdf(ficha, ruta_pdf)
        pdf = ruta_pdf.read_bytes()

    # 5) el email: asunto por regla si toca, si no por rotacion
    reglado = endpoint_asunto(AsuntoIn(
        nombre=cli["nombre"], hallazgo=h,
        resumen_compacto=cli["resumen_compacto"],
        pesos_rivales_mb=pesos_rivales))
    variante = reglado.get("tipo_e1") or auditoria.variante_rotada(cli["url_normalizada"])
    email = auditoria.generar_email(cli, comparativa, reglado.get("asunto"),
                                    variante, gasto)

    salida = {
        "ok": True,
        "nombre": cli["nombre"],
        "barrio": cli["barrio"],
        "barrio_es_real": cli["barrio_es_real"],
        "tiempo_carga": cli["tiempo_carga"],
        "hallazgo_principal": h["hallazgo_principal"],
        "tipo_hallazgo": h["tipo_hallazgo"],
        "tipo_e1": email["tipo_e1"],
        "asunto": email["asunto"],
        "cuerpo": email["cuerpo"],
        "resumen_recon": json.dumps(cli["resumen_compacto"], ensure_ascii=False)[:95000],
        "nombre_pdf": f"{nombre_archivo(ficha)}.pdf",
        "pdf_base64": base64.b64encode(pdf).decode("ascii"),
        "avisos": avisos,
        "rivales": nombres_rivales,
        "gasto_llm": gasto.resumen(),
        "segundos": round(time.time() - t0, 1),
    }
    log.info("auditoria %s -> %s, E1=%s, %s KB, %ss, $%s",
             cli["url_normalizada"], cli["nombre"], email["tipo_e1"],
             round(len(pdf) / 1024), salida["segundos"], gasto.dolares)
    return salida


# --- manejador global: nada se pierde sin traceback -------------------------

@app.exception_handler(Exception)
async def excepcion_no_controlada(request: Request, exc: Exception):
    log.error("EXCEPCION en %s %s\n%s", request.method, request.url.path,
              traceback.format_exc())
    return JSONResponse(status_code=500, content={
        "ok": False,
        "error": f"{type(exc).__name__}: {exc}",
        "ruta": request.url.path,
    })
