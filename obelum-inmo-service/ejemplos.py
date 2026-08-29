"""Detecta ejemplos y nombres propios que no salen de la web auditada.

El modelo rellena con topónimos plausibles: escribió «piso en Gràcia» y
«alquiler Eixample» para una inmobiliaria en cuyos datos no aparece ni Gràcia ni
Eixample. Suena bien y es mentira. Si el cliente trabaja en Sants, ve que el
informe habla de otro barrio y deja de creerse el resto.

Regla: todo nombre propio y todo entrecomillado del informe tiene que salir de
los datos medidos, salvo plataformas genéricas del sector.
"""
import json
import re
import sys
import unicodedata

sys.stdout.reconfigure(encoding="utf-8")

# plataformas y siglas que puede nombrar sin haberlas medido: son del oficio
GENERICOS = {
    "google", "whatsapp", "instagram", "facebook", "linkedin", "youtube",
    "tiktok", "twitter", "idealista", "fotocasa", "habitaclia", "pisos",
    "yaencontre", "wordpress", "wix", "elementor", "joomla", "analytics",
    "search", "console", "maps", "business", "seo", "ux", "ia", "ssl", "https",
    "hsts", "url", "urls", "html", "css", "json", "schema", "localbusiness",
    "realestateagent", "sitemap", "robots", "alt", "meta", "kb", "mb", "ms",
    "obelum", "labs", "francesc", "web", "webp", "pdf", "crm", "api",
}

# palabras que van en mayuscula por gramatica, no por ser nombre propio
GRAMATICALES = {
    "el", "la", "los", "las", "un", "una", "tu", "tus", "su", "sus", "y", "o",
    "de", "del", "en", "con", "sin", "por", "para", "que", "no", "si", "es",
    "esto", "eso", "cada", "cuando", "como", "mientras", "ahora", "ya", "solo",
    "tienes", "pierdes", "google", "crear", "añadir", "anadir", "instalar",
    "marcar", "enlazar", "optimizar", "agregar", "revisar", "reducir", "bajar",
    "publicar", "activar", "mejorar", "duplicar", "implementar", "poner",
}


def _norm(t):
    t = unicodedata.normalize("NFKD", (t or "").lower())
    return "".join(c for c in t if not unicodedata.combining(c))


def revisar_ejemplos(contenido: dict, permitido: dict) -> list[str]:
    """Nombres propios y entrecomillados que no aparecen en los datos medidos."""
    fuente = _norm(json.dumps(permitido, ensure_ascii=False))

    # cada campo se revisa por separado: al concatenar titulo y descripcion, la
    # primera palabra de la descripcion parecia un nombre propio en mitad de frase
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

    avisos = []
    for nombre, texto in campos:
        if not texto:
            continue
        texto = str(texto)

        # 1) lo entrecomillado: son ejemplos y tienen que ser reales
        for cita in re.findall(r"«([^»]{2,60})»", texto):
            if _norm(cita) not in fuente:
                avisos.append(f"{nombre}: el ejemplo «{cita}» no sale de la web auditada")

        # 2) nombres propios: mayuscula que NO sea inicio de frase.
        # Se recorre frase a frase y se salta siempre la primera palabra.
        for frase in re.split(r"(?<=[.:;!?])\s+", texto):
            palabras = re.findall(r"\b[\wÁÉÍÓÚÑáéíóúñ]+\b", frase)
            for palabra in palabras[1:]:
                if not re.fullmatch(r"[A-ZÁÉÍÓÚÑ][a-záéíóúñ]{2,}", palabra):
                    continue
                n = _norm(palabra)
                if n in GENERICOS or n in GRAMATICALES or n in fuente:
                    continue
                avisos.append(f"{nombre}: el nombre propio {palabra} "
                              f"no sale de la web auditada")

    # sin duplicados, conservando el orden
    vistos, unicos = set(), []
    for a in avisos:
        if a not in vistos:
            vistos.add(a)
            unicos.append(a)
    return unicos
