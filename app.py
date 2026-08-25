import io
import pandas as pd
from pulp import PULP_CBC_CMD, LpInteger, LpMinimize, LpProblem, LpStatus, lpSum, LpVariable
import requests
import streamlit as st

st.set_page_config(page_title="Kalkulator Blendów", layout="wide")

st.title("🍹 Kalkulator Blendów")
st.markdown("---")

gdrive_id = "1t6ssjST2jEFoFRvM5OIMgebzV7HMYwJv"

# Krok inkrementu dla zbiorników (kg)
STEP_KG = 100.0


@st.cache_data(ttl=60)
def pobierz_dane(file_id):
  url = f"https://drive.google.com/uc?export=download&id={file_id}"
  response = requests.get(url)
  response.raise_for_status()
  return pd.read_excel(io.BytesIO(response.content), engine="openpyxl")


# Pobieramy wstępnie dane do tabeli wyboru zbiorników
try:
  df_raw = pobierz_dane(gdrive_id)
  df_raw.columns = df_raw.columns.str.strip()
  lista_zbiornikow = (
      df_raw["Zbiornik"].dropna().astype(str).str.strip().unique().tolist()
  )
except Exception:
  lista_zbiornikow = []

# Sidebar - Parametry wejściowe
st.sidebar.header("⚙️ Parametry Docelowe")

# Tworzymy DataFrame dla tabelki z checkboxami
df_selekcja_init = pd.DataFrame({
    "Zbiornik": lista_zbiornikow,
    "Dostępny": [True] * len(lista_zbiornikow)  # Domyślnie wszystkie zaznaczone
})

st.sidebar.subheader("📋 Dostępność zbiorników")
# Interaktywna tabela z ptaszkami (checkboxy)
df_selekcja = st.sidebar.data_editor(
    df_selekcja_init,
    column_config={
        "Dostępny": st.column_config.CheckboxColumn(
            "Użyj",
            default=True,
        ),
        "Zbiornik": st.column_config.TextColumn("Zbiornik", disabled=True),
    },
    disabled=["Zbiornik"],
    hide_index=True,
)

# Filtrujemy listę tylko do tych z zaznaczonym ptaszkiem
wybrane_zbiorniki = df_selekcja[df_selekcja["Dostępny"] == True]["Zbiornik"].tolist()

# Docelowa masa dotyczy wyłącznie surowców (koncentratów)
docelowa_ilosc_koncentratu = st.sidebar.number_input(
    "Docelowa ilość [KG]", value=100000.0, step=STEP_KG
)
docelowy_brix = st.sidebar.number_input(
    "Min. Brix [°Bx]", value=70.0, step=0.1
)

max_uzytych_tankow = st.sidebar.number_input(
    "Maksymalna liczba zbiorników", value=3, min_value=1, max_value=40, step=1
)

st.sidebar.subheader("Kwasowość")
kwas_jednostka = st.sidebar.radio(
    "Jednostka Kwasowości", ["CA", "MA"], horizontal=True
)
col_k1, col_k2 = st.sidebar.columns(2)
kwas_min = col_k1.number_input("Kwas MIN", value=2.2, step=0.01)
kwas_max = col_k2.number_input("Kwas MAX", value=2.4, step=0.01)

st.sidebar.subheader("Barwa")
barwa_jednostka = st.sidebar.radio(
    "Jednostka Barwy", ["T", "A"], horizontal=True
)
col_b1, col_b2 = st.sidebar.columns(2)
barwa_min = col_b1.number_input(
    "Barwa MIN",
    value=40.0 if barwa_jednostka == "T" else 0.40,
    step=0.5 if barwa_jednostka == "T" else 0.01,
)
barwa_max = col_b2.number_input(
    "Barwa MAX",
    value=50.0 if barwa_jednostka == "T" else 0.50,
    step=0.5 if barwa_jednostka == "T" else 0.01,
)

pozwol_na_wode = st.sidebar.checkbox(
    "Zbijanie Brixa wodą", value=True
)

