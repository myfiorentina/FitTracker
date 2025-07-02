import logging
import re
import json
import requests
import os
from datetime import datetime, timedelta
from functools import wraps
from flask import Flask, render_template, request, redirect, url_for, session, flash, abort
from werkzeug.security import generate_password_hash, check_password_hash
from collections import defaultdict

USERS_FILE = "/data/users.json"
PASTI_FILE = "/data/pasti.json"
PESATE_FILE = "/data/pesate.json"

DATA_PATH = "/data"

# Configurazioni Flask e logging
app = Flask(__name__)
app.secret_key = "supersegreto"
logging.basicConfig(level=logging.DEBUG)

# ======================
# Funzioni di utilità
# ======================

def estrai_json_da_markdown(testo):
    pattern = r"```json\s*(\{.*?\})\s*```"
    match = re.search(pattern, testo, re.DOTALL)
    if match:
        return match.group(1)
    return testo.strip()

def valida_data_ora(data_ora_str):
    try:
        datetime.strptime(data_ora_str, "%d/%m/%Y - %H:%M")
        return True
    except ValueError:
        return False

def load_users():
    if not os.path.exists(USERS_FILE):
        return {}
    with open(USERS_FILE, "r", encoding="utf-8") as f:
        return json.load(f)

def save_users(users):
    with open(USERS_FILE, "w", encoding="utf-8") as f:
        json.dump(users, f, indent=2)

