"""Human labels for Meteocat coded variables (render-side only).

Sources, pinned in docs/notes/meteocat-pronostic-semantics.md: `cel` comes
from the official referencia/v1/simbols catalog (recorded fixture,
2026-07-17); the ordinal tables (probabilitat 1–5, acumulacio(-Neu) 1–6
BINS — never cm/mm amounts) come from the official API docs. An unknown
code must render as the raw code, never crash (codes may grow in winter).
"""

CEL = {
    1: "Cel serè", 2: "Núvols alts", 3: "Entre poc i mig ennuvolat",
    4: "Molt ennuvolat", 5: "Núvols mitjans amb plugims", 6: "Pluja",
    7: "Xàfec", 8: "Tempesta", 9: "Tempesta amb calamarsa", 10: "Nevada",
    11: "Boira", 12: "Boirina", 13: "Xàfec de neu",
    20: "Entre mig i molt ennuvolat", 21: "Ennuvolat", 22: "Calitja",
    23: "Mig ennuvolat amb ruixats", 24: "Xàfec amb tempesta",
    25: "Xàfec amb tempesta i calamarsa", 26: "Ruixat de neu",
    27: "Neu feble", 28: "Xàfec de neu", 29: "Xàfec d'aiguaneu",
    30: "Aiguaneu", 31: "Molt ennuvolat amb ruixats",
    32: "Ennuvolat amb plugims",
}

# Probability of precipitation, ordinal 1–5 (official docs table)
PROBABILITAT = {1: "No se n'espera", 2: "<10%", 3: "10–30%", 4: "30–70%", 5: ">70%"}

# 24h accumulation BINS (rain, mm), ordinal 1–6
ACUMULACIO = {1: "No se n'espera", 2: "0,1–5 mm", 3: "5–20 mm",
              4: "20–50 mm", 5: "50–100 mm", 6: ">100 mm"}

# 24h snow accumulation BINS (cm), ordinal 1–6
ACUMULACIO_NEU = {1: "No se n'espera", 2: "<2 cm", 3: "2–5 cm",
                  4: "5–10 cm", 5: "10–40 cm", 6: ">40 cm"}

# Display names for the rendered zones (the API truncates its own `nom` at
# ~25 chars; these are the official map names, confirmed 2026-07-17).
ZONE_DISPLAY_NAMES = {
    1: "Vessant nord Pirineu occidental",
    5: "Vessant sud Pirineu occidental",
    6: "Prepirineu oriental",
}

MISSING = "—"


def label(table: dict[int, str], code) -> str:
    """Label for a coded value: None → em dash, unknown code → the raw code."""
    if code is None:
        return MISSING
    try:
        return table.get(int(code), str(int(code)))
    except (TypeError, ValueError):
        return str(code)
