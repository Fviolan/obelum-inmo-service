#!/usr/bin/env python3
"""Genera el informe Audit Inmob Web F (Obelum Labs) en HTML y PDF a partir de content.json.

Uso:
    python generate.py content.json --outdir "C:/ruta/salida"

Salida: "Auditoria inmobiliaria <slug>.html" y ".pdf" — apaisado, 3 páginas:
  1. Semáforo de 6 áreas + bloqueantes + diagnóstico
  2. 10 acciones priorizadas + regla 80/20
  3. Antes vs. Después + KPIs
"""
from __future__ import annotations

import argparse
import base64
import html as html_mod
import json
import re
from pathlib import Path

from reportlab.lib.colors import HexColor, white
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfgen import canvas

# --- marca Obelum (fija) ----------------------------------------------------
OBELUM_WEB = "Obelumlabs.com"
OBELUM_URL = "https://obelumlabs.com"
OBELUM_WA = "745 08 34 16"
OBELUM_MAIL = "info@obelumlabs.com"

TINTA = "#111827"
GRIS = "#6b7280"
LINEA = "#e5e7eb"
FONDO = "#f8fafc"
SEMAFORO = {"r": "#dc2626", "a": "#f59e0b", "g": "#16a34a"}
SEMAFORO_TXT = {"r": "Crítico", "a": "Mejorable", "g": "Correcto"}
PRIO_COLOR = {"alta": "#dc2626", "media": "#f59e0b", "baja": "#6b7280"}

# --- tamanos de texto -------------------------------------------------------
# Los usa TANTO el dibujo como revisar_ajuste(). Si se cambia un tamano aqui,
# la validacion se entera sola. Nunca escribir estos numeros a mano en el codigo.
T_BADGE = 12          # titular del area, 2 lineas
T_DESC_AREA = 11.6    # explicacion del area, 5 lineas
T_BLOCKER = 12        # bloqueante, 1 linea
T_ACCION_TIT = 13     # titulo de accion, 1 linea
T_ACCION_DESC = 11.4  # descripcion de accion, 2 lineas
T_COMPARADOR = 12.2   # cada punto de antes/despues, 2 lineas
T_DIAGNOSTICO = 12.5  # diagnostico y titular de competencia, 3 lineas

W, H = landscape(A4)          # 297 x 210 mm
MARGEN = 14 * mm


# --- utilidades -------------------------------------------------------------

def esc(s) -> str:
    return html_mod.escape(str(s if s is not None else ""))


def acento(data: dict, cual: str = "acento") -> str:
    marca = data.get("marca") or {}
    por_defecto = {"acento": "#0f4c81", "acento2": "#0b3358"}
    val = marca.get(cual) or por_defecto[cual]
    return val if re.fullmatch(r"#[0-9a-fA-F]{6}", val) else por_defecto[cual]


def wrap(c: canvas.Canvas, texto: str, fuente: str, tam: float, ancho: float) -> list[str]:
    """Parte un texto en líneas que caben en `ancho` puntos."""
    palabras, lineas, actual = str(texto).split(), [], ""
    for p in palabras:
        prueba = f"{actual} {p}".strip()
        if pdfmetrics.stringWidth(prueba, fuente, tam) <= ancho:
            actual = prueba
        else:
            if actual:
                lineas.append(actual)
            actual = p
    if actual:
        lineas.append(actual)
    return lineas


def texto_bloque(c, x, y, texto, fuente, tam, ancho, interlineado=None,
                 color=TINTA, max_lineas=None, justificar=False) -> float:
    """Escribe texto envuelto y devuelve la Y final."""
    interlineado = interlineado or tam * 1.25
    c.setFont(fuente, tam)
    c.setFillColor(HexColor(color))
    lineas = wrap(c, texto, fuente, tam, ancho)
    recortado = False
    if max_lineas and len(lineas) > max_lineas:
        lineas = lineas[:max_lineas]
        lineas[-1] = lineas[-1][:max(0, len(lineas[-1]) - 1)] + "…"
        recortado = True

    for i, ln in enumerate(lineas):
        ultima = (i == len(lineas) - 1)
        # la última línea de un párrafo nunca se justifica (quedaría rota),
        # salvo que el texto venga recortado y no sea final de verdad
        if justificar and not (ultima and not recortado):
            palabras = ln.split()
            espacio = pdfmetrics.stringWidth(" ", fuente, tam)
            sobra = ancho - pdfmetrics.stringWidth(ln, fuente, tam)
            hueco = sobra / (len(palabras) - 1) if len(palabras) > 1 else 0
            # una URL larga deja la línea medio vacía: justificarla abre zanjas
            if len(palabras) > 1 and hueco <= 2.2 * espacio:
                cx = x
                for p in palabras:
                    c.drawString(cx, y, p)
                    cx += pdfmetrics.stringWidth(p, fuente, tam) + espacio + hueco
                y -= interlineado
                continue
        c.drawString(x, y, ln)
        y -= interlineado
    return y


LOGO = Path(__file__).with_name("obelum_logo.png")


def cabecera(c, data, titulo_pagina, pagina):
    a1 = acento(data)
    c.setFillColor(HexColor(a1))
    c.rect(0, H - 6 * mm, W, 6 * mm, stroke=0, fill=1)

    # el maestro (obelum_logo_blanco.png) es blanco sobre transparente y no se ve
    # en cabecera blanca; aquí va la versión en azul de marca #1e2fc4.
    # Si falta el archivo, cae al nombre en texto.
    if LOGO.exists():
        ancho_logo = 46 * mm
        alto_logo = ancho_logo * 767 / 3523
        c.drawImage(str(LOGO), MARGEN, H - 19.5 * mm, width=ancho_logo,
                    height=alto_logo, mask="auto")
    else:
        c.setFont("Helvetica-Bold", 17.0)
        c.setFillColor(HexColor(TINTA))
        c.drawString(MARGEN, H - 18 * mm, "OBELUM LABS")
    c.setFont("Helvetica", 10.5)
    c.setFillColor(HexColor(GRIS))
    c.drawString(MARGEN, H - 22.5 * mm, "Auditoría web para inmobiliarias")

    c.setFont("Helvetica-Bold", 14.0)
    c.setFillColor(HexColor(a1))
    c.drawRightString(W - MARGEN, H - 18 * mm, titulo_pagina)
    c.setFont("Helvetica", 10.5)
    c.setFillColor(HexColor(GRIS))
    c.drawRightString(W - MARGEN, H - 22.5 * mm,
                      f"{data.get('cliente', '')} · {data.get('url', '')}")

    c.setStrokeColor(HexColor(LINEA))
    c.setLineWidth(0.8)
    c.line(MARGEN, H - 26 * mm, W - MARGEN, H - 26 * mm)


