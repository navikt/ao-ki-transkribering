# ADR-0004: OpenAI-kompatibelt modell-API for pilot

**Status:** Godkjent for pilot  
**Dato:** 2026-09-21  
**Forfattere:** ao-ki-taskforce  
**Forutsetning:** ADR-0002, ADR-0003

---

## Kontekst

Applikasjonen trenger et stabilt API-kontrakt mot modell-laget for både
transkripsjon og tekstgenerering. I pilotfasen er det viktigere å få et enkelt
og utskiftbart grensesnitt enn å låse applikasjonen direkte til én bestemt
modellruntime.

vLLM tilbyr et OpenAI-kompatibelt HTTP-API. Navnet `vllm-openai` og endepunkter
som `/v1/audio/transcriptions` og `/v1/chat/completions` betyr API-kompatibilitet,
ikke at data sendes til OpenAI sin SaaS-tjeneste.

Whisper-testen i GKE viste at standardimagen `vllm/vllm-openai` starter og
serverer modellen, men mangler audio-ekstraene som kreves for transkripsjon:

```text
Please install vllm[audio] for audio support
```

---

## Beslutning

Vi bruker OpenAI-kompatibelt API som kontrakt mellom applikasjonen, LiteLLM og
modellruntime i pilotfasen.

For GKE kjører vi fortsatt selvhostede modeller bak intern load balancer. Data
skal ikke sendes til ekstern OpenAI-tjeneste som del av denne beslutningen.

Whisper kjøres med en egen vLLM-image som bygger videre på `vllm/vllm-openai` og
installerer nødvendige audio-avhengigheter. Imagen pushes til prosjektets
Artifact Registry og brukes av `k8s/vllm-whisper.yaml`.

Borealis holdes deploybar, men skaleres ikke automatisk opp før ressursbruk,
oppstartstid og minneprofil er verifisert. Dette reduserer kostnad og støy i
pilotfasen.

---

## Konsekvenser

### Positive

- Klientkode kan bruke ett stabilt API-kontrakt uavhengig av modellruntime.
- LiteLLM kan rute til selvhostede modeller nå og senere byttes mot annen
  runtime eller en egen modell-API uten store endringer i applikasjonen.
- Lokal utvikling, NAIS og GKE kan bruke samme request/response-format.

### Negative

- Begrepet "OpenAI-kompatibel" kan misforstås som bruk av OpenAI SaaS og må
  dokumenteres tydelig.
- Vi må eie image-provenans, sårbarhetsoppfølging og etter hvert digest-pinning
  for vLLM-imagene.
- Audio-støtte krever egen image for Whisper.

### Risiko

- Upstream `latest` kan endre oppførsel. For pilot aksepteres dette midlertidig,
  men produksjonsmodning krever speilet image og pinning.
- OpenAI-kompatible API-er dekker ikke nødvendigvis alle fremtidige behov. En
  egen intern modell-API kan innføres senere bak samme klientabstraksjon.

---

## Aksjonspunkter

- [x] Dokumenter at OpenAI-kompatibelt API ikke betyr ekstern OpenAI-tjeneste.
- [x] Legg til egen Whisper-image med `vllm[audio]`.
- [x] Unngå automatisk oppskalering av Borealis inntil den er verifisert.
- [ ] Bygg og push Whisper-image til Artifact Registry.
- [ ] Test `/v1/audio/transcriptions` mot Whisper via port-forward.
- [ ] Speil og pin vLLM-baseimage før produksjonsmodning.
- [ ] Vurder egen intern modell-API når kravene er tydeligere.
