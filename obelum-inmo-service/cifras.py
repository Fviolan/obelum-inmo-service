"""Detecta cifras inventadas en el informe.

El modelo rellena con estadisticas de sector que suenan bien pero no ha medido
nadie (el 70% de las consultas empiezan por WhatsApp, la media del sector...).
En un informe que se entrega al cliente eso es exactamente lo que no puede pasar:
basta con que una sea falsa para tumbar la credibilidad del resto.

Regla: toda cifra del informe tiene que aparecer en los datos medidos. Se
exceptuan los KPIs, que son proyecciones declaradas como tales.
"""
import json
import re
import sys

sys.stdout.reconfigure(encoding="utf-8")


def numeros(texto):
    """Numeros con significado, ignorando los de una cifra suelta."""
    return set(re.findall(r"\d+(?:[.,]\d+)?", str(texto)))


def normaliza(n):
    return n.replace(",", ".").rstrip("0").rstrip(".")


def revisar_cifras(contenido: dict, medidos: dict) -> list[str]:
    """Cifras del informe que no aparecen en los datos medidos."""
    permitidos = set()
    for v in json.dumps(medidos, ensure_ascii=False).replace('"', " ").split():
        permitidos |= numeros(v)
    permitidos = {normaliza(n) for n in permitidos}
    # los milisegundos suelen citarse en segundos y al reves
    extra = set()
    for n in list(permitidos):
        try:
            f = float(n)
            extra |= {normaliza(f"{f / 1000:.1f}"), normaliza(f"{f * 1000:.0f}"),
                      normaliza(f"{f:.0f}"), normaliza(f"{f / 1024:.1f}")}
        except ValueError:
            pass
    permitidos |= extra

    sospechosas = []
    campos = []
    for i, a in enumerate(contenido.get("areas", []), 1):
        campos.append((f"area {i} badge", a.get("badge")))
        campos.append((f"area {i} desc", a.get("desc")))
    for i, b in enumerate(contenido.get("blockers", []), 1):
        campos.append((f"bloqueante {i}", f"{b.get('title')} {b.get('desc')}"))
    for i, a in enumerate(contenido.get("actions", []), 1):
        campos.append((f"accion {i}", f"{a.get('title')} {a.get('desc')}"))
    for campo in ("antes", "despues"):
        for i, t in enumerate(contenido.get(campo, []), 1):
            campos.append((f"{campo} {i}", t))
    campos.append(("diagnostico", contenido.get("diagnostico")))
    campos.append(("rule", contenido.get("rule")))
    campos.append(("titular", contenido.get("titular_competencia")
                   or (contenido.get("competencia") or {}).get("titular")))

    for nombre, texto in campos:
        if not texto:
            continue
        for n in numeros(texto):
            if normaliza(n) not in permitidos:
                frase = re.search(r"[^.]*\b" + re.escape(n) + r"\b[^.]*", str(texto))
                sospechosas.append(f"{nombre}: cifra {n} no medida "
                                   f"-> {(frase.group(0) if frase else '')[:80].strip()}")
    return sospechosas


if __name__ == "__main__":
    import glob
    for f in sorted(glob.glob("*-content.json")):
        d = json.load(open(f, encoding="utf-8"))
        medidos = json.load(open(f.replace("-content", "-medidos"), encoding="utf-8")) \
            if glob.glob(f.replace("-content", "-medidos")) else {}
        av = revisar_cifras(d, medidos)
        print(f"\n{d['cliente']}: {len(av)} cifras sospechosas")
        for a in av:
            print("   -", a)
