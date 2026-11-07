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

## Bevestigen van persoonsgegevens (voorbereid)

De PWN-website gebruikt `POST /api/SupplementInformation/private/{identifier}`
met JSON-requestbody `{"token": "<bevestigingstoken>"}`. Een succesvolle
bevestiging retourneert HTTP `204 No Content` zonder responsebody.

De `identifier` bestaat uit **precies 10 ASCII-cijfers**: drie vaste nullen
gevolgd door het **7-cijferige** `relationNumber` uit de accountgegevens.
`PWNAPI.build_private_confirmation_url(relation_number)` controleert de lengte
en behoudt alle voorloopnullen. Er wordt geen bestaand token hergebruikt zonder
bevestiging van zijn herkomst.

**De integratie voert deze POST-aanvraag niet uit.** De herkomst van het
bevestigingstoken is nog onbekend. Het invoeren en verzenden van een
watermeterstand is een aparte, nog niet bevestigde API-stap.

## Watermeterdetails (voorbereid)

De PWN-website gebruikt `POST /api/WaterUsage/meter-details`. De bevestigde
JSON-respons bevat `meterType` (tekst, mag leeg zijn),
`amountOfBlackWheels` (niet-negatief geheel getal) en
`amountOfRedWheels` (niet-negatief geheel getal). Een waargenomen respons
heeft een leeg `meterType`, vijf zwarte telwielen en nul rode telwielen.

Het endpoint is geregistreerd en `PWNAPI.parse_water_meter_details()`
valideert de respons. Deze waarden zijn afzonderlijk van
`amountOfWheels` uit de meterstandverzoekrespons; de relatie ertussen is
nog niet vastgesteld.

**De integratie doet geen POST-aanroep naar dit endpoint:** de requestbody
is nog niet bekend. De gegevens worden nog niet als entiteiten aangeboden
of gebruikt voor het indienen van een meterstand.