MESES = ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio",
         "agosto", "septiembre", "octubre", "noviembre", "diciembre"]


def fecha_pie(data: dict) -> str:
    """En el pie va solo mes y año: el informe no caduca el día siguiente."""
    bruta = str(data.get("fecha", "")).strip()
    m = re.match(r"(\d{4})-(\d{1,2})(?:-\d{1,2})?$", bruta)
    if m:
        anio, mes = int(m.group(1)), int(m.group(2))
        if 1 <= mes <= 12:
            return f"{MESES[mes - 1]} {anio}"
    m = re.match(r"\d{1,2}[/-](\d{1,2})[/-](\d{4})$", bruta)
    if m:
        mes, anio = int(m.group(1)), int(m.group(2))
        if 1 <= mes <= 12:
            return f"{MESES[mes - 1]} {anio}"
    return bruta


def total_paginas(data: dict) -> int:
    return 4 if (data.get("competencia") or {}).get("filas") else 3


def pie(c, data, pagina):
    c.setStrokeColor(HexColor(LINEA))
    c.setLineWidth(0.8)
    c.line(MARGEN, 12 * mm, W - MARGEN, 12 * mm)
    c.setFont("Helvetica", 9.5)
    # la web va en color y enlazada; el resto del pie, en gris
    c.setFillColor(HexColor(acento(data)))
    c.drawString(MARGEN, 8 * mm, OBELUM_WEB)
    ancho_web = pdfmetrics.stringWidth(OBELUM_WEB, "Helvetica", 9.5)
    c.setStrokeColor(HexColor(acento(data)))
    c.setLineWidth(0.4)
    c.line(MARGEN, 7.2 * mm, MARGEN + ancho_web, 7.2 * mm)
    c.linkURL(OBELUM_URL,
              (MARGEN, 7 * mm, MARGEN + ancho_web, 8 * mm + 9.5),
              relative=0, thickness=0)
    c.setFillColor(HexColor(GRIS))
    c.drawString(MARGEN + ancho_web, 8 * mm,
                 f"  ·  WhatsApp {OBELUM_WA}  ·  {OBELUM_MAIL}")
    c.drawRightString(W - MARGEN, 8 * mm,
                      f"{fecha_pie(data)}   ·   Página {pagina} de {total_paginas(data)}")


# --- página 1: semáforo -----------------------------------------------------

def pagina_semaforo(c, data):
    cabecera(c, data, "Diagnóstico", 1)
    a1 = acento(data)
    areas = data["areas"][:6]
    blockers = data.get("blockers", [])

    cols, filas = 3, 2
    ancho_col = (W - 2 * MARGEN - (cols - 1) * 5 * mm) / cols
    alto_fila = 53 * mm      # crece para absorber los 2 px de más de cada texto

    # el bloque de áreas se centra entre la línea de cabecera y el diagnóstico
    alto_blockers = (9.5 * mm + len(blockers[:3]) * 5.5 * mm + 10 * mm) if blockers else 0
    alto_contenido = filas * alto_fila + (filas - 1) * 5 * mm + alto_blockers
    zona_alta, zona_baja = H - 30 * mm, 39 * mm
    top = zona_alta - max(0, (zona_alta - zona_baja - alto_contenido) / 2)

    for i, ar in enumerate(areas):
        col, fila = i % cols, i // cols
        x = MARGEN + col * (ancho_col + 5 * mm)
        y = top - fila * (alto_fila + 5 * mm)
        estado = (ar.get("status") or "a").lower()[:1]
        color = SEMAFORO.get(estado, SEMAFORO["a"])

        c.setFillColor(HexColor(FONDO))
        c.setStrokeColor(HexColor(LINEA))
        c.roundRect(x, y - alto_fila, ancho_col, alto_fila, 3 * mm, stroke=1, fill=1)
        # banda de color a la izquierda: el semáforo se lee de un vistazo
        c.setFillColor(HexColor(color))
        c.roundRect(x, y - alto_fila, 2.2 * mm, alto_fila, 1 * mm, stroke=0, fill=1)

        ix = x + 6 * mm
        iw = ancho_col - 11 * mm
        c.setFont("Helvetica-Bold", 13.5)
        c.setFillColor(HexColor(TINTA))
        c.drawString(ix, y - 7.5 * mm, str(ar.get("name", ""))[:38])

        c.setFont("Helvetica-Bold", 11.0)
        c.setFillColor(HexColor(color))
        c.drawString(ix, y - 12.5 * mm,
                     f"{SEMAFORO_TXT.get(estado, '')}  ·  {ar.get('eje', '')}".upper())

        yy = texto_bloque(c, ix, y - 18 * mm, ar.get("badge", ""),
                          "Helvetica-Bold", T_BADGE, iw, 13.8, TINTA,
                          max_lineas=2)
        texto_bloque(c, ix, yy - 1 * mm, ar.get("desc", ""),
                     "Helvetica", T_DESC_AREA, iw, 13.0, GRIS, max_lineas=5, justificar=True)

    y = top - filas * (alto_fila + 5 * mm) - 3 * mm

    # bloqueantes: lo que hay que arreglar hoy
    if blockers:
        c.setFillColor(HexColor("#fef2f2"))
        c.setStrokeColor(HexColor("#fecaca"))
        alto_bl = 9.5 * mm + len(blockers[:3]) * 5.5 * mm
        y_bl = max(y, 40.5 * mm + alto_bl)
        c.roundRect(MARGEN, y_bl - alto_bl, W - 2 * MARGEN, alto_bl, 2 * mm, stroke=1, fill=1)
        c.setFont("Helvetica-Bold", 12.5)
        c.setFillColor(HexColor("#b91c1c"))
        c.drawString(MARGEN + 4 * mm, y_bl - 6.5 * mm, "BLOQUEANTES — corregir antes que nada")
        yy = y_bl - 12.5 * mm
        for b in blockers[:3]:
            c.setFont("Helvetica-Bold", 12.0)
            c.setFillColor(HexColor(TINTA))
            etiqueta = f"· {b.get('title', '')}: "
            c.drawString(MARGEN + 4 * mm, yy, etiqueta)
            ancho_et = pdfmetrics.stringWidth(etiqueta, "Helvetica-Bold", 12)
            texto_bloque(c, MARGEN + 4 * mm + ancho_et, yy, b.get("desc", ""),
                         "Helvetica", T_BLOCKER, W - 2 * MARGEN - 10 * mm - ancho_et,
                         13, GRIS, max_lineas=1)
            yy -= 5.5 * mm
        y -= alto_bl + 4 * mm

    # diagnóstico, casi al pie
    c.setFillColor(HexColor(a1))
    alto_dg = 24 * mm
    c.roundRect(MARGEN, 15 * mm, W - 2 * MARGEN, alto_dg, 2 * mm, stroke=0, fill=1)
    c.setFont("Helvetica-Bold", 10.0)
    c.setFillColor(white)
    c.drawString(MARGEN + 5 * mm, 15 * mm + alto_dg - 6 * mm, "DIAGNÓSTICO")
    c.setFillColor(white)
    lineas = wrap(c, data.get("diagnostico", ""), "Helvetica-Bold", 12.5,
                  W - 2 * MARGEN - 10 * mm)
    yy = 15 * mm + alto_dg - 11 * mm
    c.setFont("Helvetica-Bold", 12.5)
    for ln in lineas[:3]:
        c.drawString(MARGEN + 5 * mm, yy, ln)
        yy -= 5 * mm

    pie(c, data, 1)
    c.showPage()


