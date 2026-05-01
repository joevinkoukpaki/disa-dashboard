"""
DISA Pro Dashboard — Streamlit
Auteur : KOUKPAKI Cyrius — ESMER Bénin 2025-2026
Usage  : streamlit run dashboard_disa_streamlit.py
"""

import streamlit as st
import json, time, csv, os, threading, datetime, hashlib, math, random
from collections import deque
import paho.mqtt.client as mqtt

# ═══════════════════════════════════════════
# CONFIGURATION
# ═══════════════════════════════════════════
MQTT_BROKER   = "c57db5b59f8a4cad9713c5ba47edc1a3.s1.eu.hivemq.cloud"
MQTT_PORT     = 8883
MQTT_USERNAME = "Cyrius"
MQTT_PASSWORD = "@KOUDOSS18Mars"
MQTT_TOPIC    = "disa/supervision/data"
SERIAL_BAUD   = 115200
MAX_POINTS    = 360
CSV_FILE      = "disa_logs.csv"
DESIGNER_HASH = "fe8c04d7a7356d8927b04c002dc91bd2f0cd646c8b0e55ef4f9c8e2c13088d04"

# ═══════════════════════════════════════════
# ÉTAT PARTAGÉ (thread-safe via st.session_state)
# ═══════════════════════════════════════════
def init_state():
    defaults = {
        "history_ts":        deque(maxlen=MAX_POINTS),
        "history_soc":       deque(maxlen=MAX_POINTS),
        "history_voltage":   deque(maxlen=MAX_POINTS),
        "history_current":   deque(maxlen=MAX_POINTS),
        "history_power":     deque(maxlen=MAX_POINTS),
        "history_temp":      deque(maxlen=MAX_POINTS),
        "history_humid":     deque(maxlen=MAX_POINTS),
        "history_lux":       deque(maxlen=MAX_POINTS),
        "history_mode":      deque(maxlen=MAX_POINTS),
        "history_anomalies": deque(maxlen=MAX_POINTS),
        "alerts":            deque(maxlen=100),
        "last":              {},
        "conn_mode":         "disconnected",
        "conn_port":         "",
        "conn_topic":        MQTT_TOPIC,
        "total_measures":    0,
        "session_start":     datetime.datetime.now(),
        "last_ts":           0,
        "nonce":             "",
        "nonce_ts":          0,
        "auth_ok":           False,
        "auth_ts":           0,
        "mqtt_started":      False,
        "usb_started":       False,
        "page":              "📊 Tableau de bord",
        "hist_range":        60,
        "cmd_result":        "",
        "mqtt_topic":        MQTT_TOPIC,
    }
    for k, v in defaults.items():
        if k not in st.session_state:
            st.session_state[k] = v

