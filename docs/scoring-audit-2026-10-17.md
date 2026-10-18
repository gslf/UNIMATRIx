# Audit del punteggio e confronto Flash Next / 27B

17 ottobre 2026. Dati: i quattro run Society completati, `standard-v2`, seed 1,
ricetta SHA-256 `b409b10e685cb1726a1c988e9c37c3604b6e75103b18f434e2af1124245ea1c2`.
Analisi delle decisioni, dei riscontri del motore, dei domini e delle traiettorie salvate.
Nessuna nuova chiamata ai modelli. La ricetta e il protocollo di gioco restano congelati.

**Il precedente vantaggio del 27B non dimostra che Flash Next sia meno capace.**
Il 27B aveva 407,201 punti lordi in più, ma collassa per fame all'ultima settimana.
Flash termina senza collasso e passa davanti con la penalità del 10%.
Il confronto misura queste due esecuzioni: un solo seed, prompt e configurazioni
di inferenza diversi non consentono di attribuire causalmente il risultato alla dimensione.

## Penalità scelta e sensibilità

Per ogni seed, `earned` è la somma dei punti salvati degli otto domini, arrotondata
a tre decimali. Se il seed collassa, `penalty = round(earned × 0,10, 3)`;
altrimenti è zero. `total = round(earned − penalty, 3)`. La media dei `total`
è il risultato del run. Identificativo: `accumulated-collapse-10pct-v2`.
`forfeited` rimane un alias della penalità per i lettori del formato precedente.

| Strategia | Vantaggio | Problema |
| --- | --- | --- |
| −2.000 punti, minimo zero | Impatto assoluto immediato | Un run da 1.000 viene azzerato, uno da 100.000 perde solo il 2%; sensibile a durata e cambio di unità |
| −10% del lordo | Stessa severità relativa, mantiene la graduazione e la scala illimitata | Un ottimo run collassato può ancora superare uno mediocre sopravvissuto |
| Massimo fra 2.000 e 10% | Mantiene un minimo assoluto | Conserva la distorsione per punteggi bassi e aggiunge una soglia arbitraria |
| Azzeramento o sopravvivenza come primo criterio | Sopravvivere domina ogni altro obiettivo | Appiattisce capacità molto diverse e reintroduce il problema della regola originaria |

Scelgo il 10% come giudizio esplicito sul valore del fallimento, non come coefficiente
statisticamente stimato da quattro run. Un collassato deve produrre oltre l'11,11% di
lordo aggiuntivo per superare un sopravvissuto. Il costo dell'interruzione anticipata
rimane già nei punti non maturati; la penalità aggiunge un costo terminale anche alla
settimana 52. Non applico una seconda penalità all'intero run se collassa un solo seed.
Interruzioni, pause ed errori infrastrutturali non sono collassi della società e restano
esclusi dalla classifica dei run completati.

| Penalità | 27B | Flash Next | Primo dei due |
| --- | ---: | ---: | --- |
| Nessuna | 12.661,644 | 12.254,443 | 27B |
| 5% | 12.028,562 | 12.254,443 | Flash |
| 10% | 11.395,480 | 12.254,443 | Flash |
| 15% | 10.762,397 | 12.254,443 | Flash |
| 20% | 10.129,315 | 12.254,443 | Flash |
| 2.000 punti | 10.661,644 | 12.254,443 | Flash |

Basta una penalità superiore al 3,216% per invertire questa coppia: l'esito non dipende
da aver scelto precisamente il 10%. La regola si applica indistintamente a tutti i modelli
e ai riferimenti scriptati. Le tabelle di calibrazione in `society.md` sono ricalcolate.

