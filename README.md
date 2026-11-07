# mijn_pwn
Mijn PWN component for Home Assistant

## Meterstandverzoeken (alleen-lezen)

De integratie haalt openstaande en andere beschikbare meterstandverzoeken op via
`GET /api/connection/reading-request?withReadingNumberOnly=false`.
De bestaande aanmelding bij Mijn PWN wordt hergebruikt.

De integratie toont een binaire sensor voor openstaande verzoeken binnen het account
en per watermeter een binaire sensor en acht sensoren:

- Openstaand meterstandverzoek (`openReading`)
- Opnamenummer (`readingNumber`, exact 11 cijfers als tekst; een eerste 0 is toegestaan)
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

**Opmerking:** het ophalen van meterstandverzoeken is alleen-lezen. De
afzonderlijke `number`- en `date`-entiteiten bewaren lokaal een concept.
Een watermeterstand wordt uitsluitend naar PWN verstuurd nadat de gebruiker
eerst **Controleer meterstand** en vervolgens binnen twee minuten expliciet
**Bevestig verzending meterstand** indrukt. De definitieve API-client
vereist HTTP `200` én `{"success": true, "errorText": ""}`.
De werkelijke HTTP-statuscode bij de geobserveerde finale POST is nog
niet afzonderlijk met een live account geverifieerd.

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
meterstandverzoek (exact zoals PWN het teruggeeft, zonder nullen toe te voegen
of weg te halen), niet het
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
## Watermeterstand invoeren in Home Assistant (concept, niet versturen)

De webpagina voor **Watermeterstand doorgeven** toont drie stappen:
**Uw gegevens**, **Watermeterstand**, en **Controleren & versturen**.
Bij stap twee zijn zowel een verplichte **datum opname** in het formaat
`dd-mm-jjjj` als de zwarte/witte telwielcijfers verplicht. In de aangetroffen
HTML stonden vijf afzonderlijke cijfervelden, plus de instructie dat alle
cijfers **inclusief voorloopnullen** moeten worden ingevuld. Dit betreft de
**meterstand**, niet het 11-cijferige `readingNumber`.

De integratie heeft nu een `number`-entiteit per gevonden meter:
`Watermeterstand (concept, niet verzonden) ({meter_number})`.
De waarde is een geheel getal van `0` tot `10**amountOfWheels - 1` met stap
`1`, zolang er een open meterstandverzoek is. Voor de waargenomen meter met
vijf telwielen betekent invoer `123` de weergegeven tekst `00123`.
De exacte tekst is zichtbaar via het attribuut `formatted_reading`.
De entiteit is niet beschikbaar bij gesloten, ontbrekende of onduidelijke
meterstandverzoeken, en bij onbruikbare aantallen telwielen.

De numerieke waarde is een **concept in Home Assistant** en wordt **niet naar
PWN verzonden** bij het wijzigen. Home Assistant biedt daarnaast per meter
een afzonderlijke `date`-entiteit **Opnamedatum watermeter ({meter_number})**.
Vul eerst de conceptmeterstand en daarna de **werkelijke opnamedatum** in.
Een leeg datumveld blokkeert de verzending. De datum moet binnen de door
PWN opgegeven opnameperiode vallen en mag niet in de toekomst liggen.
Bij een wijziging van de meterstand blijft de eerder gekozen opnamedatum
staan, maar vervalt een eventuele reeds uitgevoerde plausibiliteitscontrole.

De conceptstand, oorspronkelijke invoertijd (`entered_at`) en gekozen
opnamedatum (`reading_date`) worden met `RestoreNumber` opgeslagen.
Na een herstart worden ze uitsluitend hersteld wanneer hetzelfde opnamenummer
nog openstaat en de stand en datum geldig zijn. Bij een nieuw opnamenummer
wordt het concept gewist.
Home Assistant bewaart de herstelstatus periodiek en bij een normale stop,
dus dit is geen garantie tegen een plotselinge stroomonderbreking.

Twee aanvullende sensoren heten **Laatst doorgegeven meterstand** en
**Datum laatst doorgegeven meterstand**. Ze zijn strikt gescheiden van de
reeds bestaande sensor **Laatste meterstand**, die uit de PWN-verbruikshistorie
komt. Beide nieuwe sensoren worden **uitsluitend na een geslaagde definitieve
meterstandverzending** bijgewerkt. Voor het aangetroffen JSON-antwoord
vereist de client HTTP `200` én `success: true` met een lege `errorText`. De datum is het tijdstip waarop de definitieve API-reactie als
succesvol is verwerkt, niet de opnamedatum.