# ═══════════════════════════════════════════
# TRAITEMENT DONNÉES
# ═══════════════════════════════════════════
def process_data(raw):
    try:
        d = json.loads(raw) if isinstance(raw, str) else raw
        if not isinstance(d, dict): return
        bat   = d.get("battery", {})
        env   = d.get("env", {})
        rels  = d.get("relays", {})
        anoms = d.get("anomalies", [])
        now   = datetime.datetime.now().strftime("%H:%M:%S")

        soc     = bat.get("soc_pct", 0)
        voltage = bat.get("voltage_v", 0)
        current = bat.get("current_a", 0)
        power   = bat.get("power_w", 0)
        temp    = env.get("temp_c", 0)    if env.get("dht_ok")    else None
        humid   = env.get("humid_pct", 0) if env.get("dht_ok")    else None
        lux     = env.get("lux", -1)      if env.get("bh1750_ok") else None
        mode    = d.get("mode_name", "INCONNU")
        a_count = d.get("anomaly_count", 0)

        st.session_state.history_ts.append(now)
        st.session_state.history_soc.append(soc)
        st.session_state.history_voltage.append(voltage)
        st.session_state.history_current.append(current)
        st.session_state.history_power.append(power)
        st.session_state.history_temp.append(temp or 0)
        st.session_state.history_humid.append(humid or 0)
        st.session_state.history_lux.append(lux or 0)
        st.session_state.history_mode.append(mode)
        st.session_state.history_anomalies.append(a_count)
        st.session_state.total_measures += 1
        st.session_state.last_ts = time.time()

        for a in anoms:
            e = {"time": now, "type": a.get("type_name",""), "sev": a.get("severity","INFO"), "desc": a.get("description","")}
            if not st.session_state.alerts or st.session_state.alerts[-1]["desc"] != e["desc"]:
                st.session_state.alerts.append(e)

        if not os.path.exists(CSV_FILE):
            with open(CSV_FILE,"w",newline="",encoding="utf-8") as f:
                csv.writer(f).writerow(["timestamp","mode","soc","voltage","current","power","temp","humid","lux","grid","r1","r2","r3","anomaly_count","source"])
        with open(CSV_FILE,"a",newline="",encoding="utf-8") as f:
            csv.writer(f).writerow([now,mode,soc,voltage,current,power,temp,humid,lux,
                d.get("grid",False),rels.get("r1_critical",False),rels.get("r2_noncrit",False),
                rels.get("r3_grid",False),a_count,d.get("source","SOLAR")])

        st.session_state.last = {
            "ts":now,"mode":mode,"source":d.get("source","SOLAR"),"soc":soc,
            "voltage":voltage,"current":current,"power":power,
            "temp":temp,"humid":humid,"lux":lux,"grid":d.get("grid",False),
            "r1":rels.get("r1_critical",False),"r2":rels.get("r2_noncrit",False),
            "r3":rels.get("r3_grid",False),"anomaly_count":a_count,"anomalies":anoms,
            "sd_ok":d.get("sd_ok",False),"wifi_ok":d.get("wifi_ok",False),
            "wdt_ok":d.get("wdt_ok",False),"ai":d.get("ai",{})
        }
    except Exception as e:
        print(f"[PARSE] {e}")

# ═══════════════════════════════════════════
# USB SÉRIE
# ═══════════════════════════════════════════
def usb_thread():
    try:
        import serial, serial.tools.list_ports
    except:
        return
    st.session_state.conn_mode = "usb_searching"
    while True:
        port = None
        for p in serial.tools.list_ports.comports():
            if any(k in p.description for k in ["CP210","CH340","USB Serial"]):
                port = p.device; break
        if port:
            try:
                st.session_state.conn_mode = "usb"
                st.session_state.conn_port = port
                with serial.Serial(port, SERIAL_BAUD, timeout=2) as ser:
                    while True:
                        line = ser.readline().decode("utf-8", errors="ignore")
                        if line.strip(): process_data(line)
            except:
                st.session_state.conn_mode = "usb_searching"
        time.sleep(3)

# ═══════════════════════════════════════════
# MQTT
# ═══════════════════════════════════════════
def start_mqtt():
    def on_connect(client, ud, flags, rc):
        if rc == 0:
            st.session_state.conn_mode = "mqtt"
            client.subscribe(st.session_state.mqtt_topic)
    def on_message(client, ud, msg):
        try: process_data(json.loads(msg.payload.decode()))
        except: pass
    client = mqtt.Client()
    client.username_pw_set(MQTT_USERNAME, MQTT_PASSWORD)
    client.tls_set()
    client.on_connect = on_connect
    client.on_message = on_message
    try:
        client.connect(MQTT_BROKER, MQTT_PORT, 60)
        client.loop_forever()
    except Exception as e:
        print(f"[MQTT] {e}")

# ═══════════════════════════════════════════
# AUTH
# ═══════════════════════════════════════════
def gen_nonce():
    import secrets
    n = secrets.token_hex(8)
    st.session_state.nonce = n
    st.session_state.nonce_ts = time.time()
    return n