| Run | Esito | Lordo | Penalità | Finale |
| --- | --- | ---: | ---: | ---: |
| Flash Next · `c09e05389fb54dbf8a1c1cc5` | 52 settimane, nessun collasso | 12.254,443 | 0 | 12.254,443 |
| 27B · `fc484e27dd6e4a06874d9094` | Fame, settimana 52 | 12.661,644 | 1.266,164 | 11.395,480 |
| Muse Glimmer · `009f8058b4a94b258d05f66f` | 52 settimane, nessun collasso | 8.497,682 | 0 | 8.497,682 |
| Gemma 31B · `85c73c68c59c475f9f626876` | Lago, settimana 38 | 7.684,736 | 768,474 | 6.916,262 |

## Da dove venivano i 407 punti di vantaggio lordo del 27B

| Dominio | Flash | 27B | Flash meno 27B |
| --- | ---: | ---: | ---: |
| D1 Informazione | 898,426 | 1.313,213 | −414,787 |
| D2 Economia | 388,988 | 1.019,619 | −630,631 |
| D3 Opere pubbliche | 7.164,000 | 6.300,000 | +864,000 |
| D4 Lago | 820,305 | 1.201,341 | −381,036 |
| D5 Relazioni | 1.015,507 | 961,146 | +54,361 |
| D6 Istituzioni | 866,417 | 808,925 | +57,492 |
| D7 Trasmissione | 1.015,000 | 956,200 | +58,800 |
| D8 Adattamento | 85,800 | 101,200 | −15,400 |

Flash è avanti in quattro domini. Completa il canale alla settimana 6 contro 12,
la scuola a 12 contro 16, il mercato a 18 contro 25 e la clinica a 22 contro 30.
Queste anticipazioni spiegano il vantaggio nelle settimane di servizio D3.
Il 27B prevale in informazione, ricchezza e produzione del lago.

**D1:** il 27B esegue 28 annunci contro 15. Annuncia la siccità come vera a settimana
10 e 14, Flash a 15; ribadisce più volte le smentite delle voci. D1 accumula settimane
di credenze corrette, perciò tempi e ripetizioni contano. Questa è una spiegazione
coerente con la traiettoria, non una misura causale dell'effetto di ciascun annuncio.
Le previsioni effettivamente valutate valgono soltanto 30,713 punti D1 nel 27B e
29,676 in Flash: quasi tutto il divario D1 viene dalle credenze dei residenti.

**D2 e D4:** Flash termina con patrimonio 2.309,475 contro 3.570,738 del 27B.
Il suo lago è al 90,50% contro 83,22%, ma D4 misura raccolto più variazione dello stock,
non soltanto pesci rimasti. Un lago più pieno non implica un D4 superiore. Non emerge
un errore aritmetico nel ledger registrato. Il replay di Flash coincide; quello del 27B presenta la divergenza descritta sotto.

**Sopravvivenza:** la fame deve colpire almeno il 30% dei presenti per tre settimane
consecutive. Il 27B registra 30%, 30%, 30% nelle settimane 50–52; Flash 25%, 20%, 30%.
La diversa classificazione è corretta rispetto alla soglia. Flash comunque finisce
con granaio vuoto e il 30% affamato: sopravvive all'orizzonte, non dimostra sostenibilità
oltre l'anno. Non ho esteso l'orizzonte né modificato le soglie.

## Errori osservabili e difetti del benchmark

Entrambi emettono 312 azioni. Flash ne vede rifiutate 33 (10,58%), il 27B 17 (5,45%).
In Flash 21 rifiuti riguardano il commercio, contro 5 del 27B.

1. **Una trappola lessicale pesa sull'adattamento.** L'indizio parla di “sealed bids”.
   Entrambi adottano `sealed_bids` alla settimana 32. Il motore accetta il nome come
   convenzione ufficiale ma richiede il confronto letterale con `sealed_bid` per
   l'adozione corretta e per gli scambi. La risposta ai tentativi sbagliati è generica.
   Flash subisce 12 rifiuti per protocollo, il 27B 2; quest'ultimo usa molto di più il
   mercato locale, che evita quel controllo. Non si può attribuire tutto il divario
   economico a questa causa, ma il benchmark mescola adattamento e indovinare una stringa.