def leggi_pasti_utente(username):
    pasti = []
    try:
        with open(PASTI_FILE, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    pasto = json.loads(line)
                    if pasto.get("utente") == username:
                        pasti.append(pasto)
                except json.JSONDecodeError:
                    logging.warning(f"Riga JSON malformata ignorata in pasti: {line}")
    except FileNotFoundError:
        pass

    def key_func(x):
        try:
            return datetime.strptime(x["data_ora"], "%d/%m/%Y - %H:%M")
        except Exception:
            return datetime.min
    pasti.sort(key=key_func, reverse=True)
    return pasti

def leggi_pesate_utente(username):
    pesate = []
    try:
        with open(PESATE_FILE, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    pesata = json.loads(line)
                    if pesata.get("utente") == username:
                        pesate.append(pesata)
                except json.JSONDecodeError:
                    logging.warning(f"Riga JSON malformata ignorata in pesate: {line}")
    except FileNotFoundError:
        pass

    def key_func(x):
        try:
            return datetime.strptime(x["data_ora"], "%d/%m/%Y - %H:%M")
        except Exception:
            return datetime.min
    pesate.sort(key=key_func, reverse=True)
    return pesate

# ======================
# Decoratori
# ======================

def login_required(f):
    @wraps(f)
    def wrapped(*args, **kwargs):
        if "username" not in session:
            flash("Devi prima accedere.")
            return redirect(url_for("login"))
        return f(*args, **kwargs)
    return wrapped

def admin_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if "username" not in session:
            flash("Devi prima accedere.")
            return redirect(url_for("login"))
        
        users = load_users()
        username = session["username"]
        user = users.get(username)
        
        if not user or not user.get("admin", False):
            flash("Accesso negato: area riservata agli amministratori.")
            return redirect(url_for("home"))
        
        return f(*args, **kwargs)
    return decorated_function

# ======================
# Route di base
# ======================

@app.route("/")
@login_required
def home():
    return render_template("home.html", username=session["username"])

@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form["username"].strip()
        password = request.form["password"]
        users = load_users()

        if username in users and check_password_hash(users[username]["password"], password):
            session["username"] = username
            # Salva anche dati base in session
            session["nome"] = users[username]["nome"]
            session["cognome"] = users[username]["cognome"]
            session["sesso"] = users[username]["sesso"]
            session["eta"] = users[username]["eta"]
            session["peso_iniziale"] = users[username]["peso_iniziale"]
            session["altezza"] = users[username]["altezza"]
            session["email"] = users[username].get("email", "")
            session["is_admin"] = users[username].get("admin", False)
            return redirect(url_for("home"))
        else:
            flash("Credenziali non valide.")
    return render_template("login.html")

@app.route("/logout")
def logout():
    session.clear()
    flash("Logout effettuato.")
    return redirect(url_for("login"))

# ======================
# Route Pasti
# ======================

@app.route("/inserisci_pasto", methods=["GET", "POST"])
@login_required
def inserisci_pasto():
    if request.method == "POST":
        tipo = request.form.get('tipo', '').strip()
        descrizione = request.form.get('descrizione', '').strip()
        data_ora = request.form.get('data_ora', '').strip()
        username = session['username']

        if not valida_data_ora(data_ora):
            flash("Data/Ora non valida. Usa formato gg/mm/aaaa - hh:mm (ora 00-23).")
            return redirect(request.url)

        # Chiamata API Gemini
        gemini_api_key = "AIzaSyAJ_U8NMEJAr7sk24uqjzJdOrxD9meFMr0"
        gemini_url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={gemini_api_key}"

        prompt = (
            f"Fornisci solo un oggetto JSON con i seguenti campi: "
            f"calorie (in kcal), proteine (in grammi), carboidrati (in grammi), grassi (in grammi). "
            f"Esempio: {{\"calorie\": 250, \"proteine\": 10, \"carboidrati\": 20, \"grassi\": 5}}. "
            f"Descrizione del pasto: {descrizione}"
        )

        payload = {"contents": [{"parts": [{"text": prompt}]}]}

        logging.debug(f"Chiamata Gemini URL: {gemini_url}")
        logging.debug(f"Payload inviato a Gemini: {json.dumps(payload)}")

        response = requests.post(gemini_url, json=payload)

        logging.debug(f"Status code risposta Gemini: {response.status_code}")
        logging.debug(f"Contenuto risposta Gemini: {response.text}")

        calorie = proteine = carboidrati = grassi = "N/D"

        if response.status_code == 200:
            try:
                output = response.json()
                text_response = output["candidates"][0]["content"]["parts"][0]["text"]
                logging.debug(f"RISPOSTA GEMINI RAW: {text_response}")

                pulito = estrai_json_da_markdown(text_response)
                nutrizione = json.loads(pulito)

                calorie = nutrizione.get("calorie", "N/D")
                proteine = nutrizione.get("proteine", "N/D")
                carboidrati = nutrizione.get("carboidrati", "N/D")
                grassi = nutrizione.get("grassi", "N/D")

            except Exception as e:
                logging.error(f"Errore parsing risposta Gemini: {e}")
                flash("Errore nell'elaborazione della risposta da Gemini")
        else:
            logging.error(f"Errore API Gemini, status code: {response.status_code}")
            flash("Errore nella chiamata all'API Gemini")

        nuovo_pasto = {
            "utente": username,
            "tipo": tipo,
            "descrizione": descrizione,
            "data_ora": data_ora,
            "calorie": calorie,
            "proteine": proteine,
            "carboidrati": carboidrati,
            "grassi": grassi
        }

        with open(PASTI_FILE, "a", encoding="utf-8") as f:
            f.write(json.dumps(nuovo_pasto) + "\n")

        flash("Pasto registrato con valori nutrizionali.")
        return redirect(url_for("home"))

    default_ora = datetime.now().strftime("%d/%m/%Y - %H:%M")
    return render_template("inserisci_pasto.html", default_ora=default_ora)

@app.route("/storico_pasti")
@login_required
def storico_pasti():
    username = session["username"]
    pasti = leggi_pasti_utente(username)
    return render_template("storico_pasti.html", pasti=pasti)

@app.route("/elimina_pasto/<int:index>", methods=["POST"])
@login_required
def elimina_pasto(index):
    username = session["username"]
    pasti = leggi_pasti_utente(username)
    if 0 <= index < len(pasti):
        del pasti[index]
        nuovi_pasti = []
        try:
            with open(PASTI_FILE, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        p = json.loads(line)
                        if p.get("utente") != username:
                            nuovi_pasti.append(p)
                    except json.JSONDecodeError:
                        logging.warning(f"Riga JSON malformata ignorata in elimina_pasto: {line}")
        except FileNotFoundError:
            pass
        nuovi_pasti.extend(pasti)
        with open(PASTI_FILE, "w", encoding="utf-8") as f:
            for pasto in nuovi_pasti:
                f.write(json.dumps(pasto) + "\n")
        flash("Pasto eliminato con successo.")
    else:
        flash("Indice pasto non valido.")
    return redirect(url_for("storico_pasti"))

@app.route("/modifica_pasto/<int:index>", methods=["GET", "POST"])
@login_required
def modifica_pasto(index):
    username = session["username"]
    pasti = leggi_pasti_utente(username)

    if not (0 <= index < len(pasti)):
        flash("Indice pasto non valido.")
        return redirect(url_for("storico_pasti"))

    pasto = pasti[index]

    if request.method == "POST":
        tipo = request.form.get('tipo', '').strip()
        descrizione = request.form.get('descrizione', '').strip()
        data_ora = request.form.get('data_ora', '').strip()

        if not valida_data_ora(data_ora):
            flash("Data/Ora non valida. Usa formato gg/mm/aaaa - hh:mm (ora 00-23).")
            return redirect(request.url)

        pasto['tipo'] = tipo
        pasto['descrizione'] = descrizione
        pasto['data_ora'] = data_ora

        nuovi_pasti = []
        try:
            with open(PASTI_FILE, "r", encoding="utf-8") as f:
                for line in f:
                    if line.strip():
                        p = json.loads(line)
                        if p.get("utente") != username:
                            nuovi_pasti.append(p)
        except FileNotFoundError:
            pass
        nuovi_pasti.extend(pasti)
        with open(PASTI_FILE, "w", encoding="utf-8") as f:
            for p in nuovi_pasti:
                f.write(json.dumps(p) + "\n")
        flash("Pasto modificato correttamente.")
        return redirect(url_for("storico_pasti"))

    return render_template("modifica_pasto.html", pasto=pasto, index=index)

# ======================
# Route Pesate
# ======================

@app.route("/storico_pesate")
@login_required
def storico_pesate():
    username = session["username"]
    pesate = leggi_pesate_utente(username)
    return render_template("storico_pesate.html", pesate=pesate)

@app.route("/elimina_pesata/<int:index>", methods=["POST"])
@login_required
def elimina_pesata(index):
    username = session["username"]
    pesate = leggi_pesate_utente(username)
    if 0 <= index < len(pesate):
        del pesate[index]
        nuovi_pesate = []
        try:
            with open(PESATE_FILE, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        p = json.loads(line)
                        if p.get("utente") != username:
                            nuovi_pesate.append(p)
                    except json.JSONDecodeError:
                        logging.warning(f"Riga JSON malformata ignorata in elimina_pesata: {line}")
        except FileNotFoundError:
            pass
        nuovi_pesate.extend(pesate)
        with open(PESATE_FILE, "w", encoding="utf-8") as f:
            for pesata in nuovi_pesate:
                f.write(json.dumps(pesata) + "\n")
        flash("Pesata eliminata con successo.")
    else:
        flash("Indice pesata non valido.")
    return redirect(url_for("storico_pesate"))

@app.route("/modifica_pesata/<int:index>", methods=["GET", "POST"])
@login_required
def modifica_pesata(index):
    username = session["username"]
    pesate = leggi_pesate_utente(username)

    if not (0 <= index < len(pesate)):
        flash("Indice pesata non valido.")
        return redirect(url_for("storico_pesate"))

    pesata = pesate[index]

    if request.method == "POST":
        pesata["data_ora"] = request.form.get("data_ora", pesata.get("data_ora"))
        pesata["peso"] = float(request.form.get("peso", pesata.get("peso")))
        pesata["bmi"] = float(request.form.get("bmi", pesata.get("bmi")))
        pesata["grasso_corporeo"] = float(request.form.get("grasso_corporeo", pesata.get("grasso_corporeo")))
        pesata["muscolo_scheletrico"] = float(request.form.get("muscolo_scheletrico", pesata.get("muscolo_scheletrico")))
        pesata["massa_muscolare"] = float(request.form.get("massa_muscolare", pesata.get("massa_muscolare")))
        pesata["acqua_corporea"] = float(request.form.get("acqua_corporea", pesata.get("acqua_corporea")))
        pesata["bmr"] = float(request.form.get("bmr", pesata.get("bmr")))
        pesata["eta_metabolica"] = int(request.form.get("eta_metabolica", pesata.get("eta_metabolica")))

        nuovi_pesate = []
        try:
            with open(PESATE_FILE, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        p = json.loads(line)
                        if p.get("utente") != username:
                            nuovi_pesate.append(p)
                    except json.JSONDecodeError:
                        logging.warning(f"Riga JSON malformata ignorata in modifica_pesata: {line}")
        except FileNotFoundError:
            pass

        nuovi_pesate.extend(pesate)
        with open(PESATE_FILE, "w", encoding="utf-8") as f:
            for p in nuovi_pesate:
                f.write(json.dumps(p) + "\n")

        flash("Pesata modificata correttamente.")
        return redirect(url_for("storico_pesate"))

    return render_template("modifica_pesata.html", pesata=pesata, index=index)

@app.route("/inserisci_pesata", methods=["GET", "POST"])
@login_required
def inserisci_pesata():
    username = session["username"]

    if request.method == "POST":
        data_ora = request.form.get("data_ora", "").strip()

        if not valida_data_ora(data_ora):
            flash("Data/Ora non valida. Usa formato gg/mm/aaaa - hh:mm (ora 00-23).")
            return redirect(request.url)

        campi_float = [
            "peso", "bmi", "grasso_corporeo", "muscolo_scheletrico",
            "peso_senza_grassi", "grasso_sottocutaneo", "acqua_corporea",
            "massa_muscolare", "massa_ossea", "proteine", "bmr"
        ]
        campi_int = ["grasso_viscerale", "eta_metabolica"]

        dati_pesata = {"utente": username, "data_ora": data_ora}

        for campo in campi_float:
            valore = request.form.get(campo, "").strip()
            try:
                dati_pesata[campo] = float(valore)
            except ValueError:
                flash(f"Valore non valido per {campo}. Inserisci un numero corretto.")
                return redirect(request.url)

        for campo in campi_int:
            valore = request.form.get(campo, "").strip()
            try:
                dati_pesata[campo] = int(valore)
            except ValueError:
                flash(f"Valore non valido per {campo}. Inserisci un numero intero corretto.")
                return redirect(request.url)

        try:
            with open(PESATE_FILE, "a", encoding="utf-8") as f:
                f.write(json.dumps(dati_pesata) + "\n")
            flash("Pesata salvata correttamente.")
        except Exception as e:
            logging.error(f"Errore salvataggio pesata: {e}")
            flash("Errore nel salvataggio della pesata.")

        return redirect(url_for("home"))

    pesate = leggi_pesate_utente(username)
    dati_precompilati = {}
    if pesate:
        dati_precompilati = pesate[0].copy()
        dati_precompilati["data_ora"] = datetime.now().strftime("%d/%m/%Y - %H:%M")

    return render_template("inserisci_pesata.html", default_ora=datetime.now().strftime("%d/%m/%Y - %H:%M"), dati=dati_precompilati)


# ======================
# Route Report
# ======================

def aggrega_pasti_per_giorno(username, start_date, end_date):
    pasti = leggi_pasti_utente(username)
    dati_giornalieri = defaultdict(lambda: {"calorie": 0, "proteine": 0, "carboidrati": 0, "grassi": 0})
    
    for pasto in pasti:
        try:
            data_pasto = datetime.strptime(pasto["data_ora"], "%d/%m/%Y - %H:%M").date()
            if start_date <= data_pasto <= end_date:
                data_str = data_pasto.strftime("%d/%m/%Y")
                dati_giornalieri[data_str]["calorie"] += float(pasto.get("calorie", 0) or 0)
                dati_giornalieri[data_str]["proteine"] += float(pasto.get("proteine", 0) or 0)
                dati_giornalieri[data_str]["carboidrati"] += float(pasto.get("carboidrati", 0) or 0)
                dati_giornalieri[data_str]["grassi"] += float(pasto.get("grassi", 0) or 0)
        except Exception as e:
            logging.warning(f"Errore aggregazione pasto: {e}")

    date_ordinate = sorted(dati_giornalieri.keys(), key=lambda d: datetime.strptime(d, "%d/%m/%Y"))
    calorie = [dati_giornalieri[d]["calorie"] for d in date_ordinate]
    proteine = [dati_giornalieri[d]["proteine"] for d in date_ordinate]
    carboidrati = [dati_giornalieri[d]["carboidrati"] for d in date_ordinate]
    grassi = [dati_giornalieri[d]["grassi"] for d in date_ordinate]

    return date_ordinate, calorie, proteine, carboidrati, grassi

@app.route("/report_pasti")
@login_required
def report_pasti():
    username = session["username"]
    start_date_str = request.args.get("start_date")
    end_date_str = request.args.get("end_date")

    oggi = datetime.now().date()
    default_start = oggi - timedelta(days=30)
    default_end = oggi

    try:
        start_date = datetime.strptime(start_date_str, "%Y-%m-%d").date() if start_date_str else default_start
        end_date = datetime.strptime(end_date_str, "%Y-%m-%d").date() if end_date_str else default_end
    except ValueError:
        flash("Formato data non valido, usa YYYY-MM-DD")
        return redirect(url_for("report_pasti"))

    date, calorie, proteine, carboidrati, grassi = aggrega_pasti_per_giorno(username, start_date, end_date)

    return render_template("report_pasti.html",
        date_labels=date,
        calorie=calorie,
        proteine=proteine,
        carboidrati=carboidrati,
        grassi=grassi,
        start_date=start_date.strftime("%Y-%m-%d"),
        end_date=end_date.strftime("%Y-%m-%d")
    )

@app.route("/report_pesate", methods=["GET", "POST"])
@login_required
def report_pesate():
    username = session["username"]

    oggi = datetime.now().date()
    default_start = oggi - timedelta(days=30)

    if request.method == "POST":
        start_date = request.form.get("start_date", default_start.strftime("%Y-%m-%d"))
        end_date = request.form.get("end_date", oggi.strftime("%Y-%m-%d"))
    else:
        start_date = default_start.strftime("%Y-%m-%d")
        end_date = oggi.strftime("%Y-%m-%d")

    pesate = leggi_pesate_utente(username)

    start_dt = datetime.strptime(start_date, "%Y-%m-%d").date()
    end_dt = datetime.strptime(end_date, "%Y-%m-%d").date()

    filtered = [p for p in pesate if start_dt <= datetime.strptime(p["data_ora"], "%d/%m/%Y - %H:%M").date() <= end_dt]

    labels = sorted(set(datetime.strptime(p["data_ora"], "%d/%m/%Y - %H:%M").strftime("%d/%m/%Y") for p in filtered))

    campi = ["peso", "bmi", "grasso_corporeo", "grasso_sottocutaneo", "grasso_viscerale", "muscolo_scheletrico",
             "peso_senza_grassi", "acqua_corporea", "massa_muscolare", "massa_ossea", "proteine", "bmr", "eta_metabolica"]

    pesate_data = {campo: [] for campo in campi}

    for label in labels:
        giorno_pesate = [p for p in filtered if datetime.strptime(p["data_ora"], "%d/%m/%Y - %H:%M").strftime("%d/%m/%Y") == label]
        for campo in campi:
            valori = [p.get(campo, 0) for p in giorno_pesate if p.get(campo) is not None]
            media = sum(valori) / len(valori) if valori else None
            pesate_data[campo].append(media)

    date_objs = [datetime.strptime(d, "%d/%m/%Y") for d in labels]

    zipped_data = sorted(
        zip(date_objs, *(pesate_data[campo] for campo in campi))
    )

    labels = [d.strftime("%d/%m/%Y") for d in [item[0] for item in zipped_data]]

    for i, campo in enumerate(campi):
        pesate_data[campo] = [item[i+1] for item in zipped_data]

    return render_template("report_pesate.html",
                           labels=labels,
                           pesate_data=pesate_data,
                           start_date=start_date,
                           end_date=end_date)

# ======================
# Route Registrazione e Profilo
# ======================

@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        email = request.form.get("email", "").strip()
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        conferma_password = request.form.get("conferma_password", "")
        nome = request.form.get("nome", "").strip()
        cognome = request.form.get("cognome", "").strip()
        sesso = request.form.get("sesso", "")
        eta = request.form.get("eta", "")
        peso_iniziale = request.form.get("peso_iniziale", "")
        altezza = request.form.get("altezza", "")

        logging.debug(f"DEBUG: username='{username}', password='{password}', conferma_password='{conferma_password}', email='{email}'")

        # Controlli di base sui campi obbligatori
        if not username or not password or not conferma_password:
            flash("Username, password e conferma password sono obbligatori.")
            return redirect(request.url)

        if password != conferma_password:
            flash("Le password non corrispondono.")
            return redirect(request.url)

        if not nome or not cognome or not sesso or not eta or not peso_iniziale or not altezza or not email:
            flash("Compila tutti i campi obbligatori, inclusa l'email.")
            return redirect(request.url)

        # Validazione base formato email (puoi usare regex più complesse se vuoi)
        if "@" not in email or "." not in email:
            flash("Inserisci un indirizzo email valido.")
            return redirect(request.url)

        try:
            eta = int(eta)
            peso_iniziale = float(peso_iniziale)
            altezza = int(altezza)
        except ValueError:
            flash("Inserisci valori numerici validi per età, peso iniziale e altezza.")
            return redirect(request.url)

        users = load_users()

        if username in users:
            flash("Username già esistente.")
            return redirect(request.url)

        hashed_password = generate_password_hash(password)

        users[username] = {
            "password": hashed_password,
            "nome": nome,
            "cognome": cognome,
            "email": email,
            "sesso": sesso,
            "eta": eta,
            "peso_iniziale": peso_iniziale,
            "altezza": altezza
        }

        save_users(users)
        flash("Registrazione avvenuta con successo. Ora effettua il login.")
        return redirect(url_for("login"))

    return render_template("register.html")


@app.route("/profilo", methods=["GET", "POST"])
@login_required
def profilo():
    username = session["username"]
    users = load_users()
    user = users.get(username)

    if not user:
        flash("Utente non trovato.")
        return redirect(url_for("logout"))

    if request.method == "POST":
        nome = request.form.get("nome", "").strip()
        cognome = request.form.get("cognome", "").strip()
        sesso = request.form.get("sesso", "").strip()
        eta = request.form.get("eta", "").strip()
        peso_iniziale = request.form.get("peso_iniziale", "").strip()
        altezza = request.form.get("altezza", "").strip()
        email = request.form.get("email", "").strip()

        nuova_password = request.form.get("password", "").strip()
        conferma_password = request.form.get("conferma_password", "").strip()

        # Validazioni base
        if nuova_password:
            if nuova_password != conferma_password:
                flash("Le password non coincidono.")
                return redirect(request.url)
            else:
                user["password"] = generate_password_hash(nuova_password)

        if not nome or not cognome or sesso not in ["M", "F", "O"] or not email:
            flash("Compila tutti i campi correttamente, compresa l'email.")
            return redirect(request.url)

        if "@" not in email or "." not in email:
            flash("Inserisci un indirizzo email valido.")
            return redirect(request.url)

        try:
            eta = int(eta)
            peso_iniziale = float(peso_iniziale)
            altezza = int(altezza)
        except ValueError:
            flash("Età, peso e altezza devono essere numeri validi.")
            return redirect(request.url)

        # Aggiorna dati utente
        user.update({
            "nome": nome,
            "cognome": cognome,
            "sesso": sesso,
            "eta": eta,
            "peso_iniziale": peso_iniziale,
            "altezza": altezza,
            "email": email
        })

        users[username] = user
        save_users(users)

        session["email"] = email
        flash("Profilo aggiornato con successo.")
        return redirect(url_for("profilo"))

    return render_template("profilo.html", user=user)


@app.route("/cancella_account", methods=["POST"])
@login_required
def cancella_account():
    username = session["username"]
    users = load_users()

    if username in users:
        del users[username]
        save_users(users)
        session.clear()
        flash("Account cancellato con successo.")
        return redirect(url_for("login"))
    else:
        flash("Utente non trovato.")
        return redirect(url_for("home"))

# ======================
# Route Admin
# ======================

@app.route("/admin_utenti")
@login_required
@admin_required
def admin_utenti():
    users = load_users()
    return render_template("admin_utenti.html", users=users)

@app.route("/elimina_utente/<username>", methods=["POST"])
@login_required
@admin_required
def elimina_utente(username):
    users = load_users()
    if username in users:
        del users[username]
        save_users(users)
        flash(f"Utente '{username}' eliminato con successo.")
    else:
        flash("Utente non trovato.")
    return redirect(url_for("admin_utenti"))

# ======================
# Avvio app
# ======================

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