def verify_token(token):
    if not st.session_state.nonce: return False
    if time.time() - st.session_state.nonce_ts > 120: return False
    expected = hashlib.sha256((DESIGNER_HASH + st.session_state.nonce).encode()).hexdigest()
    return token == expected

# ═══════════════════════════════════════════
# SIMULATION
# ═══════════════════════════════════════════
def simulate():
    t = time.time(); soc = 50 + 30 * math.sin(t / 60)
    mode = "NORMAL" if soc>70 else("ECO" if soc>50 else("CRITIQUE" if soc>40 else"COUPE"))
    process_data({"device":"DISA-SIM","version":"4.1.0","ts":int(t*1000),
        "mode_name":mode,"source":"SOLAR",
        "battery":{"soc_pct":round(soc,1),"voltage_v":round(11+soc/45,2),
                   "current_a":round(random.uniform(1.5,3.5),2),
                   "power_w":round(random.uniform(18,42),1),"system_v":12},
        "env":{"temp_c":round(32+random.uniform(-2,5),1),"humid_pct":round(65+random.uniform(-5,10),1),
               "lux":round(random.uniform(2000,5000),0),"dht_ok":True,"bh1750_ok":True},
        "grid":False,"relays":{"r1_critical":soc>40,"r2_noncrit":soc>50,"r3_grid":False},
        "anomaly_count":0,"anomalies":[],"sd_ok":True,"wifi_ok":True,"wdt_ok":True,
        "ai":{"ready":True,"pred_v":round(11+soc/45-0.1,2),"pred_mode":mode,
              "inferences":random.randint(10,100)}})

# ═══════════════════════════════════════════
# APPLICATION STREAMLIT
# ═══════════════════════════════════════════
st.set_page_config(page_title="DISA Pro", page_icon="⚡", layout="wide",
                   initial_sidebar_state="expanded")

init_state()

# Démarrer les threads une seule fois
if not st.session_state.mqtt_started:
    st.session_state.mqtt_started = True
    threading.Thread(target=start_mqtt, daemon=True).start()

if not st.session_state.usb_started:
    st.session_state.usb_started = True
    threading.Thread(target=usb_thread, daemon=True).start()

# ── CSS PERSONNALISÉ ──
st.markdown("""
<style>
[data-testid="stSidebar"] {background:#131A2E}
[data-testid="stSidebar"] * {color:#ECF0F1}
.stButton button {width:100%;border-radius:8px;font-weight:600;transition:all 0.2s}
div[data-testid="metric-container"] {background:#131A2E;border:1px solid #2C3E6E;border-radius:10px;padding:12px}
div[data-testid="metric-container"] label {color:#95A5A6;font-size:11px;text-transform:uppercase;letter-spacing:1px}
div[data-testid="metric-container"] div[data-testid="stMetricValue"] {color:#ECF0F1;font-size:24px;font-weight:700}
.mode-NORMAL {background:linear-gradient(135deg,#0D3B1E,#1A5C2E);border:2px solid #27AE60;border-radius:14px;padding:20px;color:white}
.mode-ECO {background:linear-gradient(135deg,#3E2A00,#6B4800);border:2px solid #EF9F27;border-radius:14px;padding:20px;color:white}
.mode-CRITIQUE {background:linear-gradient(135deg,#3E1500,#7A2900);border:2px solid #E67E22;border-radius:14px;padding:20px;color:white}
.mode-COUPE {background:linear-gradient(135deg,#3E0000,#7A0000);border:2px solid #C0392B;border-radius:14px;padding:20px;color:white}
.relay-on {background:#0D3B1E;border:1px solid #27AE60;border-radius:8px;padding:8px 12px;color:#27AE60;font-weight:700;text-align:center}
.relay-off {background:#1C2540;border:1px solid #2C3E6E;border-radius:8px;padding:8px 12px;color:#5D6D7E;font-weight:700;text-align:center}
.alert-box {background:#3A0808;border:1px solid #C0392B;border-radius:10px;padding:12px;color:#ECF0F1}
.auth-ok {background:#0D3B1E;border:1px solid #27AE60;border-radius:8px;padding:10px;color:#27AE60}
.auth-fail {background:#3A0808;border:1px solid #C0392B;border-radius:8px;padding:10px;color:#C0392B}
h1,h2,h3 {color:#EF9F27}
</style>
""", unsafe_allow_html=True)