# --- página de competencia --------------------------------------------------

ESTADO_FONDO = {"bien": "#dcfce7", "regular": "#fef3c7", "mal": "#fee2e2"}
ESTADO_TINTA = {"bien": "#15803d", "regular": "#b45309", "mal": "#b91c1c"}


def pagina_competencia(c, data, pagina):
    cabecera(c, data, "Frente a la competencia", pagina)
    a1 = acento(data)
    comp = data["competencia"]
    columnas = comp["columnas"]
    filas = comp["filas"]
    n = len(columnas)

    ancho_crit = 88 * mm
    ancho_col = (W - 2 * MARGEN - ancho_crit) / n
    alto_fila = 9.6 * mm
    alto_cab = 12 * mm

    alto_titular = 20 * mm if comp.get("titular") else 0
    alto_total = alto_cab + len(filas) * alto_fila
    zona_alta, zona_baja = H - 32 * mm, 15 * mm + alto_titular + 6 * mm
    top = zona_alta - max(0, (zona_alta - zona_baja - alto_total) / 2)

    # cabecera de la tabla: la primera columna es el cliente y va destacada
    y = top - alto_cab
    for i, nombre in enumerate(columnas):
        x = MARGEN + ancho_crit + i * ancho_col
        es_cliente = (i == 0)
        c.setFillColor(HexColor(a1) if es_cliente else HexColor(FONDO))
        c.roundRect(x + 0.6 * mm, y, ancho_col - 1.2 * mm, alto_cab, 1.5 * mm,
                    stroke=0, fill=1)
        c.setFont("Helvetica-Bold", 10.0)
        c.setFillColor(white if es_cliente else HexColor(TINTA))
        etiqueta = str(nombre)
        while (pdfmetrics.stringWidth(etiqueta, "Helvetica-Bold", 10) > ancho_col - 4 * mm
               and len(etiqueta) > 4):
            etiqueta = etiqueta[:-1]
        c.drawCentredString(x + ancho_col / 2, y + alto_cab / 2 - 0.5 * mm, etiqueta)
        if es_cliente:
            c.setFont("Helvetica", 8.0)
            c.setFillColor(white)
            c.drawCentredString(x + ancho_col / 2, y + 2 * mm, "TU WEB")

    # filas
    for j, fila in enumerate(filas):
        yf = y - (j + 1) * alto_fila
        if j % 2 == 0:
            c.setFillColor(HexColor("#fbfcfe"))
            c.rect(MARGEN, yf, W - 2 * MARGEN, alto_fila, stroke=0, fill=1)

        c.setFont("Helvetica-Bold", 10.2)
        c.setFillColor(HexColor(TINTA))
        crit = str(fila["criterio"])
        while (pdfmetrics.stringWidth(crit, "Helvetica-Bold", 10.2) > ancho_crit - 6 * mm
               and len(crit) > 6):
            crit = crit[:-1]
        c.drawString(MARGEN + 2 * mm, yf + alto_fila / 2 - 1 * mm, crit)

        for i, valor in enumerate(fila["valores"][:n]):
            x = MARGEN + ancho_crit + i * ancho_col
            est = (fila.get("estados") or ["regular"] * n)[i]
            c.setFillColor(HexColor(ESTADO_FONDO.get(est, "#f1f5f9")))
            c.roundRect(x + 3 * mm, yf + 1.2 * mm, ancho_col - 6 * mm,
                        alto_fila - 2.4 * mm, 1.2 * mm, stroke=0, fill=1)
            c.setFont("Helvetica-Bold", 10.4)
            c.setFillColor(HexColor(ESTADO_TINTA.get(est, GRIS)))
            c.drawCentredString(x + ancho_col / 2, yf + alto_fila / 2 - 1 * mm, str(valor))

        c.setStrokeColor(HexColor(LINEA))
        c.setLineWidth(0.4)
        c.line(MARGEN, yf, W - MARGEN, yf)

    # marco de la columna del cliente: que se lea de un vistazo dónde va
    c.setStrokeColor(HexColor(a1))
    c.setLineWidth(1.2)
    c.roundRect(MARGEN + ancho_crit + 0.6 * mm, y - len(filas) * alto_fila,
                ancho_col - 1.2 * mm, alto_cab + len(filas) * alto_fila,
                1.5 * mm, stroke=1, fill=0)

    if comp.get("titular"):
        c.setFillColor(HexColor(a1))
        c.roundRect(MARGEN, 15 * mm, W - 2 * MARGEN, alto_titular, 2 * mm, stroke=0, fill=1)
        c.setFont("Helvetica-Bold", 10.0)
        c.setFillColor(white)
        c.drawString(MARGEN + 5 * mm, 15 * mm + alto_titular - 6 * mm,
                     "LO QUE ELLOS YA TIENEN")
        c.setFont("Helvetica-Bold", 12.5)
        yy = 15 * mm + alto_titular - 11 * mm
        for ln in wrap(c, comp["titular"], "Helvetica-Bold", 12.5,
                       W - 2 * MARGEN - 10 * mm)[:3]:
            c.drawString(MARGEN + 5 * mm, yy, ln)
            yy -= 5 * mm

    pie(c, data, pagina)
    c.showPage()


