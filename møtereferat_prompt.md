# Prompter og referatmaler

Dette dokumentet beskriver dagens promptoppsett. Selve instruksjonene ligger i
versjonerte definisjoner, slik at dokumentasjonen ikke blir en separat kopi av promptene.

## Tre hovedvalg

De tre hovedvalgene vises i denne rekkefølgen:

| Valg | Form | Maksimalt svar |
| --- | --- | --- |
| **Stikkord 0.1** | Korte kulepunkter og fraser, inntil 120 ord | 768 tokener |
| **Basis 0.1** | Det opprinnelige standardsammendraget, uendret og fortsatt valgt som standard | 2048 tokener |
| **Utfyllende 0.1** | Sammenhengende prosa, mer relevant kontekst og detaljer, konkrete avtaler og neste steg | 3072 tokener |

Alle tre bruker samme systemprompt, de samme fem overskriftene, modellvalg,
temperatur, top-p og kontekststørrelse som Basis 0.1. Brukerprompt og svarbudsjett
gir forskjellen i form og detaljnivå. Basis-filen er ikke endret, heller ikke for
å legge til nye stilkrav om prosa. De to nye definisjonene er selvstendige,
versjonerte filer, slik at senere endringer ikke endrer Basis 0.1 i stillhet.
Begge oppgir `based_on = "basis_0_1"`; samme baseline-ID følger med i
resultatmetadata. Dette dokumenterer opphavet, ikke dynamisk arv av prompttekst.

De eldre valgene er samlet under **Tidligere maler**, også når oppslag av
menyvalgene feiler og nettsiden bruker sin statiske fallback. Ingen eldre mal
er fjernet: de har ikke nøyaktig samme prompt og behandling som de tre nye.
Maler for generelle møter, spesielle avgrensninger, møteformål og egne
instruksjoner er derfor fortsatt tilgjengelige for sammenligning og særbehov.

## Basis 0.1

**Basis 0.1** er nytt standardvalg. Den kan leses opp for brukeren og bruker
de fem overskriftene Bakgrunn for møtet, Hva brukeren fortalte, Hva veilederen
informerte om, Avtaler og Neste steg (hvem gjør hva, og når).

Den autoritative definisjonen er
[basis_0_1.toml](worker/prompts/versions/basis_0_1.toml). System- og brukerprompten
bygger på den leverte grunnprompten, uten gamle tillegg om møteformål,
§14a, kildekontroll eller omskriving. «Ingen» brukes når noe ikke ble tatt opp.

| Innstilling | Basis 0.1 |
| --- | --- |
| Modus | `ett_steg` |
| Modell | `borealis-4b:latest` lokalt, `borealis-4b` i GCloud |
| Temperatur | 0.2 |
| Top-p | 0.9 |
| Kontekst | 32768 tokener, inkludert prompt og svar |
| Maksimalt svar | 2048 tokener |

Modellvelgeren kan fortsatt overstyre modellvalget. Lokalmodellen må være
installert eller importert med det angitte navnet; navnet er ikke en garanti
for at en tilsvarende tag finnes i Ollamas offentlige modellbibliotek.

Når teksten passer, brukes én generering på originaltranskripsjonen, uten de
eldre kontroll- og omskrivingsrundene. Ved for lang tekst beholdes eksisterende
oppdeling og sammenstilling, med en synlig advarsel om at flere steg ble brukt.
Ingen tekst kuttes bevisst bort for å få kilden til å passe. Dette er ikke en
garanti for et feilfritt eller fullstendig sammendrag; veileder må gjennomgå det.

### Versjonering

Ikke endre en utgitt definisjon på stedet. Lag for eksempel `basis_0_2.toml`
med ny ID `basis_0_2`, versjon `0.2` og navn `Basis 0.2`. Endringer i ordlyd,
genereringsinnstillinger eller genereringsmåte skal få en ny versjon. Git
bevarer historikken, og en test låser innholdet i Basis 0.1 med en kontrollsum.
API-svar og genereringsjobber inkluderer promptens ID, navn, versjon og modus.
Nettsiden viser versjonen sammen med modellnavnet på resultatet.

Ved utrulling må også gatewayens allowlist oppdateres fra
`images/litellm/scoped_auth.py` via OpenTofu-oppsettet i `terraform/gke`, siden
Basis 0.1 sender `top_p`. En ren NAIS-utrulling uten gatewayoppdateringen kan
ellers bli avvist med HTTP 403.