2. **Errori di esecuzione reali di Flash.** Alla settimana 19 assegna tre persone alla
   clinica prima di averla avviata. Tenta di vendere strumenti al mercato locale, che
   tratta solo cibo, alle settimane 40, 44 e 52. Alla 52 concatena vendita impossibile,
   acquisto senza fondi e tre doni senza cibo: cinque delle sei azioni sono rifiutate.
   Il 27B commette alcuni errori analoghi, ma meno spesso. Le scelte rispondono ai vincoli
   osservati: non sono errori JSON né richieste perse dal trasporto.
3. **Feedback incompleto sulle previsioni.** Flash invia identificativi non richiesti
   in 42 settimane, il 27B in 13. Il motore li ignora senza riscontro. Entrambi rispondono
   comunque ai tre probe reali. Quindi non è corretto dire che Flash perde tutti i punti
   di forecasting: spreca output e mostra una confusione non chiarita dall'interfaccia.
4. **Il punteggio non misura in modo uniforme otto abilità.** D3 rappresenta il 58,46%
   del lordo di Flash e il 49,76% del 27B; D8 vale meno dell'1% per entrambi. Una
   compensazione forte tra domini può nascondere carenze. Inoltre il testo libero di
   `say` non è giudicato semanticamente: contano soprattutto azione, argomenti e regole
   fisse. Una risposta verbalmente brillante non equivale a un intervento efficace.
5. **La sola memoria persistente è la nota di 1.500 caratteri.** Le note emesse qui
   restano sotto il limite: massimo 1.084 per Flash, 1.091 per 27B. Non emerge un
   troncamento delle note, ma la prova misura anche come il modello conserva lo stato
   in questo formato, non tutte le sue capacità di pianificazione con altri strumenti.

## Dove ho sbagliato nella valutazione precedente

Il primo errore era passare dall'azzeramento alla conservazione integrale del punteggio:
così un collasso all'ultima settimana diventava praticamente gratuito. Il secondo era
leggere il totale come una misura generale della capacità senza rendere abbastanza
visibili lo sbilanciamento dei domini e i limiti del protocollo.

C'è poi un problema verificato di comparabilità che il nuovo punteggio **non cancella**:

| Condizione registrata | Flash | 27B |
| --- | --- | --- |
| Prompt sul collasso | Conservi i punti accumulati | Tutto il punteggio diventa zero |
| Output vincolato | `false` | `true` |
| Esecuzione | Endpoint compatibile HTTP, contesto dichiarato in byte | LM Studio gestito, contesto caricato 50.176 token |
| Engine fingerprint storico | `cbcec246f7…` | `6bb082879c…` |
| Token generati dichiarati dal provider | 764.706 | 878.170 |
| Secondi registrati | 10.145,6 | 24.334,4 |

I due prompt hanno hash distinti per tutte le 52 decisioni. Il 27B ha ricevuto
un incentivo più forte a evitare il collasso, senza riuscirci. Non sappiamo come
avrebbe giocato con il prompt di Flash. Tutte le risposte terminano con `stop`, senza
errori di parsing né retry; gli input massimi registrati sono 3.328 e 3.302 token.
Non c'è evidenza nei log di troncamento, ma contesti e budget non sono misure equivalenti.
Flash non espone il contatore dei reasoning token: non va interpretato come zero ragionamento.
I tempi non sono confrontabili come velocità intrinseca senza controllare hardware e backend.

Il nome `iq3_s` suggerisce una quantizzazione diversa, ma i run non contengono hash dei
pesi, architettura certificata e confronto a precisione equivalente. Attribuire il risultato
alla quantizzazione, ai parametri attivi o a un peggioramento del modello sarebbe una
congettura. La dimensione dichiarata dall'utente non basta a isolare nessuna di queste cause.

