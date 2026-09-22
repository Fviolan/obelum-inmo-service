"""Detecta que el informe niega algo que el recon si midio.

El 22/9/2026, con lasose.com: los datos medidos decian buscador true (9 senales
de buscador en el JS) y dos telefonos a la vista, y el informe afirmo que la web
no tenia buscador propio y que la unica via de contacto era WhatsApp. No fue un
fallo de medicion: el dato estaba delante y el modelo escribio lo contrario.

`cifras.py` prohibe INVENTAR numeros. Esto prohibe NEGAR hechos. Es el error mas
caro de los dos: una cifra inventada el cliente no puede comprobarla, pero una
ausencia falsa la desmiente abriendo su propia portada, y con ella se cae el
informe entero.

Regla: si un campo de los datos medidos viene a true, con lista no vacia o con un
numero mayor que cero, el informe no puede decir que eso no existe. Puede decir
que es mejorable, nunca que no esta.
"""
import re
import sys

sys.stdout.reconfigure(encoding="utf-8")


# Negacion de existencia con verbo: «no tienes buscador», «el buscador no existe».
# Vale en cualquier orden porque el verbo ya acota de que se esta negando.
# Deliberadamente NO entran «no filtra», «no permite», «no se ve» ni «Google no
# las lee»: esas son criticas de algo que si existe, que es justo lo permitido.
NEG_VERBO = re.compile(
    r"(\bno\s+(?:se\s+|lo\s+|la\s+|los\s+|las\s+|te\s+)*"
    r"(?:tien\w+|hay\b|existe\w*|dispon\w+|cuent\w+|ofrec\w+|incluy\w+|"
    r"encuentr\w+|aparec\w+|figur\w+)|"
    r"\binexistente\b|\bbrilla\s+por\s+su\s+ausencia\b)", re.I)

# Negacion que va delante de lo negado: solo cuenta si el tema viene despues.
# Sin esa condicion, «captacion sin friccion» se leia como negar la captacion.
NEG_DELANTE = re.compile(
    r"(\bsin\b|\bcarec\w+\s+de\b|\bfalta\w*\s+(?:de\s+|un[ao]?\s+)?|"
    r"\bausencia\s+de\b|\bning[uú]n[ao]?\b|\bni\s+(?:un[ao]?|siquiera|tienes?)\b)",
    re.I)

EXCLUSIVO = re.compile(r"\b([uú]nic[ao]s?|solo|s[oó]lo|solamente|[uú]nicamente)\b", re.I)
CONTACTO = re.compile(r"(contact\w+|\bv[ií]a\b|\bcanal\w*\b|\bforma\b|\bmanera\b)", re.I)


def _t(patron):
    return re.compile(patron, re.I)


# Cada entrada: el campo de los datos medidos, como se llama en castellano, el
# patron con el que el informe habla de el, y la salvedad que NO es una negacion
# de existencia sino una critica legitima de algo que si esta.
CAMPOS = [
    {"campo": "buscador", "etiqueta": "buscador de inmuebles",
     "tema": _t(r"(buscador|b[uú]squeda\s+(?:de\s+)?(?:inmuebles|propiedades|pisos|"
                r"viviendas)|buscar\s+(?:inmuebles|propiedades|pisos))"),
     "salvedad": _t(r"avanzad")},
    {"campo": "captacion", "etiqueta": "captación de propietarios",
     "tema": _t(r"(captaci[oó]n|captar\s+propietari|valorador|"
                r"(?:valoraci[oó]n|tasaci[oó]n)\s+(?:gratu|online|de\s+vivienda)|"
                r"p[aá]gina\s+(?:de|para)\s+vender|vende\s+tu\s+piso)"),
     "salvedad": None},
    {"campo": "telefonos", "etiqueta": "teléfono",
     "tema": _t(r"(tel[eé]fono|n[uú]mero\s+de\s+tel[eé]fono)"),
     "salvedad": _t(r"(schema|estructurad|clicab|pulsab|enlace|en\s+cada\s+ficha|"
                    r"en\s+el\s+m[oó]vil|en\s+la\s+cabecera)")},
    {"campo": "emails", "etiqueta": "correo de contacto",
     "tema": _t(r"(correo|e-?mail)"),
     "salvedad": None},
    {"campo": "whatsapp", "etiqueta": "WhatsApp",
     "tema": _t(r"whatsapp"),
     "salvedad": _t(r"(flotante|en\s+cada\s+ficha|autom[aá]tic)")},
    {"campo": "resenas", "etiqueta": "prueba social (reseñas)",
     "tema": _t(r"(rese[nñ]as?|opiniones|testimonios|prueba\s+social)"),
     # que Google no las lea como estrellas SI se puede decir: es otra cosa
     "salvedad": _t(r"(schema|estructurad|estrellas|aggregate|rich|"
                    r"google\s+no\s+las|no\s+las\s+lee|en\s+el\s+c[oó]digo)")},
    {"campo": "redes", "etiqueta": "redes sociales",
     "tema": _t(r"(redes\s+sociales|instagram|facebook|linkedin|tiktok|youtube)"),
     "salvedad": _t(r"(actualiz|public|activ|seguidor|frecuencia)")},
    {"campo": "blog", "etiqueta": "blog",
     "tema": _t(r"(blog|secci[oó]n\s+de\s+noticias)"),
     "salvedad": _t(r"(actualiz|public|desde\s+20|frecuencia|abandonad)")},
    {"campo": "sitemap_urls", "etiqueta": "sitemap",
     "tema": _t(r"(sitemap|mapa\s+del?\s+sitio)"),
     "salvedad": _t(r"(robots|search\s+console|enviad|declarad)")},
    {"campo": "schema", "etiqueta": "datos estructurados (schema)",
     "tema": _t(r"(schema|datos\s+estructurados|marcado\s+estructurado|json-?ld)"),
     # negar un tipo concreto que no esta medido es legitimo
     "salvedad": _t(r"(inmobiliari|localbusiness|realestate|negocio\s+local|"
                    r"rese[nñ]|producto|ficha|evento|faq|breadcrumb|agente)")},
]

