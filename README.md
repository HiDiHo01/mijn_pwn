# mijn_pwn
Mijn PWN component for Home Assistant

## Meterstandverzoeken (alleen-lezen)

De integratie haalt openstaande en andere beschikbare meterstandverzoeken op via
`GET /api/connection/reading-request?withReadingNumberOnly=false`.
De bestaande aanmelding bij Mijn PWN wordt hergebruikt.

De integratie toont een binaire sensor voor openstaande verzoeken binnen het account
en per watermeter een binaire sensor en acht sensoren:

- Openstaand meterstandverzoek (`openReading`)
- Opnamenummer (`readingNumber`, exact 11 cijfers als tekst, inclusief voorloopnullen)
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
gevolgd door een **7-cijferige** relationele identifier zoals vastgesteld
bij de bevestigingsaanvraag. `PWNAPI.build_private_confirmation_url()` bewaart
deze prefix en controleert de lengte. De bij de nieuwe meterstandvalidatie
teruggegeven `relationNumber` bestaat in het voorbeeld echter uit tien cijfers.
De precieze relatie tussen beide nummers is **niet bevestigd**; de integratie
mag ze niet automatisch gelijkstellen.

De tokenwaarden zijn in de browser vergeleken en **exact gelijk** bevonden.
Het veld `additionalDataToken` uit
`POST /api/WaterUsage/validate-measurement-request/{reading_number}`
wordt ongewijzigd doorgestuurd als `{"token": "<additionalDataToken>"}` naar
`POST /api/SupplementInformation/private/{identifier}`. Het is dus een
afzonderlijk bevestigingstoken, niet het Bearer-toegangstoken.

**De integratie voert de bevestigings-POST niet uit.** Het invoeren en
verzenden van een watermeterstand is een aparte, nog niet bevestigde API-stap.

## Watermeterdetails (voorbereid)

De PWN-website gebruikt `POST /api/WaterUsage/meter-details`. De bevestigde
JSON-respons bevat `meterType` (tekst, mag leeg zijn),
`amountOfBlackWheels` (niet-negatief geheel getal) en
`amountOfRedWheels` (niet-negatief geheel getal). Een waargenomen respons
heeft een leeg `meterType`, vijf zwarte telwielen en nul rode telwielen.

De bevestigde JSON-requestbody bevat `postalCode`, `street`,
`houseNumber` en `houseNumberAddition` (mag leeg zijn). Alleen deze vier
adresvelden worden doorgestuurd; extra accountgegevens worden weggelaten.

`PWNAPI.get_water_meter_details(address)` kan de details via de bestaande
authenticatie opvragen en valideert de respons met
`PWNAPI.parse_water_meter_details()`. Fouten bevatten geen serverrespons
met eventuele persoonsgegevens. De gegevens staan los van
`amountOfWheels` uit de meterstandverzoekrespons; hun onderlinge relatie
is nog niet vastgesteld.

**Deze POST wordt niet automatisch uitgevoerd** bij het instellen of
verversen van de integratie. De gegevens worden nog niet als entiteiten
aangeboden of gebruikt om een meterstand in te dienen.

## Meterstandverzoek valideren (voorbereid)

Voordat de website persoonsgegevens bevestigt, gebruikt zij
`POST /api/WaterUsage/validate-measurement-request/{reading_number}`.
Hierbij is `reading_number` het **11-cijferige opnamenummer** van het
meterstandverzoek (als tekst, met behoud van voorloopnullen), niet het
relatienummer of de 10-cijferige bevestigingsidentifier. De JSON-requestbody
bevat alleen `houseNumber`,
`houseNumberAddition` (mag leeg zijn) en `postalCode`.

De server retourneert een JSON-object met `relationNumber`, `clientType`,
`additionalDataToken`, `readingReason`, `periodStartDate` (kan `null`
zijn), `periodEndDate` en `houseNumberAddition`. De numerieke codes
`clientType` en `readingReason` worden niet geïnterpreteerd zonder
specificatie.

`PWNAPI.validate_measurement_request(reading_number, address)` voert deze
API-aanvraag uitsluitend uit wanneer de methode expliciet wordt aangeroepen.
Zij controleert het responseformaat en geeft het `additionalDataToken`
uitsluitend terug aan de aanroeper: het wordt niet gelogd, in entiteiten
gepubliceerd of blijvend opgeslagen. De koppeling is bevestigd: de PWN-website gebruikt precies deze waarde
als `token` voor `SupplementInformation/private`.
