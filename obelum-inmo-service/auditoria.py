"""Auditoria completa de un lead: del recon al PDF y al email, en una llamada.

Traslada la logica ya probada en `herramientas-inmob/informe.py` y `redactar.py`,
que se validaron contra leads reales de la base. Aqui no se inventa nada nuevo:
se mueve al servicio para que n8n solo tenga que orquestar.

Lo que vive aqui y por que:
  - los dos prompts de sistema, verbatim
  - el bucle de correccion: si /validar protesta, el JSON vuelve al modelo
  - la rotacion estable de B/D/E, porque dejar elegir al modelo escoraba el A/B
  - los reintentos ante `content: null` y ante JSON invalido, que en produccion
    tumbarian el lead con un mensaje de error que no dice nada
"""
from __future__ import annotations

import json
import logging
import os
import re
import time

import requests

log = logging.getLogger("obelum-inmo.auditoria")

MODELO = os.getenv("MODELO_LLM", "deepseek/deepseek-v4-flash")
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY", "").strip()
OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
# on = el modelo razona antes de escribir (por defecto): 4 veces mas lento y 3
# veces mas caro, pero respeta los limites de caracteres y no deja textos
# cortados en el PDF. Medido: con razonamiento 0 avisos, sin el 5 badges cortados.
# Las auditorias se acumulan en segundo plano, asi que el reloj no aprieta.
# off | low | medium | high | on
RAZONAMIENTO = os.getenv("RAZONAMIENTO", "on").strip().lower()

# precios de deepseek-v4-flash, dolares por millon de tokens
PRECIO_PROMPT, PRECIO_COMPLETION = 0.08316, 0.16632


# --- los bloques fijos del email: son la voz de Francesc, el modelo no los toca

P2 = ("Te adjunto la auditoría completa, gratis, con el plan de las acciones que más "
      "impacto tendrían y en qué orden hacerlas.")
P3 = ("Ayudo a negocios como el tuyo a cerrar estos huecos y, cuando tiene sentido, a "
      "meter IA donde de verdad ahorra tiempo o vende más, sin humo ni nada que no "
      "puedas medir.")
P4 = ("Contesta con un «vamos» y te cuento en cuál de las 6 etapas que tiene toda "
      "empresa está el cuello de botella que te está frenando.")
FIRMA = "Francesc"

# los asuntos no llevan el nombre de la inmobiliaria: el movil corta a los 40
PLANTILLAS = {
    "B": "Hay una parte de tu negocio que te cuesta más que las otras cinco juntas",
    "D": "Miré tu web 4 minutos y encontré algo raro",
    "E": "¿Sabes cuál es tu cuello de botella?",
}