# Canales de contacto, para la trampa de la exclusividad: «la unica via de
# contacto es WhatsApp» no niega el WhatsApp, niega los dos telefonos medidos.
CANALES = [
    ("telefonos", "teléfono", _t(r"tel[eé]fono")),
    ("whatsapp", "WhatsApp", _t(r"whatsapp")),
    ("emails", "correo", _t(r"(correo|e-?mail)")),
]


def _medido(valor) -> bool:
    """El campo dice que ESO ESTA: true, lista con algo o numero mayor que cero."""
    if isinstance(valor, bool):
        return valor
    if isinstance(valor, (int, float)):
        return valor > 0
    if isinstance(valor, (list, tuple, dict, str)):
        return len(valor) > 0
    return False


def _prueba(campo, valor) -> str:
    if isinstance(valor, bool):
        return f"{campo} está a true en los DATOS MEDIDOS"
    if isinstance(valor, (list, tuple)):
        return f"{campo} trae {len(valor)}: {', '.join(str(v) for v in valor[:3])}"
    return f"{campo} vale {valor}"


def _clausulas(texto: str) -> list[str]:
    """Trocea la frase. «No hay schema y el buscador va bien» son dos cosas: sin
    trocear, el buscador quedaba dentro del alcance de la negacion del schema."""
    trozos = re.split(r"[.;:,()]|\s+(?:y|pero|aunque|mientras|salvo|excepto|o)\s+",
                      texto)
    return [t for t in trozos if t and t.strip()]


def _negado(clausula: str, tema) -> str | None:
    """La negacion que alcanza al tema dentro de esta clausula, si la hay."""
    if tema.search(clausula):
        m = NEG_VERBO.search(clausula)
        if m:
            return m.group(0).strip()
    for m in NEG_DELANTE.finditer(clausula):
        if tema.search(clausula, m.end()):
            return m.group(0).strip()
    return None


def _campos_del_informe(contenido: dict) -> list:
    campos = []
    for i, a in enumerate(contenido.get("areas", []), 1):
        campos += [(f"area {i} badge", a.get("badge")), (f"area {i} desc", a.get("desc"))]
    for i, b in enumerate(contenido.get("blockers", []), 1):
        campos += [(f"bloqueante {i} titulo", b.get("title")),
                   (f"bloqueante {i} desc", b.get("desc"))]
    for i, a in enumerate(contenido.get("actions", []), 1):
        campos += [(f"accion {i} titulo", a.get("title")),
                   (f"accion {i} desc", a.get("desc"))]
    for campo in ("antes", "despues"):
        for i, t in enumerate(contenido.get(campo, []), 1):
            campos.append((f"{campo} {i}", t))
    campos += [("diagnostico", contenido.get("diagnostico")),
               ("rule", contenido.get("rule")),
               ("titular", contenido.get("titular_competencia")
                or (contenido.get("competencia") or {}).get("titular"))]
    return [(n, str(t)) for n, t in campos if t]


def revisar_contradicciones(contenido: dict, permitido: dict) -> list:
    """Frases del informe que niegan un campo que los datos medidos dan por cierto."""
    medidos = (permitido or {}).get("datos_medidos") or permitido or {}
    ciertos = [c for c in CAMPOS if _medido(medidos.get(c["campo"]))]
    canales_ciertos = [(et, rx) for clave, et, rx in CANALES if _medido(medidos.get(clave))]

    avisos = []
    for nombre, texto in _campos_del_informe(contenido):
        for clausula in _clausulas(texto):
            for c in ciertos:
                if c["salvedad"] and c["salvedad"].search(clausula):
                    continue
                neg = _negado(clausula, c["tema"])
                if neg:
                    avisos.append(
                        f"{nombre}: dices «{neg}» sobre {c['etiqueta']}, pero "
                        f"{_prueba(c['campo'], medidos.get(c['campo']))}. Eso SÍ existe: "
                        f"no puedes decir que no está, solo que es mejorable "
                        f"-> {clausula.strip()[:80]}")

            # la exclusividad: afirmar un unico canal borra los demas medidos
            if (len(canales_ciertos) > 1 and EXCLUSIVO.search(clausula)
                    and CONTACTO.search(clausula)):
                nombrados = [et for et, rx in canales_ciertos if rx.search(clausula)]
                olvidados = [et for et, rx in canales_ciertos if not rx.search(clausula)]
                if nombrados and olvidados:
                    avisos.append(
                        f"{nombre}: dices que {' y '.join(nombrados)} es la única vía de "
                        f"contacto, pero también hay {' y '.join(olvidados)} en los DATOS "
                        f"MEDIDOS. Quita la exclusividad "
                        f"-> {clausula.strip()[:80]}")

    vistos, unicos = set(), []
    for a in avisos:
        if a not in vistos:
            vistos.add(a)
            unicos.append(a)
    return unicos


if __name__ == "__main__":
    import glob
    import json
    for f in sorted(glob.glob("*-content.json")):
        d = json.load(open(f, encoding="utf-8"))
        medidos = json.load(open(f.replace("-content", "-medidos"), encoding="utf-8")) \
            if glob.glob(f.replace("-content", "-medidos")) else {}
        av = revisar_contradicciones(d, medidos)
        print(f"\n{d.get('cliente')}: {len(av)} contradicciones")
        for a in av:
            print("   -", a)