Zolang nog geen meterstand definitief is bevestigd, zijn deze twee sensoren
**niet beschikbaar**. De `number`- en `date`-entiteiten bewaren alleen
lokale conceptgegevens en mogen deze sensoren niet wijzigen. Ook voorbereidende API-aanvragen
(`validate-measurement-request` en `SupplementInformation/private`) gelden
niet als meterstandbevestiging. Na een echte geslaagde verzending blijven de laatste
sensorwaarden behouden via `RestoreSensor`. Daarnaast bewaart de integratie
**alle bevestigde opnamenummers per watermeter** in een eigen
Home Assistant-opslagbestand (`Store`, per configuratie-entry). Deze
bevestigingen worden vóór het instellen van de entiteiten geladen, zodat een
nieuwere bevestiging van een andere meter een eerdere blokkade tegen dubbel
verzenden niet overschrijft.
Het attribuut `pwn_confirmed: true` markeert bevestigde waarden; reeds uit
historie afkomstige sensorstatussen mogen niet als een bevestiging worden
hersteld.

De definitieve verzendaanvraag is vastgesteld als
`POST /api/WaterUsage/{readingNumber}`. Home Assistant biedt **twee aparte
knoppen** bij elk gevonden meterstandverzoek:

1. **Controleer meterstand ({meter_number})** valideert eerst of PWN het
   opnamenummer nog accepteert via `validate-measurement-request` en controleert
   daarna het ingevulde concept via de plausibiliteits-API. De meterstand wordt
   hierbij nog niet definitief verzonden.
2. **Bevestig verzending meterstand ({meter_number})** komt uitsluitend
   beschikbaar wanneer PWN de plausibiliteitscontrole heeft goedgekeurd.
   Een tweede expliciete knopdruk start de definitieve POST.

De bevestiging geldt maximaal **twee minuten** en blijft alleen geldig zolang
de volledige conceptstand, opnamedatum, het 11-cijferige opnamenummer,
relatienummer en aansluitadres onveranderd blijven. Wanneer de termijn
verstrijkt, ververst Home Assistant de knoppen automatisch; de bevestigingsknop
wordt onbeschikbaar zonder te wachten op een coordinator-refresh.
Bij een wijziging, nieuw meterstandverzoek, verstreken termijn of herstart
vervalt de bevestiging; controleer dan opnieuw. De tijdelijke goedkeuring wordt
**niet** op schijf bewaard. De onvoltooide conceptstand en opnamedatum worden
daarentegen afzonderlijk opgeslagen, ook wanneer de meterstand-entiteit
tijdelijk onbeschikbaar is doordat de API een fout geeft.
Een wijziging van de `number`-entiteit **verstuurt niets**.

### Afgesloten verzoek bij PWN (`sap-01`)

Als PWN bij de expliciet gestarte validatie van het opnamenummer **HTTP 400**
met JSON-foutcode `sap-01` teruggeeft, is de stand volgens PWN al ontvangen of
is de stand geschat. De integratie behandelt uitsluitend deze combinatie als
een afgesloten verzoek, ongeacht of de GET-API nog `openReading: true` toont.
De invoer voor de conceptmeterstand, opnamedatum, controle- en verzendknop en
alle afzonderlijke meterstandverzoek-sensoren/binaire sensoren voor **deze
watermeter en dit opnamenummer** worden dan `unavailable`. De algemene
binaire sensor voor openstaande verzoeken telt dit verzoek niet langer mee.

Deze blokkering wordt zonder HTML-bericht, persoonsgegevens of tokens per
configuratie-entry lokaal opgeslagen en na een herstart hersteld. Een **nieuw
opnamenummer** kan weer worden ingevoerd; andere watermeters blijven werken.
Andere HTTP 400-codes, time-outs en serverfouten leiden niet tot een
permanente blokkering. De validatie gebeurt **uitsluitend na een handmatige
knopdruk**, nooit automatisch bij opstarten, verversen of conceptinvoer.

Het `ReadingDate`-veld gebruikt uitsluitend de afzonderlijk gekozen
**werkelijke opnamedatum**; het wordt niet meer uit `entered_at` afgeleid.
Het veld `amountOfWheels` komt uit het meterstandverzoek; de HTML en de
`meter-details`-API bevestigen vijf zwarte en nul rode telwielen voor de
onderzochte meter, maar dit is geen garantie voor andere watermeters.

## Plausibiliteitscontrole na invoer (voorbereid)

Direct na invoer van de watermeterstand gebruikt de website
`POST /api/WaterUsage/validate-plausible-reading/{readingNumber}/{relationNumber}`.
Het `readingNumber` is exact **11 cijfers**, zonder conversie of aanpassing
(ook een eerste `0` blijft behouden). De URL bevat daarnaast een
**10-cijferige** relationele identifier die in de waargenomen aanvraag
bestaat uit de prefix `000` en **zeven cijfers** van het relatienummer.
Dit URL-formaat is niet automatisch uitwisselbaar met het
`relationNumber` dat de eerdere `validate-measurement-request`-response
retourneert; daar is een tiencijferige waarde gezien.

`PWNAPI.build_plausible_reading_url(reading_number, relation_number)`
controleert beide identifiers en bouwt uitsluitend de URL.

De bevestigde **JSON-requestbody** bevat exact de volgende velden:
`meterReading` (geheel getal), `readingDate` (opnamedatum als
`YYYY-MM-DD`), `postalCode`, `houseNumber` (tekst) en
`houseNumberAddition` (tekst, mag leeg zijn). Een stand met vijf telwielen
die op het formulier als `00165` wordt weergegeven, wordt als numerieke
`meterReading: 165` verstuurd. Het voorbeeld van de website gebruikt
`readingDate: "2026-11-07"`; dit is de opnamedatum en niet het tijdstip van
een serverbevestiging.

