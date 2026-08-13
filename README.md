# Datenbank-Repository Winterreise

## Aufnahmen und Noten

`scores/` enthält die Noten mitsamt handschriftlicher Notizen Fischer-Dieskaus.

`performances/` enthält die Aufführungsdaten zu 26 Aufnahmen Fischer-Dieskaus der *Winterreise*. Jede Aufnahme hat einen eigenen Ordner mit je 24 Dateien (in der Regel), der nach dem Namen des Pianisten, Aufführungsjahr und ggf. Aufführungsort betitelt ist. Die Datei `performances.json` enthält wiederum Metadaten zu diesen Aufführungen.

## Statistik-Pipeline

Der Workflow läuft bei jedem Push auf main, der Dateien unterhalb von performances/ verändert; unabhängig davon, wie viele Commits der Push enthält. Zusätzlich lässt er sich jederzeit von Hand über Actions → Build statistics → Run workflow auslösen. Enthält die Commit-Message [skip ci], wird der Lauf übersprungen.

### Was erzeugt wird

```
stats/corpus.json              korpusweite Streuungsmaße (MAD) und Tempo-Mediane
stats/recordings/<name>.json   Mediane je Aufnahme: liedweise und gesamter Zyklus 
stats/report.json              Datenqualitätsbericht
```

Das Skript kommt ohne externe Abhängigkeiten aus — `xml.etree.ElementTree`und `statistics` genügen. Deshalb entfällt im Workflow jeder Installationsschritt.

### Der Qualitätsbericht

`stats/report.json` meldet zwei Klassen von Auffälligkeiten. *Problems* sind harte Fehler: nicht parsebare Dateien, Dateien ohne Schlagzeilen. *Warnings* betreffen Datenlagen, die Vergleiche verzerren können:

- Ein Lied, dessen Anzahl verwertbarer Tempowerte zwischen den Aufnahmen um   mehr als fünf Prozent auseinanderliegt. Das ist die Konstellation, die DTW-Distanzen systematisch verzerrt, weil die kürzere Sequenz mehr Dehnungsschritte erfordert und deren Kosten in die Summe eingehen.
- Ein Lied, bei dem weniger als neunzig Prozent der Schläge einen verwertbaren Wert tragen.

Beide erscheinen zusätzlich als Annotationen im Actions-Protokoll.