# --- página 2: acciones -----------------------------------------------------

def pagina_acciones(c, data, pagina):
    cabecera(c, data, "10 acciones inmediatas", pagina)
    a1 = acento(data)
    acciones = data["actions"][:10]

    cols = 2
    ancho_col = (W - 2 * MARGEN - 6 * mm) / cols
    alto = 20 * mm

    filas = (len(acciones) + cols - 1) // cols
    alto_contenido = filas * alto + (filas - 1) * 2.5 * mm
    zona_alta, zona_baja = H - 30 * mm, 36 * mm      # 36mm = techo de la regla 80/20
    top = zona_alta - max(0, (zona_alta - zona_baja - alto_contenido) / 2)

    for i, ac in enumerate(acciones):
        col, fila = i % cols, i // cols
        x = MARGEN + col * (ancho_col + 6 * mm)
        y = top - fila * (alto + 2.5 * mm)
        prio = (ac.get("prio") or "media").lower()
        color = PRIO_COLOR.get(prio, PRIO_COLOR["media"])

        c.setFillColor(HexColor(FONDO))
        c.setStrokeColor(HexColor(LINEA))
        c.roundRect(x, y - alto, ancho_col, alto, 2 * mm, stroke=1, fill=1)

        # número
        c.setFillColor(HexColor(a1))
        c.circle(x + 6.5 * mm, y - alto / 2, 4.2 * mm, stroke=0, fill=1)
        c.setFont("Helvetica-Bold", 12.5)
        c.setFillColor(white)
        c.drawCentredString(x + 6.5 * mm, y - alto / 2 - 1.4 * mm, str(i + 1))

        ix = x + 13.5 * mm
        c.setFont("Helvetica-Bold", 12.8)
        c.setFillColor(HexColor(TINTA))
        titulo = str(ac.get("title", ""))[:52]

        # etiqueta de prioridad, arriba a la derecha
        et = prio.upper()
        c.setFont("Helvetica-Bold", 10.2)
        anc = pdfmetrics.stringWidth(et, "Helvetica-Bold", 10.2) + 4.5 * mm
        c.setFillColor(HexColor(color))
        c.roundRect(x + ancho_col - anc - 2.5 * mm, y - 7 * mm, anc, 5 * mm,
                    1 * mm, stroke=0, fill=1)
        c.setFillColor(white)
        c.drawCentredString(x + ancho_col - anc / 2 - 2.5 * mm, y - 5.5 * mm, et)

        iw = ancho_col - 19 * mm - anc
        c.setFont("Helvetica-Bold", 12.8)
        c.setFillColor(HexColor(TINTA))
        while (pdfmetrics.stringWidth(titulo, "Helvetica-Bold", 12.8) > iw + 4 * mm
               and len(titulo) > 8):
            titulo = titulo[:-1]
        c.drawString(ix, y - 6 * mm, titulo)

        texto_bloque(c, ix, y - 10.8 * mm, ac.get("desc", ""),
                     "Helvetica", T_ACCION_DESC, ancho_col - 17 * mm, 12.8, GRIS, max_lineas=2)

    # regla 80/20 al pie
    c.setFillColor(HexColor(FONDO))
    c.setStrokeColor(HexColor(a1))
    c.setLineWidth(1.2)
    c.roundRect(MARGEN, 15 * mm, W - 2 * MARGEN, 17 * mm, 2 * mm, stroke=1, fill=1)
    c.setFont("Helvetica-Bold", 12.0)
    c.setFillColor(HexColor(a1))
    c.drawString(MARGEN + 5 * mm, 27.5 * mm, "REGLA 80/20")
    texto_bloque(c, MARGEN + 5 * mm, 22.5 * mm, data.get("rule", ""),
                 "Helvetica", 13, W - 2 * MARGEN - 10 * mm, 13.5, TINTA, max_lineas=2)

    pie(c, data, pagina)
    c.showPage()


# --- página 3: antes/después + KPIs ----------------------------------------