SISTEMA_INFORME = """Eres un auditor web especializado en inmobiliarias que escribe para el
dueño de la agencia, no para un técnico. Tiene diez minutos y quiere saber qué le
está costando dinero y qué hace el lunes.

REGLAS INNEGOCIABLES
1. NUNCA uses comillas dobles ("). Si necesitas entrecomillar, usa « ». Tu respuesta
   viaja dentro de un JSON y las comillas dobles lo romperian.
2. Español con TODAS las tildes y signos correctos (á é í ó ú ñ ¿ ¡). Ortografía
   impecable: este informe lo lee un cliente y las faltas destruyen la credibilidad.
3. Trata al lector de tú, en singular. Nunca de vosotros.
4. Solo puedes afirmar lo que esté en los DATOS MEDIDOS. Cada frase con su cifra.
   Si un dato no aparece, no existe: no lo inventes ni lo estimes.
   PROHIBIDO citar estadísticas de sector, medias del mercado o porcentajes de
   comportamiento de usuarios. Nada de «el 70% de las consultas empiezan por
   WhatsApp» ni «la media del sector carga en 2 segundos»: eso no lo has medido
   y basta una cifra falsa para tumbar la credibilidad del informe entero.
   Las ÚNICAS cifras permitidas son las de DATOS MEDIDOS y las de la comparativa.
   En los KPIs sí puedes proyectar, porque se presentan como estimación.
5. Traduce siempre lo técnico a consecuencia de negocio. En vez de «no hay schema»,
   escribe «Google no sabe que esto es una inmobiliaria: no sales en el mapa».
6. NO todo puede estar en rojo. Busca de verdad lo que la web hace bien y ponlo en
   verde: el verde es lo que hace creíble al rojo. Si las seis áreas salen críticas,
   el informe parece un argumentario de venta y pierde credibilidad.
7. BREVEDAD: el informe es una maqueta con huecos fijos. Un texto que no cabe se
   corta a media palabra. Respeta los límites al pie de la letra.
8. VELOCIDAD, criterio exacto para el área 6 (Salud técnica y velocidad): un
   tiempo_carga por debajo de 2.2 segundos es correcto, NUNCA lo llames lento ni
   crítico, aunque el resto del área tenga fallos (esos fallos van por su propia
   señal, no por la velocidad). Entre 2.2 y 3.5 s es mejorable. Solo por encima de
   3.5 s es un problema real de velocidad. Y no mezcles tiempo_carga con el peso de
   las imágenes de portada (peso_portada_MB o similar) en la misma frase como si
   fueran el mismo hecho: son dos medidas independientes (una es solo la descarga
   del HTML, la otra el peso de las fotos que se cargan aparte) y juntarlas suena
   contradictorio («la portada pesa 9 MB y carga en 900 ms» no se entiende).
   Cuéntalas siempre por separado.

DEVUELVE SOLO UN JSON con esta forma exacta, sin texto alrededor ni bloques de codigo:
{
 "areas": [6 objetos: {"name","eje","status","badge","desc"}],
 "blockers": [de 0 a 3 objetos: {"title","desc"}],
 "diagnostico": "una sola frase de negocio, la conclusion que el dueno repetiria",
 "actions": [10 objetos: {"title","prio","desc"}],
 "antes": [7 frases], "despues": [7 frases],
 "kpis": [4 objetos: {"big","lab"}],
 "rule": "que 4 acciones concentran el retorno",
 "titular_competencia": "una frase con lo que los rivales ya tienen y este cliente no"
}

Las 6 áreas son SIEMPRE estas, en este orden, con su eje:
 1 Captación de propietarios (Marketing)
 2 SEO local por zona (SEO)
 3 Fichas y buscador (UX + SEO)
 4 Confianza y prueba social (Marketing)
 5 Conversión y contacto (UX)
 6 Salud técnica y velocidad (UX técnico)
status: r crítico, a mejorable, g correcto.
prio de cada acción: alta, media o baja. Recomendado 4 altas.
Las acciones dicen QUÉ HACER, en infinitivo, no qué está mal.
antes[i] y despues[i] van emparejados: el punto i de despues resuelve el i de antes.

LÍMITES DE CARACTERES, son huecos físicos y no admiten excusas:
 badge de área ........ 52    desc de área ......... 165
 title de acción ...... 38    desc de acción ....... 110
 cada antes / despues . 78    diagnóstico .......... 185
 titular_competencia .. 185   desc de bloqueante ... 70
 kpis.big ............. 5     kpis.lab ............. 30
 title de bloqueante .. 28"""


SISTEMA_EMAIL = """Eres Francesc, de Obelum Labs. Escribes el primer email en frío a una
inmobiliaria a la que acabas de auditar la web.

REGLAS INNEGOCIABLES
1. NUNCA uses comillas dobles (") en ningún texto. Si necesitas entrecomillar, usa « ».
   Esto es obligatorio: tu respuesta viaja dentro de un JSON y las rompería.
2. Escribe en español con TODAS las tildes y signos correctos (á é í ó ú ñ ¿ ¡).
   Las tildes son obligatorias, la ortografía impecable.
3. Solo puedes afirmar datos que aparezcan en los DATOS MEDIDOS. No inventes cifras,
   ni servicios, ni el tamaño de la empresa. Si un dato no está, no existe.
4. Trata al lector SIEMPRE de tú, en singular. Nunca de vosotros. Escribe tu web,
   no tenéis, no tienes, te cuesta. Prohibido: tenéis, vuestra, vuestro, sois, os.
5. Nada de emojis, ni mayúsculas de grito, ni signos de exclamación.
6. No le atribuyas pensamientos ni sentimientos a nadie. No escribas que el visitante
   piensa, cree, siente o percibe algo, ni que le pareces poco serio: eso no se ha
   medido y suena a insulto. Describe lo que pasa, no lo que alguien opina.
7. Prohibidas las frases de relleno que no significan nada. Cada frase tiene que poder
   discutirse con un dato delante.
8. VELOCIDAD: un tiempo_carga por debajo de 2.2 segundos es correcto. NUNCA digas que es
   lento, que tarda demasiado o que está por encima de lo recomendado si el dato es menor
   de 2.2 s, aunque el resto de la web tenga fallos. Y no mezcles tiempo_carga con el peso
   de las imágenes (peso_portada_MB o similar) en la misma frase como si fueran el mismo
   hecho: son dos medidas independientes. Si la web es rápida pero las fotos pesan mucho,
   dilo así: rápida de servidor, pesada de fotos — nunca "pesa X MB y tarda Y segundos,
   muy por encima de lo recomendado" cuando Y es menor de 2.2.

ESTRUCTURA DEL CUERPO
Párrafo 1 (el único que escribes tú, 3 o 4 frases): empieza con He revisado <dominio>
y cuenta el hallazgo principal con su cifra concreta, más uno o dos hallazgos de apoyo.
La última frase dice qué se pierde, en concreto y contable: encargos de venta, visitas,
contactos o llamadas. Nada de perder oportunidades ni de perder clientes potenciales
en abstracto: di qué se pierde y por qué mecanismo.
Después van tres párrafos fijos y la firma, que se añaden solos: no los escribas.

ASUNTO
Si te doy un asunto, devuélvelo tal cual, sin tocar ni una coma.
Si no te lo doy, elige la variante que mejor encaje con el hallazgo y usa su plantilla.
El asunto NUNCA lleva el nombre de la inmobiliaria: el móvil corta a los 40 caracteres.

RESPONDE SOLO CON UN JSON, sin texto alrededor y sin bloques de código:
{tipo_e1: la letra, asunto: el asunto, parrafo1: tu párrafo}"""


