#!/usr/bin/env python3
"""Compara el recon del cliente con el de sus competidores locales.

Uso:
    python compare.py cliente.json comp1.json comp2.json [comp3.json] \
        --nombres "Fincas Blanco,Finques Esplugues,Casavertia,BCN Sellers" \
        --out competencia.json

El primer JSON es SIEMPRE el cliente. Salida: el bloque "competencia" listo para
pegar en content.json. No inventa nada: cada fila sale de un campo del recon.
"""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from urllib.parse import urlparse

ZONA_RE = re.compile(r"/(zona|zonas|barrio|barrios|municipi|localidad|localidades|"
                     r"oficina|oficinas|delegacion|delegaciones|sede|donde-estamos)[s]?(/|$|-)", re.I)


def carga(ruta: Path) -> dict:
    d = json.loads(ruta.read_text(encoding="utf-8"))
    if "error" in d or "resumen" not in d:
        raise SystemExit(f"{ruta}: recon incompleto ({d.get('error', 'sin resumen')})")
    return d


def nombre_por_defecto(d: dict) -> str:
    host = urlparse(d["site"]["url_final"] or d["site"]["url_original"]).netloc
    return host.replace("www.", "").split(".")[0].title()


def paginas_zona(d: dict) -> int:
    """Cuenta URLs que parecen landings de zona. Heurística: verifícala a mano."""
    urls = set(d["home"]["links"]["internal"])
    for sm in d["site"].get("sitemaps", []):
        urls.update(sm.get("sample", []))
    return sum(1 for u in urls if ZONA_RE.search(urlparse(u).path))


def si_no(valor: bool) -> str:
    return "Sí" if valor else "No"


# cada criterio: (etiqueta, extractor -> (texto, numero_o_None), mayor_es_mejor)
CRITERIOS = [
    ("Página de captación de propietarios",
     lambda d: (si_no(d["resumen"]["hay_formulario_captacion"]),
                1 if d["resumen"]["hay_formulario_captacion"] else 0), True),
    ("Buscador de inmuebles propio",
     lambda d: (si_no(d["resumen"]["hay_buscador"]),
                1 if d["resumen"]["hay_buscador"] else 0), True),
    ("Ficha de negocio en datos estructurados",
     lambda d: (si_no(any(t in d["resumen"]["schema_types"]
                          for t in ("RealEstateAgent", "LocalBusiness", "Organization"))),
                1 if any(t in d["resumen"]["schema_types"]
                         for t in ("RealEstateAgent", "LocalBusiness", "Organization")) else 0), True),
    ("Reseñas marcadas para Google",
     lambda d: (si_no(d["home"]["schema"]["has_rating"]),
                1 if d["home"]["schema"]["has_rating"] else 0), True),
    ("Páginas propias por zona",
     lambda d: (str(paginas_zona(d)), paginas_zona(d)), True),
    ("Páginas indexables en sitemap",
     lambda d: ((f"{d['site'].get('sitemap_total_urls', 0):,}".replace(",", ".")
                 if d["site"].get("sitemap_total_urls") else "0"),
                d["site"].get("sitemap_total_urls", 0)), True),
    ("Velocidad media de respuesta",
     lambda d: (f"{d['resumen']['tiempo_medio_ms']} ms",
                d["resumen"]["tiempo_medio_ms"]), False),
    ("Peso de las fotos de portada",
     lambda d: (f"{round(d['site']['peso_imagenes_home']['peso_total_kb'])} KB",
                d["site"]["peso_imagenes_home"]["peso_total_kb"]), False),
    ("Redes sociales enlazadas",
     lambda d: (str(len(d["resumen"]["redes"])), len(d["resumen"]["redes"])), True),
    ("Certificado y seguridad correctos",
     lambda d: (si_no(d["site"]["certificado_ssl_valido"]),
                1 if d["site"]["certificado_ssl_valido"] else 0), True),
]


def estado(valor, valores: list, mayor_mejor: bool) -> str:
    """bien / regular / mal según la posición frente a los demás."""
    utiles = [v for v in valores if v is not None]
    if valor is None or not utiles:
        return "regular"
    mejor, peor = (max(utiles), min(utiles)) if mayor_mejor else (min(utiles), max(utiles))
    if mejor == peor:
        return "bien" if (valor > 0 if mayor_mejor else True) else "mal"
    if valor == mejor:
        return "bien"
    if valor == peor:
        return "mal"
    return "regular"


def construir(recons: list[dict], nombres: list[str]) -> dict:
    filas = []
    for etiqueta, extractor, mayor_mejor in CRITERIOS:
        textos, numeros = [], []
        for d in recons:
            try:
                t, n = extractor(d)
            except Exception:
                t, n = "?", None
            textos.append(t)
            numeros.append(n)
        filas.append({
            "criterio": etiqueta,
            "valores": textos,
            "estados": [estado(n, numeros, mayor_mejor) for n in numeros],
        })

    # dónde pierde el cliente: son los argumentos del informe
    pierde = [f["criterio"] for f in filas if f["estados"][0] == "mal"]
    gana = [f["criterio"] for f in filas if f["estados"][0] == "bien"]

    return {
        "columnas": nombres,
        "filas": filas,
        "_pierde_en": pierde,
        "_gana_en": gana,
        "titular": "",
        "_nota_titular": "Escribe aquí la conclusión en una frase, apoyada en _pierde_en.",
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="Compara recons: el primero es el cliente")
    ap.add_argument("recons", nargs="+", type=Path)
    ap.add_argument("--nombres", default="", help="nombres separados por coma")
    ap.add_argument("--out", type=Path, default=Path("competencia.json"))
    args = ap.parse_args()

    if len(args.recons) < 2:
        raise SystemExit("Hacen falta al menos el cliente y un competidor")
    if len(args.recons) > 4:
        raise SystemExit("Máximo cliente + 3 competidores (no cabe más en la página)")

    recons = [carga(r) for r in args.recons]
    nombres = [n.strip() for n in args.nombres.split(",") if n.strip()]
    while len(nombres) < len(recons):
        nombres.append(nombre_por_defecto(recons[len(nombres)]))

    datos = construir(recons, nombres[:len(recons)])
    args.out.write_text(json.dumps(datos, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"Comparativa guardada en {args.out}")
    print(f"  columnas: {', '.join(datos['columnas'])}")
    print(f"  el cliente PIERDE en: {', '.join(datos['_pierde_en']) or 'nada'}")
    print(f"  el cliente GANA en:   {', '.join(datos['_gana_en']) or 'nada'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