def pagina_comparador(c, data, pagina):
    cabecera(c, data, "Antes vs. Después", pagina)
    a1 = acento(data)
    antes, despues = data["antes"][:7], data["despues"][:7]

    ancho_col = (W - 2 * MARGEN - 8 * mm) / 2
    # las columnas crecen para ocupar el hueco entre cabecera y KPIs
    zona_alta, zona_baja = H - 32 * mm, 18 * mm
    alto_kpis = 26 * mm if data.get("kpis") else 0
    alto = zona_alta - zona_baja - alto_kpis - (6 * mm if alto_kpis else 0)
    alto = max(60 * mm, min(alto, 118 * mm))
    top = zona_alta

    for idx, (titulo, items, color, fondo) in enumerate([
        ("ANTES — situación actual", antes, "#b91c1c", "#fef2f2"),
        ("DESPUÉS — con las mejoras aplicadas", despues, "#15803d", "#f0fdf4"),
    ]):
        x = MARGEN + idx * (ancho_col + 8 * mm)
        c.setFillColor(HexColor(fondo))
        c.setStrokeColor(HexColor(LINEA))
        c.roundRect(x, top - alto, ancho_col, alto, 2.5 * mm, stroke=1, fill=1)
        c.setFont("Helvetica-Bold", 13.0)
        c.setFillColor(HexColor(color))
        c.drawString(x + 5 * mm, top - 7.5 * mm, titulo)

        # reparte los 7 puntos por toda la columna en vez de amontonarlos arriba
        anchura_texto = ancho_col - 16 * mm
        n_lineas = sum(min(2, len(wrap(c, it, "Helvetica", 12.2, anchura_texto)))
                       for it in items)
        alto_texto = n_lineas * 14
        hueco = (alto - 20 * mm - alto_texto) / max(1, len(items) - 1)
        hueco = max(1.8 * mm, min(hueco, 9 * mm))

        y = top - 15.5 * mm
        for it in items:
            c.setFillColor(HexColor(color))
            c.circle(x + 6.4 * mm, y + 1.3 * mm, 1.3 * mm, stroke=0, fill=1)
            y = texto_bloque(c, x + 10.5 * mm, y, it, "Helvetica", 12.2,
                             anchura_texto, 14, TINTA, max_lineas=2)
            y -= hueco

    # KPIs
    kpis = data.get("kpis", [])[:4]
    if kpis:
        y_k = top - alto - 6 * mm
        ancho_k = (W - 2 * MARGEN - 3 * 4 * mm) / 4
        for i, k in enumerate(kpis):
            x = MARGEN + i * (ancho_k + 4 * mm)
            c.setFillColor(HexColor(a1))
            c.roundRect(x, y_k - 20 * mm, ancho_k, 20 * mm, 2 * mm, stroke=0, fill=1)
            c.setFont("Helvetica-Bold", 19.0)
            c.setFillColor(white)
            c.drawCentredString(x + ancho_k / 2, y_k - 10 * mm, str(k.get("big", "")))
            c.setFont("Helvetica", 9.5)
            c.drawCentredString(x + ancho_k / 2, y_k - 15.5 * mm, str(k.get("lab", ""))[:34])

    if data.get("note"):
        c.setFont("Helvetica-Oblique", 9.5)
        c.setFillColor(HexColor(GRIS))
        c.drawString(MARGEN, 15.5 * mm, str(data["note"])[:150])

    pie(c, data, pagina)
    c.showPage()


def create_pdf(data: dict, ruta: Path) -> None:
    c = canvas.Canvas(str(ruta), pagesize=landscape(A4))
    c.setTitle(f"Auditoría web — {data.get('cliente', data.get('slug', ''))}")
    c.setAuthor("Obelum Labs")
    n = 1
    pagina_semaforo(c, data)
    n += 1
    if (data.get("competencia") or {}).get("filas"):
        pagina_competencia(c, data, n)
        n += 1
    pagina_acciones(c, data, n)
    pagina_comparador(c, data, n + 1)
    c.save()


# --- HTML -------------------------------------------------------------------