# ── SIDEBAR ──
with st.sidebar:
    st.markdown("# ⚡ DISA Pro")
    st.markdown("**Supervision Solaire v4.1**")
    st.markdown("---")

    page = st.radio("Navigation", [
        "📊 Tableau de bord",
        "⚡ Commandes",
        "📈 Historique",
        "🖥 Système",
        "⚙ Paramètres"
    ], key="page")

    st.markdown("---")

    # Statut connexion
    mode = st.session_state.conn_mode
    if mode == "usb":
        st.success(f"🔌 USB — {st.session_state.conn_port}")
    elif mode == "mqtt":
        st.success("📡 MQTT connecté")
    elif mode == "usb_searching":
        st.warning("🔍 Recherche USB...")
    else:
        st.error("❌ Déconnecté")

    # Statut en ligne
    age = time.time() - st.session_state.last_ts if st.session_state.last_ts > 0 else -1
    if age < 0:
        st.info("⏳ En attente de données")
    elif age < 10:
        st.success(f"🟢 En ligne")
    elif age < 60:
        st.warning(f"🟡 Délai {round(age)}s")
    else:
        st.error("🔴 Hors ligne")

    st.markdown(f"**Mesures :** {st.session_state.total_measures}")
    uptime = str(datetime.datetime.now() - st.session_state.session_start).split(".")[0]
    st.markdown(f"**Uptime :** {uptime}")
    st.markdown("---")
    if st.button("▶ Simuler données"):
        simulate()
        st.rerun()
    if st.button("🔄 Actualiser"):
        st.rerun()

last = st.session_state.last