# --- llamada al modelo ------------------------------------------------------

class GastoLLM:
    """Cuenta TODAS las llamadas, incluidos reintentos y correcciones: son las
    que disparan el coste real por auditoria."""

    def __init__(self):
        self.llamadas = 0
        self.prompt = 0
        self.completion = 0

    def anotar(self, uso: dict) -> None:
        self.llamadas += 1
        self.prompt += (uso or {}).get("prompt_tokens", 0) or 0
        self.completion += (uso or {}).get("completion_tokens", 0) or 0

    @property
    def dolares(self) -> float:
        return round((self.prompt * PRECIO_PROMPT
                      + self.completion * PRECIO_COMPLETION) / 1_000_000, 5)

    def resumen(self) -> dict:
        return {"llamadas": self.llamadas, "prompt_tokens": self.prompt,
                "completion_tokens": self.completion, "coste_usd": self.dolares}


def pedir_json(sistema: str, prompt: str, gasto: GastoLLM,
               max_tokens: int = 16000, temperatura: float = 0.6) -> dict:
    """Pide un JSON al modelo, con reintentos.

    Dos fallos reales que hay que absorber:
      - `content: null` con `finish_reason: length`: el modelo gasta el
        presupuesto razonando y no llega a escribir. Parece un fallo de red.
      - JSON invalido: una coma de mas o una cadena cortada.
    Reintentar sale mucho mas barato que perder el lead.
    """
    if not OPENROUTER_API_KEY:
        raise RuntimeError("falta OPENROUTER_API_KEY en el entorno del servicio")

    cuerpo = {"model": MODELO,
              "messages": [{"role": "system", "content": sistema},
                           {"role": "user", "content": prompt}],
              "temperature": temperatura, "max_tokens": max_tokens}

    # DeepSeek v4 flash razona antes de escribir, y ese razonamiento se paga como
    # salida (al doble) y se nota en el reloj: medido, 24.546 tokens de salida
    # para un JSON de menos de 2.000. Apagarlo lo deja en cero.
    if RAZONAMIENTO == "off":
        cuerpo["reasoning"] = {"enabled": False}
    elif RAZONAMIENTO in ("low", "medium", "high"):
        cuerpo["reasoning"] = {"effort": RAZONAMIENTO}

    ultimo = ""
    for intento in (1, 2, 3):
        r = requests.post(OPENROUTER_URL,
                          headers={"Authorization": f"Bearer {OPENROUTER_API_KEY}",
                                   "Content-Type": "application/json"},
                          json=cuerpo, timeout=300)
        r.raise_for_status()
        d = r.json()
        eleccion = (d.get("choices") or [{}])[0]
        texto = (eleccion.get("message") or {}).get("content")
        fin = eleccion.get("finish_reason")
        gasto.anotar(d.get("usage"))

        if texto and texto.strip():
            crudo = re.sub(r"^```(json)?|```$", "", texto.strip(), flags=re.M).strip()
            try:
                return json.loads(crudo)
            except json.JSONDecodeError as exc:
                ultimo = f"JSON invalido: {exc}"
                log.warning("intento %s: %s", intento, ultimo)
        else:
            ultimo = f"contenido vacio (finish_reason={fin})"
            log.warning("intento %s: %s", intento, ultimo)
        time.sleep(2)

    raise RuntimeError(f"el modelo no devolvio un JSON valido en 3 intentos: {ultimo}")


def corregir(sistema: str, contenido: dict, instruccion: str, detalle: list,
             gasto: GastoLLM) -> dict:
    """Pide una correccion. Si falla, se queda la version anterior: un informe
    con un texto largo es mejor que ningun informe."""
    try:
        return pedir_json(
            sistema,
            instruccion + "\n\n" + "\n".join(f"- {d}" for d in detalle)
            + "\n\nTU JSON:\n" + json.dumps(contenido, ensure_ascii=False),
            gasto)
    except Exception as exc:
        log.warning("correccion fallida (%s), me quedo la anterior",
                    type(exc).__name__)
        return contenido


# --- variante del asunto ----------------------------------------------------

