# mijn_pwn
Mijn PWN component for Home Assistant

## Meterstandverzoeken (alleen-lezen)

De integratie haalt openstaande en andere beschikbare meterstandverzoeken op via
`GET /api/connection/reading-request?withReadingNumberOnly=false`.
De bestaande aanmelding bij Mijn PWN wordt hergebruikt.

De integratie toont een binaire sensor voor openstaande verzoeken binnen het account
en per watermeter een binaire sensor en acht sensoren:

- Openstaand meterstandverzoek (`openReading`)
- Opnamenummer (`readingNumber`)
- Startdatum meterstandopname (`readingStartDate`)
- Opnamedatum (`readingDate`)
- Opnameredencode (`readingReason`)
- Aantal cijfers watermeter (`amountOfWheels`)
- Meternummer (`meterNumber`)
- Aansluitadres (`address`, met adresvelden als attributen)
- Contractstatuscode (`contractStatus`)

Alle entiteiten worden gekoppeld aan het bestaande verbruiksapparaat.
Nieuwe watermeters worden bij een coordinatorverversing ontdekt. Wanneer een
watermeter geen eenduidig verzoek meer heeft, worden de bijbehorende entiteiten
onbeschikbaar. Bij een lege respons is de algemene binaire sensor uit; bij een
mislukte API-aanroep is deze onbeschikbaar. Statuscodes worden als getal getoond:
de exacte betekenis van `readingReason` en `contractStatus` is nog niet vastgesteld.

**Opmerking:** deze functionaliteit bevestigt geen klantgegevens en verzendt
nog geen meterstanden. Het invoer- en verzendendpoint is niet geverifieerd.

## Verhuizing: adresvalidatie (voorbereid)

De URL `/api/MoveHouse/validate-address` is geregistreerd voor toekomstig
gebruik. Het bevestigde responseformaat is een JSON-object met de tekstvelden
`street`, `houseNumber`, `houseNumberAddition`, `postalCode`, `city` en
`country`. `houseNumberAddition` mag een lege tekenreeks zijn.

`PWNAPI.parse_move_house_address()` controleert deze response zonder het adres
te loggen of andere onbekende velden te bewaren. **Er wordt geen API-aanroep
gedaan:** de HTTP-methode en requestbody zijn nog niet bevestigd. De integratie
kan nog geen verhuizing doorgeven.