CSS = """
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif;
background:#e9edf2;color:%(tinta)s;padding:24px 12px;line-height:1.45}
.hoja{width:1123px;max-width:100%%;min-height:794px;margin:0 auto 24px;background:#fff;
border-radius:10px;box-shadow:0 8px 28px rgba(15,23,42,.12);padding:0 0 56px;position:relative;
display:flex;flex-direction:column;overflow:hidden}
.barra{height:8px;background:%(a1)s}
.cab{display:flex;justify-content:space-between;align-items:flex-end;gap:16px;
padding:20px 34px 14px;border-bottom:1px solid %(linea)s}
.marca{font-weight:800;font-size:22.0px;letter-spacing:.3px}
.marca .logo{display:block;height:34px;width:156px;margin-bottom:4px;
background:url('%(logo)s') left center/contain no-repeat}
.marca small{display:block;font-weight:500;font-size:13.0px;color:%(gris)s;letter-spacing:0}
.cab-r{text-align:right}
.cab-r b{color:%(a1)s;font-size:18.0px}
.cab-r small{display:block;color:%(gris)s;font-size:13.0px}
.cuerpo{flex:1;padding:22px 34px}
.rejilla{display:grid;grid-template-columns:repeat(3,1fr);gap:14px}
.area{border:1px solid %(linea)s;border-left:6px solid var(--c);border-radius:8px;
background:%(fondo)s;padding:14px 16px}
.area h3{font-size:18.0px;margin-bottom:4px}
.estado{font-size:14.0px;font-weight:800;letter-spacing:.6px;color:var(--c);text-transform:uppercase}
.area .badge{display:block;font-size:16.0px;font-weight:700;margin:6px 0 4px}
.area p{font-size:15.5px;color:%(gris)s;text-align:justify;hyphens:auto}
.blockers{margin-top:16px;background:#fef2f2;border:1px solid #fecaca;border-radius:8px;padding:12px 16px}
.blockers h4{color:#b91c1c;font-size:16.0px;letter-spacing:.4px;margin-bottom:6px}
.blockers li{font-size:16.0px;list-style:none;margin-bottom:4px}
.blockers li b{color:%(tinta)s}
.blockers li span{color:%(gris)s}
.diag{margin:18px 34px 0;background:%(a1)s;color:#fff;border-radius:8px;padding:16px 20px}
.diag small{font-size:12.0px;font-weight:800;letter-spacing:1px;opacity:.85}
.diag p{font-size:18.0px;font-weight:700;margin-top:6px}
.acciones{display:grid;grid-template-columns:repeat(2,1fr);gap:10px 18px}
.accion{display:flex;gap:12px;border:1px solid %(linea)s;border-radius:8px;background:%(fondo)s;padding:10px 12px}
.num{flex:0 0 30px;height:30px;border-radius:50%%;background:%(a1)s;color:#fff;font-weight:800;
font-size:16.0px;display:flex;align-items:center;justify-content:center}
.accion h4{font-size:16.5px;display:flex;justify-content:space-between;gap:8px;align-items:center}
.prio{font-size:13.0px;font-weight:800;letter-spacing:.5px;color:#fff;background:var(--p);
border-radius:4px;padding:2px 6px;text-transform:uppercase;white-space:nowrap}
.accion p{font-size:15.0px;color:%(gris)s;margin-top:2px}
.regla{margin:18px 34px 0;border:1.5px solid %(a1)s;background:%(fondo)s;border-radius:8px;padding:14px 18px}
.regla small{color:%(a1)s;font-weight:800;font-size:14.0px;letter-spacing:1px}
.regla p{font-size:17.0px;margin-top:4px}
table.vs{width:100%%;border-collapse:separate;border-spacing:0 4px;font-size:14.0px}
table.vs th{padding:8px 6px;text-align:center;font-size:13.5px;border-radius:6px}
table.vs th.crit{text-align:left;width:33%%}
table.vs th.yo{background:%(a1)s;color:#fff}
table.vs th.yo small{display:block;font-size:10.5px;font-weight:500;opacity:.85}
table.vs th.rival{background:%(fondo)s;color:%(tinta)s}
table.vs td{padding:6px;text-align:center;font-weight:700;border-radius:5px}
table.vs td.crit{text-align:left;font-size:14.0px;font-weight:700;padding-left:8px}
table.vs tr:nth-child(odd) td.crit{background:#fbfcfe}
td.bien{background:#dcfce7;color:#15803d}
td.regular{background:#fef3c7;color:#b45309}
td.mal{background:#fee2e2;color:#b91c1c}
table.vs td.yo{outline:2px solid %(a1)s;outline-offset:-2px}
.titular{margin:18px 34px 0;background:%(a1)s;color:#fff;border-radius:8px;padding:16px 20px}
.titular small{font-size:12.0px;font-weight:800;letter-spacing:1px;opacity:.85}
.titular p{font-size:18.0px;font-weight:700;margin-top:6px}
.comp{display:grid;grid-template-columns:1fr 1fr;gap:18px}
.col{border:1px solid %(linea)s;border-radius:8px;padding:16px 18px}
.col.antes{background:#fef2f2}.col.despues{background:#f0fdf4}
.col h3{font-size:17.0px;margin-bottom:10px}
.col.antes h3{color:#b91c1c}.col.despues h3{color:#15803d}
.col li{list-style:none;font-size:16.0px;margin-bottom:9px;padding-left:18px;position:relative}
.col li:before{content:'';position:absolute;left:0;top:7px;width:8px;height:8px;border-radius:50%%}
.col.antes li:before{background:#b91c1c}.col.despues li:before{background:#15803d}
.kpis{display:grid;grid-template-columns:repeat(4,1fr);gap:12px;margin-top:18px}
.kpi{background:%(a1)s;color:#fff;border-radius:8px;padding:14px;text-align:center}
.kpi b{display:block;font-size:28.0px;line-height:1.1}
.kpi span{font-size:13.0px;opacity:.9}
.nota{font-size:13.0px;color:%(gris)s;font-style:italic;margin:12px 34px 0}
.pie{position:absolute;left:0;right:0;bottom:0;display:flex;justify-content:space-between;
padding:10px 34px;border-top:1px solid %(linea)s;font-size:12.5px;color:%(gris)s;background:#fff}
.pie a{color:%(a1)s;font-weight:600}
@media print{body{background:#fff;padding:0}
.hoja{box-shadow:none;border-radius:0;margin:0;width:297mm;min-height:210mm;page-break-after:always}}
@page{size:A4 landscape;margin:0}
"""


LOGO_WEB = Path(__file__).with_name("obelum_logo_web.png")


def logo_datauri() -> str:
    """El HTML tiene que viajar solo: el logo va incrustado, no enlazado.
    Se incrusta UNA vez en el CSS; si fuera por <img> se repetiría en cada página."""
    if not LOGO_WEB.exists():
        return ""
    return "data:image/png;base64," + base64.b64encode(
        LOGO_WEB.read_bytes()).decode("ascii")


def logo_html() -> str:
    return ('<span class="logo" role="img" aria-label="Obelum Labs"></span>'
            if LOGO_WEB.exists() else "OBELUM LABS")


def _cab(data, titulo):
    return f"""<div class="barra"></div>
<div class="cab">
  <div class="marca">{logo_html()}<small>Auditoría web para inmobiliarias</small></div>
  <div class="cab-r"><b>{esc(titulo)}</b>
    <small>{esc(data.get('cliente',''))} · {esc(data.get('url',''))}</small></div>
</div>"""


def _pie(data, n):
    return (f'<div class="pie"><span><a href="{OBELUM_URL}" target="_blank" '
            f'rel="noopener">{OBELUM_WEB}</a> · WhatsApp {OBELUM_WA} · {OBELUM_MAIL}</span>'
            f'<span>{esc(fecha_pie(data))} · Página {n} de {total_paginas(data)}</span>'
            '</div>')


