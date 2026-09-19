# body-stats

Väg dig på en Wii Balance Board och få vikten i Home Assistant.

En liten Python-tjänst som körs på en Raspberry Pi: den läser de fyra
lastcellerna i brädan, väntar tills du står stilla, och publicerar vikten till
Home Assistant via MQTT med auto-discovery. Inget att lägga till i
`configuration.yaml` — entiteterna dyker upp av sig själva.

Du kliver upp på brädan, står still ett par sekunder, kliver av. Klart.

## Varför Raspberry Pi och inte en Mac-app

Linux har redan en drivrutin för hårdvaran, `hid-wiimote`. När brädan är parad
syns den som ett helt vanligt input-device där de fyra lastcellerna ligger som
absoluta axlar, redan körda genom brädans egen kalibreringstabell. BlueZ har
dessutom en Wii-specifik plugin som kan den udda PIN-koden Wii-enheter
använder, så `bluetoothctl pair` bara funkar.

macOS har varken det ena eller det andra. En Mac-app hade betytt att
implementera HID-protokollet och kalibreringsmatten för hand, mot en
Bluetooth-stack som är ökänd för att bråka med Wii-tillbehör — och Mac:en hade
behövt vara vaken och i närheten varje gång du väger dig. En Pi som står
bredvid vågen och alltid är på är helt enkelt rätt verktyg.

## Så här mäter den

Rådatat från brädan skakar hela tiden — du svajar, plattan fjädrar, sensorerna
är billiga. Därför plockar tjänsten inte ett värde utan väntar in ett *fönster*
av mätvärden som är överens med varandra:

1. **Tomgång.** Brädan är tom. Nollpunkten lärs in löpande (`auto_tare`), så en
   bräda som driver några hundra gram fel inte förskjuter varje mätning.
2. **Insvängning.** Någon står på brädan. Ett glidande fönster på 2 sekunder
   fylls på. När spannet (max − min) i fönstret är nere under 0,3 kg räknas det
   som stabilt.
3. **Publicering.** Vikten blir ett trimmat medelvärde av fönstret — de
   extrema värdena kastas, så en fot som flyttar sig inte drar med sig
   resultatet. Med på köpet följer ett kvalitetsvärde 0–100 som säger hur väl
   mätvärdena var överens.
4. **Karens.** Brädan ignoreras tills den varit tom en stund, så ett
   uppkliv ger exakt en mätning.

Svänger det aldrig in ger den upp efter 20 sekunder och publicerar ändå, men
med kvalitet högst 50 så att du kan filtrera bort den i Home Assistant.

## Kom igång

```bash
git clone https://github.com/larsabrasha/body-stats.git
cd body-stats
sudo ./deploy/install.sh
```

Sedan:

1. **Para brädan** — se [docs/pairing.md](docs/pairing.md). Engångsjobb; därefter
   kopplar brädan upp sig själv när du trycker på power-knappen.
2. **Konfigurera** `/etc/wiiscale/config.yaml` (broker-adress och användarnamn),
   och lägg lösenordet i `/etc/wiiscale/wiiscale.env` som
   `WIISCALE_MQTT_PASSWORD=...`.
3. **Testa avläsningen** innan du kopplar på MQTT:

   ```bash
   sudo wiiscale monitor
   ```

   Tom bräda ska visa runt 0,00 kg. Kliv upp — totalen ska stämma med din
   vanliga våg på något hundratal gram när.
4. **Starta tjänsten:**

   ```bash
   sudo systemctl start wiiscale
   journalctl -u wiiscale -f
   ```

## Entiteter i Home Assistant

| Entitet | Beskrivning |
| --- | --- |
| `sensor.wii_balance_board_weight` | Vikten i kg. Hela mätningen följer med som attribut. |
| `sensor.wii_balance_board_quality` | 0–100, hur stabil mätningen var. |
| `sensor.wii_balance_board_last_measurement` | Tidpunkt för senaste vägningen. |
| `binary_sensor.wii_balance_board_occupied` | Om någon står på brädan just nu. |

Eftersom viktsensorn har `state_class: measurement` sparar Home Assistant
långtidsstatistik automatiskt — ett statistikkort ger dig trendkurvan utan mer
handpåläggning. Exempel på automationer och hur du filtrerar bort dåliga
mätningar finns i [docs/home-assistant.md](docs/home-assistant.md).

## Kommandon

```bash
wiiscale run        # tjänsten (det systemd startar)
wiiscale monitor    # liveavläsning i terminalen, rör inte MQTT
wiiscale devices    # listar input-devices som ser ut som en balansbräda
wiiscale config     # visar konfigurationen som den faktiskt tolkats
```

Alla tar `--config <fil>` och `--log-level DEBUG`.

## Konfiguration

Alla värden finns dokumenterade i
[deploy/config.example.yaml](deploy/config.example.yaml). Varje inställning kan
också sättas med en miljövariabel som heter `WIISCALE_<SEKTION>_<NYCKEL>`, till
exempel `WIISCALE_MQTT_PASSWORD` eller `WIISCALE_MEASUREMENT_WINDOW_SECONDS` —
praktiskt för hemligheter som inte ska ligga i YAML-filen.

Det du troligast vill röra:

- `measurement.stability_tolerance_kg` — höj om mätningar aldrig blir klara,
  sänk om du vill ha strängare avläsningar.
- `measurement.window_seconds` — längre fönster ger stabilare värde men kräver
  att du står still längre.
- `board.unit_scale` — bara om brädan läser konsekvent fel med en fast
  *procent*. Ett fast antal gram fel sköter `auto_tare` redan.

## Utveckling

```bash
python3 -m venv .venv && . .venv/bin/activate
pip install -e ".[dev]"
pytest
```

Testerna kör var som helst — mät- och MQTT-logiken är fri från evdev, och
brädan matas in som syntetiska mätvärden.

## Licens

MIT.
