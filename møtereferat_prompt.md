# Prompter og referatmaler

Dette dokumentet beskriver dagens promptoppsett. Selve instruksjonene ligger i
kode, slik at dokumentasjonen ikke blir en separat kopi av promptene.

## Kilder i kode

| Fil | Ansvar |
|-----|--------|
| [worker/prompts/motereferat.py](worker/prompts/motereferat.py) | Grunnprompter, møteformål og tekstmaler |
| [worker/prompts/handlinger.py](worker/prompts/handlinger.py) | Tilgjengelige scenarier, standardvalg og svarbudsjetter |
| [frontend/routes/referat.py](frontend/routes/referat.py) | Setter sammen valgt mal, møteformål og avgrensning |
| [worker/prompts/normalisering.py](worker/prompts/normalisering.py) | Deterministisk bokmål-normalisering av modelloutput |
| [frontend/compact_summary.py](frontend/compact_summary.py) | Kompakt kildekontroll av referatet |
| [frontend/summary_assignments.py](frontend/summary_assignments.py) | Konservativ håndtering av oppgaver og ansvar |

## Tekstmaler

- **Generelt møte** er standard og gir et nøytralt møtereferat.
- **Minimalt sammendrag** prioriterer hovedpoengene i inntil tre korte punkter.
- **Utfyllende sammendrag** gir en tematisk gjennomgang med oppgaver, åpne
  spørsmål og konklusjon når kilden inneholder dette.
- **Veiledermøte** skriver i vi/du-form og bruker en felles grunnprompt med
  møteformålet **Kartlegging** eller **Oppfølging**. Oppfølging er standard
  innenfor denne malen. Detaljnivået følger innholdet, ikke en fast ordgrense.
- **Samarbeidsmøte** vektlegger flere deltakere, roller og oppfølging uten å
  gjette hvem som har ansvar.
- **Egendefinert** tillater egne instruksjoner om struktur og vektlegging,
  men beholder systemreglene om kildebasert innhold.

Møteformål og tekstmal påvirker referatgenereringen, ikke transkripsjonsmodellen.
Bakgrunn fra et testscenario skal ikke legges til dersom den ikke står i kilden.

## Begrenset referat

**Begrenset §14a-referat** er et valgfritt, separat valg for Veiledermøte og er
av som standard. Det velger en mer avgrenset prompt for både ferdig referat og
løpende utkast. De eldre promptene `SYSTEM_REFERAT` og `SYSTEM_RULLERENDE`
beholdes for dette valget og for eksisterende API-handlinger.

Valget er en tekstlig avgrensning, ikke en tilgangstillatelse, en juridisk
godkjenning eller en garanti for at sensitive opplysninger blir fjernet.
Veileder må alltid gjennomgå innholdet før videre bruk eller lagring.

## Kildekontroll og begrensninger

Referatet skal bare bygge på transkripsjonen. Forslag, usikkerhet og vilkår skal
ikke bli til vedtak eller avtaler. Navn, ansvar og frister skal ikke gjettes.
Talernumre fra diarisering er estimater, ikke bekreftede identiteter.

Når hele transkripsjonen passer i modellens kontekstvindu, brukes den direkte.
Større kilder behandles i avgrensede deler uten å kutte bort slutten av møtet.
Kompakt kontroll er standard; den eldre, mer omfattende kontrollen beholdes for
evaluering. Begge bruker en språkmodell og kan overse feil eller mangler.

Se [kildekontroll og evaluering](docs/summary-grounding.md) for algoritme,
ansvarshåndtering, tester og kjente begrensninger.

## Modellvalg og databehandling

NAIS bruker Borealis gjennom den autentiserte modellgatewayen i GCP. Lokal
kjøring kan bruke Ollama med `LLM_BACKEND=ollama`, `OLLAMA_URL` og `LLM_MODELL`.
Modellnavn og kontekstvindu må samsvare med serveren som faktisk kjører modellen.

Bokmål-normalisering er en begrenset ord- og formretting. Den verken validerer
fakta eller fungerer som personvernfilter. Promptvalg endrer heller ikke hvor
lyd og tekst behandles. Se [sikkerhetsoppsettet](docs/model-api-security.md) og
[README](README.md) før behandling av møteinnhold.