def _hoja_competencia(data, pagina):
    comp = data["competencia"]
    cols = comp["columnas"]
    cab = '<th class="crit"></th>' + "".join(
        f'<th class="{"yo" if i == 0 else "rival"}">{esc(c)}'
        f'{"<small>TU WEB</small>" if i == 0 else ""}</th>'
        for i, c in enumerate(cols))
    filas = ""
    for f in comp["filas"]:
        estados = f.get("estados") or ["regular"] * len(cols)
        celdas = "".join(
            f'<td class="{esc(estados[i])}{" yo" if i == 0 else ""}">{esc(v)}</td>'
            for i, v in enumerate(f["valores"][:len(cols)]))
        filas += f'<tr><td class="crit">{esc(f["criterio"])}</td>{celdas}</tr>'
    titular = ""
    if comp.get("titular"):
        titular = ('<div class="titular"><small>LO QUE ELLOS YA TIENEN</small>'
                   f'<p>{esc(comp["titular"])}</p></div>')
    return f"""<section class="hoja">{_cab(data, 'Frente a la competencia')}
  <div class="cuerpo"><table class="vs"><thead><tr>{cab}</tr></thead>
  <tbody>{filas}</tbody></table></div>
  {titular}<div style="height:18px"></div>{_pie(data, pagina)}
</section>"""


def create_html(data: dict, ruta: Path) -> None:
    a1 = acento(data)
    css = CSS % {"tinta": TINTA, "gris": GRIS, "linea": LINEA, "fondo": FONDO,
                 "a1": a1, "logo": logo_datauri()}

    areas = "".join(
        f'<div class="area" style="--c:{SEMAFORO.get((a.get("status") or "a")[:1], SEMAFORO["a"])}">'
        f'<h3>{esc(a.get("name"))}</h3>'
        f'<span class="estado">{SEMAFORO_TXT.get((a.get("status") or "a")[:1], "")} · '
        f'{esc(a.get("eje",""))}</span>'
        f'<span class="badge">{esc(a.get("badge"))}</span>'
        f'<p>{esc(a.get("desc"))}</p></div>'
        for a in data["areas"][:6])

    blockers = ""
    if data.get("blockers"):
        items = "".join(f'<li><b>{esc(b.get("title"))}:</b> <span>{esc(b.get("desc"))}</span></li>'
                        for b in data["blockers"][:4])
        blockers = ('<div class="blockers"><h4>BLOQUEANTES — corregir antes que nada</h4>'
                    f'<ul>{items}</ul></div>')

    acciones = "".join(
        f'<div class="accion"><div class="num">{i+1}</div><div>'
        f'<h4>{esc(a.get("title"))}'
        f'<span class="prio" style="--p:{PRIO_COLOR.get((a.get("prio") or "media").lower(), PRIO_COLOR["media"])}">'
        f'{esc((a.get("prio") or "media").upper())}</span></h4>'
        f'<p>{esc(a.get("desc"))}</p></div></div>'
        for i, a in enumerate(data["actions"][:10]))

    antes = "".join(f"<li>{esc(x)}</li>" for x in data["antes"][:7])
    despues = "".join(f"<li>{esc(x)}</li>" for x in data["despues"][:7])
    kpis = "".join(f'<div class="kpi"><b>{esc(k.get("big"))}</b><span>{esc(k.get("lab"))}</span></div>'
                   for k in data.get("kpis", [])[:4])

    doc = f"""<!doctype html>
<html lang="es"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Auditoría web — {esc(data.get('cliente', data.get('slug','')))}</title>
<style>{css}</style></head><body>

<section class="hoja">{_cab(data, 'Diagnóstico')}
  <div class="cuerpo"><div class="rejilla">{areas}</div>{blockers}</div>
  <div class="diag"><small>DIAGNÓSTICO</small><p>{esc(data.get('diagnostico'))}</p></div>
  <div style="height:18px"></div>{_pie(data, 1)}
</section>

{_hoja_competencia(data, 2) if (data.get('competencia') or {}).get('filas') else ''}

<section class="hoja">{_cab(data, '10 acciones inmediatas')}
  <div class="cuerpo"><div class="acciones">{acciones}</div></div>
  <div class="regla"><small>REGLA 80/20</small><p>{esc(data.get('rule'))}</p></div>
  <div style="height:18px"></div>{_pie(data, total_paginas(data) - 1)}
</section>

<section class="hoja">{_cab(data, 'Antes vs. Después')}
  <div class="cuerpo">
    <div class="comp">
      <div class="col antes"><h3>ANTES — situación actual</h3><ul>{antes}</ul></div>
      <div class="col despues"><h3>DESPUÉS — con las mejoras aplicadas</h3><ul>{despues}</ul></div>
    </div>
    <div class="kpis">{kpis}</div>
  </div>
  {'<p class="nota">' + esc(data['note']) + '</p>' if data.get('note') else ''}
  <div style="height:18px"></div>{_pie(data, total_paginas(data))}
</section>

</body></html>"""
    ruta.write_text(doc, encoding="utf-8")


# --- validación y arranque --------------------------------------------------

RECUENTOS = {"areas": 6, "actions": 10, "antes": 7, "despues": 7, "kpis": 4}


def validar(data: dict) -> list[str]:
    avisos = []
    for campo, n in RECUENTOS.items():
        real = len(data.get(campo) or [])
        if real != n:
            avisos.append(f"'{campo}': {real} elementos (se esperaban {n})")
    for campo in ("slug", "url", "cliente", "diagnostico"):
        if not data.get(campo):
            avisos.append(f"falta '{campo}'")
    for a in data.get("areas", []):
        if (a.get("status") or "")[:1] not in SEMAFORO:
            avisos.append(f"área '{a.get('name')}': status debe ser r, a o g")
    for a in data.get("actions", []):
        if (a.get("prio") or "").lower() not in PRIO_COLOR:
            avisos.append(f"acción '{a.get('title')}': prio debe ser alta, media o baja")
    avisos.extend(revisar_ajuste(data))
    return avisos


# --- ¿cabe el texto? Se mide con el mismo wrap() que dibuja, no con una
# estimacion paralela: cualquier calculo aparte acaba divergiendo.

def _lineas(texto, fuente, tam, ancho):
    from reportlab.pdfgen import canvas as _c
    import io
    c = _c.Canvas(io.BytesIO())
    return len(wrap(c, str(texto or ""), fuente, tam, ancho))


