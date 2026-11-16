# Architektur und Kostenüberblick

## 1. Überblick

```
 Handy-Browser (HTML/JS, mobil optimiert)
   │  1. Login (Passwort)            ─┐
   │  2. Upload (MP4/MOV, mit Fortschrittsbalken)
   │  3. Status abfragen (alle 2 s)    │  HTTPS, Cookie-geschützt
   │  4. Vorschau + Download (ZIP)    ─┘
   ▼
 FastAPI-Server (Python)  ── läuft in einem Docker-Container auf Render
   │
   ├─ Job-Warteschlange (Hintergrund-Thread, 1 Video gleichzeitig)
   │     └─ Pipeline:
   │          a) ffprobe: Video prüfen (Länge, Auflösung, Tonspur)
   │          b) Audio extrahieren (16 kHz mono)
   │          c) Transkription mit Wort-Zeitstempeln (OpenAI Whisper API, optional)
   │          d) Analyse: Szenenwechsel (FFmpeg), Lautstärke/Intensität (numpy)
   │          e) Highlight-Kandidaten bilden (15–60 s, an Satzgrenzen)
   │          f) Bewertung: Hook, Emotion, Überraschung, Vollständigkeit
   │             – Heuristik (kostenlos, immer aktiv)
   │             – optional Claude (Anthropic API) für bessere Auswahl
   │          g) Schnittliste: Pausen + Füllwörter („äh“, „ähm“) + Wortwiederholungen entfernen
   │          h) Bildausschnitt 9:16: Gesichtserkennung (OpenCV), sonst Mitte
   │          i) Rendern mit FFmpeg: 1080×1920, Punch-in-Zooms, Wort-für-Wort-Untertitel,
   │             dezente „Whoosh“-Effekte, optional Musik
   │          j) ZIP erstellen
   │
   ├─ Aufräum-Thread: löscht Jobs nach X Stunden (Standard 6 h)
   └─ Kostenzähler: Tagesbudget, Maximallänge pro Video
```

### Ordnerstruktur

| Pfad | Inhalt |
|---|---|
| `app/main.py` | Web-Server, Login, Upload, Download-Endpunkte |
| `app/config.py` | Alle Einstellungen aus Umgebungsvariablen |
| `app/jobs.py` | Job-Speicher und Hintergrund-Worker |
| `app/costs.py` | Kostenschätzung und Tageslimit |
| `app/pipeline/` | Videoverarbeitung (jede Datei ein Schritt) |
| `app/static/` | Mobile Weboberfläche |
| `tests/` | Automatisierte Tests |
| `Dockerfile`, `render.yaml` | Bereitstellung |

### Wichtige Designentscheidungen

* **Ein Prozess, ein Worker-Thread.** Einfach und günstig. Nachteil: Wenn der Server neu
  startet, gehen laufende Jobs verloren (sie werden als „fehlgeschlagen“ markiert). Für einen
  Prototyp mit einer Person ist das ausreichend. Später kann man auf Redis + separaten
  Worker umsteigen.
* **Keine öffentlichen Dateien.** Videos liegen nur im Container (`DATA_DIR`) und werden
  ausschließlich über Endpunkte ausgeliefert, die ein gültiges Login-Cookie verlangen.
  Job-IDs sind zufällige UUIDs.
* **Original bleibt unverändert.** Die hochgeladene Datei wird schreibgeschützt abgelegt
  und nur gelesen; alle Ergebnisse sind neue Dateien. Das Video auf dem Handy wird ohnehin
  nie verändert.
* **Funktioniert auch ohne KI-Schlüssel.** Ohne `OPENAI_API_KEY` gibt es keine Untertitel,
  Highlights werden dann nur über Lautstärke und Szenenwechsel gewählt, Pausen über Stille
  erkannt. Ohne `ANTHROPIC_API_KEY` wird nur die kostenlose Heuristik genutzt.
* **Soundeffekte werden von FFmpeg erzeugt** (gefiltertes Rauschen) – dadurch gibt es keine
  Lizenzfragen. Musik wird nur verwendet, wenn du selbst lizenzierte Dateien bereitstellst.

## 2. Kostenüberblick (Stand der Preise: bitte vor Nutzung selbst prüfen)

Preise ändern sich. Die Zahlen unten sind Schätzungen, die ich nicht live überprüfen
konnte. Prüfe sie auf den Preis-Seiten von Render, OpenAI und Anthropic.

### Laufende Kosten pro Video

| Posten | Annahme | Kosten |
|---|---|---|
| Transkription (OpenAI `whisper-1`) | ca. 0,006 USD pro Audio-Minute | 20-Min-Video ≈ **0,12 USD** |
| Highlight-Auswahl (Claude, optional) | ca. 5 000–15 000 Eingabe-Token, ~1 500 Ausgabe-Token. Modell `claude-opus-5-5`: 4 USD / 1 Mio. Eingabe, 20 USD / 1 Mio. Ausgabe | ≈ **0,05–0,10 USD** |
| Videoschnitt (FFmpeg) | läuft auf deinem Server | 0 USD extra |
| **Summe** | 20-Min-Video | **≈ 0,15–0,25 USD** |

Die App rechnet diese Schätzung vor jedem Job aus und verweigert den Job, wenn das
Tagesbudget (`DAILY_BUDGET_USD`, Standard 2 USD) überschritten würde.

### Server (Render)

| Render-Plan | RAM / CPU (ungefähr) | Preis (ungefähr) | Eignung |
|---|---|---|---|
| Free | 512 MB / 0,1 CPU | 0 USD | Nur zum Ausprobieren der Oberfläche. Schläft nach Inaktivität ein; Videoverarbeitung extrem langsam und kann abbrechen. |
| Starter | 512 MB / 0,5 CPU | ca. 7 USD/Monat | Kurze Videos (bis ~10 min) möglich, aber langsam. |
| Standard | 2 GB / 1 CPU | ca. 25 USD/Monat | **Empfohlen** für 20–30-Min-Videos. |

Richtwert für die Rechenzeit: Pro fertigem Short (30–60 s) braucht FFmpeg auf 1 CPU grob
1–3 Minuten. 5 Shorts aus einem 20-Min-Video also etwa 10–20 Minuten.

**Wichtig:** Bevor kostenpflichtige Pläne oder API-Schlüssel eingerichtet werden, frage ich
dich um Zustimmung. Die Datei `render.yaml` ist standardmäßig auf den **kostenlosen** Plan
eingestellt.

### Eingebaute Kostenbremsen

| Variable | Standard | Wirkung |
|---|---|---|
| `MAX_UPLOAD_MB` | 500 | Größere Uploads werden abgelehnt |
| `MAX_VIDEO_MINUTES` | 30 | Längere Videos werden abgelehnt |
| `DAILY_BUDGET_USD` | 2.0 | Tageslimit für geschätzte API-Kosten |
| `MAX_CLIPS` | 5 | Höchstzahl Shorts pro Video |
| `RETENTION_HOURS` | 6 | Danach werden alle Dateien gelöscht |
