#!/usr/bin/env python3
"""Recon de una web inmobiliaria: extrae señales reales de SEO, marketing y UX.

Uso:
    python recon.py https://inmobiliaria.com --out recon.json [--pages 8] [--quiet]

No juzga: solo recoge datos. La valoración la hace el agente leyendo el JSON.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from collections import Counter
from datetime import datetime, timezone
from urllib.parse import urljoin, urlparse

import requests
import urllib3
from bs4 import BeautifulSoup

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")
HEADERS = {"User-Agent": UA, "Accept-Language": "es-ES,es;q=0.9"}
TIMEOUT = 20

# --- vocabulario del sector -------------------------------------------------

CAPTACION_RE = re.compile(
    r"(valoraci[oó]n\s+(gratuita|gratis|online|de\s+tu)|tasaci[oó]n\s+(gratuita|gratis|online)|"
    r"cu[aá]nto\s+vale\s+(tu|mi)|vende(r)?\s+(tu|su|mi)\s+(casa|piso|vivienda|propiedad|inmueble)|"
    r"quiero\s+vender|vender\s+con\s+nosotros|valora\s+(tu|su)\s+(casa|piso|vivienda|inmueble))",
    re.I)
ALQUILER_RE = re.compile(r"\balquil(er|ar|o)\b", re.I)
VENTA_RE = re.compile(r"\b(en\s+venta|comprar|compra)\b", re.I)
FICHA_URL_RE = re.compile(
    r"/(propiedad|propiedades|inmueble|inmuebles|vivienda|viviendas|piso|pisos|casa|casas|"
    r"chalet|obra-nueva|ref|referencia|listing|property|properties)(?=[/?#\-=]|$)", re.I)
# un listado lleva paginación o filtros; una ficha individual lleva id/referencia
LISTADO_RE = re.compile(r"[?&](pagina|page|accion|operacion|zona|tipo|precio|orden)=", re.I)
FICHA_ID_RE = re.compile(r"/(\d{3,}|[a-z0-9]+-\d{3,}|ref[-_]?\w+)(?=[/?#]|$)", re.I)
IDIOMA_RE = re.compile(r"/(es|ca|en|fr|de|nl|ru|it|pt|sv|no|pl)(?=[/?#]|$)", re.I)
LEGAL_URL_RE = re.compile(r"(aviso[-_]?legal|privacidad|privacy|cookies|proteccion[-_]?de[-_]?datos|"
                          r"terminos|condiciones|legal)", re.I)
BLOG_URL_RE = re.compile(r"/(blog|noticias|actualidad|articulos|art[ií]culos|consejos)(/|$)", re.I)
LEGAL_RE = re.compile(r"(aviso\s+legal|pol[ií]tica\s+de\s+privacidad|protecci[oó]n\s+de\s+datos|"
                      r"pol[ií]tica\s+de\s+cookies)", re.I)
PHONE_RE = re.compile(r"(?:\+34[\s.-]?)?(?:[6789]\d{2})[\s.-]?\d{2}[\s.-]?\d{2}[\s.-]?\d{2}")
POSTAL_RE = re.compile(r"\b\d{5}\b")
HEX_RE = re.compile(r"#([0-9a-fA-F]{6}|[0-9a-fA-F]{3})\b")
DATE_RE = re.compile(r"(20[12]\d)-(\d{2})-(\d{2})")

TECH_FINGERPRINTS = {
    "WordPress": [r"wp-content", r"wp-includes", r"wp-json"],
    # con frontera: "elementorySupportWixCodeSdk" no es Elementor
    "Elementor": [r"(?<![a-z])elementor[-/_.]", r"class=\"[^\"]*\belementor\b"],
    "Divi": [r"/themes/Divi/"],
    "WooCommerce": [r"woocommerce"],
    "Wix": [r"wix\.com", r"parastorage"],
    "Squarespace": [r"squarespace"],
    "Webflow": [r"webflow"],
    "Shopify": [r"cdn\.shopify"],
    "Prestashop": [r"prestashop"],
    "Joomla": [r"/media/jui/", r"joomla"],
    # CRMs / softwares inmobiliarios habituales en España
    "Inmovilla": [r"inmovilla"],
    "Witei": [r"witei"],
    "Optima CRM": [r"optima-crm", r"optimacrm"],
    "Mediaelx": [r"mediaelx"],
    "Sooprema": [r"sooprema"],
    "Inmoweb": [r"inmoweb"],
    "Egorealestate": [r"egorealestate", r"\bego\b.*inmo"],
    "Casafari": [r"casafari"],
    "Idealista tools": [r"idealista\.com/tools", r"idealista\.com[^\"']{0,80}widget"],
}

ANALYTICS_FINGERPRINTS = {
    "Google Analytics 4": [r"gtag\('config',\s*'G-", r"googletagmanager\.com/gtag/js\?id=G-"],
    "Universal Analytics (obsoleto)": [r"UA-\d{4,}-\d"],
    "Google Tag Manager": [r"googletagmanager\.com/gtm\.js", r"GTM-[A-Z0-9]+"],
    "Meta Pixel": [r"connect\.facebook\.net/.*fbevents", r"fbq\('init'"],
    "TikTok Pixel": [r"analytics\.tiktok\.com"],
    "LinkedIn Insight": [r"snap\.licdn\.com"],
    "Hotjar": [r"static\.hotjar\.com"],
    "Clarity": [r"clarity\.ms"],
    "Google Ads / Remarketing": [r"googleads\.g\.doubleclick", r"AW-\d+"],
}

CHAT_FINGERPRINTS = {
    "WhatsApp widget": [r"wa\.me/", r"api\.whatsapp\.com"],
    "Tawk.to": [r"tawk\.to"],
    "Crisp": [r"crisp\.chat"],
    "Intercom": [r"intercom"],
    "Tidio": [r"tidio"],
}

COOKIE_FINGERPRINTS = {
    "Complianz": [r"complianz"],
    "CookieYes": [r"cookieyes", r"cookie-law-info"],
    "Cookiebot": [r"cookiebot"],
    "Iubenda": [r"iubenda"],
    "Borlabs": [r"borlabs"],
    "Genérico": [r"cookie[-_]?consent", r"aceptar\s+cookies"],
}

PORTALES = {
    "Idealista": r"idealista\.com",
    "Fotocasa": r"fotocasa\.es",
    "Habitaclia": r"habitaclia\.com",
    "Pisos.com": r"pisos\.com",
    "Kyero": r"kyero\.com",
    "ThinkSpain": r"thinkspain\.com",
    "Rightmove": r"rightmove\.co\.uk",
    "Yaencontre": r"yaencontre\.com",
}

SOCIALES = {
    "Instagram": r"instagram\.com/([\w.\-]+)",
    "Facebook": r"facebook\.com/([\w.\-]+)",
    "LinkedIn": r"linkedin\.com/(company/|in/)([\w.\-]+)",
    "YouTube": r"youtube\.com/(@|channel/|c/|user/)([\w.\-]+)",
    "TikTok": r"tiktok\.com/@([\w.\-]+)",
    "X / Twitter": r"(twitter|x)\.com/([\w.\-]+)",
}

NEUTRAL_COLORS = {"#ffffff", "#000000", "#fff", "#000", "#f5f5f5", "#fafafa",
                  "#eeeeee", "#dddddd", "#cccccc", "#333333", "#666666", "#999999",
                  "#f0f0f0", "#e0e0e0", "#1a1a1a", "#212529", "#343a40", "#6c757d"}
# paleta por defecto de Bootstrap 5: aparecer aquí no dice nada de la marca
FRAMEWORK_COLORS = {"#0d6efd", "#6610f2", "#6f42c1", "#d63384", "#dc3545", "#fd7e14",
                    "#ffc107", "#198754", "#20c997", "#0dcaf0", "#adb5bd", "#495057",
                    "#0a58ca", "#146c43", "#b02a37", "#664d03", "#087990"}


# --- utilidades -------------------------------------------------------------

def log(msg: str, quiet: bool = False) -> None:
    if not quiet:
        print(f"  {msg}", file=sys.stderr)


def fetch(url: str, session: requests.Session, method: str = "GET") -> dict:
    """Descarga una URL y devuelve un dict con la respuesta o el error."""
    out = {"url": url, "ok": False, "status": None, "elapsed_ms": None,
           "bytes": None, "final_url": None, "error": None, "text": "",
           "content_type": None, "ssl_ok": True}
    verify = True
    for intento in (1, 2):
        try:
            t0 = time.time()
            r = session.request(method, url, headers=HEADERS, timeout=TIMEOUT,
                                allow_redirects=True, verify=verify)
        except requests.exceptions.SSLError as exc:
            # cadena de certificados incompleta o caducada: es un hallazgo, no un fallo
            out["ssl_ok"] = False
            out["error"] = f"SSLError: {exc}"
            if intento == 1:
                verify = False
                continue
            return out
        except Exception as exc:  # red caída, timeout, DNS...
            out["error"] = f"{type(exc).__name__}: {exc}"
            return out
        out["elapsed_ms"] = int((time.time() - t0) * 1000)
        out["status"] = r.status_code
        out["final_url"] = r.url
        out["content_type"] = r.headers.get("Content-Type", "")
        out["bytes"] = len(r.content)
        out["ok"] = r.ok
        out["redirected"] = (r.url.rstrip("/") != url.rstrip("/"))
        out["headers"] = {k.lower(): v for k, v in r.headers.items()
                          if k.lower() in ("server", "x-powered-by", "content-encoding",
                                           "cache-control", "strict-transport-security")}
        if method == "GET":
            ct = out["content_type"] or ""
            if url.endswith(".gz") or "gzip" in ct:
                try:
                    import gzip
                    out["text"] = gzip.decompress(r.content).decode("utf-8", "replace")
                except Exception:
                    out["text"] = ""
            elif any(k in ct for k in ("text", "xml", "json", "javascript")):
                out["text"] = r.text
        break
    return out


def match_any(text: str, patterns: list[str]) -> bool:
    return any(re.search(p, text, re.I) for p in patterns)


def detect(text: str, table: dict) -> list[str]:
    return [name for name, pats in table.items() if match_any(text, pats)]


def txt(el) -> str:
    return el.get_text(" ", strip=True) if el else ""


# --- análisis de una página -------------------------------------------------

def analyze_page(res: dict, base: str) -> dict:
    """Extrae las señales SEO/UX de una página ya descargada."""
    html = res.get("text") or ""
    soup = BeautifulSoup(html, "html.parser")
    page = {
        "url": res.get("final_url") or res.get("url"),
        "status": res.get("status"),
        "elapsed_ms": res.get("elapsed_ms"),
        "html_bytes": res.get("bytes"),
    }

    # --- head / SEO básico
    title = txt(soup.title)
    desc_el = soup.find("meta", attrs={"name": re.compile("^description$", re.I)})
    desc = (desc_el.get("content") or "").strip() if desc_el else ""
    canon = soup.find("link", rel=lambda v: v and "canonical" in v)
    robots_el = soup.find("meta", attrs={"name": re.compile("^robots$", re.I)})
    viewport = soup.find("meta", attrs={"name": re.compile("^viewport$", re.I)})
    html_tag = soup.find("html")

    page["seo"] = {
        "title": title,
        "title_len": len(title),
        "meta_description": desc,
        "meta_description_len": len(desc),
        "canonical": canon.get("href") if canon else None,
        "meta_robots": robots_el.get("content") if robots_el else None,
        "has_viewport": bool(viewport),
        "lang": (html_tag.get("lang") if html_tag else None),
        "hreflang": sorted({l.get("hreflang") for l in soup.find_all("link", hreflang=True)}),
        "og_title": bool(soup.find("meta", property="og:title")),
        "og_image": bool(soup.find("meta", property="og:image")),
    }

    # --- encabezados
    h1s = [txt(h) for h in soup.find_all("h1")]
    page["headings"] = {
        "h1_count": len(h1s),
        "h1": h1s[:5],
        "h2": [txt(h) for h in soup.find_all("h2")][:20],
        "h3_count": len(soup.find_all("h3")),
    }

    # --- datos estructurados
    ld_types, ld_blocks, ld_errors = [], [], 0
    for tag in soup.find_all("script", type=lambda v: v and "ld+json" in v):
        raw = tag.string or tag.get_text() or ""
        try:
            data = json.loads(raw)
        except Exception:
            ld_errors += 1
            continue
        for node in (data if isinstance(data, list) else [data]):
            if not isinstance(node, dict):
                continue
            graph = node.get("@graph") if isinstance(node.get("@graph"), list) else [node]
            for g in graph:
                if not isinstance(g, dict):
                    continue
                t = g.get("@type")
                for tt in (t if isinstance(t, list) else [t]):
                    if tt:
                        ld_types.append(str(tt))
                ld_blocks.append({k: v for k, v in g.items()
                                  if k in ("@type", "name", "telephone", "address",
                                           "aggregateRating", "openingHours",
                                           "openingHoursSpecification", "areaServed",
                                           "priceRange", "geo", "sameAs")})
    page["schema"] = {
        "types": sorted(set(ld_types)),
        "json_errors": ld_errors,
        "has_localbusiness": any(t in ld_types for t in
                                 ("RealEstateAgent", "LocalBusiness", "Organization")),
        "has_realestateagent": "RealEstateAgent" in ld_types,
        "has_rating": any("aggregateRating" in b for b in ld_blocks),
        "has_breadcrumb": "BreadcrumbList" in ld_types,
        "has_faq": "FAQPage" in ld_types,
        "blocks": ld_blocks[:6],
    }

    # --- imágenes
    imgs = soup.find_all("img")
    no_alt = [i for i in imgs if not (i.get("alt") or "").strip()]
    lazy = [i for i in imgs if (i.get("loading") == "lazy" or i.get("data-src"))]
    srcs = []
    for i in imgs:
        s = i.get("src") or i.get("data-src") or ""
        if s and not s.startswith("data:"):
            srcs.append(urljoin(base, s))
    page["images"] = {
        "count": len(imgs),
        "without_alt": len(no_alt),
        "lazy_loaded": len(lazy),
        "modern_formats": sum(1 for s in srcs if re.search(r"\.(webp|avif)(\?|$)", s, re.I)),
        "sample": srcs[:40],
    }

    # --- enlaces
    links, tels, mails, whats = [], [], [], []
    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        if href.startswith("tel:"):
            tels.append(href[4:])
        elif href.startswith("mailto:"):
            mails.append(href[7:])
        elif re.search(r"(wa\.me|api\.whatsapp\.com)", href, re.I):
            whats.append(href)
        elif href.startswith(("http://", "https://", "/")) or not href.startswith(("#", "javascript:")):
            links.append({"href": urljoin(base, href), "text": txt(a)[:80]})

    host = urlparse(base).netloc.replace("www.", "")
    internos = [l for l in links if host in urlparse(l["href"]).netloc]
    externos = [l for l in links if host not in urlparse(l["href"]).netloc and l["href"].startswith("http")]
    page["links"] = {
        "internal_count": len(internos),
        "external_count": len(externos),
        "tel": sorted(set(tels)),
        "mailto": sorted(set(mails)),
        "whatsapp": sorted(set(whats)),
        "internal": [l["href"] for l in internos],
        "internal_con_texto": [{"href": l["href"], "text": l["text"]} for l in internos][:150],
        "external_hosts": sorted({urlparse(l["href"]).netloc for l in externos}),
        "legales": sorted({l["href"] for l in internos
                           if LEGAL_URL_RE.search(l["href"]) or LEGAL_RE.search(l["text"])}),
        "idiomas_en_urls": sorted({m.group(1).lower()
                                   for l in internos
                                   for m in [IDIOMA_RE.match(urlparse(l["href"]).path)] if m}),
    }

    # --- formularios
    contexto = " ".join([page["url"].replace("_", " ").replace("-", " "),
                         page["seo"]["title"], " ".join(page["headings"]["h1"])])
    forms = []
    for f in soup.find_all("form"):
        fields = []
        for inp in f.find_all(["input", "select", "textarea"]):
            t = inp.get("type", inp.name)
            if t in ("hidden", "submit", "button"):
                continue
            fields.append({"type": t,
                           "name": inp.get("name") or inp.get("id") or "",
                           "placeholder": inp.get("placeholder") or ""})
        ftext = txt(f)[:300]
        forms.append({
            "action": urljoin(base, f.get("action") or ""),
            "field_count": len(fields),
            "fields": fields[:15],
            "text_sample": ftext,
            # el contexto manda: un formulario dentro de "Vende tu piso" es captación
            # aunque su propio texto solo enumere municipios
            "es_captacion": bool(CAPTACION_RE.search(ftext) or CAPTACION_RE.search(contexto)),
            "es_buscador": bool(re.search(r"(buscar|operaci[oó]n|zona|municipio|"
                                          r"habitacion|dormitor|precio\s*(m[ií]n|m[aá]x))",
                                          ftext + " " + " ".join(
                                              f2["name"] + f2["placeholder"] for f2 in fields), re.I)),
        })
    page["forms"] = forms

    # --- texto visible
    body_text = soup.get_text(" ", strip=True)
    page["text"] = {
        "word_count": len(body_text.split()),
        "captacion_hits": sorted(set(m.group(0)[:60] for m in CAPTACION_RE.finditer(body_text)))[:8],
        "menciona_alquiler": bool(ALQUILER_RE.search(body_text)),
        "menciona_venta": bool(VENTA_RE.search(body_text)),
        "telefonos_en_texto": sorted(set(PHONE_RE.findall(body_text)))[:5],
        "codigos_postales": sorted(set(POSTAL_RE.findall(body_text)))[:5],
        "fechas_visibles": sorted(set("-".join(m) for m in DATE_RE.findall(html)))[-5:],
        # los widgets de WhatsApp montan el enlace por JS: contar menciones
        # evita afirmar que no lo tienen cuando si
        "menciones_whatsapp": len(re.findall(r"whatsapp", html, re.I)),
    }

    # --- tecnologías
    page["tech"] = {
        "cms_y_crm": detect(html, TECH_FINGERPRINTS),
        "analytics": detect(html, ANALYTICS_FINGERPRINTS),
        "chat": detect(html, CHAT_FINGERPRINTS),
        "cookies": detect(html, COOKIE_FINGERPRINTS),
        "scripts_externos": sorted({urlparse(s["src"]).netloc
                                    for s in soup.find_all("script", src=True)
                                    if s["src"].startswith("http")})[:25],
        "script_count": len(soup.find_all("script")),
        "inline_style_blocks": len(soup.find_all("style")),
        "stylesheets": [urljoin(base, l["href"]) for l in
                        soup.find_all("link", rel=lambda v: v and "stylesheet" in v, href=True)][:15],
    }

    # --- portales y redes
    # enlazar un portal no es lo mismo que nombrarlo en un párrafo: separa ambas cosas
    hosts = " ".join(page["links"]["external_hosts"])
    page["portales"] = sorted({n for n, p in PORTALES.items() if re.search(p, hosts, re.I)})
    page["portales_mencionados"] = sorted({n for n, p in PORTALES.items()
                                           if re.search(p.replace(r"\.", "."), body_text, re.I)})
    redes = {}
    for red, pat in SOCIALES.items():
        m = re.search(pat, html, re.I)
        if m:
            redes[red] = m.group(0)
    page["redes"] = redes

    # --- inmuebles: distingue listados (buscador) de fichas individuales
    inmuebles = sorted({l["href"] for l in internos if FICHA_URL_RE.search(l["href"])})
    listados = [u for u in inmuebles if LISTADO_RE.search(u) and not FICHA_ID_RE.search(u)]
    fichas = [u for u in inmuebles if u not in listados]
    page["inmuebles"] = {
        "listados_count": len(listados), "listados_sample": listados[:6],
        "fichas_count": len(fichas), "fichas_sample": fichas[:12],
        "fichas_con_id": sum(1 for u in fichas if FICHA_ID_RE.search(u)),
    }
    page["es_pagina_captacion"] = bool(CAPTACION_RE.search(contexto))

    return page


# --- análisis de sitio ------------------------------------------------------

def check_robots_sitemap(root: str, session: requests.Session, quiet: bool) -> dict:
    out = {}
    r = fetch(urljoin(root, "/robots.txt"), session)
    cuerpo = r["text"] or ""
    # ojo: muchos servidores devuelven la home (HTML, 200) cuando no hay robots.txt
    es_robots = bool(re.search(r"(?im)^\s*(user-agent|disallow|allow|sitemap)\s*:", cuerpo)) \
        and "<html" not in cuerpo[:500].lower()
    out["robots_txt"] = {"status": r["status"], "exists": es_robots,
                         "devuelve_html": "<html" in cuerpo[:500].lower(),
                         "content": cuerpo[:1500] if es_robots else ""}
    sitemaps = re.findall(r"(?im)^\s*sitemap:\s*(\S+)", cuerpo if es_robots else "")
    if not sitemaps:
        sitemaps = [urljoin(root, "/sitemap.xml"), urljoin(root, "/sitemap_index.xml")]
    found = []
    for sm in sitemaps[:6]:
        s = fetch(sm, session)
        if s["ok"] and ("<urlset" in s["text"] or "<sitemapindex" in s["text"]):
            urls = re.findall(r"<loc>(.*?)</loc>", s["text"])
            found.append({"url": sm, "entries": len(urls),
                          "is_index": "<sitemapindex" in s["text"],
                          "sample": urls[:10]})
            log(f"sitemap {sm} -> {len(urls)} entradas", quiet)
    out["sitemaps"] = found
    # un sitemap índice no cuenta URLs: baja a sus hijos y súmalas
    if any(f["is_index"] for f in found):
        for idx in [f for f in found if f["is_index"]]:
            s = fetch(idx["url"], session)
            for h in re.findall(r"<loc>(.*?)</loc>", s["text"] or "")[:8]:
                sc = fetch(h, session)
                if sc["ok"] and "<urlset" in sc["text"]:
                    urls_h = re.findall(r"<loc>(.*?)</loc>", sc["text"])
                    found.append({"url": h, "entries": len(urls_h), "is_index": False,
                                  "sample": urls_h[:10]})
                    log(f"sitemap hijo {h} -> {len(urls_h)} entradas", quiet)
    out["sitemap_total_urls"] = sum(f["entries"] for f in found if not f["is_index"])
    return out


def check_https_and_www(url: str, session: requests.Session) -> dict:
    p = urlparse(url)
    root = f"{p.scheme}://{p.netloc}"
    alt_host = p.netloc[4:] if p.netloc.startswith("www.") else "www." + p.netloc
    res = {}
    http = fetch(f"http://{p.netloc}/", session, method="HEAD")
    res["http_redirige_a_https"] = bool(http.get("final_url", "").startswith("https://"))
    alt = fetch(f"{p.scheme}://{alt_host}/", session, method="HEAD")
    res["variante_www"] = {"host": alt_host, "status": alt["status"],
                           "final_url": alt.get("final_url")}
    res["hsts"] = bool(fetch(root, session, method="HEAD").get("headers", {})
                       .get("strict-transport-security"))
    return res


def weigh_images(srcs: list[str], session: requests.Session, limit: int = 25) -> dict:
    """Pesa las imágenes de la home: es donde estas webs se hunden en móvil."""
    total, medidas, fallos = 0, [], 0
    vistas = []
    for s in srcs:
        if s not in vistas:
            vistas.append(s)
    for s in vistas[:limit]:
        r = fetch(s, session, method="HEAD")
        size = None
        try:
            size = int(r.get("headers", {}).get("content-length") or 0) or None
        except (TypeError, ValueError):
            size = None
        if not size:
            # muchos servidores omiten content-length en HEAD (o lo comprimen):
            # cae a un GET real, que es la única medida fiable
            g = fetch(s, session)
            size = g.get("bytes") if g["ok"] else None
        if size:
            total += size
            medidas.append({"file": s.split("/")[-1].split("?")[0][:60],
                            "kb": round(size / 1024, 1),
                            "formato": (re.search(r"\.(\w{3,4})(\?|$)", s).group(1).lower()
                                        if re.search(r"\.(\w{3,4})(\?|$)", s) else "?")})
        else:
            fallos += 1
    medidas.sort(key=lambda m: -m["kb"])
    pesadas = [m for m in medidas if m["kb"] > 200]
    return {"medidas": len(medidas), "no_medidas": fallos,
            "peso_total_kb": round(total / 1024, 1),
            "imagenes_de_mas_de_200kb": len(pesadas),
            "mas_pesadas": medidas[:8]}


def brand_colors(stylesheets: list[str], html: str, session: requests.Session) -> list[str]:
    """Colores de marca: los hex no neutros más repetidos en el CSS."""
    # solo CSS propio: los CDN (bootstrap, fontawesome...) devolverían su paleta por
    # defecto y no la de la marca
    propios = [c for c in stylesheets
               if not re.search(r"(cdn|unpkg|jsdelivr|cloudflare|googleapis|bootstrapcdn)",
                                urlparse(c).netloc, re.I)]
    blob = html
    for css in propios[:5]:
        r = fetch(css, session)
        if r["ok"]:
            blob += r["text"][:400_000]
    cnt = Counter()
    for m in HEX_RE.finditer(blob):
        hexv = "#" + m.group(1).lower()
        if len(hexv) == 4:
            hexv = "#" + "".join(c * 2 for c in hexv[1:])
        if hexv in NEUTRAL_COLORS or hexv in FRAMEWORK_COLORS:
            continue
        r_, g_, b_ = (int(hexv[i:i + 2], 16) for i in (1, 3, 5))
        if max(r_, g_, b_) - min(r_, g_, b_) < 12:  # grises
            continue
        cnt[hexv] += 1
    return [c for c, _ in cnt.most_common(6)]


def pick_key_pages(home: dict, root: str, limit: int) -> list[str]:
    """Elige las páginas que más dicen del negocio."""
    prioridades = [
        (re.compile(r"(vender|vende|valorar|valoraci|tasaci|propietario|compram|compra|vendi|captac)", re.I), 100),
        (re.compile(r"(contact|contacto)", re.I), 90),
        (re.compile(r"(servicio|servicios)", re.I), 70),
        (re.compile(r"(quienes-somos|nosotros|about|equipo|empresa|oficina)", re.I), 60),
        (FICHA_URL_RE, 55),
        (BLOG_URL_RE, 40),
        (re.compile(r"(opinion|resena|rese[nñ]a|testimoni)", re.I), 35),
        (LEGAL_RE, 20),
        (re.compile(r"(zona|barrio|municipio|localidad)", re.I), 30),
    ]
    scored: dict[str, int] = {}
    for href in home["links"]["internal"]:
        clean = href.split("#")[0].rstrip("/")
        if not clean or clean.rstrip("/") == root.rstrip("/"):
            continue
        if re.search(r"\.(jpg|jpeg|png|gif|pdf|zip|webp|svg|mp4)$", clean, re.I):
            continue
        best = 0
        for rx, score in prioridades:
            if rx.search(clean):
                best = max(best, score)
        if best:
            scored[clean] = max(scored.get(clean, 0), best)
    ordenadas = sorted(scored, key=lambda u: (-scored[u], len(u)))
    # no repetir tres fichas de inmueble: con una basta para ver el patrón
    salida, fichas_vistas = [], 0
    for u in ordenadas:
        if FICHA_URL_RE.search(u):
            fichas_vistas += 1
            if fichas_vistas > 2:
                continue
        salida.append(u)
        if len(salida) >= limit:
            break
    return salida


# --- orquestación -----------------------------------------------------------

def run(url: str, max_pages: int, quiet: bool) -> dict:
    if not url.startswith(("http://", "https://")):
        url = "https://" + url
    session = requests.Session()
    p = urlparse(url)
    root = f"{p.scheme}://{p.netloc}"

    log(f"descargando home {url}", quiet)
    home_res = fetch(url, session)
    if not home_res["ok"] and "NameResolutionError" in (home_res.get("error") or ""):
        # el dominio puede resolver solo con www, o solo sin él
        alterno = (url.replace("://www.", "://") if "://www." in url
                   else url.replace("://", "://www."))
        log(f"DNS falla, probando {alterno}", quiet)
        alt_res = fetch(alterno, session)
        if alt_res["ok"]:
            home_res, url = alt_res, alterno
            p = urlparse(url)
            root = f"{p.scheme}://{p.netloc}"
    if not home_res["ok"]:
        return {"url": url, "error": home_res["error"] or f"HTTP {home_res['status']}",
                "fetched_at": datetime.now(timezone.utc).isoformat()}

    home = analyze_page(home_res, home_res["final_url"] or url)
    log(f"home ok ({home_res['elapsed_ms']} ms, {round(home_res['bytes']/1024)} KB)", quiet)

    site = {
        "url_original": url,
        "url_final": home_res["final_url"],
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "servidor": home_res.get("headers", {}),
        "certificado_ssl_valido": home_res.get("ssl_ok", True),
        "ssl_error": None if home_res.get("ssl_ok", True) else home_res.get("error"),
        "transporte": check_https_and_www(home_res["final_url"] or url, session),
    }
    site.update(check_robots_sitemap(root, session, quiet))

    log("pesando imágenes de la home", quiet)
    site["peso_imagenes_home"] = weigh_images(home["images"]["sample"], session)
    site["colores_marca"] = brand_colors(home["tech"]["stylesheets"], home_res["text"], session)

    # páginas clave
    objetivo = pick_key_pages(home, root, max_pages)
    log(f"páginas clave a revisar: {len(objetivo)}", quiet)
    paginas = []
    for u in objetivo:
        r = fetch(u, session)
        if not r["ok"] or "html" not in (r["content_type"] or ""):
            paginas.append({"url": u, "status": r["status"], "error": r["error"]})
            continue
        pg = analyze_page(r, r["final_url"] or u)
        paginas.append(pg)
        log(f"  {r['status']} {u}", quiet)

    # 404 personalizado
    r404 = fetch(urljoin(root, "/pagina-que-no-existe-obelum-test"), session)
    t404 = r404.get("text") or ""
    parece_404 = bool(re.search(r"(404|no\s+(se\s+ha\s+)?encontrad|not\s+found|"
                                r"p[aá]gina\s+no\s+existe)", t404, re.I))
    site["error_404"] = {
        "status": r404["status"],
        "devuelve_404": r404["status"] == 404,
        # peor caso: responde 200 con una página de error -> Google la indexa como válida
        "soft_404": r404["status"] == 200 and parece_404,
        "redirige_a_home": (r404.get("final_url") or "").rstrip("/") == root.rstrip("/"),
        "tiene_pagina_personalizada": len(t404) > 500 and parece_404,
    }

    # --- resumen transversal
    todas = [home] + [pg for pg in paginas if "seo" in pg]
    resumen = {
        "paginas_analizadas": len(todas),
        "cms_y_crm": sorted({t for pg in todas for t in pg["tech"]["cms_y_crm"]}),
        "analytics": sorted({t for pg in todas for t in pg["tech"]["analytics"]}),
        "chat": sorted({t for pg in todas for t in pg["tech"]["chat"]}),
        "cookies": sorted({t for pg in todas for t in pg["tech"]["cookies"]}),
        "portales": sorted({t for pg in todas for t in pg["portales"]}),
        "redes": {k: v for pg in todas for k, v in pg["redes"].items()},
        "schema_types": sorted({t for pg in todas for t in pg["schema"]["types"]}),
        "telefonos": sorted({t for pg in todas for t in pg["links"]["tel"]}),
        "whatsapp": sorted({t for pg in todas for t in pg["links"]["whatsapp"]}),
        "menciones_whatsapp": sum(pg["text"]["menciones_whatsapp"] for pg in todas),
        "emails": sorted({t for pg in todas for t in pg["links"]["mailto"]}),
        "hay_formulario_captacion": any(f["es_captacion"] for pg in todas for f in pg["forms"]),
        "hay_buscador": any(f["es_buscador"] for pg in todas for f in pg["forms"]),
        "paginas_captacion": [pg["url"] for pg in todas if pg["es_pagina_captacion"]],
        "hay_pagina_vender": any(pg["es_pagina_captacion"] for pg in todas)
                             or any(CAPTACION_RE.search(l["text"] or "")
                                    for l in home["links"]["internal_con_texto"]),
        "hay_blog": any(BLOG_URL_RE.search(pg["url"]) for pg in todas)
                    or any(BLOG_URL_RE.search(l) for l in home["links"]["internal"]),
        "paginas_legales": sorted({u for pg in todas for u in pg["links"]["legales"]}),
        "hay_legal": any(pg["links"]["legales"] for pg in todas),
        "fichas_en_home": home["inmuebles"]["fichas_count"],
        "listados_en_home": home["inmuebles"]["listados_count"],
        "titulos_duplicados": [t for t, n in Counter(
            pg["seo"]["title"] for pg in todas).items() if n > 1 and t],
        "paginas_sin_meta_description": [pg["url"] for pg in todas
                                         if not pg["seo"]["meta_description"]],
        "paginas_sin_h1": [pg["url"] for pg in todas if pg["headings"]["h1_count"] == 0],
        "paginas_multiple_h1": [pg["url"] for pg in todas if pg["headings"]["h1_count"] > 1],
        "tiempo_medio_ms": round(sum(pg["elapsed_ms"] for pg in todas) / len(todas)),
        "html_medio_kb": round(sum(pg["html_bytes"] for pg in todas) / len(todas) / 1024, 1),
        "imagenes_sin_alt_total": sum(pg["images"]["without_alt"] for pg in todas),
        "imagenes_total": sum(pg["images"]["count"] for pg in todas),
        "idiomas_hreflang": sorted({h for pg in todas for h in pg["seo"]["hreflang"]}),
        "idiomas_en_urls": sorted({i for pg in todas for i in pg["links"]["idiomas_en_urls"]}),
        "paginas_sin_og_image": [pg["url"] for pg in todas if not pg["seo"]["og_image"]],
        "titles_largos": [pg["url"] for pg in todas if pg["seo"]["title_len"] > 60],
        "titles_cortos": [pg["url"] for pg in todas if 0 < pg["seo"]["title_len"] < 25],
    }

    return {"site": site, "resumen": resumen, "home": home, "paginas": paginas}


def main() -> int:
    ap = argparse.ArgumentParser(description="Recon de web inmobiliaria")
    ap.add_argument("url")
    ap.add_argument("--out", default="recon.json")
    ap.add_argument("--pages", type=int, default=8, help="páginas internas a revisar")
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args()

    data = run(args.url, args.pages, args.quiet)
    with open(args.out, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=2)
    print(f"Recon guardado en {args.out}")
    if "error" in data:
        print(f"ERROR: {data['error']}", file=sys.stderr)
        return 1
    r = data["resumen"]
    print(f"  {r['paginas_analizadas']} páginas | CMS/CRM: {', '.join(r['cms_y_crm']) or '?'} "
          f"| captación: {'sí' if r['hay_formulario_captacion'] else 'NO'} "
          f"| buscador: {'sí' if r['hay_buscador'] else 'NO'} "
          f"| schema: {', '.join(r['schema_types'][:5]) or 'ninguno'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