# ══════════════════════════════════════════
# PAGE TABLEAU DE BORD
# ══════════════════════════════════════════
if page == "📊 Tableau de bord":
    st.markdown("## 📊 Tableau de bord")

    if not last:
        st.info("En attente de données... Cliquez sur **▶ Simuler données** dans la barre latérale.")
    else:
        # Alerte anomalie
        if last.get("anomaly_count", 0) > 0:
            names = ", ".join([a.get("type_name","") for a in last.get("anomalies",[])])
            st.markdown(f'<div class="alert-box">⚠️ <strong>{last["anomaly_count"]} anomalie(s) active(s) : {names}</strong></div>', unsafe_allow_html=True)

        # Mode card
        mode = last.get("mode","INCONNU")
        soc  = last.get("soc", 0)
        src  = last.get("source","—")
        grid = "Présent ✓" if last.get("grid") else "Absent ✗"
        st.markdown(f"""
        <div class="mode-{mode}">
          <div style="font-size:11px;opacity:0.6;letter-spacing:1px">MODE ÉNERGÉTIQUE ACTIF</div>
          <div style="display:flex;justify-content:space-between;align-items:center">
            <div>
              <div style="font-size:36px;font-weight:900;letter-spacing:3px">{mode}</div>
              <div style="font-size:13px;margin-top:4px;opacity:0.8">Source : {src} &nbsp;|&nbsp; Réseau 220V : {grid}</div>
            </div>
            <div style="text-align:right">
              <div style="font-size:52px;font-weight:900">{soc:.1f}%</div>
              <div style="font-size:11px;opacity:0.6">État de charge (SOC)</div>
            </div>
          </div>
        </div>
        """, unsafe_allow_html=True)

        st.markdown("")

        # KPI
        c1,c2,c3,c4,c5,c6,c7 = st.columns(7)
        def fmt(val, dec=1):
            if val is None or val == -99 or val == -1: return "—"
            return f"{float(val):.{dec}f}"
        c1.metric("Tension", fmt(last.get("voltage"),2)+" V")
        c2.metric("Courant", fmt(last.get("current"),2)+" A")
        c3.metric("Puissance", fmt(last.get("power"),1)+" W")
        c4.metric("Température", fmt(last.get("temp"),1)+" °C")
        c5.metric("Humidité", fmt(last.get("humid"),1)+" %")
        c6.metric("Irradiance", fmt(last.get("lux"),0)+" lx")
        c7.metric("Anomalies", str(last.get("anomaly_count",0)))

        # Relais
        st.markdown("#### Relais")
        r1,r2,r3 = st.columns(3)
        def relay_html(label, desc, on):
            cls = "relay-on" if on else "relay-off"
            state = "ON ●" if on else "OFF ○"
            return f'<div class="{cls}"><strong>{label}</strong><br><small>{desc}</small><br>{state}</div>'
        r1.markdown(relay_html("R1 — Charges critiques","Éclairage, équipements vitaux",last.get("r1")), unsafe_allow_html=True)
        r2.markdown(relay_html("R2 — Charges secondaires","Ventilateurs, confort",last.get("r2")), unsafe_allow_html=True)
        r3.markdown(relay_html("R3 — Bascule source","Grid / Solaire / Recharge",last.get("r3")), unsafe_allow_html=True)

        # Status
        st.markdown("")
        s1,s2,s3,s4 = st.columns(4)
        s1.markdown("🟢 SD Card OK" if last.get("sd_ok") else "🔴 SD Card erreur")
        s2.markdown("🟢 WiFi OK" if last.get("wifi_ok") else "🔴 WiFi hors ligne")
        s3.markdown("🟢 Watchdog OK" if last.get("wdt_ok") else "🔴 Watchdog erreur")
        s4.markdown(f"🕐 Dernière : {last.get('ts','—')}")

    # Graphiques
    ts   = list(st.session_state.history_ts)
    socs = list(st.session_state.history_soc)
    vols = list(st.session_state.history_voltage)
    curs = list(st.session_state.history_current)
    pows = list(st.session_state.history_power)
    tmps = list(st.session_state.history_temp)
    hums = list(st.session_state.history_humid)
    luxs = list(st.session_state.history_lux)
    anms = list(st.session_state.history_anomalies)

    if ts:
        import pandas as pd
        g1,g2 = st.columns(2)
        with g1:
            st.markdown("#### ⚡ SOC Batterie (%)")
            st.line_chart(pd.DataFrame({"SOC (%)": socs}, index=ts))
        with g2:
            st.markdown("#### 🔋 Tension (V)")
            st.line_chart(pd.DataFrame({"Tension (V)": vols}, index=ts))
        g3,g4 = st.columns(2)
        with g3:
            st.markdown("#### ⚡ Courant & Puissance")
            st.line_chart(pd.DataFrame({"Courant (A)": curs, "Puissance (W)": pows}, index=ts))
        with g4:
            st.markdown("#### 🌡️ Température & Humidité")
            st.line_chart(pd.DataFrame({"Temp (°C)": tmps, "Humid (%)": hums}, index=ts))
        g5,g6 = st.columns(2)
        with g5:
            st.markdown("#### ☀️ Irradiance (lux)")
            st.line_chart(pd.DataFrame({"Lux": luxs}, index=ts))
        with g6:
            st.markdown("#### ⚠️ Anomalies actives")
            st.line_chart(pd.DataFrame({"Anomalies": anms}, index=ts))

    # Journal alertes
    if st.session_state.alerts:
        st.markdown("#### 📋 Journal des alertes")
        import pandas as pd
        df = pd.DataFrame(list(st.session_state.alerts))
        st.dataframe(df, use_container_width=True, hide_index=True)

    # Export
    col_a, col_b = st.columns(2)
    if os.path.exists(CSV_FILE):
        with open(CSV_FILE,"rb") as f:
            col_a.download_button("⬇ Exporter CSV", f, file_name="disa_export.csv", mime="text/csv")

