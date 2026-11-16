# AutoShorts – aus langen Videos automatisch Shorts machen

Du lädst auf dem Handy ein langes Video hoch und bekommst **3 bis 5 fertig geschnittene
Hochkant-Clips (1080×1920, 15–60 Sekunden)** für TikTok, Instagram Reels und YouTube Shorts –
mit Wort-für-Wort-Untertiteln, entfernten Pausen, dezenten Zooms und Soundeffekten.
Alles als einzelne MP4-Dateien oder gesammelt als ZIP.

> **Status: Prototyp.** Was getestet ist und was nicht, steht unten im Abschnitt
> [„Was ist getestet?“](#was-ist-getestet).

---

## Was die App macht

1. **Upload** (MP4 oder MOV) mit Fortschrittsbalken – direkt aus der Handy-Galerie.
2. **Spracherkennung** mit Zeitstempel für jedes Wort (OpenAI Whisper, optional).
3. **Spannende Momente finden:** Kandidaten von 15–60 s an Satzgrenzen werden bewertet nach
   *Hook* (packt der erste Satz?), *Emotion*, *Überraschung* und *Vollständigkeit*.
   Dazu fließen Lautstärke/Intensität und Szenenwechsel ein. Optional entscheidet Claude (KI).
4. **Schnitt:** lange Pausen, „äh/ähm“ und Wortwiederholungen („ich ich“) werden entfernt.
5. **9:16-Bildausschnitt:** Gesichter werden erkannt und im Bild gehalten, sonst Bildmitte.
6. **Untertitel:** groß, mittig unten, das gerade gesprochene Wort leuchtet gelb.
7. **Effekte:** abwechselnde leichte Zooms (Punch-in) mit leisem „Whoosh“, optional Musik.
8. **Download:** Vorschau im Browser, einzelne MP4 oder alle als ZIP.

Dein Originalvideo wird **nie verändert** – die App liest nur eine Kopie und löscht diese
nach der Verarbeitung.

## Was du brauchst

| Was | Pflicht? | Wozu | Kosten |
|---|---|---|---|
| GitHub-Konto | ja | hier liegt der Code | kostenlos |
| Render-Konto | ja | hier läuft die App | kostenlos bis ca. 25 USD/Monat (siehe unten) |
| OpenAI-API-Schlüssel | empfohlen | Spracherkennung → Untertitel, bessere Auswahl | ca. 0,006 USD pro Videominute |
| Anthropic-API-Schlüssel | optional | KI wählt die Highlights (Claude) | ca. 0,05–0,10 USD pro Video |

**Ohne API-Schlüssel** funktioniert die App auch – dann aber ohne Untertitel, und die
Highlights werden nur nach Lautstärke und Szenenwechseln gewählt.

Die ausführliche Kostenaufstellung und die Architektur stehen in
[`docs/ARCHITEKTUR.md`](docs/ARCHITEKTUR.md).

---

## Bereitstellung auf Render – Schritt für Schritt nur mit dem Handy

> ⚠️ **Bevor irgendetwas kostet, entscheidest du.** Die mitgelieferte Konfiguration
> (`render.yaml`) nutzt den **kostenlosen** Plan. Ein Upgrade machst du nur selbst und
> bewusst (Schritt 7).

### Schritt 1 – Code in den Hauptzweig bringen
Der Code liegt zunächst in einem eigenen Zweig (Branch). Render nimmt standardmäßig den
Hauptzweig (`main`).
1. Öffne in der GitHub-App oder im Handy-Browser dein Repository.
2. Tippe auf **Pull requests** → den Pull Request dieses Projekts öffnen → **Merge pull request**
   → **Confirm merge**. (Wenn noch kein Pull Request existiert, kann ich ihn auf Wunsch anlegen.)
3. Alternative ohne Merge: In Schritt 3 kannst du bei Render den Branch
   `claude/dazzling-heisenberg-muaoxi` direkt auswählen.

### Schritt 2 – Render-Konto anlegen
1. Öffne im Handy-Browser **render.com** → **Get Started**.
2. Wähle **Sign up with GitHub** und erlaube den Zugriff.
3. Beim Plan nichts Kostenpflichtiges auswählen.

### Schritt 3 – Blueprint anlegen
1. Im Render-Dashboard oben auf **New +** (ggf. im Menü ☰) → **Blueprint**.
2. Repository **autoshort-** auswählen (falls es fehlt: **Configure GitHub** und das Repo freigeben).
3. Render liest automatisch die Datei `render.yaml` und zeigt den Dienst **autoshorts** an.

### Schritt 4 – Geheimnisse eintragen
Render fragt jetzt nach Werten:
* **APP_PASSWORD**: dein Passwort für die App. Nimm etwas Langes (z. B. 4 zufällige Wörter).
* **OPENAI_API_KEY**: optional. Leer lassen, wenn du (noch) keinen hast.
* **ANTHROPIC_API_KEY**: optional. Leer lassen, wenn du keinen hast.

Diese Schlüssel liegen **nur auf dem Server** (als Umgebungsvariablen) – nie im Code und nie
im Browser. Tippe auf **Apply**.

### Schritt 5 – Warten
Der erste Build dauert ca. 5–10 Minuten. Unter **Events/Logs** siehst du den Fortschritt.
Wenn oben **Live** steht, ist die App bereit.

### Schritt 6 – Benutzen
1. Oben im Dienst steht eine Adresse wie `https://autoshorts-xxxx.onrender.com` – antippen.
2. Passwort eingeben → **Video auswählen** → **Hochladen & Shorts erstellen**.
3. Tipp: Im Browser-Menü **„Zum Home-Bildschirm“** wählen, dann ist die App wie eine echte App erreichbar.
4. Die Seite zeigt den Fortschritt. Wenn „Fertig“ steht: Vorschau ansehen, **MP4 laden** oder
   **Alle als ZIP**.

### Schritt 7 – (Optional, kostenpflichtig) Mehr Leistung
Der kostenlose Plan ist sehr schwach (wenig CPU, 512 MB RAM, schläft nach ca. 15 Minuten ohne
Aufrufe ein). Zum Ausprobieren mit kurzen Videos (1–3 Min.) reicht er vielleicht; für 20–30-
Minuten-Videos ist er zu langsam. **Lass die Seite während der Verarbeitung offen** – das hält
den Server wach.

Wenn du mehr Leistung willst: Im Dienst → **Settings** → **Instance Type** → z. B. *Standard*.
Das kostet monatlich Geld (siehe Render-Preisseite). **Ich richte keine kostenpflichtigen
Ressourcen ohne deine ausdrückliche Zustimmung ein.**

### API-Schlüssel später hinzufügen oder ändern
Render → Dienst **autoshorts** → **Environment** → Variable bearbeiten → **Save Changes**.
Render startet die App dann neu.

* OpenAI-Schlüssel: platform.openai.com → API keys. Lege dort ein **Ausgabenlimit** fest.
* Anthropic-Schlüssel: console.anthropic.com → API Keys. Lege dort ebenfalls ein **Ausgabenlimit** fest.

---

## Einstellungen (Umgebungsvariablen)

| Variable | Standard | Bedeutung |
|---|---|---|
| `APP_PASSWORD` | – | **Pflicht.** Passwort für die Weboberfläche |
| `SECRET_KEY` | zufällig | Signiert das Login-Cookie (Render erzeugt ihn automatisch) |
| `OPENAI_API_KEY` | – | Spracherkennung (Whisper) |
| `ANTHROPIC_API_KEY` | – | KI-Highlight-Auswahl (Claude) |
| `CLAUDE_MODEL` | `claude-opus-5-5` | Claude-Modell für die Auswahl |
| `HIGHLIGHT_AI` | `auto` | `off` = Claude nicht verwenden, auch wenn ein Schlüssel da ist |
| `VIDEO_LANGUAGE` | `de` | Sprache des Videos (z. B. `en`) |
| `MAX_UPLOAD_MB` | `500` | Maximale Dateigröße |
| `MAX_VIDEO_MINUTES` | `30` | Maximale Videolänge |
| `DAILY_BUDGET_USD` | `2` | Tageslimit für geschätzte API-Kosten |
| `MIN_CLIPS` / `MAX_CLIPS` | `3` / `5` | Anzahl der Shorts |
| `CLIP_MIN_SECONDS` / `CLIP_MAX_SECONDS` | `15` / `60` | Länge der Shorts |
| `RETENTION_HOURS` | `6` | Danach werden alle Dateien automatisch gelöscht |
| `X264_PRESET` | `veryfast` | `ultrafast` = schneller, aber größere Dateien |

## Hintergrundmusik
Lege lizenzierte Musikdateien in den Ordner [`music/`](music/LIESMICH.md) (geht auch über die
GitHub-Webseite: Ordner öffnen → **Add file → Upload files**). Nach dem nächsten Deploy
erscheinen sie in der App zur Auswahl. Ohne Dateien gibt es nur „Keine Musik“.

## Sicherheit
* Passwortschutz für alle Daten-Endpunkte; Login-Cookie `HttpOnly`, `Secure`, `SameSite=Strict`.
* Nach 10 falschen Passwörtern 15 Minuten Sperre (pro IP).
* Videos sind **nie öffentlich** erreichbar: nur über geschützte Adressen mit zufälliger Job-ID.
* Upload-Größe, Videolänge, Anzahl gleichzeitiger Aufträge und Tageskosten sind begrenzt.
* Temporäre Dateien werden nach der Verarbeitung bzw. nach `RETENTION_HOURS` gelöscht.
* Technische Fehlerdetails landen nur in den Server-Logs, die Oberfläche zeigt verständliche Meldungen.

## Grenzen des Prototyps (ehrlich)
* **Ein Server, ein Video gleichzeitig.** Startet der Server neu (z. B. Deploy, Einschlafen),
  gehen laufende Aufträge und alle Dateien verloren – du siehst dann eine Fehlermeldung und
  lädst neu hoch.
* **Gesichtserkennung** ist einfach (OpenCV, frontale Gesichter). Seitenprofile oder Objekte
  werden nicht verfolgt – dann wird die Bildmitte genommen. Der Ausschnitt wechselt nur an
  Schnittstellen, es gibt kein flüssiges Mitschwenken.
* **Versprecher**: entfernt werden Füllwörter und direkte Wortwiederholungen – keine
  inhaltlichen Versprecher oder abgebrochenen Sätze.
* **Die Highlight-Heuristik** arbeitet mit Wortlisten (vor allem Deutsch/Englisch). Mit Claude
  ist die Auswahl inhaltlich besser.
* **Upload über Mobilfunk**: große Videos brauchen lange; WLAN empfohlen.

## Was ist getestet?
Automatisiert getestet (40 Tests, `python -m pytest`), lokal in einer Linux-Umgebung:
* Schnittlogik (Pausen, Füllwörter, Wiederholungen, Zeit-Umrechnung), Untertitel-Erzeugung,
  Highlight-Auswahl (15–60 s, 3–5 Clips, keine Überschneidungen), Bildausschnitt-Geometrie.
* **Echter Durchlauf mit FFmpeg** an künstlich erzeugten Testvideos (Testbild + Ton):
  Ausgabe 1080×1920, AAC-Ton, Länge 15–60 s, Vorschaubild, ZIP-Inhalt, Original unverändert,
  temporäre Dateien gelöscht; auch für Videos ohne Tonspur.
* Web-API: Login, falsches Passwort, Sperre nach Fehlversuchen, manipuliertes Cookie,
  Upload-Größen-/Typ-Limits, ungültige Dateien, Download inkl. Range-Anfragen (wichtig für
  iPhone-Vorschau), Pfad-Tricks, Aufräumen alter Aufträge, Neustart-Verhalten, Tageslimit.
* Oberfläche einmal manuell im Browser mit iPhone-Bildschirmgröße (Playwright) durchgeklickt:
  Login, Upload, Fortschritt, Vorschau, Download-Links.

**Nicht getestet:**
* **Echte Aufrufe an OpenAI und Anthropic** – es standen keine Schlüssel zur Verfügung. Die
  Tests prüfen nur mit simulierten Antworten, dass Anfragen richtig aufgebaut und Antworten
  richtig verarbeitet werden.
* **Docker-Image und Render-Bereitstellung** – das Image konnte in meiner Umgebung nicht gebaut
  werden (kein Zugriff auf die Debian-Paketquellen). Die GitHub-Action
  (`.github/workflows/tests.yml`) baut das Image und prüft den Start; ihr Ergebnis siehst du
  auf GitHub unter **Actions**.
* **Echte Handy-Videos** (iPhone-HEVC/HDR, Hochkant mit Drehinfo, lange Videos) und echte
  Gesichter – die Gesichtserkennung wurde nur ohne Gesichter (Fallback Mitte) durchlaufen.
* Verhalten auf dem kostenlosen Render-Plan (Geschwindigkeit, Speicher, Upload-Grenzen).

## Für Entwickler: lokal starten
```bash
sudo apt-get install ffmpeg fonts-dejavu-core   # bzw. brew install ffmpeg
pip install -r requirements-dev.txt
python -m pytest                                  # Tests
APP_PASSWORD=test SECURE_COOKIES=0 uvicorn app.main:app --reload
# → http://localhost:8000
```

Projektstruktur und Abläufe: [`docs/ARCHITEKTUR.md`](docs/ARCHITEKTUR.md).
