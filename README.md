# Winterreise Database

*[English](#english) · [Deutsch](#deutsch)*

---

## English

Data repository of the application
[Winterreise Performance Visualization](https://github.com/cvoncram/Winterreise-data-vis):
scores and per-beat performance data of 26 recordings of Schubert's
*Winterreise* by Dietrich Fischer-Dieskau (1948–1990). The application reads
this repository through the GitHub API.

### Contents

```
scores/                        24 MEI scores (1-Gute_Nacht.mei … 24-Der_Leiermann.mei)
performances/                  one folder per recording
  <year>_<pianist>[_<place>]/  one file per song: <folder>_<song>_Audio_Data.xml
  performances.json            metadata of the recordings
stats/                         normalization statistics and data-quality report
.github/workflows/             statistics pipeline
```

**`scores/`**: the scores including Fischer-Dieskau's handwritten entries,
encoded in MEI.

**`performances/`**: 26 folders, usually with 24 files each. Each file mirrors
the measures of the score; per measure, an `<add>` block holds one value per
beat: `<tempo mm="…">` (bpm), `<dynam n="…">` (dBFS) and
`<annot label="spectral centroid (Hz)" n="…">` (timbre). A calibration block
(`<application xml:id="sv_to_mei">`) holds the sample rate and related figures.

**`performances.json`**: one entry per recording with number, date, pianist
(with GND number), label, place, coordinates, recording context, source and
further information.

### Statistics pipeline

`stats/build_stats.py` computes the reference figures for normalization. The
workflow `Build statistics` runs it on every push to `main` that changes
`performances/`, the script or the workflow, and commits the results with
`[skip ci]`. It can also be started by hand under Actions → Build statistics →
Run workflow. The script uses the Python standard library only.

Locally:

```bash
python3 stats/build_stats.py .
```

#### Outputs

```
stats/corpus.json              scales and tempo centres, per song and for the cycle; covariance
stats/recordings/<name>.json   dynamics and timbre centres per recording, per song and overall
stats/report.json              data-quality report
```

Every figure is computed both as mean/standard deviation and as median/scaled
MAD. Tempo and timbre are log-transformed. Dynamics and timbre are centred per
recording, tempo across all recordings. The application uses mean/standard
deviation by default.

The covariance of tempo, dynamics and timbre (natural units, beats with all
three values, at least 200 beats) is used for the Mahalanobis distance.

#### Data-quality report

`stats/report.json` lists:

- **problems**: unreadable files, files without beat values, recordings
  without usable songs;
- **warnings**: songs whose number of usable tempo values differs by more than
  5 % between recordings, and songs in which less than 90 % of the beats carry
  a value;
- **spreadRatios**: ratio of standard deviation to MAD per song and parameter.

Problems and warnings also appear as annotations in the Actions log.

Note: This documentation was generated with the help of Generative AI.

---

## Deutsch

Daten-Repositorium der Anwendung
[Winterreise Performance Visualization](https://github.com/cvoncram/Winterreise-data-vis):
Partituren und schlagweise Aufführungsdaten von 26 Aufnahmen der
*Winterreise* von Schubert mit Dietrich Fischer-Dieskau (1948–1990). Die
Anwendung liest dieses Repositorium über die GitHub-API.

### Inhalt

```
scores/                        24 MEI-Partituren (1-Gute_Nacht.mei … 24-Der_Leiermann.mei)
performances/                  ein Ordner je Aufnahme
  <Jahr>_<Pianist>[_<Ort>]/    eine Datei je Lied: <Ordner>_<Lied>_Audio_Data.xml
  performances.json            Metadaten der Aufnahmen
stats/                         Normalisierungsgrößen und Datenqualitätsbericht
.github/workflows/             Statistik-Pipeline
```

**`scores/`**: die Partituren einschließlich der handschriftlichen
Eintragungen Fischer-Dieskaus, in MEI kodiert.

**`performances/`**: 26 Ordner mit in der Regel je 24 Dateien. Jede Datei
bildet die Takte der Partitur ab; je Takt enthält ein `<add>`-Block einen Wert
pro Schlag: `<tempo mm="…">` (bpm), `<dynam n="…">` (dBFS) und
`<annot label="spectral centroid (Hz)" n="…">` (Klangfarbe). Ein
Kalibrierungsblock (`<application xml:id="sv_to_mei">`) enthält die Abtastrate
und zugehörige Werte.

**`performances.json`**: ein Eintrag je Aufnahme mit Nummer, Datum, Pianist
(mit GND-Nummer), Label, Ort, Koordinaten, Aufnahmekontext, Quelle und
weiteren Informationen.

### Statistik-Pipeline

`stats/build_stats.py` berechnet die Bezugsgrößen für die Normalisierung. Der
Workflow `Build statistics` führt es bei jedem Push auf `main` aus, der
`performances/`, das Skript oder den Workflow ändert, und committet die
Ergebnisse mit `[skip ci]`. Von Hand lässt er sich unter Actions → Build
statistics → Run workflow starten. Das Skript nutzt nur die
Python-Standardbibliothek.

Lokal:

```bash
python3 stats/build_stats.py .
```

#### Ausgaben

```
stats/corpus.json              Skalen und Tempo-Zentren, je Lied und für den Zyklus; Kovarianz
stats/recordings/<name>.json   Zentren für Dynamik und Klangfarbe je Aufnahme, je Lied und gesamt
stats/report.json              Datenqualitätsbericht
```

Jede Größe wird sowohl als Mittelwert/Standardabweichung als auch als
Median/skalierte MAD berechnet. Tempo und Klangfarbe werden logarithmiert.
Dynamik und Klangfarbe werden je Aufnahme zentriert, Tempo über alle
Aufnahmen. Die Anwendung verwendet standardmäßig Mittelwert/Standardabweichung.

Die Kovarianz von Tempo, Dynamik und Klangfarbe (natürliche Einheiten,
Schläge mit allen drei Werten, mindestens 200 Schläge) dient der
Mahalanobis-Distanz.

#### Datenqualitätsbericht

`stats/report.json` enthält:

- **problems**: nicht lesbare Dateien, Dateien ohne Schlagwerte, Aufnahmen
  ohne verwertbare Lieder;
- **warnings**: Lieder, deren Anzahl verwertbarer Tempowerte zwischen den
  Aufnahmen um mehr als 5 % abweicht, und Lieder, in denen weniger als 90 %
  der Schläge einen Wert tragen;
- **spreadRatios**: Verhältnis von Standardabweichung zu MAD je Lied und
  Parameter.

Problems und Warnings erscheinen zusätzlich als Annotationen im
Actions-Protokoll.

Anmerkung: Diese Dokumentation wurde mithilfe von generativer KI erstellt.
