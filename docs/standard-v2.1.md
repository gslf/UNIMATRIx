# Standard v2.1: vocabolario, errori e pesi

18 ottobre 2026. **Standard v2.1** (`standard-v2.1`) è il villaggio predefinito.
Vocabolario, feedback e conteggio delle dipendenze valgono per ogni villaggio, inclusi Hell
ed eventuali ricette personalizzate. Non esiste un'opzione per riattivare il vecchio protocollo.
La ricetta Standard v2 è stata rimossa. I vecchi log restano registrazioni storiche: il motore
corrente non ne garantisce il replay e non li trasforma in risultati di Standard v2.1.
La penalità per collasso resta il 10% del lordo di ciascun seed.
L'Explorer accetta esclusivamente run di modelli; i bot servono alla calibrazione nel Lab.

## Contratto delle azioni

- `sealed_bid`, `sealed_bids`, `sealed bids`, `sealed-bid` e differenze di maiuscole
  convergono a `sealed_bid`. La stessa canonicalizzazione vale per ricetta, mercanti,
  convenzione ufficiale e azioni. I residenti possono quindi adottare la convenzione corretta.
- Le convenzioni sconosciute vengono rifiutate con l'elenco dei nomi validi. Il vocabolario
  non rivela quale convenzione sarà richiesta in futuro. Una dichiarazione che non coincide
  con la regola corrente restituisce un avviso.
- Uno scambio senza `bid` rimane un errore quando il mercante lo richiede, ma il feedback
  riporta il campo e il valore esatti. Impostare una convenzione non compila implicitamente
  gli scambi successivi. Dei 12 rifiuti commerciali storici di Flash, sei omettevano il campo
  e sei usavano il plurale: non erano tutti errori di una singola lettera.
- Il feedback distingue protocollo, merce non supportata, disponibilità del mercato,
  risorse insufficienti, progetto non avviato e disputa inesistente. Gli errori di risorse
  espongono disponibilità e fabbisogno numerici; i prezzi fissi del mercato sono mostrati
  senza l'arrotondamento a due decimali che nascondeva `3.125` dietro `3.12`.
- Il prompt esplicita l'ordine dell'array, il costo delle azioni rifiutate, l'assenza di
  ripianificazione intermedia e il fatto che la nota precede l'esecuzione. I risultati
  ricevuti e le risorse correnti prevalgono sulle aspettative annotate.

## Conteggio delle dipendenze

Ogni rifiuto ha `code`, `kind`, indici delle azioni precedenti (`caused_by`) e radici
della catena (`root_actions`). Gli indici salvati partono da zero, quelli mostrati da uno.
Le categorie sono `initial`, `consequential`, `unclassified`; la loro somma è il totale
dei rifiuti. Si contano azioni, non presunti errori mentali indipendenti del modello.

Una conseguenza è **inferita** soltanto quando un'azione fallita precedente nello stesso
batch avrebbe fornito la risorsa mancante secondo i parametri richiesti. La disponibilità
ipotetica è finita e viene riservata, così una vendita non giustifica doni illimitati.
Un progetto non avviato può dipendere da un `build` già fallito; un `build` successivo
non giustifica retroattivamente assegnazioni premature. Nessuna dipendenza attraversa i turni.

Questa è un'analisi delle dipendenze pianificate, **non** la prova che uno scambio corretto
sarebbe riuscito, né una simulazione alternativa. Le annotazioni non modificano effetti,
risorse o punti e non introducono una seconda penalità sui rifiuti. Una risposta con
controfferta rimane un'azione eseguita: questo conteggio riguarda i rifiuti del motore.

### Risultati già registrati

Report, CSV ed Explorer espongono i conteggi salvati nel run. L'audit storico ha rilevato
33 rifiuti per Flash (24 iniziali e 9 conseguenti) e 17 per 27B (13 iniziali, 3 conseguenti,
1 non classificabile con i dati disponibili). Sono risultati del vecchio protocollo,
non prestazioni misurate con le correzioni correnti. Il sistema non ricostruisce vecchi
saldi da messaggi testuali e non mantiene percorsi di esecuzione compatibili.

## Ribilanciamento dei domini

Standard v2.1 e Hell usano gli stessi nuovi tassi, mai adattati ai modelli in classifica.
La calibrazione di ogni ricetta, anche personalizzata, usa il criterio descritto sotto;
i tassi delle ricette personalizzate restano modificabili nel Lab.
Per D1, D2 e D4–D8: `1000 / media delle unità naturali dell'expert sui seed 1–5`.
Il vecchio expert costruisce poco: usarlo da solo per D3 suggerirebbe ancora un tasso
di circa 5,8 e riprodurrebbe il problema. D3 usa anche il potenziale della ricetta:
`52 settimane × somma dei valori delle 8 opere = 1508 unità`, più eventuali bonus D3
positivi (zero qui). Il tasso è il minore tra quello dell'expert e `1000 / 1508`.
Non si impone un tetto al punteggio: restano tassi lineari per le unità accumulate.

| Dominio | Tasso v2 | Tasso v2.1 |
| --- | ---: | ---: |
| D1 Informazione | 1,25 | 1,2818 |
| D2 Economia | 0,5 | 0,4520 |
| D3 Opere | 6 | **0,6631** |
| D4 Lago | 0,9 | 0,8794 |
| D5 Relazioni | 1,15 | 1,1593 |
| D6 Istituzioni | 2,2 | 2,1867 |
| D7 Trasmissione | 1,4 | 1,4501 |
| D8 Adattamento | 2,2 | 2,1805 |

Solo come analisi di sensibilità, applicare i tassi v2.1 al ledger storico di Flash
ridurrebbe D3 da 7164 a 791,741 punti, dal 58,46% al 13,45% del lordo ripesato.
**Non è un nuovo risultato del modello**: la correzione del protocollo richiede nuovi run.
Non si gonfia D8 per compensare un adattamento effettivamente fallito; il suo valore torna
significativo quando la convenzione corretta viene adottata. Sui seed di validazione 6–10,
mai usati per fissare i tassi, D8 vale in media 979,480 per l'expert e 1143,454 per l'oracle.
L'expert mantiene D3 basso (113,390), coerentemente con la sua scarsa costruzione.

La scelta assegna circa 1000 punti al pieno potenziale delle opere e agli altri domini
del riferimento. È un criterio esplicito di bilanciamento, non una stima empirica dell'intelligenza.
Un singolo seed e i bot di riferimento non bastano a validare una misura generale di abilità.

## Riprodurre le verifiche

```powershell
.venv\Scripts\python.exe tests/tools/calibrate_standard_v2_1.py --output docs/standard-v2.1-calibration.json
.venv\Scripts\python.exe -m pytest tests/test_polis.py tests/test_polis_explorer.py tests/test_polis_protocol.py
```

[Calibrazione e validazione complete](standard-v2.1-calibration.json). Nessuna nuova chiamata
a modelli è stata usata per la calibrazione o per la migrazione storica.

La calibrazione è riproducibile senza chiamate a modelli. I test coprono entrambi i
villaggi inclusi, il vocabolario delle ricette personalizzate, le dipendenze entro il batch
e il rifiuto dei run di riferimento in esportazione e importazione nell’Explorer.