Correzione del controllo di replay: tre run (Flash, Muse, Gemma) coincidono per osservazioni,
frame, domini e collasso. Il 27B diverge alla settimana 4: il log contiene un messaggio di Bruno
assente nel replay corrente. La divergenza era presente anche negli audit salvati prima e dopo
la penalità: la precedente affermazione di quattro replay identici era errata. Per il 27B
restano autorevoli le osservazioni e i frame registrati; non si ricostruiscono nuovi esiti.
Questi controlli non rendono uguali prompt o trasporti.

## Soluzioni proposte nel primo intervento

Aggiornamento: vocabolario, feedback, audit dei rifiuti e pesi sono ora implementati nella
[Standard v2.1](standard-v2.1.md). Il contratto delle azioni vale per tutti i villaggi;
non si conserva un motore compatibile con i vecchi run. Il testo seguente descrive
le proposte al momento della sola introduzione della penalità.

- Per una futura coorte, fissare prompt completo, schema, backend, template, precisione,
  contesto e budget; salvare hash dei pesi e impostazioni effettive. Confrontare più seed
  appaiati e repliche prima di parlare di superiorità generale. Non mescolare quella
  coorte con questi risultati senza identificarla.
- Accettare alias semantici di `sealed_bid`, oppure esporre il vocabolario valido dopo
  un'indagine riuscita. Se l'obiettivo è scoprire la nuova regola, valutare quella scoperta,
  non il singolare/plurale del token. Avvisare quando una convenzione dichiarata non ha effetto.
- Restituire errori espliciti per probe sconosciuti e chiarire disponibilità del mercato,
  natura persistente delle assegnazioni e dipendenze tra azioni. Confrontare in un esperimento
  separato la capacità con e senza queste facilitazioni.
- Per una futura versione della ricetta, studiare il peso effettivo dei domini, la
  sostenibilità alla fine dell'orizzonte e la fragilità vicino alle soglie. Conservare
  sempre totale, domini, sopravvivenza e benessere come misure distinte. Non ripesare
  oggi D3 o D8 e non prolungare oggi l'inverno.

La nuova penalità migliora l'interpretazione di questa leaderboard; non risolve da sola
tutti i problemi di validità del benchmark. Le proposte sopra richiedono una nuova
versione o coorte e non sono state implementate in questo intervento.

## Riproducibilità e migrazione

`tests/tools/audit_society_runs.py --output runs/scoring-audit.json` legge i run completati,
misura errori e traiettorie e verifica il replay senza provider né scritture ai run.
`unimatrix rescore --explorer-dir explorer` applica la nuova valutazione dai domini salvati,
con backup, aggiorna report per seed e aggregati, risultati e indice xCDN. Una seconda
esecuzione non sottrae nuovamente la penalità. Il punteggio finale del replay usa il
totale valutato, mentre frame, cronologia e domini mantengono il lordo originale.
L'export CSV espone lordo, penalità, finale e versione della regola separatamente.

Verifiche: 48 test Society/Explorer superati, inclusi migrazione di seed misti,
idempotenza, backup, collasso all'ultima settimana, scala illimitata e export CSV/xCDN;
lint e sintassi JavaScript validi. Verificati nel browser classifica, report e HUD del
replay alla settimana 52. Audit prima/dopo identico per tutti e quattro i run: log,
frame, domini, ricette, configurazioni, fingerprint storici e timestamp preservati.
La suite generale ha inoltre evidenziato un test estraneo allo scoring che pretende
permessi POSIX `0600` su Windows (`test_model_editor_keeps_credentials_apart`).

I vecchi file sotto `runs/benchmarks/` appartengono a `standard-v1`, al benchmark a
episodi separati e alla metrica `reference-gain-v2`: non fanno parte della leaderboard
Society corrente. Non hanno il ledger/collasso di questa simulazione; convertirli con
la stessa sottrazione sarebbe inventare una corrispondenza fra misure differenti.
