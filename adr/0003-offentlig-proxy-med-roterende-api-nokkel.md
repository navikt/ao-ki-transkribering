# ADR-0003: Offentlig proxy med roterende API-nøkkel for vLLM

**Status:** Forslag  
**Dato:** 2026-09-04  
**Forfattere:** ao-ki-taskforce  
**Forutsetning:** ADR-0002

---

## Kontekst

Vi trenger én stabil API-inngang som fungerer både fra NAIS og fra lokale utviklermaskiner uten å eksponere vLLM direkte på internett.

Tidligere retning i ADR-0002 forutsatte VPC peering mellom NAIS og team-prosjekt for intern trafikk. Dette gir høy nettverkssikkerhet, men løser ikke lokal utvikling og øker operasjonell kompleksitet.

Målet er derfor en enklere modell:

- vLLM holdes privat i GKE (internal load balancer)
- én offentlig proxy (LiteLLM på Cloud Run)
- autentisering med API-nøkkel som roteres
- rotasjon og secrets håndteres i Terraform + Secret Manager

Konsekvens ved å ikke gjøre dette: vi står med fragmentert tilgangsmønster (lokal vs NAIS), tregere onboarding og mer manuelt driftstrykk.

---

## Beslutning

Vi eksponerer kun LiteLLM-proxyen offentlig (HTTPS), og lar all klienttrafikk gå via proxy med `Authorization: Bearer <key>`.

vLLM-endepunktene for transkripsjon og tekstgenerering forblir private og nås kun internt via VPC fra Cloud Run.

API-nøkkel lagres i Secret Manager, injiseres i proxy-runtime, og roteres kontrollert med overlappende overgangsperiode.

---

## Alternativer vurdert

### Alternativ A: Offentlig proxy + roterende API-nøkkel (valgt)
- **Fordeler:** Enkel klientbruk (lokal + NAIS), liten nettverkskompleksitet, tydelig kontrollpunkt, støtter gradvis sikkerhetsmodning.
- **Ulemper:** Offentlig angrepsflate på proxy-laget, krever nøkkelhygiene, rate limiting og aktiv overvåking.
- **Nav-vurdering:** God balanse mellom leveransehastighet og sikkerhet for pilotfase.

### Alternativ B: Kun intern trafikk med VPC peering
- **Fordeler:** Minimal offentlig eksponering.
- **Ulemper:** Avhengig av plattformkoordinering, dårlig støtte for lokal utvikling, mer kompleks drift.
- **Nav-vurdering:** Sterkt sikkerhetsmessig, men tungt operasjonelt for nåværende behov.

### Alternativ C: Eksponer vLLM direkte offentlig
- **Fordeler:** Færre komponenter.
- **Ulemper:** Høy risiko, dårlig separasjon av ansvar, svakere kontroll med auth/rate-limit/audit.
- **Nav-vurdering:** Forkastet.

### Alternativ D: Gjøre ingenting
- **Fordeler:** Ingen migrasjonsarbeid nå.
- **Ulemper:** Blokkerer målbildet og gir varig friksjon for brukere og utviklere.
- **Nav-vurdering:** Ikke aktuelt.

---

## Nav-spesifikke vurderinger

### Sikkerhet
- **Dataklassifisering:** Møtedata og transkripsjoner kan inneholde personopplysninger.
- **Auth-mekanisme:** Bearer API-nøkkel på proxy-lag (senere mulig overgang til OIDC/JWT).
- **Least privilege:** Kun proxy-servicekonto får lese relevante secrets.
- **PII-håndtering:** Ingen logging av rå lyd, prompt-innhold eller API-nøkler; maskering i applikasjonslogger.
- **Nøkkelrotasjon:** Aktiv + neste nøkkel i overgangsvindu for null nedetid ved rotasjon.

### Plattform
- **Kjøremodell:** LiteLLM på Cloud Run (offentlig HTTPS), vLLM på GKE bak intern LB.
- **Nettverk:** Cloud Run bruker direkte VPC-egress til private vLLM-endepunkt.
- **Drift:** Terraform eier Cloud Run, secrets og IAM-bindinger.
- **Observerbarhet:** Requests, 401/403-rate, feilrater, latency og backend-helse måles per modell.

### Team-påvirkning
- **Berørte team:** ao-ki-taskforce, NAIS (informasjon), eventuelt sikkerhetsrådgiver ved produksjonsmodning.
- **Migrasjonsstrategi:** Bytt frontend/backend-klienter til proxy-URL stegvis.
- **Tilbakerulle-strategi:** Revert URL/secret til forrige versjon og deaktiver ny nøkkel.

### Migrasjon
- **Bakoverkompatibilitet:** Ja, ved å støtte både gammel og ny nøkkel i overgangsperiode.
- **Utrullingsstrategi:** Gradvis.
- **Feature toggle:** Miljøvariabel for valg av proxy-endepunkt per miljø.
- **Rollback-trigger:** Ved økt feilrate, auth-feil eller uakseptabel latency.
- **Exit-kriterier:** All trafikk går via proxy, direkte kall til vLLM er fjernet fra klienter.
- **Dekommisjonering:** Fjern gammel nøkkelversjon etter bekreftet klientmigrering.

---

## Beskyttelse mot krysspåvirkning mellom forespørsler

Rød sone — forstå dette grundig:

1. Inferens er stateless (ingen runtime-trening eller vektoppdatering).
2. Ingen delt samtaletilstand mellom brukere i proxy-laget.
3. Modellartefakter er read-only og distribueres kontrollert.
4. vLLM er ikke offentlig eksponert; all tilgang går gjennom auth-gatet proxy.
5. Rate limiting og audit gjør misbruk og anomali detekterbart.

---

## Konsekvenser

### Positive
- Samme tilgangsmønster for lokal utvikling og NAIS.
- Lavere nettverkskompleksitet enn peering-først.
- Tydelig sikkerhetskontroll i ett lag (proxy).

### Negative
- Offentlig endpoint krever streng operasjonell disiplin.
- API-nøkkelregime må vedlikeholdes aktivt.

### Risiko
- Nøkkellekkasje uten rask rotasjon kan gi uautorisert bruk.
- Manglende rate-limit kan gi kostnads- og tilgjengelighetsproblemer.

---

## Aksjonspunkter

- [ ] Implementer Terraform for Cloud Run-proxy, Secret Manager og IAM.
- [ ] Etabler nøkkelrotasjon med overlapp (aktiv + neste).
- [ ] Legg til rate limiting, request quotas og blokkering ved misbruk.
- [ ] Oppdater NAIS-secrets med proxy-URL og API-nøkkel.
- [ ] Oppdater klientkall til kun å bruke proxy-endepunkt.
- [ ] Sett opp dashboards/alarmer for auth-feil, latency og feilrate.
- [ ] Dokumenter operasjonell runbook for nøkkelrotasjon og incident-håndtering.