def _max_caracteres(texto, fuente, tam, ancho, lineas):
    """Cuantos caracteres del texto caben en el hueco.

    Se recorta palabra a palabra hasta que entra: da un objetivo exacto que el
    modelo puede cumplir, en vez de hablarle de lineas.
    """
    palabras = str(texto or "").split()
    for corte in range(len(palabras), 0, -1):
        prueba = " ".join(palabras[:corte])
        if _lineas(prueba, fuente, tam, ancho) <= lineas:
            return len(prueba)
    return 0


def _aviso(campo, texto, fuente, tam, ancho, lineas, n):
    """Pide al modelo que acorte un texto.

    OJO con como se redacta esto. Dar solo un objetivo en caracteres, junto al
    "no cambies el significado" del bucle de correccion, empuja al modelo a
    cumplir por la via literal: borrar letras de las palabras. Conserva todas
    las palabras (significado intacto) y baja el recuento. Paso de verdad el
    6/9/2026 con gruphabitat.cat: "buscador propio" salio como "oscador ropio".
    Por eso se le dice explicitamente COMO acortar y que esta prohibido.
    """
    cabe = _max_caracteres(texto, fuente, tam, ancho, lineas)
    return (f"{campo}: tiene {len(str(texto))} caracteres y ocupa {n} lineas; "
            f"caben {lineas}. Reescribelo mas corto, en {cabe} caracteres como "
            f"maximo, quitando alguna idea o diciendo lo mismo con menos "
            f"palabras. PROHIBIDO borrar letras de una palabra, abreviarla o "
            f"partirla: todas las palabras deben quedar enteras y bien escritas. "
            f"Texto actual: {str(texto)[:70]}")


def revisar_ajuste(data: dict) -> list[str]:
    """Devuelve los textos que se cortarian al maquetar."""
    avisos = []
    ancho_col = (W - 2 * MARGEN - 2 * 5 * mm) / 3
    iw = ancho_col - 11 * mm
    ancho_accion = (W - 2 * MARGEN - 6 * mm) / 2

    for i, a in enumerate(data.get("areas", []), 1):
        n = _lineas(a.get("badge"), "Helvetica-Bold", T_BADGE, iw)
        if n > 2:
            avisos.append(_aviso(f"area {i} badge", a.get("badge"),
                                 "Helvetica-Bold", T_BADGE, iw, 2, n))
        n = _lineas(a.get("desc"), "Helvetica", T_DESC_AREA, iw)
        if n > 5:
            avisos.append(_aviso(f"area {i} desc", a.get("desc"),
                                 "Helvetica", T_DESC_AREA, iw, 5, n))

    for i, b in enumerate(data.get("blockers", []), 1):
        etiqueta = f"· {b.get('title', '')}: "
        ancho = W - 2 * MARGEN - 10 * mm - pdfmetrics.stringWidth(
            etiqueta, "Helvetica-Bold", T_BLOCKER)
        n = _lineas(b.get("desc"), "Helvetica", T_BLOCKER, ancho)
        if n > 1:
            avisos.append(_aviso(f"bloqueante {i} desc", b.get("desc"),
                                 "Helvetica", T_BLOCKER, ancho, 1, n))

    for i, a in enumerate(data.get("actions", []), 1):
        t = str(a.get("title", ""))
        if pdfmetrics.stringWidth(t, "Helvetica-Bold", T_ACCION_TIT) > ancho_accion - 42 * mm:
            cabe_t = _max_caracteres(t, "Helvetica-Bold", T_ACCION_TIT,
                                     ancho_accion - 42 * mm, 1)
            avisos.append(f"accion {i} titulo: tiene {len(t)} caracteres y no cabe "
                          f"en una linea. Acortalo a {cabe_t} como maximo. "
                          f"Texto actual: {t[:50]}")
        n = _lineas(a.get("desc"), "Helvetica", T_ACCION_DESC, ancho_accion - 17 * mm)
        if n > 2:
            avisos.append(_aviso(f"accion {i} desc", a.get("desc"),
                                 "Helvetica", T_ACCION_DESC,
                                 ancho_accion - 17 * mm, 2, n))

    ancho_comp = (W - 2 * MARGEN - 8 * mm) / 2 - 16 * mm
    for campo in ("antes", "despues"):
        for i, t in enumerate(data.get(campo, []), 1):
            n = _lineas(t, "Helvetica", T_COMPARADOR, ancho_comp)
            if n > 2:
                avisos.append(_aviso(f"{campo} {i}", t, "Helvetica",
                                     T_COMPARADOR, ancho_comp, 2, n))

    n = _lineas(data.get("diagnostico"), "Helvetica-Bold", T_DIAGNOSTICO,
                W - 2 * MARGEN - 10 * mm)
    if n > 3:
        avisos.append(f"diagnostico ocupa {n} lineas (caben 3)")

    comp = data.get("competencia") or {}
    if comp.get("titular"):
        n = _lineas(comp["titular"], "Helvetica-Bold", T_DIAGNOSTICO,
                    W - 2 * MARGEN - 10 * mm)
        if n > 3:
            avisos.append(f"titular de competencia ocupa {n} lineas (caben 3)")
    return avisos


def main() -> int:
    ap = argparse.ArgumentParser(description="Genera el informe Audit Inmob Web F en HTML y PDF")
    ap.add_argument("jsonfile", type=Path)
    ap.add_argument("--outdir", type=Path, default=Path("."))
    args = ap.parse_args()

    data = json.loads(args.jsonfile.read_text(encoding="utf-8"))
    for aviso in validar(data):
        print(f"  aviso: {aviso}")

    args.outdir.mkdir(parents=True, exist_ok=True)
    slug = data.get("slug", "sin-slug")
    base = f"Auditoria inmobiliaria {slug}"
    html_file, pdf_file = args.outdir / f"{base}.html", args.outdir / f"{base}.pdf"

    create_html(data, html_file)
    create_pdf(data, pdf_file)
    print(f"Generados:\n  {html_file}\n  {pdf_file}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