# ══════════════════════════════════════════
# PAGE COMMANDES
# ══════════════════════════════════════════
elif page == "⚡ Commandes":
    st.markdown("## ⚡ Commandes")

    # Auth
    st.markdown("### 🔐 Authentification SHA-256")
    auth_ok = st.session_state.auth_ok and (time.time() - st.session_state.auth_ts < 900)

    if auth_ok:
        st.markdown('<div class="auth-ok">✓ Authentifié — session valide 15 minutes</div>', unsafe_allow_html=True)
        if st.button("Se déconnecter"):
            st.session_state.auth_ok = False
            st.rerun()
    else:
        col_n, col_t = st.columns([1,2])
        with col_n:
            if st.button("1. Obtenir le nonce"):
                nonce = gen_nonce()
                st.session_state["show_nonce"] = nonce
        if st.session_state.get("show_nonce"):
            st.code(f"Nonce : {st.session_state['show_nonce']}")
            st.caption("Calculez : SHA256(DESIGNER_HASH + nonce) et entrez le résultat ci-dessous")
            token = st.text_input("Token SHA-256 (64 caractères hex)", key="token_input")
            if st.button("2. S'authentifier"):
                if verify_token(token):
                    st.session_state.auth_ok = True
                    st.session_state.auth_ts = time.time()
                    st.session_state["show_nonce"] = ""
                    st.success("✓ Authentifié !")
                    st.rerun()
                else:
                    st.error("✗ Token invalide")

    st.markdown("---")

    if not auth_ok:
        st.warning("Authentifiez-vous pour envoyer des commandes.")
    else:
        def send_cmd(cmd):
            try:
                import serial
                for p in __import__("serial.tools.list_ports",fromlist=["list_ports"]).list_ports.comports():
                    if any(k in p.description for k in ["CP210","CH340","USB Serial"]):
                        with serial.Serial(p.device, SERIAL_BAUD, timeout=1) as s:
                            s.write((json.dumps(cmd)+"\n").encode())
                            st.session_state.cmd_result = "✓ Envoyé via USB : " + json.dumps(cmd)
                            return
            except: pass
            st.session_state.cmd_result = "⚠ USB non disponible — commande : " + json.dumps(cmd)

        col1, col2 = st.columns(2)

        with col1:
            st.markdown("#### 🔄 Modes énergétiques")
            mc1,mc2 = st.columns(2)
            if mc1.button("✅ NORMAL", use_container_width=True): send_cmd({"cmd":"SET_MODE","mode":0}); st.rerun()
            if mc2.button("🌙 ECO", use_container_width=True): send_cmd({"cmd":"SET_MODE","mode":1}); st.rerun()
            mc3,mc4 = st.columns(2)
            if mc3.button("⚠️ CRITIQUE", use_container_width=True): send_cmd({"cmd":"SET_MODE","mode":2}); st.rerun()
            if mc4.button("🔴 COUPE", use_container_width=True): send_cmd({"cmd":"SET_MODE","mode":3}); st.rerun()
            if st.button("↩ Revenir en AUTO", use_container_width=True): send_cmd({"cmd":"AUTO"}); st.rerun()

            st.markdown("#### 🔧 Actions système")
            if st.button("💾 Flush SD", use_container_width=True): send_cmd({"cmd":"FLUSH_SD"}); st.rerun()
            if st.button("🧠 Reset IA", use_container_width=True): send_cmd({"cmd":"RESET_AI"}); st.rerun()
            if st.button("📏 Reset baselines anomalies", use_container_width=True): send_cmd({"cmd":"RESET_BASELINE","type":0}); st.rerun()
            if st.button("🕐 Synchroniser l'heure", use_container_width=True):
                send_cmd({"cmd":"SET_TIME","epoch":int(time.time())}); st.rerun()

        with col2:
            st.markdown("#### 📊 Modifier les seuils SOC")
            sn = st.number_input("NORMAL ≥ (%)", min_value=0, max_value=100, value=70)
            se = st.number_input("ECO ≥ (%)", min_value=0, max_value=100, value=50)
            sc_val = st.number_input("CRITIQUE ≥ (%)", min_value=0, max_value=100, value=40)
            if st.button("Appliquer les seuils", use_container_width=True):
                if sn > se > sc_val > 0:
                    send_cmd({"cmd":"SET_THRESHOLDS","normal":sn,"eco":se,"critique":sc_val})
                    st.rerun()
                else:
                    st.error("NORMAL > ECO > CRITIQUE > 0")

        # Résultat
        if st.session_state.cmd_result:
            st.markdown("#### Résultat")
            st.code(st.session_state.cmd_result)