if st.sidebar.button("🚀 OBLICZ BLENDY", type="primary"):
  try:
    df = pobierz_dane(gdrive_id)
    df.columns = df.columns.str.strip()

    KOLUMNA_KWAS = f"Kwasowość ({kwas_jednostka})"
    KOLUMNA_BARWA = f"Barwa ({barwa_jednostka})"

    kolumny_numeryczne = [
        "Ilość (KG)",
        "Zablokowana Ilość",
        "Brix",
        "Kwasowość (MA)",
        "Kwasowość (CA)",
        "Barwa (T)",
        "Barwa (A)",
    ]
    for col in kolumny_numeryczne:
      if col in df.columns:
        df[col] = df[col].astype(str).str.replace(",", ".").str.strip()
        df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0)

    if "Zablokowana Ilość" not in df.columns:
      df["Zablokowana Ilość"] = 0.0

    df["Zbiornik"] = df["Zbiornik"].astype(str).str.strip()
    df["Dostępne_Netto"] = (
        df["Ilość (KG)"] - df["Zablokowana Ilość"]
    ).clip(lower=0)
    df = df.dropna(subset=["Zbiornik", KOLUMNA_KWAS, KOLUMNA_BARWA, "Brix"])
    df = df[df["Dostępne_Netto"] > 0]

    # FILTRACJA ZBIORNIKÓW ZGODNIE Z SELEKCJĄ Z TABELI Z CHECKBOXAMI
    df = df[df["Zbiornik"].isin(wybrane_zbiorniki)]

    if "Barwa (T)" in df.columns and df["Barwa (T)"].max() <= 1.0:
      df["Barwa (T)"] = df["Barwa (T)"] * 100

    if pozwol_na_wode:
      woda_df = pd.DataFrame([{
          "Zbiornik": "WODA (Dodatek)",
          "Ilość (KG)": 999999.0,
          "Zablokowana Ilość": 0.0,
          "Dostępne_Netto": 999999.0,
          "Brix": 0.0,
          "Kwasowość (MA)": 0.0,
          "Kwasowość (CA)": 0.0,
          "Barwa (T)": 100.0,
          "Barwa (A)": 0.0,
      }])
      df = pd.concat([df, woda_df], ignore_index=True)

    def licz_blend(podejscie):
      prob = LpProblem(f"Blend_{podejscie}", LpMinimize)
      v_vars = {} 
      y_vars = {} 
      n_vars = {}

      for idx, row in df.iterrows():
        t_id = str(row["Zbiornik"]).strip()
        max_kg = float(row["Dostępne_Netto"])
        
        y_vars[t_id] = LpVariable(f"y_{idx}", cat="Binary")

        if t_id == "WODA (Dodatek)":
          v_vars[t_id] = LpVariable(f"v_woda_{idx}", 0, max_kg)
          prob += v_vars[t_id] <= max_kg * y_vars[t_id]
        else:
          max_steps = int(max_kg // STEP_KG)
          n_vars[t_id] = LpVariable(f"n_{idx}", 0, max_steps, cat=LpInteger)
          v_vars[t_id] = n_vars[t_id] * STEP_KG
          prob += n_vars[t_id] <= max_steps * y_vars[t_id]

      # SZTYWNY BILANS MASY SAMYCH KONCENTRATÓW:
      prob += (
          lpSum([v_vars[t] for t in v_vars if t != "WODA (Dodatek)"])
          == docelowa_ilosc_koncentratu
      )

      # Limit liczby użytych tanków (bez wody)
      prob += (
          lpSum([y_vars[t] for t in y_vars if t != "WODA (Dodatek)"])
          <= max_uzytych_tankow
      )

      # LOGIKA BRIXA:
      masa_calkowita = lpSum([v_vars[t] for t in v_vars])

      if pozwol_na_wode:
        prob += (
            lpSum([
                v_vars[str(row["Zbiornik"]).strip()] * float(row["Brix"])
                for _, row in df.iterrows()
            ])
            == masa_calkowita * docelowy_brix
        )
      else:
        prob += (
            lpSum([
                v_vars[str(row["Zbiornik"]).strip()] * float(row["Brix"])
                for _, row in df.iterrows()
            ])
            >= docelowa_ilosc_koncentratu * docelowy_brix
        )

      # Bilans Kwasowości i Barwy odniesiony do MASY CAŁKOWITEJ
      prob += (
          lpSum([
              v_vars[str(row["Zbiornik"]).strip()] * float(row[KOLUMNA_KWAS])
              for _, row in df.iterrows()
          ])
          >= masa_calkowita * kwas_min
      )
      prob += (
          lpSum([
              v_vars[str(row["Zbiornik"]).strip()] * float(row[KOLUMNA_KWAS])
              for _, row in df.iterrows()
          ])
          <= masa_calkowita * kwas_max
      )

      prob += (
          lpSum([
              v_vars[str(row["Zbiornik"]).strip()] * float(row[KOLUMNA_BARWA])
              for _, row in df.iterrows()
          ])
          >= masa_calkowita * barwa_min
      )
      prob += (
          lpSum([
              v_vars[str(row["Zbiornik"]).strip()] * float(row[KOLUMNA_BARWA])
              for _, row in df.iterrows()
          ])
          <= masa_calkowita * barwa_max
      )

      suma_kwasu = lpSum([
          v_vars[str(row["Zbiornik"]).strip()] * float(row[KOLUMNA_KWAS])
          for _, row in df.iterrows()
      ])
      suma_barwy = lpSum([
          v_vars[str(row["Zbiornik"]).strip()] * float(row[KOLUMNA_BARWA])
          for _, row in df.iterrows()
      ])
      uzyte_tanki = lpSum(
          [y_vars[t] for t in y_vars if t != "WODA (Dodatek)"]
      )

      if podejscie == "min_kwas":
        prob += suma_kwasu * 1000 + uzyte_tanki * 10
      elif podejscie == "min_barwa":
        prob += suma_barwy * 1000 + uzyte_tanki * 10
      elif podejscie == "min_oba":
        prob += suma_kwasu * 10000 + suma_barwy * 1000 + uzyte_tanki * 10

      prob.solve(PULP_CBC_CMD(msg=0))
      if LpStatus[prob.status] != "Optimal":
        return None

      wyniki = []
      tot_mass = 0
      tot_brix = 0
      tot_kwas_ma = 0
      tot_kwas_ca = 0
      tot_barwa_t = 0
      tot_barwa_a = 0

      for _, row in df.iterrows():
        t = str(row["Zbiornik"]).strip()
        is_woda = (t == "WODA (Dodatek)")

        if is_woda:
          val = v_vars[t].varValue
        else:
          val = n_vars[t].varValue * STEP_KG if n_vars[t].varValue is not None else 0.0

        if val and val > 0.001:
          pobrano = round(val, 1) if is_woda else int(round(val))
          
          stan_aktualny = float(row["Ilość (KG)"])
          zablokowane = float(row["Zablokowana Ilość"])
          dostepne = float(row["Dostępne_Netto"])

          pozostanie = 0.0 if is_woda else round(dostepne - pobrano, 1)

          wyniki.append({
              "Zbiornik": t,
              "Pobrano [KG]": pobrano,
              "Stan Aktualny [KG]": "—" if is_woda else stan_aktualny,
              "Zablokowane [KG]": "—" if is_woda else zablokowane,
              "Dostępne [KG]": "—" if is_woda else dostepne,
              "Pozostanie [KG]": "—" if is_woda else pozostanie,
              "Brix [°Bx]": float(row["Brix"]),
              "Kwas MA": (
                  float(row["Kwasowość (MA)"])
                  if "Kwasowość (MA)" in df.columns
                  else None
              ),
              "Kwas CA": (
                  float(row["Kwasowość (CA)"])
                  if "Kwasowość (CA)" in df.columns
                  else None
              ),
              "Barwa T [%]": (
                  float(row["Barwa (T)"])
                  if "Barwa (T)" in df.columns
                  else None
              ),
              "Barwa A": (
                  float(row["Barwa (A)"])
                  if "Barwa (A)" in df.columns
                  else None
              ),
          })

          tot_mass += val
          tot_brix += val * float(row["Brix"])
          if "Kwasowość (MA)" in df.columns:
            tot_kwas_ma += val * float(row["Kwasowość (MA)"])
          if "Kwasowość (CA)" in df.columns:
            tot_kwas_ca += val * float(row["Kwasowość (CA)"])
          if "Barwa (T)" in df.columns:
            tot_barwa_t += val * float(row["Barwa (T)"])
          if "Barwa (A)" in df.columns:
            tot_barwa_a += val * float(row["Barwa (A)"])

      return {
          "sklad": pd.DataFrame(wyniki),
          "tot_mass": round(tot_mass, 1),
          "brix": round(tot_brix / tot_mass, 2),
          "kwas_ma": (
              round(tot_kwas_ma / tot_mass, 2)
              if "Kwasowość (MA)" in df.columns
              else None
          ),
          "kwas_ca": (
              round(tot_kwas_ca / tot_mass, 2)
              if "Kwasowość (CA)" in df.columns
              else None
          ),
          "barwa_t": (
              round(tot_barwa_t / tot_mass, 2)
              if "Barwa (T)" in df.columns
              else None
          ),
          "barwa_a": (
              round(tot_barwa_a / tot_mass, 3)
              if "Barwa (A)" in df.columns
              else None
          ),
          "uzyte_tanki": len(
              [x for x in wyniki if x["Zbiornik"] != "WODA (Dodatek)"]
          ),
      }

    tab1, tab2, tab3 = st.tabs([
        "📉 1. Najniższa Kwasowość",
        "🎨 2. Najniższa Barwa",
        "⚖️ 3. Najniższa Kwasowość + Barwa",
    ])

    warianty_map = {
        tab1: ("min_kwas", "Wariant z najniższą możliwą kwasowością"),
        tab2: ("min_barwa", "Wariant z najniższym/najjaśniejszym kolorem"),
        tab3: (
            "min_oba",
            "Wariant ze zrównoważonym najniższym kwasem i barwą",
        ),
    }

    for tab, (kod, opisy) in warianty_map.items():
      with tab:
        res = licz_blend(kod)
        if res:
          st.success(f"{opisy} | Użyte tanki: {res['uzyte_tanki']}")
          col1, col2, col3, col4 = st.columns(4)
          col1.metric("Masa Całkowita (z wodą)", f"{res['tot_mass']:.1f} KG")
          col2.metric("Wynikowy Brix", f"{res['brix']} °Bx")

          kwas_val = (
              f"{res['kwas_ca']} CA"
              if kwas_jednostka == "CA"
              else f"{res['kwas_ma']} MA"
          )
          col3.metric("Wynikowa Kwasowość", kwas_val)

          barwa_val = (
              f"{res['barwa_t']}% T"
              if barwa_jednostka == "T"
              else f"{res['barwa_a']} A"
          )
          col4.metric("Wynikowa Barwa", barwa_val)

          st.dataframe(res["sklad"], use_container_width=True)
        else:
          st.error(f"Brak możliwości ułożenia blendu w podanych zakresach. Sprawdź zaznaczone zbiorniki lub poszerz parametry.")

  except Exception as e:
    st.error(f"Błąd przetwarzania: {e}")