## Kilder i kode

Alle faste prompttekster brukt av appen ligger nå i TOML under
`worker/prompts/versions/`, også de eldre malene og interne kontrollinstruksjoner.
Python-filene beholder sammensetting, validering, JSON-skjemaer og kjøremåte.
Feilmeldinger, UI-tekster og syntetiske benchmark-/testdata er ikke promptdefinisjoner.

| TOML-fil | Innhold |
| --- | --- |
| `basis_0_1.toml` | Uendret Basis 0.1, fortsatt standard |
| `stikkord_0_1.toml` | Kort stikkordsvariant av Basis |
| `utfyllende_0_1.toml` | Utfyllende prosavariant av Basis |
| `generelt_mote_0_1.toml` | Generelt møtereferat |
| `minimalt_sammendrag_0_1.toml` | Minimalt sammendrag |
| `utfyllende_sammendrag_0_1.toml` | Utfyllende sammendrag og svarbudsjett |
| `veiledermote_0_1.toml` | Fullstendig veilederprompt og svarbudsjett |
| `samarbeidsmote_0_1.toml` | Samarbeidsreferat |
| `referat_14a_0_1.toml` | Eldre begrenset §14a-referat |
| `rullerende_referat_0_1.toml`, `sammendrag_0_1.toml` | Eldre løpende utkast og sammendrag |
| `avtaler_0_1.toml`, `kvalitetssjekk_0_1.toml` | Avtaleuttrekk og kvalitetssjekk |
| `meeting_fragments_0_1.toml` | Felles veilederregler, Kartlegging/Oppfølging, egendefinert mal og tillegg for pågående møter |
| `source_processing_0_1.toml` | Kildeuttrekk, faktakontroll, dekning, retting og oppdeling av lange transkripsjoner |

Definisjonene deler feltene `id`, `title`, `version` og `mode`. Hele prompter har
`system_prompt` og `user_prompt`; fragmentgruppene har en `[texts]`-tabell.
Basis har eksplisitte genereringsinnstillinger. Eldre maler beholder arvede
modell-/samplingsinnstillinger når disse ikke er angitt i filen. `max_tokens`
flyttes til TOML der malen allerede hadde et eget svarbudsjett.

Migreringen endrer ikke API-ID-er, menyvalg eller kontrollflyt. En eldre prompt
blir ikke en Basis-/ett-stegsprofil bare fordi teksten nå ligger i TOML.
Publiserte systemprompter er lagret ferdig sammensatt, slik at endringer i en
annen definisjon ikke stille endrer en eksisterende versjon. Python-eksporter
som `SYSTEM_REFERAT` beholdes for kompatibilitet, men leser teksten fra TOML.

Definisjonene lastes ved oppstart og caches skrivebeskyttet. Lag en ny fil og
versjon ved endringer, oppdater referansen i registeret/sammensettingen, og start
appen på nytt. Dette gir ikke et redigeringsgrensesnitt eller hot-reload i prod.
Snapshot-tester sammenligner de migrerte tekstene og sammensatte variantene med
kontrollsummer tatt før migreringen.

| Fil | Ansvar |
|-----|--------|
| [worker/prompts/versions/](worker/prompts/versions/) | Versjonerte prompttekster og tilhørende innstillinger |
| [worker/prompts/motereferat.py](worker/prompts/motereferat.py) | Kompatible Python-navn for møtepromptene i TOML |
| [worker/prompts/handlinger.py](worker/prompts/handlinger.py) | Tilgjengelige scenarier, standardvalg og svarbudsjetter |
| [frontend/routes/referat.py](frontend/routes/referat.py) | Setter sammen valgt mal, møteformål og avgrensning |
| [worker/prompts/normalisering.py](worker/prompts/normalisering.py) | Deterministisk bokmål-normalisering av modelloutput |
| [frontend/compact_summary.py](frontend/compact_summary.py) | Kompakt kildekontroll av referatet |
| [frontend/summary_assignments.py](frontend/summary_assignments.py) | Konservativ håndtering av oppgaver og ansvar |

## Eldre tekstmaler

- Malene under beholdes som eldre alternativer under **Tidligere maler**.
- **Generelt møte** gir et nøytralt møtereferat.
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
De eldre referatmalene bruker kompakt kontroll; den mer omfattende kontrollen beholdes for
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