# ══════════════════════════════════════════
# PAGE HISTORIQUE
# ══════════════════════════════════════════
elif page == "📈 Historique":
    st.markdown("## 📈 Historique")
    import pandas as pd

    r = st.radio("Période", ["1h (60 pts)","6h (360 pts)","Tout"], horizontal=True)
    n = {"1h (60 pts)":60,"6h (360 pts)":360,"Tout":0}[r]

    ts   = list(st.session_state.history_ts)
    socs = list(st.session_state.history_soc)
    vols = list(st.session_state.history_voltage)
    curs = list(st.session_state.history_current)
    pows = list(st.session_state.history_power)
    tmps = list(st.session_state.history_temp)
    hums = list(st.session_state.history_humid)
    luxs = list(st.session_state.history_lux)
    mods = list(st.session_state.history_mode)
    anms = list(st.session_state.history_anomalies)

    if n > 0:
        ts=ts[-n:];socs=socs[-n:];vols=vols[-n:];curs=curs[-n:]
        pows=pows[-n:];tmps=tmps[-n:];hums=hums[-n:];luxs=luxs[-n:]
        mods=mods[-n:];anms=anms[-n:]

    if ts:
        h1,h2 = st.columns(2)
        with h1:
            st.markdown("#### SOC + Tension")
            st.line_chart(pd.DataFrame({"SOC (%)":socs,"Tension (V)":vols},index=ts))
        with h2:
            st.markdown("#### Courant + Puissance")
            st.line_chart(pd.DataFrame({"Courant (A)":curs,"Puissance (W)":pows},index=ts))
        h3,h4 = st.columns(2)
        with h3:
            st.markdown("#### Température + Humidité")
            st.line_chart(pd.DataFrame({"Temp (°C)":tmps,"Humid (%)":hums},index=ts))
        with h4:
            st.markdown("#### Irradiance")
            st.line_chart(pd.DataFrame({"Lux":luxs},index=ts))

        st.markdown("#### Tableau des mesures")
        df = pd.DataFrame({
            "Heure":ts,"Mode":mods,"SOC %":socs,"Tension V":vols,
            "Courant A":curs,"Puissance W":pows,"Temp °C":tmps,
            "Humid %":hums,"Lux":luxs,"Anomalies":anms
        })
        st.dataframe(df.iloc[::-1].head(100), use_container_width=True, hide_index=True)
    else:
        st.info("Aucune donnée encore reçue.")

    if os.path.exists(CSV_FILE):
        with open(CSV_FILE,"rb") as f:
            st.download_button("⬇ Télécharger CSV complet", f, file_name="disa_historique.csv", mime="text/csv")

