# Testdata og proveniens

Filer for manuelle integrasjonstester og modellbenchmarker. De er ikke nødvendige
for vanlig oppstart av API-et eller for standard unit-tester.

| Fil | Bruk | Proveniens |
|-----|------|------------|
| `king.mp3` | Manuell transkripsjonstest | Opprinnelig angitt som et offentlig NRK-opptak |
| `tre_stemmer_test.wav` | Talerskille, integrasjonstest og belastningstest | Opprinnelig angitt som et offentlig NRK-opptak med tre stemmer |
| `audio/test001.wav`, `audio/test002.wav` | Eksplisitt valgte ASR-benchmarker; `test002` er standard i modell-API-testen | Opphav, eventuell rollespillstatus og grunnlag for offentlig deling må bekreftes |
| `conversation_nb.md`, `conversation_en.md` | Referat- og sammendragsbenchmarker | Tekstfiksturer; dokumentasjon av opphav bør kompletteres før de behandles som godkjente delingsdata |

Offentlig tilgjengelig lyd er ikke automatisk fri for personopplysninger eller
rettighetsbegrensninger. Stemmer kan være gjenkjennelige. Ikke legg til ekte
brukeropptak, transkripsjoner eller andre sensitive data i dette offentlige repoet.

De tidligere referansetranskripsjonene i `transcriptions/` er fjernet fordi de var
unøyaktige og ikke ble lest av kode. De skal ikke brukes som fasit. Fjerningen
endrer ikke tidligere Git-historikk eller rettighetene til lydopptakene.

Lokale `opptak_*.webm`-filer og modellcacher er beholdt og ignorert av Git.
De inngår ikke i containerimages. Sjekk likevel innhold og lagringsbehov manuelt;
en ignore-regel er ikke en sletterutine.

For måling mot gjennomgåtte norske referanser, bruk den eksplisitte NB Samtale-
benchmarken. Nedlastinger og referanser lagres under ignorert `.cache/`, ikke i
repoet. Se [datasett og begrensninger](../docs/norwegian-speech-testdata.md).
