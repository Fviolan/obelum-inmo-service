"""El semaforo tiene que ser creible: sin verdes, nadie se cree los rojos."""


def revisar_credibilidad(contenido, permitido):
    """Un semáforo todo en rojo no es un diagnóstico, es un argumentario.

    El verde es lo que hace creíble al rojo: si el cliente ve que reconoces lo
    que hace bien, se cree lo que dices que hace mal. Y aquí hay datos para
    ponerlo: la comparativa dice en qué gana.
    """
    estados = [(a.get("status") or "")[:1] for a in contenido.get("areas", [])]
    gana = (permitido.get("comparativa_con_rivales") or {}).get("gana_en") or []
    avisos = []
    if estados and "g" not in estados:
        avisos.append(
            "ninguna de las 6 áreas está en verde. Según la comparativa este "
            f"cliente GANA a sus rivales en: {', '.join(gana) or 'algún punto'}. "
            "Pon en verde (g) el área correspondiente y ajusta su texto: un "
            "semáforo entero en rojo no se cree nadie")
    if estados.count("r") >= 6:
        avisos.append("las 6 áreas están en crítico: baja a mejorable (a) las que "
                      "tengan algún punto a favor en los datos medidos")
    return avisos