def variante_rotada(web: str) -> str:
    """Reparte B, D y E de forma estable y equilibrada.

    Dejar elegir al modelo escoraba el reparto: en 6 tiradas eligio E cuatro
    veces, y con ese sesgo el A/B no se puede leer. Con el dominio como semilla
    cada lead cae siempre en la misma variante.
    """
    opciones = ["B", "D", "E"]
    return opciones[sum(ord(c) for c in (web or "")) % 3]


def limpiar_comillas(txt: str) -> str:
    """Ultima red: si el modelo cuela comillas dobles, se cambian por angulares."""
    if not txt or '"' not in txt:
        return txt or ""
    partes = txt.split('"')
    salida = partes[0]
    for i, p in enumerate(partes[1:]):
        salida += ("«" if i % 2 == 0 else "»") + p
    return salida


def montar_cuerpo(parrafo1: str) -> str:
    """El parrafo del modelo mas los tres bloques fijos y la firma."""
    return "\n\n".join([limpiar_comillas(parrafo1).strip(), P2, P3, P4, FIRMA])


# --- el informe -------------------------------------------------------------

def generar_informe(permitido: dict, gasto: GastoLLM, validar) -> tuple[dict, list]:
    """Devuelve (contenido del informe, avisos que quedaron sin resolver).

    `validar(contenido)` es la funcion del servicio que agrupa las cuatro
    comprobaciones. Se le devuelven al modelo hasta dos veces; si algo persiste,
    se entrega el informe con el aviso anotado en vez de no entregar nada.
    """
    prompt = "DATOS MEDIDOS:\n" + json.dumps(permitido, ensure_ascii=False, indent=1)
    contenido = pedir_json(SISTEMA_INFORME, prompt, gasto)

    for vuelta in (1, 2):
        avisos = validar(contenido)
        if not avisos:
            return contenido, []
        log.info("informe: vuelta %s de correccion, %s avisos", vuelta, len(avisos))
        contenido = corregir(
            SISTEMA_INFORME, contenido,
            "Tu JSON tiene problemas. Corrige SOLO lo indicado, sin cambiar el "
            "significado ni tocar el resto. Devuelve el JSON entero otra vez, con "
            "la misma estructura.\n\nPROBLEMAS:",
            avisos, gasto)

    # tercera revision: la ultima correccion tambien se comprueba
    return contenido, validar(contenido)


# --- el email ---------------------------------------------------------------

def generar_email(recon: dict, comparativa: dict, asunto_fijado: str | None,
                  variante: str, gasto: GastoLLM) -> dict:
    """Devuelve {tipo_e1, asunto, parrafo1, cuerpo}.

    El asunto NUNCA sale del modelo: o viene de la regla (variantes A y C) o de
    la plantilla de la variante rotada. Al modelo se le da hecho y solo escribe
    el primer parrafo.
    """
    rc = recon.get("resumen_compacto") or {}
    h = recon.get("hallazgo") or {}
    asunto = asunto_fijado or PLANTILLAS[variante]

    datos = {
        "dominio": (recon.get("url_normalizada") or "").replace("https://", "").rstrip("/"),
        "nombre": recon.get("nombre"),
        "hallazgo_principal": h.get("hallazgo_principal"),
        "otros_hallazgos": [c.get("hallazgo") for c in (h.get("candidatos") or [])[1:4]],
        "peso_portada_MB": round((rc.get("peso_home_kb") or 0) / 1024, 2),
        "tiempo_respuesta_s": recon.get("tiempo_carga"),
        "tiene_captacion_propietarios": rc.get("captacion"),
        "tiene_buscador_propio": rc.get("buscador"),
        "tiene_whatsapp": rc.get("whatsapp"),
        "certificado_ok": rc.get("ssl_ok"),
        "paginas_en_sitemap": rc.get("sitemap_urls"),
        "imagenes_sin_alt": rc.get("img_sin_alt"),
        "redes_enlazadas": rc.get("redes"),
        "pierde_frente_a_la_competencia_en": comparativa.get("_pierde_en"),
        "gana_frente_a_la_competencia_en": comparativa.get("_gana_en"),
    }

    prompt = (f"DATOS MEDIDOS DE LA WEB:\n{json.dumps(datos, ensure_ascii=False, indent=1)}"
              f"\n\nASUNTO YA DECIDIDO (variante {variante}), devuélvelo literal, "
              f"sin cambiar ni una coma:\n{asunto}")

    salida = pedir_json(SISTEMA_EMAIL, prompt, gasto, max_tokens=8000,
                        temperatura=0.7)
    parrafo1 = limpiar_comillas(salida.get("parrafo1", ""))
    return {"tipo_e1": variante, "asunto": asunto, "parrafo1": parrafo1,
            "cuerpo": montar_cuerpo(parrafo1)}