# ══════════════════════════════════════════
# PAGE SYSTÈME
# ══════════════════════════════════════════
elif page == "🖥 Système":
    st.markdown("## 🖥 Système")

    c1,c2 = st.columns(2)
    with c1:
        st.markdown("#### 📡 Firmware")
        st.table({
            "Paramètre": ["Version DISA","Uptime dashboard","Mesures reçues","Dernière donnée"],
            "Valeur": [
                "v4.1.0",
                str(datetime.datetime.now()-st.session_state.session_start).split(".")[0],
                str(st.session_state.total_measures),
                last.get("ts","—") if last else "—"
            ]
        })

    with c2:
        st.markdown("#### 🔬 Capteurs")
        if last:
            dht_ok = last.get("temp") is not None and last.get("temp") != -99
            bh_ok  = last.get("lux") is not None and last.get("lux") != -1
            rows = {
                "Capteur":["INA219 (V+I)","DHT22 (T+H)","BH1750 (lux)","SD Card","WiFi ESP32","Watchdog"],
                "Statut":[
                    "🟢 OK","🟢 OK" if dht_ok else "🔴 Erreur",
                    "🟢 OK" if bh_ok else "🟡 Non détecté",
                    "🟢 OK" if last.get("sd_ok") else "🔴 Erreur",
                    "🟢 OK" if last.get("wifi_ok") else "🔴 Hors ligne",
                    "🟢 OK" if last.get("wdt_ok") else "🔴 Erreur"
                ]
            }
            import pandas as pd
            st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
        else:
            st.info("En attente de données...")

    c3,c4 = st.columns(2)
    with c3:
        st.markdown("#### 🧠 Module IA")
        if last and last.get("ai"):
            ai = last["ai"]
            st.table({
                "Paramètre":["Statut","Inférences","Tension prédite","Mode prédit"],
                "Valeur":[
                    "Prêt" if ai.get("ready") else "En apprentissage",
                    str(ai.get("inferences",0)),
                    f"{ai.get('pred_v','—')} V" if ai.get("pred_v",0)>0 else "—",
                    ai.get("pred_mode","—")
                ]
            })
        else:
            st.info("En attente de données IA...")

    with c4:
        st.markdown("#### 🔗 Connexion")
        st.table({
            "Paramètre":["Mode","Port COM","Topic MQTT","Broker"],
            "Valeur":[
                st.session_state.conn_mode,
                st.session_state.conn_port or "—",
                st.session_state.mqtt_topic,
                MQTT_BROKER
            ]
        })

# ══════════════════════════════════════════
# PAGE PARAMÈTRES
# ══════════════════════════════════════════
elif page == "⚙ Paramètres":
    st.markdown("## ⚙ Paramètres")

    c1,c2 = st.columns(2)
    with c1:
        st.markdown("#### 📡 MQTT")
        new_topic = st.text_input("Topic MQTT", value=st.session_state.mqtt_topic)
        st.text_input("Broker", value=MQTT_BROKER, disabled=True)
        if st.button("Appliquer topic MQTT"):
            st.session_state.mqtt_topic = new_topic
            st.success(f"Topic mis à jour : {new_topic}")

    with c2:
        st.markdown("#### 🌐 Accès distant (ngrok)")
        st.info("Pour accéder au dashboard depuis n'importe où :")
        st.code("streamlit run dashboard_disa_streamlit.py")
        st.code("ngrok http 8501")
        st.markdown("Partagez l'URL ngrok générée.")

    st.markdown("---")
    st.markdown("#### 📊 Export données")
    if os.path.exists(CSV_FILE):
        with open(CSV_FILE,"rb") as f:
            st.download_button("⬇ Télécharger CSV", f,
                              file_name=f"disa_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}.csv",
                              mime="text/csv")
    else:
        st.warning("Aucun fichier CSV disponible.")

    st.markdown("---")
    st.markdown("#### ℹ️ À propos")
    st.markdown("""
    **DISA Pro Dashboard v3.0**  
    KOUKPAKI Cyrius — ESMER Bénin 2025-2026  
    Dispositif Intelligent de Supervision Autonome
    """)