`PWNAPI._build_plausible_reading_payload(meter_reading, reading_date, address,
wheel_count)` bouwt de vijf velden zonder andere accountgegevens door te
sturen. De builder vereist een Python-`date`, valideert het aantal
telwielen (1 t/m 9) en accepteert alleen gehele meterstanden binnen dit
bereik.

De bevestigde **JSON-response** is
`{"isPlausible": true, "minReading": null, "maxReading": null}`.
`isPlausible` is een boolean die aangeeft of de ingevoerde stand plausibel
is. `minReading` en `maxReading` kunnen `null` zijn; er werden in
het waargenomen antwoord geen grenzen teruggegeven. Eventuele niet-lege
numerieke grenzen en hun betekenis zijn nog niet in de browser bevestigd.

`PWNAPI.parse_plausible_reading_response()` valideert de response.
`PWNAPI.validate_plausible_reading()` biedt een afzonderlijk aanroepbare
JSON-POST aan. Een resultaat met `isPlausible: false` wordt **altijd als
afgewezen plausibiliteitscontrole behandeld**, ook als de HTTP-status
succes aangeeft. De methode werpt dan
`PWNReadingNotPlausibleError` op, met eventuele numerieke
`min_reading`- en `max_reading`-waarden voor latere foutweergave. De
meterstand blijft als concept staan; de definitieve verzendstap wordt
niet gestart. Ook ongeldige responses en netwerkfouten leiden niet
tot een definitieve verzendactie.

Een plausibiliteitsfout mag niet automatisch met dezelfde stand worden
herhaald: controleer eerst de ingevoerde stand en opnamedatum. Ook bij
een later falende definitieve POST blijven de bestaande sensoren
**Laatst doorgegeven meterstand** en **Datum laatst doorgegeven meterstand**
ongewijzigd. Ze tonen uitsluitend de laatste **door PWN bevestigde**
verzending, niet een poging of concept.

De plausibiliteitscontrole wordt **niet automatisch uitgevoerd** bij
opstarten, verversen of het aanpassen van de conceptmeterstand.
De exacte **HTTP-statuscode** van het waargenomen antwoord is nog onbekend.

De plausibiliteitscontrole **bevestigt nooit de definitieve verzending**.
Ook een eventuele HTTP `200` of `204` van dit endpoint mag de sensoren
**Laatst doorgegeven meterstand** en **Datum laatst doorgegeven meterstand**
niet wijzigen. Die sensoren krijgen pas nieuwe waarden als de *aparte,
definitieve verzendaanvraag* door PWN is geaccepteerd.

## Definitieve watermeterstandverzending (alleen na knopdruk)

De waargenomen finale aanvraag is
`POST /api/WaterUsage/{readingNumber}` met de **hoofdlettergevoelige**
JSON-velden `MeterReading` (geheel getal), `AnomalyReason: null`,
`ReadingDate` (`YYYY-MM-DD`), `PostalCode`, `Street`,
`HouseNumber`, `HouseNumberAddition` (mag leeg zijn) en
`WaterMeterImage: null`. De laatste response bevat
`{"success": true, "errorText": ""}`; daarna toont de website
**‘Bedankt voor het doorgeven van uw watermeterstand.’** Deze pagina-tekst
wordt **niet** gebruikt als technische bevestiging in Home Assistant.

De API-client controleert conservatief op **HTTP 200** met een geldig JSON
object dat `success: true` en `errorText: ""` bevat. De exacte HTTP-code
van de aangetroffen aanvraag is niet afzonderlijk aangeleverd. Bij
`success: false`, een niet-lege fouttekst, een onverwachte response of een
HTTP/netwerkfout wordt **geen bevestigde meterstand vastgelegd**.
Potentieel privé-informatie uit `errorText` of een upstream-fout wordt
niet gelogd of aan de gebruiker getoond.

Na een bevestigde finale API-reactie worden de sensoren
**Laatst doorgegeven meterstand** en
**Datum laatst doorgegeven meterstand** onmiddellijk bijgewerkt met de
stand en het UTC-tijdstip van bevestiging. De bestaande sensor
**Laatste meterstand** uit de PWN-verbruikshistorie blijft ongewijzigd.
De bevestigde sensorwaarden worden via `RestoreSensor` bewaard.
De bevestigingsknop blokkeert na een PWN-bevestiging iedere nieuwe
verzendpoging voor hetzelfde opnamenummer van dezelfde meter, ook wanneer
daarna een andere watermeter is doorgegeven of Home Assistant is herstart. Bij een onzekere netwerkuitkomst
wordt de tijdelijke bevestiging **eenmalig verbruikt**, zodat opnieuw
verzenden alleen via een nieuwe plausibiliteitscontrole en een nieuwe
expliciete bevestiging mogelijk is. Controleer vóór een nieuwe poging het
PWN-account om dubbele meldingen te voorkomen.
